import copy
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from swarmz.bridge import SwarmAgent, SwarmResult
from swarmz.config import settings
from swarmz.registry import store
from swarmz.registry.db import connect

FIXTURES = Path(__file__).parent / "fixtures"
BRIEF = FIXTURES / "brief_fed.md"
T0 = datetime(2027, 1, 1)


def _h(s: str) -> float:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class FakeLLM:
    """Deterministic stand-in. Dispatches on the system prompt."""

    def __init__(self, lessons=None):
        self.calls = []
        self.lessons = lessons or ["When evidence is mixed, stay closer to the base rate than feels natural."]

    def json(self, system, user, *, model, max_tokens=1024, temperature=None):
        self.calls.append((system[:30], model))
        if system.startswith("You are one forecaster"):
            persona = user.split("QUESTION:")[0]
            return {"prob": round(0.05 + 0.9 * _h(persona), 4), "rationale": "fixture rationale"}
        if system.startswith("You are a member of a forecasting panel"):
            persona = user.split("QUESTION:")[0]
            said = user.split("THE DISCUSSION SO FAR")[1].count("\n[")
            return {"post": f"fixture post after {said} entries", "prob": round(0.05 + 0.9 * _h(persona + "talk"), 4)}
        if system.startswith("You are a forecaster reviewing"):
            return {"lessons": list(self.lessons)}
        if system.startswith("You consolidate"):
            return {"lessons": ["Weigh base rates before case-specific evidence."]}
        if system.startswith("You edit forecaster persona"):
            parent = user.split("PARENT PERSONA:\n")[1].split("\n\nVARIATION")[0]
            var = user.split("VARIATION: ")[1].split("\n")[0]
            return {"base_prompt": f"{parent} Variation: {var}"}
        raise AssertionError(f"unexpected prompt: {system[:60]}")


def fake_swarm(req):
    agents = [SwarmAgent(i, p.persona_id, p.profile, posts=[f"post by {p.persona_id}"],
                         discussion="[Post 1] Wary Lynx: the committee is data dependent")
              for i, p in enumerate(req.personas)]
    return SwarmResult(agents=agents, native_prob=0.4)


@pytest.fixture
def cfg(monkeypatch):
    """Mutable copy of learning settings for a test."""
    s = settings()
    learning = copy.deepcopy(s.learning)
    monkeypatch.setattr(s, "learning", learning)
    monkeypatch.setattr(s, "population_size", 6)
    monkeypatch.setattr(s, "discussion_rounds", 2)   # the default is 5; two keeps the suite quick
    monkeypatch.setattr(s, "reports_dir", Path(os.environ.get("PYTEST_TMP", "/tmp")) / "swarmz-test-reports")
    return learning


@pytest.fixture
def con(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(settings(), "reports_dir", tmp_path / "reports")
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def llm():
    return FakeLLM()


def add_q(con, qid, freeze, split="train", category="economy", text=None, source="manual"):
    return store.add_question(con, question_id=qid, question_text=text or f"Will the Fed cut rates at meeting {qid}?",
                              freeze_ts=freeze, brief_path=BRIEF, category=category, split=split, source=source)
