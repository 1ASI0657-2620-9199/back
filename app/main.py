import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Query, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import HealthResponse, MonthlySummary, TransactionCreate, TransactionResponse
from .repository import FinancialRecordsRepository, IdempotencyConflictError
from .kafka_transport import broker_lifespan, broker_status

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def create_app(database_path: str | None = None, database_url: str | None = None, *, serve_frontend: bool = True) -> FastAPI:
    configured_url = database_url or (os.getenv("DATABASE_URL") if database_path is None else None)
    if os.getenv("RENDER") == "true" and not configured_url and database_path is None:
        raise RuntimeError("Configure DATABASE_URL before deploying to Render")
    repository = FinancialRecordsRepository(
        database_path or os.getenv("FINANZEN_DB_PATH", "data/financial_records.db"),
        database_url=configured_url,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        repository.initialize()
        async with broker_lifespan(application, repository, "financial"):
            yield

    app = FastAPI(
        title="FinanZen Financial Records Service",
        version="0.3.0",
        root_path=os.getenv("SERVICE_ROOT_PATH", ""),
        description=(
            "Registra movimientos idempotentes y consulta el resumen mensual. "
            "Cada movimiento se confirma junto con TransactionRecorded en su Outbox. "
            "Budget, Notification y Device Integration tienen aplicaciones y bases propias."
        ),
        lifespan=lifespan,
    )
    app.state.repository = repository

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(_, __):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "The Idempotency-Key was already used with a different request body."
            },
        )

    @app.get("/health", tags=["Operations"])
    def health():
        return {"status": "ok", "service": "financial-records-service", **broker_status(app, repository)}

    @app.get("/operations/events", tags=["Operations"])
    def events():
        return broker_status(app, repository)

    @app.post(
        "/api/v1/transactions",
        response_model=TransactionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Transactions"],
    )
    def create_transaction(
        transaction: TransactionCreate,
        response: Response,
        x_user_id: str = Header(min_length=1, max_length=80, alias="X-User-Id"),
        idempotency_key: str = Header(min_length=8, max_length=120, alias="Idempotency-Key"),
    ) -> TransactionResponse:
        created, replayed = repository.create_transaction(
            x_user_id, idempotency_key, transaction
        )
        response.headers["Idempotent-Replayed"] = str(replayed).lower()
        if replayed:
            response.status_code = status.HTTP_200_OK
        return created

    @app.get(
        "/api/v1/transactions",
        response_model=list[TransactionResponse],
        tags=["Transactions"],
    )
    def list_transactions(
        x_user_id: str = Header(min_length=1, max_length=80, alias="X-User-Id"),
    ) -> list[TransactionResponse]:
        return repository.list_transactions(x_user_id)

    @app.get(
        "/api/v1/summary",
        response_model=MonthlySummary,
        tags=["Reports"],
    )
    def monthly_summary(
        response: Response,
        month: str = Query(description="Month in YYYY-MM format", examples=["2026-10"]),
        currency: str = Query(default="PEN", pattern=r"^[A-Z]{3}$"),
        x_user_id: str = Header(min_length=1, max_length=80, alias="X-User-Id"),
    ) -> MonthlySummary:
        if not MONTH_PATTERN.fullmatch(month):
            response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"detail": "month must use the YYYY-MM format"},
            )
        return repository.monthly_summary(x_user_id, month, currency)

    frontend_path = Path(
        os.getenv(
            "FINANZEN_FRONTEND_PATH",
            str(Path(__file__).resolve().parents[2] / "front"),
        )
    )
    if serve_frontend and frontend_path.is_dir():
        app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

    return app


app = create_app()
