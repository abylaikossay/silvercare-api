from datetime import datetime
from typing import Optional

from pydantic import BaseModel


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
