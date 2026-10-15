"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from starlette.responses import JSONResponse

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uniq_emp_date"
    )
    db.attendance_logs.create_index([("date", ASCENDING)])
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)])
    db.attendance_logs.create_index([("late_minutes", DESCENDING)])
    db.employees.create_index([("emp_code", ASCENDING)], unique=True)
    db.employees.create_index([("department", ASCENDING)])
    db.employees.create_index([("joined_on", ASCENDING)])

# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #

def to_ms(dt: datetime | None) -> int | None:
    """Convert datetime to epoch milliseconds"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def from_ms(ms: int) -> datetime:
    """Convert epoch milliseconds to timezone-aware datetime (UTC)"""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def truncate_to_seconds(dt: datetime) -> datetime:
    """R1: truncate to whole seconds"""
    return dt.replace(microsecond=0)


def get_ist_date(dt: datetime) -> str:
    """Return IST calendar date as YYYY-MM-DD"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(IST)
    return ist_dt.date().isoformat()


def half_up_round(value: float, places: int = 2) -> float:
    """R4 & R8: half-up rounding"""
    d = Decimal(str(value))
    quantize_str = "1." + "0" * places
    return float(d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP))


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    """R2: Late calculation"""
    punch_in = truncate_to_seconds(punch_in)
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    
    ist_punch = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    
    shift_start_dt = ist_punch.replace(hour=h, minute=m, second=0, microsecond=0)
    
    diff_seconds = (ist_punch - shift_start_dt).total_seconds()
    if diff_seconds <= 600:  # 10 minutes grace
        return 0
    return int(diff_seconds // 60)  # floor minutes since shift_start


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: work hours with half-up rounding"""
    punch_in = truncate_to_seconds(punch_in)
    punch_out = truncate_to_seconds(punch_out)
    hours = (punch_out - punch_in).total_seconds() / 3600
    return half_up_round(hours, 2)


def compute_overtime(punch_out: datetime, shift_end: str, shift_start: str, date_str: str) -> int:
    """R3 + R1: Overtime with overnight support"""
    punch_out = truncate_to_seconds(punch_out)
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    
    ist_out = punch_out.astimezone(IST)
    h, m = map(int, shift_end.split(":"))
    sh, sm = map(int, shift_start.split(":"))
    
    # Base date of the shift
    base_date = date.fromisoformat(date_str)
    
    # Overnight shift?
    is_overnight = (h < sh) or (h == sh and m <= sm)
    
    if is_overnight:
        # shift_end is on the next calendar day
        end_date = base_date + timedelta(days=1)
    else:
        end_date = base_date
    
    shift_end_dt = datetime(
        end_date.year, end_date.month, end_date.day,
        h, m, 0, tzinfo=IST
    )
    
    diff_seconds = (ist_out - shift_end_dt).total_seconds()
    minutes = int(diff_seconds // 60)
    
    if minutes >= 30:
        return minutes
    return 0


def format_employee(doc: dict) -> dict:
    """Format employee document for API response"""
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_ms(doc.get("created_at")),
    }


def format_attendance(doc: dict) -> dict:
    """Format attendance document for API response (no id field)"""
    history = []
    for h in doc.get("history") or []:
        changes = {}
        for field, change in (h.get("changes") or {}).items():
            changes[field] = {
                "from": to_ms(change["from"]) if isinstance(change.get("from"), datetime) else change.get("from"),
                "to": to_ms(change["to"]) if isinstance(change.get("to"), datetime) else change.get("to"),
            }
        history.append({
            "at": to_ms(h.get("at")),
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": changes,
        })
    
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_ms(doc.get("punch_in")),
        "punch_out": to_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history,
    }

# --------------------------------------------------------------------------- #
# Pydantic Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeIn(BaseModel):
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    status: Optional[str] = None
    reason: str
    by: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(status_code=409, detail="emp_code already exists")
    
    doc = body.model_dump()
    doc["created_at"] = datetime.now(tz=timezone.utc)
    
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    
    return format_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department
    
    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    
    cursor = (
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )
    items = [format_employee(d) for d in cursor]
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")
    
    if body.status not in ("PRESENT", "WFH", "ON_DUTY"):
        raise HTTPException(status_code=422, detail="invalid status for punch-in")
    
    if body.punched_at is not None:
        if body.punched_at < 100_000_000_000:  # probably seconds instead of ms
            raise HTTPException(status_code=422, detail="punched_at must be epoch milliseconds")
        ts = from_ms(body.punched_at)
    else:
        ts = datetime.now(tz=timezone.utc)
    
    ts = truncate_to_seconds(ts)
    
    # R1: attendance date is IST calendar date of punch_in
    # Overnight handling: if punch is before shift_end and shift is overnight → previous day
    d = get_ist_date(ts)
    
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    is_overnight = (eh < sh) or (eh == sh and em <= sm)
    
    if is_overnight:
        ist_ts = ts.astimezone(IST) if ts.tzinfo else ts.replace(tzinfo=timezone.utc).astimezone(IST)
        punch_time = ist_ts.hour * 60 + ist_ts.minute
        end_minutes = eh * 60 + em
        if punch_time < end_minutes:
            # belongs to previous day's shift
            prev = date.fromisoformat(d) - timedelta(days=1)
            d = prev.isoformat()
    
    late = compute_late_minutes(ts, shift_start)
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")
    
    return format_attendance(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    
    cursor = (
        db.attendance_logs.find(q)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )
    
    items = [format_attendance(d) for d in cursor]
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")
    
    if body.punched_at is not None:
        if body.punched_at < 100_000_000_000:
            raise HTTPException(status_code=422, detail="punched_at must be epoch milliseconds")
        ts = from_ms(body.punched_at)
    else:
        ts = datetime.now(tz=timezone.utc)
    
    ts = truncate_to_seconds(ts)
    
    # Find the open attendance record (most recent with no punch_out)
    # We look for the record whose date matches the expected shift date
    d = get_ist_date(ts)
    
    # Also check previous day for overnight shifts
    candidates = [d]
    prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
    candidates.append(prev)
    
    record = None
    for candidate_date in candidates:
        rec = db.attendance_logs.find_one({
            "emp_code": body.emp_code,
            "date": candidate_date,
            "punch_out": None
        })
        if rec:
            record = rec
            break
    
    if not record:
        raise HTTPException(status_code=404, detail="no open punch-in found")
    
    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="already punched out")
    
    work_hours = compute_work_hours(record["punch_in"], ts)
    overtime = compute_overtime(
        ts,
        emp["shift_end"],
        emp["shift_start"],
        record["date"]
    )
    half_day = work_hours < 4.50
    
    result = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": half_day,
        }},
        return_document=True
    )
    
    if result is None:
        raise HTTPException(status_code=409, detail="already punched out")
    
    return format_attendance(result)


@app.patch("/attendance/{emp_code}/{date}")
def regularize(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")
    
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(status_code=404, detail="attendance record not found")
    
    changes = {}
    update_fields = {}
    
    # Handle status change
    if body.status is not None:
        if body.status not in ("PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"):
            raise HTTPException(status_code=422, detail="invalid status")
        if body.status != record.get("status"):
            changes["status"] = {"from": record.get("status"), "to": body.status}
            update_fields["status"] = body.status
    
    # Handle punch_in change
    new_punch_in = record.get("punch_in")
    if body.punch_in is not None:
        if body.punch_in < 100_000_000_000:
            raise HTTPException(status_code=422, detail="punch_in must be epoch milliseconds")
        new_punch_in = truncate_to_seconds(from_ms(body.punch_in))
        old_ms = to_ms(record.get("punch_in"))
        new_ms = to_ms(new_punch_in)
        if old_ms != new_ms:
            changes["punch_in"] = {"from": record.get("punch_in"), "to": new_punch_in}
            update_fields["punch_in"] = new_punch_in
    
    # Handle punch_out change
    new_punch_out = record.get("punch_out")
    if body.punch_out is not None:
        if body.punch_out < 100_000_000_000:
            raise HTTPException(status_code=422, detail="punch_out must be epoch milliseconds")
        new_punch_out = truncate_to_seconds(from_ms(body.punch_out))
        old_ms = to_ms(record.get("punch_out"))
        new_ms = to_ms(new_punch_out)
        if old_ms != new_ms:
            changes["punch_out"] = {"from": record.get("punch_out"), "to": new_punch_out}
            update_fields["punch_out"] = new_punch_out
    
    # Recompute derived fields if punches changed or status changed to present-type
    final_status = update_fields.get("status", record.get("status"))
    
    if new_punch_in and new_punch_out:
        work_hours = compute_work_hours(new_punch_in, new_punch_out)
        late = compute_late_minutes(new_punch_in, emp["shift_start"])
        overtime = compute_overtime(new_punch_out, emp["shift_end"], emp["shift_start"], date)
        half_day = work_hours < 4.50
        
        if work_hours != record.get("work_hours"):
            changes["work_hours"] = {"from": record.get("work_hours"), "to": work_hours}
            update_fields["work_hours"] = work_hours
        if late != record.get("late_minutes", 0):
            changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": late}
            update_fields["late_minutes"] = late
        if overtime != record.get("overtime_minutes", 0):
            changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": overtime}
            update_fields["overtime_minutes"] = overtime
        if half_day != record.get("half_day", False):
            changes["half_day"] = {"from": record.get("half_day", False), "to": half_day}
            update_fields["half_day"] = half_day
    elif final_status in ("ABSENT", "LEAVE"):
        # Clear punches and derived fields
        if record.get("punch_in") is not None:
            changes["punch_in"] = {"from": record.get("punch_in"), "to": None}
            update_fields["punch_in"] = None
        if record.get("punch_out") is not None:
            changes["punch_out"] = {"from": record.get("punch_out"), "to": None}
            update_fields["punch_out"] = None
        if record.get("work_hours") is not None:
            changes["work_hours"] = {"from": record.get("work_hours"), "to": None}
            update_fields["work_hours"] = None
        if record.get("late_minutes", 0) != 0:
            changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": 0}
            update_fields["late_minutes"] = 0
        if record.get("overtime_minutes", 0) != 0:
            changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": 0}
            update_fields["overtime_minutes"] = 0
        if record.get("half_day", False):
            changes["half_day"] = {"from": True, "to": False}
            update_fields["half_day"] = False
    
    if not changes:
        return format_attendance(record)
    
    history_entry = {
        "at": datetime.now(tz=timezone.utc),
        "by": body.by,
        "reason": body.reason,
        "changes": changes,
    }
    
    result = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"]},
        {
            "$set": update_fields,
            "$push": {"history": history_entry},
        },
        return_document=True
    )
    
    return format_attendance(result)

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")
    
    year, mon = map(int, month.split("-"))
    # First and last day of the month
    first_day = f"{year}-{mon:02d}-01"
    if mon == 12:
        last_day = f"{year}-12-31"
    else:
        next_month = date(year, mon + 1, 1)
        last_day = (next_month - timedelta(days=1)).isoformat()
    
    joined = emp["joined_on"]
    
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": first_day, "$lte": last_day}
            }
        },
        {
            "$group": {
                "_id": None,
                "total_work_hours": {"$sum": {"$ifNull": ["$work_hours", 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    # Only count Mon-Fri (dayOfWeek 2-6 in MongoDB, where 1=Sun)
                                    {
                                        "$and": [
                                            {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                                            {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}
                                        ]
                                    }
                                ]
                            },
                            {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                            0
                        ]
                    }
                },
                "records": {"$push": "$$ROOT"}
            }
        }
    ]
    
    result = list(db.attendance_logs.aggregate(pipeline))
    
    if not result:
        present_days = 0.0
        total_work_hours = 0.0
        total_late = 0
        total_ot = 0
    else:
        r = result[0]
        present_days = r.get("present_days", 0)
        total_work_hours = half_up_round(r.get("total_work_hours", 0), 2)
        total_late = r.get("total_late_minutes", 0)
        total_ot = r.get("total_overtime_minutes", 0)
    
    # Working days in the month from joined_on (Mon-Fri only)
    start = max(date.fromisoformat(first_day), date.fromisoformat(joined))
    end = date.fromisoformat(last_day)
    working_days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            working_days += 1
        current += timedelta(days=1)
    
    attendance_rate = 0.0
    if working_days > 0:
        attendance_rate = half_up_round(present_days / working_days, 4)
    
    return {
        "emp_code": emp_code,
        "name": emp["name"],
        "department": emp["department"],
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "attendance_rate": attendance_rate,
        "total_work_hours": total_work_hours,
        "total_late_minutes": total_late,
        "total_overtime_minutes": total_ot,
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    year, mon = map(int, month.split("-"))
    first_day = f"{year}-{mon:02d}-01"
    if mon == 12:
        last_day = f"{year}-12-31"
    else:
        next_month = date(year, mon + 1, 1)
        last_day = (next_month - timedelta(days=1)).isoformat()
    
    # R9: employees who joined on or before last_day
    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": last_day}
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", first_day]},
                                    {"$lte": ["$date", last_day]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$project": {
                "department": 1,
                "emp_code": 1,
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {
                                        "$and": [
                                            {"$in": ["$$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                            {
                                                "$and": [
                                                    {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$$log.date"}}}, 2]},
                                                    {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$$log.date"}}}, 6]}
                                                ]
                                            }
                                        ]
                                    },
                                    {"$cond": [{"$eq": [{"$ifNull": ["$$log.half_day", False]}, True]}, 0.5, 1]},
                                    0
                                ]
                            }
                        }
                    }
                },
                "total_work_hours": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {"$ifNull": ["$$log.work_hours", 0]}
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "log",
                            "in": {"$ifNull": ["$$log.late_minutes", 0]}
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "total_work_hours": {"$sum": "$total_work_hours"},
                "total_late_minutes": {"$sum": "$total_late_minutes"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "total_work_hours": {"$round": ["$total_work_hours", 2]},
                "total_late_minutes": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$eq": ["$headcount", 0]},
                        0,
                        {"$round": [{"$divide": ["$total_work_hours", "$headcount"]}, 2]}
                    ]
                }
            }
        },
        {"$sort": {"department": 1}}
    ]
    
    results = list(db.employees.aggregate(pipeline))
    
    # Apply half-up rounding properly
    for r in results:
        r["total_work_hours"] = half_up_round(r.get("total_work_hours", 0), 2)
        r["avg_work_hours"] = half_up_round(r.get("avg_work_hours", 0), 2)
    
    return {"month": month, "departments": results}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=100),
):
    year, mon = map(int, month.split("-"))
    first_day = f"{year}-{mon:02d}-01"
    if mon == 12:
        last_day = f"{year}-12-31"
    else:
        next_month = date(year, mon + 1, 1)
        last_day = (next_month - timedelta(days=1)).isoformat()
    
    pipeline = [
        {
            "$match": {
                "date": {"$gte": first_day, "$lte": last_day},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {"$match": {"emp": {"$ne": []}}},
        {"$unwind": "$emp"},
    ]
    
    if department:
        pipeline.append({"$match": {"emp.department": department}})
    
    pipeline.extend([
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        },
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])
    
    results = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "leaderboard": results}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
):
    # Validate department exists
    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="department not found")
    
    pipeline = [
        {
            "$match": {
                "date": {"$gte": date_from, "$lte": date_to}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {"$match": {"emp.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$and": [
                                            {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                                            {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}
                                        ]
                                    }
                                ]
                            },
                            {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                            0
                        ]
                    }
                },
                "total_work_hours": {"$sum": {"$ifNull": ["$work_hours", 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "present_count": 1,
                "total_work_hours": {"$round": ["$total_work_hours", 2]},
                "total_late_minutes": 1
            }
        },
        {"$sort": {"date": 1}}
    ]
    
    results = list(db.attendance_logs.aggregate(pipeline))
    
    for r in results:
        r["total_work_hours"] = half_up_round(r.get("total_work_hours", 0), 2)
    
    return {
        "department": department,
        "from": date_from,
        "to": date_to,
        "trend": results
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = None,
    emp_code: Optional[str] = None,
    department: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    limit: int = 10,
    page: int = 1,
    page_size: int = 20,
):
    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        
        cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip((page-1)*page_size).limit(page_size)
        explain_result = cursor.explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain_result}
    
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(status_code=422, detail="emp_code and month are required")
        year, mon = map(int, month.split("-"))
        first_day = f"{year}-{mon:02d}-01"
        if mon == 12:
            last_day = f"{year}-12-31"
        else:
            last_day = (date(year, mon + 1, 1) - timedelta(days=1)).isoformat()
        
        pipe = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": first_day, "$lte": last_day}}},
            {"$group": {"_id": None, "total": {"$sum": 1}}}
        ]
        result = db.command({
            "explain": {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            "verbosity": "executionStats"
        })
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": result}
    
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")
        year, mon = map(int, month.split("-"))
        first_day = f"{year}-{mon:02d}-01"
        last_day = (date(year, mon + 1, 1) - timedelta(days=1)).isoformat() if mon < 12 else f"{year}-12-31"
        
        pipe = [
            {"$match": {"joined_on": {"$lte": last_day}}},
            {"$group": {"_id": "$department", "count": {"$sum": 1}}}
        ]
        result = db.command({
            "explain": {"aggregate": "employees", "pipeline": pipe, "cursor": {}},
            "verbosity": "executionStats"
        })
        return {"endpoint": endpoint, "collection": "employees", "explain": result}
    
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")
        year, mon = map(int, month.split("-"))
        first_day = f"{year}-{mon:02d}-01"
        last_day = (date(year, mon + 1, 1) - timedelta(days=1)).isoformat() if mon < 12 else f"{year}-12-31"
        
        pipe = [
            {"$match": {"date": {"$gte": first_day, "$lte": last_day}, "late_minutes": {"$gt": 0}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
            {"$sort": {"total_late_minutes": -1}},
            {"$limit": limit}
        ]
        result = db.command({
            "explain": {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            "verbosity": "executionStats"
        })
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": result}
    
    elif endpoint == "department_trend":
        if not date_from or not date_to or not department:
            raise HTTPException(status_code=422, detail="from, to, and department are required")
        
        pipe = [
            {"$match": {"date": {"$gte": date_from, "$lte": date_to}}},
            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
            {"$match": {"emp.department": department}},
            {"$group": {"_id": "$date", "count": {"$sum": 1}}}
        ]
        result = db.command({
            "explain": {"aggregate": "attendance_logs", "pipeline": pipe, "cursor": {}},
            "verbosity": "executionStats"
        })
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": result}
    
    else:
        raise HTTPException(status_code=404, detail="unknown endpoint")

# --------------------------------------------------------------------------- #
