import pytest
from flask import Flask

import app.api.brief_v2 as brief_v2_routes
from app.api import brief_v2_bp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(brief_v2_routes, "audit_all", lambda conn, briefs_dir, llm=None: {"briefs": [], "summary": {"total": 0, "passed": 0, "failed": 0}})
    monkeypatch.setattr(brief_v2_routes, "get_connection", lambda p: type("C", (), {"close": lambda self: None})())
    app = Flask(__name__)
    app.register_blueprint(brief_v2_bp, url_prefix="/api/brief/v2")
    return app.test_client()


def test_audit_route(client):
    resp = client.get("/api/brief/v2/audit")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["summary"]["total"] == 0
