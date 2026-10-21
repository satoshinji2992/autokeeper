"""The 0.0.4 encounter contracts: the state a fight or a chase keeps, the changes that move it, the
effects on people and things, what a model writes about one, and the module and world additions
(engine/encounter/model.py, plan.py, engine/effects.py, module/schema.py, world/record.py)."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from autokeeper import paths
from autokeeper.engine import effects
from autokeeper.engine.encounter import model as m
from autokeeper.engine.encounter import plan as p
from autokeeper.engine.state import GameState
from autokeeper.harness.models import problems, strict_schema
from autokeeper.module import package
from autokeeper.module.schema import Event, Leg, Method, Module, Obstacle, Stake, Track
from autokeeper.rules import chase as chase_rules
from autokeeper.rules.bcdice import Level
from autokeeper.rules.coc import InjuryState, Investigator
from autokeeper.rules.dice_expr import PATTERN
from autokeeper.world.record import Entity, Life, WeaponState


def _inv(**kw) -> Investigator:
    c = {"力量": 50, "体质": 60, "体型": 55, "敏捷": 65, "外貌": 60, "智力": 75, "意志": 60, "教育": 70}
    return Investigator(id="chen", name="陈墨", characteristics=c, luck=55, **kw)


def _every_effect() -> list:
    return [
        effects.Wound(target="chen", amount=0, means="blow", con=Level.FAILURE, injury="enc1:r1:a0"),
        effects.Wound(target="douglas", amount=7, injury="enc1:r1:a1"),
        effects.Heal(target="chen", amount=1, means="first_aid", injury="enc1:r1:a0"),
        effects.Wake(target="chen"),
        effects.Stabilize(target="chen"),
        effects.Die(target="ghoul", why="一击超过生命上限"),
        effects.Ammo(item="revolver", loaded=0, spare=None),
        effects.Ammo(item="rifle", loaded=5, spare=0),
        effects.Jam(item="revolver", jammed=True),
        effects.Ready(who="chen", item="revolver"),
        effects.Drop(item="revolver", to="kimball_study"),
        effects.Relocate(who="douglas", place="suidao"),
        effects.Mark(investigator="chen", skill="射击(手枪)"),
    ]


def _action(**kw) -> p.DeclaredAction:
    return p.DeclaredAction(**{"do": "attack", "attacks": [p.AttackPick(target="douglas", with_="poker")],
                               "summary": "陈墨挥拨火棍打道格拉斯", **kw})


def _state() -> m.EncounterState:
    """An encounter with every part filled in, zeros and Nones where they mean something."""
    arrangement = p.NpcArrangement(who="douglas", goal="护书撤退", side="foe", actions=[
        p.DeclaredAction(do="flee", to="tunnel", summary="道格拉斯抱着书往地道跑"),
        p.DeclaredAction(do="attack", attacks=[p.AttackPick(target="chen", with_="claw"), p.AttackPick(target="chen", with_="bite")],
                         summary="道格拉斯扑向陈墨"),
    ], react="dodge", say="别过来！", pursue=["chen"], let_go=["thomas"], keep=False)
    node = p.SiteNode(id="graves", name="墓碑间的窄道", place="cemetery", obstacle="wall", seen=True)
    wall = p.ObstacleState(id="wall", kind="barrier", what="矮墙", harm="1D3",
                           methods=[Method(id="climb", label="翻过去", skill="攀爬", opens=False)], state="broken", by="chen")
    site = m.ChaseSite(nodes=[node, p.SiteNode(id="tunnel", name="地道口", exit=True)], edges=[p.SiteEdge(a="graves", b="tunnel")],
                       obstacles={"wall": wall}, origin="graves", source="keeper")
    receipt = m.ActionReceipt(
        id="enc1:r0:chen:0", round=0, actor="chen", source="words", declared=_action(react="fight_back"),
        summary="陈墨挥拨火棍打道格拉斯", ruling={"skill": "格斗(斗殴)"}, phase="committed",
        reactions=[m.ReactionReceipt(attack=0, attacker="chen", defender="douglas", options=["dodge", "fight_back"],
                                     chosen="dodge", by="keeper")],
        rolls=[m.RollRecord(purpose="attack", who="chen", skill="格斗(斗殴)", target=0, value=0, level="失败", bonus=0),
               m.RollRecord(purpose="damage", who="chen", expr="1D8", total=0)],
        effects=_every_effect(), lines=["{chen} 一棍落空"])
    return m.EncounterState(
        id="enc1", mode="chase", place="cemetery", reason="道格拉斯抢了书就跑", started_turn=0,
        participants=[
            m.Participant(id="chen", kind="investigator", side="party", controller="human", speed=0, react="none",
                          ask="always", edges=[m.Edge(dice=0, why="同伴帮忙", source="thomas", until="next_roll")]),
            m.Participant(id="douglas", kind="creature", side="foe", controller="keeper", node="graves",
                          arrangement=arrangement, arranged_round=0, presence="fled"),
        ],
        round=0, order=["douglas", "chen"], cursor=0,
        budget=m.RoundBudget(responses={"chen": 0}, acted=["douglas"], lost_next={"chen": "扑向掩体"}, movement={"chen": 0},
                             owed={"chen": 0}, attacks_made={"douglas": 0}, shots={"chen": 0}),
        scene=m.FightScene(engaged=[m.Engagement(a="chen", b="douglas", since=0)],
                           cover=[m.Cover(who="chen", what="墓碑", since=0)], holds=[m.Hold(held="chen", by="douglas", since=0)],
                           prone=["chen"], distance={"chen|douglas": 0}),
        chase=m.ChaseState(site=site, quarry=["douglas"], pursuers=["chen"], goals={"douglas": "tunnel"},
                           lost_sight=["chen|douglas"], started_round=0),
        receipts=[receipt], history=["第 0 轮：……"],
        pending=m.PendingChoice(id="enc1:r0:douglas:0:react:0", kind="reaction", who="chen", receipt="enc1:r0:douglas:0",
                                prompt="道格拉斯扑过来了", default="dodge", deadline=None,
                                options=[m.ChoiceOption(id="dodge", label="闪避", value={"react": "dodge"})]),
        stakes=[m.StakeState(when="escaped:douglas", flags=["douglas_escaped"], source="module")],
        fills=[p.Fill(who="douglas", key="生命", value=0, why="原文没写")],
        clock=m.EncounterClock(pace=None, elapsed=0),
        changes=[m.Change(round=0, kind="fled", who="douglas")], switches=["r0:chase"],
        ended=m.EndRecord(round=0, how="escaped", per={"douglas": "escaped"}, places={"douglas": "suidao"}, minutes=0),
    )


# -- round trips ----------------------------------------------------------------------------------
def test_encounter_state_round_trips_through_json_with_zeros_and_nones():
    st = _state()
    back = m.EncounterState.model_validate_json(st.model_dump_json())
    assert back == st
    assert back.participants[0].speed == 0 and back.participants[1].speed is None
    assert back.participants[1].arranged_round == 0 and back.participants[0].arranged_round is None
    assert back.participants[0].react == "none" and back.participants[1].react is None
    assert back.receipts[0].rolls[0].target == 0 and back.receipts[0].rolls[1].target is None
    assert back.receipts[0].rolls[1].total == 0
    assert back.clock.pace is None and back.fills[0].value == 0 and back.budget.movement == {"chen": 0}
    assert [type(e) for e in back.receipts[0].effects] == [type(e) for e in _every_effect()]
    assert back.receipts[0].effects[6].loaded == 0 and back.receipts[0].effects[6].spare is None
    assert back.receipts[0].effects[7].spare == 0
    assert back.receipts[0].effects[0].con is Level.FAILURE
    wall = back.chase.site.obstacles["wall"]
    assert wall.state == "broken" and wall.by == "chen" and back.chase.site.nodes[0].seen  # kept, though not in the schema


def test_attack_pick_reads_and_writes_with():
    pick = p.AttackPick.model_validate({"target": "douglas", "with": "poker"})
    assert pick.with_ == "poker" and p.AttackPick(target="a", with_="b").with_ == "b"
    assert pick.model_dump() == {"target": "douglas", "with": "poker", "shots": 1}
    assert "with" in p.AttackPick.model_json_schema()["properties"]


def test_life_weapon_and_injury_round_trip_zero_hp_and_none():
    life = Life(hp=0, hp_max=12, conditions=["濒死"], injury=InjuryState(dying_since=0, knockout=0, injuries=["x"]))
    back = Life.model_validate_json(life.model_dump_json())
    assert back == life and back.hp == 0 and back.injury.dying_since == 0 and back.injury.stabilized_at is None
    gun = WeaponState(kind=".38 左轮手枪", loaded=0, capacity=6, spare=None)
    assert WeaponState.model_validate_json(gun.model_dump_json()) == gun
    assert WeaponState(kind="x").loaded is None  # not known yet, never "empty"
    e = Entity(id="ghoul", kind="creature", name="食尸鬼", life=life, fills={"闪避": 0})
    back_e = Entity.model_validate_json(e.model_dump_json())
    assert back_e == e and back_e.fills == {"闪避": 0}
    assert Entity(id="poker", kind="item", name="拨火棍").life is None


def test_investigator_injury_defaults_empty_and_zero_values_stand():
    inv = _inv(hp=0, san=0)
    assert inv.injury == InjuryState() and inv.hp == 0 and inv.san == 0
    assert "injury" not in inv.model_dump() and "injury" not in json.loads(inv.model_dump_json())  # dumps as before 0.0.4
    inv.injury.dying_since = 0
    assert inv.model_dump()["injury"]["dying_since"] == 0
    back = Investigator.model_validate_json(inv.model_dump_json())
    assert back == inv and back.hp == 0 and back.injury.dying_since == 0
    assert "injury" not in Investigator.model_json_schema()["properties"]  # the rules keep it; no module or model writes it


def test_game_state_saves_and_loads_the_encounter(tmp_path):
    from autokeeper.tui.library import write_save

    module = Module.load(paths.EXAMPLE)
    st = GameState(module=module.id, slot="enc-test", investigators=[_inv(hp=0)], location=module.start)
    st.encounter = _state()
    st.encounter_log = [m.EncounterSummary(id="enc0", mode="fight", place=module.start, started_turn=0, rounds=0,
                                           participants=["chen"], ended=m.EndRecord(round=0, how="won"))]
    st.sightings = {"chen": []}
    st.identified = ["ghoul"]
    st.apart = {"chen": module.start}
    st.record.entities["ghoul"] = Entity(id="ghoul", kind="creature", name="食尸鬼", life=Life(hp=0, hp_max=13),
                                         weapon=None, fills={"生命": 13})
    write_save(st, module)
    back = GameState.model_validate_json(paths.save("enc-test").read_text(encoding="utf-8"))
    assert back == st
    assert back.encounter == st.encounter and back.encounter_log[0].rounds == 0
    assert back.sightings == {"chen": []} and back.record.entities["ghoul"].life.hp == 0
    assert back.investigators[0].hp == 0


def _strip(node, keys: set[str]):
    if isinstance(node, dict):
        return {k: _strip(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_strip(v, keys) for v in node]
    return node


def test_a_save_from_before_the_encounter_loads_unchanged():
    st = GameState(module="example", investigators=[_inv(hp=3)], location="x", seen=["ghoul"])
    st.record.entities["douglas"] = Entity(id="douglas", kind="npc", name="道格拉斯", status="受伤")
    new = {"encounter", "encounter_log", "sightings", "identified", "apart", "injury", "life", "weapon", "fills"}
    old = _strip(json.loads(st.model_dump_json()), new)
    assert not new & set(old) and "injury" not in old["investigators"][0]
    back = GameState.model_validate(old)
    assert back.encounter is None and back.encounter_log == [] and back.sightings == {} and back.apart == {}
    assert back.seen == ["ghoul"] and back.fight is None and back.chase is None  # the old engine's, still read
    assert back.investigators[0].hp == 3 and back.investigators[0].injury == InjuryState()
    assert back.record.entities["douglas"].life is None and back.record.entities["douglas"].status == "受伤"
    assert back == st


# -- modules --------------------------------------------------------------------------------------
def _given_comes_back(given, dumped, where: str = "") -> None:
    """Everything a module's files write is in the loaded module, as written."""
    if isinstance(given, dict):
        assert isinstance(dumped, dict), where
        for k, v in given.items():
            assert k in dumped, f"{where}/{k}"
            _given_comes_back(v, dumped[k], f"{where}/{k}")
    elif isinstance(given, list):
        assert isinstance(dumped, list) and len(given) == len(dumped), where
        for i, (a, b) in enumerate(zip(given, dumped, strict=True)):
            _given_comes_back(a, b, f"{where}/{i}")
    else:
        assert given == dumped, f"{where}: {given!r} != {dumped!r}"


@pytest.mark.parametrize("name", ["zhuishuren", "dongxuezhixia", "fallout3-youxierenyouyanbujin", "example"])
def test_the_bundled_modules_and_the_example_load_unchanged(name):
    folder = paths.EXAMPLE if name == "example" else paths.find_module(name)
    assert folder is not None
    data = package.read(folder)
    module = Module.load(folder)
    _given_comes_back(data, module.model_dump(mode="json"))  # what they write of the new fields: tests/test_fight_module.py


def test_the_chase_way_moved_to_the_module_format_and_is_still_found_in_the_rules():
    assert chase_rules.Leg is Leg and chase_rules.Obstacle is Obstacle and chase_rules.Track is Track
    assert chase_rules.Method is Method
    ob = Obstacle(kind="hazard", what="结冰的台阶", skill="跳跃", harm="1d3")
    assert ob.harm == "1D3"  # its canonical spelling
    kept = Obstacle(kind="hazard", what="碎玻璃", skill="闪避", harm="1D3点")  # an imported module's: still loads
    assert kept.harm == "1D3点"
    assert Track.model_json_schema()["$defs"]["Obstacle"]["properties"]["harm"]["pattern"] == PATTERN
    leg = Leg(id="wall", name="墓园矮墙", place="cemetery", links=["road"], exit=True,
              obstacle=Obstacle(kind="barrier", what="矮墙", skill="攀爬",
                                methods=[Method(id="climb", label="翻过去", skill="攀爬"),
                                         Method(id="force", label="撞开", skill="力量", difficulty="困难", opens=True)]))
    assert Leg.model_validate_json(leg.model_dump_json()) == leg


def test_event_stakes_and_sights():
    ev = Event.model_validate({"id": "e_horde", "title": "尸群", "when": "flag:x", "text": "……", "fight": ["ghouls"],
                               "stakes": [{"when": "escaped:douglas", "flags": ["douglas_escaped"]}], "sights": ["ghouls"]})
    assert ev.stakes == [Stake(when="escaped:douglas", flags=["douglas_escaped"])] and ev.sights == ["ghouls"]
    assert set(Stake.model_json_schema()["properties"]) == {"when", "flags", "note"}
    for atom in p.STAKE_ATOMS:
        assert f"{atom}:" in Stake.model_fields["when"].description


# -- the unions -----------------------------------------------------------------------------------
EFFECTS = TypeAdapter(list[effects.Effect])
CHANGES = TypeAdapter(list[m.EncounterChange])


def test_the_effect_union_reads_each_kind_by_its_tag():
    every = _every_effect()
    assert {e.kind for e in every} == {"wound", "heal", "wake", "stabilize", "die", "ammo", "jam", "ready", "drop", "relocate", "mark"}
    assert EFFECTS.validate_json(EFFECTS.dump_json(every)) == every
    assert isinstance(EFFECTS.validate_python([{"kind": "jam", "item": "g", "jammed": False}])[0], effects.Jam)
    with pytest.raises(ValidationError):
        EFFECTS.validate_python([{"kind": "explode", "target": "chen"}])
    with pytest.raises(ValidationError):
        EFFECTS.validate_python([{"kind": "wound", "target": "chen", "amount": -1, "injury": "x"}])
    with pytest.raises(ValidationError):  # a wound always says which injury it is
        EFFECTS.validate_python([{"kind": "wound", "target": "chen", "amount": 3}])


def _every_change() -> list:
    st = _state()
    return [
        m.Begin(state=st),
        m.SetParticipant(participant=m.Participant(id="thomas", kind="npc", side="party", controller="keeper", joined_round=0)),
        m.SetParticipant(participant=st.participants[0].model_copy(update={"presence": "escaped"})),
        m.SetReceipt(receipt=st.receipts[0].model_copy(update={"phase": "lapsed", "lapsed": "没有拿着拨火棍"})),
        m.SetRound(round=1, order=["chen", "douglas", "thomas"], cursor=0, budget=m.RoundBudget()),
        m.SetCursor(cursor=0),
        m.SetBudget(budget=m.RoundBudget(acted=["chen"])),
        m.SetScene(scene=m.FightScene()),
        m.SetChase(chase=None),
        m.SetPending(pending=None),
        m.SetMode(mode="fight", switch="r1:fight"),
        m.SetPhase(phase="aftermath"),
        m.SetStake(index=0, fired=True),
        m.AddStakes(stakes=[m.StakeState(when="down:douglas", flags=["douglas_down"])]),
        m.SetFills(fills=[p.Fill(who="douglas", key="闪避", value=0)]),
        m.AddChanges(changes=[m.Change(round=1, kind="joined", who="thomas")]),
        m.Compact(history=["第 0 轮：……", "第 1 轮：……"], receipts=[]),
        m.SetClock(clock=m.EncounterClock(pace=6, elapsed=6, committed=0, applied=0)),
        m.Close(record=m.EndRecord(round=1, how="won", minutes=0)),
        m.Archive(summary=m.EncounterSummary(id="enc1", mode="fight", place="cemetery", started_turn=0, rounds=1)),
    ]


def test_the_change_union_reads_each_kind_by_its_tag():
    every = _every_change()
    assert len({c.kind for c in every}) == 19  # Begin … Archive, every kind (two SetParticipant)
    assert CHANGES.validate_json(CHANGES.dump_json(every)) == every
    with pytest.raises(ValidationError):
        CHANGES.validate_python([{"kind": "teleport"}])


def test_changes_are_absolute_so_applying_them_again_changes_nothing():
    every = _every_change()
    begun = m.apply(None, every[:1])
    assert begun == _state() and begun is not every[0].state  # a copy: the op's own value is never changed
    body = every[1:-1]
    once = m.apply(begun, body)
    assert m.apply(once, body) == once
    assert once.get("thomas").side == "party" and once.get("chen").presence == "escaped"
    assert [p.id for p in once.participants] == ["chen", "douglas", "thomas"]
    assert once.receipts == []  # compacted
    assert once.round == 1 and once.mode == "fight" and once.switches == ["r0:chase", "r1:fight"]
    assert once.stakes[0].fired and [s.when for s in once.stakes] == ["escaped:douglas", "down:douglas"]
    assert once.changes == [m.Change(round=0, kind="fled", who="douglas"), m.Change(round=1, kind="joined", who="thomas")]
    assert once.phase == "ended" and once.ended.how == "won" and once.pending is None and once.chase is None
    assert begun.round == 0 and begun.mode == "chase"  # apply works on a copy
    assert m.apply(once, every[-1:]) is None  # archived: GameState.encounter_log takes the summary


def test_a_change_needs_an_encounter():
    with pytest.raises(ValueError, match="没有进行中的遭遇"):
        m.apply(None, [m.SetCursor(cursor=1)])
    with pytest.raises(ValueError, match="第 3 条"):
        m.apply(_state(), [m.SetStake(index=3, fired=True)])


# -- what a model writes --------------------------------------------------------------------------
# The plan schemas' sizes as a model reads them (JSON, unescaped), recorded here so growth is a
# decision: the everyday Plan is about 3 200 and the arbiter's Ruling about 6 300 (2026-10-21).
SIZES = {
    p.DeclaredAction: 2800,
    p.NpcArrangement: 4200,
    p.EncounterTurnPlan: 7000,
    p.EncounterStart: 10500,
    p.SiteDraft: 3600,
}


@pytest.mark.parametrize("schema", SIZES, ids=lambda s: s.__name__)
def test_plan_schemas_stay_small(schema):
    size = len(json.dumps(schema.model_json_schema(), ensure_ascii=False))
    assert size <= SIZES[schema], f"{schema.__name__} 的 schema 有 {size} 字符"


@pytest.mark.parametrize("schema", SIZES, ids=lambda s: s.__name__)
def test_plan_schemas_suit_strict_structured_output(schema):
    """No open maps (strict providers refuse them) and nothing of the engine's own in the schema."""
    text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    assert '"additionalProperties"' not in text
    for engine_only in ('"seen"', '"revealed"', '"by": {"enum"', '"fired"', '"source"'):
        assert engine_only not in text
    strict_schema(schema.model_json_schema())


def test_a_turn_plan_as_a_model_writes_it():
    data = {
        "action": {"do": "attack", "attacks": [{"target": "douglas", "with": "poker"}], "react": "dodge",
                   "takes": [{"name": "拨火棍"}], "summary": "陈墨抄起拨火棍打道格拉斯"},
        "npcs": [{"who": "douglas", "goal": "护书撤退", "actions": [{"do": "flee", "to": "tunnel", "summary": "道格拉斯往地道跑"}],
                  "react": "dodge", "pursue": [], "let_go": ["chen"]}],
        "fills": [{"who": "douglas", "key": "闪避", "value": 40}],
        "stakes": [{"when": "escaped:douglas", "flags": ["douglas_escaped"]}],
        "pace": 6,
    }
    schema = p.EncounterTurnPlan.model_json_schema()
    assert problems(data, schema) == []
    plan = p.EncounterTurnPlan.model_validate(data)
    assert plan.action.attacks[0].with_ == "poker" and plan.fills[0].by == "keeper" and plan.end is None
    assert p.EncounterTurnPlan.model_validate_json(plan.model_dump_json()) == plan
    assert problems({**data, "pace": 0}, schema)  # a round takes some time
    no_action = {"npcs": data["npcs"]}
    assert problems(no_action, schema) == [] and p.EncounterTurnPlan.model_validate(no_action).action is None


def test_an_encounter_start_with_a_drawn_site():
    data = {
        "mode": "chase", "foes": ["douglas"], "quarry": "foe", "pace": 10, "reason": "道格拉斯抢了书就跑",
        "site": {"nodes": [{"id": "study", "name": "书房"}, {"id": "yard", "name": "后院", "obstacle": "fence"},
                           {"id": "graves", "name": "墓地", "exit": True}],
                 "edges": [{"a": "study", "b": "yard"}, {"a": "yard", "b": "graves"}],
                 "obstacles": [{"id": "fence", "kind": "barrier", "what": "铁栅栏", "harm": "1D3",
                                "methods": [{"id": "climb", "label": "翻过去", "skill": "攀爬"}]}]},
    }
    schema = p.EncounterStart.model_json_schema()
    assert problems(data, schema) == []
    start = p.EncounterStart.model_validate(data)
    assert start.site.obstacles[0].state == "intact" and start.npcs == [] and start.surprise_contest is None
    bad = json.loads(json.dumps(data))
    bad["site"]["obstacles"][0]["harm"] = "1D3点"
    assert problems(bad, schema)  # the harness sends it back once
    assert p.EncounterStart.model_validate({"mode": "fight", "foes": ["ghoul"], "reason": "事件"}).pace is None  # an event's


def test_an_arrangement_needs_at_least_one_action_and_a_reaction():
    with pytest.raises(ValidationError):
        p.NpcArrangement(who="douglas", goal="逃", actions=[], react="dodge")
    with pytest.raises(ValidationError):
        p.NpcArrangement.model_validate({"who": "douglas", "goal": "逃", "actions": [{"do": "flee", "summary": "跑"}]})
