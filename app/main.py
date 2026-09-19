from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from .db import Base, engine, get_db
from .models import Intake, Medication, Patient
from .schemas import IntakeOut, PatientOut, PatientStats, StatsOut, TodayOut

TZ = ZoneInfo("Asia/Almaty")
MISSED_AFTER = timedelta(minutes=60)


def now_local() -> datetime:
    """Local Asia/Almaty time as a naive datetime (matches DB storage)."""
    return datetime.now(TZ).replace(tzinfo=None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="SilverCare API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- helpers ----------

def recompute_status(intake: Intake, now: datetime) -> None:
    if intake.taken_at is not None:
        intake.status = "taken"
    elif intake.scheduled_at + MISSED_AFTER < now:
        intake.status = "missed"
    else:
        intake.status = "pending"


def to_intake_out(i: Intake) -> IntakeOut:
    return IntakeOut(
        intake_id=i.id,
        medication_name=i.medication.name,
        dose=i.medication.dose,
        scheduled_at=i.scheduled_at,
        taken_at=i.taken_at,
        status=i.status,
    )


def materialize_today(db: Session, patient: Patient, now: datetime) -> list[Intake]:
    """Create today's intake rows for every active medication (idempotent)."""
    today = now.date()
    for med in patient.medications:
        if not med.active:
            continue
        for slot in med.times.split(","):
            slot = slot.strip()
            if not slot:
                continue
            hh, mm = slot.split(":")
            scheduled = datetime.combine(today, datetime.min.time()).replace(hour=int(hh), minute=int(mm))
            exists = db.scalar(
                select(Intake.id).where(Intake.medication_id == med.id, Intake.scheduled_at == scheduled)
            )
            if exists is None:
                db.add(Intake(medication_id=med.id, scheduled_at=scheduled, status="pending"))
    db.flush()

    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(days=1)
    intakes = db.scalars(
        select(Intake)
        .join(Medication)
        .options(selectinload(Intake.medication))
        .where(Medication.patient_id == patient.id, Intake.scheduled_at >= start, Intake.scheduled_at < end)
        .order_by(Intake.scheduled_at, Intake.id)
    ).all()
    for i in intakes:
        recompute_status(i, now)
    db.commit()
    return intakes


# ---------- endpoints ----------

@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"status": "error", "db": str(e)})
    return {"status": "ok", "db": "ok"}


SEED = [
    {
        "id": 1,
        "name": "Айгуль",
        "medications": [
            {"name": "Амлодипин", "dose": "1 таблетка", "times": "08:00,20:00"},
            {"name": "Метформин", "dose": "1 таблетка", "times": "08:00,14:00,20:00"},
        ],
    },
    {
        "id": 2,
        "name": "Серик",
        "medications": [
            {"name": "Аспирин", "dose": "1 таблетка", "times": "09:00"},
        ],
    },
]


@app.post("/seed", response_model=list[PatientOut])
def seed(db: Session = Depends(get_db)):
    for p in SEED:
        patient = db.get(Patient, p["id"])
        if patient is None:
            patient = Patient(id=p["id"], name=p["name"])
            db.add(patient)
            db.flush()
        for m in p["medications"]:
            exists = db.scalar(
                select(Medication.id).where(Medication.patient_id == patient.id, Medication.name == m["name"])
            )
            if exists is None:
                db.add(Medication(patient_id=patient.id, active=True, **m))
    db.commit()
    # keep the sequence in sync after explicit ids
    db.execute(text("SELECT setval(pg_get_serial_sequence('patients', 'id'), (SELECT MAX(id) FROM patients))"))
    db.commit()
    patients = db.scalars(
        select(Patient).options(selectinload(Patient.medications)).order_by(Patient.id)
    ).all()
    return patients


@app.get("/patients/{patient_id}/today", response_model=TodayOut)
def patient_today(patient_id: int, db: Session = Depends(get_db)):
    patient = db.scalar(
        select(Patient).options(selectinload(Patient.medications)).where(Patient.id == patient_id)
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    now = now_local()
    intakes = materialize_today(db, patient, now)
    items = [to_intake_out(i) for i in intakes]
    next_item: Optional[IntakeOut] = next((x for x in items if x.status == "pending"), None)
    return TodayOut(patient_id=patient.id, patient_name=patient.name, now=now, items=items, next=next_item)


@app.post("/intakes/{intake_id}/take", response_model=IntakeOut)
def take_intake(intake_id: int, db: Session = Depends(get_db)):
    intake = db.scalar(
        select(Intake).options(selectinload(Intake.medication)).where(Intake.id == intake_id)
    )
    if intake is None:
        raise HTTPException(status_code=404, detail="intake not found")
    if intake.taken_at is None:
        intake.taken_at = now_local()
    intake.status = "taken"
    db.commit()
    db.refresh(intake)
    return to_intake_out(intake)


@app.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)):
    now = now_local()
    patients = db.scalars(select(Patient).order_by(Patient.id)).all()
    intakes = db.scalars(
        select(Intake).options(selectinload(Intake.medication))
    ).all()
    for i in intakes:
        recompute_status(i, now)
    db.commit()

    per_patient: dict[int, dict[str, int]] = {p.id: {"taken": 0, "missed": 0} for p in patients}
    for i in intakes:
        pid = i.medication.patient_id
        if i.status in ("taken", "missed") and pid in per_patient:
            per_patient[pid][i.status] += 1

    def pct(missed: int, total: int) -> float:
        return round(missed * 100 / total, 1) if total else 0.0

    rows = []
    for p in patients:
        c = per_patient[p.id]
        total = c["taken"] + c["missed"]
        rows.append(PatientStats(
            patient_id=p.id, name=p.name, total=total, taken=c["taken"], missed=c["missed"],
            missed_pct=pct(c["missed"], total),
        ))
    total = sum(r.total for r in rows)
    taken = sum(r.taken for r in rows)
    missed = sum(r.missed for r in rows)
    return StatsOut(patients=rows, total=total, taken=taken, missed=missed, missed_pct=pct(missed, total))
