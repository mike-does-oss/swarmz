"""Persona injection: registry persona -> agent profile text for one run."""

from __future__ import annotations

from datetime import datetime

from ..registry import asof


def lessons_block(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = "\n".join(f"- {l['text']}" for l in lessons)
    return f"\n\nLessons from your past forecasts (habits, not facts):\n{lines}"


def build_profile(con, persona: dict, freeze_ts: datetime, with_lessons: bool) -> tuple[str, list[str]]:
    """Returns (profile text, lesson ids used). Lessons come only from asof.py with as_of = freeze_ts."""
    lessons = asof.lessons_for(con, persona["persona_id"], freeze_ts) if with_lessons else []
    return persona["base_prompt"] + lessons_block(lessons), [l["lesson_id"] for l in lessons]
