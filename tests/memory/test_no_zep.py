"""No zep_cloud import may remain in the vendored MiroFish app or scripts."""

import re
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "mirofish"
IMPORT_RE = re.compile(r"^\s*(from\s+zep_cloud\b|import\s+zep_cloud\b)", re.M)


def test_no_zep_cloud_imports():
    offenders = []
    for sub in ("app", "scripts"):
        for py in (VENDOR / sub).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for m in IMPORT_RE.finditer(text):
                offenders.append(f"{py.relative_to(VENDOR)}: {m.group(0).strip()}")
            # generated code text (ontology_generator) must not reference zep_cloud either
            if "'from zep_cloud" in text or '"from zep_cloud' in text:
                offenders.append(f"{py.relative_to(VENDOR)}: emits zep_cloud import text")
    assert not offenders, offenders


def test_zep_cloud_not_a_dependency():
    for name in ("pyproject.toml", "requirements.txt"):
        text = (VENDOR / name).read_text(encoding="utf-8")
        deps = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
        assert not any("zep-cloud" in ln for ln in deps), name
