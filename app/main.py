"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Optional

from bson.decimal128 import Decimal128
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator, BaseModel, Field, StrictInt, model_validator
import pymongo
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
# MongoClient connects lazily, so importing this module never touches the network
client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]
employees = db["employees"]
attendance_logs = db["attendance_logs"]


def ensure_indexes(database: Database) -> None:
    # create_index is a no-op when an identical index already exists
    database.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uniq_emp_code")
    database.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)], name="department_emp_code"
    )
    database.employees.create_index([("joined_on", ASCENDING)], name="joined_on")

    # also what makes concurrent duplicate punch-ins fail with DuplicateKeyError
    database.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uniq_emp_code_date"
    )
    database.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)], name="date_desc_emp_code"
    )
    database.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="emp_code_punch_in_desc"
    )


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

# StrictInt rejects floats, numeric strings and bools, so FastAPI returns 422 for them
EpochMillis = Annotated[StrictInt, Field(ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes(db)
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def truncate_to_second(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)

def utc_now() -> datetime:
    return truncate_to_second(datetime.now(UTC))

def ms_to_datetime(ms: int) -> datetime:
    # integer division keeps it exact and drops the sub-second part (R1)
    return datetime.fromtimestamp(ms // 1000, tz=UTC)


def datetime_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)  # PyMongo treats naive datetimes as UTC
    return (dt - EPOCH) // timedelta(seconds=1) * 1000

def validation_error(field: Optional[str], msg: str, where: str = "body") -> RequestValidationError:
    loc = (where, field) if field else (where,)
    return RequestValidationError([{"type": "value_error", "loc": loc, "msg": msg, "input": None}])

def _ms_or_none(value):
    return datetime_to_ms(value) if isinstance(value, datetime) else value

# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def serialize_history_entry(entry: dict) -> dict:
    changes = {}
    for field, change in (entry.get("changes") or {}).items():
        if field in ("punch_in", "punch_out"):
            change = {"from": _ms_or_none(change.get("from")), "to": _ms_or_none(change.get("to"))}
        changes[field] = change
    return {
        "at": _ms_or_none(entry.get("at")),
        "by": entry.get("by"),
        "reason": entry.get("reason"),
        "changes": changes,
    }

def serialize_employee(doc: dict) -> dict:
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": _ms_or_none(doc.get("created_at")),
    }

def serialize_attendance(doc: dict) -> dict:
    # explicit field list so _id never leaks; legacy records may lack the last few fields
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": _ms_or_none(doc.get("punch_in")),
        "punch_out": _ms_or_none(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes") or 0,
        "overtime_minutes": doc.get("overtime_minutes") or 0,
        "half_day": bool(doc.get("half_day", False)),
        "history": [serialize_history_entry(e) for e in doc.get("history") or []],
    }

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    local = punch_in.astimezone(IST)
    # overnight shift: an early-morning punch belongs to the shift that started the day before
    if shift_end <= shift_start and local.time() < time.fromisoformat(shift_end):
        return (local.date() - timedelta(days=1)).isoformat()
    return local.date().isoformat()


def shift_start_at(date_str: str, shift_start: str) -> datetime:
    return datetime.combine(date.fromisoformat(date_str), time.fromisoformat(shift_start), tzinfo=IST)


def compute_late_minutes(punch_in: datetime, shift_start: str, date_str: str) -> int:
    seconds = int((truncate_to_second(punch_in) - shift_start_at(date_str, shift_start)).total_seconds())
    # grace is exactly 10:00; once past it, lateness counts from shift start
    return seconds // 60 if seconds > 600 else 0


def shift_end_at(date_str: str, shift_start: str, shift_end: str) -> datetime:
    end = datetime.combine(date.fromisoformat(date_str), time.fromisoformat(shift_end), tzinfo=IST)
    # an overnight shift ends on the next calendar day
    return end + timedelta(days=1) if shift_end <= shift_start else end


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((truncate_to_second(punch_out) - truncate_to_second(punch_in)).total_seconds())
    # Decimal so e.g. 9.125 rounds half-up to 9.13; round() would give 9.12
    hours = (Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(hours)


def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    seconds = int((truncate_to_second(punch_out) - shift_end_at(date_str, shift_start, shift_end)).total_seconds())
    minutes = seconds // 60
    return minutes if minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
def _check_calendar_date(value: str) -> str:
    date.fromisoformat(value)  # rejects impossible dates like 2026-02-30
    return value


# [0-9] rather than \d so non-ASCII digits are rejected
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
IsoDate = Annotated[str, Field(pattern=DATE_PATTERN), AfterValidator(_check_calendar_date)]
ShiftTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP[0-9]{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: ShiftTime = "09:30"
    shift_end: ShiftTime = "18:30"
    joined_on: IsoDate

    @model_validator(mode="after")
    def shift_start_differs_from_end(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]


class PunchInIn(BaseModel):
    emp_code: str
    # defaults aren't validated, so omitting it gives None but an explicit null is a 422
    punched_at: EpochMillis = None
    status: PresenceStatus = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: EpochMillis = None


class RegularizeIn(BaseModel):
    # omitted means unchanged; an explicit null is a 422. Derived fields sent by a client are ignored.
    status: Status = None
    punch_in: EpochMillis = None
    punch_out: EpochMillis = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    # short timeout so a dead database answers 503 quickly instead of after server selection
    try:
        with pymongo.timeout(2):
            client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = utc_now()
    # rely on the unique index instead of check-then-insert, which races
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department is not None:
        q["department"] = department
    total = db.employees.count_documents(q)
    docs = db.employees.find(
        q, {"_id": 0}, sort=[("emp_code", ASCENDING)], skip=(page - 1) * page_size, limit=page_size
    )
    items = [serialize_employee(d) for d in docs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code}, {"shift_start": 1, "shift_end": 1})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = ms_to_datetime(body.punched_at) if body.punched_at is not None else utc_now()
    d = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    # the unique (emp_code, date) index settles races, so no check-then-insert
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code}, {"shift_start": 1, "shift_end": 1})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = ms_to_datetime(body.punched_at) if body.punched_at is not None else utc_now()

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}}, sort=[("punch_in", DESCENDING)]
    )
    if record is None:
        raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")

    started = truncate_to_second(record["punch_in"])
    if ts <= started or ts - started > timedelta(hours=24):
        raise validation_error("punched_at", "must be after punch_in and within 24 hours of it")

    work_hours = compute_work_hours(started, ts)
    updates = {
        "punch_out": ts,
        "work_hours": work_hours,
        "overtime_minutes": compute_overtime(ts, emp["shift_start"], emp["shift_end"], record["date"]),
        "half_day": work_hours < 4.5,
    }
    # matching on punch_out: null makes this atomic, so only one concurrent punch-out wins;
    # matching punch_in too stops a concurrent correction from leaving stale work hours behind
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None, "punch_in": record["punch_in"]},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "already punched out")
    return serialize_attendance(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=DATE_PATTERN),
    date_to: Optional[str] = Query(None, pattern=DATE_PATTERN),
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise validation_error(name, "not a valid calendar date", "query")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise validation_error("date_from", "must be on or before date_to", "query")

    q = {}
    if emp_code is not None:
        q["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        q["date"] = {}
        if date_from is not None:
            q["date"]["$gte"] = date_from
        if date_to is not None:
            q["date"]["$lte"] = date_to
    if status is not None:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    docs = db.attendance_logs.find(
        q,
        {"_id": 0},
        sort=[("date", DESCENDING), ("emp_code", ASCENDING)],
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    items = [serialize_attendance(d) for d in docs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}

NON_PRESENCE = ("ABSENT", "LEAVE")
EDITABLE_FIELDS = ("status", "punch_in", "punch_out")
TRACKED_FIELDS = EDITABLE_FIELDS + ("work_hours", "late_minutes", "overtime_minutes", "half_day")

def plan_regularization(record: dict, emp: dict, body: RegularizeIn) -> tuple[dict, dict]:
    """Returns the record's new tracked fields and the history diff, or raises a 422."""
    current = {
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes") or 0,
        "overtime_minutes": record.get("overtime_minutes") or 0,
        "half_day": bool(record.get("half_day", False)),
    }
    status = body.status if body.status is not None else current["status"]

    if status in NON_PRESENCE:
        for field in ("punch_in", "punch_out"):
            if getattr(body, field) is not None:
                raise validation_error(field, f"must not be set when status is {status}")
        final = {"status": status, "punch_in": None, "punch_out": None,
                 "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    else:
        punch_in = ms_to_datetime(body.punch_in) if body.punch_in is not None else current["punch_in"]
        punch_out = ms_to_datetime(body.punch_out) if body.punch_out is not None else current["punch_out"]
        if punch_in is None:
            raise validation_error("punch_in", f"is required when status is {status}")
        if attendance_date(punch_in, emp["shift_start"], emp["shift_end"]) != record["date"]:
            raise validation_error("punch_in", "must fall on the record's attendance date")
        if punch_out is not None:
            started, ended = truncate_to_second(punch_in), truncate_to_second(punch_out)
            if ended <= started or ended - started > timedelta(hours=24):
                raise validation_error("punch_out", "must be after punch_in and within 24 hours of it")

        late = compute_late_minutes(punch_in, emp["shift_start"], record["date"])
        final = {"status": status, "punch_in": punch_in, "punch_out": punch_out,
                 "work_hours": None, "late_minutes": late, "overtime_minutes": 0, "half_day": False}
        if punch_out is not None:
            hours = compute_work_hours(punch_in, punch_out)
            final.update(
                work_hours=hours,
                overtime_minutes=compute_overtime(punch_out, emp["shift_start"], emp["shift_end"], record["date"]),
                half_day=hours < 4.5,
            )

    # only the editable fields decide whether anything changed; this is not a repair endpoint
    if all(final[f] == current[f] for f in EDITABLE_FIELDS):
        raise validation_error(None, "request changes nothing")
    changes = {f: {"from": current[f], "to": final[f]} for f in TRACKED_FIELDS if final[f] != current[f]}
    return final, changes


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: Annotated[str, Path(pattern=DATE_PATTERN)],
    body: RegularizeIn,
):
    try:
        _check_calendar_date(date)
    except ValueError:
        raise validation_error("date", "not a valid calendar date", "path")

    emp = db.employees.find_one({"emp_code": emp_code}, {"shift_start": 1, "shift_end": 1})
    if emp is None:
        raise HTTPException(404, "employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "attendance record not found")

    final, changes = plan_regularization(record, emp, body)
    entry = {"at": utc_now(), "by": body.regularized_by, "reason": body.reason, "changes": changes}

    # optimistic lock: only write if nobody changed the record since we read it.
    # Every correction adds a history entry, so the history length acts as a version.
    history = record.get("history")
    guard = {
        "_id": record["_id"],
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "history": {"$size": len(history)} if history is not None else None,
    }
    if history is None:
        update = {"$set": {**final, "history": [entry]}}
    else:
        update = {"$set": final, "$push": {"history": entry}}
    updated = db.attendance_logs.find_one_and_update(guard, update, return_document=ReturnDocument.AFTER)
    if updated is None:
        raise HTTPException(409, "record was changed by another request, retry")
    return serialize_attendance(updated)


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
PRESENCE_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]

# stored dates are "YYYY-MM-DD" strings, so the weekday is derived from the string (1 = Monday)
IS_WEEKDAY = {"$lte": [{"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}}, 5]}
# R5/R6: a presence record is one day, or half a day when half_day is set; missing half_day means false
PRESENT_VALUE = {
    "$cond": [
        {"$in": ["$status", PRESENCE_STATUSES]},
        {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
        0,
    ]
}
LATE_MINUTES = {"$ifNull": ["$late_minutes", 0]}


def month_bounds(month: str) -> tuple[date, date]:
    first = date.fromisoformat(f"{month}-01")
    next_first = (first + timedelta(days=32)).replace(day=1)
    return first, next_first - timedelta(days=1)


def count_working_days(start: date, end: date) -> int:
    # R7: Mon-Fri, no holiday calendar
    return sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)


def round_half_up(value, places: int) -> float:
    # $round in MongoDB rounds half to even, so R8 is applied here; str() keeps floats like 0.5 exact
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def employee_monthly_pipeline(emp_code: str, month: str) -> list[dict]:
    first, last = month_bounds(month)
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {
            "$group": {
                "_id": None,
                # R7: present days only count Mon-Fri; late and overtime include weekends
                "present_days": {"$sum": {"$cond": [IS_WEEKDAY, PRESENT_VALUE, 0]}},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [LATE_MINUTES, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": LATE_MINUTES},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            }
        },
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(pattern=MONTH_PATTERN)):
    emp = db.employees.find_one({"emp_code": emp_code}, {"joined_on": 1})
    if emp is None:
        raise HTTPException(404, "employee not found")

    first, last = month_bounds(month)
    start = max(first, date.fromisoformat(emp["joined_on"]))
    working_days = count_working_days(start, last) if start <= last else 0

    totals = next(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, month)), {})
    present_days = totals.get("present_days", 0)
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": round_half_up(present_days, 2),
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": round_half_up(Decimal(str(present_days)) * 100 / working_days, 2) if working_days else None,
    }


DEPARTMENT_TOTALS = ["present_days", "late_count", "total_late_minutes", "leave_count", "on_duty_count",
                     "work_hours_sum", "work_hours_count"]


def department_summary_pipeline(month: str, department: Optional[str] = None) -> list[dict]:
    first, last = month_bounds(month)
    # R9: start from employees so people with no logs still count; later joiners are left out
    match = {"joined_on": {"$lte": last.isoformat()}}
    if department is not None:
        match["department"] = department
    # R4 average is over present records that have work_hours; summed as decimals so float drift can't move R8 rounding
    has_hours = {"$and": [{"$in": ["$status", PRESENCE_STATUSES]}, {"$ne": [{"$ifNull": ["$work_hours", None]}, None]}]}
    return [
        {"$match": match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                # emp_code plus a date range, so each lookup reads the unique {emp_code, date} index
                "pipeline": [
                    {"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {"$sum": {"$cond": [IS_WEEKDAY, PRESENT_VALUE, 0]}},
                            "late_count": {"$sum": {"$cond": [{"$gt": [LATE_MINUTES, 0]}, 1, 0]}},
                            "total_late_minutes": {"$sum": LATE_MINUTES},
                            "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
                            "work_hours_sum": {"$sum": {"$cond": [has_hours, {"$toDecimal": "$work_hours"}, 0]}},
                            "work_hours_count": {"$sum": {"$cond": [has_hours, 1, 0]}},
                        }
                    },
                ],
                "as": "month",
            }
        },
        # "month" holds one row, or none for an employee with no logs, so the inner $sum gives 0 then
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                **{field: {"$sum": {"$sum": f"$month.{field}"}} for field in DEPARTMENT_TOTALS},
            }
        },
        {"$sort": {"_id": 1}},
    ]


def to_decimal(value) -> Decimal:
    return value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value))


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(pattern=MONTH_PATTERN), department: Optional[str] = None):
    items = []
    for row in db.employees.aggregate(department_summary_pipeline(month, department)):
        count = row["work_hours_count"]
        items.append({
            "department": row["_id"],
            "headcount": row["headcount"],
            "present_days": round_half_up(row["present_days"], 2),
            "avg_work_hours": round_half_up(to_decimal(row["work_hours_sum"]) / count, 2) if count else None,
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"],
        })
    return {"month": month, "items": items}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
