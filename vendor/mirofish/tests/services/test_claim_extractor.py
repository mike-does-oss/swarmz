from dataclasses import dataclass

from app.services.brief_v2.claim_extractor import (
    Claim, extract_claims, build_prompt, CONFIRMED, REPORTED_UNCONFIRMED,
)


@dataclass
class Doc:
    url: str
    source_name: str
    source_tier: int
    publication_time: str
    event_time: object
    full_text: str


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return self._payload


DOCS = [
    Doc("https://reuters.com/a", "Reuters", 2, "2026-07-01T00:00:00Z", None, "The vote passed 60-40."),
    Doc("https://apnews.com/b", "AP", 2, "2026-07-01T00:00:00Z", None, "Officials confirmed the vote passed."),
]


def test_build_prompt_includes_doc_fields_and_urls():
    msgs = build_prompt(DOCS)
    joined = " ".join(m["content"] for m in msgs)
    assert "https://reuters.com/a" in joined
    assert "Reuters" in joined
    assert "The vote passed 60-40." in joined


def test_extract_claims_parses_llm_output():
    payload = {"claims": [
        {"text": "The vote passed.", "source_urls": ["https://reuters.com/a", "https://apnews.com/b"],
         "status": "CONFIRMED", "event_time": "2026-06-30T00:00:00Z", "publication_time": "2026-07-01T00:00:00Z"},
    ]}
    claims = extract_claims(DOCS, FakeLLM(payload))
    assert len(claims) == 1
    assert claims[0].text == "The vote passed."
    assert claims[0].status == CONFIRMED
    assert claims[0].source_urls == ["https://reuters.com/a", "https://apnews.com/b"]


def test_extract_claims_drops_unknown_source_urls_and_normalizes_status():
    payload = {"claims": [
        {"text": "X.", "source_urls": ["https://reuters.com/a", "https://hallucinated.com/z"],
         "status": "maybe"},
    ]}
    claims = extract_claims(DOCS, FakeLLM(payload))
    assert claims[0].source_urls == ["https://reuters.com/a"]   # hallucinated url dropped
    assert claims[0].status == REPORTED_UNCONFIRMED             # unknown status normalized


def test_extract_claims_returns_empty_on_malformed_response():
    assert extract_claims(DOCS, FakeLLM({"nonsense": True})) == []
    assert extract_claims(DOCS, FakeLLM("not a dict")) == []


def test_extract_claims_drops_claim_with_no_valid_sources():
    payload = {"claims": [
        {"text": "Invented.", "source_urls": ["https://hallucinated.com/z"], "status": "CONFIRMED"},
        {"text": "Real.", "source_urls": ["https://reuters.com/a"], "status": "REPORTED-UNCONFIRMED"},
    ]}
    claims = extract_claims(DOCS, FakeLLM(payload))
    assert [c.text for c in claims] == ["Real."]  # sourceless claim dropped
