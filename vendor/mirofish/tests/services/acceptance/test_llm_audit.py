from app.services.acceptance.llm_audit import neutrality_audit, odds_llm_audit


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return self._payload


BRIEF = "## CONFIRMED FACTS\n- Reuters reports the vote passed."


def test_neutrality_flags_leak():
    out = neutrality_audit(BRIEF, "Will X win?", FakeLLM({"implies_outcome": True, "note": "leans YES"}))
    assert out["implies_outcome"] is True
    assert "YES" in out["note"]


def test_neutrality_pass_and_prompt_content():
    llm = FakeLLM({"implies_outcome": False, "note": ""})
    out = neutrality_audit(BRIEF, "Will X win?", llm)
    assert out["implies_outcome"] is False
    joined = " ".join(m["content"] for m in llm.messages)
    assert "Will X win?" in joined and "Reuters reports the vote passed." in joined


def test_neutrality_inconclusive_on_malformed():
    assert neutrality_audit(BRIEF, "Q?", FakeLLM("nope"))["implies_outcome"] is False


def test_odds_llm_audit_flags_and_passes():
    flagged = odds_llm_audit(BRIEF, FakeLLM({"found": ["the line was -150"]}))
    assert flagged["clean"] is False and flagged["found"] == ["the line was -150"]
    clean = odds_llm_audit(BRIEF, FakeLLM({"found": []}))
    assert clean["clean"] is True


def test_odds_llm_audit_inconclusive_on_malformed():
    assert odds_llm_audit(BRIEF, FakeLLM("nope"))["clean"] is True
