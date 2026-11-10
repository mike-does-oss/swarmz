from app.services.brief_v2.odds_terms import find_odds_contamination


def test_flags_odds_and_market_price_contamination():
    assert find_odds_contamination("The betting odds were 2/1.")
    assert find_odds_contamination("Polymarket price implied 60%.")
    assert find_odds_contamination("It was trading at 62 cents on the exchange.")
    assert find_odds_contamination("The moneyline favored the incumbent.")
    assert find_odds_contamination("Implied probability from the book was 55%.")


def test_ignores_bare_market_name_in_resolution_source():
    # A resolution source may legitimately name the market; that alone is not contamination.
    assert find_odds_contamination("Resolves YES per the Kalshi settlement rules.") == []
    assert find_odds_contamination("This question is listed on Polymarket.") == []


def test_ignores_clean_brief_and_odds_ratio():
    assert find_odds_contamination("Reuters reported the vote passed. Polls show 52%.") == []
    assert find_odds_contamination("The odds ratio of re-election was 1.5 historically.") == []


def test_returns_all_matches():
    matches = find_odds_contamination("Betting odds 3/1. The moneyline moved.")
    assert len(matches) >= 2


def test_market_name_with_nonfinancial_verb_not_flagged():
    assert find_odds_contamination("Kalshi puts pressure on regulators over listing rules.") == []
    assert find_odds_contamination("Polymarket faced scrutiny from the CFTC.") == []
