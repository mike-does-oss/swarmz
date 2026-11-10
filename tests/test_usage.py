"""Token usage capture, pricing, storage and the subprocess hook."""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from swarmz import usage
from swarmz.scoring.report import cost_by_arm, cost_by_stage
from swarmz.usage_hook import _usage_core as core


class FakeMessages:
    def __init__(self):
        self._client = NS(base_url="https://api.anthropic.com/v1/")

    def create(self, **kw):
        return NS(model=kw["model"], usage=NS(input_tokens=1000, output_tokens=200,
                                              cache_read_input_tokens=50, cache_creation_input_tokens=10))


class FakeAsyncEmbeddings:
    def __init__(self):
        self._client = NS(base_url="https://api.openai.com/v1/")

    async def create(self, **kw):
        return NS(model=kw["model"], usage=NS(prompt_tokens=300, total_tokens=300))


def _call_as(module_name: str, fn, **kw):
    """Call fn from code whose __name__ is module_name, to exercise stage detection."""
    g = {"__name__": module_name, "fn": fn, "kw": kw}
    exec("result = fn(**kw)", g)
    return g["result"]


def test_wrap_records_tokens_stage_and_host():
    recs = []
    m = FakeMessages()
    wrapped = core.wrap(FakeMessages.create, "anthropic", "chat", recs.append)
    _call_as("swarmz.capture.elicit", lambda **kw: wrapped(m, **kw), model="claude-haiku-5-5")
    assert recs == [{"provider": "anthropic", "host": "api.anthropic.com", "kind": "chat", "stage": "elicit",
                     "model": "claude-haiku-5-5", "calls": 1, "input_tokens": 1000, "output_tokens": 200,
                     "cache_read_tokens": 50, "cache_write_tokens": 10}]


def test_async_wrap_and_stage():
    recs = []
    e = FakeAsyncEmbeddings()
    wrapped = core.wrap(FakeAsyncEmbeddings.create, "openai", "embedding", recs.append)
    _call_as("swarmz.learning.lessons",
             lambda **kw: asyncio.run(wrapped(e, **kw)), model="text-embedding-3-small")
    assert recs[0]["stage"] == "learning" and recs[0]["input_tokens"] == 300 and recs[0]["output_tokens"] == 0


def test_nested_wrapped_calls_count_once():
    recs = []
    m = FakeMessages()
    inner = core.wrap(FakeMessages.create, "anthropic", "chat", recs.append)
    outer = core.wrap(lambda self, **kw: inner(self, **kw), "anthropic", "chat", recs.append)
    outer(m, model="claude-haiku-5-5")
    assert len(recs) == 1


def test_openai_chat_usage_splits_cached_prompt_tokens():
    u = NS(prompt_tokens=1000, completion_tokens=100, prompt_tokens_details=NS(cached_tokens=400))
    assert core.normalise("openai", "chat", u) == {"input_tokens": 600, "output_tokens": 100,
                                                    "cache_read_tokens": 400, "cache_write_tokens": 0}


def test_cost_uses_pricing_table():
    counts = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert usage.cost("claude-haiku-5-5", counts) == pytest.approx(0.10 + 0.50)
    assert usage.cost("claude-sonnet-5-5", counts) == pytest.approx(2.0 + 10.0)
    assert usage.cost("some-unknown-model", counts) is None


def test_tracker_save_and_report(con):
    t = usage.Tracker()
    for stage in ("elicit", "elicit", "discussion"):
        t.add({"stage": stage, "provider": "anthropic", "host": "api.anthropic.com", "model": "claude-haiku-5-5",
               "input_tokens": 1_000_000, "output_tokens": 0})
    t.add({"stage": "brief", "provider": "openai", "host": "api.openai.com", "model": "mystery-embedder",
           "input_tokens": 10})
    # no runs row needed: the report LEFT JOINs runs
    total = usage.save(con, t, context="run", run_id="r1", question_id="q1", arm="A2")
    assert total == pytest.approx(0.30) and t.unpriced == ["mystery-embedder"]
    (row,) = cost_by_arm(con)
    assert row["arm"] == "A2" and row["runs"] == 1 and row["total_usd"] == pytest.approx(0.30) and row["has_unpriced"]
    stages = {(r["stage"], r["model"]): r["calls"] for r in cost_by_stage(con)}
    assert stages[("elicit", "claude-haiku-5-5")] == 2


def test_track_only_collects_inside_block():
    with usage.track() as t:
        usage._sink({"stage": "x", "provider": "p", "model": "m", "input_tokens": 5})
    usage._sink({"stage": "x", "provider": "p", "model": "m", "input_tokens": 7})  # no tracker: dropped
    assert [r["input_tokens"] for r in t.rows()] == [5]


def test_merge_file_and_subprocess_env(tmp_path):
    f = tmp_path / "usage.jsonl"
    f.write_text(json.dumps({"stage": "brief", "provider": "openai", "host": "api.anthropic.com",
                             "model": "claude-haiku-5-5", "calls": 1, "input_tokens": 100,
                             "output_tokens": 10}) + "\nnot json\n")
    with usage.track() as t:
        assert usage.merge_file(f) == 1
        assert usage.merge_file(tmp_path / "missing.jsonl") == 0
    assert t.rows()[0]["stage"] == "brief"
    env = usage.subprocess_env(f)
    assert env["SWARMZ_USAGE_FILE"] == str(f) and env["SWARMZ_USAGE_STAGE"] == "brief"
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(usage.HOOK_DIR)


from swarmz.picker import BRIEF_BUILDER_PYTHON

CHILD_PY = str(BRIEF_BUILDER_PYTHON)   # the hook itself is stdlib only; the child script needs the openai SDK

CHILD = r"""
import asyncio, httpx, openai
def handler(request):
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 0, "model": "claude-haiku-5-5",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127}})
c = openai.OpenAI(api_key="x", base_url="https://api.anthropic.com/v1/",
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))
c.chat.completions.create(model="claude-haiku-5-5", messages=[{"role": "user", "content": "hi"}])

async def handler_async(request):
    return handler(request)
ac = openai.AsyncOpenAI(api_key="x", base_url="https://api.anthropic.com/v1/",
                        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler_async)))
asyncio.run(ac.chat.completions.create(model="claude-haiku-5-5", messages=[{"role": "user", "content": "hi"}]))
"""


@pytest.mark.skipif(not Path(CHILD_PY).exists(), reason="brief builder Python (BRIEF_BUILDER_PYTHON) not available")
def test_subprocess_hook_logs_calls(tmp_path):
    f = tmp_path / "usage.jsonl"
    env = {**os.environ, **usage.subprocess_env(f)}
    subprocess.run([CHILD_PY, "-c", CHILD], env=env, check=True, timeout=120, cwd=tmp_path)
    recs = [json.loads(line) for line in f.read_text().splitlines()]
    assert len(recs) == 2, recs   # one sync call, one async call
    for rec in recs:
        assert rec["stage"] == "brief" and rec["input_tokens"] == 120 and rec["output_tokens"] == 7
        assert rec["host"] == "api.anthropic.com" and rec["model"] == "claude-haiku-5-5"


def test_decorated_async_methods_are_recorded_after_await():
    """SDK async methods wrapped in a plain decorator look sync but return a coroutine."""
    recs = []
    m = FakeMessages()

    async def _real(self, **kw):
        return FakeMessages.create(self, **kw)

    def decorated(self, **kw):          # like @required_args around an async def
        return _real(self, **kw)

    wrapped = core.wrap(decorated, "anthropic", "chat", recs.append)
    out = _call_as("swarmz.panel",
                   lambda **kw: asyncio.run(wrapped(m, **kw)), model="claude-haiku-5-5")
    assert out.model == "claude-haiku-5-5"
    assert len(recs) == 1 and recs[0]["stage"] == "discussion" and recs[0]["input_tokens"] == 1000


def test_reprice_fills_missing_costs(con):
    t = usage.Tracker()
    t.add({"stage": "brief", "provider": "openai", "model": "claude-sonnet-5", "input_tokens": 1_000_000})
    usage.save(con, t, context="brief")
    con.execute("UPDATE usage SET cost_usd = NULL")
    assert usage.reprice(con) == 1
    assert con.execute("SELECT cost_usd FROM usage").fetchone()[0] == pytest.approx(2.0)
