from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from app.eventing import Event, EventConflict, Outbox, utcnow
from app.suite import create_app
from services.notifications.repository import InAppDelivery

OWNER = {"X-User-Id": "sprint-user"}
OTHER = {"X-User-Id": "another-user"}
MONTH = "2026-11"


@pytest.fixture
def suite(tmp_path):
    application = create_app(tmp_path, start_worker=False)
    with TestClient(application) as client:
        yield application, client


def movement(client, amount="80.00", *, kind="expense", category="food", key="expense-001", month=MONTH, currency="PEN"):
    return client.post("/api/v1/transactions", headers={**OWNER, "Idempotency-Key": key}, json={
        "account_id": "cash-account", "type": kind, "amount": amount, "currency": currency,
        "category": category, "occurred_at": month + "-03T12:00:00Z",
    })


def budget(client, *, limit="100.00", threshold=80, category="food"):
    response = client.post("/api/v1/budgets", headers=OWNER, json={
        "category": category, "month": MONTH, "limit_amount": limit, "alert_threshold": threshold,
    })
    assert response.status_code == 201, response.text
    return response.json()


def device(client, *, source="simulator", limit="100"):
    response = client.post("/api/v1/devices", headers=OWNER, json={
        "name": "Medidor de prueba", "source": source, "monthly_limit_kwh": limit, "tariff_per_kwh": "0.75",
    })
    assert response.status_code == 201, response.text
    return response.json()


def reading(client, device_id, value="10", *, reading_id="reading-001", unit="kWh", headers=OWNER):
    return client.post(f"/api/v1/devices/{device_id}/simulate", headers=headers, json={
        "reading_id": reading_id, "value": value, "unit": unit, "occurred_at": MONTH + "-04T12:00:00Z",
    })


def test_financial_budget_notification_flow_is_async_and_idempotent(suite):
    application, client = suite
    created_budget = budget(client)
    first = movement(client)
    assert first.status_code == 201
    assert client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json()[0]["spent"] == "0"
    application.state.runtime.dispatch_once()
    budgets = client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json()
    assert Decimal(budgets[0]["spent"]) == 80
    assert budgets[0]["status"] == "near_limit"
    notifications = client.get("/api/v1/notifications", headers=OWNER).json()
    assert len(notifications) == 1
    assert notifications[0]["status"] == "delivered"
    assert notifications[0]["metadata"]["budget_id"] == created_budget["id"]
    assert movement(client).status_code == 200
    application.state.runtime.dispatch_once()
    assert len(client.get("/api/v1/notifications", headers=OWNER).json()) == 1
    source = application.state.services["financial"]
    with source.database.connect() as connection:
        event = Event.model_validate_json(connection.execute("SELECT body FROM outbox").fetchone()["body"])
    assert application.state.services["budgets"].consume(event) is False
    assert Decimal(client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json()[0]["spent"]) == 80


def test_outbox_failure_rolls_back_financial_movement(suite, monkeypatch):
    application, client = suite
    def unavailable(*_):
        raise RuntimeError("forced outbox failure")
    monkeypatch.setattr("app.repository.enqueue", unavailable)
    with pytest.raises(RuntimeError, match="forced outbox failure"):
        movement(client)
    assert client.get("/api/v1/transactions", headers=OWNER).json() == []
    with application.state.services["financial"].database.connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_consumer_failure_rolls_back_receipt_and_recovers(suite, monkeypatch):
    application, client = suite
    budget(client)
    movement(client)
    repository = application.state.services["budgets"]
    original = repository._emit_thresholds
    def failure(*_):
        raise RuntimeError("consumer unavailable")
    monkeypatch.setattr(repository, "_emit_thresholds", failure)
    application.state.runtime.dispatch_once()
    with repository.database.connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM inbox").fetchone()["n"] == 0
        assert connection.execute("SELECT count(*) AS n FROM movement_projection").fetchone()["n"] == 0
    assert Outbox(application.state.services["financial"].database).counts()["pending"] == 1
    monkeypatch.setattr(repository, "_emit_thresholds", original)
    with application.state.services["financial"].database.connect() as connection:
        connection.execute("UPDATE outbox SET next_attempt_at = '2000-01-01'")
    application.state.runtime.dispatch_once()
    assert Decimal(client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json()[0]["spent"]) == 80


def test_conflicting_event_id_is_rejected_without_changing_projection(suite):
    application, client = suite
    movement(client)
    financial = application.state.services["financial"]
    original = Outbox(financial.database).pending()[0]
    repository = application.state.services["budgets"]
    repository.consume(original)
    changed = original.model_copy(deep=True)
    changed.payload["amount"] = "900.00"
    with pytest.raises(EventConflict):
        repository.consume(changed)
    with repository.database.connect() as connection:
        assert connection.execute("SELECT amount FROM movement_projection").fetchone()["amount"] == "80.00"


def test_concurrent_consumer_replays_have_one_effect(suite):
    application, client = suite
    budget(client)
    movement(client)
    event = Outbox(application.state.services["financial"].database).pending()[0]
    repository = application.state.services["budgets"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(repository.consume, [event] * 8))
    assert outcomes.count(True) == 1
    assert Decimal(repository.list("sprint-user", MONTH)[0]["spent"]) == 80


def test_budget_excludes_income_other_months_categories_and_currencies(suite):
    application, client = suite
    budget(client)
    movement(client, kind="income", key="income-001")
    movement(client, month="2026-10", key="october-001")
    movement(client, category="transport", key="transport-001")
    movement(client, currency="USD", key="dollars-001")
    movement(client, amount="10.25", key="correct-001")
    application.state.runtime.dispatch_once()
    result = client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json()[0]
    assert Decimal(result["spent"]) == Decimal("10.25")


def test_late_budget_includes_existing_expenses_and_updates_without_duplicate_alerts(suite):
    application, client = suite
    movement(client, amount="90.00")
    application.state.runtime.dispatch_once()
    result = budget(client)
    assert Decimal(result["spent"]) == 90
    payload = {"category": "food", "month": MONTH, "currency": "PEN", "limit_amount": "100.00", "alert_threshold": 80}
    for _ in range(2):
        response = client.put("/api/v1/budgets/" + result["id"], headers=OWNER, json=payload)
        assert response.status_code == 200
        assert response.json()["revision"] == 1
    application.state.runtime.dispatch_once()
    assert len(client.get("/api/v1/notifications", headers=OWNER).json()) == 1
    payload["limit_amount"] = "200.00"
    response = client.put("/api/v1/budgets/" + result["id"], headers=OWNER, json=payload)
    assert response.json()["revision"] == 2
    assert response.json()["status"] == "on_track"


def test_budget_isolation_duplicates_and_invalid_month(suite):
    _, client = suite
    result = budget(client)
    payload = {"category": "food", "month": MONTH, "limit_amount": "100"}
    assert client.post("/api/v1/budgets", headers=OWNER, json=payload).status_code == 409
    assert client.put("/api/v1/budgets/" + result["id"], headers=OTHER, json=payload).status_code == 404
    assert client.get("/api/v1/budgets?month=" + MONTH, headers=OTHER).json() == []
    assert client.get("/api/v1/budgets?month=2026-13", headers=OWNER).status_code == 422
    assert client.get("/api/v1/budgets?month=" + MONTH).status_code == 422


def test_recommendation_uses_previous_income_reserves_savings_and_requires_data(suite):
    application, client = suite
    assert client.get("/api/v1/budget-recommendations?month=" + MONTH, headers=OWNER).status_code == 422
    movement(client, "1000.00", kind="income", month="2026-10", key="oct-income-001")
    movement(client, "200.00", month="2026-10", key="oct-food-001")
    movement(client, "100.00", month="2026-10", category="transport", key="oct-transport-001")
    application.state.runtime.dispatch_once()
    result = client.get("/api/v1/budget-recommendations?month=" + MONTH, headers=OWNER)
    assert result.status_code == 200
    data = result.json()
    assert Decimal(data["average_monthly_income"]) == 1000
    assert Decimal(data["savings_target"]) == 200
    assert sum(Decimal(item["limit_amount"]) for item in data["budgets"]) == 800
    assert data["historical_months"] == ["2026-10"]


def test_weekly_report_has_exact_period_totals(suite):
    application, client = suite
    movement(client, "1000", kind="income", key="weekly-income")
    movement(client, "125.50")
    application.state.runtime.dispatch_once()
    response = client.get("/api/v1/reports/weekly?week_start=2026-11-04", headers=OWNER)
    assert response.status_code == 200
    report = response.json()
    assert report["week_start"] == "2026-11-02"
    assert report["week_end"] == "2026-11-08"
    assert Decimal(report["balance"]) == Decimal("874.50")
    assert report["transaction_count"] == 2
    assert report["sufficient_data"] is True
    assert client.get("/api/v1/reports/weekly?week_start=2026-11-20", headers=OWNER).json()["sufficient_data"] is False


def test_device_normalization_deduplication_conflict_and_no_financial_movement(suite):
    application, client = suite
    created = device(client)
    first = reading(client, created["id"], "1500", unit="Wh")
    repeated = reading(client, created["id"], "1500", unit="Wh")
    assert first.status_code == 201
    assert Decimal(first.json()["value"]) == Decimal("1.5")
    assert first.json()["unit"] == "kWh"
    assert repeated.status_code == 200
    assert repeated.headers["Idempotent-Replayed"] == "true"
    assert repeated.json()["id"] == first.json()["id"]
    assert reading(client, created["id"], "2000", unit="Wh").status_code == 409
    application.state.runtime.dispatch_once()
    consumption = client.get(f"/api/v1/devices/{created['id']}/consumption?month={MONTH}", headers=OWNER).json()
    assert consumption["reading_count"] == 1
    assert Decimal(consumption["estimated_cost"]) == Decimal("1.125")
    assert client.get("/api/v1/transactions", headers=OWNER).json() == []
    assert client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json() == []


def test_vendor_adapter_and_wrong_payload_rejection(suite):
    _, client = suite
    created = device(client, source="meter_v1")
    response = client.post(f"/api/v1/devices/{created['id']}/readings", headers=OWNER, json={
        "sampleId": "vendor-sample-1", "energyWh": "2500", "timestamp": MONTH + "-03T10:00:00Z",
    })
    assert response.status_code == 201
    assert Decimal(response.json()["value"]) == Decimal("2.5")
    assert reading(client, created["id"]).status_code == 422
    assert client.post(f"/api/v1/devices/{created['id']}/readings", headers=OWNER, json={
        "reading_id": "wrong-format", "value": "10", "unit": "kWh",
    }).status_code == 422


def test_device_revocation_and_owner_isolation(suite):
    _, client = suite
    identity = device(client)["id"]
    assert reading(client, identity, headers=OTHER).status_code == 404
    assert client.get(f"/api/v1/devices/{identity}/readings?month={MONTH}", headers=OTHER).status_code == 404
    assert client.delete(f"/api/v1/devices/{identity}/connection", headers=OWNER).status_code == 200
    assert reading(client, identity).status_code == 409
    assert client.post(f"/api/v1/devices/{identity}/connection", headers=OWNER).status_code == 200
    assert reading(client, identity).status_code == 201


@pytest.mark.parametrize("data", [
    {"value": "-1", "unit": "kWh"},
    {"value": "1", "unit": "m3"},
    {"value": "1", "occurred_at": "2026-11-03T10:00:00"},
    {"value": "1", "occurred_at": (utcnow() + timedelta(days=1)).isoformat()},
])
def test_invalid_telemetry_is_rejected(suite, data):
    _, client = suite
    identity = device(client)["id"]
    assert client.post(f"/api/v1/devices/{identity}/simulate", headers=OWNER, json=data).status_code == 422


def test_device_threshold_emits_once_per_month(suite):
    application, client = suite
    identity = device(client, limit="20")["id"]
    for i in range(3):
        assert reading(client, identity, "10", reading_id=f"reading-{i}").status_code == 201
        application.state.runtime.dispatch_once()
    notifications = client.get("/api/v1/notifications", headers=OWNER).json()
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "device"
    assert Decimal(notifications[0]["metadata"]["monthly_kwh"]) == 20
    assert client.get("/api/v1/transactions", headers=OWNER).json() == []


def test_notification_preferences_and_owner_scoping(suite):
    application, client = suite
    response = client.put("/api/v1/notification-preferences", headers=OWNER, json={
        "budget_enabled": False, "device_enabled": True,
    })
    assert response.status_code == 200
    assert client.get("/api/v1/notification-preferences", headers=OWNER).json()["budget_enabled"] is False
    budget(client)
    movement(client)
    application.state.runtime.dispatch_once()
    assert client.get("/api/v1/notifications", headers=OWNER).json() == []
    identity = device(client, limit="1")["id"]
    reading(client, identity)
    application.state.runtime.dispatch_once()
    notification = client.get("/api/v1/notifications", headers=OWNER).json()[0]
    assert client.patch("/api/v1/notifications/" + notification["id"] + "/read", headers=OTHER).status_code == 404
    assert client.patch("/api/v1/notifications/" + notification["id"] + "/read", headers=OWNER).json()["read_at"]
    assert client.get("/api/v1/notifications?unread_only=true", headers=OWNER).json() == []


def test_notification_retry_dead_letter_and_manual_recovery(suite):
    application, client = suite
    class Unavailable:
        def deliver(self, _):
            raise ConnectionError("synthetic provider outage")
    repository = application.state.services["notifications"]
    repository.provider = Unavailable()
    budget(client)
    movement(client)
    application.state.runtime.dispatch_once()
    for _ in range(4):
        with repository.database.connect() as connection:
            connection.execute("UPDATE notifications SET next_attempt_at = '2000-01-01'")
        repository.deliver_pending()
    notification = client.get("/api/v1/notifications", headers=OWNER).json()[0]
    assert notification["status"] == "failed"
    assert notification["attempts"] == 5
    assert len(client.get("/api/v1/transactions", headers=OWNER).json()) == 1
    repository.provider = InAppDelivery()
    assert client.post("/api/v1/notifications/" + notification["id"] + "/retry", headers=OTHER).status_code == 404
    assert client.post("/api/v1/notifications/" + notification["id"] + "/retry", headers=OWNER).status_code == 200
    repository.deliver_pending()
    assert client.get("/api/v1/notifications", headers=OWNER).json()[0]["status"] == "delivered"


def test_service_databases_and_openapi_are_separate(suite):
    application, client = suite
    paths = [store.database.database_path for store in application.state.services.values()]
    assert len(set(paths)) == 4
    for name in ("financial", "budgets", "notifications", "devices"):
        spec = client.get("/services/" + name + "/openapi.json")
        assert spec.status_code == 200
    with application.state.services["financial"].database.connect() as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert not tables.intersection({"budgets", "devices", "readings", "notifications"})
    assert client.post("/services/budgets/internal/events", json={}).status_code == 503


def test_pending_event_survives_restart_and_existing_data_is_preserved(tmp_path):
    application = create_app(tmp_path, start_worker=False)
    with TestClient(application) as client:
        budget(client)
        result = movement(client)
        identity = result.json()["id"]
    restarted = create_app(tmp_path, start_worker=False)
    with TestClient(restarted) as client:
        restarted.state.runtime.dispatch_once()
        assert movement(client).json()["id"] == identity
        assert Decimal(client.get("/api/v1/budgets?month=" + MONTH, headers=OWNER).json()[0]["spent"]) == 80
        assert len(client.get("/api/v1/notifications", headers=OWNER).json()) == 1
        restarted.state.runtime.dispatch_once()
        assert len(client.get("/api/v1/notifications", headers=OWNER).json()) == 1


def test_timezone_offset_uses_same_utc_month_in_financial_and_budget_reports(suite):
    application, client = suite
    response = client.post("/api/v1/transactions", headers={**OWNER, "Idempotency-Key": "timezone-boundary"}, json={
        "account_id": "cash-account", "type": "expense", "amount": "10.00", "category": "food",
        "occurred_at": "2026-11-01T00:30:00+02:00",
    })
    assert response.status_code == 201
    assert response.json()["occurred_at"].startswith("2026-10-31")
    application.state.runtime.dispatch_once()
    assert Decimal(client.get("/api/v1/summary?month=2026-10", headers=OWNER).json()["expenses"]) == 10
    assert Decimal(client.get("/api/v1/summary?month=2026-11", headers=OWNER).json()["expenses"]) == 0
    report = client.get("/api/v1/reports/weekly?week_start=2026-10-31", headers=OWNER).json()
    assert Decimal(report["expenses"]) == 10


def test_internal_event_contract_validation_and_authentication(suite, monkeypatch):
    _, client = suite
    monkeypatch.setenv("FINANZEN_INTERNAL_EVENT_KEY", "private-test-secret")
    event = Event(event_type="BudgetThresholdReached", payload={}).model_dump(mode="json")
    assert client.post("/services/notifications/internal/events", json=event).status_code == 403
    response = client.post("/services/notifications/internal/events", json=event,
                           headers={"X-Event-Key": "private-test-secret"})
    assert response.status_code == 422
