import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import AfterValidator, Field, ValidationError

from app.eventing import EventConflict

UserId = Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=80)]


def valid_month(value: str) -> str:
    try:
        parsed = date.fromisoformat(value + "-01")
        if parsed.strftime("%Y-%m") != value:
            raise ValueError
    except ValueError:
        raise ValueError("El periodo debe usar YYYY-MM.")
    return value


Month = Annotated[str, AfterValidator(valid_month)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]


def as_utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_month(value: str | datetime) -> str:
    return as_utc(value).strftime("%Y-%m")


def service_app(title: str, lifespan, repository=None) -> FastAPI:
    application = FastAPI(title="FinanZen " + title, version="0.3.0", lifespan=lifespan,
                          root_path=os.getenv("SERVICE_ROOT_PATH", ""))

    @application.exception_handler(ValidationError)
    async def invalid_contract(_, exception):
        import json
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": json.loads(exception.json(include_url=False))})

    @application.exception_handler(EventConflict)
    async def event_conflict(_, exception):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exception)})

    @application.get("/health", tags=["Operations"])
    def health():
        from app.kafka_transport import broker_status
        return {"status": "ok", "service": title,
                **(broker_status(application, repository) if repository else {})}

    @application.get("/operations/events", tags=["Operations"])
    def events():
        from app.kafka_transport import broker_status
        return broker_status(application, repository) if repository else {"transport": "disabled"}

    return application


def internal_event_key(x_event_key: Annotated[str | None, Header()] = None):
    expected = os.getenv("FINANZEN_INTERNAL_EVENT_KEY")
    if not expected:
        raise HTTPException(503, "El ingreso HTTP de eventos está deshabilitado; configure FINANZEN_INTERNAL_EVENT_KEY.")
    import secrets
    if not x_event_key or not secrets.compare_digest(x_event_key, expected):
        raise HTTPException(403, "Credencial interna no válida.")
