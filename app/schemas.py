from datetime import datetime
from typing import Optional

import re

from pydantic import BaseModel, field_validator

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class MedicationOut(BaseModel):
    id: int
    name: str
    dose: str
    times: str
    active: bool


class PatientOut(BaseModel):
    id: int
    name: str
    medications: list[MedicationOut]


class IntakeOut(BaseModel):
    intake_id: int
    medication_name: str
    dose: str
    scheduled_at: datetime
    taken_at: Optional[datetime] = None
    status: str


class TodayOut(BaseModel):
    patient_id: int
    patient_name: str
    now: datetime
    items: list[IntakeOut]
    next: Optional[IntakeOut] = None


class PatientStats(BaseModel):
    patient_id: int
    name: str
    total: int
    taken: int
    missed: int
    missed_pct: float


class StatsOut(BaseModel):
    patients: list[PatientStats]
    total: int
    taken: int
    missed: int
    missed_pct: float


class PatientCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class PatientShort(BaseModel):
    id: int
    name: str


class MedicationCreate(BaseModel):
    name: str
    dose: str
    times: str  # "08:00,20:00"

    @field_validator("times")
    @classmethod
    def validate_times(cls, v: str) -> str:
        slots = [t.strip() for t in v.split(",")]
        if not slots or any(not TIME_RE.match(t) for t in slots):
            raise ValueError('times must be HH:MM values separated by commas, e.g. "08:00,20:00"')
        return ",".join(slots)


class DeletedOut(BaseModel):
    deleted: int
