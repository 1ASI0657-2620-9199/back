from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Energy = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=6)]
Tariff = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=4)]


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    source: Literal["simulator", "meter_v1"] = "simulator"
    monthly_limit_kwh: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)] = Decimal("100")
    tariff_per_kwh: Tariff = Decimal("0.75")


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    monthly_limit_kwh: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
    tariff_per_kwh: Tariff


class DeviceView(DeviceCreate):
    model_config = ConfigDict(extra="ignore")
    id: str
    active: bool
    created_at: datetime
    last_sync_at: datetime | None
    reading_count: int


class SimulatorReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=120)
    value: Energy
    unit: Literal["Wh", "kWh"] = "kWh"
    occurred_at: AwareDatetime | None = None


class MeterV1Reading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sampleId: str = Field(min_length=1, max_length=120)
    energyWh: Energy
    timestamp: AwareDatetime


class ReadingView(BaseModel):
    id: str
    reading_id: str
    device_id: str
    measurement_type: Literal["energy_consumption"]
    value: Decimal
    unit: Literal["kWh"]
    raw_value: Decimal
    raw_unit: Literal["Wh", "kWh"]
    source: Literal["simulator", "meter_v1"]
    occurred_at: datetime
    received_at: datetime
    validation_status: Literal["valid"]
    estimated_cost: Decimal
    currency: Literal["PEN"]


class DailyConsumption(BaseModel):
    date: str
    kwh: Decimal
    estimated_cost: Decimal


class ConsumptionSummary(BaseModel):
    device_id: str
    month: str
    reading_count: int
    kwh: Decimal
    estimated_cost: Decimal
    currency: Literal["PEN"]
    monthly_limit_kwh: Decimal
    percentage: Decimal
    daily: list[DailyConsumption]
    disclaimer: str
