from decimal import Decimal

from behave import given, when, then


def register(context, amount, kind, key):
    context.payload = {
        "account_id": "acceptance-account", "type": kind, "amount": amount,
        "currency": "PEN", "category": "salary" if kind == "income" else "food",
        "occurred_at": "2026-10-10T12:00:00+00:00", "description": "BDD synthetic evidence",
    }
    context.key = key
    context.response = context.client.post(
        "/api/v1/transactions", json=context.payload,
        headers={"X-User-Id": context.user_id, "Idempotency-Key": key},
    )


@given("a separate demonstration user")
def demonstration_user(context):
    assert context.user_id.startswith("bdd-")


@when('I register an income of "{amount}" with key "{key}"')
def create_income(context, amount, key):
    register(context, amount, "income", key)


@given('an income of "{amount}" registered with key "{key}"')
def registered_income(context, amount, key):
    register(context, amount, "income", key)
    assert context.response.status_code == 201, context.response.text
    context.original_id = context.response.json()["id"]


@given('an expense of "{amount}" registered with key "{key}"')
def registered_expense(context, amount, key):
    register(context, amount, "expense", key)
    assert context.response.status_code == 201, context.response.text


@when("I repeat the same registration")
def repeat_registration(context):
    context.response = context.client.post(
        "/api/v1/transactions", json=context.payload,
        headers={"X-User-Id": context.user_id, "Idempotency-Key": context.key},
    )


@when('I request the monthly summary for "{month}"')
def request_summary(context, month):
    context.response = context.client.get(
        "/api/v1/summary", params={"month": month, "currency": "PEN"},
        headers={"X-User-Id": context.user_id},
    )


@when("I recreate the local application and repeat the registration")
def recreate_application(context):
    from features.environment import open_local_client

    assert not context.remote_url
    context.client.__exit__(None, None, None)
    open_local_client(context)
    repeat_registration(context)


@then("the API responds with status {status:d}")
def status_matches(context, status):
    assert context.response.status_code == status, context.response.text


@then('the movement amount is "{amount}"')
def amount_matches(context, amount):
    assert Decimal(context.response.json()["amount"]) == Decimal(amount)


@then("the movement keeps its original identifier")
def same_identifier(context):
    assert context.response.json()["id"] == context.original_id
    movements = context.client.get("/api/v1/transactions", headers={"X-User-Id": context.user_id})
    assert len(movements.json()) == 1


@then('the replay header is "{value}"')
def replay_matches(context, value):
    assert context.response.headers["Idempotent-Replayed"] == value


@then('income, expenses and balance are "{income}", "{expenses}" and "{balance}"')
def summary_matches(context, income, expenses, balance):
    summary = context.response.json()
    for name, expected in (("income", income), ("expenses", expenses), ("balance", balance)):
        assert Decimal(summary[name]) == Decimal(expected)
    assert summary["transaction_count"] == 2


@then("another demonstration user has no movements")
def isolation_matches(context):
    other = context.client.get(
        "/api/v1/transactions", headers={"X-User-Id": context.user_id + "-other"},
    )
    assert other.status_code == 200
    assert other.json() == []
