import pytest
from flask import Flask

import app.api.brief_v2 as brief_v2_routes
from app.api import brief_v2_bp


@pytest.fixture
def client(monkeypatch):
    # never actually run the orchestrator in the route test
    monkeypatch.setattr(brief_v2_routes, "_spawn_run_prediction", lambda *a, **k: "task_123")
    app = Flask(__name__)
    app.register_blueprint(brief_v2_bp, url_prefix="/api/brief/v2")
    return app.test_client()


def test_run_prediction_route_starts_task(client):
    resp = client.post("/api/brief/v2/Q1/run-prediction", json={"num_agents": 50})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["task_id"] == "task_123"
    assert body["data"]["status"] == "running"
    assert body["data"]["question_id"] == "Q1"


def test_run_prediction_route_rejects_bad_num_agents(client):
    resp = client.post("/api/brief/v2/Q1/run-prediction", json={"num_agents": 0})
    assert resp.status_code == 400
    assert "num_agents" in resp.get_json()["error"]


def test_run_prediction_route_rejects_bad_max_rounds(client):
    resp = client.post("/api/brief/v2/Q1/run-prediction", json={"max_rounds": 0})
    assert resp.status_code == 400
    assert "max_rounds" in resp.get_json()["error"]
