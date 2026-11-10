"""Model calls through Claude Code: the command it builds, what it does with the answer, and how it is chosen."""
import json
import subprocess

import pytest

from swarmz import claude_code, llm, usage


def fake(answers):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        a = answers[min(len(calls), len(answers)) - 1]
        return subprocess.CompletedProcess(cmd, 0, stdout=a if isinstance(a, str) else json.dumps(a), stderr="")
    return run, calls


OK = {"is_error": False, "result": '{"prob": 0.3}', "usage": {"input_tokens": 5, "output_tokens": 7,
                                                              "cache_creation_input_tokens": 600, "cache_read_input_tokens": 9}}


def test_a_call_carries_nothing_from_a_coding_session(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-to-be-used")
    run, calls = fake([OK])
    assert claude_code.ask("be brief", "the question", model="claude-haiku-5-5", run=run) == '{"prob": 0.3}'
    cmd, kw = calls[0]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--tools") + 1] == "" and "--allowedTools" not in cmd          # no tools unless asked for
    assert cmd[cmd.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in cmd and "--disable-slash-commands" in cmd
    assert kw["input"] == "the question"                                                # the prompt goes in on stdin
    assert "ANTHROPIC_API_KEY" not in kw["env"]                                         # the login is used, never a key
    assert not any(p.name not in () for p in __import__("pathlib").Path(kw["cwd"]).iterdir())   # started from an empty folder


def test_tools_are_switched_on_only_by_name():
    cmd = claude_code.command("s", "m", ("WebSearch",))
    assert cmd[cmd.index("--tools") + 1] == "WebSearch" and cmd[cmd.index("--allowedTools") + 1] == "WebSearch"


def test_a_refused_call_is_tried_again_and_a_dead_one_gives_up(monkeypatch):
    monkeypatch.setattr(claude_code.time, "sleep", lambda s: None)
    run, calls = fake([{"is_error": True, "result": "usage limit reached"}, "not json at all", OK])
    assert claude_code.ask("s", "u", model="m", run=run) == '{"prob": 0.3}' and len(calls) == 3
    run, calls = fake([{"is_error": True, "result": "usage limit reached"}])
    with pytest.raises(claude_code.ClaudeCodeError, match="usage limit reached"):
        claude_code.ask("s", "u", model="m", tries=2, run=run)
    run, _ = fake([{"is_error": True, "result": "Invalid API key · Please run /login"}])
    with pytest.raises(claude_code.ClaudeCodeError, match="not signed in"):
        claude_code.ask("s", "u", model="m", run=run)


def test_tokens_are_counted_and_cost_nothing(monkeypatch):
    run, _ = fake([OK])
    monkeypatch.delenv("SWARMZ_USAGE_FILE", raising=False)
    with usage.track() as t:
        claude_code.ask("s", "u", model="claude-haiku-5-5", run=run)
    row = t.rows()[0]
    assert (row["model"], row["calls"], row["input_tokens"], row["output_tokens"], row["cache_write_tokens"]) == \
        ("subscription/claude-haiku-5-5", 1, 5, 7, 600)
    assert row["cost_usd"] == 0.0 and t.cost_usd == 0.0


def test_a_child_process_writes_its_usage_to_the_file(monkeypatch, tmp_path):
    f = tmp_path / "usage.jsonl"
    monkeypatch.setenv("SWARMZ_USAGE_FILE", str(f))
    monkeypatch.setenv("SWARMZ_USAGE_STAGE", "brief")
    run, _ = fake([OK])
    claude_code.ask("s", "u", model="claude-haiku-5-5", run=run)
    rec = json.loads(f.read_text())
    assert (rec["stage"], rec["provider"], rec["output_tokens"]) == ("brief", "claude-code", 7)


def test_the_backend_is_the_api_with_a_key_and_claude_code_without(monkeypatch):
    monkeypatch.delenv("SWARMZ_LLM", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert llm.backend() == "api"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(claude_code, "available", lambda: True)
    assert llm.backend() == "claude-code"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("SWARMZ_LLM", "claude-code")
    assert llm.backend() == "claude-code"                      # asked for by name, it wins over a key
    monkeypatch.delenv("SWARMZ_LLM")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(claude_code, "available", lambda: False)
    with pytest.raises(RuntimeError, match="install Claude Code"):
        llm.backend()


def test_the_llm_asks_again_when_the_answer_is_not_json(monkeypatch):
    answers = iter(["Sure! Here you go", '{"prob": 0.4}'])
    monkeypatch.setattr(claude_code, "ask", lambda system, user, **kw: next(answers))
    assert llm.ClaudeCodeLLM().json("s", "u", model="m") == {"prob": 0.4}
