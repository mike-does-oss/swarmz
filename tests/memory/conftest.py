import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "mirofish"

FREEZE = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def freeze_ts():
    return FREEZE


@pytest.fixture
def vendor_app(tmp_path, monkeypatch):
    """Import vendor/mirofish's `app` package with data dirs pointed at tmp."""
    monkeypatch.setenv("UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from app import memory_compat

    memory_compat.reset()
    memory_compat.set_backend_factory(None)
    yield memory_compat
    memory_compat.reset()


@pytest.fixture
def lite_cfg(tmp_path_factory):
    from swarmz.memory.falkor import FalkorConfig

    # One embedded server per test session (startup ~1-2 s).
    path = os.environ.get("SWARMZ_TEST_LITE_PATH") or str(tmp_path_factory.getbasetemp() / "falkor_lite")
    return FalkorConfig(mode="lite", lite_path=path)
