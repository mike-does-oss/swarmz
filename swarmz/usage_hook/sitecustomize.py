"""Loaded automatically by the OASIS subprocess when this directory is on PYTHONPATH.

Appends one JSON line per Anthropic/OpenAI call to $SWARMZ_USAGE_FILE; the
bridge reads the file after the simulation and adds it to the run's usage.
"""

import json
import os
import threading

_path = os.environ.get("SWARMZ_USAGE_FILE")
_stage = os.environ.get("SWARMZ_USAGE_STAGE", "simulation")

if _path:
    try:
        from _usage_core import install

        _lock = threading.Lock()

        def _sink(rec):
            rec = dict(rec, stage=_stage)
            with _lock, open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

        install(_sink)
    except Exception:  # never break the simulation over usage capture
        pass
