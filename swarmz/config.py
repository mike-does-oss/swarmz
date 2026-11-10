"""Runtime settings from env (.env) and config/*.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Settings:
    db_path: Path
    reports_dir: Path
    agent_model: str
    elicit_model: str
    mutate_model: str
    lesson_model: str
    population_size: int
    discussion_rounds: int = 5   # rounds the panel talks after the opening forecasts
    challengers: int = 2         # members per question told to argue against the leading view
    learning: dict = field(default_factory=dict)
    training_cutoffs: dict = field(default_factory=dict)


def _yaml(name: str) -> dict:
    p = ROOT / "config" / name
    return yaml.safe_load(p.read_text()) if p.exists() else {}


@lru_cache
def settings() -> Settings:
    default_model = os.environ.get("SWARMZ_AGENT_MODEL", "claude-haiku-5-5")
    return Settings(
        db_path=Path(os.environ.get("SWARMZ_DB_PATH", ROOT / "data" / "swarmz.duckdb")),
        reports_dir=Path(os.environ.get("SWARMZ_REPORTS_DIR", ROOT / "reports")),
        agent_model=default_model,
        elicit_model=os.environ.get("SWARMZ_ELICIT_MODEL", default_model),
        mutate_model=os.environ.get("SWARMZ_MUTATE_MODEL", "claude-sonnet-5-5"),
        lesson_model=os.environ.get("SWARMZ_LESSON_MODEL", default_model),
        population_size=int(os.environ.get("SWARMZ_POPULATION_SIZE", "10")),
        discussion_rounds=int(os.environ.get("SWARMZ_DISCUSSION_ROUNDS", "5")),
        challengers=int(os.environ.get("SWARMZ_CHALLENGERS", "2")),
        learning=_yaml("learning.yaml"),
        training_cutoffs={k: str(v) for k, v in (_yaml("models.yaml").get("training_cutoffs") or {}).items()},
    )
