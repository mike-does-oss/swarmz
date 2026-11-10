"""
Assembler (spec §4.5).

Consumes structured TriageResult data (never raw web text) and asks model #1 to
write the brief following the fixed 11-section template with flat attributive
language, no conclusions, and a controlled length.
"""

from typing import List, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.assembler')

SECTION_HEADERS = [
    "QUESTION & RESOLUTION CRITERIA",
    "AS-OF",
    "ENTITIES & RELATIONSHIPS",
    "CONFIRMED FACTS",
    "REPORTED BUT UNCONFIRMED",
    "CONFLICTING REPORTS",
    "QUANTITATIVE ANCHORS",
    "STAKEHOLDER POSITIONS & INCENTIVES",
    "RECENT TIMELINE",
    "OPEN UNCERTAINTIES",
    "AUDIT",
]

_MIN_WORDS = 1500
_MAX_WORDS = 3000

_SYSTEM = (
    "You assemble a neutral, contamination-free research brief from structured claims. "
    "Use EXACTLY these section headers in this order, each as a markdown H2:\n"
    + "\n".join(f"## {h}" for h in SECTION_HEADERS) + "\n\n"
    "Rules: flat attributive language only ('Reuters reports', 'the ministry stated'); "
    "no loaded verbs; NO conclusions and NO probability or likelihood language anywhere; "
    "any sentence that pre-answers the question is a defect. One fact per bullet with its "
    "source and timestamp. Put CONFIRMED claims under CONFIRMED FACTS, single-source under "
    "REPORTED BUT UNCONFIRMED, and disagreements side-by-side under CONFLICTING REPORTS "
    "(do NOT resolve conflicts). Use only the provided claims; do not invent facts. "
    "For the ENTITIES & RELATIONSHIPS section, synthesize an exhaustive roster of every "
    "person, organization, place, and instrument named across the claims, and state each "
    "relationship as a plain declarative sentence (e.g. 'X is the foreign minister of Y'). "
    "This section is the highest priority and feeds downstream world-modeling; be exhaustive "
    "even when a relationship appears only once. "
    f"Target length {_MIN_WORDS} to {_MAX_WORDS} words."
)


def format_claim(claim, sources: Dict[str, Dict[str, Any]]) -> str:
    parts = []
    for url in claim.source_urls:
        meta = sources.get(url, {})
        name = meta.get('source_name') or url
        tier = meta.get('source_tier')
        pub = meta.get('publication_time')
        parts.append(f"{name} (tier {tier}, {pub})")
    src = "; ".join(parts) if parts else "no source"
    return f'[{claim.status}] "{claim.text}" | sources: {src}'


def build_prompt(qp, triage_result, freeze_ts: str, discarded=None) -> List[Dict[str, str]]:
    claim_lines = "\n".join(format_claim(c, triage_result.sources) for c in triage_result.claims)
    audit_items = list(triage_result.quarantined) + list(discarded or [])
    audit_lines = "\n".join(
        f"- {q.get('url')} ({q.get('reason')})" for q in audit_items
    ) or "- none"
    user = (
        f"QUESTION (verbatim): {qp.question_text}\n"
        f"RESOLUTION CRITERIA (verbatim): {qp.resolution_criteria}\n"
        f"RESOLUTION DEADLINE: {getattr(qp, 'close_ts', '') or 'not specified'}\n"
        f"RESOLUTION EDGE CASES: {getattr(qp, 'edge_cases', '') or 'none noted'}\n"
        f"AS-OF (freeze timestamp): {freeze_ts}\n\n"
        f"CLAIMS (organize into the correct sections; keep attribution and timestamps):\n"
        f"{claim_lines or '- none'}\n\n"
        f"AUDIT (list these discarded/quarantined docs under the AUDIT section):\n"
        f"{audit_lines}\n\n"
        "Write the brief now, using the exact section headers."
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def assemble(qp, triage_result, freeze_ts: str, llm, discarded=None) -> str:
    text = llm.chat(build_prompt(qp, triage_result, freeze_ts, discarded=discarded),
                    temperature=0.3, max_tokens=8000)
    logger.info(f"assembler: produced {len(text.split())} words")
    return text
