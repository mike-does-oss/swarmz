from dataclasses import dataclass

from app.services.brief_v2.assembler import (
    SECTION_HEADERS, format_claim, build_prompt, assemble,
)
from app.services.brief_v2.claim_extractor import Claim, CONFIRMED
from app.services.brief_v2.triage import TriageResult


@dataclass
class QP:
    question_text: str
    resolution_criteria: str
    category: str
    close_ts: str = ""
    edge_cases: str = ""


class FakeLLM:
    def __init__(self, text):
        self._text = text
        self.messages = None

    def chat(self, messages, temperature=0.7, max_tokens=4096):
        self.messages = messages
        return self._text


QP1 = QP("Will X win?", "Resolves YES if X wins per AP.", "politics",
         close_ts="2026-11-05T00:00:00Z", edge_cases="Cancellation resolves NO.")

SOURCES = {
    "https://reuters.com/a": {"source_name": "Reuters", "source_tier": 2,
                              "publication_time": "2026-07-01T00:00:00Z", "event_time": None},
    "https://apnews.com/b": {"source_name": "AP", "source_tier": 2,
                             "publication_time": "2026-07-01T00:00:00Z", "event_time": None},
}
TR = TriageResult(
    claims=[Claim("X leads by 5 points.", ["https://reuters.com/a", "https://apnews.com/b"], CONFIRMED,
                  None, "2026-07-01T00:00:00Z")],
    wire_groups={"https://reuters.com/a": 0, "https://apnews.com/b": 1},
    quarantined=[{"url": "https://bad.com/x", "reason": "prompt_injection", "match": "ignore..."}],
    sources=SOURCES,
)


def test_section_headers_are_the_eleven_spec_headers():
    assert SECTION_HEADERS == [
        "QUESTION & RESOLUTION CRITERIA", "AS-OF", "ENTITIES & RELATIONSHIPS",
        "CONFIRMED FACTS", "REPORTED BUT UNCONFIRMED", "CONFLICTING REPORTS",
        "QUANTITATIVE ANCHORS", "STAKEHOLDER POSITIONS & INCENTIVES",
        "RECENT TIMELINE", "OPEN UNCERTAINTIES", "AUDIT",
    ]


def test_format_claim_resolves_source_name_and_tier():
    line = format_claim(TR.claims[0], SOURCES)
    assert "[CONFIRMED]" in line
    assert "X leads by 5 points." in line
    assert "Reuters (tier 2" in line
    assert "AP (tier 2" in line


def test_build_prompt_includes_verbatim_question_resolution_asof_and_audit():
    joined = " ".join(m["content"] for m in build_prompt(QP1, TR, "2026-07-04T12:00:00Z"))
    assert "Will X win?" in joined
    assert "Resolves YES if X wins per AP." in joined
    assert "2026-07-04T12:00:00Z" in joined         # AS-OF freeze ts
    assert "X leads by 5 points." in joined          # claim text
    assert "Reuters (tier 2" in joined               # resolved source
    assert "https://bad.com/x" in joined             # AUDIT material
    # instructs the exact headers
    assert "CONFIRMED FACTS" in joined and "AUDIT" in joined
    assert "2026-11-05T00:00:00Z" in joined   # deadline
    assert "Cancellation resolves NO." in joined  # edge cases


def test_build_prompt_includes_retriever_discarded_in_audit():
    discarded = [{"url": "https://late.com/z", "reason": "publication_time > freeze_ts"}]
    joined = " ".join(m["content"] for m in build_prompt(QP1, TR, "2026-07-04T12:00:00Z", discarded=discarded))
    assert "https://late.com/z" in joined
    assert "publication_time > freeze_ts" in joined
    assert "https://bad.com/x" in joined  # quarantined still present too


def test_assemble_returns_llm_brief_text():
    brief = "# QUESTION & RESOLUTION CRITERIA\n..."
    llm = FakeLLM(brief)
    out = assemble(QP1, TR, "2026-07-04T12:00:00Z", llm)
    assert out == brief
