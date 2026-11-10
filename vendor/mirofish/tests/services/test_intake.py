from app.services.brief_v2.intake import QuestionPackage, from_kalshi_market, map_category


def test_map_category_canonicalizes():
    assert map_category("Politics") == "politics"
    assert map_category("Economics") == "econ"
    assert map_category("Financials") == "econ"
    assert map_category("Sports") == "sports"
    assert map_category("World") == "geopolitics"
    assert map_category("Science and Technology") == "other"
    assert map_category(None) == "other"


def test_from_kalshi_market_maps_core_fields():
    market = {
        "ticker": "KXPRES-24",
        "title": "Will candidate X win?",
        "rules_primary": "Resolves YES if X wins.",
        "rules_secondary": "Source: AP call.",
        "category": "Politics",
        "close_time": "2026-11-05T00:00:00Z",
        "event_ticker": "KXPRES",
    }
    pkg = from_kalshi_market(market)
    assert pkg.question_id == "KXPRES-24"
    assert pkg.question_text == "Will candidate X win?"
    assert pkg.resolution_criteria == "Resolves YES if X wins.\n\nSource: AP call."
    assert pkg.category == "politics"
    assert pkg.close_ts == "2026-11-05T00:00:00Z"
    assert pkg.event_ticker == "KXPRES"
    assert pkg.raw_market["ticker"] == "KXPRES-24"


def test_from_kalshi_market_without_secondary_rules():
    pkg = from_kalshi_market({
        "ticker": "T1", "title": "Q?", "rules_primary": "Only primary.",
        "category": "Sports", "close_time": "2026-07-04T00:00:00Z",
    })
    assert pkg.resolution_criteria == "Only primary."
    assert pkg.category == "sports"


def test_to_dict_has_expected_keys():
    pkg = from_kalshi_market({"ticker": "T1", "title": "Q?", "rules_primary": "R",
                              "category": "X", "close_time": "t"})
    d = pkg.to_dict()
    assert set(d.keys()) == {
        "question_id", "question_text", "resolution_criteria", "category",
        "close_ts", "edge_cases", "event_ticker", "raw_market",
    }


def test_raw_market_is_decoupled_from_caller_mutation():
    market = {"ticker": "T1", "title": "Q?", "rules_primary": "R",
              "category": "Sports", "close_time": "t"}
    pkg = from_kalshi_market(market)
    market["title"] = "MUTATED"
    assert pkg.raw_market["title"] == "Q?"
