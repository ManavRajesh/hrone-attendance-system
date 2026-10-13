from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    with TestClient(app) as tc:
        res = tc.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

def test_list_employees():
    with TestClient(app) as tc:
        res = tc.get("/employees")
        assert res.status_code == 200
        assert "items" in res.json()
        assert res.json()["total"] >= 6

def test_list_attendance():
    with TestClient(app) as tc:
        res = tc.get("/attendance")
        assert res.status_code == 200
        assert "items" in res.json()
        assert res.json()["total"] >= 9

def test_department_summary():
    with TestClient(app) as tc:
        res = tc.get("/analytics/departments/summary?month=2026-07")
        assert res.status_code == 200
        assert "items" in res.json()

def test_late_leaderboard():
    with TestClient(app) as tc:
        res = tc.get("/analytics/leaderboard/late?month=2026-07")
        assert res.status_code == 200
        assert "items" in res.json()

def test_department_trend():
    with TestClient(app) as tc:
        res = tc.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-10")
        assert res.status_code == 200
        assert "items" in res.json()
