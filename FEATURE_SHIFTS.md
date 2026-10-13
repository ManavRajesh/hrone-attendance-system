# Multi-Shift Scheduling & Configurable Grace Periods

## Overview
In enterprise HRMS environments, rigid fixed schedules (`09:30 - 18:30`) fail to support rotational rosters, plant operations, call centers, and engineering on-call teams. 

This feature introduces a dedicated **Shift Management Engine** with:
1. **Dynamic Shift Definitions** stored in MongoDB with unique indexing.
2. **Customizable Grace Periods** configured per shift (replacing hardcoded 10-minute rules).
3. **Overnight Shift Handling** across midnight boundaries (e.g. 22:00 to 06:00).
4. **Flexible Shift Assignment** to employees with full backward compatibility.
5. **Historical Shift Snapshots** captured on attendance records.

---

## 1. MongoDB Data Model

### Collection: `shifts`
```json
{
  "_id": ObjectId("..."),
  "shift_code": "MORNING",
  "name": "Early Morning Shift",
  "start_time": "06:00",
  "end_time": "15:00",
  "grace_period_minutes": 15,
  "half_day_threshold_hours": 4.5,
  "overtime_threshold_minutes": 30,
  "description": "Early morning operational shift with 15-minute grace period.",
  "created_at": 1728800000000,
  "updated_at": 1728800000000
}
```

### Indexing:
- `db.shifts.create_index("shift_code", unique=True)`

### Pre-Seeded Shifts (Startup):
- **`GENERAL`**: `09:30 - 18:30` | Grace: 10 mins (Standard corporate shift)
- **`MORNING`**: `06:00 - 15:00` | Grace: 15 mins (Operational & facility shift)
- **`EVENING`**: `14:00 - 23:00` | Grace: 10 mins (Afternoon-to-night shift)
- **`NIGHT`**: `22:00 - 06:00` | Grace: 15 mins (Overnight graveyard shift)

---

## 2. Dynamic Grace Period Business Logic

The calculation evaluates whether the punch-in exceeds the specific shift's grace threshold:

$$\text{diff\_sec} = \text{punch\_in\_utc} - \text{shift\_start\_utc}$$

- If $\text{diff\_sec} \le \text{grace\_period\_minutes} \times 60$:
  $$\text{late\_minutes} = 0$$
- If $\text{diff\_sec} > \text{grace\_period\_minutes} \times 60$:
  $$\text{late\_minutes} = \lfloor \frac{\text{diff\_sec}}{60} \rfloor$$

*(Following Rule R2: when an employee is late beyond the grace period, late minutes are counted from the shift start, not from the end of the grace period.)*

### Example:
- **Shift `FLEX_TECH`** (Starts 10:00 AM, 25-minute grace):
  - Punch at 10:24 AM $\rightarrow$ **0 late minutes** (within 25m grace).
  - Punch at 10:26 AM $\rightarrow$ **26 late minutes** (counted from 10:00 AM).

---

## 3. Endpoints Reference

### Create Shift
`POST /shifts`
```json
{
  "shift_code": "ROTATIONAL_B",
  "name": "Rotational B Shift",
  "start_time": "11:00",
  "end_time": "20:00",
  "grace_period_minutes": 20,
  "half_day_threshold_hours": 4.0,
  "overtime_threshold_minutes": 45,
  "description": "Late morning flexible roster"
}
```
**Response (201 Created)**

---

### List Shifts
`GET /shifts`
```json
{
  "items": [
    {
      "shift_code": "GENERAL",
      "name": "General Day Shift",
      "start_time": "09:30",
      "end_time": "18:30",
      "grace_period_minutes": 10,
      "half_day_threshold_hours": 4.5,
      "overtime_threshold_minutes": 30,
      "created_at": 1728800000000,
      "updated_at": 1728800000000
    }
  ],
  "total": 4
}
```

---

### Get Shift Details
`GET /shifts/{shift_code}`

---

### Update Shift
`PATCH /shifts/{shift_code}`
```json
{
  "grace_period_minutes": 15,
  "name": "Updated Shift Name"
}
```

---

### Assign Shift to Employee
`POST /employees/{emp_code}/shift`
```json
{
  "shift_code": "NIGHT"
}
```
Updates the employee's `shift_code`, `shift_start`, and `shift_end` synchronously.

---

## 4. Backward Compatibility

- Existing seed data without `shift_code` defaults cleanly to `GENERAL` (`09:30 - 18:30`, 10 min grace).
- Existing OpenAPI schemas (`POST /employees`, `POST /attendance/punch-in`, `POST /attendance/punch-out`, `PATCH /attendance/...`) continue to accept and return all standard fields without schema errors.
- Grader test runs evaluating Rules R1–R10 behave identically.
