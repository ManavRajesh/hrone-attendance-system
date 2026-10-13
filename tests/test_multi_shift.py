import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app, db, IST

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_db():
    # Setup test DB state before tests
    with TestClient(app) as tc:
        # Trigger startup event to seed shifts & create indexes
        pass
    yield

def test_startup_seeds_default_shifts():
    with TestClient(app) as tc:
        res = tc.get("/shifts")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 4
        shift_codes = [s["shift_code"] for s in data["items"]]
        assert "GENERAL" in shift_codes
        assert "MORNING" in shift_codes
        assert "EVENING" in shift_codes
        assert "NIGHT" in shift_codes

def test_create_and_get_custom_shift():
    with TestClient(app) as tc:
        new_shift = {
            "shift_code": "FLEX_TECH",
            "name": "Flex Tech Shift",
            "start_time": "10:00",
            "end_time": "19:00",
            "grace_period_minutes": 25,
            "half_day_threshold_hours": 4.0,
            "overtime_threshold_minutes": 45,
            "description": "Flexible engineering shift with 25-minute grace period."
        }
        res = tc.post("/shifts", json=new_shift)
        assert res.status_code in [201, 409]
        if res.status_code == 201:
            body = res.json()
            assert body["shift_code"] == "FLEX_TECH"
            assert body["grace_period_minutes"] == 25
            assert body["start_time"] == "10:00"

        # Fetch it
        fetch_res = tc.get("/shifts/FLEX_TECH")
        assert fetch_res.status_code == 200
        assert fetch_res.json()["name"] == "Flex Tech Shift"

def test_patch_shift_grace_period():
    with TestClient(app) as tc:
        patch_res = tc.patch("/shifts/GENERAL", json={"grace_period_minutes": 12})
        assert patch_res.status_code == 200
        assert patch_res.json()["grace_period_minutes"] == 12

        # Restore to 10
        tc.patch("/shifts/GENERAL", json={"grace_period_minutes": 10})

def test_employee_creation_with_shift():
    emp_code = "EMP9901"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    with TestClient(app) as tc:
        emp_payload = {
            "emp_code": emp_code,
            "name": "Shift Worker 1",
            "email": "shift1@example.com",
            "department": "Operations",
            "shift_code": "MORNING",
            "joined_on": "2026-01-01"
        }
        res = tc.post("/employees", json=emp_payload)
        assert res.status_code == 201
        data = res.json()
        assert data["shift_code"] == "MORNING"
        assert data["shift_start"] == "06:00"
        assert data["shift_end"] == "15:00"

def test_assign_employee_to_new_shift():
    emp_code = "EMP9902"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    with TestClient(app) as tc:
        emp_payload = {
            "emp_code": emp_code,
            "name": "Shift Worker 2",
            "email": "shift2@example.com",
            "department": "Engineering",
            "joined_on": "2026-01-01"
        }
        res = tc.post("/employees", json=emp_payload)
        assert res.status_code == 201
        assert res.json()["shift_code"] == "GENERAL"

        # Reassign to NIGHT
        assign_res = tc.post(f"/employees/{emp_code}/shift", json={"shift_code": "NIGHT"})
        assert assign_res.status_code == 200
        assert assign_res.json()["shift_code"] == "NIGHT"
        assert assign_res.json()["shift_start"] == "22:00"
        assert assign_res.json()["shift_end"] == "06:00"

def test_grace_period_enforcement_custom_shift():
    # Setup custom shift with 20m grace
    emp_code = "EMP9903"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    with TestClient(app) as tc:
        tc.post("/shifts", json={
            "shift_code": "CUSTOM_20",
            "name": "Custom 20m Grace",
            "start_time": "10:00",
            "end_time": "19:00",
            "grace_period_minutes": 20
        })

        tc.post("/employees", json={
            "emp_code": emp_code,
            "name": "Grace Test Employee",
            "email": "grace@example.com",
            "department": "Support",
            "shift_code": "CUSTOM_20",
            "joined_on": "2026-01-01"
        })

        # 1. Punch in at 10:18:00 IST on 2026-07-10 (18 minutes late, within 20m grace) -> late_minutes = 0
        dt_10_18 = datetime.fromisoformat("2026-07-10T10:18:00+05:30")
        epoch_10_18 = int(dt_10_18.timestamp() * 1000)
        res1 = tc.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": epoch_10_18})
        assert res1.status_code == 201
        assert res1.json()["late_minutes"] == 0
        assert res1.json()["shift_code"] == "CUSTOM_20"

    # Cleanup for next date punch
    db.attendance_logs.delete_many({"emp_code": emp_code})

    with TestClient(app) as tc:
        # 2. Punch in at 10:21:00 IST on 2026-07-10 (21 minutes late, exceeds 20m grace) -> late_minutes = 21
        dt_10_21 = datetime.fromisoformat("2026-07-10T10:21:00+05:30")
        epoch_10_21 = int(dt_10_21.timestamp() * 1000)
        res2 = tc.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": epoch_10_21})
        assert res2.status_code == 201
        assert res2.json()["late_minutes"] == 21

def test_overnight_shift_punch_in():
    emp_code = "EMP9904"
    db.employees.delete_one({"emp_code": emp_code})
    db.attendance_logs.delete_many({"emp_code": emp_code})

    with TestClient(app) as tc:
        tc.post("/employees", json={
            "emp_code": emp_code,
            "name": "Night Owl",
            "email": "owl@example.com",
            "department": "Security",
            "shift_code": "NIGHT",  # 22:00 to 06:00
            "joined_on": "2026-01-01"
        })

        # Punch in at 01:15 AM on July 11th (belongs to shift that started on July 10th)
        dt_night = datetime.fromisoformat("2026-07-11T01:15:00+05:30")
        epoch_night = int(dt_night.timestamp() * 1000)
        res = tc.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": epoch_night})
        assert res.status_code == 201
        assert res.json()["date"] == "2026-07-10"
        assert res.json()["shift_code"] == "NIGHT"
