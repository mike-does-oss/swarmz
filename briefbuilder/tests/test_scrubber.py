from app.services.brief_v2.scrubber import Removal, ScrubResult, build_prompt, scrub


class FakeLLM:
    def __init__(self, payload):
        self._payload = payload

    def chat_json(self, messages, max_tokens=None):
        return self._payload


BRIEF = "The poll shows 52%. Betting odds put it at 2/1. Base rate is 40%."


def test_build_prompt_mentions_remove_keep_and_rewrite_flag():
    joined = " ".join(m["content"] for m in build_prompt(BRIEF)).lower()
    assert "odds" in joined
    assert "poll" in joined or "base rate" in joined  # KEEP items named
    assert "[scrubbed-rewrite]" in joined
    assert BRIEF in " ".join(m["content"] for m in build_prompt(BRIEF))


def test_scrub_parses_cleaned_and_removals():
    payload = {
        "cleaned_brief": "The poll shows 52%. Base rate is 40%.",
        "removals": [
            {"quoted_text": "Betting odds put it at 2/1.", "rule": "odds string", "action": "delete"},
        ],
    }
    result = scrub(BRIEF, FakeLLM(payload))
    assert isinstance(result, ScrubResult)
    assert result.cleaned_brief == "The poll shows 52%. Base rate is 40%."
    assert len(result.removals) == 1
    assert result.removals[0] == Removal("Betting odds put it at 2/1.", "odds string", "delete")
    assert result.degraded is False


def test_scrub_falls_back_to_original_on_malformed_response():
    result = scrub(BRIEF, FakeLLM("not a dict"))
    assert result.cleaned_brief == BRIEF
    assert result.removals == []
    assert result.degraded is True


def test_scrub_degraded_when_cleaned_brief_missing():
    result = scrub(BRIEF, FakeLLM({"removals": []}))  # no cleaned_brief key
    assert result.cleaned_brief == BRIEF
    assert result.degraded is True


def test_scrub_skips_non_dict_removal_entries():
    payload = {"cleaned_brief": "clean.", "removals": [{"quoted_text": "x", "rule": "r", "action": "delete"}, "junk"]}
    result = scrub(BRIEF, FakeLLM(payload))
    assert len(result.removals) == 1
