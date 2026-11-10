"""Loaded automatically by a child Python (the brief builder) when this directory is on PYTHONPATH.

Appends one JSON line per Anthropic/OpenAI call to $SWARMZ_USAGE_FILE; the
parent reads the file afterwards and adds it to its usage (swarmz.usage.merge_file).
"""

import json
import os
import threading

_path = os.environ.get("SWARMZ_USAGE_FILE")
_stage = os.environ.get("SWARMZ_USAGE_STAGE", "brief")

if _path:
    try:
        from _usage_core import install

        _lock = threading.Lock()

        def _sink(rec):
            rec = dict(rec, stage=_stage)
            with _lock, open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

        install(_sink)
    except Exception:  # never break the child over usage capture
        pass
