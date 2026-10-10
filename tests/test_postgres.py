"""Integration against a real PostgreSQL instance, enabled by TEST_DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from test_api import transaction_payload


@pytest.fixture
def postgres_client():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration")
    with TestClient(create_app(database_url=url)) as client:
        yield client


def test_postgres_transactions_summary_and_isolation(postgres_client):
    owner = "owner-" + str(uuid4())
    headers = {"X-User-Id": owner, "Idempotency-Key": "income-0001"}
    income = transaction_payload("1000.00")
    created = postgres_client.post("/api/v1/transactions", json=income, headers=headers)
    assert created.status_code == 201
    replay = postgres_client.post("/api/v1/transactions", json=income, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    conflict = postgres_client.post(
        "/api/v1/transactions", json=transaction_payload("2000.00"), headers=headers,
    )
    assert conflict.status_code == 409
    expense = postgres_client.post(
        "/api/v1/transactions", json=transaction_payload("125.50", "expense"),
        headers={**headers, "Idempotency-Key": "expense-0001"},
    )
    assert expense.status_code == 201
    summary = postgres_client.get("/api/v1/summary?month=2026-10", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["balance"] == "874.50"
    assert summary.json()["transaction_count"] == 2
    unrelated = postgres_client.get(
        "/api/v1/transactions", headers={"X-User-Id": "unrelated-" + str(uuid4())},
    )
    assert unrelated.status_code == 200
    assert unrelated.json() == []


def test_postgres_data_and_idempotency_survive_application_restart():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration")
    headers = {"X-User-Id": "restart-" + str(uuid4()), "Idempotency-Key": "restart-0001"}
    payload = transaction_payload()
    with TestClient(create_app(database_url=url)) as client:
        created = client.post("/api/v1/transactions", json=payload, headers=headers)
        assert created.status_code == 201
        movement_id = created.json()["id"]
    with TestClient(create_app(database_url=url)) as restarted:
        movements = restarted.get("/api/v1/transactions", headers=headers)
        replay = restarted.post("/api/v1/transactions", json=payload, headers=headers)
    assert movements.status_code == 200
    assert [item["id"] for item in movements.json()] == [movement_id]
    assert replay.status_code == 200
    assert replay.json()["id"] == movement_id


def test_concurrent_postgres_replays_create_only_one_movement(postgres_client):
    headers = {"X-User-Id": "concurrent-" + str(uuid4()), "Idempotency-Key": "concurrent-0001"}
    payload = transaction_payload()
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(
            lambda _: postgres_client.post("/api/v1/transactions", json=payload, headers=headers),
            range(8),
        ))
    assert sorted(response.status_code for response in responses) == [200] * 7 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    movements = postgres_client.get("/api/v1/transactions", headers=headers)
    assert len(movements.json()) == 1
