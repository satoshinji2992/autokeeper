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
    del module.pictures["creature-douglas_ghoul"]  # not drawn yet: no face at all, never Douglas's own
    assert module.npc("douglas").portrait and views.fight_view(game)["fighters"][1]["portrait"] == ""
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
    _out, _asked = await play(app, "逃跑")
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
                 if o.label.startswith("攻击 犬脸的佝偻身影（徒手，"))
    fake.queue(1, 99, 10, 10)  # a critical punch, his answer fails; e_unmasked's and e_horde's sanity rolls hold
    out, _asked = await play(app, label)
    st = game.state
    assert {"douglas_down", "unmasked", "recognized", "horde_arrived"} <= set(st.flags)
    assert st.encounter_log[-1].id == first and st.encounter_log[-1].ended.how == "won"
    assert st.encounter is not None and st.encounter.id != first and [p.id for p in st.encounter.participants][-1] == "ghouls"
    kinds = [e["type"] for e in out]
    assert kinds.index("fight_end") < len(kinds) - 1 - kinds[::-1].index("fight_start")  # the horde's after the end


# -- the chase ------------------------------------------------------------------------------------------
async def test_douglas_runs_with_the_books_thomas_helps_and_the_stake_fires_by_the_dice(module, fake):
    """The acceptance case: the thief runs for the tomb with the books, the investigator after him and
    Thomas helping (his help a bonus die on the wall); it comes down to the dice, the stake its outcome
    names fires, and the world places follow the run."""
    from autokeeper.engine.encounter.plan import AttackPick
    from autokeeper.module.schema import Stake

    from .encounter_kit import begin  # noqa: F401  (the kit the fights use)

    module.events = []
    douglas = arrange("douglas", DeclaredAction(do="move", to="low_tunnel", summary="道格拉斯抱着书往陵墓跑"), react="dodge",
                      goal="带书逃回地下")
    thomas = arrange("thomas", DeclaredAction(do="help", target="brennan", summary="托马斯托着布伦南翻墙"), react="dodge",
                     goal="帮调查员抓贼")
    game, cast, _events = fight_game(module, fake, location="jinboer_jia", arrange=lambda *a: EncounterTurnPlan(npcs=[douglas, thomas]))
    fake.queue(10, 40, 40, 40)  # the first sight of him (no event here pays it), then CON for speed: 布伦南, 道格拉斯, 托马斯
    start = EncounterStart(mode="chase", foes=["douglas_ghoul"], allies=["thomas"], quarry="foe", npcs=[douglas, thomas], pace=15,
                           reason="人影抱着书跑向墓地",
                           stakes=[Stake(when="escaped:douglas", flags=["douglas_escaped"]), Stake(when="down:douglas", flags=["douglas_down"])])
    assert await game._encounter_open(start, Turn())
    enc = game.state.encounter
    assert {p.id: p.side for p in enc.participants} == {"brennan": "party", "douglas": "foe", "thomas": "party"}
    assert {p.id: p.speed for p in enc.participants} == {"brennan": 8, "douglas": 5, "thomas": 8}
    assert enc.chase.quarry == ["douglas"] and enc.chase.pursuers == ["brennan", "thomas"]
    assert {p.id: p.node for p in enc.participants} == {"brennan": "backyard_path", "douglas": "shrubs", "thomas": "backyard_path"}
    app = turn_graph.build(game, InMemorySaver())
    cast.modes["act"] = EncounterTurnPlan(action=DeclaredAction(do="defend", summary="布伦南等托马斯过来搭手"))
    fake.queue(10, 10)  # he is out of sight behind the wall: both follow his trail
    await play(app, "等托马斯过来搭把手")
    enc = game.state.encounter
    assert enc.get("douglas").node == "dirt_road" and game.world.place("douglas") == "mudi"  # into the cemetery: he is there
    assert any(e.source == "thomas" for e in enc.get("brennan").edges)
    fake.queue(15, 30, 10, 10)  # 布伦南 over the wall, through the shrubs; 道格拉斯 over the graves; 托马斯 follows the trail
    out, _ = await play(app, "追赶 犬脸的佝偻身影")
    wall = next(e for e in out if e["type"] == "dice")
    assert wall["bonus"] == 1  # Thomas's help on the wall
    enc = game.state.encounter
    assert enc.get("brennan").node == "dirt_road" and enc.get("douglas").node == "graves"
    game.world.rec.entities["douglas"].life.hp = 1
    strike = DeclaredAction(do="pursue", target="douglas", attacks=[AttackPick(target="douglas")], summary="布伦南追上去扑倒他")
    cast.modes["act"] = EncounterTurnPlan(action=strike)
    fake.queue(5, 10, 90).queue_totals(2, 1)  # the graves (Thomas helps again); the blow lands, his dodge fails
    await play(app, "追上去把他扑倒")
    st = game.state
    assert "douglas_down" in st.flags and "douglas_escaped" not in st.flags
    assert st.location == "mudi"  # the party is where it caught him
    assert st.encounter is None and st.encounter_log[-1].ended.how == "won"


def test_douglas_as_the_ghoul_keeps_his_own_skills_for_a_chase(module):
    """The review: the ghoul sheet that is Douglas has no 跳跃 or 攀爬, so in a chase he jumped and climbed
    at DEX/2 (an ordinary creature's), not his own 75 and 85. The form's sheet stands over the NPC's: his
    claws, HP and dodge are the ghoul's, what it leaves out is his."""
    from autokeeper.engine.encounter import profiles
    from autokeeper.engine.encounter.actions import Ctx
    from autokeeper.engine.encounter.chase import value
    from autokeeper.engine.encounter.resolve import Work

    from .test_engine import make

    game, _, _ = make(module, None, {}, location="mudi")
    p = profiles.profile(game.world, "douglas_ghoul")
    assert p is not None and p.id == "douglas" and (p.skill("跳跃"), p.skill("攀爬"), p.skill("侦查")) == (75, 85, 50)
    assert p.skill("闪避") == 30 and p.hp_max == 12 and [a.name for a in p.attacks] == ["爪击", "撕咬"] and p.chars["智力"] == 80
    game.state.encounter = None
    start = EncounterStart(mode="fight", foes=["douglas_ghoul"], pace=6, reason="x")
    game._begin("e1", start, ["douglas"], Turn(), None)
    w = Work(game, Ctx.of(game), "t")
    assert (value(w, "douglas", "跳跃"), value(w, "douglas", "攀爬")) == (75, 85)  # not 40 // 2
