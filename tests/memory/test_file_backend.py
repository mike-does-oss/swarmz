"""FileBackend: the brief's cast as a JSON file, read by MiroFish through the same adapter."""

import json
from datetime import timedelta

import pytest

from swarmz.memory import SHARED_GROUP, Episode, FreezeViolation, agent_group
from swarmz.memory.file_backend import FileBackend, clean_cast, extract_cast, load_cast

ANSWER = {
    "entities": [
        {"name": "Victoria Rodríguez", "type": "Official",
         "summary": "Governor of the Bank of Mexico. Said further cuts depend on core inflation."},
        {"name": "Bank of Mexico", "type": "GovernmentAgency", "summary": "Sets the overnight rate, now 7.25%."},
        {"name": "Citi Mexico", "type": "company", "summary": "Its survey of analysts expects a 25 point cut."},
        {"name": "bank of mexico", "type": "Organization", "summary": "the same body again"},
        {"name": "Core inflation", "type": "Indicator", "summary": "not an actor, but named"},
        {"name": "", "type": "Person", "summary": "nameless"},
    ],
    "facts": [
        {"subject": "Victoria Rodríguez", "object": "Bank of Mexico", "relation": "leads",
         "fact": "Victoria Rodríguez is governor of the Bank of Mexico."},
        {"subject": "Citi Mexico", "object": None, "relation": "expects a cut",
         "fact": "Citi Mexico's survey expects a 25 point cut in November."},
        {"subject": "Someone Else", "object": "Bank of Mexico", "relation": "X", "fact": "Unknown subject."},
        {"subject": "Citi Mexico", "object": None, "relation": "expects a cut",
         "fact": "Citi Mexico's survey expects a 25 point cut in November."},
    ],
}


class OneAnswer:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def json(self, system, user, *, model, max_tokens=1024, temperature=None):
        self.calls.append((system, user, model))
        return self.answer


def test_clean_cast_keeps_named_typed_entities_and_facts_about_them():
    cast = clean_cast(ANSWER)
    assert [(e["name"], e["type"]) for e in cast["entities"]] == [
        ("Victoria Rodríguez", "Official"), ("Bank of Mexico", "GovernmentAgency"),
        ("Citi Mexico", "Company"),            # type matched whatever its case
        ("Core inflation", "Organization")]    # a type MiroFish does not know becomes an organisation
    by_name = {e["name"]: e["uuid"] for e in cast["entities"]}
    assert [(f["relation"], f["source_uuid"], f["target_uuid"]) for f in cast["facts"]] == [
        ("LEADS", by_name["Victoria Rodríguez"], by_name["Bank of Mexico"]),
        ("EXPECTS_A_CUT", by_name["Citi Mexico"], None)]   # unknown subject and the repeat are dropped
    assert clean_cast(ANSWER) == cast                      # ids come from names, not from chance
    assert clean_cast(["not", "a", "dict"]) == {"entities": [], "facts": []}


def test_a_brief_with_nobody_in_it_fails_loudly():
    with pytest.raises(RuntimeError, match="no people or organisations"):
        extract_cast(OneAnswer({"entities": []}), "m", "brief", "Q?")


def test_cast_is_built_with_one_call_then_read_from_the_file(tmp_path, freeze_ts):
    path = tmp_path / "casts" / "q_X__brief__abc.json"
    llm = OneAnswer(ANSWER)
    first = FileBackend("q_X__A1__r1", freeze_ts, path=path)
    assert first.load_or_build("the brief text", "Will the bank cut?", llm=llm, model="haiku") == "built"
    assert len(llm.calls) == 1 and "the brief text" in llm.calls[0][1] and "Will the bank cut?" in llm.calls[0][1]
    saved = json.loads(path.read_text())
    assert saved["model"] == "haiku" and len(saved["brief_sha256"]) == 64 and len(saved["entities"]) == 4

    second = FileBackend("q_X__A2__r2", freeze_ts, path=path)      # the question's next arm
    assert second.load_or_build("the brief text", "Will the bank cut?", llm=llm, model="haiku") == "read"
    assert len(llm.calls) == 1
    assert [e.uuid for e in second.list_entities()] == [e.uuid for e in first.list_entities()]

    path.write_text("{ half a file")                               # unreadable: build it again
    assert load_cast(path) is None
    assert FileBackend("q_X__A3__r3", freeze_ts, path=path).load_or_build("b", "Q", llm=llm, model="m") == "built"
    assert len(llm.calls) == 2


def test_search_ranks_by_shared_words(freeze_ts):
    b = FileBackend("g", freeze_ts, cast={**clean_cast(ANSWER)})
    facts = b.search("survey expects a cut in November", limit=5).facts
    assert facts[0].fact.startswith("Citi Mexico's survey") and len(facts) == 1
    both = b.search("Bank of Mexico governor", scope="both", limit=1)
    assert [e.name for e in both.entities] == ["Bank of Mexico"]   # a word in the name counts double
    assert both.facts[0].name == "LEADS"
    assert b.search("nothing matches zebra").facts == []
    gov = b.list_entities()[0]
    assert b.get_entity(gov.uuid).labels == ["Entity", "Official"]
    assert [f.name for f in b.entity_facts(gov.uuid)] == ["LEADS"]


def test_what_agents_say_stays_in_the_run_and_out_of_the_file(tmp_path, freeze_ts):
    path = tmp_path / "cast.json"
    b = FileBackend("g", freeze_ts, path=path)
    b.load_or_build("brief", "Q", llm=OneAnswer(ANSWER), model="m")
    before = path.read_text()
    ref = freeze_ts - timedelta(minutes=5)
    b.add_episodes([Episode("r1", "Dana: I doubt the cut happens.", agent_group(0), ref),
                    Episode("r1", "Eli: the survey says cut.", agent_group(1), ref)])
    assert [f.fact for f in b.search("cut", group_ids=[agent_group(0)]).facts] == ["Dana: I doubt the cut happens."]
    assert len(b.list_facts([SHARED_GROUP])) == 2 and len(b.list_facts()) == 4
    assert path.read_text() == before
    with pytest.raises(FreezeViolation):
        b.add_episodes([Episode("late", "x", agent_group(0), freeze_ts + timedelta(seconds=1))])
    b.delete_graph()
    assert b.list_entities() == [] and path.exists()               # other arms still read the file


def test_mirofish_reads_the_cast_through_the_adapter(vendor_app, freeze_ts):
    from app.services.zep_entity_reader import ZepEntityReader
    from app.services.zep_tools import ZepToolsService

    gid = "q_file__A1__r1"
    vendor_app.register_backend(gid, FileBackend(gid, freeze_ts, cast=clean_cast(ANSWER)))
    filtered = ZepEntityReader().filter_defined_entities(gid, enrich_with_edges=True)
    by_name = {e.name: e for e in filtered.entities}
    assert set(by_name) == {"Victoria Rodríguez", "Bank of Mexico", "Citi Mexico", "Core inflation"}
    assert by_name["Victoria Rodríguez"].get_entity_type() == "Official"
    assert [n["name"] for n in by_name["Victoria Rodríguez"].related_nodes] == ["Bank of Mexico"]
    assert by_name["Bank of Mexico"].related_edges[0]["direction"] == "incoming"
    res = ZepToolsService(llm_client=object()).search_graph(gid, "survey cut November", limit=5)
    assert res.facts == ["Citi Mexico's survey expects a 25 point cut in November."]
