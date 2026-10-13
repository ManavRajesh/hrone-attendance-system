import os
import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30), "IST")

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def startup_event():
    # Enforce unique emp_code
    db.employees.create_index("emp_code", unique=True)
    # Support department searches
    db.employees.create_index("department")
    # Enforce one record per (emp_code, date)
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    # Support fast list_attendance sorting
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    # Enforce unique shift_code
    db.shifts.create_index("shift_code", unique=True)

    # Seed default enterprise shifts if shifts collection is empty
    if db.shifts.count_documents({}) == 0:
        now_ts = now_truncated()
        default_shifts = [
            {
                "shift_code": "GENERAL",
                "name": "General Day Shift",
                "start_time": "09:30",
                "end_time": "18:30",
                "grace_period_minutes": 10,
                "half_day_threshold_hours": 4.5,
                "overtime_threshold_minutes": 30,
                "description": "Standard 9:30 AM to 6:30 PM corporate shift with 10-minute grace period.",
                "created_at": now_ts,
                "updated_at": now_ts,
            },
            {
                "shift_code": "MORNING",
                "name": "Early Morning Shift",
                "start_time": "06:00",
                "end_time": "15:00",
                "grace_period_minutes": 15,
                "half_day_threshold_hours": 4.5,
                "overtime_threshold_minutes": 30,
                "description": "Early morning operational shift with 15-minute grace period.",
                "created_at": now_ts,
                "updated_at": now_ts,
            },
            {
                "shift_code": "EVENING",
                "name": "Evening Shift",
                "start_time": "14:00",
                "end_time": "23:00",
                "grace_period_minutes": 10,
                "half_day_threshold_hours": 4.5,
                "overtime_threshold_minutes": 30,
                "description": "Mid-day to night shift with 10-minute grace period.",
                "created_at": now_ts,
                "updated_at": now_ts,
            },
            {
                "shift_code": "NIGHT",
                "name": "Night Shift (Overnight)",
                "start_time": "22:00",
                "end_time": "06:00",
                "grace_period_minutes": 15,
                "half_day_threshold_hours": 4.5,
                "overtime_threshold_minutes": 30,
                "description": "Graveyard overnight shift spanning across midnight with 15-minute grace period.",
                "created_at": now_ts,
                "updated_at": now_ts,
            },
        ]
        db.shifts.insert_many(default_shifts)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def parse_epoch_ms(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc)

def to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def now_truncated() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(microsecond=0)

def get_employee_shift_config(emp: dict) -> dict:
    shift_code = emp.get("shift_code")
    if shift_code:
        shift_doc = db.shifts.find_one({"shift_code": shift_code.upper()})
        if shift_doc:
            return {
                "shift_code": shift_doc["shift_code"],
                "name": shift_doc.get("name", shift_code),
                "shift_start": shift_doc.get("start_time", "09:30"),
                "shift_end": shift_doc.get("end_time", "18:30"),
                "grace_period_minutes": shift_doc.get("grace_period_minutes", 10),
                "half_day_threshold_hours": shift_doc.get("half_day_threshold_hours", 4.5),
                "overtime_threshold_minutes": shift_doc.get("overtime_threshold_minutes", 30),
            }
    # Graceful fallback to employee embedded timings & standard 10m grace period
    return {
        "shift_code": "GENERAL",
        "name": "General Day Shift",
        "shift_start": emp.get("shift_start", "09:30"),
        "shift_end": emp.get("shift_end", "18:30"),
        "grace_period_minutes": 10,
        "half_day_threshold_hours": 4.5,
        "overtime_threshold_minutes": 30,
    }

def get_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = punch_in_utc.astimezone(IST)
    if shift_end <= shift_start:
        if punch_in_ist.strftime("%H:%M") < shift_end:
            return (punch_in_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    return punch_in_ist.strftime("%Y-%m-%d")

def get_shift_start_dt(attendance_date: str, shift_start: str) -> datetime:
    return datetime.fromisoformat(f"{attendance_date}T{shift_start}:00+05:30")

def get_shift_end_dt(attendance_date: str, shift_start: str, shift_end: str) -> datetime:
    end_dt = datetime.fromisoformat(f"{attendance_date}T{shift_end}:00+05:30")
    if shift_end <= shift_start:
        end_dt += timedelta(days=1)
    return end_dt

def compute_late_minutes(punch_in_utc: datetime, attendance_date: str, shift_start: str, grace_period_minutes: int = 10) -> int:
    start_dt = get_shift_start_dt(attendance_date, shift_start)
    diff_sec = (punch_in_utc - start_dt).total_seconds()
    grace_sec = grace_period_minutes * 60
    if diff_sec > grace_sec:
        return int(diff_sec // 60)
    return 0

def compute_overtime(punch_out_utc: datetime, attendance_date: str, shift_start: str, shift_end: str, overtime_threshold_minutes: int = 30) -> int:
    end_dt = get_shift_end_dt(attendance_date, shift_start, shift_end)
    diff_sec = (punch_out_utc - end_dt).total_seconds()
    threshold_sec = overtime_threshold_minutes * 60
    if diff_sec >= threshold_sec:
        return int(diff_sec // 60)
    return 0

def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    diff_sec = max(0, (punch_out_utc - punch_in_utc).total_seconds())
    val = Decimal(diff_sec) / Decimal(3600)
    return float(val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def compute_half_day(work_hours: float, threshold_hours: float = 4.50) -> bool:
    return work_hours < threshold_hours

def format_attendance(doc: dict) -> dict:
    if not doc: return doc
    d = dict(doc)
    d.pop("_id", None)
    if d.get("punch_in"): d["punch_in"] = to_epoch_ms(d["punch_in"])
    if d.get("punch_out"): d["punch_out"] = to_epoch_ms(d["punch_out"])
    if d.get("history"):
        for h in d["history"]:
            if h.get("at"): h["at"] = to_epoch_ms(h["at"])
            changes = h.get("changes", {})
            for k in ["punch_in", "punch_out"]:
                if k in changes:
                    if changes[k].get("from"): changes[k]["from"] = to_epoch_ms(changes[k]["from"])
                    if changes[k].get("to"): changes[k]["to"] = to_epoch_ms(changes[k]["to"])
    return d

def format_shift(doc: dict) -> dict:
    if not doc: return doc
    d = dict(doc)
    d.pop("_id", None)
    if d.get("created_at") and isinstance(d["created_at"], datetime):
        d["created_at"] = to_epoch_ms(d["created_at"])
    if d.get("updated_at") and isinstance(d["updated_at"], datetime):
        d["updated_at"] = to_epoch_ms(d["updated_at"])
    return d

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class ShiftIn(BaseModel):
    shift_code: str = Field(..., pattern=r'^[A-Za-z0-9_-]{2,20}$')
    name: str = Field(..., min_length=2, max_length=100)
    start_time: str = Field(..., pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end_time: str = Field(..., pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    grace_period_minutes: int = Field(10, ge=0, le=120)
    half_day_threshold_hours: float = Field(4.5, ge=1.0, le=12.0)
    overtime_threshold_minutes: int = Field(30, ge=0, le=180)
    description: Optional[str] = Field(None, max_length=250)

class ShiftUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    start_time: Optional[str] = Field(None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end_time: Optional[str] = Field(None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    grace_period_minutes: Optional[int] = Field(None, ge=0, le=120)
    half_day_threshold_hours: Optional[float] = Field(None, ge=1.0, le=12.0)
    overtime_threshold_minutes: Optional[int] = Field(None, ge=0, le=180)
    description: Optional[str] = Field(None, max_length=250)

class AssignShiftIn(BaseModel):
    shift_code: str = Field(..., pattern=r'^[A-Za-z0-9_-]{2,20}$')

class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r'^EMP\d{4,6}$')
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_code: Optional[str] = Field(None, pattern=r'^[A-Za-z0-9_-]{2,20}$')
    shift_start: Optional[str] = Field(None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: Optional[str] = Field(None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

class RegularizeRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None

# --------------------------------------------------------------------------- #
# Shift Management Endpoints
# --------------------------------------------------------------------------- #
@app.post("/shifts", status_code=201)
def create_shift(body: ShiftIn):
    shift_code = body.shift_code.upper()
    doc = body.model_dump()
    doc["shift_code"] = shift_code
    now_ts = now_truncated()
    doc["created_at"] = now_ts
    doc["updated_at"] = now_ts
    try:
        db.shifts.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, f"Shift with code '{shift_code}' already exists")
    return format_shift(doc)

@app.get("/shifts")
def list_shifts():
    shifts = list(db.shifts.find({}, {"_id": 0}).sort("shift_code", ASCENDING))
    return {"items": [format_shift(s) for s in shifts], "total": len(shifts)}

@app.get("/shifts/{shift_code}")
def get_shift(shift_code: str = Path(...)):
    shift = db.shifts.find_one({"shift_code": shift_code.upper()})
    if not shift:
        raise HTTPException(404, f"Shift '{shift_code}' not found")
    return format_shift(shift)

@app.patch("/shifts/{shift_code}")
def update_shift(shift_code: str = Path(...), body: ShiftUpdateIn = ...):
    shift = db.shifts.find_one({"shift_code": shift_code.upper()})
    if not shift:
        raise HTTPException(404, f"Shift '{shift_code}' not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(422, "No update fields provided")
    updates["updated_at"] = now_truncated()
    updated_shift = db.shifts.find_one_and_update(
        {"shift_code": shift_code.upper()},
        {"$set": updates},
        return_document=True
    )
    return format_shift(updated_shift)

@app.post("/employees/{emp_code}/shift")
def assign_employee_shift(emp_code: str = Path(...), body: AssignShiftIn = ...):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    shift_code = body.shift_code.upper()
    shift = db.shifts.find_one({"shift_code": shift_code})
    if not shift:
        raise HTTPException(404, f"Shift '{shift_code}' not found")
    res = db.employees.find_one_and_update(
        {"emp_code": emp_code},
        {"$set": {
            "shift_code": shift["shift_code"],
            "shift_start": shift["start_time"],
            "shift_end": shift["end_time"],
        }},
        return_document=True
    )
    res.pop("_id", None)
    if res.get("created_at"):
        res["created_at"] = to_epoch_ms(res["created_at"])
    return res

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unready")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    if doc.get("shift_code"):
        shift = db.shifts.find_one({"shift_code": doc["shift_code"].upper()})
        if not shift:
            raise HTTPException(404, f"Shift '{doc['shift_code']}' not found")
        doc["shift_code"] = shift["shift_code"]
        if not doc.get("shift_start"):
            doc["shift_start"] = shift["start_time"]
        if not doc.get("shift_end"):
            doc["shift_end"] = shift["end_time"]
    else:
        doc["shift_code"] = "GENERAL"
        if not doc.get("shift_start"):
            doc["shift_start"] = "09:30"
        if not doc.get("shift_end"):
            doc["shift_end"] = "18:30"

    doc["created_at"] = now_truncated()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(doc["created_at"])
    return doc

@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip(skip).limit(page_size))
    for d in items:
        if d.get("created_at"): d["created_at"] = to_epoch_ms(d["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(422, "Invalid status for punch-in")
        
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    shift_cfg = get_employee_shift_config(emp)
    ts = parse_epoch_ms(body.punched_at) if body.punched_at else now_truncated()
    d = get_attendance_date(ts, shift_cfg["shift_start"], shift_cfg["shift_end"])
    late_minutes = compute_late_minutes(ts, d, shift_cfg["shift_start"], shift_cfg["grace_period_minutes"])
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "shift_code": shift_cfg["shift_code"],
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Already punched in for this date")
    return format_attendance(doc)

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    shift_cfg = get_employee_shift_config(emp)
    ts = parse_epoch_ms(body.punched_at) if body.punched_at else now_truncated()
    
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", DESCENDING)]
    )
    if not record:
        raise HTTPException(404, "No punch-in found")
        
    if record.get("punch_out"):
        raise HTTPException(409, "Already punched out")
        
    if ts <= record["punch_in"] or (ts - record["punch_in"]).total_seconds() > 24 * 3600:
        raise HTTPException(422, "Invalid punch_out time")
        
    work_hours = compute_work_hours(record["punch_in"], ts)
    overtime_minutes = compute_overtime(
        ts,
        record["date"],
        shift_cfg["shift_start"],
        shift_cfg["shift_end"],
        shift_cfg["overtime_threshold_minutes"]
    )
    half_day = compute_half_day(work_hours, shift_cfg["half_day_threshold_hours"])
    
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day
        }},
        return_document=True
    )
    if not res:
        raise HTTPException(409, "Already punched out")
    return format_attendance(res)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from > date_to")
        
    q = {}
    if emp_code: q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from: q["date"]["$gte"] = date_from
        if date_to: q["date"]["$lte"] = date_to
    if status: q["status"] = status
    
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    docs = list(db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size))
    return {"items": [format_attendance(d) for d in docs], "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "Employee not found")
        
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record: raise HTTPException(404, "Record not found")
        
    shift_cfg = get_employee_shift_config(emp)
    new_status = body.status if body.status is not None else record["status"]
    
    if body.punch_in is not None: new_punch_in = parse_epoch_ms(body.punch_in)
    elif "punch_in" in body.model_fields_set: new_punch_in = None
    else: new_punch_in = record["punch_in"]
        
    if body.punch_out is not None: new_punch_out = parse_epoch_ms(body.punch_out)
    elif "punch_out" in body.model_fields_set: new_punch_out = None
    else: new_punch_out = record["punch_out"]
        
    if new_status in ["ABSENT", "LEAVE"]:
        if ("punch_in" in body.model_fields_set and body.punch_in is not None) or \
           ("punch_out" in body.model_fields_set and body.punch_out is not None):
            raise HTTPException(422, "Cannot supply punch times for ABSENT/LEAVE")
        new_punch_in = None
        new_punch_out = None
    else:
        if new_punch_in is None:
            raise HTTPException(422, "Presence status requires punch_in")
        computed_date = get_attendance_date(new_punch_in, shift_cfg["shift_start"], shift_cfg["shift_end"])
        if computed_date != date:
            raise HTTPException(422, f"punch_in date {computed_date} does not match {date}")
        if new_punch_out:
            if new_punch_out <= new_punch_in or (new_punch_out - new_punch_in).total_seconds() > 24 * 3600:
                raise HTTPException(422, "Invalid punch_out time")
                
    if new_status in ["ABSENT", "LEAVE"]:
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        new_late_minutes = compute_late_minutes(new_punch_in, date, shift_cfg["shift_start"], shift_cfg["grace_period_minutes"])
        if new_punch_out:
            new_work_hours = compute_work_hours(new_punch_in, new_punch_out)
            new_overtime_minutes = compute_overtime(
                new_punch_out,
                date,
                shift_cfg["shift_start"],
                shift_cfg["shift_end"],
                shift_cfg["overtime_threshold_minutes"]
            )
            new_half_day = compute_half_day(new_work_hours, shift_cfg["half_day_threshold_hours"])
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False
            
    changes = {}
    def check_change(field, old_val, new_val):
        if old_val != new_val:
            changes[field] = {"from": old_val, "to": new_val}
            
    check_change("status", record["status"], new_status)
    check_change("punch_in", record["punch_in"], new_punch_in)
    check_change("punch_out", record["punch_out"], new_punch_out)
    check_change("work_hours", record["work_hours"], new_work_hours)
    check_change("late_minutes", record.get("late_minutes", 0), new_late_minutes)
    check_change("overtime_minutes", record.get("overtime_minutes", 0), new_overtime_minutes)
    check_change("half_day", record.get("half_day", False), new_half_day)
    
    if not changes:
        raise HTTPException(422, "Request changes nothing")
        
    history_entry = {
        "at": now_truncated(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }
    
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "history": {"$size": len(record.get("history", []))}},
        {"$set": {
            "status": new_status, "punch_in": new_punch_in, "punch_out": new_punch_out,
            "work_hours": new_work_hours, "late_minutes": new_late_minutes,
            "overtime_minutes": new_overtime_minutes, "half_day": new_half_day
        }, "$push": {"history": history_entry}},
        return_document=True
    )
    if not res: raise HTTPException(409, "Concurrent modification")
    return format_attendance(res)

# --------------------------------------------------------------------------- #
# Pipelines Builders
# --------------------------------------------------------------------------- #
def get_employee_monthly_pipeline(emp_code: str, month: str):
    date_start = f"{month}-01"
    y, m = map(int, month.split("-"))
    last_day_str = f"{month}-{calendar.monthrange(y, m)[1]}"
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": date_start, "$lte": last_day_str}}},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [{"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [1, 2, 3, 4, 5]]}
                        ]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                        0
                    ]
                }
            },
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
        }}
    ]

def get_department_summary_pipeline(month: str, department: Optional[str]):
    date_start = f"{month}-01"
    y, m = map(int, month.split("-"))
    last_day_str = f"{month}-{calendar.monthrange(y, m)[1]}"
    emp_match = {"joined_on": {"$lte": last_day_str}}
    if department: emp_match["department"] = department
    return [
        {"$match": emp_match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp"]},
                    "date": {"$gte": date_start, "$lte": last_day_str}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "unique_emps": {"$addToSet": "$emp_code"},
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$ne": ["$logs.date", None]},
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [{"$isoDayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, [1, 2, 3, 4, 5]]}
                        ]},
                        {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]},
                        0
                    ]
                }
            },
            "work_hours_sum": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$ne": ["$logs.work_hours", None]}
                        ]},
                        "$logs.work_hours",
                        0
                    ]
                }
            },
            "work_hours_count": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$ne": ["$logs.work_hours", None]}
                        ]},
                        1, 0
                    ]
                }
            },
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}
        }},
        {"$project": {
            "department": "$_id",
            "_id": 0,
            "headcount": {"$size": "$unique_emps"},
            "present_days": 1,
            "work_hours_sum": 1,
            "work_hours_count": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1
        }},
        {"$sort": {"department": 1}}
    ]

def get_late_leaderboard_pipeline(month: str, limit: int, department: Optional[str]):
    date_start = f"{month}-01"
    y, m = map(int, month.split("-"))
    last_day_str = f"{month}-{calendar.monthrange(y, m)[1]}"
    log_match = {"date": {"$gte": date_start, "$lte": last_day_str}, "late_minutes": {"$gt": 0}}
    p = [
        {"$match": log_match},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"}
    ]
    if department: p.append({"$match": {"emp.department": department}})
    p.extend([
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}}
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {
            "emp_code": "$_id", "_id": 0, "name": "$emp.name",
            "department": "$emp.department", "total_late_minutes": 1,
            "late_count": 1, "rank": 1
        }},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])
    return p

def get_department_trend_pipeline(department: str, from_: str, to: str):
    d_from = datetime.fromisoformat(from_)
    d_to = datetime.fromisoformat(to)
    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {"$project": {"_id": 0, "dummy": 1}},
        {"$densify": {
            "field": "date",
            "range": {"step": 1, "unit": "day", "bounds": [d_from, d_to + timedelta(days=1)]}
        }},
        {"$project": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
            "is_working_day": {"$in": [{"$isoDayOfWeek": "$date"}, [1, 2, 3, 4, 5]]}
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"current_date": "$date"},
            "pipeline": [
                {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$current_date"]}}},
                {"$count": "headcount"}
            ],
            "as": "emp_data"
        }},
        {"$addFields": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$emp_data.headcount", 0]}, 0]}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"current_date": "$date"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$date", "$$current_date"]}}},
                {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
                {"$match": {"emp.department": department}}
            ],
            "as": "logs"
        }},
        {"$addFields": {
            "present_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "log",
                        "in": {
                            "$cond": [
                                {"$in": ["$$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": [{"$eq": ["$$log.half_day", True]}, 0.5, 1]},
                                0
                            ]
                        }
                    }
                }
            },
            "late_count": {"$size": {"$filter": {"input": "$logs", "as": "log", "cond": {"$gt": ["$$log.late_minutes", 0]}}}}
        }},
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": [{"$eq": ["$is_working_day", True]}, {"$gt": ["$headcount", 0]}]},
                    {"$divide": [
                        {"$trunc": {"$add": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 10000]}, 0.5]}},
                        10000
                    ]}, None
                ]
            }
        }},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d_raw": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}
        }},
        {"$addFields": {
            "moving_avg_7d": {
                "$cond": [
                    {"$ne": ["$moving_avg_7d_raw", None]},
                    {"$divide": [
                        {"$trunc": {"$add": [{"$multiply": ["$moving_avg_7d_raw", 10000]}, 0.5]}},
                        10000
                    ]}, None
                ]
            }
        }},
        {"$project": {
            "_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
            "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1
        }}
    ]

# --------------------------------------------------------------------------- #
# Analytics endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "Employee not found")
        
    y, m = map(int, month.split("-"))
    num_days = calendar.monthrange(y, m)[1]
    joined_on = datetime.strptime(emp["joined_on"], "%Y-%m-%d").date()
    working_days = sum(1 for day in range(1, num_days + 1) 
                       if datetime(y, m, day).date() >= joined_on and datetime(y, m, day).date().weekday() < 5)
                       
    res = list(db.attendance_logs.aggregate(get_employee_monthly_pipeline(emp_code, month)))
    agg = res[0] if res else {"present_days": 0, "leave_days": 0, "late_count": 0, "total_late_minutes": 0, "total_overtime_minutes": 0}
    
    if working_days > 0:
        attendance_pct = float(Decimal(agg["present_days"] / working_days * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    else:
        attendance_pct = None
        
    return {
        "emp_code": emp_code, "month": month, "working_days": working_days,
        "present_days": agg.get("present_days", 0), "leave_days": agg.get("leave_days", 0),
        "late_count": agg.get("late_count", 0), "total_late_minutes": agg.get("total_late_minutes", 0),
        "total_overtime_minutes": agg.get("total_overtime_minutes", 0), "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    res = list(db.employees.aggregate(get_department_summary_pipeline(month, department)))
    for d in res:
        if d["work_hours_count"] > 0:
            avg = d["work_hours_sum"] / d["work_hours_count"]
            d["avg_work_hours"] = float(Decimal(avg).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        else:
            d["avg_work_hours"] = None
        d.pop("work_hours_sum")
        d.pop("work_hours_count")
    return {"month": month, "items": res}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = 10, department: Optional[str] = None):
    res = list(db.attendance_logs.aggregate(get_late_leaderboard_pipeline(month, limit, department)))
    return {"month": month, "items": res}

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_: str = Query(..., alias="from"), to: str = Query(..., alias="to")):
    if from_ > to or (datetime.fromisoformat(to) - datetime.fromisoformat(from_)).days > 92:
        raise HTTPException(422, "Invalid date range")
    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "Department not found")
        
    res = list(db.employees.aggregate(get_department_trend_pipeline(department, from_, to)))
    return {"department": department, "items": res}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None, alias="to"),
    page: int = 1,
    page_size: int = 20
):
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = date_from
            if date_to: q["date"]["$lte"] = date_to
        if status: q["status"] = status
        explain_plan = db.attendance_logs.find(q).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain_plan}
        
    if endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "Missing parameters")
        pipeline = get_employee_monthly_pipeline(emp_code, month)
        explain_plan = db.command('explain', {'aggregate': 'attendance_logs', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain_plan}
        
    if endpoint == "department_summary":
        if not month: raise HTTPException(422, "Missing month")
        pipeline = get_department_summary_pipeline(month, department)
        explain_plan = db.command('explain', {'aggregate': 'employees', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {"endpoint": endpoint, "collection": "employees", "explain": explain_plan}
        
    if endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "Missing month")
        pipeline = get_late_leaderboard_pipeline(month, limit, department)
        explain_plan = db.command('explain', {'aggregate': 'attendance_logs', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain_plan}
        
    if endpoint == "department_trend":
        if not department or not from_ or not to: raise HTTPException(422, "Missing parameters")
        pipeline = get_department_trend_pipeline(department, from_, to)
        explain_plan = db.command('explain', {'aggregate': 'employees', 'pipeline': pipeline, 'cursor': {}}, verbosity='executionStats')
        return {"endpoint": endpoint, "collection": "employees", "explain": explain_plan}
        
    raise HTTPException(422, "Unknown endpoint")
