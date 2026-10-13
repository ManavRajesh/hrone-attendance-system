# Employee Attendance & Analytics API

## Setup and Running

1. **Environment Setup**: Ensure you have Python 3.9+ and MongoDB 6.0+ running (e.g., via Docker: `docker run -d -p 27017:27017 --name hrmongo mongo:7`).
2. **Environment Variables**: Create a `.env` file with `MONGO_URI` (e.g., `mongodb://localhost:27017`) and `MONGO_DB` (e.g., `attendance_db`).
3. **Install Dependencies**: `pip install -r requirements.txt`.
4. **Seed Sample Data**: Run `python sample_seed.py` to populate the DB with sample employees and attendance logs.
5. **Start API**: Run `uvicorn app.main:app --port 8000` or `uvicorn app.main:app --port 8000 --reload` for development.
6. **Run Test Suite**: Run `PYTHONPATH=. pytest tests/ -v` to run all unit and regression tests.

## Features Implemented
- **Core Attendance System**: Complete implementation of `create_employee`, `list_employees`, `punch_in`, `punch_out`, `list_attendance`, and `regularize_attendance`.
- **Business Rules R1–R10 Enforced**: Timezone bounds (IST), dynamic/custom grace periods, half-up rounding, overnight shift wrapping across midnight, and strict concurrency protection.
- **Audit Trails**: Manual regularization tracking via `history` array capturing timestamps, modifier identity, reason, and before/after changes.
- **MongoDB 7.0 Aggregation Analytics**:
  - Employee Monthly Summary with join-date aware working day counts.
  - Department Summary pipeline grouping headcounts, presence ratios, and average work hours.
  - Competition-ranked Late Leaderboard using `$setWindowFields` with tie handling.
  - Department Trend analysis with zero-filled date sequences via `$densify` and 7-day moving averages.
- **Admin Query Profiler**: `/admin/explain/{endpoint}` providing execution plans and index statistics directly from MongoDB.
- **Multi-Shift Scheduling & Configurable Grace Periods**:
  - Dynamic shift catalog (`GENERAL`, `MORNING`, `EVENING`, `NIGHT`) in MongoDB `shifts` collection.
  - Per-shift configurable grace periods (0–120 mins).
  - Shift creation, updates, and employee shift reassignment endpoints (`POST /shifts`, `GET /shifts`, `PATCH /shifts/{code}`, `POST /employees/{code}/shift`).
  - Full documentation in [`FEATURE_SHIFTS.md`](./FEATURE_SHIFTS.md).

## Assumptions & Design Highlights
- Python `Decimal` is utilized with `ROUND_HALF_UP` to strictly avoid banker's rounding discrepancies for fractional work hours.
- MongoDB compound indexing guarantees $O(1)$ lookups and prevents duplicate punches per calendar date.
- Backward compatibility is maintained for clients unaware of multi-shift extensions.
