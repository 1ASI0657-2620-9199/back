"""Replay one quarantined Kafka record after fixing its cause; retains its event ID."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from confluent_kafka import Consumer, TopicPartition

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.eventing import Event
from app.kafka_transport import DLQ_TOPICS, KafkaPublisher, SUBSCRIPTIONS


def replay(bootstrap: str, topic: str, partition: int, offset: int, corrected_event: Path | None):
    if topic not in DLQ_TOPICS or partition < 0 or offset < 0:
        raise ValueError("Select a consumer DLQ and a nonnegative partition/offset.")
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": "finanzen-dlq-replay-" + str(uuid4()),
                         "enable.auto.commit": False, "enable.auto.offset.store": False})
    try:
        consumer.assign([TopicPartition(topic, partition, offset)])
        deadline = time.monotonic() + 15
        message = None
        while time.monotonic() < deadline:
            candidate = consumer.poll(0.5)
            if candidate and not candidate.error():
                message = candidate
                break
        if not message or message.offset() != offset:
            raise ValueError("The exact quarantined message is unavailable; no other offset was replayed.")
        quarantined = json.loads(message.value())
        original_topic = quarantined["original_topic"]
        if original_topic not in {item for items in SUBSCRIPTIONS.values() for item in items}:
            raise ValueError("The record does not point to an allowed source topic.")
        original_bytes = base64.b64decode(quarantined["body_base64"])
        if corrected_event:
            corrected = json.loads(corrected_event.read_text(encoding="utf-8-sig"))
            if not corrected.get("event_id"):
                raise ValueError("The corrected event must explicitly preserve its original event_id.")
            try:
                original = json.loads(original_bytes)
            except ValueError:
                original = {}
            if original.get("event_id") and corrected["event_id"] != original["event_id"]:
                raise ValueError("Changing the original event_id would break deduplication.")
            event = Event.model_validate(corrected)
        else:
            event = Event.model_validate_json(original_bytes)
        publisher = KafkaPublisher(bootstrap, "finanzen-dlq-replay")
        publisher.send(original_topic, event.model_dump_json().encode(),
                       str(event.payload.get("user_id", event.event_id)))
        return {"delivery_confirmed": True, "event_id": event.event_id, "source_topic": original_topic,
                "original_dlq_position": {"topic": topic, "partition": partition, "offset": offset}}
    finally:
        consumer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", default="127.0.0.1:9092")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--partition", type=int, required=True)
    parser.add_argument("--offset", type=int, required=True)
    parser.add_argument("--corrected-event", type=Path)
    args = parser.parse_args()
    receipt = replay(args.bootstrap, args.topic, args.partition, args.offset, args.corrected_event)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
