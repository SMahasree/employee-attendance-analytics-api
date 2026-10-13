# Attendance Analytics API

This project implements the FastAPI attendance and analytics service described by the HR assignment contract in `openapi.yaml` and the MongoDB schema in `DATA_MODEL.md`.

## What is implemented
- Employee creation and listing
- Punch-in / punch-out attendance workflow
- Manual correction via `PATCH /attendance/{emp_code}/{date}`
- Monthly employee analytics
- Department summary, late leaderboard, and department trend analytics
- Health check and debug/explain endpoints for investigation
- Startup index creation for the required MongoDB uniqueness / lookup patterns

## Run locally

### With MongoDB

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start MongoDB, then seed and start the API:

```powershell
docker run --detach --name attendance-mongo -p 27017:27017 mongo:7
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

Set `MONGO_URI` and `MONGO_DB` in the environment as needed. `sample_seed.py` replaces the contents of both
collections in the configured database, so use it only with a local development database.

### Without a MongoDB server

Mock mode is an in-memory database for trying the API; it starts empty and is reset when the process stops:

```powershell
$env:USE_MOCK_MONGO = "1"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000 --reload
```

`mongomock` does not implement every aggregation operator used by the analytics endpoints. Use a real MongoDB instance
to validate the complete assignment contract, especially department trends and explain plans.

After startup, open `http://127.0.0.1:8000/docs` for the interactive API.

## Tests

Run the isolated unit and API-rule tests with the project environment:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The unit/API tests use a fresh in-memory MongoDB mock and do not touch the configured application database. Real MongoDB
aggregation behavior is covered by an opt-in integration test. It requires a local MongoDB instance, creates a uniquely
named `attendance_test_*` database, and drops only that test database when finished:

```powershell
$env:ATTENDANCE_TEST_MONGO_URI = "mongodb://localhost:27017"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_mongodb_analytics.py" -v
Remove-Item Env:\ATTENDANCE_TEST_MONGO_URI
```

The integration test rejects non-local MongoDB hosts. Do not point the test URI at a production database.

## Project files
- `app/main.py` — primary implementation
- `openapi.yaml` — API contract and business rules
- `DATA_MODEL.md` — MongoDB schema and derived-value rules
- `sample_seed.py` — loads the sample data
- `sample_data/` — example employee and attendance records
- `REVIEW.md` — defects found in the starter and fixes applied
- `DECISIONS.md` — design choices and edge-case decisions

## Notes
- Attendance dates and shift times follow IST semantics.
- Derived fields are stored and treated as authoritative in MongoDB.
- Instants are truncated to whole seconds at the API boundary and returned as epoch milliseconds.
- Trend date rows and rolling averages are produced by a MongoDB aggregation.
- Work hours use decimal half-up rounding, and late/overtime calculations follow the assignment contract.
- Test timestamps are epoch milliseconds for absolute instants; convert IST wall times with their `+05:30` offset before
  sending them. For example, `2026-10-13T22:15:00+05:30` is `1791909900000` milliseconds. Treating `22:15` as UTC
  instead represents `03:45` IST the following day and will correctly produce a different attendance date and lateness.
