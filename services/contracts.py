"""Version 1 integration contracts validated before consumer side effects."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class BudgetThresholdPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=80)
    budget_id: str
    category: str
    month: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    threshold: int = Field(ge=1, le=100)
    spent: Decimal = Field(ge=0)
    limit_amount: Decimal = Field(gt=0)


class ConsumptionPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=80)
    device_id: str
    device_name: str
    reading_id: str
    month: str
    monthly_kwh: Decimal = Field(ge=0)
    monthly_limit_kwh: Decimal = Field(gt=0)
    threshold_reached: bool
    unit: Literal["kWh"]
