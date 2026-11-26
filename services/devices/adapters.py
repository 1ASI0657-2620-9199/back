"""Ports & Adapters is used here to isolate actual vendor payload differences."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from fastapi import HTTPException
from pydantic import ValidationError

from app.eventing import utcnow
from .models import MeterV1Reading, SimulatorReading


@dataclass(frozen=True)
class CanonicalReading:
    reading_id: str
    value_kwh: Decimal
    raw_value: Decimal
    raw_unit: str
    occurred_at: datetime


class DeviceAdapter(Protocol):
    def normalize(self, payload: dict) -> CanonicalReading: ...


class SimulatorAdapter:
    def normalize(self, payload: dict) -> CanonicalReading:
        reading = SimulatorReading.model_validate(payload)
        value = reading.value / 1000 if reading.unit == "Wh" else reading.value
        return CanonicalReading(reading.reading_id, value, reading.value, reading.unit,
                                reading.occurred_at or utcnow())


class MeterV1Adapter:
    def normalize(self, payload: dict) -> CanonicalReading:
        reading = MeterV1Reading.model_validate(payload)
        return CanonicalReading(reading.sampleId, reading.energyWh / 1000, reading.energyWh, "Wh", reading.timestamp)


ADAPTERS: dict[str, DeviceAdapter] = {"simulator": SimulatorAdapter(), "meter_v1": MeterV1Adapter()}


def normalize(source: str, payload: dict) -> CanonicalReading:
    try:
        reading = ADAPTERS[source].normalize(payload)
    except ValidationError as error:
        raise HTTPException(422, "La lectura no corresponde al formato del adaptador configurado.") from error
    occurred_at = reading.occurred_at.astimezone(timezone.utc)
    if occurred_at > utcnow() + timedelta(minutes=5):
        raise HTTPException(422, "La lectura no puede estar más de cinco minutos en el futuro.")
    return CanonicalReading(reading.reading_id, reading.value_kwh, reading.raw_value, reading.raw_unit, occurred_at)
