from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from fastapi import HTTPException

from app.database import Database
from app.eventing import Event, EventConflict, accept_event, atomic, enqueue, initialize_event_tables, json_text, utcnow
from app.models import TransactionResponse
from services.common import as_utc, utc_month
from .models import BudgetWrite

CENT = Decimal("0.01")


class BudgetRepository:
    def __init__(self, database_path: str, database_url: str | None = None):
        self.database = Database(database_path, database_url)

    def initialize(self):
        self.database.prepare_directory()
        with self.database.connect() as connection:
            initialize_event_tables(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, category TEXT NOT NULL,
                    month TEXT NOT NULL, currency TEXT NOT NULL, limit_amount TEXT NOT NULL,
                    alert_threshold INTEGER NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(user_id, category, month, currency)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS movement_projection (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, type TEXT NOT NULL,
                    amount TEXT NOT NULL, currency TEXT NOT NULL, category TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, month TEXT NOT NULL, fingerprint TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_projection_owner_month ON movement_projection(user_id, month, currency)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS threshold_receipts (
                    id TEXT PRIMARY KEY, budget_id TEXT NOT NULL, created_at TEXT NOT NULL
                )
            """)

    def _view(self, connection, row: dict) -> dict:
        values = connection.execute("""
            SELECT amount FROM movement_projection
            WHERE user_id = ? AND category = ? AND month = ? AND currency = ? AND type = 'expense'
        """, (row["user_id"], row["category"], row["month"], row["currency"])).fetchall()
        spent = sum((Decimal(value["amount"]) for value in values), Decimal(0))
        limit = Decimal(row["limit_amount"])
        percentage = (spent / limit * 100).quantize(CENT, rounding=ROUND_HALF_UP)
        status = "exceeded" if spent >= limit else ("near_limit" if spent * 100 >= limit * row["alert_threshold"] else "on_track")
        return {**row, "limit_amount": limit, "spent": spent, "remaining": limit - spent,
                "percentage": percentage, "status": status}

    def list(self, user_id: str, month: str, currency: str = "PEN"):
        with self.database.connect() as connection:
            rows = connection.execute("""
                SELECT * FROM budgets WHERE user_id = ? AND month = ? AND currency = ?
                ORDER BY category
            """, (user_id, month, currency)).fetchall()
            return [self._view(connection, row) for row in rows]

    def save(self, user_id: str, data: BudgetWrite, budget_id: str | None = None):
        with atomic(self.database, "budgets-write") as connection:
            if budget_id:
                previous = connection.execute("SELECT * FROM budgets WHERE id = ? AND user_id = ?", (budget_id, user_id)).fetchone()
                if not previous:
                    raise HTTPException(404, "Presupuesto no encontrado.")
                if any(previous[field] != getattr(data, field) for field in ("month", "category", "currency")):
                    raise HTTPException(422, "La categoría, moneda y periodo identifican el presupuesto y no se pueden cambiar.")
                # Retrying the same update does not re-emit alerts.
                changed = Decimal(previous["limit_amount"]) != data.limit_amount or previous["alert_threshold"] != data.alert_threshold
                connection.execute("""
                    UPDATE budgets SET limit_amount = ?, alert_threshold = ?, revision = ?
                    WHERE id = ? AND user_id = ?
                """, (str(data.limit_amount), data.alert_threshold, previous["revision"] + int(changed), budget_id, user_id))
            else:
                existing = connection.execute("""
                    SELECT id FROM budgets WHERE user_id = ? AND category = ? AND month = ? AND currency = ?
                """, (user_id, data.category, data.month, data.currency)).fetchone()
                if existing:
                    raise HTTPException(409, "Ya existe un presupuesto para esa categoría y periodo.")
                budget_id = str(uuid4())
                connection.execute("INSERT INTO budgets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (budget_id, user_id, data.category, data.month, data.currency,
                                    str(data.limit_amount), data.alert_threshold, 1, utcnow().isoformat()))
            row = connection.execute("SELECT * FROM budgets WHERE id = ?", (budget_id,)).fetchone()
            view = self._view(connection, row)
            self._emit_thresholds(connection, view)
            return view

    def consume(self, event: Event) -> bool:
        if event.schema_version != 1 or event.event_type != "TransactionRecorded":
            raise HTTPException(422, "Contrato de evento financiero no soportado.")
        movement = TransactionResponse.model_validate(event.payload)
        payload = movement.model_dump(mode="json")
        with atomic(self.database, "budgets-write") as connection:
            if not accept_event(connection, event):
                return False
            existing = connection.execute("SELECT fingerprint FROM movement_projection WHERE id = ?", (movement.id,)).fetchone()
            if existing:
                if existing["fingerprint"] != json_text(payload):
                    raise EventConflict("La transacción proyectada ya existe con otro contenido.")
                return False
            month = utc_month(movement.occurred_at)
            connection.execute("INSERT INTO movement_projection VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (movement.id, movement.user_id, movement.type.value, str(movement.amount),
                                movement.currency, movement.category, as_utc(movement.occurred_at).isoformat(),
                                month, json_text(payload)))
            if movement.type.value == "expense":
                budgets = connection.execute("""
                    SELECT * FROM budgets WHERE user_id = ? AND category = ? AND month = ? AND currency = ?
                """, (movement.user_id, movement.category, month, movement.currency)).fetchall()
                for budget in budgets:
                    self._emit_thresholds(connection, self._view(connection, budget))
        return True

    def _emit_thresholds(self, connection, budget: dict):
        for threshold in sorted({budget["alert_threshold"], 100}):
            if budget["spent"] * 100 < budget["limit_amount"] * threshold:
                continue
            identity = f"budget:{budget['id']}:revision:{budget['revision']}:threshold:{threshold}"
            receipt = connection.execute("SELECT id FROM threshold_receipts WHERE id = ?", (identity,)).fetchone()
            if receipt:
                continue
            connection.execute("INSERT INTO threshold_receipts VALUES (?, ?, ?)", (identity, budget["id"], utcnow().isoformat()))
            enqueue(connection, Event(event_id=identity, event_type="BudgetThresholdReached", payload={
                "user_id": budget["user_id"], "budget_id": budget["id"], "category": budget["category"],
                "month": budget["month"], "currency": budget["currency"], "threshold": threshold,
                "spent": str(budget["spent"]), "limit_amount": str(budget["limit_amount"]),
            }))

    def recommend(self, user_id: str, month: str, currency: str):
        first = date.fromisoformat(month + "-01")
        months = []
        for _ in range(3):
            first = (first - timedelta(days=1)).replace(day=1)
            months.append(first.strftime("%Y-%m"))
        with self.database.connect() as connection:
            rows = connection.execute("""
                SELECT * FROM movement_projection WHERE user_id = ? AND currency = ?
                AND month IN (?, ?, ?)
            """, (user_id, currency, *months)).fetchall()
        income_months = {row["month"] for row in rows if row["type"] == "income"}
        if not income_months:
            raise HTTPException(422, "No hay suficientes ingresos en los tres meses anteriores para recomendar un presupuesto.")
        average = (sum((Decimal(row["amount"]) for row in rows if row["type"] == "income"), Decimal(0)) / len(income_months)).quantize(CENT)
        available = (average * Decimal("0.80")).quantize(CENT)
        if available < CENT:
            raise HTTPException(422, "El ingreso histórico es insuficiente para distribuir un presupuesto.")
        weights = {}
        for row in rows:
            if row["type"] == "expense":
                weights[row["category"]] = weights.get(row["category"], Decimal(0)) + Decimal(row["amount"])
        if not weights:
            weights = {"food": Decimal(30), "transport": Decimal(15), "services": Decimal(25),
                       "education": Decimal(10), "entertainment": Decimal(10), "other": Decimal(10)}
        total = sum(weights.values())
        items = []
        remaining = available
        sorted_weights = sorted(weights.items())
        for index, (category, weight) in enumerate(sorted_weights):
            allocation = (available * weight / total).quantize(CENT, rounding=ROUND_HALF_UP)
            allocation = min(remaining, allocation) if index < len(sorted_weights) - 1 else remaining
            remaining -= allocation
            if allocation > 0:
                items.append({"category": category, "limit_amount": allocation, "share": (weight / total * 100).quantize(CENT)})
        return {"month": month, "currency": currency, "average_monthly_income": average,
                "savings_target": average - available, "historical_months": sorted(income_months),
                "explanation": "Promedio de ingresos de los meses con ingresos dentro de los tres meses anteriores; reserva del 20 % y distribución según gastos históricos. Sin gastos previos se usa una distribución base.",
                "budgets": items}

    def weekly_report(self, user_id: str, week_start: date, currency: str):
        start = week_start - timedelta(days=week_start.weekday())
        end = start + timedelta(days=7)
        with self.database.connect() as connection:
            rows = connection.execute("""
                SELECT * FROM movement_projection WHERE user_id = ? AND currency = ?
                AND substr(occurred_at, 1, 10) >= ? AND substr(occurred_at, 1, 10) < ?
            """, (user_id, currency, start.isoformat(), end.isoformat())).fetchall()
        income = sum((Decimal(row["amount"]) for row in rows if row["type"] == "income"), Decimal(0))
        expenses = sum((Decimal(row["amount"]) for row in rows if row["type"] == "expense"), Decimal(0))
        categories = {}
        for row in rows:
            if row["type"] == "expense":
                categories[row["category"]] = categories.get(row["category"], Decimal(0)) + Decimal(row["amount"])
        budgets = self.list(user_id, start.strftime("%Y-%m"), currency)
        suggestions = []
        if not rows:
            suggestions.append("No se encontraron datos suficientes para generar un reporte detallado.")
        if expenses > income and rows:
            suggestions.append("Los gastos de esta semana superan los ingresos registrados; revisa tus gastos variables.")
        for budget in budgets:
            if budget["status"] != "on_track":
                suggestions.append(f"Revisa el presupuesto de {budget['category']}: has usado {budget['percentage']} % del límite mensual.")
        if rows and not suggestions:
            suggestions.append("Mantén el seguimiento de tus gastos y reserva una parte de tus ingresos para el ahorro.")
        return {"week_start": start, "week_end": end - timedelta(days=1), "currency": currency,
                "income": income, "expenses": expenses, "balance": income - expenses,
                "transaction_count": len(rows), "categories": [{"category": key, "expenses": value} for key, value in sorted(categories.items())],
                "budgets": budgets, "suggestions": suggestions, "sufficient_data": bool(rows)}
