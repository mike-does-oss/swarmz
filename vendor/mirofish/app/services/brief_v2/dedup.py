"""
Wire-copy near-duplicate detection (spec §4.4).

A republished wire story shares its body text, so text-shingling + Jaccard
similarity groups such copies together. Each group counts as ONE independent
source for the two-source rule.
"""

import re
from typing import List, Set, Dict, Any

_PUNCT = re.compile(r'[^a-z0-9\s]+')
_WS = re.compile(r'\s+')


def normalize_text(text: str) -> str:
    lowered = (text or '').lower()
    stripped = _PUNCT.sub(' ', lowered)
    return _WS.sub(' ', stripped).strip()


def shingles(text: str, k: int = 5) -> Set[str]:
    words = normalize_text(text).split()
    if not words:
        return set()
    if len(words) < k:
        return {' '.join(words)}
    return {' '.join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def group_near_duplicates(docs: List[Any], threshold: float = 0.8, k: int = 5) -> Dict[str, int]:
    reps = []  # list of (group_id, shingle_set)
    url_to_group: Dict[str, int] = {}
    for doc in docs:
        sh = shingles(getattr(doc, 'full_text', '') or '', k=k)
        matched = None
        for group_id, rep_sh in reps:
            if jaccard(sh, rep_sh) >= threshold:
                matched = group_id
                break
        if matched is None:
            matched = len(reps)
            reps.append((matched, sh))
        url_to_group[doc.url] = matched
    return url_to_group


def count_independent(source_urls: List[str], url_to_group: Dict[str, int]) -> int:
    groups = {url_to_group[u] for u in source_urls if u in url_to_group}
    return len(groups)
