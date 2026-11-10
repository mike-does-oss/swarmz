"""FileBackend: the brief's cast of people and organisations, kept as one JSON file.

The swarm needs one thing from the brief besides its text: who is in it. MiroFish turns those
people and organisations into agents (arm A1) and drafts the opening posts from them. A graph
database did that job at about $0.19 and 215 seconds per question. Here one model call reads the
whole brief and writes the cast to a file; MiroFish reads it through the same adapter
(``app.memory_compat``), so no simulation code changes.

What is lost compared with the graph: search is by shared words, not by meaning, and whatever
agents say during a run is stored as plain text, not broken into facts. Nothing in a file comes
from anywhere but the frozen brief, so the freeze guarantee is unchanged.

The cast is deliberately as wide as the graph's was: sources the brief cites (news sites, data
providers) are members too, as they were when the graph picked them. Telling the model to leave
out sources the brief could not verify was tried on 2026-11-10 and left two cast members on a
data question (oil price, CPI), which MiroFish then pads with 13 copies of them. Narrowing the
cast is a change to arm A1, the control, and should be made on purpose.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backend import SHARED_GROUP, Entity, Episode, Fact, MemoryBackend, SearchResult

VERSION = 1

# MiroFish's profile generator writes a personal profile for the first group and an
# organisation's account for the second. Any other type is treated as an organisation.
INDIVIDUAL_TYPES = ("Person", "PublicFigure", "Expert", "Official", "Journalist")
GROUP_TYPES = ("Organization", "GovernmentAgency", "Company", "MediaOutlet", "Institution", "Group", "Community")
TYPES = INDIVIDUAL_TYPES + GROUP_TYPES
_TYPE_BY_KEY = {t.lower(): t for t in TYPES}
MAX_ENTITIES = 30
MAX_FACTS = 80

SYSTEM = (
    "You read the research brief written for one forecasting question and list its cast: the people "
    "and organisations the brief names that act in, decide, or publicly comment on the matter. They "
    "will become the participants in a discussion of the question.\n"
    "Rules:\n"
    "- Use only what the brief says. Add nothing from your own knowledge.\n"
    "- A cast member is a named person or a named organisation. Prices, indices, data series, events, "
    "places and dates are not cast members.\n"
    f"- type is exactly one of: {', '.join(TYPES)}.\n"
    "- summary: one to three sentences from the brief on who they are, what they have said or done, "
    "and where they stand on the question if the brief says.\n"
    "- facts: single sentences from the brief that link two cast members, or say something specific "
    "about one. subject and object are cast names exactly as you listed them; object may be null. "
    "relation is a short UPPER_SNAKE_CASE verb phrase.\n"
    f"- List every cast member the brief supports, at most {MAX_ENTITIES}, most central first. "
    f"At most {MAX_FACTS} facts.\n"
    'Return JSON: {"entities": [{"name": "...", "type": "...", "summary": "..."}], '
    '"facts": [{"subject": "...", "object": "..." or null, "relation": "...", "fact": "..."}]}'
)

_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the their this to "
                  "was were what when which who will with would".split())


def cast_dir() -> Path:
    from ..config import settings
    return settings().db_path.parent / "casts"


def extract_cast(llm, model: str, brief_text: str, question: str) -> dict:
    """One model call: the frozen brief in, its cast out. Raises if the brief yields nobody."""
    user = f"QUESTION: {question}\n\nRESEARCH BRIEF:\n{brief_text}"
    raw = llm.json(SYSTEM, user, model=model, max_tokens=8000)
    cast = clean_cast(raw)
    if not cast["entities"]:
        raise RuntimeError("no people or organisations found in the brief")
    return cast


def clean_cast(raw: Any) -> dict:
    """Keep what is usable from the model's answer: named entities with a known type, and facts
    whose subject is in the cast. Ids are derived from names, so the same answer gives the same file."""
    raw = raw if isinstance(raw, dict) else {}
    entities, by_key = [], {}
    for e in raw.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = _clean(e.get("name"))
        key = name.lower()
        if not name or key in by_key or len(entities) >= MAX_ENTITIES:
            continue
        etype = _TYPE_BY_KEY.get(re.sub(r"[^a-z]", "", str(e.get("type") or "").lower()), "Organization")
        ent = {"uuid": _uid("entity", key), "name": name, "type": etype, "summary": _clean(e.get("summary"))}
        by_key[key] = ent
        entities.append(ent)
    facts, seen = [], set()
    for f in raw.get("facts") or []:
        if not isinstance(f, dict):
            continue
        text = _clean(f.get("fact"))
        subject = by_key.get(_clean(f.get("subject")).lower())
        target = by_key.get(_clean(f.get("object")).lower())
        if not text or subject is None or text.lower() in seen or len(facts) >= MAX_FACTS:
            continue
        seen.add(text.lower())
        relation = re.sub(r"[^A-Z0-9]+", "_", str(f.get("relation") or "RELATES_TO").upper()).strip("_")
        facts.append({"uuid": _uid("fact", text.lower()), "fact": text, "relation": relation or "RELATES_TO",
                      "source_uuid": subject["uuid"], "target_uuid": target["uuid"] if target else None})
    return {"entities": entities, "facts": facts}


def load_cast(path: Path) -> dict | None:
    try:
        cast = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(cast, dict) and cast.get("version") == VERSION and cast.get("entities")
    return cast if ok else None


def save_cast(path: Path, cast: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cast, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)   # a reader never sees a half-written file


class FileBackend(MemoryBackend):
    """The cast of one question's brief. ``path`` is shared by every arm of the question."""

    def __init__(self, graph_name: str, freeze_ts: datetime, path: Path | None = None,
                 cast: dict | None = None):
        super().__init__(graph_name, freeze_ts)
        self.path = Path(path) if path else cast_dir() / f"{graph_name}.json"
        self._entities: dict[str, Entity] = {}
        self._facts: list[Fact] = []
        self._fact_group: dict[str, str] = {}
        self.ontology: dict | None = None
        if cast:
            self._load(cast)

    # -- the cast -----------------------------------------------------------
    def load_or_build(self, brief_text: str, question: str, *, llm, model: str) -> str:
        """Read this question's cast file, or make it. Returns "read" or "built"."""
        cast = load_cast(self.path)
        how = "read"
        if cast is None:
            cast = {"version": VERSION, "question": question, "model": model,
                    "brief_sha256": hashlib.sha256(brief_text.encode("utf-8")).hexdigest(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    **extract_cast(llm, model, brief_text, question)}
            save_cast(self.path, cast)
            how = "built"
        self._load(cast)
        return how

    def _load(self, cast: dict) -> None:
        self._entities = {
            e["uuid"]: Entity(uuid=e["uuid"], name=e["name"], labels=["Entity", e["type"]],
                              summary=e.get("summary") or "", group_id=SHARED_GROUP)
            for e in cast.get("entities") or []}
        self._facts, self._fact_group = [], {}
        for f in cast.get("facts") or []:
            self._add_fact(Fact(uuid=f["uuid"], fact=f["fact"], source_uuid=f.get("source_uuid"),
                                target_uuid=f.get("target_uuid"), name=f.get("relation") or "RELATES_TO"),
                           SHARED_GROUP)

    def _add_fact(self, fact: Fact, group_id: str) -> None:
        fact.group_id = group_id  # type: ignore[attr-defined]  (the adapter reads it off the fact)
        self._facts.append(fact)
        self._fact_group[fact.uuid] = group_id

    def set_ontology(self, ontology: dict | None) -> None:
        self.ontology = ontology   # kept for the adapter; the cast's types are fixed above

    # -- writes ---------------------------------------------------------------
    def build_seed_graph(self, chunks: list[str], ontology: dict | None, reference_time: datetime) -> None:
        """MiroFish's own graph build route (overlapping chunks of the brief). swarmz does not use
        it: ``load_or_build`` reads the brief whole."""
        self.check_reference_time(reference_time)
        if self._entities:
            return
        from ..config import settings
        from ..llm import default_llm
        self.load_or_build("\n\n".join(chunks), "", llm=default_llm(), model=settings().agent_model)

    def add_episodes(self, episodes: list[Episode]) -> None:
        """What agents do in a run, kept as plain text for this run only (never written to the file)."""
        for ep in episodes:
            self.check_reference_time(ep.reference_time)
        for ep in episodes:
            body = _clean(ep.body)
            if body:
                self._add_fact(Fact(uuid=_uid("episode", f"{ep.group_id}|{ep.name}|{len(self._facts)}"),
                                    fact=body, name="SAID_OR_DID", valid_at=ep.reference_time), ep.group_id)

    # -- reads ----------------------------------------------------------------
    def search(self, query: str, group_ids: list[str] | None = None, limit: int = 10,
               scope: str = "edges") -> SearchResult:
        q = _words(query)
        out = SearchResult()
        if scope in ("edges", "both"):
            out.facts = _best(self.list_facts(group_ids), lambda f: len(q & _words(f.fact)), limit)
        if scope in ("nodes", "both"):   # a word in the name counts double
            out.entities = _best(self.list_entities(group_ids),
                                 lambda e: 2 * len(q & _words(e.name)) + len(q & _words(e.summary)), limit)
        return out

    def list_entities(self, group_ids: list[str] | None = None) -> list[Entity]:
        return [e for e in self._entities.values() if group_ids is None or e.group_id in group_ids]

    def list_facts(self, group_ids: list[str] | None = None) -> list[Fact]:
        return [f for f in self._facts if group_ids is None or self._fact_group[f.uuid] in group_ids]

    def get_entity(self, uuid: str) -> Entity | None:
        return self._entities.get(uuid)

    def entity_facts(self, uuid: str) -> list[Fact]:
        return [f for f in self._facts if uuid in (f.source_uuid, f.target_uuid)]

    # -- lifecycle ------------------------------------------------------------
    def close(self) -> None:
        pass

    def delete_graph(self) -> None:
        """Forget this run's copy. The cast file stays: the question's other arms read it."""
        self._entities, self._facts, self._fact_group = {}, [], {}


def _best(items: list, score, limit: int) -> list:
    """Items sharing the most words with the query, most first; ties keep their order."""
    scored = [(score(x), i, x) for i, x in enumerate(items)]
    return [x for n, _, x in sorted((s for s in scored if s[0]), key=lambda s: (-s[0], s[1]))[:limit]]


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 1 and w not in _STOP}


def _clean(x: Any) -> str:
    return " ".join(str(x or "").split())


def _uid(kind: str, key: str) -> str:
    return hashlib.sha256(f"{kind}|{key}".encode("utf-8")).hexdigest()[:32]
