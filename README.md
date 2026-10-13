# Employee Attendance & Analytics API

A FastAPI + MongoDB backend for the HROne attendance assignment. Employees punch in and out, HR corrects records,
and analytics endpoints answer questions about lateness, departments and trends. All application code is in
`app/main.py` and follows `openapi.yaml` (business rules R1-R10) and `DATA_MODEL.md`.

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | 200 when MongoDB answers a ping, otherwise 503 |
| POST, GET | `/employees` | Create an employee; list by department with pagination |
| POST | `/attendance/punch-in`, `/attendance/punch-out` | Record the day's punches (race-safe) |
| GET | `/attendance` | List records with filters, sorting and pagination |
| PATCH | `/attendance/{emp_code}/{date}` | Correct a record and append to its history |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly summary for one employee |
| GET | `/analytics/departments/summary` | Monthly summary per department |
| GET | `/analytics/leaderboard/late` | Late-comers ranking with ties |
| GET | `/analytics/departments/{department}/trend` | Daily series with a 7-day moving average |
| GET | `/admin/explain/{endpoint}` | MongoDB `executionStats` explain for an endpoint's main query |

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer (the target environment is MongoDB 7.0)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Set `MONGO_URI` and `MONGO_DB` in `.env` or as environment variables. Real environment variables take priority over
`.env`.

## Run

```bash
uvicorn app.main:app --port 8000
```

- Health check: `curl http://localhost:8000/health` returns `{"status": "ok"}`
- Swagger UI: http://localhost:8000/docs (OpenAPI JSON at `/openapi.json`)

Indexes are created at startup and the step is safe to repeat, so the app can start against a database that
already holds data.

### Sample data (optional)

> **Warning:** `python sample_seed.py` **deletes every document in `employees` and `attendance_logs`** of the
> database named by `MONGO_DB` before loading the nine sample documents. Only run it against a scratch database,
> never against real data.

## Things to know

- **Indexes.** `employees`: unique `emp_code`, `{department, emp_code}`, `joined_on`. `attendance_logs`: unique
  `{emp_code, date}` (one record per person per day; it also settles concurrent punch-ins), `{date: -1, emp_code}`,
  `{emp_code, punch_in: -1}`.
- **Concurrency.** Duplicate creates and punch-ins are rejected by the unique indexes (409). Punch-out and PATCH only
  save if the record has not changed since it was read; the loser gets 409, so no history entry is lost.
- **Time.** The API uses epoch milliseconds; MongoDB stores UTC datetimes. Attendance dates and shift times are IST,
  instants are truncated to whole seconds, and overnight shifts belong to the day they started.
- **Rounding.** Work hours, `present_days`, `avg_work_hours` and `attendance_pct` use 2 decimals half-up; the trend's
  `attendance_rate` and `moving_avg_7d` use 4 decimals half-up. MongoDB's `$round` rounds half to even, so half-up is
  done with decimals instead.
- **`joined_on`.** Working days and headcount start from `joined_on`. Attendance logs dated before `joined_on` still
  count toward the totals. The department summary and trend leave out employees who join after the period,
  together with their logs; the leaderboard ranks by logs alone.
- **Trend range.** `from` and `to` are inclusive and may cover at most 92 days; 93 or more is a 422.
- **Explain.** `/admin/explain/attendance_list` explains the paged `find` (not the `count_documents` used for `total`).
  The analytics targets reuse the same pipeline functions as their endpoints.
- **Empty filters.** A query parameter sent with an empty value (for example `department=`) filters for an empty
  string rather than being ignored.
