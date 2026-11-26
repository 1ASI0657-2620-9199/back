import os
from contextlib import asynccontextmanager

from fastapi import Depends, Query

from app.eventing import Event
from app.kafka_transport import broker_lifespan
from services.common import UserId, internal_event_key, service_app
from .models import NotificationPreferences, NotificationView
from .repository import NotificationRepository


def create_app(database_path: str | None = None, database_url: str | None = None, *, provider=None):
    repository = NotificationRepository(
        database_path or os.getenv("NOTIFICATION_DB_PATH", "data/notifications.db"),
        database_url or (os.getenv("NOTIFICATION_DATABASE_URL") if database_path is None else None), provider)

    @asynccontextmanager
    async def lifespan(application):
        repository.initialize()
        async with broker_lifespan(application, repository, "notifications"):
            yield

    application = service_app("Notification Service", lifespan, repository)
    application.state.repository = repository

    @application.get("/api/v1/notifications", response_model=list[NotificationView], tags=["Notifications"])
    def notifications(x_user_id: UserId, unread_only: bool = False, limit: int = Query(default=100, ge=1, le=500)):
        return repository.list(x_user_id, unread_only=unread_only, limit=limit)

    @application.patch("/api/v1/notifications/{notification_id}/read", response_model=NotificationView, tags=["Notifications"])
    def mark_read(notification_id: str, x_user_id: UserId):
        return repository.mark_read(x_user_id, notification_id)

    @application.post("/api/v1/notifications/{notification_id}/retry", response_model=NotificationView, tags=["Notifications"])
    def retry(notification_id: str, x_user_id: UserId):
        return repository.retry(x_user_id, notification_id)

    @application.get("/api/v1/notification-preferences", response_model=NotificationPreferences, tags=["Notifications"])
    def preferences(x_user_id: UserId):
        return repository.preferences(x_user_id)

    @application.put("/api/v1/notification-preferences", response_model=NotificationPreferences, tags=["Notifications"])
    def save_preferences(data: NotificationPreferences, x_user_id: UserId):
        return repository.save_preferences(x_user_id, data)

    @application.post("/internal/events", dependencies=[Depends(internal_event_key)], tags=["Internal"])
    def consume(event: Event):
        return {"processed": repository.consume(event)}

    return application


app = create_app()
