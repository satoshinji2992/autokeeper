"""A compound player request runs through real turns, interrupts, receipts and party time."""

from __future__ import annotations

from collections import deque

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper import config
from autokeeper.agents.cast import (
    Action,
    Cast,
    Continuation,
    ContinuedHostedReply,
    Narration,
    PartyPlan,
    PartyStep,
    Plan,
    Ruling,
)
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast
from .test_scene import SCRIPT, SEEN, WRITER, sandbox  # noqa: F401

THREAD = {"configurable": {"thread_id": "compound-request"}, "recursion_limit": 100}


class RequestCast(FakeCast):
    def __init__(self, plans, rulings, tellings):
        super().__init__({})
        self.plans = plans
        self.rulings = rulings
        self.tellings = deque(tellings)
        self.planned = []
        self.party_calls = 0

    async def plan(self, state, text):
        self.planned.append(text)
        return self.plans[text]

    async def party_plan(self, state, inputs):
        self.party_calls += 1
        return PartyPlan(steps=[PartyStep(token=row.token, plan=self.plans[row.text]) for row in inputs])

    async def arbiter(self, state, action, at=None, **kwargs):
        self.ruled.append(action)
        return self.rulings[action.summary]

    async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix=""):
        self.reports.append(report)
        told = self.tellings.popleft()
        if told.scene is not None and check is not None:
            check(told.scene)
        return told


def module():
    m = Module.load(EXAMPLE)
    m.events.clear()
    m.endings.clear()
    m.foreshadow.clear()
    m.agendas.clear()
    return m


def make(fake, cast, *, m=None, state=None, party=False):
    m = m or module()
    if state is None:
        first = m.pregens[0].model_copy(deep=True)
        investigators = [first]
        if party:
            investigators.append(first.model_copy(update={"id": "lin", "name": "林秋"}, deep=True))
        state = Game.new(m, investigators, human=[i.id for i in investigators])
        state.location, state.minutes = "tower", 20 * 60 + 20
    game = Game(m, state, cast, Rules(dice=fake))
    graph = turn_graph.build(game, InMemorySaver())
    return game, graph


async def run(graph, game, value):
    events, offers = [], []
    async for mode, chunk in turn_graph.stream(graph, game, value, THREAD, stream_mode=["custom", "updates"]):
        if mode == "custom":
            events.append(chunk)
        elif "__interrupt__" in chunk:
            offers.extend(item.value for item in chunk["__interrupt__"])
    return events, offers


def telling(*, next=None, pending="", scene=None, error="", control=True):
    return Narration(text="当前段已结算。", scene={} if scene is None else scene,
                     continuation=Continuation(next=next, pending=pending) if control else None,
                     continuation_error=error)


@pytest.mark.parametrize("recover_after_gift", [False, True])
async def test_real_cast_talk_then_wait_executes_the_tail_from_the_same_keeper_response(fake, recover_after_gift):
    m = module()
    cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES}), m)
    game, graph = make(fake, cast, m=m)
    games, casts = [game], [cast]
    st, original = game.state, "先向守钟人借钥匙，然后原地等到20:35。"
    who = st.investigator.id
    SCRIPT.extend([
        {"action": {"kind": "talk", "summary": "周衡向守钟人借钥匙", "npc": "warden", "speech": "请借我钥匙。"},
         "remaining": "原地等到20:35。"},
        {"needs_roll": False, "minutes": 5},
        {"text": "借给你。", "items": [{"name": "借用钥匙", "to": who}]},
        {"kind": "tell", "telling": {"scene": {}, "text": "钥匙已经交到你手里。", "continuation": {
            "next": {"action": {"kind": "wait", "summary": "周衡原地等到20:35"}}}}},
        {"needs_roll": False, "minutes": 10, "rest": True},
        {"kind": "tell", "telling": {"scene": {}, "text": "钟面走到了20:35。"}},
    ])
    try:
        value = {"input": original}
        if recover_after_gift:
            saved = []

            def disconnect():
                if st.pending_turn.get("stage") == "narrate":
                    saved.append(st.model_dump_json())
                    raise RuntimeError("NPC交物后断开")

            game.checkpoint = disconnect
            with pytest.raises(RuntimeError, match="NPC交物后断开"):
                await run(graph, game, value)
            assert st.investigator.inventory.count("借用钥匙") == 1
            cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES}), m)
            game, graph = make(fake, cast, m=m, state=GameState.model_validate_json(saved[-1]))
            st = game.state
            games.append(game)
            casts.append(cast)
            value = {"input": "/retry"}
        events, offers = await run(graph, game, value)
        assert not offers and not SCRIPT
        assert st.clock == "D1 20:35" and st.turn == 2 and not st.pending_turn
        assert [e.text for e in st.log if e.kind == "player"] == [original]
        assert st.investigator.inventory.count("借用钥匙") == 1
        assert st.usage["plan"]["calls"] == 1 and st.usage["npc"]["calls"] == 1
        assert st.usage["keeper"]["calls"] == 2 and st.usage["arbiter"]["calls"] == 2
        continued = [row["schema"] for row in SEEN if row["schema"].get("title") == ContinuedHostedReply.__name__]
        assert len(continued) == 1 and "telling" in continued[0]["properties"]
        assert len([e for e in st.log if e.kind == "npc"]) == 1
        assert len([e for e in events if e["type"] == "npc"]) == (0 if recover_after_gift else 1)
    finally:
        for old in games:
            await old.close()
        for old in casts:
            await old.close()


async def test_second_segment_roll_and_luck_survive_saved_graph_reconstruction_without_repeating(fake):
    original = "先整理工具，再辨认门上的刻痕，成功后原地等两分钟。"
    first = Action(kind="use", summary="周衡整理工具")
    checked = Action(kind="investigate", summary="周衡辨认刻痕", target="门上的刻痕")
    final = Action(kind="wait", summary="周衡原地等两分钟")
    next_plan = Plan(action=checked, remaining="成功后原地等两分钟。")
    cast = RequestCast({original: Plan(action=first, remaining="辨认刻痕后原地等两分钟。")}, {
        first.summary: Ruling(needs_roll=False, minutes=1),
        checked.summary: Ruling(needs_roll=True, skill="侦查", minutes=1),
        final.summary: Ruling(needs_roll=False, minutes=2, rest=True),
    }, [telling(next=next_plan), telling(next=Plan(action=final)), telling()])
    game, graph = make(fake, cast)
    games = [game]
    start, skill, luck = game.state.minutes, game.state.investigator.value("侦查"), game.state.investigator.luck
    fake.queue(skill + 1)
    try:
        _, offers = await run(graph, game, {"input": original, "by_hand": True})
        assert [o["kind"] for o in offers] == ["roll"]
        assert game.state.turn == 2 and len(fake.values) == 1 and cast.planned == [original]
        state = GameState.model_validate_json(game.state.model_dump_json())
        game, graph = make(fake, cast, m=game.m, state=state)
        games.append(game)
        _, offers = await run(graph, game, {"input": "/retry", "by_hand": True})
        assert [o["kind"] for o in offers] == ["roll"]
        _, offers = await run(graph, game, Command(resume=True))
        assert [o["kind"] for o in offers] == ["push"] and not fake.values
        assert len(game.state.checks) == 1 and not game.state.checks[0].passed

        # A new graph has no old LangGraph checkpoint; the saved engine journal owns the dice.
        state = GameState.model_validate_json(game.state.model_dump_json())
        game, graph = make(fake, cast, m=game.m, state=state)
        games.append(game)
        _, offers = await run(graph, game, {"input": "/retry", "by_hand": True})
        assert [o["kind"] for o in offers] == ["push"] and not fake.values
        _, offers = await run(graph, game, Command(resume="luck"))
        assert not offers and not cast.tellings
        st = game.state
        assert st.turn == 3 and st.minutes == start + 4 and not st.pending_turn and not st.pending_push
        assert len(st.checks) == 1 and st.checks[0].passed and st.investigator.luck == luck - 1
        assert cast.planned == [original] and [a.summary for a in cast.ruled] == [first.summary, checked.summary, final.summary]
        assert [e.text for e in st.log if e.kind == "player"] == [original]
        dice_lines = [e.text for e in st.log if e.kind == "dice"]
        assert len(dice_lines) == 2 and sum("→" in text for text in dice_lines) == 1
        assert sum("花费幸运" in text for text in dice_lines) == 1
    finally:
        for old in games:
            await old.close()


@pytest.mark.parametrize("failure", ["scene", "missing", "invalid"])
async def test_unaccepted_scene_or_continuation_preserves_tail_without_executing_it(fake, failure):
    original = "先整理工具，然后到广场去。"
    first = Action(kind="use", summary="周衡整理工具")
    next_plan = Plan(action=Action(kind="move", summary="周衡前往广场", destination="square"))
    if failure == "scene":
        tellings = [telling(next=next_plan, scene={"here": ["unknown_person"]}),
                    telling(next=next_plan, scene={"here": ["unknown_person"]}), telling(next=next_plan)]
    else:
        tellings = [telling(control=False, error="续行格式未通过" if failure == "invalid" else "")]
    cast = RequestCast({original: Plan(action=first, remaining="到广场去。")},
                       {first.summary: Ruling(needs_roll=False, minutes=1)}, tellings)
    game, graph = make(fake, cast)
    try:
        _, offers = await run(graph, game, {"input": original})
        assert not offers and game.state.turn == 1 and game.state.location == "tower"
        assert not game.state.pending_turn and cast.planned == [original]
        assert [a.summary for a in cast.ruled] == [first.summary]
        note = game.state.unfinished[game.state.investigator.id]
        assert note["original"] == original and note["remaining"] == "到广场去。" and note["reason"]
    finally:
        await game.close()


@pytest.mark.parametrize("new_actor", [0, 1])
async def test_keeper_pending_unlocks_the_graph_for_a_new_human_input(fake, new_actor):
    original, fresh = "先整理工具，再去广场。", "我改变安排，先查看窗框。"
    first = Action(kind="use", summary="周衡整理工具")
    other = Action(kind="investigate", summary="调查员查看窗框", target="窗框")
    cast = RequestCast({original: Plan(action=first, remaining="去广场。"), fresh: Plan(action=other)}, {
        first.summary: Ruling(needs_roll=False, minutes=1),
        other.summary: Ruling(needs_roll=False, minutes=1),
    }, [telling(pending="守钟人提出新问题，原定去广场的意图尚待玩家决定。"), telling()])
    game, graph = make(fake, cast, party=True)
    try:
        await run(graph, game, {"input": original})
        owner = game.state.investigator.id
        assert owner in game.state.unfinished and not game.state.pending_turn
        game.state.active = new_actor
        _, offers = await run(graph, game, {"input": fresh})
        assert not offers and not game.state.pending_turn and game.state.turn == 2
        assert game.state.location == "tower" and cast.planned == [original, fresh]
        assert [e.text for e in game.state.log if e.kind == "player"] == [original, fresh]
    finally:
        await game.close()


async def test_party_compound_work_overlaps_and_completes_each_token_once_with_all_checks(fake):
    a_text, b_text = "先看齿轮，再听窗外。", "我独自检查书桌二十分钟。"
    a1 = Action(kind="investigate", summary="周衡查看齿轮", target="齿轮")
    a2 = Action(kind="investigate", summary="周衡听窗外", target="窗外")
    b = Action(kind="investigate", summary="林秋检查书桌", target="书桌")
    cast = RequestCast({a_text: Plan(action=a1, remaining="听窗外。"), b_text: Plan(action=b)}, {
        a1.summary: Ruling(needs_roll=True, skill="侦查", minutes=5),
        a2.summary: Ruling(needs_roll=True, skill="聆听", minutes=10),
        b.summary: Ruling(needs_roll=True, skill="图书馆使用", minutes=20),
    }, [telling(next=Plan(action=a2)), telling(), telling()])  # different personal results are not one public body
    game, graph = make(fake, cast, party=True)
    st, start = game.state, game.state.minutes
    a_id, b_id = [i.id for i in st.investigators]
    complete, completed_calls = game.complete_party_action, []

    def counted():
        completed_calls.append(st.pending_turn.get("party_token"))
        complete()

    game.complete_party_action = counted
    fake.queue(1, 2, 3)
    try:
        game.begin_round()
        assert await game.arrange_party([(a_id, a_text), (b_id, b_text)]) == [(a_id, a_text), (b_id, b_text)]
        game.holding = True
        _, offers = await run(graph, game, {"input": a_text})
        assert not offers and st.round_elapsed[a_id] == 15 and st.minutes == start + 15
        assert st.round_plan["done"] == ["0"] and completed_calls == ["0"]
        result = st.round_plan["results"]["0"]
        assert "侦查" in result and "聆听" in result
        st.active, game.holding = 1, False
        _, offers = await run(graph, game, {"input": b_text})
        assert not offers and st.minutes == start + 20 and st.round_elapsed == {a_id: 15, b_id: 20}
        assert st.round_plan["done"] == ["0", "1"] and completed_calls == ["0", "1"]
        assert st.turn == 3 and len(st.checks) == 3 and not fake.values and not st.pending_turn
        assert cast.party_calls == 1 and cast.planned == []
        assert [e.text for e in st.log if e.kind == "player"] == [a_text, b_text]
        game.end_round()
    finally:
        await game.close()


async def test_casual_talk_in_a_held_compound_request_still_obtains_and_executes_its_continuation(fake):
    original, other = "先收起工具，再向守钟人道谢，然后等两分钟。", "我等五分钟。"
    first = Action(kind="use", summary="周衡收起工具")
    thanks = Action(kind="talk", summary="周衡向守钟人道谢", npc="warden", speech="谢谢。", casual=True)
    final = Action(kind="wait", summary="周衡等两分钟")
    b = Action(kind="wait", summary="林秋等五分钟")
    cast = RequestCast({original: Plan(action=first, remaining="向守钟人道谢，然后等两分钟。"), other: Plan(action=b)}, {
        first.summary: Ruling(needs_roll=False, minutes=1),
        thanks.summary: Ruling(needs_roll=False, minutes=1),
        final.summary: Ruling(needs_roll=False, minutes=2, rest=True),
        b.summary: Ruling(needs_roll=False, minutes=5, rest=True),
    }, [telling(next=Plan(action=thanks, remaining="等两分钟。")), telling(next=Plan(action=final)), telling()])
    game, graph = make(fake, cast, party=True)
    st, start = game.state, game.state.minutes
    a_id, b_id = [i.id for i in st.investigators]
    try:
        game.begin_round()
        await game.arrange_party([(a_id, original), (b_id, other)])
        game.holding = True
        _, offers = await run(graph, game, {"input": original})
        assert not offers and not st.pending_turn and st.turn == 3
        # The existing casual greeting path costs no minutes; it must still hand off the wait.
        assert st.minutes == start + 3 and st.round_elapsed[a_id] == 3
        assert len(cast.reports) == 2 and len(cast.npc_calls) == 1
        assert st.round_plan["done"] == ["0"] and [h["turn"] for h in st.held] == [3]
        assert [e.text for e in st.log if e.kind == "player"] == [original]
        st.active, game.holding = 1, False
        await run(graph, game, {"input": other})
        assert st.minutes == start + 5 and st.round_plan["done"] == ["0", "1"]
        assert not st.held and not cast.tellings
        game.end_round()
    finally:
        await game.close()


async def test_real_keeper_lookup_in_next_plan_is_not_silently_dropped_or_executed(fake):
    m = module()
    cast = Cast(config.Config(roles={role: WRITER for role in config.DEFAULT_ROLES}), m)
    game, graph = make(fake, cast, m=m)
    original = "先收起工具，再查明关门时间后等一会儿。"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": "周衡收起工具"}, "remaining": "查明关门时间后等一会儿。"},
        {"needs_roll": False, "minutes": 1},
        {"kind": "tell", "telling": {"scene": {}, "text": "工具已经收好。", "continuation": {"next": {
            "lookup": "今晚钟楼何时关门", "action": {"kind": "wait", "summary": "周衡等一会儿"}}}}},
    ])
    try:
        _, offers = await run(graph, game, {"input": original})
        st = game.state
        assert not offers and not SCRIPT and st.turn == 1 and not st.pending_turn
        assert st.minutes == 20 * 60 + 21 and len(SEEN) == 3
        assert st.usage["plan"]["calls"] == 1 and st.usage["keeper"]["calls"] == 1
        note = st.unfinished[st.investigator.id]
        assert note["remaining"] == "查明关门时间后等一会儿。" and note["reason"]
        assert [e.text for e in st.log if e.kind == "player"] == [original]
    finally:
        await game.close()
        await cast.close()


@pytest.mark.parametrize("next_kind", ["wait", "move"])
async def test_an_original_dice_command_applies_only_to_the_first_segment(fake, next_kind):
    original = ".ra 侦查"
    first = Action(kind="investigate", summary="周衡查看刻痕", target="刻痕")
    tail = (Action(kind="wait", summary="周衡原地等两分钟") if next_kind == "wait" else
            Action(kind="move", summary="周衡前往广场", destination="square"))
    cast = RequestCast({original: Plan(action=first, remaining=tail.summary)}, {
        first.summary: Ruling(needs_roll=True, skill="侦查", minutes=1),
        tail.summary: Ruling(needs_roll=False, minutes=2, rest=True),
    }, [telling(next=Plan(action=tail)), telling()])
    normal = cast.arbiter

    async def requested(state, action, at=None, **kwargs):
        ruling = await normal(state, action, at, **kwargs)
        # A rules assistant may grant a requested roll; the host must not repeat that request.
        return ruling.model_copy(update={"needs_roll": True, "skill": action.asked.skill}) if action.asked else ruling

    cast.arbiter = requested
    game, graph = make(fake, cast)
    game.state.add("speech", "先查看刻痕，之后等两分钟或去广场。", who=game.state.investigator.name)
    fake.queue(1)
    try:
        events, offers = await run(graph, game, {"input": original})
        assert not offers and not fake.values and not game.state.pending_turn and game.state.turn == 2
        assert cast.planned == [original] and cast.ruled[0].asked.skill == "侦查"
        actions = [e for e in events if e["type"] == "action"]
        assert len(actions) == 2 and actions[0]["asked"]["skill"] == "侦查" and actions[1]["asked"] is None
        assert len(game.state.checks) == 1 and game.state.checks[0].skill == "侦查"
        assert len([e for e in events if e["type"] == "dice"]) == 1
        assert not any(e["type"] == "waived" for e in events)
        assert [e.text for e in game.state.log if e.kind == "player"] == [original]
        if next_kind == "wait":
            assert cast.ruled[-1].asked is None and game.state.minutes == 20 * 60 + 23
        else:
            assert game.state.location == "square"
    finally:
        await game.close()
