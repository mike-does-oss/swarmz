"""
Claim extraction (spec §4.4, triage LLM step).

A single LLM call sees all deduped, injection-cleared docs (as structured
fields, never re-injectable raw prose to downstream stages) and returns atomic
claims with their supporting source URLs and a proposed status. The two-source
rule is NOT trusted to the model — it is re-validated in code (see triage.py).
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.claim_extractor')

CONFIRMED = "CONFIRMED"
REPORTED_UNCONFIRMED = "REPORTED-UNCONFIRMED"
CONFLICTING = "CONFLICTING"
VALID_STATUSES = {CONFIRMED, REPORTED_UNCONFIRMED, CONFLICTING}

# Cap per-doc text handed to the model (full text can be long; the model needs
# enough to extract atomic claims, not the entire article verbatim).
_MAX_DOC_CHARS = 6000
# Cap docs fed to a single claim-extraction call so the prompt stays within the
# model's context (dozens of full-text docs otherwise blow it out). Prefer the
# most authoritative sources (lowest tier number).
_MAX_DOCS_FOR_CLAIMS = 20

_SYSTEM = (
    "You extract atomic factual claims from news documents for a neutral research brief. "
    "Rules: one fact per claim; flat attributive language only (e.g. 'Reuters reports', "
    "'the ministry stated'); no conclusions, no probability or likelihood language. "
    "For each claim list the exact source URLs (from the provided documents only) that support it. "
    "Assign a status: CONFIRMED (supported by two or more independent sources), "
    "REPORTED-UNCONFIRMED (single source), or CONFLICTING (sources disagree). "
    "Do NOT resolve conflicts. Return JSON only."
)


def build_prompt(docs: List[Any]) -> List[Dict[str, str]]:
    blocks = []
    for d in docs:
        text = (getattr(d, 'full_text', '') or '')[:_MAX_DOC_CHARS]
        blocks.append(
            f"URL: {d.url}\n"
            f"SOURCE: {getattr(d, 'source_name', '')} (tier {getattr(d, 'source_tier', '')})\n"
            f"PUBLICATION_TIME: {getattr(d, 'publication_time', '')}\n"
            f"TEXT: {text}"
        )
    user = (
        "Documents:\n\n" + "\n\n---\n\n".join(blocks) + "\n\n"
        "Return JSON of the form:\n"
        '{"claims": [{"text": "...", "source_urls": ["..."], "status": "CONFIRMED|REPORTED-UNCONFIRMED|CONFLICTING"}]}'
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


@dataclass
class Claim:
    text: str
    source_urls: List[str]
    status: str
    event_time: Optional[str] = None
    publication_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_status(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip().upper().replace('_', '-') in VALID_STATUSES:
        return raw.strip().upper().replace('_', '-')
    return REPORTED_UNCONFIRMED


def extract_claims(docs: List[Any], llm) -> List[Claim]:
    # Cap to the most authoritative docs so the single extraction prompt fits context.
    if len(docs) > _MAX_DOCS_FOR_CLAIMS:
        docs = sorted(docs, key=lambda d: getattr(d, 'source_tier', 4) or 4)[:_MAX_DOCS_FOR_CLAIMS]
    known_urls = {d.url for d in docs}
    try:
        response = llm.chat_json(build_prompt(docs))
    except Exception as e:
        logger.error(f"claim extraction LLM call failed: {e}")
        return []
    if not isinstance(response, dict) or not isinstance(response.get('claims'), list):
        logger.error("claim extraction returned a malformed response")
        return []

    claims: List[Claim] = []
    for item in response['claims']:
        if not isinstance(item, dict) or not item.get('text'):
            continue
        source_urls = [u for u in (item.get('source_urls') or []) if u in known_urls]
        if not source_urls:
            continue
        claims.append(Claim(
            text=item['text'],
            source_urls=source_urls,
            status=_normalize_status(item.get('status')),
            event_time=item.get('event_time'),
            publication_time=item.get('publication_time'),
        ))
    return claims
