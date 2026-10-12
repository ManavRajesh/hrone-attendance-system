# DECISIONS.md

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
- `employees`: `emp_code` (unique) to guarantee uniqueness and serve lookups. `department` for listing and department-wise analytics.
- `attendance_logs`: Compound unique index on `(emp_code, date)` to enforce one record per day per employee. Compound index on `(date, emp_code)` (descending/ascending) for efficient pagination and sorting in `list_attendance`.
- I considered an index on `status` but rejected it because it typically has low cardinality and wouldn't be highly selective enough for the `list_attendance` endpoint compared to `date`.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
Both requests proceed past the employee lookup. They both attempt to `insert_one` into `attendance_logs` with the same `(emp_code, date)`. Due to the compound unique index on these two fields, MongoDB atomically accepts the first insert and throws a `DuplicateKeyError` for the second. The app catches this exception and returns a 409 Conflict for the second request.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
The leaderboard uses MongoDB's `$setWindowFields` with `$rank` to compute standard competition ranking. If employees are tied on `total_late_minutes` exactly at the `limit` rank, all of them receive the same rank. Because the pipeline matches `$rank <= limit`, the response will return all tied employees, exceeding the `limit` parameter, conforming to the requirement.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
The aggregation pipeline for the department summary begins on the `employees` collection rather than `attendance_logs`. It groups by `department` to sum the total headcount of employees who joined on or before the month's end. It then uses a `$lookup` to join their attendance logs, ensuring that even departments with zero attendance logs are represented accurately based on their current headcount.

5. **One thing you would change** if this had to serve 100x the data.
I would decouple the analytics pipelines from real-time endpoints. For 100x data, running complex `$lookup` aggregations across `employees` and `attendance_logs` synchronously could cause timeouts. Instead, I would introduce asynchronous pre-aggregation (e.g., materialized views in MongoDB or periodic cron jobs) to update a daily roll-up collection for department statistics and leaderboards.
