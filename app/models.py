from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))

    medications: Mapped[list["Medication"]] = relationship(back_populates="patient")


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    name: Mapped[str] = mapped_column(String(200))
    dose: Mapped[str] = mapped_column(String(200))  # e.g. "1 таблетка"
    times: Mapped[str] = mapped_column(String(200))  # e.g. "08:00,14:00,20:00"
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    patient: Mapped["Patient"] = relationship(back_populates="medications")
    intakes: Mapped[list["Intake"]] = relationship(back_populates="medication")


class Intake(Base):
    __tablename__ = "intakes"
    __table_args__ = (UniqueConstraint("medication_id", "scheduled_at", name="uq_intake_med_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medications.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))  # naive, Asia/Almaty
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | taken | missed

    medication: Mapped["Medication"] = relationship(back_populates="intakes")
