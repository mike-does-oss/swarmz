"""
Source tiering (spec §4.3).

1 = official/primary (.gov/.mil/.int + known official orgs)
2 = wire (Reuters/AP/AFP)
3 = major outlet
4 = other (default)
"""

from urllib.parse import urlparse

# Government/official signals that can appear as any dot-separated label
# (e.g. sec.gov, gov.uk, news.gov.hk, gouv.fr) without misfiring on substrings
# like "mygov.com" (label is "mygov", not "gov").
_TIER1_LABELS = {'gov', 'mil', 'gouv'}
_TIER1_TLD_SUFFIXES = ('.int',)
_TIER1_DOMAINS = {
    'un.org', 'europa.eu', 'imf.org', 'worldbank.org', 'oecd.org',
    'federalreserve.gov', 'whitehouse.gov', 'sec.gov', 'who.int',
}
_TIER2_DOMAINS = {
    'reuters.com', 'apnews.com', 'ap.org', 'afp.com',
}
_TIER3_DOMAINS = {
    'nytimes.com', 'washingtonpost.com', 'wsj.com', 'bbc.com', 'bbc.co.uk',
    'theguardian.com', 'ft.com', 'bloomberg.com', 'cnn.com', 'nbcnews.com',
    'cnbc.com', 'politico.com', 'aljazeera.com', 'economist.com', 'npr.org',
    'abcnews.go.com', 'cbsnews.com',
}


def domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    return netloc


def _matches(domain: str, registry: set) -> bool:
    return any(domain == d or domain.endswith('.' + d) for d in registry)


def classify_tier(url: str) -> int:
    domain = domain_of(url)
    labels = set(domain.split('.'))
    if (labels & _TIER1_LABELS
            or domain.endswith(_TIER1_TLD_SUFFIXES)
            or _matches(domain, _TIER1_DOMAINS)):
        return 1
    if _matches(domain, _TIER2_DOMAINS):
        return 2
    if _matches(domain, _TIER3_DOMAINS):
        return 3
    return 4
