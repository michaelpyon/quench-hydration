import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["RUN_SCHEDULER"] = "0"
os.environ["EXPOSE_VERIFY_CODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from calculator import calculate_daily_oz, generate_schedule
from models import db


@pytest.fixture()
def app():
    db_fd, db_path = tempfile.mkstemp()
    test_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )
    with test_app.app_context():
        db.drop_all()
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture()
def client(app):
    return app.test_client()


def test_calculate_daily_oz_and_schedule_shape():
    user = {
        "weight_lbs": 180,
        "wake_time": "07:00",
        "sleep_time": "23:00",
        "timezone": "America/New_York",
        "activity_level": "moderate",
        "exercise_days": [1, 3, 5],
        "climate": "dry",
        "caffeine_cups": 2,
        "alcohol_weekly": 4,
    }
    goal = calculate_daily_oz(user)
    schedule = generate_schedule(user, goal)

    assert goal >= 48
    assert 4 <= len(schedule) <= 12
    assert sum(item["amount_oz"] for item in schedule) == goal


def test_onboard_verify_and_dashboard_flow(client):
    onboard_response = client.post(
        "/api/onboard",
        json={
            "first_name": "Sam",
            "weight_lbs": 180,
            "wake_time": "07:00",
            "sleep_time": "23:00",
            "timezone": "America/New_York",
            "activity_level": "light",
            "exercise_days": [1, 3, 5],
            "climate": "temperate",
            "caffeine_cups": 2,
            "alcohol_weekly": 2,
            "phone": "+13105551234",
        },
    )
    assert onboard_response.status_code == 201
    onboard_payload = onboard_response.get_json()
    assert onboard_payload["daily_goal"] >= 48
    assert len(onboard_payload["schedule_preview"]) >= 4

    verify_response = client.post(
        "/api/verify",
        json={
            "phone": "+13105551234",
            "code": onboard_payload["debug_verify_code"],
        },
    )
    assert verify_response.status_code == 200
    dashboard_payload = verify_response.get_json()
    assert dashboard_payload["daily_goal"] == onboard_payload["daily_goal"]
    assert dashboard_payload["phone"] == "+13105551234"
    assert len(dashboard_payload["schedule"]) >= 4

    dashboard_response = client.get("/api/dashboard/%2B13105551234")
    assert dashboard_response.status_code == 200
    assert dashboard_response.get_json()["daily_goal"] == dashboard_payload["daily_goal"]
