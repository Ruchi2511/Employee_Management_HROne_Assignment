# Employee Attendance & Analytics API

My submission for the HROne assignment: a FastAPI + MongoDB service for attendance, corrections and analytics.
All the code is in `app/main.py`, and it follows `openapi.yaml` and `DATA_MODEL.md`.

## How to run

You need Python 3.11+ and MongoDB 6.0+ (I tested on MongoDB 7.0).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
uvicorn app.main:app --port 8000
```

The app reads `MONGO_URI` and `MONGO_DB` from the environment. A `.env` file also works, but real environment
variables win. `GET /health` returns 200 once MongoDB answers, and Swagger is at `http://localhost:8000/docs`.

All indexes are created at startup, so there are no manual steps. It is safe to start the app on a database that
already has data and indexes.

`python sample_seed.py` loads the nine sample documents, but **it deletes everything in `employees` and
`attendance_logs` first**. Only run it on a scratch database.

## What is done

Every endpoint in `openapi.yaml` is implemented, including the four analytics endpoints and
`/admin/explain/{endpoint}`. Nothing is missing. The starter's defects and my fixes are in `REVIEW.md`, and my
answers to the five questions are in `DECISIONS.md`.

## Things you should know

- Two identical punch-ins, two creates with the same `emp_code`, or two punch-outs: one wins and the rest get 409,
  because of the unique indexes. A PATCH only saves if the record hasn't changed since it was read, so no history
  entry is lost.
- All rounding is half-up. MongoDB's `$round` rounds half to even, so I round with decimals instead. Reported
  numbers use 2 decimals, including `attendance_pct`. The trend's `attendance_rate` and `moving_avg_7d` use 4.
- Working days and headcount start from `joined_on`, but logs dated before `joined_on` still count. The department
  summary and trend leave out people who join after the period.
- The trend accepts up to 92 days, counting both `from` and `to`. 93 days or more is a 422.
- `/admin/explain/attendance_list` explains the paged `find`, not the count used for `total`.
- A filter sent empty, like `department=`, looks for an empty value instead of being ignored.
