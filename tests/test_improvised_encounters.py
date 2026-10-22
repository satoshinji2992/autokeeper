"""Director additions use the same presence, profiles and outcome rules as compiled scenes."""

from __future__ import annotations

import pytest

from autokeeper.agents.director import Improv, NewEvent, NewFlag, NewLocation, NewNPC
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module import improv
from autokeeper.module.schema import Leg, Module, Stake
from autokeeper.paths import EXAMPLE
from autokeeper.world.ops import Improvise
from autokeeper.world.record import KEEPER
from autokeeper.world.store import World

from .encounter_kit import Keeper, fight_game
from .test_harness import harness_sandbox  # noqa: F401


def scene(*, chase: bool = False):
    return Improv(kind="展开", reason="调查员拦下冒名顶替的车夫", flags=[NewFlag(id="driver_talked", meaning="车夫接受谈和")],
                  npcs=[NewNPC(id="driver", name="亨利", role="车夫", look="穿油污外套", location="square",
                               called="穿油污外套的车夫", stats={"敏捷": 70, "生命": 14, "格斗(斗殴)": 55},
                               attacks=["格斗(斗殴) 55%，伤害 1D3+DB"])],
                  events=[NewEvent(id="driver_arrives", title="车夫现身", when="now", read_aloud="车夫推门走了进来。",
                                   present=["driver"], chase=["driver"] if chase else [],
                                   fight=[] if chase else ["driver"], surprise="" if chase else "party",
                                   chase_party="追" if chase else "", stakes=[Stake(when="ended:parley", flags=["driver_talked"])])])


@pytest.mark.parametrize("ident,name", [("player", "新来的人"), ("betty_copy", "贝蒂·康西丁"),
                                       ("betty_copy", "贝蒂 康西丁")])
def test_new_npc_cannot_duplicate_a_live_player_identity(ident, name):
    module = Module.load(EXAMPLE)
    player = module.pregens[0].model_copy(update={"id": "player", "name": "贝蒂·康西丁"}, deep=True)
    state = Game.new(module, [player], human=[player.id])
    world = World(module, state)
    plan = Improv(kind="展开", reason="现场登记", npcs=[NewNPC(id=ident, name=name, role="同伴", location=state.location)])
    before = set(world.rec.entities)
    result = world.apply(Improvise(plan=plan.model_dump(mode="json"), actor=KEEPER))
    assert not result.ok
    assert set(world.rec.entities) == before and not state.improv


def test_unselected_pregen_does_not_lose_the_ability_to_appear_as_an_npc():
    module = Module.load(EXAMPLE)
    npc = NewNPC(id="other_investigator", name="贝蒂·康西丁", role="受邀人", location="square")
    module.pregens.append(module.pregens[0].model_copy(update={"id": "betty", "name": npc.name}, deep=True))
    plan = Improv(kind="展开", reason="别的受邀人来访", npcs=[npc])
    assert not improv.check(module, plan, at="square", actors={module.pregens[0].id})


@pytest.mark.parametrize("chase", [False, True])
async def test_improvised_event_starts_a_real_encounter_with_the_added_character_and_settles_outcome(fake, chase):
    module = Module.load(EXAMPLE)
    module.events = []
    game, _, _ = fight_game(module, fake)
    plan = scene(chase=chase)
    assert not improv.check(module, plan, at="tower")
    assert await game._land("展开", plan) is not None
    assert game.world.place("driver") == "square"
    event = next(e for e in game.m.events if e.id == "driver_arrives")
    if chase:
        fake.queue(40, 40)  # speed checks for the investigator and the driver
    await game._fire(event, Turn())
    enc = game.encounter()
    assert enc is not None and enc.mode == ("chase" if chase else "fight")
    assert game.world.place("driver") == "tower" and enc.get("driver") is not None
    assert game.world.entity("driver").life.hp == 14
    assert game.m.npc("driver").stats["格斗(斗殴)"] == 55
    assert game.m.npc("driver").called == "穿油污外套的车夫"
    assert enc.stakes[0].flags == ["driver_talked"]
    await game._encounter_end("parley", Turn())
    assert "driver_talked" in game.state.flags and game.encounter() is None
    assert game.state.encounter_log[-1].ended.how == "parley"
    await game.close()


def test_location_rules_routes_and_distances_survive_the_improv_format():
    module = Module.load(EXAMPLE)
    plan = Improv(kind="展开", reason="调查员进入钟楼隔壁的门房", locations=[NewLocation(
        id="lodge", name="门房", read_aloud="一间狭小的门房。", keeper="推门前需过聆听；失败惊动门后的人。",
        exits=["tower"], travel_minutes={"tower": 0}, chase=[Leg(id="door", name="门房外窄道", exit=True)],
    )])
    assert not improv.check(module, plan, at="tower")
    improv.apply(module, plan)
    loc = module.location("lodge")
    assert "失败惊动" in loc.keeper and loc.travel_minutes == {"tower": 0} and loc.chase[0].exit
    replay = Module.load(EXAMPLE)
    improv.apply(replay, Improv.model_validate_json(plan.model_dump_json()))
    assert replay.location("lodge").model_dump() == loc.model_dump()


@pytest.mark.parametrize("changes,expected", [
    ({"present": ["absent"]}, "出场人物"),
    ({"fight": ["absent"]}, "遭遇对象"),
    ({"chase": ["driver"]}, "同时开始"),
    ({"stakes": [Stake(when="ended:parley", flags=["undeclared"])]}, "结果标记"),
    ({"stakes": [Stake(when="dead:absent", flags=["driver_talked"])]}, "结果条件"),
    ({"sights": ["absent"]}, "恐怖照面"),
])
def test_improvised_encounter_references_are_checked_before_publication(changes, expected):
    module = Module.load(EXAMPLE)
    plan = scene()
    plan.events[0] = plan.events[0].model_copy(update=changes)
    assert any(expected in problem for problem in improv.check(module, plan, at="tower"))


async def test_event_presence_and_encounter_survive_current_format_reload(fake):
    module = Module.load(EXAMPLE)
    module.events = []
    game, _, _ = fight_game(module, fake)
    assert await game._land("展开", scene()) is not None
    await game._fire(next(e for e in game.m.events if e.id == "driver_arrives"), Turn())
    saved = GameState.model_validate_json(game.state.model_dump_json())
    # Use the same constructor path that replays saved improvisations.
    from autokeeper.engine.game import Game
    from autokeeper.rules.coc import Rules

    resumed = Game(Module.load(EXAMPLE), saved, Keeper(), Rules(dice=fake))
    assert resumed.world.place("driver") == "tower" and resumed.encounter().get("driver") is not None
    assert next(e for e in resumed.m.events if e.id == "driver_arrives").present == ["driver"]
    assert resumed.m.npc("driver").stats["生命"] == 14
    await game.close()
    await resumed.close()


async def test_npc_intentions_are_not_reapplied_when_the_reply_resumes_after_display_failure(fake):
    from autokeeper.agents.cast import Action, Ruling

    from .test_harness import SCRIPT, SEEN
    from .test_npc_acts import setup

    game, _ = setup(fake)
    SCRIPT.append({"text": "我去找帮手。", "intentions": {"goals": ["找帮手"], "reason": "听从劝告",
                                                       "steps": [{"id": "get_help", "text": "找帮手"}]}})
    turn = await game.resolve(Action(kind="talk", npc="warden", summary="劝他找帮手", speech="找些帮手吧。"),
                              Ruling(needs_roll=False))
    saved = {}

    def interrupted(value):
        saved.update(state=game.state.model_dump_json(), turn=TURN_RECORD.dump_json(value))
        raise ConnectionError("显示前断线")

    game.turn_checkpoint = interrupted
    with pytest.raises(ConnectionError):
        await game.talk(turn)
    count = len([entry for entry in game.world.rec.journal if entry.kind == "plan"])
    assert game.world.agent("warden").goals == ["找帮手"] and len(SEEN) == 1
    await game.close()
    state = GameState.model_validate_json(saved["state"])
    from autokeeper import config
    from autokeeper.agents.cast import Cast
    from autokeeper.engine.game import Game
    from autokeeper.rules.coc import Rules

    from .test_harness import SCRIPTED

    module = Module.load(EXAMPLE)
    again = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    resumed = Game(module, state, again, Rules(dice=fake))
    await resumed.talk(TURN_RECORD.validate_json(saved["turn"]))
    assert len(SEEN) == 1 and len([entry for entry in resumed.world.rec.journal if entry.kind == "plan"]) == count
    assert resumed.world.agent("warden").plan[-1].status == "pending"
    await resumed.close()
