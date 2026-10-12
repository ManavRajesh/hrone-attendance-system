# README.md

## Setup and Running

1. **Environment Setup**: Ensure you have Python 3.11+ and MongoDB 6.0+ running.
2. **Environment Variables**: Create a `.env` file with `MONGO_URI` (e.g., `mongodb://localhost:27017`) and `MONGO_DB` (e.g., `attendance_db`).
3. **Install Dependencies**: `pip install -r requirements.txt`.
4. **Seed Sample Data**: Run `python sample_seed.py` to populate the DB with sample employees and attendance logs.
5. **Start API**: Run `uvicorn app.main:app --port 8000` or `uvicorn app.main:app --port 8000 --reload` for development.

## What is done
- All core endpoints (`create_employee`, `list_employees`, `punch_in`, `punch_out`, `list_attendance`, `regularize_attendance`).
- Re-computed fields and rules R1-R10 fully enforced (grace periods, IST bounds handling, correct calculation of overtime).
- Manual regularization `PATCH` endpoint tracks audit `history`.
- All Analytics endpoints implemented using MongoDB aggregation pipelines (`$group`, `$lookup`, `$densify`, and `$setWindowFields`).
- Admin endpoint `explain_endpoint` builds identical query/pipelines for performance profiling.
- Handled edge cases (overnight shifts, proper pagination at the DB level, and timezone safety).

## What is missing
- No formal automated test suite (`pytest`) is included due to time constraints, but I manually validated endpoints.

## Assumptions
- For half-day `work_hours` rounding, Python `Decimal` is used to accurately perform `ROUND_HALF_UP`, avoiding the banker's rounding quirk in standard python/mongo floating points.
