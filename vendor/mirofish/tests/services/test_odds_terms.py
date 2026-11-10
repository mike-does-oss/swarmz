from app.services.brief_v2.odds_terms import is_odds_query


def test_flags_odds_and_market_queries():
    assert is_odds_query("election betting odds 2026")
    assert is_odds_query("polymarket price for the nominee")
    assert is_odds_query("kalshi market implied probability")
    assert is_odds_query("bookmaker line on the match")
    assert is_odds_query("moneyline for the game")


def test_allows_substantive_research_queries():
    assert not is_odds_query("candidate X polling average trend")
    assert not is_odds_query("central bank interest rate decision July 2026")
    assert not is_odds_query("historical base rate of incumbent re-election")


def test_allows_odds_ratio_statistics_term():
    assert not is_odds_query("odds ratio of incumbent re-election historically")
    assert not is_odds_query("case-control study odds ratio for vaccine efficacy")


def test_still_flags_betting_odds_and_two_word_moneyline():
    assert is_odds_query("betting odds for the race")
    assert is_odds_query("the odds on the election")
    assert is_odds_query("money line for the game")
