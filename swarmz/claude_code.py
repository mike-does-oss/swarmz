"""Model calls made through Claude Code instead of the API, so swarmz can run on a Claude subscription with no API key.

Each call starts `claude -p` (Claude Code's non-interactive mode) with everything that belongs to a
coding session switched off: no tools, no connectors, no skills, no settings, no project files. Left
on, those add hundreds of thousands of tokens to every call (measured: 312,000 against 662 with them
off), and a run makes about 75 calls.

It is slower than the API and it spends the subscription's usage allowance, so a few calls run at a
time (SWARMZ_CLAUDE_CODE_PARALLEL, default 4) and a call that is refused is tried again after a wait.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

MARK = "subscription/"            # usage rows for these calls carry this prefix on the model: counted, not priced
_slots = threading.BoundedSemaphore(max(1, int(os.environ.get("SWARMZ_CLAUDE_CODE_PARALLEL", "4"))))


class ClaudeCodeError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which(os.environ.get("SWARMZ_CLAUDE_BIN", "claude")) is not None


def _empty_dir() -> str:
    """A folder with nothing in it to start Claude Code from, so it finds no project to read."""
    d = Path(tempfile.gettempdir()) / "swarmz-claude-code"
    d.mkdir(exist_ok=True)
    return str(d)


def command(system: str, model: str, tools: tuple[str, ...] = ()) -> list[str]:
    cmd = [os.environ.get("SWARMZ_CLAUDE_BIN", "claude"), "-p", "--output-format", "json", "--model", model,
           "--system-prompt", system, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
           "--tools", ",".join(tools)]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    return cmd


def _record(model: str, usage: dict) -> None:
    """Count the call's tokens the way API calls are counted: in the active tracker, or the child-process usage file."""
    rec = {"provider": "claude-code", "host": "claude-code", "kind": "print", "model": MARK + model, "calls": 1,
           "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
           "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
           "cache_write_tokens": usage.get("cache_creation_input_tokens", 0)}
    try:
        path = os.environ.get("SWARMZ_USAGE_FILE")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({**rec, "stage": os.environ.get("SWARMZ_USAGE_STAGE", "brief")}) + "\n")
        else:
            from . import usage as usage_mod
            from .usage_hook._usage_core import stage_from_stack
            usage_mod._sink({**rec, "stage": stage_from_stack(skip=3)})
    except Exception:   # counting must never break a call
        pass


def ask(system: str, user: str, *, model: str, tools: tuple[str, ...] = (), timeout: float = 600,
        tries: int = 4, run=subprocess.run) -> str:
    """One question, one answer. The prompt goes in on standard input, so its length is not limited by the command line."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}   # use the login, not a key
    last = "no attempt made"
    for attempt in range(tries):
        with _slots:
            try:
                proc = run(command(system, model, tools), input=user, text=True, capture_output=True,
                           timeout=timeout, cwd=_empty_dir(), env=env)
            except FileNotFoundError:
                raise ClaudeCodeError("Claude Code is not installed: the `claude` command was not found") from None
            except subprocess.TimeoutExpired:
                last = f"no answer within {timeout:.0f}s"
                continue
        try:
            out = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            out = None
        if out and not out.get("is_error") and isinstance(out.get("result"), str):
            _record(model, out.get("usage") or {})
            return out["result"]
        last = (out or {}).get("result") or proc.stderr.strip()[-300:] or proc.stdout.strip()[-300:] or f"exit {proc.returncode}"
        if "log in" in str(last).lower() or "/login" in str(last).lower():
            raise ClaudeCodeError("Claude Code is not signed in: run `claude` once and sign in, then try again")
        time.sleep(min(60, 5 * 2 ** attempt))      # most refusals are usage limits: wait, then ask again
    raise ClaudeCodeError(f"Claude Code gave no usable answer after {tries} tries: {str(last)[:300]}")
