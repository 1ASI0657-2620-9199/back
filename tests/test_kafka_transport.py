import json
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from app.eventing import Event, Outbox
from app.kafka_transport import KafkaPublisher, KafkaRuntime, TOPICS
from app.suite import create_app


class Publisher:
    def __init__(self, failure=False):
        self.messages = []
        self.failure = failure

    def send(self, topic, value, key):
        if self.failure:
            raise ConnectionError("synthetic broker outage")
        self.messages.append((topic, json.loads(value), key))


class Message:
    def __init__(self, value, topic="finanzen.transactions.v1", offset=15):
        self.body = value
        self.source_topic = topic
        self.source_offset = offset

    def value(self): return self.body
    def topic(self): return self.source_topic
    def partition(self): return 0
    def offset(self): return self.source_offset


@pytest.fixture
def stores(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app):
        yield app.state.services


def transaction_event(stores):
    from app.models import TransactionCreate
    financial = stores["financial"]
    financial.create_transaction("kafka-user", "kafka-expense-001", TransactionCreate(
        account_id="cash", type="expense", amount="25.00", category="food",
        occurred_at="2026-11-03T12:00:00Z",
    ))
    return Outbox(financial.database).pending()[0]


def test_outbox_only_marked_after_broker_confirmation(stores):
    event = transaction_event(stores)
    publisher = Publisher(failure=True)
    runtime = KafkaRuntime("financial", stores["financial"], "unused", publisher=publisher)
    runtime.publish_once()
    assert runtime.outbox.counts()["pending"] == 1
    assert runtime.published == 0
    with stores["financial"].database.connect() as connection:
        connection.execute("UPDATE outbox SET next_attempt_at = '2000-01-01'")
    publisher.failure = False
    runtime.publish_once()
    assert runtime.outbox.counts()["pending"] == 0
    assert publisher.messages[0][0] == TOPICS["TransactionRecorded"]
    assert publisher.messages[0][1]["event_id"] == event.event_id


def test_long_broker_outage_does_not_discard_events(stores):
    event = transaction_event(stores)
    outbox = Outbox(stores["financial"].database)
    for _ in range(8):
        outbox.defer(event.event_id, ConnectionError())
    assert outbox.counts() == {"pending": 1, "dead_letters": 0}
    with stores["financial"].database.connect() as connection:
        row = connection.execute("SELECT * FROM outbox").fetchone()
    assert row["attempts"] == 8
    assert row["delivered_at"] is None


def test_successful_consumer_commits_effect_once_before_ack_permission(stores):
    event = transaction_event(stores)
    publisher = Publisher()
    runtime = KafkaRuntime("budgets", stores["budgets"], "unused", publisher=publisher)
    message = Message(event.model_dump_json().encode())
    assert runtime.process_message(message) is True
    assert runtime.process_message(message) is True
    with stores["budgets"].database.connect() as connection:
        rows = connection.execute("SELECT amount FROM movement_projection").fetchall()
    assert len(rows) == 1
    assert Decimal(rows[0]["amount"]) == 25
    assert publisher.messages == []


def test_poison_message_retries_then_reaches_real_dlq_contract(stores, monkeypatch):
    publisher = Publisher()
    runtime = KafkaRuntime("budgets", stores["budgets"], "unused", publisher=publisher)
    monkeypatch.setattr(runtime.stop_event, "wait", lambda *_: None)
    assert runtime.process_message(Message(b'{"not":"an event"}')) is True
    assert publisher.messages[0][0] == "finanzen.transactions.v1.budgets.dlq"
    dead_letter = publisher.messages[0][1]
    assert dead_letter["attempts"] == 5
    assert dead_letter["offset"] == 15
    assert dead_letter["body_base64"]
    with stores["budgets"].database.connect() as connection:
        assert connection.execute("SELECT attempts, dead_lettered FROM consumer_failures").fetchone() == {
            "attempts": 5, "dead_lettered": 1,
        }


def test_dlq_failure_does_not_allow_source_offset_commit(stores, monkeypatch):
    class FailedDLQ(Publisher):
        def send(self, *args):
            runtime.stop_event.set()
            raise ConnectionError("DLQ unavailable")
    runtime = KafkaRuntime("budgets", stores["budgets"], "unused", publisher=FailedDLQ())
    monkeypatch.setattr(runtime.stop_event, "wait", lambda *_: None)
    assert runtime.process_message(Message(b"invalid")) is False
    assert runtime.dead_letters == 0
    with stores["budgets"].database.connect() as connection:
        assert connection.execute("SELECT dead_lettered FROM consumer_failures").fetchone()["dead_lettered"] == 0


def test_retry_count_survives_new_consumer_instance(stores):
    message = Message(b"invalid")
    first = KafkaRuntime("budgets", stores["budgets"], "unused", publisher=Publisher())
    assert first.receipts.failed(first.group_id, message, ValueError()) == 1
    second = KafkaRuntime("budgets", stores["budgets"], "unused", publisher=Publisher())
    assert second.receipts.failed(second.group_id, message, ValueError()) == 2


def test_producer_queue_without_delivery_callback_is_not_confirmation():
    class QueueOnly:
        def produce(self, *args, **kwargs): pass
        def flush(self, _): return 0
    import threading
    publisher = KafkaPublisher.__new__(KafkaPublisher)
    publisher.client = QueueOnly()
    publisher.lock = threading.Lock()
    with pytest.raises(TimeoutError):
        publisher.send("test", b"event", "user")


def test_event_with_wrong_topic_cannot_modify_budget(stores, monkeypatch):
    event = transaction_event(stores)
    publisher = Publisher()
    runtime = KafkaRuntime("budgets", stores["budgets"], "unused", publisher=publisher)
    monkeypatch.setattr(runtime.stop_event, "wait", lambda *_: None)
    assert runtime.process_message(Message(event.model_dump_json().encode(), topic="finanzen.telemetry.v1")) is True
    with stores["budgets"].database.connect() as connection:
        assert connection.execute("SELECT count(*) AS n FROM movement_projection").fetchone()["n"] == 0
