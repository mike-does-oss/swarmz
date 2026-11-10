import pytest
from flask import Flask

from app.api import simulation_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(simulation_bp, url_prefix="/api/simulation")
    return app.test_client()


def test_prepare_rejects_non_positive_num_agents(client):
    resp = client.post("/api/simulation/prepare",
                       json={"simulation_id": "sim_x", "num_agents": 0})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    assert "num_agents" in resp.get_json()["error"]


def test_prepare_rejects_oversized_num_agents(client):
    resp = client.post("/api/simulation/prepare",
                       json={"simulation_id": "sim_x", "num_agents": 999999})
    assert resp.status_code == 400
    assert "num_agents" in resp.get_json()["error"]


def test_prepare_rejects_non_int_num_agents(client):
    resp = client.post("/api/simulation/prepare",
                       json={"simulation_id": "sim_x", "num_agents": "lots"})
    assert resp.status_code == 400
    assert "num_agents" in resp.get_json()["error"]
