"""Local API composition. Each service can also run as its own ASGI app."""
import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from app.main import create_app as financial_app
from app.runtime import LocalEventDispatcher
from services.budgets.main import create_app as budget_app
from services.devices.main import create_app as device_app
from services.notifications.main import create_app as notification_app


def create_app(data_dir: str | Path | None = None, *, start_worker: bool = True, notification_provider=None):
    if os.getenv("EVENT_TRANSPORT") == "kafka":
        raise ValueError("Kafka uses standalone service processes. Start Docker Compose instead of app.suite.")
    isolated = data_dir is not None
    directory = Path(data_dir or os.getenv("FINANZEN_DATA_DIR", "data"))
    database_url = lambda name: None if isolated else os.getenv(name)
    applications = {
        "financial": financial_app(str(directory / "financial_records.db"), database_url("DATABASE_URL"), serve_frontend=False),
        "budgets": budget_app(str(directory / "budgets.db"), database_url("BUDGET_DATABASE_URL")),
        "notifications": notification_app(str(directory / "notifications.db"), database_url("NOTIFICATION_DATABASE_URL"), provider=notification_provider),
        "devices": device_app(str(directory / "telemetry.db"), database_url("TELEMETRY_DATABASE_URL")),
    }
    stores = {name: application.state.repository for name, application in applications.items()}
    runtime = LocalEventDispatcher(stores["financial"], stores["budgets"], stores["notifications"], stores["devices"])

    @asynccontextmanager
    async def lifespan(_):
        async with AsyncExitStack() as stack:
            for application in applications.values():
                await stack.enter_async_context(application.router.lifespan_context(application))
            stop = asyncio.Event()
            task = asyncio.create_task(runtime.run(stop)) if start_worker else None
            try:
                yield
            finally:
                stop.set()
                if task:
                    await task

    application = FastAPI(
        title="FinanZen — APIs integradas", version="0.2.0", lifespan=lifespan,
        description="Desarrollo local de Financial Records, Budget, Notification y Device Integration. "
                    "Persistencias separadas y despacho asíncrono local con Outbox. X-User-Id es una identidad demo, no autenticación.",
    )
    application.state.services = stores
    application.state.runtime = runtime
    for name, child in applications.items():
        # Share API routes for one same-origin frontend and one combined OpenAPI.
        router = APIRouter()
        router.routes = [route for route in child.router.routes if route.path.startswith("/api/v1/")]
        application.include_router(router)
        application.exception_handlers.update(child.exception_handlers)
        # Preserve each application's own Swagger and authenticated event ingress.
        application.mount("/services/" + name, child)

    @application.get("/health", tags=["Operations"])
    def health():
        return {"status": "ok", "service": "finanzen-local-suite", **runtime.status()}

    frontend = Path(os.getenv("FINANZEN_FRONTEND_PATH", str(Path(__file__).resolve().parents[2] / "front")))
    if frontend.is_dir():
        application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return application


app = create_app()
