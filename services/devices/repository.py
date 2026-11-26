import hashlib
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from app.database import Database
from app.eventing import Event, atomic, enqueue, initialize_event_tables, json_text, utcnow
from services.common import utc_month
from .adapters import normalize
from .models import DeviceCreate, DeviceUpdate


class DeviceRepository:
    def __init__(self, database_path: str, database_url: str | None = None):
        # Registry and telemetry belong to this service, never to financial records.
        self.database = Database(database_path, database_url)

    def initialize(self):
        self.database.prepare_directory()
        with self.database.connect() as connection:
            initialize_event_tables(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                    source TEXT NOT NULL, monthly_limit_kwh TEXT NOT NULL,
                    tariff_per_kwh TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS readings (
                    id TEXT PRIMARY KEY, reading_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, source TEXT NOT NULL, value TEXT NOT NULL,
                    raw_value TEXT NOT NULL, raw_unit TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL, month TEXT NOT NULL, estimated_cost TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, UNIQUE(device_id, source, reading_id)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_readings_device_month ON readings(device_id, month)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS consumption_thresholds (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def _device(connection, user_id: str, device_id: str):
        row = connection.execute("SELECT * FROM devices WHERE id = ? AND user_id = ?", (device_id, user_id)).fetchone()
        if not row:
            raise HTTPException(404, "Dispositivo no encontrado.")
        return row

    @staticmethod
    def _view(connection, row: dict):
        stats = connection.execute("SELECT count(*) AS n, max(received_at) AS last_sync FROM readings WHERE device_id = ?", (row["id"],)).fetchone()
        return {**row, "active": bool(row["active"]), "last_sync_at": stats["last_sync"], "reading_count": stats["n"]}

    def create(self, user_id: str, data: DeviceCreate):
        with self.database.connect() as connection:
            identity = str(uuid4())
            connection.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                               (identity, user_id, data.name, data.source, str(data.monthly_limit_kwh),
                                str(data.tariff_per_kwh), utcnow().isoformat()))
            return self._view(connection, self._device(connection, user_id, identity))

    def list(self, user_id: str):
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM devices WHERE user_id = ? ORDER BY created_at, id", (user_id,)).fetchall()
            return [self._view(connection, row) for row in rows]

    def update(self, user_id: str, device_id: str, data: DeviceUpdate):
        with atomic(self.database, "devices-write") as connection:
            self._device(connection, user_id, device_id)
            connection.execute("UPDATE devices SET name = ?, monthly_limit_kwh = ?, tariff_per_kwh = ? WHERE id = ?",
                               (data.name, str(data.monthly_limit_kwh), str(data.tariff_per_kwh), device_id))
            return self._view(connection, self._device(connection, user_id, device_id))

    def set_connection(self, user_id: str, device_id: str, active: bool):
        with atomic(self.database, "devices-write") as connection:
            self._device(connection, user_id, device_id)
            connection.execute("UPDATE devices SET active = ? WHERE id = ?", (int(active), device_id))
            return self._view(connection, self._device(connection, user_id, device_id))

    def sync_status(self, user_id: str, device_id: str):
        with self.database.connect() as connection:
            return self._view(connection, self._device(connection, user_id, device_id))

    @staticmethod
    def _reading_view(row):
        return {**row, "measurement_type": "energy_consumption", "unit": "kWh",
                "validation_status": "valid", "currency": "PEN"}

    def ingest(self, user_id: str, device_id: str, payload: dict, *, simulate: bool = False):
        with atomic(self.database, "devices-write") as connection:
            device = self._device(connection, user_id, device_id)
            if not device["active"]:
                raise HTTPException(409, "El dispositivo está desconectado. Autoriza su conexión antes de enviar lecturas.")
            if simulate and device["source"] != "simulator":
                raise HTTPException(422, "La simulación solo está disponible para dispositivos de tipo simulator.")
            reading = normalize(device["source"], payload)
            fingerprint = hashlib.sha256(json_text(payload).encode()).hexdigest()
            existing = connection.execute("""
                SELECT * FROM readings WHERE device_id = ? AND source = ? AND reading_id = ?
            """, (device_id, device["source"], reading.reading_id)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "El identificador de lectura ya se utilizó con otro contenido.")
                return self._reading_view(existing), True
            identity = str(uuid4())
            now = utcnow()
            month = utc_month(reading.occurred_at)
            estimate = reading.value_kwh * Decimal(device["tariff_per_kwh"])
            connection.execute("INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (identity, reading.reading_id, device_id, user_id, device["source"],
                                str(reading.value_kwh), str(reading.raw_value), reading.raw_unit,
                                reading.occurred_at.isoformat(), now.isoformat(), month, str(estimate), fingerprint))
            readings = connection.execute("SELECT value FROM readings WHERE device_id = ? AND month = ?", (device_id, month)).fetchall()
            total = sum((Decimal(row["value"]) for row in readings), Decimal(0))
            threshold_key = f"device:{device_id}:month:{month}:limit:{Decimal(device['monthly_limit_kwh']).normalize()}"
            threshold_reached = total >= Decimal(device["monthly_limit_kwh"]) and not connection.execute(
                "SELECT id FROM consumption_thresholds WHERE id = ?", (threshold_key,)).fetchone()
            if threshold_reached:
                connection.execute("INSERT INTO consumption_thresholds VALUES (?, ?)", (threshold_key, now.isoformat()))
            row = connection.execute("SELECT * FROM readings WHERE id = ?", (identity,)).fetchone()
            enqueue(connection, Event(event_id="reading:" + identity, event_type="ConsumptionReadingNormalized",
                                      occurred_at=now, payload={
                **self._reading_view(row), "device_name": device["name"], "monthly_kwh": str(total),
                "monthly_limit_kwh": device["monthly_limit_kwh"], "threshold_reached": bool(threshold_reached),
            }))
            return self._reading_view(row), False

    def readings(self, user_id: str, device_id: str, month: str, limit: int = 100):
        with self.database.connect() as connection:
            self._device(connection, user_id, device_id)
            rows = connection.execute("""
                SELECT * FROM readings WHERE device_id = ? AND month = ?
                ORDER BY occurred_at DESC, received_at DESC, id LIMIT ?
            """, (device_id, month, limit)).fetchall()
        return [self._reading_view(row) for row in rows]

    def consumption(self, user_id: str, device_id: str, month: str):
        with self.database.connect() as connection:
            device = self._device(connection, user_id, device_id)
            rows = connection.execute("SELECT * FROM readings WHERE device_id = ? AND month = ?", (device_id, month)).fetchall()
        total = sum((Decimal(row["value"]) for row in rows), Decimal(0))
        cost = sum((Decimal(row["estimated_cost"]) for row in rows), Decimal(0))
        daily = {}
        for row in rows:
            key = row["occurred_at"][:10]
            entry = daily.setdefault(key, {"date": key, "kwh": Decimal(0), "estimated_cost": Decimal(0)})
            entry["kwh"] += Decimal(row["value"])
            entry["estimated_cost"] += Decimal(row["estimated_cost"])
        return {"device_id": device_id, "month": month, "reading_count": len(rows), "kwh": total,
                "estimated_cost": cost, "currency": "PEN", "monthly_limit_kwh": Decimal(device["monthly_limit_kwh"]),
                "percentage": (total / Decimal(device["monthly_limit_kwh"]) * 100).quantize(Decimal("0.01")),
                "daily": [daily[key] for key in sorted(daily)],
                "disclaimer": "Costo estimado según la tarifa configurada al recibir cada lectura. No crea una transacción financiera."}
