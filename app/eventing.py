"""Durable outbox/inbox primitives, independent of the selected transport."""
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, Field

from .database import Database


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=80)
    schema_version: int = 1
    occurred_at: AwareDatetime = Field(default_factory=utcnow)
    payload: dict


class EventConflict(ValueError):
    pass


@contextmanager
def atomic(database: Database, identity: str):
    """Serialize writes before checking unique keys, including concurrent replay."""
    with database.connect() as connection:
        if database.postgres:
            lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], signed=True)
            connection.execute("SELECT pg_advisory_xact_lock(?)", (lock,))
        else:
            connection.execute("BEGIN IMMEDIATE")
        yield connection


def initialize_event_tables(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS outbox (
            event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, body TEXT NOT NULL,
            created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL, delivered_at TEXT, last_error TEXT,
            dead_letter INTEGER NOT NULL DEFAULT 0
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS inbox (
            event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, processed_at TEXT NOT NULL
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(dead_letter, delivered_at, next_attempt_at)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS consumer_failures (
            group_id TEXT NOT NULL, topic TEXT NOT NULL, partition_id INTEGER NOT NULL,
            offset_id BIGINT NOT NULL, attempts INTEGER NOT NULL, error_type TEXT NOT NULL,
            dead_lettered INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            PRIMARY KEY(group_id, topic, partition_id, offset_id)
        )
    """)


def enqueue(connection, event: Event):
    connection.execute("""
        INSERT INTO outbox(event_id, event_type, body, created_at, next_attempt_at)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING
    """, (event.event_id, event.event_type, event.model_dump_json(), utcnow().isoformat(), utcnow().isoformat()))


def accept_event(connection, event: Event) -> bool:
    fingerprint = hashlib.sha256(json_text(event.model_dump(mode="json")).encode()).hexdigest()
    existing = connection.execute("SELECT fingerprint FROM inbox WHERE event_id = ?", (event.event_id,)).fetchone()
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise EventConflict("El identificador del evento ya existe con otro contenido.")
        return False
    connection.execute("INSERT INTO inbox VALUES (?, ?, ?)", (event.event_id, fingerprint, utcnow().isoformat()))
    return True


class Outbox:
    def __init__(self, database: Database):
        self.database = database

    def pending(self, limit: int = 100) -> list[Event]:
        with self.database.connect() as connection:
            rows = connection.execute("""
                SELECT body FROM outbox WHERE delivered_at IS NULL AND dead_letter = 0
                AND next_attempt_at <= ? ORDER BY created_at, event_id LIMIT ?
            """, (utcnow().isoformat(), limit)).fetchall()
        return [Event.model_validate_json(row["body"]) for row in rows]

    def delivered(self, event_id: str):
        with self.database.connect() as connection:
            connection.execute("UPDATE outbox SET delivered_at = ?, last_error = NULL WHERE event_id = ?",
                               (utcnow().isoformat(), event_id))

    def failed(self, event_id: str, error: Exception):
        with atomic(self.database, "outbox") as connection:
            row = connection.execute("SELECT attempts FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
            attempts = row["attempts"] + 1
            due = utcnow() + timedelta(seconds=min(2 ** attempts, 60))
            connection.execute("""
                UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ?, dead_letter = ?
                WHERE event_id = ?
            """, (attempts, due.isoformat(), type(error).__name__, int(attempts >= 5), event_id))

    def counts(self) -> dict:
        with self.database.connect() as connection:
            pending = connection.execute("SELECT count(*) AS n FROM outbox WHERE delivered_at IS NULL AND dead_letter = 0").fetchone()["n"]
            failed = connection.execute("SELECT count(*) AS n FROM outbox WHERE dead_letter = 1").fetchone()["n"]
        return {"pending": pending, "dead_letters": failed}

    def defer(self, event_id: str, error: Exception):
        """Transport failure: retain the event until the broker recovers."""
        with atomic(self.database, "outbox") as connection:
            row = connection.execute("SELECT attempts FROM outbox WHERE event_id = ?", (event_id,)).fetchone()
            attempts = row["attempts"] + 1
            due = utcnow() + timedelta(seconds=min(2 ** min(attempts, 6), 30))
            connection.execute("""
                UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ?
                WHERE event_id = ? AND delivered_at IS NULL
            """, (attempts, due.isoformat(), type(error).__name__, event_id))
