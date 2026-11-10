"""Thin LLM layer. Everything in swarmz calls ``LLM.json(...)``; tests swap in a fake."""

from __future__ import annotations

import json
import swarmz.config  # noqa: F401  (loads .env)
import os
import re
from typing import Protocol


class LLM(Protocol):
    def json(self, system: str, user: str, *, model: str, max_tokens: int = 1024,
             temperature: float | None = None) -> dict | list: ...


class LLMOutputError(ValueError):
    pass


def parse_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", t, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    raise LLMOutputError(f"model did not return JSON: {text[:200]!r}")


class AnthropicLLM:
    def __init__(self, api_key: str | None = None):
        import anthropic
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic(api_key=key, max_retries=4)

    def json(self, system, user, *, model, max_tokens=1024, temperature=None):
        # Newer models may think before answering; leave headroom beyond the answer itself.
        kwargs = dict(model=model, max_tokens=max_tokens + 4000, system=system + "\nReturn JSON only, no prose.",
                      messages=[{"role": "user", "content": user}])
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            msg = self.client.messages.create(**kwargs)
        except Exception as e:  # some newer models reject temperature
            if temperature is not None and "temperature" in str(e).lower():
                kwargs.pop("temperature")
                msg = self.client.messages.create(**kwargs)
            else:
                raise
        if msg.stop_reason == "max_tokens":  # thinking can use up the budget; retry once with more room
            kwargs["max_tokens"] *= 2
            msg = self.client.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return parse_json(text)


class ClaudeCodeLLM:
    """The same calls, made through Claude Code on a Claude subscription. See swarmz.claude_code."""

    def json(self, system, user, *, model, max_tokens=1024, temperature=None):
        from . import claude_code
        text = claude_code.ask(system + "\nReturn JSON only, no prose.", user, model=model)
        try:
            return parse_json(text)
        except LLMOutputError:      # one more try, saying what was wrong
            text = claude_code.ask(system + "\nReturn JSON only, no prose. Your last answer was not valid JSON.", user, model=model)
            return parse_json(text)


def backend() -> str:
    """Where model calls go: "api" (an Anthropic API key) or "claude-code" (a Claude subscription, through Claude Code).
    SWARMZ_LLM picks one; otherwise the API when a key is set, else Claude Code when it is installed."""
    from . import claude_code
    want = os.environ.get("SWARMZ_LLM", "").strip().lower()
    if want in ("api", "claude-code"):
        return want
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if claude_code.available():
        return "claude-code"
    raise RuntimeError("no way to reach a model: set ANTHROPIC_API_KEY, or install Claude Code and sign in "
                       "(then swarmz uses your Claude subscription)")


_default: LLM | None = None


def default_llm() -> LLM:
    global _default
    if _default is None:
        _default = ClaudeCodeLLM() if backend() == "claude-code" else AnthropicLLM()
    return _default


def set_default_llm(llm: LLM | None) -> None:
    global _default
    _default = llm
