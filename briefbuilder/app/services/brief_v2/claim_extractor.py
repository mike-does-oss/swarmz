"""
Claim extraction (spec §4.4, triage LLM step).

A single LLM call sees all deduped, injection-cleared docs (as structured
fields, never re-injectable raw prose to downstream stages) and returns atomic
claims with their supporting source URLs and a proposed status. The two-source
rule is NOT trusted to the model — it is re-validated in code (see triage.py).
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from ...utils.logger import get_logger

logger = get_logger('briefbuilder.services.claim_extractor')

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
# Cap the number of claims asked for. The answer is JSON under a fixed output limit: on
# 2026-11-10 four of seven briefs asked for every fact, were cut off after 67 to 86 claims,
# failed to parse, and were frozen with no claims at all.
MAX_CLAIMS = 40


class ClaimExtractionError(RuntimeError):
    """The model's answer could not be used. The brief must not be built from nothing."""

_SYSTEM = (
    "You extract atomic factual claims from news documents for a neutral research brief. "
    "Rules: one fact per claim; flat attributive language only (e.g. 'Reuters reports', "
    "'the ministry stated'); no conclusions, no probability or likelihood language. "
    "For each claim list the exact source URLs (from the provided documents only) that support it. "
    "Assign a status: CONFIRMED (supported by two or more independent sources), "
    "REPORTED-UNCONFIRMED (single source), or CONFLICTING (sources disagree). "
    "Do NOT resolve conflicts. Return JSON only."
)

# How often a new value of the quantity is published. For the first two a forecaster needs a
# value from the last few days; for the rest the latest release can be weeks old and still current.
UPDATES = ("continuous", "daily", "weekly", "monthly", "quarterly", "yearly", "irregular")
FAST_UPDATES = ("continuous", "daily")
# What a latest_reading describes: the value of a measured quantity, or the state of play of an
# event or decision (not every question is about a price).
VALUE, STATE = "value", "state"
# A reading of a fast-moving quantity older than this at the freeze is treated as missing.
# Four days covers a weekend plus a market holiday.
MAX_READING_AGE = timedelta(days=4)


def build_prompt(docs: List[Any], question: str = "", max_claims: int = MAX_CLAIMS) -> List[Dict[str, str]]:
    blocks = []
    for d in docs:
        text = (getattr(d, 'full_text', '') or '')[:_MAX_DOC_CHARS]
        if getattr(d, 'date_basis', 'published') == 'retrieved':
            when = f"PAGE HAS NO DATE OF ITS OWN. RETRIEVED: {getattr(d, 'retrieved_at', '')}"
        else:
            when = f"PUBLICATION_TIME: {getattr(d, 'publication_time', '')}"
            if getattr(d, 'retrieved_at', ''):
                when += f"\nRETRIEVED: {d.retrieved_at}"
        looked_up = "LOOKED UP FOR: the latest value of what the question resolves on\n" \
            if getattr(d, 'latest_value', False) else ""
        blocks.append(
            f"URL: {d.url}\n"
            f"SOURCE: {getattr(d, 'source_name', '')} (tier {getattr(d, 'source_tier', '')})\n"
            f"{when}\n{looked_up}"
            f"TEXT: {text}"
        )
    focus = (
        f"The brief is for this question:\n{question}\n\n"
        "Extract first the claims a forecaster needs to answer it: the latest value of the quantity "
        "it asks about, with its date; recent readings and their direction; scheduled events and "
        "decisions before it resolves; and what named people or bodies have said about it. "
        "Leave out background that does not bear on the question.\n\n"
        "Dating a value: a quote page, live table or data page shows the value as it stood when the "
        "page was RETRIEVED, unless the page states its own date or time for that value; such a page's "
        "PUBLICATION_TIME is often the date the page was first made and says nothing about the value. "
        "Say in the claim what the value is as of (for example 'closed at 27,907.63 on November 9, 2026' "
        "or 'stood at $74,970 when retrieved on 2026-11-10'). RETRIEVED times are UTC: quote the date as "
        "given and do not join it to a clock time shown on the page.\n\n"
    ) if question else ""
    reading = (
        '"latest_reading" says where the thing the question resolves on stands now, from the most '
        "recent document that says so. It must also be the first claim.\n"
        'If the question resolves on a measured quantity (a price, an index level, a statistic, a '
        'temperature): "kind" is "value", "value" is the most recent value of that quantity (for a '
        'weather question, the latest official forecast of it) and "updates" says how often a new '
        "value is published.\n"
        "If it resolves on an event or a decision (a vote, a ruling, an election, an appointment, a "
        'game, an announcement): "kind" is "state", "quantity" names the matter (for example "Senate '
        'confirmation of the nominee"), "value" is one sentence on the state of play: what has '
        "already happened, what has not, and the next scheduled step with its date; "
        '"updates" is "irregular". Keep betting lines, point spreads, odds and prediction-market '
        "prices out of it: say who is playing or deciding, not who is favoured.\n"
        "If no document gives it, keep the object and set value, as_of and source_url to null.\n"
        '{"latest_reading": {"kind": "value|state", "quantity": "...", "value": "...", '
        '"as_of": "YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ", '
        f'"updates": "{"|".join(UPDATES)}", "source_url": "..."}}, '
    ) if question else "{"
    user = (
        focus +
        "Documents:\n\n" + "\n\n---\n\n".join(blocks) + "\n\n"
        f"Return at most {max_claims} claims, the most useful first, as JSON of the form:\n" + reading +
        '"claims": [{"text": "...", "source_urls": ["..."], "status": "CONFIRMED|REPORTED-UNCONFIRMED|CONFLICTING"}]}'
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


@dataclass
class Extraction:
    claims: List[Claim] = field(default_factory=list)
    # {"kind", "quantity", "value", "as_of", "updates", "source_url"}: kind "value" for a measured
    # quantity, "state" for where an event or decision stands. None when no question was given.
    latest_reading: Optional[Dict[str, Any]] = None


def clean_reading(raw: Any, known_urls) -> Optional[Dict[str, Any]]:
    """The model's latest_reading, kept only as far as it can be trusted: a value needs a
    source among the documents, and an unknown update rhythm is 'irregular'."""
    if not isinstance(raw, dict) or not str(raw.get('quantity') or '').strip():
        return None
    url = raw.get('source_url') if raw.get('source_url') in known_urls else None
    value = str(raw.get('value')).strip() if raw.get('value') not in (None, '') and url else None
    updates = str(raw.get('updates') or '').strip().lower()
    kind = STATE if str(raw.get('kind') or '').strip().lower() == STATE else VALUE
    if kind == STATE:
        updates = 'irregular'     # a state of play has no publication rhythm to be late against
    return {
        'kind': kind,
        'quantity': str(raw['quantity']).strip(),
        'value': value,
        'as_of': str(raw.get('as_of')).strip() if value and raw.get('as_of') else None,
        'updates': updates if updates in UPDATES else 'irregular',
        'source_url': url if value else None,
    }


def reading_problem(reading: Optional[Dict[str, Any]], freeze_ts: str) -> Optional[str]:
    """Why a brief with this latest reading should not be frozen, or None if it is fine.

    Only fast-moving quantities are judged (prices, index levels, daily weather): without a
    value from the last few days, forecasters can only answer 50%. The state of an event or a
    decision is never judged here: it changes when something happens, not on a schedule."""
    if not reading or reading.get('kind') == STATE or reading.get('updates') not in FAST_UPDATES:
        return None
    if not reading.get('value'):
        return 'no_current_reading'
    as_of, freeze = _parse_ts(reading.get('as_of')), _parse_ts(freeze_ts)
    if as_of is None:
        return 'reading_has_no_date'
    # whole days, so a Friday close still counts on the Tuesday after a long weekend
    if freeze is not None and freeze.date() - as_of.date() > MAX_READING_AGE:
        return f"stale_reading:{str(reading.get('as_of'))[:10]}"
    return None


def _parse_ts(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip().replace('Z', '+00:00')
    for candidate in (s, s[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def extract_claims(docs: List[Any], llm, question: str = "") -> List[Claim]:
    return extract(docs, llm, question).claims


def extract(docs: List[Any], llm, question: str = "") -> Extraction:
    """Claims from the docs, and the latest reading of what the question resolves on. Raises
    ClaimExtractionError if the model's answer cannot be used after one retry that asks for
    half as many claims (a cut-off answer is the usual cause)."""
    # Cap to the most authoritative docs so the single extraction prompt fits context. Pages
    # looked up for the latest value come first whatever their tier: quote pages are tier 4.
    if len(docs) > _MAX_DOCS_FOR_CLAIMS:
        docs = sorted(docs, key=lambda d: (not getattr(d, 'latest_value', False),
                                           getattr(d, 'source_tier', 4) or 4))[:_MAX_DOCS_FOR_CLAIMS]
    known_urls = {d.url for d in docs}
    response, problem = None, ""
    for max_claims in (MAX_CLAIMS, MAX_CLAIMS // 2):
        try:
            # Room for the whole answer: models that think first spend output tokens on that too.
            response = llm.chat_json(build_prompt(docs, question, max_claims), max_tokens=16000)
        except Exception as e:
            problem = f"LLM call failed: {str(e)[:300]}"
        else:
            if isinstance(response, dict) and isinstance(response.get('claims'), list):
                break
            problem = "malformed response"
        logger.error(f"claim extraction asking for {max_claims} claims: {problem}")
        response = None
    if response is None:
        raise ClaimExtractionError(problem)

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
    return Extraction(claims=claims, latest_reading=clean_reading(response.get('latest_reading'), known_urls))
