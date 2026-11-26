from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget_enabled: bool = True
    device_enabled: bool = True


class NotificationView(BaseModel):
    id: str
    kind: Literal["budget", "device"]
    title: str
    message: str
    status: Literal["pending", "retrying", "delivered", "failed"]
    attempts: int
    created_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None
    last_error: str | None
    metadata: dict
