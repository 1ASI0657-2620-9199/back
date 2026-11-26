"""Kafka delivery: producer confirmations, database-first consumers and DLQs.

Every process only receives the repository of its own service. There are no
cross-service database reads or direct calls to other service repositories.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
import logging
import os
import threading

from .eventing import Event, Outbox, atomic, json_text, utcnow

logger = logging.getLogger(__name__)

TOPICS = {
    "TransactionRecorded": "finanzen.transactions.v1",
    "BudgetThresholdReached": "finanzen.budget-thresholds.v1",
    "ConsumptionReadingNormalized": "finanzen.telemetry.v1",
    "NotificationDelivered": "finanzen.notification-results.v1",
    "NotificationFailed": "finanzen.notification-delivery.dlq",
}
SUBSCRIPTIONS = {
    "budgets": ["finanzen.transactions.v1"],
    "notifications": ["finanzen.budget-thresholds.v1", "finanzen.telemetry.v1"],
}
ALLOWED_TYPES = {
    "budgets": {"TransactionRecorded"},
    "notifications": {"BudgetThresholdReached", "ConsumptionReadingNormalized"},
}
DLQ_TOPICS = {
    topic + "." + service + ".dlq"
    for service, topics in SUBSCRIPTIONS.items() for topic in topics
}
ALL_TOPICS = sorted(set(TOPICS.values()) | DLQ_TOPICS)


class KafkaPublisher:
    def __init__(self, bootstrap_servers: str, client_id: str):
        from confluent_kafka import Producer
        self.client = Producer({
            "bootstrap.servers": bootstrap_servers, "client.id": client_id,
            "enable.idempotence": True, "acks": "all",
            "delivery.timeout.ms": 5000, "request.timeout.ms": 3000,
            "socket.timeout.ms": 3000,
            "reconnect.backoff.ms": 500, "reconnect.backoff.max.ms": 5000,
            "log.connection.close": False,
        })
        self.lock = threading.Lock()

    def send(self, topic: str, value: bytes, key: str):
        # Producer.produce() alone only queues in memory; its callback is the ack.
        result = []
        def confirmed(error, _):
            result.append(error)
        with self.lock:
            self.client.produce(topic, value=value, key=key.encode(), on_delivery=confirmed)
            remaining = self.client.flush(7)
        if remaining or not result:
            raise TimeoutError("Kafka did not confirm delivery")
        if result[0] is not None:
            raise ConnectionError(str(result[0]))


class ConsumerReceipts:
    """Durable retry counters keyed by the Kafka message's original position."""
    def __init__(self, database):
        self.database = database

    def failed(self, group: str, message, error: Exception) -> int:
        identity = (group, message.topic(), message.partition(), message.offset())
        with atomic(self.database, "consumer-retries") as connection:
            row = connection.execute("""
                SELECT attempts FROM consumer_failures
                WHERE group_id = ? AND topic = ? AND partition_id = ? AND offset_id = ?
            """, identity).fetchone()
            attempts = row["attempts"] + 1 if row else 1
            connection.execute("""
                INSERT INTO consumer_failures
                    (group_id, topic, partition_id, offset_id, attempts, error_type, dead_lettered, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(group_id, topic, partition_id, offset_id) DO UPDATE SET
                    attempts = excluded.attempts, error_type = excluded.error_type,
                    updated_at = excluded.updated_at
            """, (*identity, attempts, type(error).__name__, utcnow().isoformat()))
        return attempts

    def dead_lettered(self, group: str, message):
        with self.database.connect() as connection:
            connection.execute("""
                UPDATE consumer_failures SET dead_lettered = 1, updated_at = ?
                WHERE group_id = ? AND topic = ? AND partition_id = ? AND offset_id = ?
            """, (utcnow().isoformat(), group, message.topic(), message.partition(), message.offset()))


class KafkaRuntime:
    def __init__(self, service: str, repository, bootstrap_servers: str, *, publisher=None):
        self.service = service
        self.repository = repository
        self.bootstrap_servers = bootstrap_servers
        self.group_id = "finanzen-" + service + "-v1"
        self.outbox = Outbox(repository.database)
        self.receipts = ConsumerReceipts(repository.database)
        self.publisher = publisher or KafkaPublisher(bootstrap_servers, service + "-outbox")
        self.stop_event = threading.Event()
        self.threads = []
        self.published = 0
        self.consumed = 0
        self.dead_letters = 0
        self.last_publish_at = None
        self.last_consume_at = None
        self.last_error = None

    def publish_once(self):
        for event in self.outbox.pending():
            if self.stop_event.is_set():
                break
            try:
                topic = TOPICS[event.event_type]
            except KeyError as error:
                self.outbox.failed(event.event_id, error)
                continue
            try:
                self.publisher.send(topic, event.model_dump_json().encode(), str(event.payload.get("user_id", event.event_id)))
            except Exception as error:
                # A broker outage is not a poison event. Keep it pending indefinitely.
                self.outbox.defer(event.event_id, error)
                self.last_error = type(error).__name__
                logger.warning("Kafka unavailable; outbox retained: service=%s event=%s error=%s",
                               self.service, event.event_id, type(error).__name__)
                break
            self.outbox.delivered(event.event_id)
            self.published += 1
            self.last_publish_at = utcnow().isoformat()
            self.last_error = None

    def process_message(self, message) -> bool:
        """Return only after business commit or a confirmed DLQ publication."""
        while not self.stop_event.is_set():
            try:
                event = Event.model_validate_json(message.value())
                if event.event_type not in ALLOWED_TYPES[self.service] or TOPICS[event.event_type] != message.topic():
                    raise ValueError("Event type does not match its service/topic")
                self.repository.consume(event)  # Inbox + effects commit before offset.
            except Exception as error:
                attempts = self.receipts.failed(self.group_id, message, error)
                self.last_error = type(error).__name__
                if attempts < 5:
                    self.stop_event.wait(min(0.25 * 2 ** (attempts - 1), 4))
                    continue
                identity = f"{self.group_id}:{message.topic()}:{message.partition()}:{message.offset()}"
                dead_letter = {
                    "dead_letter_id": identity, "service": self.service, "attempts": attempts,
                    "original_topic": message.topic(), "partition": message.partition(),
                    "offset": message.offset(), "error_type": type(error).__name__,
                    "body_base64": base64.b64encode(message.value() or b"").decode(),
                    "failed_at": utcnow().isoformat(),
                }
                # Do not advance the source offset if the DLQ itself is unavailable.
                while not self.stop_event.is_set():
                    try:
                        self.publisher.send(message.topic() + "." + self.service + ".dlq",
                                            json_text(dead_letter).encode(), identity)
                        self.receipts.dead_lettered(self.group_id, message)
                        self.dead_letters += 1
                        logger.warning("Kafka message quarantined: service=%s topic=%s partition=%s offset=%s",
                                       self.service, message.topic(), message.partition(), message.offset())
                        return True
                    except Exception:
                        logger.warning("DLQ unavailable; source offset retained: service=%s", self.service)
                        self.stop_event.wait(1)
                return False
            else:
                self.consumed += 1
                self.last_consume_at = utcnow().isoformat()
                self.last_error = None
                return True
        return False

    def _publish_loop(self):
        while not self.stop_event.is_set():
            try:
                self.publish_once()
            except Exception:
                logger.exception("Outbox relay will retry: service=%s", self.service)
            self.stop_event.wait(0.4)

    def _consume_loop(self):
        from confluent_kafka import Consumer
        while not self.stop_event.is_set():
            consumer = None
            try:
                consumer = Consumer({
                    "bootstrap.servers": self.bootstrap_servers, "group.id": self.group_id,
                    "client.id": self.service + "-consumer", "auto.offset.reset": "earliest",
                    "enable.auto.commit": False, "enable.auto.offset.store": False,
                    "socket.timeout.ms": 5000, "session.timeout.ms": 10000,
                    "max.poll.interval.ms": 300000,
                    "reconnect.backoff.ms": 500, "reconnect.backoff.max.ms": 5000,
                    "log.connection.close": False,
                })
                consumer.subscribe(SUBSCRIPTIONS[self.service])
                while not self.stop_event.is_set():
                    message = consumer.poll(0.5)
                    if message is None:
                        continue
                    if message.error():
                        self.last_error = "KafkaConsumerError"
                        self.stop_event.wait(0.5)
                        continue
                    if not self.process_message(message):
                        break
                    committed = consumer.commit(message=message, asynchronous=False)
                    if any(partition.error for partition in committed):
                        raise ConnectionError("Kafka offset commit failed")
            except Exception:
                # Rejoin from the last broker-committed offset. Inbox handles replay.
                logger.exception("Kafka consumer reconnecting: service=%s", self.service)
                self.stop_event.wait(1)
            finally:
                if consumer:
                    consumer.close()  # Auto-commit stays disabled on shutdown.

    def _delivery_loop(self):
        while not self.stop_event.is_set():
            try:
                self.repository.deliver_pending()
            except Exception:
                logger.exception("Notification delivery will retry")
            self.stop_event.wait(0.4)

    def start(self):
        targets = [("outbox", self._publish_loop)]
        if self.service in SUBSCRIPTIONS:
            targets.append(("consumer", self._consume_loop))
        if self.service == "notifications":
            targets.append(("delivery", self._delivery_loop))
        for name, target in targets:
            thread = threading.Thread(target=target, name=self.service + "-" + name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=20)

    def status(self):
        return {
            "transport": "kafka", "service": self.service, "group_id": self.group_id if self.service in SUBSCRIPTIONS else None,
            "outbox": self.outbox.counts(), "published": self.published, "consumed": self.consumed,
            "dead_letters": self.dead_letters, "last_publish_at": self.last_publish_at,
            "last_consume_at": self.last_consume_at, "last_error": self.last_error,
            "workers": {thread.name: thread.is_alive() for thread in self.threads},
        }


@asynccontextmanager
async def broker_lifespan(application, repository, service: str):
    application.state.broker_runtime = None
    if os.getenv("EVENT_TRANSPORT", "disabled") != "kafka":
        yield
        return
    runtime = KafkaRuntime(service, repository, os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:19092"))
    application.state.broker_runtime = runtime
    runtime.start()
    try:
        yield
    finally:
        await asyncio.to_thread(runtime.stop)


def broker_status(application, repository):
    runtime = getattr(application.state, "broker_runtime", None)
    return runtime.status() if runtime else {"transport": "disabled", "outbox": Outbox(repository.database).counts()}
