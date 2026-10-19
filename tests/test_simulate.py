"""The playthrough with no model (module/simulate.py): what a module lets a table reach."""

from __future__ import annotations

import pytest

from autokeeper.module.schema import NPC, Clue, Ending, Event, Location, Module
from autokeeper.module.simulate import playthrough, reach
from autokeeper.paths import EXAMPLE


@pytest.fixture
def m() -> Module:
    return Module.load(EXAMPLE)


def test_the_example_is_reachable_end_to_end(m):
    r = reach(m)
    assert r.places == {x.id for x in m.locations} and r.clues == {c.id for c in m.clues}
    assert set(r.endings) == {e.id for e in m.endings}
    run = playthrough(m)
    assert not any(run.unreachable.values()) and run.lean == {"k_motive": 2}


def test_what_nothing_leads_to_is_found(m):
    m.locations.append(Location(id="attic", name="阁楼", read_aloud="", exits=[]))  # no way in
    m.clues.append(Clue(id="c_attic", title="阁楼的箱子", text="……", where="attic", points_to=["k_motive"]))
    m.clues.append(Clue(id="c_after", title="后来才有", text="……", where="square", requires=["c_attic"]))
    m.npcs.append(NPC(id="ghost", name="幽灵", role="哪儿都不在", clues=["c_ghost"]))
    m.clues.append(Clue(id="c_ghost", title="幽灵的话", text="……", where="attic", npc="ghost"))  # nowhere to meet him, nowhere to go
    m.endings.append(Ending(id="nothing", title="什么都没发生", condition="以上都没有发生", text="……"))
    run = playthrough(m)
    assert run.unreachable["地点"] == ["attic"] and set(run.unreachable["线索"]) == {"c_attic", "c_after", "c_ghost"}
    assert run.unreachable["结局"] == ["nothing"]
    assert any("条件不是 flag:标记" in i for i in run.issues)
    m.clues.append(Clue(id="c_seen", title="街角的身影", text="……", where="square", npc="ghost"))
    assert "c_seen" in playthrough(m).reach.clues  # seen where it lies, as the engine has it, though he is nowhere


def test_an_event_opens_what_it_leads_to(m):
    m.locations.append(Location(id="crypt", name="地窖", read_aloud="", exits=[]))
    m.events.append(Event(id="e_fall", title="地板塌了", when="visit:tower", text="", move_to="crypt", sets=["fell"]))
    m.flags["fell"] = "掉进了地窖"
    m.clues.append(Clue(id="c_crypt", title="地窖的刻字", text="……", where="crypt", requires=["flag:fell"]))
    r = reach(m)
    assert "crypt" in r.places and "c_crypt" in r.clues and "fell" in r.flags


def test_a_conclusion_hanging_on_one_hidden_place_or_one_person(m):
    m.conclusions[0].id = "k_one"
    for c in m.clues:
        c.points_to = []
    m.locations.append(Location(id="vault", name="密室", read_aloud="", exits=["square"]))
    m.location("square").exits.append("vault")  # off the square, where the game starts
    m.clues += [Clue(id=f"c_v{i}", title="密室里", text="……", where="vault", points_to=["k_one"]) for i in range(3)]
    run = playthrough(m)
    assert run.fragile["k_one"] == ["地点 vault"] and "k_one" not in run.lean  # three clues, all behind one door
    m.location("vault").public = True  # a place anyone can ask the way to does not count
    assert playthrough(m).fragile["k_one"] == []
    m.location("vault").public = False
    m.clues.append(Clue(id="c_word", title="守钟人的话", text="……", where="tower", npc="warden", points_to=["k_one"]))
    assert playthrough(m).fragile["k_one"] == []  # now another way in: the warden says it
