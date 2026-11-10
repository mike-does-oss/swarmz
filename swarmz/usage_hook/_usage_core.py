"""Token-usage capture for Anthropic and OpenAI SDK calls. Stdlib only.

This file is imported two ways:
  * in-process as ``swarmz.usage_hook._usage_core`` (Python 3.12), and
  * inside the OASIS subprocess (Python 3.11, no swarmz installed) through
    ``sitecustomize.py`` in this same directory, which sits on PYTHONPATH.
So it must not import anything from swarmz.

``install(sink)`` wraps the SDK methods that return a ``usage`` object; each
call becomes one record dict passed to ``sink``. The stage (memory, setup,
elicit, ...) is read from the calling stack, which works across threads and
asyncio tasks.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import sys

_inside = contextvars.ContextVar("swarmz_usage_inside", default=False)
_installed: set = set()

# innermost matching caller module wins
STAGES = [
    ("graphiti_core", "memory"),
    ("swarmz.memory", "memory"),
    ("app.memory_compat", "memory"),
    ("app.services.prediction.graph_from_text", "memory"),
    ("app.services.prediction", "report"),
    ("app.services.report_agent", "report"),
    ("app.services.zep_tools", "report"),
    ("app.services", "setup"),
    ("swarmz.capture", "elicit"),
    ("swarmz.panel", "discussion"),
    ("swarmz.seedposts", "setup"),
    ("swarmz.learning", "learning"),
    ("camel", "simulation"),
    ("oasis", "simulation"),
]


def stage_from_stack(skip: int = 2) -> str:
    try:
        f = sys._getframe(skip)
    except ValueError:
        return "other"
    while f is not None:
        name = f.f_globals.get("__name__", "")
        for prefix, stage in STAGES:
            if name == prefix or name.startswith(prefix + "."):
                return stage
        f = f.f_back
    return "other"


def _get(obj, *names, default=0):
    for n in names:
        if obj is None:
            return default
        obj = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
    return default if obj is None else obj


def normalise(provider: str, kind: str, usage) -> dict:
    """Map an SDK usage object to input/output/cache token counts."""
    if usage is None:
        return {}
    if provider == "anthropic":
        return {"input_tokens": int(_get(usage, "input_tokens")),
                "output_tokens": int(_get(usage, "output_tokens")),
                "cache_read_tokens": int(_get(usage, "cache_read_input_tokens")),
                "cache_write_tokens": int(_get(usage, "cache_creation_input_tokens"))}
    if kind == "embedding":
        return {"input_tokens": int(_get(usage, "prompt_tokens") or _get(usage, "total_tokens")),
                "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    cached = int(_get(usage, "prompt_tokens_details", "cached_tokens"))
    prompt = int(_get(usage, "prompt_tokens"))
    return {"input_tokens": max(prompt - cached, 0),
            "output_tokens": int(_get(usage, "completion_tokens")),
            "cache_read_tokens": cached, "cache_write_tokens": 0}


def _host(sdk_self) -> str:
    client = getattr(sdk_self, "_client", None)
    url = str(getattr(client, "base_url", "") or "")
    return url.split("://", 1)[-1].split("/", 1)[0]


def _record(sink, provider, kind, sdk_self, kwargs, result, stage):
    if kwargs.get("stream"):
        return  # streamed responses carry usage in events; nothing here streams today
    usage = getattr(result, "usage", None)
    if usage is None and hasattr(result, "parse"):  # with_raw_response
        try:
            usage = getattr(result.parse(), "usage", None)
        except Exception:
            usage = None
    tokens = normalise(provider, kind, usage)
    if not tokens:
        return
    model = getattr(result, "model", None) or kwargs.get("model") or "unknown"
    sink({"provider": provider, "host": _host(sdk_self), "kind": kind, "stage": stage,
          "model": str(model), "calls": 1, **tokens})


def wrap(fn, provider: str, kind: str, sink):
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapper(self, *args, **kwargs):
            if _inside.get():
                return await fn(self, *args, **kwargs)
            stage = stage_from_stack()
            token = _inside.set(True)
            try:
                result = await fn(self, *args, **kwargs)
            finally:
                _inside.reset(token)
            try:
                _record(sink, provider, kind, self, kwargs, result, stage)
            except Exception:
                pass  # usage capture must never break a call
            return result
        awrapper._swarmz_usage = True
        return awrapper

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if _inside.get():
            return fn(self, *args, **kwargs)
        stage = stage_from_stack()
        token = _inside.set(True)
        try:
            result = fn(self, *args, **kwargs)
        finally:
            _inside.reset(token)
        if inspect.isawaitable(result):
            # SDK async methods are often wrapped by a plain decorator (e.g. @required_args),
            # so they look sync but return a coroutine: record once it has actually run.
            return _record_when_done(result, sink, provider, kind, self, kwargs, stage)
        try:
            _record(sink, provider, kind, self, kwargs, result, stage)
        except Exception:
            pass
        return result
    wrapper._swarmz_usage = True
    return wrapper


async def _record_when_done(awaitable, sink, provider, kind, sdk_self, kwargs, stage):
    result = await awaitable
    try:
        _record(sink, provider, kind, sdk_self, kwargs, result, stage)
    except Exception:
        pass
    return result


TARGETS = [
    ("anthropic.resources.messages", "Messages", "create", "anthropic", "chat"),
    ("anthropic.resources.messages", "AsyncMessages", "create", "anthropic", "chat"),
    ("openai.resources.chat.completions", "Completions", "create", "openai", "chat"),
    ("openai.resources.chat.completions", "AsyncCompletions", "create", "openai", "chat"),
    ("openai.resources.chat.completions", "Completions", "parse", "openai", "chat"),
    ("openai.resources.chat.completions", "AsyncCompletions", "parse", "openai", "chat"),
    ("openai.resources.beta.chat.completions", "Completions", "parse", "openai", "chat"),
    ("openai.resources.beta.chat.completions", "AsyncCompletions", "parse", "openai", "chat"),
    ("openai.resources.embeddings", "Embeddings", "create", "openai", "embedding"),
    ("openai.resources.embeddings", "AsyncEmbeddings", "create", "openai", "embedding"),
]


def install(sink) -> list[str]:
    """Wrap every SDK method in TARGETS that exists. Idempotent. Returns what was wrapped."""
    import importlib

    done = []
    for mod_name, cls_name, meth, provider, kind in TARGETS:
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
        except Exception:
            continue
        fn = cls.__dict__.get(meth)
        if fn is None or getattr(fn, "_swarmz_usage", False):
            continue
        setattr(cls, meth, wrap(fn, provider, kind, sink))
        done.append(f"{mod_name}.{cls_name}.{meth}")
        _installed.add((mod_name, cls_name, meth))
    return done
