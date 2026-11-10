"""The brief builder is its own top-level package (``app``), started by swarmz as a child process from this folder."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BRIEF_LOG_DIR", tempfile.mkdtemp(prefix="briefbuilder-logs-"))   # the builder logs to files


@pytest.fixture
def data_dir(tmp_path):
    """A fresh, isolated data directory for storage tests."""
    d = tmp_path / "data"
    d.mkdir()
    return d
