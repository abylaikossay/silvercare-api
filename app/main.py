from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, selectinload

from .db import Base, engine, get_db
from .models import Intake, Medication, Patient
from .schemas import (
    DeletedOut, IntakeOut, MedicationCreate, MedicationOut, PatientCreate, PatientOut, PatientShort,
    PatientStats, StatsOut, TodayOut,
)

STATIC_DIR = Path(__file__).parent / "static"
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
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html", media_type="text/html")


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


def day_bounds(day) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time())
    return start, start + timedelta(days=1)


def get_patient_or_404(db: Session, patient_id: int) -> Patient:
    patient = db.scalar(
        select(Patient).options(selectinload(Patient.medications)).where(Patient.id == patient_id)
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    return patient


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

    # All of today's intakes for the patient's active medications, including
    # rows not derived from `times` (e.g. created via /demo-slot).
    start, end = day_bounds(today)
    intakes = db.scalars(
        select(Intake)
        .join(Medication)
        .options(selectinload(Intake.medication))
        .where(
            Medication.patient_id == patient.id,
            Medication.active.is_(True),
            Intake.scheduled_at >= start,
            Intake.scheduled_at < end,
        )
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
            {"name": "Амлодипин 5 мг", "dose": "1 таблетка", "times": "08:00,20:00"},
            {"name": "Кардиомагнил", "dose": "1 таблетка", "times": "20:00"},
        ],
    },
    {
        "id": 2,
        "name": "Серик",
        "medications": [
            {"name": "Метформин 850 мг", "dose": "1 таблетка", "times": "08:00,20:00"},
            {"name": "Эналаприл", "dose": "1 таблетка", "times": "09:00"},
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


@app.get("/patients", response_model=list[PatientOut])
def list_patients(db: Session = Depends(get_db)):
    return db.scalars(
        select(Patient).options(selectinload(Patient.medications)).order_by(Patient.id)
    ).all()


@app.get("/patients/{patient_id}/today", response_model=TodayOut)
def patient_today(patient_id: int, db: Session = Depends(get_db)):
    patient = get_patient_or_404(db, patient_id)
    now = now_local()
    intakes = materialize_today(db, patient, now)
    items = [to_intake_out(i) for i in intakes]
    pending = [x for x in items if x.status == "pending"]
    next_item: Optional[IntakeOut] = min(pending, key=lambda x: x.scheduled_at) if pending else None
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


# ---------- management / demo endpoints ----------

@app.post("/patients", response_model=PatientShort, status_code=201)
def create_patient(body: PatientCreate, db: Session = Depends(get_db)):
    patient = Patient(name=body.name)
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@app.post("/patients/{patient_id}/medications", response_model=MedicationOut, status_code=201)
def create_medication(patient_id: int, body: MedicationCreate, db: Session = Depends(get_db)):
    get_patient_or_404(db, patient_id)
    med = Medication(patient_id=patient_id, name=body.name, dose=body.dose, times=body.times, active=True)
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


@app.post("/patients/{patient_id}/demo-slot", response_model=IntakeOut, status_code=201)
def create_demo_slot(
    patient_id: int,
    minutes: int = Query(2, ge=0, le=24 * 60),
    db: Session = Depends(get_db),
):
    patient = get_patient_or_404(db, patient_id)
    med = next((m for m in sorted(patient.medications, key=lambda m: m.id) if m.active), None)
    if med is None:
        raise HTTPException(status_code=400, detail="patient has no active medications")
    scheduled = (now_local() + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
    intake = db.scalar(
        select(Intake).where(Intake.medication_id == med.id, Intake.scheduled_at == scheduled)
    )
    if intake is None:
        intake = Intake(medication_id=med.id, scheduled_at=scheduled, status="pending")
        db.add(intake)
        db.commit()
    db.refresh(intake)
    intake.medication = med
    return to_intake_out(intake)


@app.delete("/patients/{patient_id}/today", response_model=DeletedOut)
def delete_today(patient_id: int, db: Session = Depends(get_db)):
    patient = get_patient_or_404(db, patient_id)
    start, end = day_bounds(now_local().date())
    med_ids = [m.id for m in patient.medications]
    if not med_ids:
        return DeletedOut(deleted=0)
    result = db.execute(
        delete(Intake).where(
            Intake.medication_id.in_(med_ids), Intake.scheduled_at >= start, Intake.scheduled_at < end
        )
    )
    db.commit()
    return DeletedOut(deleted=result.rowcount)


@app.delete("/medications/{medication_id}")
def deactivate_medication(medication_id: int, db: Session = Depends(get_db)):
    med = db.get(Medication, medication_id)
    if med is None:
        raise HTTPException(status_code=404, detail="medication not found")
    med.active = False
    db.commit()
    return {"id": med.id, "active": False}


@app.delete("/patients/{patient_id}")
def delete_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = get_patient_or_404(db, patient_id)
    med_ids = [m.id for m in patient.medications]
    if med_ids:
        db.execute(delete(Intake).where(Intake.medication_id.in_(med_ids)))
        db.execute(delete(Medication).where(Medication.id.in_(med_ids)))
    db.delete(patient)
    db.commit()
    return {"deleted": patient_id}
