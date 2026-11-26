import os
from contextlib import asynccontextmanager

from fastapi import Query, Response
from app.kafka_transport import broker_lifespan

from services.common import Month, UserId, service_app
from .models import ConsumptionSummary, DeviceCreate, DeviceUpdate, DeviceView, MeterV1Reading, ReadingView, SimulatorReading
from .repository import DeviceRepository


def create_app(database_path: str | None = None, database_url: str | None = None):
    repository = DeviceRepository(database_path or os.getenv("TELEMETRY_DB_PATH", "data/telemetry.db"),
                                  database_url or (os.getenv("TELEMETRY_DATABASE_URL") if database_path is None else None))

    @asynccontextmanager
    async def lifespan(application):
        repository.initialize()
        async with broker_lifespan(application, repository, "devices"):
            yield

    application = service_app("Device Integration Service", lifespan, repository)
    application.state.repository = repository

    @application.get("/api/v1/devices", response_model=list[DeviceView], tags=["Devices"])
    def devices(x_user_id: UserId):
        return repository.list(x_user_id)

    @application.post("/api/v1/devices", response_model=DeviceView, status_code=201, tags=["Devices"])
    def create_device(data: DeviceCreate, x_user_id: UserId):
        return repository.create(x_user_id, data)

    @application.put("/api/v1/devices/{device_id}", response_model=DeviceView, tags=["Devices"])
    def update_device(device_id: str, data: DeviceUpdate, x_user_id: UserId):
        return repository.update(x_user_id, device_id, data)

    @application.post("/api/v1/devices/{device_id}/connection", response_model=DeviceView, tags=["Devices"])
    def connect(device_id: str, x_user_id: UserId):
        return repository.set_connection(x_user_id, device_id, True)

    @application.delete("/api/v1/devices/{device_id}/connection", response_model=DeviceView, tags=["Devices"])
    def disconnect(device_id: str, x_user_id: UserId):
        return repository.set_connection(x_user_id, device_id, False)

    @application.get("/api/v1/devices/{device_id}/sync-status", response_model=DeviceView, tags=["Devices"])
    def sync_status(device_id: str, x_user_id: UserId):
        return repository.sync_status(x_user_id, device_id)

    def receive(device_id, data, x_user_id, response, simulate=False):
        reading, replayed = repository.ingest(x_user_id, device_id, data.model_dump(mode="json"), simulate=simulate)
        response.status_code = 200 if replayed else 201
        response.headers["Idempotent-Replayed"] = str(replayed).lower()
        return reading

    @application.post("/api/v1/devices/{device_id}/readings", response_model=ReadingView, status_code=201, tags=["Telemetry"])
    def ingest(device_id: str, data: SimulatorReading | MeterV1Reading, x_user_id: UserId, response: Response):
        return receive(device_id, data, x_user_id, response)

    @application.post("/api/v1/devices/{device_id}/simulate", response_model=ReadingView, status_code=201, tags=["Telemetry"])
    def simulate(device_id: str, data: SimulatorReading, x_user_id: UserId, response: Response):
        return receive(device_id, data, x_user_id, response, True)

    @application.get("/api/v1/devices/{device_id}/readings", response_model=list[ReadingView], tags=["Telemetry"])
    def readings(device_id: str, month: Month, x_user_id: UserId, limit: int = Query(default=100, ge=1, le=500)):
        return repository.readings(x_user_id, device_id, month, limit)

    @application.get("/api/v1/devices/{device_id}/consumption", response_model=ConsumptionSummary, tags=["Telemetry"])
    def consumption(device_id: str, month: Month, x_user_id: UserId):
        return repository.consumption(x_user_id, device_id, month)

    return application


app = create_app()
