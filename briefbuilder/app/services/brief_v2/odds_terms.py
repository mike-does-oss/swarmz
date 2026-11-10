"""
Odds / market-price term matcher (spec §4.2 query ban, defense-in-depth).

Used to block search queries that would retrieve betting odds, bookmaker lines,
or prediction-market prices — contamination is stopped at the search stage.
"""

import re

_ODDS_PATTERNS = [
    r'\bodds\b(?!\s+ratio)',
    r'\bbetting\b',
    r'\bbookmaker',
    r'\bsportsbook',
    r'\bmoney\s*line\b',
    r'\bpoint\s+spread\b',
    r'\bover/under\b',
    r'\bprediction\s+market',
    r'\bpolymarket\b',
    r'\bkalshi\b',
    r'\bbetfair\b',
    r'\bimplied\s+(?:probability|odds)\b',
    r'\bvegas\s+line',
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _ODDS_PATTERNS]


def is_odds_query(q: str) -> bool:
    if not q:
        return False
    return any(p.search(q) for p in _COMPILED)


# Contamination scan for a FINAL brief. Unlike is_odds_query (which blocks bare
# market names in search queries), this must NOT flag a market name that merely
# appears in a quoted resolution source — only odds/price contamination.
_CONTAMINATION_PATTERNS = [
    r'\bbetting\s+odds\b',
    r'\bodds\b(?!\s+ratio)',
    r'\bbookmaker',
    r'\bsportsbook',
    r'\bmoneyline\b',
    r'\bmoney\s*line\b',
    r'\bpoint\s+spread\b',
    r'\bimplied\s+(?:probability|odds)\b',
    r'\bbetfair\b',
    r'\bvegas\s+line',
    r'\b(?:polymarket|kalshi|prediction\s+market)\s+(?:price|odds|implied|trading\s+at)',
    r'\btrading\s+at\s+\d+\s*(?:cents|¢|%)',
]

_COMPILED_CONTAMINATION = [re.compile(p, re.IGNORECASE) for p in _CONTAMINATION_PATTERNS]


def find_odds_contamination(text: str) -> list:
    if not text:
        return []
    matches = []
    for pattern in _COMPILED_CONTAMINATION:
        for m in pattern.finditer(text):
            matches.append(m.group(0))
    return matches
