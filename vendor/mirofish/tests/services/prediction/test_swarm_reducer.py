from app.services.prediction.swarm_reducer import normalize_outcome, reduce_to_yes_prob


def test_normalize_outcome_maps_synonyms():
    assert normalize_outcome("YES") == "yes"
    assert normalize_outcome("Bullish") == "yes"
    assert normalize_outcome("no") == "no"
    assert normalize_outcome("bearish") == "no"
    assert normalize_outcome("neutral") == "uncertain"
    assert normalize_outcome("") == "uncertain"


def test_reduce_pure_binary():
    report = {"scenarios": [
        {"probability": 0.7, "implied_outcome": "yes"},
        {"probability": 0.3, "implied_outcome": "no"},
    ]}
    assert reduce_to_yes_prob(report) == 0.7


def test_reduce_renormalizes_over_yes_no_only():
    # uncertain mass is excluded from the binary
    report = {"scenarios": [
        {"probability": 0.5, "implied_outcome": "bullish"},   # yes
        {"probability": 0.3, "implied_outcome": "bearish"},   # no
        {"probability": 0.2, "implied_outcome": "neutral"},   # excluded
    ]}
    # 0.5 / (0.5 + 0.3) = 0.625
    assert round(reduce_to_yes_prob(report), 4) == 0.625


def test_reduce_none_when_no_binary_mass():
    report = {"scenarios": [{"probability": 1.0, "implied_outcome": "neutral"}]}
    assert reduce_to_yes_prob(report) is None
    assert reduce_to_yes_prob({"scenarios": []}) is None
    assert reduce_to_yes_prob({}) is None
