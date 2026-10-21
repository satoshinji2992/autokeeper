"""《追书人》's fights on the encounter engine: the ghoul that is Douglas fights as Douglas, the horde's
first sight is paid by its event alone, and running from it ends as the module says."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import paths
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter import views
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterStart, EncounterTurnPlan
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Module

from .encounter_kit import arrange, attack, fight_game, play


@pytest.fixture
def module() -> Module:
    m = Module.load(paths.MODULES / "zhuishuren")
    m.endings = []
    return m


async def test_the_ghoul_sheet_is_douglas_and_its_sight_is_the_events_to_pay(module, fake):
    module.pictures["creature-douglas_ghoul"] = "ghoul.png"
    game, _cast, events = fight_game(module, fake, location="mudi")
    start = EncounterStart(mode="fight", foes=["douglas_ghoul"], pace=6, reason="撞见了偷书贼")
    assert await game._encounter_open(start, Turn())
    enc = game.state.encounter
    assert [p.id for p in enc.participants][1:] == ["douglas"]  # one person, one life: the NPC's id
    assert not any(e["type"] == "sanity" for e in events)  # its first sight is paid by the module's events (Event.sights)
    fighters = views.fight_view(game)["fighters"]
    assert fighters[1]["name"] == "犬脸的佝偻身影"  # not met yet: what they see of him
    assert fighters[1]["portrait"] == "creature-douglas_ghoul" and fighters[1]["id"] != "douglas"  # the ghoul's face, no id of his
    text = views.keeper_text(game)
    assert "道格拉斯·金博尔（玩家所见：犬脸的佝偻身影）" in text and "枪伤减半" in text and "模组写明：调查员逃跑时它不追" in text


async def test_running_from_the_horde_ends_as_the_module_says(module, fake):
    horde = next(e for e in module.events if e.id == "e_horde")
    module.events = [horde]
    game, cast, events = fight_game(module, fake, location="mudi", arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("ghouls", attack(game.state.investigator.id, n=3), let_go=[])]))
    game.state.flags.append("recognized")
    fake.queue(10)  # the event's own sanity roll holds: the only one
    await game._fire(horde, Turn())
    enc = game.state.encounter
    assert enc is not None and sum(e["type"] == "sanity" for e in events) == 1
    inv = game.state.investigator.id
    assert game.state.sightings[inv] == ["ghouls"]
    cast.modes["pursuit"] = lambda *a: None  # nobody decides: no pursuit, and it is said
    cast.modes["arrange"] = lambda *a: EncounterTurnPlan(npcs=[arrange("ghouls", attack(inv, n=3))])
    app = turn_graph.build(game, InMemorySaver())
    _out, _asked = await play(app, "逃跑｜被攻击时闪避")
    assert "fled_ghouls" in game.state.flags and "taken_by_ghouls" not in game.state.flags
    assert game.state.encounter_log[-1].ended.how == "fled"


async def test_the_end_of_a_fight_can_set_off_the_next_one_and_it_begins_cleanly(module, fake):
    """The review: a fight's own stake set off e_unmasked → e_recognized → e_horde, whose fight was refused
    while the ended one was still the encounter going on, and the turn broke for good."""
    from autokeeper.module.schema import Stake

    from .encounter_kit import begin

    keep = {"e_unmasked", "e_horde"}
    module.events = [e for e in module.events if e.id in keep]
    game, _cast, _events = fight_game(module, fake, location="mudi", arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("douglas", DeclaredAction(do="defend", summary="护着书"))]))
    await begin(game, ["douglas_ghoul"], npcs=[arrange("douglas", DeclaredAction(do="defend", summary="护着书"))],
                stakes=[Stake(when="ended:won", flags=["douglas_down", "recognized"])])
    game.world.rec.entities["douglas"].life.hp = 1
    first = game.state.encounter.id
    me = game.state.investigator.id
    app = turn_graph.build(game, InMemorySaver())
    label = next(o.label for o in __import__("autokeeper.engine.encounter.choices", fromlist=["x"]).options(game, me)
                 if o.label.startswith("攻击 犬脸的佝偻身影（徒手）"))
    fake.queue(1, 99, 10, 10)  # a critical punch, his answer fails; e_unmasked's and e_horde's sanity rolls hold
    out, _asked = await play(app, label)
    st = game.state
    assert {"douglas_down", "unmasked", "recognized", "horde_arrived"} <= set(st.flags)
    assert st.encounter_log[-1].id == first and st.encounter_log[-1].ended.how == "won"
    assert st.encounter is not None and st.encounter.id != first and [p.id for p in st.encounter.participants][-1] == "ghouls"
    kinds = [e["type"] for e in out]
    assert kinds.index("fight_end") < len(kinds) - 1 - kinds[::-1].index("fight_start")  # the horde's after the end
