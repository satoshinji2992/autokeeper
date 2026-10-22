"""Chases on the encounter engine (engine/encounter/chase.py, site.py), with scripted dice and a scripted
keeper plan: the Keeper Rulebook's chapter 7 as the engine runs it, and what the reviews of 2026-10-21
found wrong with the old chase, asserted the right way round."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper.engine import graph as turn_graph
from autokeeper.engine.encounter import choices, site
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import Turn
from autokeeper.module.schema import Event, Module, Stake
from autokeeper.paths import EXAMPLE

from .encounter_kit import arrange, attack, fight_game, play

RUN = Event(id="e_run", title="守钟人追了上来", when="flag:x", text="", chase=["warden"])


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    return m


def pursue(target: str = "zhouheng", **kw) -> EncounterTurnPlan:
    return EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="pursue", target=target, summary="守钟人追上去"),
                                           react="dodge", **kw)])


async def chased(module, fake, *, speeds=(40, 40), **modes):
    """A chase from the newspaper office (the example's route), the warden after 周衡."""
    modes.setdefault("arrange", lambda *a: pursue())
    game, cast, events = fight_game(module, fake, location="office", **modes)
    fake.queue(*speeds)  # CON for speed: 周衡, then the warden
    await game._fire(RUN, Turn())
    return game, cast, events


def at(game, pid: str) -> str:
    return game.state.encounter.get(pid).node


def change(game, edit) -> None:
    """The encounter set up for a test the way the engine changes it: one op (the world keeps it)."""
    from autokeeper.engine.encounter.model import SetChase, SetParticipant
    from autokeeper.world.ops import EncounterStep
    from autokeeper.world.record import ENGINE

    enc = game.state.encounter.model_copy(deep=True)
    edit(enc)
    changes = [SetParticipant(participant=p) for p in enc.participants] + [SetChase(chase=enc.chase)]
    import uuid

    assert game.world.apply(EncounterStep(id=f"test/{uuid.uuid4().hex}", encounter=enc.id, unit="test", changes=changes,
                                          actor=ENGINE)).ok


def put(game, **nodes: str) -> None:
    def edit(enc):
        for pid, node in nodes.items():
            enc.get(pid).node = node
    change(game, edit)


async def test_a_chase_begins_on_the_modules_route_with_speeds_and_the_pursuer_two_locations_behind(module, fake):
    game, _cast, events = await chased(module, fake)
    enc = game.state.encounter
    ch = enc.chase
    assert enc.mode == "chase" and ch is not None and ch.site.source == "module"
    assert ch.quarry == ["zhouheng"] and ch.pursuers == ["warden"]
    assert {p.id: p.speed for p in enc.participants} == {"zhouheng": 8, "warden": 8}
    assert site.distance(ch.site, at(game, "warden"), at(game, "zhouheng")) == 2  # p. 112: two locations
    start = next(e for e in events if e["type"] == "chase_start")
    assert start["chase"]["quarry"] == "party" and [r["id"] for r in start["chase"]["runners"]] == ["zhouheng", "f1"]


async def test_a_round_runs_through_the_graph_and_losing_sight_ends_it_as_the_quarry_got_away(module, fake):
    module.flags["got_away"] = "周衡甩掉了守钟人"
    game, cast, _events = await chased(module, fake)
    game.state.encounter.stakes.append(__import__("autokeeper.engine.encounter.model", fromlist=["x"]).StakeState(
        when="escaped:players", flags=["got_away"]))
    app = turn_graph.build(game, InMemorySaver())
    labels = [o.label for o in choices.options(game, "zhouheng")]
    assert labels[0] == "全力奔跑"
    fake.queue(40, 90)  # the warden on the stairs (DEX 50); its Spot Hidden once 周衡 is behind the shut door
    out, asked = await play(app, "全力奔跑")
    st = game.state
    assert not asked and st.encounter is None and st.encounter_log[-1].ended.how == "fled" and "got_away" in st.flags
    report = cast.reports[-1]
    assert "【追逐 · 第 1 轮】" in report and "跟丢了" in report and "【追逐结束】" in report
    assert [e["type"] for e in out if e["type"].startswith("chase")][-1] == "chase_end"
    assert not fake.values


def open_door(game) -> None:
    def edit(enc):
        enc.chase.site.obstacles["ob_office_door"].state = "open"  # 周衡 came through it: nothing in the way
    change(game, edit)


async def test_a_hazard_failed_costs_1d3_points_and_what_this_round_cannot_pay_comes_off_the_next(module, fake):
    """The farmer in the mud (p. 114); the review: the old chase forgot the delay at the round's end."""
    game, cast, _ = await chased(module, fake)
    open_door(game)
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90).queue_totals(2, 1)  # the warden slips on the stairs: 2 points lost, 1 point of harm
    await play(app, "全力奔跑")
    enc = game.state.encounter
    assert at(game, "warden") == "office_stairs"  # a hazard is crossed, failed or not
    assert enc.budget.owed == {"warden": 2} and game.world.rec.entities["warden"].life.hp == 10
    assert "下一轮还欠 2 点" in cast.reports[-1]
    fake.queue(30)  # round 2: 周衡 pushes a crate across the way (STR 45)
    await play(app, "设路障")
    enc = game.state.encounter
    assert enc.budget.movement["warden"] == 0 and enc.budget.owed == {"warden": 1}  # paid one of the two this round
    assert at(game, "warden") == "office_stairs" and "还在补上一轮耽搁的行动点" in cast.reports[-1]
    assert any(o.kind == "barrier" and o.by == "zhouheng" for o in enc.chase.site.obstacles.values())


async def test_a_broken_barrier_stays_broken_for_those_behind(module, fake):
    """The acceptance case: a door forced open needs no second try by whoever comes after."""
    game, _cast, _ = await chased(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange(
        "warden", DeclaredAction(do="move", to="lamp_street", method="shove", summary="守钟人撞门"), react="dodge")]))
    put(game, zhouheng="office_stairs", warden="archive")  # both still inside, the door shut between them and the street
    app = turn_graph.build(game, InMemorySaver())
    door = next(o.label for o in choices.options(game, "zhouheng") if o.label.startswith("撞开"))
    fake.queue(20, 10)  # 周衡 shoves the door open (STR 45); the warden's stairs (DEX 50)
    await play(app, door)
    enc = game.state.encounter
    assert enc.chase.site.obstacles["ob_office_door"].state == "open" and at(game, "zhouheng") == "office_door"
    assert at(game, "warden") == "office_stairs" and not fake.values
    await play(app, "全力奔跑")  # round 2: no die is thrown for the warden at the door 周衡 opened
    assert at(game, "warden") == "office_door" and "撞开" not in "".join(game.state.encounter.receipts[-1].lines)


async def test_shooting_in_a_chase_shoots_and_costs_a_point_or_a_penalty_die_on_the_move(module, fake):
    """The review: in the old chase, choosing to shoot became running."""
    module.items[0:0] = []
    game, _cast, _ = await chased(module, fake)
    open_door(game)
    inv = game.state.investigator
    inv.inventory.append(".38 左轮手枪")
    inv.skills["射击(手枪)"] = 50
    put(game, warden="office_door")  # in sight, one location behind
    shot = next(o.label for o in choices.options(game, "zhouheng") if o.label.startswith("停下射击"))
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 90).queue_totals(4)  # the shot hits (no dive: the warden is not asked); its stairs
    out, _ = await play(app, shot)
    acts = [e for e in out if e["type"] == "fight_act"]
    assert len(acts) == 1 and acts[0]["kind"] == "shot" and game.world.rec.entities["warden"].life.hp == 7
    assert game.state.encounter.budget.movement["zhouheng"] == 0 and at(game, "zhouheng") == "office_door"


async def test_firing_on_the_move_costs_no_point_but_a_penalty_die(module, fake):
    """p. 120: shooting while running is a penalty die and no action point."""
    from autokeeper.engine.encounter.plan import AttackPick

    game, cast, _ = await chased(module, fake)
    open_door(game)
    inv = game.state.investigator
    inv.inventory.append(".38 左轮手枪")
    inv.skills["射击(手枪)"] = 50
    put(game, warden="office_door", zhouheng="office_door")
    gun_id = next(a.id for a in __import__("autokeeper.engine.encounter.actions", fromlist=["x"]).Ctx.of(game).view(
        "zhouheng").attacks() if a.firearm)
    shot = DeclaredAction(do="shoot", attacks=[AttackPick(target="warden", with_=gun_id)], to="lamp_street", summary="周衡边跑边开枪")
    cast.modes["act"] = EncounterTurnPlan(action=shot)
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 90).queue_totals(4)
    out, _ = await play(app, "边跑边朝他开一枪")
    act = next(e for e in out if e["type"] == "fight_act")
    assert act["roll"]["bonus"] == -2 and "边跑边射" in act["line"] and "目标在全速奔跑" in act["line"]
    assert at(game, "zhouheng") == "lamp_street"  # and still ran with the point it had: the shot cost none


async def test_turning_to_fight_where_they_meet_makes_it_a_fight_in_the_same_encounter(module, fake):
    """The review: 「回身迎战」 in the old chase was dropped; now it is a fight, where they met, the same
    encounter with its stakes, the two face to face engaged."""
    game, cast, _events = await chased(module, fake)
    eid = game.state.encounter.id
    open_door(game)
    put(game, warden="office_door", zhouheng="office_door")
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await play(app, "回身迎战")
    enc = game.state.encounter
    assert enc.id == eid and enc.mode == "fight" and enc.chase is None and enc.switches[-1] == "r1:fight"
    assert [(e.a, e.b) for e in enc.scene.engaged] in ([("zhouheng", "warden")], [("warden", "zhouheng")])
    kinds = [e["type"] for e in out]
    assert kinds.index("chase_end") < kinds.index("fight_start")
    assert "【追逐转为战斗】" in cast.reports[-1]
    assert next(o.label for o in choices.options(game, "zhouheng")).startswith("攻击")  # the fight's own choices now


async def test_the_route_end_moves_the_world_the_party_where_the_player_ran_and_a_keeper_quarry_to_its_exit(module, fake):
    """The review: a chase that ran out of the office left everyone where it began."""
    game, _cast, _ = await chased(module, fake)
    open_door(game)
    put(game, zhouheng="lamp_street", warden="office_door")
    app = turn_graph.build(game, InMemorySaver())
    fog = next(o.label for o in choices.options(game, "zhouheng") if o.label == "跑向雾里的广场")
    await play(app, fog)
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "fled"
    assert st.location == "square" and st.encounter_log[-1].ended.places["zhouheng"] == "square"


async def test_a_keeper_quarry_that_reaches_its_way_out_is_where_that_way_out_is(module, fake):
    flee = DeclaredAction(do="move", to="fog_square", summary="守钟人往广场跑")
    game, _cast, _events = fight_game(module, fake, location="office", arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", flee, react="dodge")]))
    module.flags["warden_gone"] = "守钟人跑了"
    run = RUN.model_copy(update={"chase_party": "追", "stakes": [Stake(when="escaped:warden", flags=["warden_gone"])]})
    fake.queue(40, 40)
    await game._fire(run, Turn())
    enc = game.state.encounter
    assert enc.chase.quarry == ["warden"] and enc.chase.pursuers == ["zhouheng"]
    put(game, warden="lamp_street", zhouheng="archive")
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10)
    await play(app, next(o.label for o in choices.options(game, "zhouheng") if o.label.startswith("追赶")))
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "escaped" and "warden_gone" in st.flags
    assert game.world.place("warden") == "square"  # the module's way out is in the square: he is there now
    assert st.location == "office"


async def test_a_quarry_that_drops_is_not_counted_as_escaped(module, fake):
    """The review: a downed quarry ended the old chase as escaped."""
    game, cast, _ = await chased(module, fake)
    module.flags["got_away"] = "x"
    put(game, warden="office_door", zhouheng="office_door")
    game.state.investigator.hp = 1
    cast.modes["arrange"] = lambda *a: EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"), react="fight_back")])
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 10, 90).queue_totals(3, 0)  # 周衡's crate; the warden's blow lands, the dodge fails
    _out, _asked = await play(app, "设路障", answers=["dodge"])
    st = game.state
    assert st.investigator.hp == 0 and "昏迷" in st.investigator.conditions
    enc = st.encounter
    assert enc is not None and enc.mode == "fight" and enc.get("zhouheng").presence == "in"  # caught, not away
    assert "【追逐转为战斗】" in cast.reports[-1] and "脱身" not in cast.reports[-1]


async def test_switches_keep_wounds_speeds_and_stakes_and_refresh_only_on_a_new_round(module, fake):
    """The acceptance case: caught, a fight, away again; nothing is rolled or restored twice."""
    from .encounter_kit import begin

    flee_plan = lambda *a: EncounterTurnPlan(npcs=[arrange("warden", attack("zhouheng"), pursue=["zhouheng"])])  # noqa: E731
    game, cast, _ = fight_game(module, fake, location="office", arrange=flee_plan)
    module.flags["got_away"] = "x"
    await begin(game, ["warden"], npcs=[arrange("warden", attack("zhouheng"), pursue=["zhouheng"])],
                stakes=[Stake(when="escaped:players", flags=["got_away"])])
    game.world.rec.entities["warden"].life.hp = 7
    eid = game.state.encounter.id
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(40, 40)  # the chase's speed rolls: 8 each
    await play(app, "逃跑")
    enc = game.state.encounter
    assert enc.id == eid and enc.mode == "chase" and {p.id: p.speed for p in enc.participants} == {"zhouheng": 8, "warden": 8}
    put(game, warden="office_door", zhouheng="office_door")
    cast.modes["arrange"] = lambda *a: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"),
                                                                       pursue=["zhouheng"])])
    await play(app, "回身迎战")
    enc = game.state.encounter
    assert enc.mode == "fight" and enc.round == 2
    cast.modes["arrange"] = flee_plan
    _out, _ = await play(app, "逃跑")  # no speed roll queued: speeds are kept for the encounter
    enc = game.state.encounter
    assert enc.id == eid and enc.mode == "chase" and enc.switches == ["r1:chase", "r2:fight", "r3:chase"]
    assert {p.id: p.speed for p in enc.participants} == {"zhouheng": 8, "warden": 8}
    assert game.world.rec.entities["warden"].life.hp == 7 and "got_away" not in game.state.flags
    assert not fake.values


async def test_a_chase_cut_short_between_two_dice_throws_each_once_and_lands_each_unit_once(module, fake):
    """Recovery (I2): the warden's slip on the stairs landed in its own unit; the crash at the round's
    end (his Spot Hidden, 周衡 behind the shut door) is thrown once on the retry, nothing twice."""
    from .test_encounter_recovery import Breaking

    game, _cast, events = await chased(module, fake)
    game.state.turn += 1
    game.state.pending_turn = {"input": "全力奔跑"}  # the turn's journal: what the retry goes on from
    action = await game.encounter_input("全力奔跑")
    ruling = await game.rule(action)
    fake.queue(90, 90).queue_totals(2, 1)  # the stairs failed (2 points, 1 harm); his Spot Hidden
    game.rules.dice = Breaking(fake, 2)
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    enc = game.state.encounter
    assert enc.budget.owed == {"warden": 2} and game.world.rec.entities["warden"].life.hp == 10
    assert list(fake.values) == [90] and enc.mode == "chase"
    game.rules.dice = fake
    await game.resolve(action, ruling)
    assert not fake.values and not fake.totals
    assert game.world.rec.entities["warden"].life.hp == 10  # the harm landed once
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "fled"  # he lost 周衡 behind the door
    assert sum(e["type"] == "chase_end" for e in events) == 1


async def test_something_else_in_a_chase_is_ruled_and_lands_on_the_way(module, fake):
    """An `other` action in a chase: the arbiter rules it (from its neutral summary), it costs a point,
    and what it does lands: here the door slammed and wedged is a barrier for whoever comes after."""
    from autokeeper.agents.cast import RuledEffect, Ruling

    wedge = DeclaredAction(do="other", summary="周衡把门撞上，用椅子顶住")
    game, cast, _ = await chased(module, fake, act=EncounterTurnPlan(action=wedge))
    cast.ruling_for[wedge.summary] = Ruling(needs_roll=True, skill="力量", reason="门很重", effects=[
        RuledEffect(kind="obstacle", target="office_door", what="门被椅子顶住，堵上了")])
    open_door(game)
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 10)  # STR to wedge the door; the warden's stairs
    await play(app, "把门关上顶住")
    enc = game.state.encounter
    assert enc.chase.site.obstacles["ob_office_door"].state == "blocked"
    assert enc.budget.movement["zhouheng"] == 0 and [a.summary for a in cast.ruled] == [wedge.summary]
    report = cast.reports[-1]
    assert "把门关上顶住" not in report and "周衡把门撞上" in report and "堵上了" in report


async def test_hiding_far_ahead_gets_a_bonus_die_and_a_hidden_quarry_cannot_be_gone_after(module, fake):
    game, _cast, _ = await chased(module, fake)
    open_door(game)
    put(game, zhouheng="lamp_street", warden="archive")
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10)  # 潜行 against the warden's Spot Hidden: three locations behind, a bonus die
    out, _ = await play(app, "躲藏")
    die = next(e for e in out if e["type"] == "dice")
    assert die["bonus"] == 2  # three locations ahead (rules/chase.hide_bonus)
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "fled"  # lost by every pursuer: away


# -- the pre-release review (2026-10-22): no split party, a foe who runs, perception at the switch ------------
def with_li(module, fake, li_does: list[str], **modes):
    """A fight in the office with two investigators: 周衡 (the player) and 李四 (an AI companion who
    picks the option named next in `li_does`, the last one again and again)."""
    li = module.pregens[0].model_copy(update={"id": "li", "name": "李四"}, deep=True)

    def choose(options):
        want = li_does.pop(0) if len(li_does) > 1 else li_does[0]
        return next(o.id for o in options if o.label == want)

    game, cast, events = fight_game(module, fake, location="office", choose=choose, **modes)
    game.state.investigators.append(li)
    return game, cast, events


GUARD = DeclaredAction(do="defend", summary="守着")


def as_chase(game) -> None:
    """The fight made a chase the way the engine changes it (one op), for a state set up by hand."""
    from autokeeper.engine.encounter.model import SetMode
    from autokeeper.world.ops import EncounterStep
    from autokeeper.world.record import ENGINE

    assert game.world.apply(EncounterStep(id="test/mode", encounter=game.state.encounter.id, unit="t",
                                          changes=[SetMode(mode="chase")], actor=ENGINE)).ok


async def test_a_flight_is_not_chased_while_the_others_still_fight_and_the_chase_takes_the_whole_party(module, fake):
    """The review's blocker: one investigator fled and was chased while the other still fought the preacher
    at the start; the chase ended the whole encounter as lost with him on his feet. Now the chase waits
    until nobody outside it is still fighting; whoever fled is out of the fight meanwhile."""
    from autokeeper.engine.encounter.actions import Ctx

    from .encounter_kit import begin

    warden = arrange("warden", GUARD, react="dodge", pursue=["zhouheng", "li"])
    preacher = arrange("preacher", GUARD, react="dodge", let_go=["zhouheng", "li"])
    game, cast, _ = with_li(module, fake, ["防守", "逃跑"], arrange=lambda *a: EncounterTurnPlan())
    await begin(game, ["warden", "preacher"], npcs=[warden, preacher])
    app = turn_graph.build(game, InMemorySaver())
    await play(app, "逃跑")  # 周衡 runs; 李四 stays and guards, the preacher in front of him
    enc = game.state.encounter
    assert enc.mode == "fight" and enc.get("zhouheng").presence == "fled" and Ctx.of(game).acting("li")
    assert game._outcome(Ctx.of(game)) == "" and "这里还在打，暂时没人追上去" in cast.reports[-1]
    assert choices.options(game, "zhouheng") == []  # out of the fight while it waits
    fake.queue(40, 40, 40)  # 李四 runs too: the speed rolls of 周衡, 李四 and the warden
    await play(app, "/encounter")
    enc = game.state.encounter
    assert enc.mode == "chase" and sorted(enc.chase.quarry) == ["li", "zhouheng"] and enc.chase.pursuers == ["warden"]
    assert enc.get("preacher").presence == "left"  # he let them go: out of it, where he stood
    assert not fake.values


async def test_a_chase_is_never_lost_while_an_investigator_can_still_act(module, fake):
    """The review's probe: a chase set up while someone else still stood at the start; the quarry struck
    down far away. Whoever can still act keeps it going."""
    from autokeeper.engine.encounter import chase as chasing
    from autokeeper.engine.encounter.actions import Ctx
    from autokeeper.engine.encounter.model import ChaseState

    from .encounter_kit import begin

    game, _cast, _ = with_li(module, fake, ["防守"], arrange=lambda *a: EncounterTurnPlan())
    await begin(game, ["warden", "preacher"])
    built = site.build(module, "office")
    far = next(n.id for n in built.nodes if (site.distance(built, built.origin, n.id) or 0) >= 2)

    def edit(e):
        e.chase = ChaseState(site=built, quarry=["zhouheng"], pursuers=["warden"])
        for p in e.participants:
            p.node = far if p.id in ("zhouheng", "warden") else built.origin
    change(game, edit)
    as_chase(game)
    inv = game.state.investigators[0]
    inv.hp, inv.conditions = 0, ["昏迷"]
    ctx = Ctx.of(game)
    assert ctx.acting("li") and chasing.verdict(ctx) == "" and game._outcome(ctx) == ""
    game.state.investigators[1].conditions = ["昏迷"]
    assert chasing.verdict(Ctx.of(game)) == "lost"  # nobody of theirs left who can act


async def test_a_foe_who_runs_may_be_chased_next_round_and_otherwise_got_away(module, fake):
    """The review: the fight's options never offered going after a foe who ran, and a fight the foe ran
    from ended as won. The round after it ran offers 追赶; let go, it ends as escaped."""
    from autokeeper.module.schema import Stake

    from .encounter_kit import begin

    module.flags["warden_gone"] = "守钟人跑了"
    flee = DeclaredAction(do="flee", to="away", summary="守钟人逃跑")
    game, _cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", flee, react="dodge")]))
    await begin(game, ["warden"], npcs=[arrange("warden", flee, react="dodge")],
                stakes=[Stake(when="ended:escaped", flags=["warden_gone"])])
    app = turn_graph.build(game, InMemorySaver())
    await play(app, "防守")
    enc = game.state.encounter
    assert enc is not None and enc.get("warden").presence == "fled"  # not away yet: the party may go after him
    labels = [o.label for o in choices.options(game, "zhouheng")]
    assert "追赶 楼梯口打盹的老人" in labels and not any(x.startswith("攻击") for x in labels)
    await play(app, "防守")
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "escaped" and "warden_gone" in st.flags


async def test_going_after_a_foe_who_ran_turns_the_fight_into_a_chase(module, fake):
    from .encounter_kit import begin

    flee = DeclaredAction(do="flee", to="away", summary="守钟人逃跑")
    game, _cast, _ = fight_game(module, fake, location="office",
                                arrange=lambda *a: EncounterTurnPlan(npcs=[arrange("warden", flee, react="dodge")]))
    await begin(game, ["warden"], npcs=[arrange("warden", flee, react="dodge")])
    app = turn_graph.build(game, InMemorySaver())
    await play(app, "防守")
    fake.queue(40, 40)  # the speed rolls: 周衡, the warden
    await play(app, "追赶 楼梯口打盹的老人")
    enc = game.state.encounter
    assert enc.mode == "chase" and enc.chase.quarry == ["warden"] and enc.chase.pursuers == ["zhouheng"]
    assert not fake.values


async def test_an_arrangement_to_pursue_someone_it_no_longer_sees_is_not_followed(module, fake):
    """The review: a pursuit written into an arrangement before the keeper declared the dark held at the
    round's end. Nobody goes after someone they cannot perceive."""
    from autokeeper.engine.encounter.plan import SightNote

    from .encounter_kit import begin

    keep = arrange("warden", GUARD, react="dodge", pursue=["zhouheng"])
    game, cast, _ = fight_game(module, fake, arrange=lambda *a: EncounterTurnPlan())
    await begin(game, ["warden"], npcs=[keep])
    await game._plan_unit("dark", EncounterTurnPlan(sight=[SightNote(watcher="warden", who="zhouheng", why="四下漆黑")]).model_dump(
        mode="json", by_alias=True), [], Turn())
    app = turn_graph.build(game, InMemorySaver())
    await play(app, "逃跑")
    st = game.state
    assert st.encounter is None and st.encounter_log[-1].ended.how == "fled" and not fake.values
    assert "追不了周衡" in cast.reports[-1] and "【战斗转为追逐】" not in cast.reports[-1]


def players_lines(out: list[dict]) -> list[str]:
    """Every line the players' screens were sent of the chase."""
    return [x for e in out if e["type"] in ("chase_state", "fight_state") for x in e.get("lines") or []]


async def test_a_foe_who_hides_has_no_place_on_the_players_screens(module, fake):
    """The review: the round's line gave every runner's place and points, the hide line where the warden
    hid, and the route kept his node. The players get what their investigators perceive; the keeper all."""
    hide = DeclaredAction(do="hide", summary="守钟人躲起来")
    game, cast, _ = fight_game(module, fake, location="office", arrange=lambda *a: EncounterTurnPlan(
        npcs=[arrange("warden", hide, react="dodge")]))
    fake.queue(40, 40)  # the speed rolls
    await game._fire(RUN.model_copy(update={"chase_party": "追"}), Turn())
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(1, 1)  # 周衡 down the stairs; the warden's Stealth
    out, _ = await play(app, "追赶 楼梯口打盹的老人")
    shown = players_lines(out)
    assert "楼梯口打盹的老人不见了踪影" in shown and not any("【追逐 · 第" in x or "报社大门" in x for x in shown)
    hid = [r for e in out if e["type"] == "chase_state" for r in e["chase"]["runners"] if r["name"] == "楼梯口打盹的老人"][-1]
    assert hid["hidden"] and hid["pos"] is None and hid["node"] == ""
    report = cast.reports[-1]
    assert "【追逐 · 第 1 轮】" in report and "在报社大门躲了起来" in report  # the keeper's account keeps it all


async def test_a_pursuer_nobody_sees_runs_unseen(module, fake):
    """Running where no investigator perceives it (the keeper declared the fog): the players get nothing of
    where it went, nor of the obstacle it crossed; the keeper does."""
    from autokeeper.engine.encounter.plan import SightNote

    from .test_fight import chase_route

    game, cast, _ = await chased(module, fake)
    open_door(game)
    await game._plan_unit("fog", EncounterTurnPlan(sight=[SightNote(watcher="zhouheng", who="warden", why="浓雾")]).model_dump(
        mode="json", by_alias=True), [], Turn())
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(10)  # the warden on the stairs
    out, _ = await play(app, "全力奔跑")
    shown = players_lines(out)
    assert shown and not any(x.startswith("楼梯口打盹的老人") for x in shown)
    assert "老守钟人跑到了报社的楼梯" in cast.reports[-1]  # the keeper knows him by name
    view = next(e for e in reversed(out) if e["type"] == "chase_state")["chase"]
    warden = next(r for r in view["runners"] if r["side"] == "foe")
    assert warden["pos"] is None and warden["node"] == ""
    lines = chase_route(view, 40, 30).plain.split("\n")
    rules = [i for i, x in enumerate(lines) if x and set(x) == {"─"}]
    way = lines[rules[0] + 1:rules[1]]
    assert way and not any("楼梯口打盹的老人" in x for x in way)  # not on the way drawn


async def test_the_fight_a_chase_turns_into_is_where_contact_found_it(module, fake):
    """The review: contact() and to_fight() each picked the meeting point their own way. A quarry struck
    down where its pursuer stands is the meeting point, not the place of a companion who turned round
    with nobody there to face. The review of 10-22: checked on the committed encounter and world, not on
    the unit's working copy, which had the new place while the save kept the old one."""
    from autokeeper.engine.encounter import chase as chasing
    from autokeeper.engine.encounter.model import ActionReceipt, ChaseState, SetMode, SetRound
    from autokeeper.world.ops import EncounterStep
    from autokeeper.world.record import ENGINE

    from .encounter_kit import begin

    game, _cast, _ = with_li(module, fake, ["防守"], arrange=lambda *a: EncounterTurnPlan())
    await begin(game, ["warden"])
    built = site.build(module, "office")
    here = "fog_square"  # the square, another place than the office the chase began in
    there = site.neighbours(built, here)[0][0]

    def edit(e):
        e.chase = ChaseState(site=built, quarry=["zhouheng", "li"], pursuers=["warden"])
        for p in e.participants:
            p.node = there if p.id == "li" else here
    change(game, edit)
    enc = game.state.encounter
    assert game.world.apply(EncounterStep(id="test/round", encounter=enc.id, unit="t", actor=ENGINE, changes=[
        SetMode(mode="chase"), SetRound(round=1, order=list(enc.order))])).ok
    inv = game.state.investigators[0]
    inv.hp, inv.conditions = 0, ["昏迷"]  # struck down where the warden stands

    def meet(w):
        w.enc.receipts.append(ActionReceipt(id="x", round=1, actor="li", source="ai", summary="-", phase="committed",
                                            declared=DeclaredAction(do="stand", summary="-")))
        assert chasing.contact(w) == here
        assert chasing.round_end(w) == "fight"

    value = await game._unit("test/meet", Turn(), meet)
    enc, st = game.state.encounter, game.state
    assert enc.mode == "fight" and enc.chase is None and {p.node for p in enc.participants} == {here}
    assert [{e.a, e.b} for e in enc.scene.engaged] == [{"zhouheng", "warden"}]
    assert enc.place == "square" and st.location == "square" and game.world.place("warden") == "square"
    assert not st.apart and value["extra"]["arrive"] == "square"  # the arrival there follows the op


async def test_the_ways_past_an_obstacle_read_short_and_care_is_one_choice(module, fake):
    """The review's screenshot: the same hazard four times over, each with the place it leads to in
    brackets and a 小心 copy of every way past it. Now each way once (the method and the obstacle) and
    one careful crossing, by the way the investigator is best at; the words are still matched exactly."""
    game, _cast, _ = await chased(module, fake)
    put(game, zhouheng="archive", warden="archive")
    labels = [o.label for o in choices.options(game, "zhouheng")]
    ways = [x for x in labels if "堆着报纸的楼梯" in x]
    assert ways == ["冲下去：堆着报纸的楼梯", "顺着扶手滑下去：堆着报纸的楼梯", "小心通过：堆着报纸的楼梯"]
    assert not any("（往" in x for x in labels)
    careful = choices.match(game, "zhouheng", "小心通过：堆着报纸的楼梯")
    assert careful is not None and (careful.method, careful.careful, careful.to) == ("run", 1, "office_stairs")  # 敏捷 65 over 攀爬 20


async def test_who_the_chase_left_behind_is_known_to_the_keeper_and_the_plan_until_the_party_moves(module, fake):
    """The review: GameState.apart was written and never read. The keeper's brief and the plan's
    bearings name who was left where, an investigator out cold at the start too."""
    from autokeeper import config
    from autokeeper.agents.cast import Cast
    from autokeeper.engine.encounter.model import ChaseState
    from autokeeper.world import context as world_view

    from .encounter_kit import begin

    game, _cast, _ = with_li(module, fake, ["防守"], arrange=lambda *a: EncounterTurnPlan())
    await begin(game, ["warden"])
    built = site.build(module, "office")

    def edit(e):
        e.chase = ChaseState(site=built, quarry=["zhouheng", "li"], pursuers=["warden"])
        for p in e.participants:
            p.node = "fog_square" if p.id == "zhouheng" else built.origin
        e.get("zhouheng").presence = "escaped"
    change(game, edit)
    as_chase(game)
    li = game.state.investigators[1]
    li.hp, li.conditions = 0, ["昏迷"]
    await game._encounter_end("fled", Turn())
    st = game.state
    assert st.location == "square" and st.apart == {"li": "office"}
    assert "和大家分开的调查员" in world_view.keeper_brief(game.world) and "李四在报社（昏迷）" in world_view.keeper_brief(game.world)
    cast = Cast(config.Config(), module)
    assert "【和大家分开的调查员】李四在报社（昏迷）" in cast.bearings(st)
    await game._arrive("tower", Turn())
    assert not st.apart and "李四" not in world_view.keeper_brief(game.world)  # the party moved: together again


# -- the progress review of 2026-10-22: a chase come to blows in another place lands there -------------------
def through_the_square() -> Module:
    """The example, the square an ordinary way through (not a way out): a chase can come to blows there."""
    m = Module.load(EXAMPLE)
    m.events, m.endings = [], []
    next(x for x in m.location("office").chase if x.id == "fog_square").exit = False
    return m


async def test_a_chase_that_comes_to_blows_in_another_place_is_there_in_the_save_and_the_world(fake):
    """The review (P1): 「回身迎战」 in the square told 「在雾里的广场扭打在一起」, while the encounter, the party
    and the warden stayed in the office, and the archive kept them there. Now the switch's op moves them all;
    then talking, dropping something, a save and its reload, and the end agree on where it is."""
    from autokeeper.agents.cast import Action, Ruling
    from autokeeper.engine.game import Game
    from autokeeper.engine.state import GameState, WorldChange
    from autokeeper.rules.coc import Rules

    waiting = EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="守着"))])
    game, cast, _ = await chased(through_the_square(), fake, arrange=lambda *a: waiting)
    put(game, warden="fog_square", zhouheng="fog_square")
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await play(app, "回身迎战")
    st, enc = game.state, game.encounter()
    assert enc.mode == "fight" and {p.node for p in enc.participants} == {"fog_square"}
    assert any(e["type"] == "chase_end" and "雾里的广场" in e.get("text", "") for e in out)
    assert enc.place == "square" and st.location == "square" and not st.apart
    assert game.world.place("warden") == "square" and "warden" in game.world.present("square") and "warden" in game._present()
    assert any(e["type"] == "move" and e["location"] == "square" for e in out)  # arriving there, as any arrival
    assert "square" in st.visited

    # talking at once: the warden the keeper has speak is asked where they both are
    cast.modes["arrange"] = lambda *a: EncounterTurnPlan(npcs=[arrange(
        "warden", DeclaredAction(do="speak", speech="站住！", summary="守钟人喝止"), speak=True)])
    await play(app, next(o.label for o in choices.options(game, "zhouheng") if o.value["do"] == "defend"))
    assert [n for n, _ in cast.npc_calls] == ["warden"]

    # something dropped at the scene lands in the square, the place the party is in
    cast.modes["arrange"] = lambda *a: waiting
    st.investigator.inventory.append("手电筒")
    drop = DeclaredAction(do="other", summary="周衡把手电筒丢在脚边")
    cast.modes["act"] = EncounterTurnPlan(action=drop)
    cast.ruling_for[drop.summary] = Ruling(needs_roll=False, changes=[WorldChange(
        kind="transfer", key="torch", text="周衡把手电筒丢在脚边", item="手电筒", owner="square")])
    await play(app, "把手电筒丢在脚边")
    assert st.item_owners["手电筒"] == "square" and "手电筒" not in st.investigator.inventory

    # saved and loaded: the same world
    m2 = through_the_square()
    st2 = GameState.model_validate_json(st.model_dump_json())
    game2 = Game(m2, st2, cast, Rules(dice=fake))
    enc2 = game2.encounter()
    assert enc2 is not None and enc2.mode == "fight" and enc2.place == "square" and st2.location == "square"
    assert game2.world.place("warden") == "square" and m2.npc("warden").location == "square"
    assert st2.item_owners["手电筒"] == "square"

    # ended and archived where it was fought; the warden still there to be talked to
    await game2._encounter_end("parley", Turn())
    log = st2.encounter_log[-1]
    assert st2.encounter is None and log.place == "square" and log.ended.places == {"zhouheng": "square", "warden": "square"}
    assert game2.world.place("warden") == "square" and st2.location == "square" and not st2.apart
    ask = Action(kind="talk", npc="warden", speech="你为什么追我？", summary="周衡问守钟人为什么追他")
    cast.script["问守钟人"] = (ask, Ruling(needs_roll=False))
    cast.npc_calls.clear()
    await play(turn_graph.build(game2, InMemorySaver()), "问守钟人")
    assert [n for n, _ in cast.npc_calls] == ["warden"]
