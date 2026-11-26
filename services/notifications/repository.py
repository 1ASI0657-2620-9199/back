import json
import os
from datetime import timedelta

from fastapi import HTTPException

from app.database import Database
from app.eventing import Event, accept_event, atomic, enqueue, initialize_event_tables, json_text, utcnow
from services.contracts import BudgetThresholdPayload, ConsumptionPayload
from .models import NotificationPreferences


class InAppDelivery:
    """The local provider publishes notifications to the persistent in-app inbox."""
    def deliver(self, notification: dict):
        if os.getenv("FINANZEN_NOTIFICATION_SIMULATE_FAILURE") == "1":
            raise ConnectionError("Synthetic in-app delivery outage")
        return None


class NotificationRepository:
    def __init__(self, database_path: str, database_url: str | None = None, provider=None):
        self.database = Database(database_path, database_url)
        self.provider = provider or InAppDelivery()

    def initialize(self):
        self.database.prepare_directory()
        with self.database.connect() as connection:
            initialize_event_tables(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                    title TEXT NOT NULL, message TEXT NOT NULL, metadata TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL,
                    next_attempt_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    delivered_at TEXT, read_at TEXT, last_error TEXT
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_notifications_owner ON notifications(user_id, created_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    user_id TEXT PRIMARY KEY, budget_enabled INTEGER NOT NULL,
                    device_enabled INTEGER NOT NULL
                )
            """)

    @staticmethod
    def _preferences(connection, user_id: str):
        row = connection.execute("SELECT * FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
        return NotificationPreferences(budget_enabled=bool(row["budget_enabled"]),
                                       device_enabled=bool(row["device_enabled"])) if row else NotificationPreferences()

    def preferences(self, user_id: str):
        with self.database.connect() as connection:
            return self._preferences(connection, user_id)

    def save_preferences(self, user_id: str, data: NotificationPreferences):
        with self.database.connect() as connection:
            connection.execute("""
                INSERT INTO preferences VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
                budget_enabled = excluded.budget_enabled, device_enabled = excluded.device_enabled
            """, (user_id, int(data.budget_enabled), int(data.device_enabled)))
        return data

    def consume(self, event: Event) -> bool:
        if event.schema_version != 1 or event.event_type not in {"BudgetThresholdReached", "ConsumptionReadingNormalized"}:
            raise HTTPException(422, "Contrato de notificación no soportado.")
        payload = event.payload
        contract = BudgetThresholdPayload if event.event_type == "BudgetThresholdReached" else ConsumptionPayload
        contract.model_validate(payload)
        user_id = payload["user_id"]
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("Evento sin usuario válido.")
        kind = "budget" if event.event_type == "BudgetThresholdReached" else "device"
        if kind == "budget":
            threshold = int(payload["threshold"])
            title = "Límite de presupuesto alcanzado" if threshold == 100 else "Tu presupuesto se acerca al límite"
            message = (f"Has alcanzado el {threshold} % del presupuesto de {payload['category']} "
                       f"en {payload['month']}: {payload['currency']} {payload['spent']} "
                       f"de {payload['limit_amount']}.")
        else:
            title = "Límite de consumo alcanzado"
            message = (f"{payload['device_name']} alcanzó {payload['monthly_kwh']} kWh "
                       f"en {payload['month']}; límite de {payload['monthly_limit_kwh']} kWh. "
                       "El costo mostrado es una estimación, no una transacción financiera.")
        with atomic(self.database, "notifications-write") as connection:
            if not accept_event(connection, event):
                return False
            preferences = self._preferences(connection, user_id)
            enabled = preferences.budget_enabled if kind == "budget" else preferences.device_enabled
            if not enabled or (kind == "device" and not payload.get("threshold_reached", False)):
                return True
            now = utcnow().isoformat()
            connection.execute("""
                INSERT INTO notifications VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL, NULL, NULL)
            """, (event.event_id, user_id, kind, title, message, json_text(payload), now, now))
        return True

    @staticmethod
    def _view(row):
        return {**row, "metadata": json.loads(row["metadata"])}

    def list(self, user_id: str, *, unread_only: bool = False, limit: int = 100):
        with self.database.connect() as connection:
            query = "SELECT * FROM notifications WHERE user_id = ?"
            if unread_only:
                query += " AND read_at IS NULL"
            rows = connection.execute(query + " ORDER BY created_at DESC, id LIMIT ?", (user_id, limit)).fetchall()
        return [self._view(row) for row in rows]

    def mark_read(self, user_id: str, notification_id: str):
        with atomic(self.database, "notifications-write") as connection:
            row = connection.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (notification_id, user_id)).fetchone()
            if not row:
                raise HTTPException(404, "Notificación no encontrada.")
            connection.execute("UPDATE notifications SET read_at = COALESCE(read_at, ?) WHERE id = ?",
                               (utcnow().isoformat(), notification_id))
            return self._view(connection.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone())

    def retry(self, user_id: str, notification_id: str):
        with atomic(self.database, "notifications-write") as connection:
            row = connection.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (notification_id, user_id)).fetchone()
            if not row:
                raise HTTPException(404, "Notificación no encontrada.")
            if row["status"] not in {"failed", "retrying"}:
                raise HTTPException(409, "Solo se pueden reintentar notificaciones fallidas.")
            connection.execute("""
                UPDATE notifications SET status = 'pending', attempts = 0, next_attempt_at = ?,
                last_error = NULL WHERE id = ?
            """, (utcnow().isoformat(), notification_id))
            return self._view(connection.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone())

    def deliver_pending(self, limit: int = 100) -> int:
        delivered = 0
        # One local worker; the transaction also serializes concurrent local workers.
        with atomic(self.database, "notifications-write") as connection:
            rows = connection.execute("""
                SELECT * FROM notifications WHERE status IN ('pending', 'retrying')
                AND next_attempt_at <= ? ORDER BY created_at LIMIT ?
            """, (utcnow().isoformat(), limit)).fetchall()
            for row in rows:
                attempt = row["attempts"] + 1
                try:
                    self.provider.deliver(self._view(row))
                except Exception as error:
                    status = "failed" if attempt >= 5 else "retrying"
                    due = utcnow() + timedelta(seconds=min(2 ** attempt, 60))
                    connection.execute("""
                        UPDATE notifications SET status = ?, attempts = ?, next_attempt_at = ?,
                        last_error = ? WHERE id = ?
                    """, (status, attempt, due.isoformat(), type(error).__name__, row["id"]))
                    if status == "failed":
                        enqueue(connection, Event(event_type="NotificationFailed", payload={
                            "notification_id": row["id"], "user_id": row["user_id"],
                            "attempts": attempt, "error_type": type(error).__name__,
                            "original_notification": {**self._view(row), "status": "failed",
                                                      "attempts": attempt, "last_error": type(error).__name__},
                        }))
                else:
                    connection.execute("""
                        UPDATE notifications SET status = 'delivered', attempts = ?, delivered_at = ?,
                        last_error = NULL WHERE id = ?
                    """, (attempt, utcnow().isoformat(), row["id"]))
                    enqueue(connection, Event(event_type="NotificationDelivered", payload={
                        "notification_id": row["id"], "user_id": row["user_id"], "attempts": attempt,
                    }))
                    delivered += 1
        return delivered
