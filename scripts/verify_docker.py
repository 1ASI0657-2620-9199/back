"""Real Docker/Kafka acceptance and controlled recovery checks.

Run with the Compose stack already healthy. This script creates a synthetic user,
briefly stops only this project's Kafka/Notification containers, and restores them.
Evidence is stored under reports/docker/, never represented as production traffic.
"""
import argparse
import base64
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from confluent_kafka import Consumer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.eventing import Event
from app.kafka_transport import ALL_TOPICS, KafkaPublisher


def compose(*args, environment=None):
    result = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", check=True, env=environment)
    return result.stdout


def main(base_url: str, bootstrap: str, faults: bool):
    run_id = str(uuid4())
    owner = "docker-" + run_id[:8]
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    output = ROOT / "reports/docker"
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"started_at": now.isoformat(), "user_id": owner, "base_url": base_url,
                "checks": [], "events": [], "fault_tests_enabled": faults}
    client = httpx.Client(base_url=base_url, headers={"X-User-Id": owner}, timeout=10)
    audit = Consumer({"bootstrap.servers": bootstrap, "group.id": "finanzen-evidence-" + run_id,
                      "auto.offset.reset": "earliest", "enable.auto.commit": False})
    audit.subscribe(ALL_TOPICS)
    publisher = KafkaPublisher(bootstrap, "finanzen-verifier-" + run_id)
    stopped = set()
    failure_mode_active = False

    def observe():
        for _ in range(50):
            message = audit.poll(0)
            if not message or message.error():
                return
            data = json.loads(message.value())
            original = data
            if "body_base64" in data:
                original = json.loads(base64.b64decode(data["body_base64"]))
            if original.get("payload", {}).get("user_id") == owner:
                evidence["events"].append({"topic": message.topic(), "partition": message.partition(),
                                           "offset": message.offset(), "body": data})

    def check(label, details=None):
        evidence["checks"].append({"name": label, "passed": True, "details": details})
        print("OK:", label, flush=True)

    def eventually(callback, label, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observe()
            try:
                result = callback()
                if result:
                    return result
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                pass
            time.sleep(0.25)
        raise AssertionError("Timed out: " + label)

    def get(path):
        response = client.get(path)
        response.raise_for_status()
        return response.json()

    def expense(amount, key, category="food"):
        response = client.post("/api/v1/transactions", headers={"Idempotency-Key": key}, json={
            "account_id": "cash-account", "type": "expense", "amount": amount, "currency": "PEN",
            "category": category, "occurred_at": now.isoformat(), "description": "Docker/Kafka acceptance",
        })
        assert response.status_code in (200, 201), response.text
        return response

    def spent():
        return Decimal(get("/api/v1/budgets?month=" + month)[0]["spent"])

    def notifications():
        return get("/api/v1/notifications")

    try:
        assert client.get("/").status_code == 200
        for service in ("financial", "budgets", "notifications", "devices"):
            health = get(f"/services/{service}/health")
            assert health["transport"] == "kafka"
            assert all(health["workers"].values())
            spec = get(f"/services/{service}/openapi.json")
            assert spec["servers"][0]["url"] == "/services/" + service
        check("Four independent Kafka service APIs and Swagger behind Nginx")

        response = client.post("/api/v1/budgets", json={"category": "food", "month": month,
                                                      "limit_amount": "100", "alert_threshold": 80})
        assert response.status_code == 201, response.text
        transaction = expense("85.00", "docker-first-" + run_id).json()
        eventually(lambda: spent() == 85, "Budget consumes TransactionRecorded")
        eventually(lambda: len(notifications()) == 1 and notifications()[0]["status"] == "delivered",
                   "Notification consumes budget threshold")
        check("TransactionRecorded -> BudgetThresholdReached -> delivered in-app notification")

        replay = expense("85.00", "docker-first-" + run_id)
        assert replay.status_code == 200
        assert replay.json()["id"] == transaction["id"]
        source_body = compose("exec", "-T", "financial-records", "python", "-c",
            "import sqlite3,sys; c=sqlite3.connect('/data/financial_records.db'); "
            "print(c.execute('SELECT body FROM outbox WHERE event_id = ?', ('transaction:'+sys.argv[1],)).fetchone()[0])",
            transaction["id"])
        event = Event.model_validate_json(source_body)
        publisher.send("finanzen.transactions.v1", event.model_dump_json().encode(), owner)
        time.sleep(1)
        assert spent() == 85
        assert len(get("/api/v1/transactions")) == 1
        check("HTTP replay and actual Kafka redelivery produce no duplicate financial effects")

        response = client.post("/api/v1/devices", json={"name": "Medidor Docker", "monthly_limit_kwh": "20", "tariff_per_kwh": "0.75"})
        assert response.status_code == 201
        device_id = response.json()["id"]
        reading = {"reading_id": "docker-reading-" + run_id, "value": "25000", "unit": "Wh", "occurred_at": now.isoformat()}
        path = f"/api/v1/devices/{device_id}/simulate"
        assert client.post(path, json=reading).status_code == 201
        assert client.post(path, json=reading).status_code == 200
        consumption = get(f"/api/v1/devices/{device_id}/consumption?month={month}")
        assert Decimal(consumption["kwh"]) == 25
        assert Decimal(consumption["estimated_cost"]) == Decimal("18.75")
        assert consumption["reading_count"] == 1
        eventually(lambda: len(notifications()) == 2, "Device threshold notification")
        assert len(get("/api/v1/transactions")) == 1
        check("Telemetry crosses Kafka, normalizes Wh, deduplicates and never creates a financial transaction")

        poison = Event(event_id="poison-" + run_id, event_type="TransactionRecorded",
                       payload={"user_id": owner, "deliberately_invalid": True})
        publisher.send("finanzen.transactions.v1", poison.model_dump_json().encode(), owner)
        def received_dlq():
            return next((item for item in evidence["events"]
                         if item["topic"] == "finanzen.transactions.v1.budgets.dlq"
                         and json.loads(base64.b64decode(item["body"]["body_base64"]))["event_id"] == poison.event_id), None)
        dlq = eventually(received_dlq, "Persistent poison event retries and DLQ")
        assert dlq["body"]["attempts"] == 5
        assert spent() == 85
        check("Invalid message retries five times and reaches the real Kafka DLQ", dlq)

        if faults:
            print("Testing broker interruption...", flush=True)
            compose("stop", "-t", "10", "kafka")
            stopped.add("kafka")
            disconnected = expense("5.00", "docker-broker-down-" + run_id)
            assert disconnected.status_code == 201
            health = get("/services/financial/health")
            assert health["outbox"]["pending"] >= 1
            assert spent() == 85
            check("Broker down: financial write succeeds and the Outbox retains the event")
            compose("start", "kafka")
            stopped.remove("kafka")
            eventually(lambda: spent() == 90, "Outbox catches up after Kafka restarts", timeout=120)
            check("Broker recovery delivers the retained event without duplicate spending")

            print("Testing Notification Service interruption...", flush=True)
            compose("stop", "-t", "20", "notification-service")
            stopped.add("notification-service")
            assert expense("15.00", "docker-notification-down-" + run_id).status_code == 201
            eventually(lambda: spent() == 105, "Budget continues while notifications are down")
            compose("start", "notification-service")
            stopped.remove("notification-service")
            eventually(lambda: len(notifications()) == 3 and all(item["status"] == "delivered" for item in notifications()),
                       "Notification consumer catches up from durable Kafka offsets")
            check("Notification outage does not block financial records; pending threshold is delivered on recovery")

            ids_before = sorted(item["id"] for item in get("/api/v1/transactions"))
            compose("restart", "financial-records", "budget-service", "notification-service", "device-service")
            eventually(lambda: sorted(item["id"] for item in get("/api/v1/transactions")) == ids_before,
                       "Financial persistence after service restart")
            eventually(lambda: spent() == 105 and len(notifications()) == 3, "Budget and notification persistence")
            eventually(lambda: get(f"/api/v1/devices/{device_id}/consumption?month={month}")["reading_count"] == 1,
                       "Telemetry persistence")
            check("Four service databases preserve IDs, totals, notifications and telemetry after restart")

            print("Testing notification delivery retries and recovery...", flush=True)
            failure_mode_active = True
            environment = {**os.environ, "FINANZEN_NOTIFICATION_SIMULATE_FAILURE": "1"}
            compose("up", "-d", "--no-deps", "--wait", "notification-service", environment=environment)
            eventually(lambda: get("/services/notifications/health")["transport"] == "kafka", "Failed provider ready")
            response = client.post("/api/v1/budgets", json={
                "category": "services", "month": month, "limit_amount": "50", "alert_threshold": 80,
            })
            assert response.status_code == 201
            assert expense("45.00", "docker-provider-down-" + run_id, category="services").status_code == 201
            def failed_notification():
                return next((item for item in notifications()
                             if item["metadata"].get("category") == "services" and item["status"] == "failed"), None)
            failed = eventually(failed_notification, "Five notification delivery attempts", timeout=120)
            assert failed["attempts"] == 5
            eventually(lambda: any(item["topic"] == "finanzen.notification-delivery.dlq"
                                  and item["body"]["payload"]["notification_id"] == failed["id"]
                                  for item in evidence["events"]), "NotificationFailed published to Kafka DLQ")
            check("Notification provider failure retries five times and publishes NotificationFailed to Kafka DLQ")
            environment["FINANZEN_NOTIFICATION_SIMULATE_FAILURE"] = "0"
            compose("up", "-d", "--no-deps", "--wait", "notification-service", environment=environment)
            failure_mode_active = False
            eventually(lambda: get("/services/notifications/health")["transport"] == "kafka", "Provider recovered")
            response = client.post("/api/v1/notifications/" + failed["id"] + "/retry")
            assert response.status_code == 200, response.text
            eventually(lambda: any(item["id"] == failed["id"] and item["status"] == "delivered"
                                  for item in notifications()), "Manual notification retry after recovery")
            check("Manual retry delivers the same notification after provider recovery")

        for service in ("financial", "budgets", "notifications", "devices"):
            eventually(lambda service=service: get(f"/services/{service}/operations/events")["outbox"]["pending"] == 0,
                       service + " outbox drained", timeout=90)
        eventually(lambda: any(item["topic"] == "finanzen.notification-results.v1" for item in evidence["events"]),
                   "NotificationDelivered published to Kafka")
        check("All outboxes drained and NotificationDelivered observable on Kafka")

        container_ids = compose("ps", "-q", "financial-records", "budget-service", "notification-service", "device-service").split()
        inspected = json.loads(subprocess.run(["docker", "inspect", *container_ids], capture_output=True,
                                              text=True, check=True).stdout)
        data_volumes = [next(mount["Name"] for mount in item["Mounts"] if mount["Destination"] == "/data") for item in inspected]
        assert len(set(data_volumes)) == 4
        check("Database per Service: four distinct Docker volumes", data_volumes)
        evidence["containers"] = compose("ps", "-a", "--format", "json")
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        evidence["result"] = "passed"
    except Exception as error:
        evidence["result"] = "failed"
        evidence["error"] = str(error)
        raise
    finally:
        if failure_mode_active:
            compose("up", "-d", "--no-deps", "--wait", "notification-service",
                    environment={**os.environ, "FINANZEN_NOTIFICATION_SIMULATE_FAILURE": "0"})
        for service in stopped:
            compose("start", service)
        audit.close()
        client.close()
        (output / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Evidence:", output / "verification.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--bootstrap", default="127.0.0.1:9092")
    parser.add_argument("--faults", action="store_true", help="Briefly stop and restart only this Compose project's services")
    args = parser.parse_args()
    main(args.base_url.rstrip("/"), args.bootstrap, args.faults)
