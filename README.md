# HROne Attendance API

A FastAPI service for employee attendance, punch tracking, regularization, and analytics. It uses MongoDB for storage.

## Run locally

Requires Python 3.11+ and MongoDB. Set `MONGO_URI` and `MONGO_DB` as needed, then run:

```sh
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs are available at `/docs`. See [DECISIONS.md](DECISIONS.md) and [REVIEW.md](REVIEW.md) for design notes and review findings.
