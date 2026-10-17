"""Engine and turn-graph tests with scripted agents and dice (no model calls)."""

from __future__ import annotations

import io

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from PIL import Image

from autokeeper.agents import guard
from autokeeper.agents.cast import Action, Ruling, Sketch
from autokeeper.config import EXAMPLE
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import parse_time
from autokeeper.module.schema import Module
from autokeeper.rules.coc import Rules


class FakeCast:
    def __init__(self, script: dict[str, tuple[Action, Ruling]]):
        self.script = script
        self.npc_calls: list[tuple[str, str]] = []
        self.performances: list[str] = []
        self.reports: list[str] = []

    async def intent(self, state, text):
        return self.script[text][0]

    async def arbiter(self, state, action, at=None):
        self.ruled_at = at
        return next(r for a, r in self.script.values() if a == action)

    async def npc(self, state, npc_id, message):
        self.npc_calls.append((npc_id, message))
        return f"{npc_id} 的回答"

    async def perform(self, state, npc_id, instruction, deeds):
        self.performances.append(deeds)
        return "诸位，钟声会替我们作证……"

    async def keeper(self, state, report, on_delta=None):
        self.reports.append(report)
        if on_delta:
            await on_delta("叙事")
        return "叙事"

    async def chronicle(self, state, entries):
        return "摘要"

    async def lore(self, state, query):
        return ""


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


def make(module, fake, script, location: str | None = None):
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    if location:
        state.location = location
    cast = FakeCast(script)
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    game = Game(module, state, cast, Rules(dice=fake), emit=emit)  # type: ignore[arg-type]
    return game, cast, events


async def run_turn(app, text, thread, resume=None):
    out, interrupts = [], []
    async for mode, chunk in app.astream({"input": text}, thread, stream_mode=["custom", "updates"]):
        if mode == "custom":
            out.append(chunk)
        elif "__interrupt__" in chunk:
            interrupts.append(chunk["__interrupt__"][0].value)
    if interrupts and resume:
        async for mode, chunk in app.astream(Command(resume=resume), thread, stream_mode=["custom", "updates"]):
            if mode == "custom":
                out.append(chunk)
    return out, interrupts


async def test_social_check_unlocks_npc_clue_handout_and_flag(module, fake):
    talk = Action(kind="talk", summary="周衡劝守钟人说实话", npc="warden", speech="钟里到底有什么？", approach="说服")
    ruling = Ruling(needs_roll=True, skill="说服", difficulty="困难", clues_on_success=["c_diary"], set_flags=["bell_stopped"], minutes=15)
    game, cast, _ = make(module, fake, {"ask": (talk, ruling)}, location="tower")
    fake.queue(8).queue_totals(4)  # 说服 40 → extreme success; the flag ends the game, 理智 +1D6 = 4
    app = turn_graph.build(game, InMemorySaver())
    events, interrupts = await run_turn(app, "ask", {"configurable": {"thread_id": "t1"}})
    st = game.state
    assert not interrupts
    assert st.clues == ["c_diary"] and st.handouts == ["h_diary"] and "bell_stopped" in st.flags
    assert st.npc("warden").unlocked == ["c_diary"]
    assert cast.npc_calls and "已解锁" in cast.npc_calls[0][1]
    assert st.ending == "stopped"
    kinds = [e["type"] for e in events]
    assert kinds.index("dice") < kinds.index("clue") < kinds.index("npc") < kinds.index("keeper_end")
    assert "获得线索〔守钟人的日记〕" in cast.reports[-1]


async def test_failed_core_clue_offers_push_through_interrupt(module, fake):
    look = Action(kind="investigate", summary="周衡翻旧报", target="旧报合订本")
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=30)
    game, _, _ = make(module, fake, {"look": (look, ruling)}, location="office")
    fake.queue(90, 3)  # fail, then the pushed roll succeeds
    app = turn_graph.build(game, InMemorySaver())
    events, interrupts = await run_turn(app, "look", {"configurable": {"thread_id": "t2"}}, resume="push")
    assert interrupts and interrupts[0]["skill"] == "图书馆使用" and interrupts[0]["can_push"]
    assert game.state.clues == ["c_archive"]
    dice = [e for e in events if e["type"] == "dice"]
    assert len(dice) == 2 and dice[1]["pushed"]


async def test_arbiter_cannot_hand_out_clues_from_elsewhere(module, fake):
    look = Action(kind="investigate", summary="周衡在广场四处看看")
    ruling = Ruling(needs_roll=False, auto_clues=["c_gears", "c_archive"], minutes=10)
    game, *_ = make(module, fake, {"x": (look, ruling)})
    turn = await game.resolve(look, ruling)
    assert turn.clues == [] and game.state.clues == []


async def test_walking_somewhere_to_act_moves_the_party_first(module, fake):
    look = Action(kind="investigate", summary="周衡走到钟楼楼梯口查看齿轮间", target="齿轮", destination="tower")
    ruling = Ruling(needs_roll=False, auto_clues=["c_gears"], minutes=10)
    game, cast, events = make(module, fake, {"我去钟楼看看齿轮": (look, ruling)})
    assert await game.rule(look) == ruling and cast.ruled_at == "tower"  # ruled with the tower's clue list
    fake.queue(10)  # the gears call for a sanity check
    turn = await game.resolve(look, ruling)
    assert game.state.location == "tower" and turn.arrived == "tower" and turn.clues == ["c_gears"]
    assert [e["name"] for e in events if e["type"] == "move"] == ["钟楼"]
    gaze = Action(kind="investigate", summary="周衡远远望着报社", destination="nowhere")
    assert game._heading(gaze) is None  # unknown places do not move anyone


async def test_narration_sets_the_sky_and_it_passes(module, fake):
    game, cast, events = make(module, fake, {})
    lines = iter(["钟楼背后滚过一声闷雷，雨点砸在广场的石板上。", "守钟人点起油灯。", "守钟人又打了个盹。"])

    async def keeper(state, report, on_delta=None):
        return next(lines)

    cast.keeper = keeper
    await game._narrate("报告")
    assert game.state.sky == "storm" and {"type": "sky", "sky": "storm"} in events
    game.state.minutes += 60
    await game._narrate("报告")  # not mentioned, still within the hour
    assert game.state.sky == "storm"
    game.state.minutes += 200
    await game._narrate("报告")  # unmentioned for hours: the storm has passed
    assert game.state.sky == "" and events[-1] == {"type": "sky", "sky": ""}


async def test_travel_time_and_timed_events(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    move = Action(kind="move", summary="去报社", destination="office")
    await game.resolve(move, Ruling(needs_roll=False, minutes=0))
    assert game.state.location == "office" and game.state.clock == "D1 14:30"  # two hops
    game.state.minutes = parse_time("D1 17:59")
    turn = await game.resolve(Action(kind="wait", summary="等待"), Ruling(needs_roll=False, minutes=5))
    assert [e.id for e in turn.events] == ["e_dusk"]


async def test_timed_event_moves_party_and_npc_performs(module, fake):
    game, cast, events = make(module, fake, {}, location="office")
    game.state.add("player", "我去报社翻三十年前的旧报", who="周衡")
    game.state.minutes = parse_time("D1 20:55")
    fake.queue(30)  # sanity check during the sermon
    turn = await game.resolve(Action(kind="wait", summary="等待"), Ruling(needs_roll=False, minutes=10))
    assert {"e_dusk", "e_sermon"} <= {e.id for e in turn.events}
    assert game.state.location == "square"
    await game.talk(turn)
    assert turn.performance and "旧报" in cast.performances[0]
    assert game.state.npc(turn.performance[0]).met  # the map lists who you have met
    assert any(e["type"] == "performance" for e in events)


async def test_endings_by_flag_and_by_clock(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    stop = Action(kind="use", summary="周衡拉下钟楼的制动杆")
    fake.queue_totals(4)  # 理智 +1D6 reward
    turn = await game.resolve(stop, Ruling(needs_roll=False, set_flags=["bell_stopped"], minutes=5))
    assert game.state.ending == "stopped" and turn.ending == "stopped"
    assert game.state.investigator.san == 59

    game2, *_ = make(module, fake, {})
    game2.state.minutes = parse_time("D1 21:59")
    fake.queue(50)  # the sermon's sanity check fires on the way
    await game2.resolve(Action(kind="wait", summary="等待"), Ruling(needs_roll=False, minutes=5))
    assert game2.state.ending == "toll"


async def test_sketch_that_looks_like_the_thing_costs_sanity(module, fake):
    game, cast, events = make(module, fake, {}, location="tower")

    async def sketch(state, png, asked):
        assert png.startswith(b"\x89PNG") and "螺旋" in asked
        return Sketch(description="一圈分成三股的螺旋藤", resemblance=0.82)

    cast.sketch = sketch  # type: ignore[attr-defined]
    fake.queue(70).queue_totals(3)  # failed sanity check, lose 3
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    await game.sketch(buf.getvalue(), "sk_coil")
    assert game.state.investigator.san == 52
    assert any(e["type"] == "sanity" for e in events)
    assert "画得太像了" in cast.reports[-1]


def test_leak_guard():
    secret = "他在钟里藏了东西，每到午夜它就多敲一下"
    assert guard.leaks("实话跟您说，我在钟里藏了东西，每到午夜……", {"s": secret}) == ["s"]
    assert guard.leaks("钟只是老了，您别多想。", {"s": secret}) == []
