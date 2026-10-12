# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Computes based on naive local time, failing for overnight shifts because the hour replacement doesn't handle the next day correctly. | Punch-in for an overnight shift reports incorrect late minutes. | Switched to full UTC-to-IST datetime conversion and properly bounded the shift start. |
| 2 | `compute_work_hours` | Uses Python's `round()`, which applies Banker's Rounding (half-to-even), violating R4 which requires half-up. | `1.235` hours gets rounded down to `1.23` instead of `1.24`. | Used the `decimal` module (`ROUND_HALF_UP`) for accurate mathematical rounding. |
| 3 | `compute_overtime` | Fails to increment the `shift_end` by 1 day for overnight shifts, and doesn't enforce the 30-minute minimum strictly (returns any minutes > 0). | Overtime during an overnight shift is wildly inaccurate or non-zero for 5 minutes of overtime. | Added logic to add a day to `shift_end` if `shift_end <= shift_start`, and added a `>= 30` check. |
| 4 | `create_employee` | `created_at` is generated as a naive `datetime.now()` rather than being converted to epoch milliseconds in the API response. | API returns an ISO format string instead of integer milliseconds. | Replaced with `to_epoch_ms` before returning the response. |
| 5 | `create_employee` | Manually checks for `emp_code` existence with `find_one` which allows race conditions for concurrent requests. | Two parallel requests with the same `emp_code` both create an employee. | Created a unique index on `emp_code` at startup and caught `DuplicateKeyError`. |
| 6 | `list_employees` | The total calculation `total = db.employees.count_documents({})` ignores the `department` query filter, violating R10. | Passing a department filter still returns the total count of the entire company. | Passed the query dictionary `q` into `count_documents(q)`. |
| 7 | `list_employees` | The pagination skip offset is `page * page_size`, meaning it completely skips the first page. | Passing `page=1` skips the first 20 records entirely. | Changed skip calculation to `(page - 1) * page_size`. |
| 8 | `punch_in` | Uses local timezone `datetime.now()` for `punched_at`, and derives the attendance date naively, ignoring R1 overnight shift logic. | Punches during an overnight shift are assigned to the wrong date. | Enforced UTC parsing, IST timezone casting, and explicit overnight logic for the date. |
| 9 | `punch_in` | Race condition: uses `find_one` then `insert_one` instead of a unique index on `(emp_code, date)`. | Two concurrent punch-ins for the same day both get inserted. | Created a unique compound index on `emp_code` + `date` and handled the `DuplicateKeyError`. |
| 10 | `punch_in` | Returns `id` which is internal to MongoDB and not part of the `AttendanceRecord` schema contract. | Swagger validation error or extraneous field `id` in response. | Removed `_id` extraction into `id` entirely from responses (`format_attendance`). |
| 11 | `list_attendance` | Pulls the entire collection into memory with `list(db.attendance_logs.find(q))` before sorting and slicing. | Out of Memory (OOM) or timeouts on a large dataset of ~100k records. | Implemented `.sort().skip().limit()` at the DB cursor level. |
| 12 | `list_attendance` | Uses Python's in-memory `docs.sort()` and `len(docs)` to count and sort all matched documents. | Extremely slow performance and memory bloat on large queries. | Pushed sorting and counting to the MongoDB cursor (`.count_documents(q)` and `.sort()`). |

Also note anything you looked at and decided was **not** a defect, and why:
- Connecting to MongoDB without `authSource` or credentials wasn't a defect because it uses a default `os.getenv("MONGO_URI")`, which allows running via Docker locally without credentials, standard for this setup.
- In `list_attendance`, I investigated the date range dictionary builder (`q["date"] = {}` and assigning `$gte` / `$lte`). While it looks like it might overwrite the dictionary, it actually sets properties on the same nested dict, which correctly filters the dates. Thus, it was not a defect, but just slightly unidiomatic Python.
