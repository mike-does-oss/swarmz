from app.services.brief_v2.source_tier import domain_of, classify_tier


def test_domain_of_strips_www_and_lowercases():
    assert domain_of("https://WWW.Reuters.com/world/x") == "reuters.com"
    assert domain_of("http://un.org/a") == "un.org"


def test_tier1_official_primary():
    assert classify_tier("https://www.whitehouse.gov/briefing") == 1
    assert classify_tier("https://sec.gov/filing") == 1
    assert classify_tier("https://defense.mil/news") == 1
    assert classify_tier("https://who.int/report") == 1
    assert classify_tier("https://un.org/press") == 1
    assert classify_tier("https://ecb.europa.eu/note") == 1


def test_tier2_wire():
    assert classify_tier("https://www.reuters.com/world/x") == 2
    assert classify_tier("https://apnews.com/article/y") == 2
    assert classify_tier("https://feeds.reuters.com/z") == 2  # subdomain


def test_tier3_major_outlet():
    assert classify_tier("https://www.nytimes.com/2026/07/x.html") == 3
    assert classify_tier("https://www.bbc.co.uk/news/x") == 3
    assert classify_tier("https://www.theguardian.com/x") == 3


def test_tier4_other_default():
    assert classify_tier("https://some-random-blog.example/x") == 4
    assert classify_tier("https://medium.com/@someone/x") == 4


def test_tier1_country_government_domains():
    assert classify_tier("https://www.gov.uk/guidance") == 1
    assert classify_tier("https://news.gov.hk/eng/x") == 1
    assert classify_tier("https://www.gouv.fr/x") == 1


def test_tier1_does_not_misfire_on_gov_substring():
    assert classify_tier("https://mygov.com/x") == 4
    assert classify_tier("https://govtech.example/x") == 4
