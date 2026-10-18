"""Engine and turn-graph tests with scripted agents and dice (no model calls)."""

from __future__ import annotations

import asyncio
import io

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from PIL import Image

from autokeeper.agents import guard
from autokeeper.agents.cast import Action, Ruling, Sketch
from autokeeper.config import EXAMPLE
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import LATER, PLAY_WAIT, Game
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


def test_ai_players_know_where_the_people_they_met_are(module, fake):
    from autokeeper.engine.autoplay import situation

    game, *_ = make(module, fake, {}, location="office")
    assert "见过但不在这里的人" not in situation(module, game.state)
    game.state.npc("warden").met = True
    first, second = situation(module, game.state).split("\n")[:2]
    assert "老守钟人（在钟楼）" in second and "见过" not in first  # the first line is what they remember


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


async def test_a_night_of_sleep_passes_but_other_actions_are_capped(module, fake):
    game, *_ = make(module, fake, {})
    start = game.state.minutes
    await game.resolve(Action(kind="investigate", summary="翻遍告示栏"), Ruling(needs_roll=False, minutes=600))
    assert game.state.minutes - start == 180  # searching cannot swallow the day
    fake.queue(10, 10)  # the evening sermon calls for a sanity check
    turn = await game.resolve(Action(kind="wait", summary="回客栈睡到第二天"), Ruling(needs_roll=False, minutes=600))
    assert game.state.minutes - start == 180 + 600
    assert {"e_dusk", "e_sermon"} <= {e.id for e in turn.events}  # everything due on the way fires
    said = Action(kind="talk", summary="周衡招呼同伴：先回去睡一觉，明早再来")  # a rest, though phrased as talk
    await game.resolve(said, Ruling(needs_roll=False, minutes=500, rest=True))
    assert game.state.minutes - start == 180 + 600 + 500


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


# -- improvisation ----------------------------------------------------------------------
from autokeeper.agents.director import Improv, NewClue, NewEvent, NewFlag, NewLocation, NewNPC  # noqa: E402


class Improvising(FakeCast):
    """A cast whose director follows a script of plans."""

    improvising = True

    def __init__(self, script, plans):
        super().__init__(script)
        self.plans = list(plans)
        self.asked: list[tuple[str, str, list[str] | None]] = []

    async def director(self, state, kind, *, action="", reason="", at="", problems=None):
        self.asked.append((kind, at, problems))
        return self.plans.pop(0) if self.plans else None


def improvised(module, fake, script, plans, location=None):
    game, _, events = make(module, fake, script, location=location)
    cast = Improvising(script, plans)
    game.cast = cast
    return game, cast, events


NEWSSTAND = Improv(
    kind="展开",
    reason="玩家想打听传闻，补一个报童，把人引回钟楼",
    locations=[NewLocation(id="newsstand", name="街角报摊", read_aloud="街角支着一个报摊。", exits=["square"])],
    npcs=[NewNPC(id="paperboy", name="报童小六", role="卖报的孩子", location="newsstand", knows=["半夜钟楼里有脚步声"])],
    clues=[NewClue(id="c_rumor", title="报童的传闻", text="报童说半夜钟楼里有脚步声。", where="square")],
    give=["c_rumor"],
    note="一个报童凑上来兜售传闻，顺势把人引向钟楼",
)


async def test_a_sensible_unwritten_action_opens_a_new_line(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    ruling = Ruling(needs_roll=False, minutes=10, scope="可即兴", reason="场景里没有可以打听的路人")
    game, cast, events = improvised(module, fake, {"x": (ask, ruling)}, [NEWSSTAND])
    steered = await game.rule(ask)
    assert cast.asked == [("展开", "square", [])] and steered.auto_clues == ["c_rumor"]
    turn = await game.resolve(ask, steered)
    assert game.state.clues == ["c_rumor"]  # not refused: the investigator walks away with something
    assert "newsstand" in module.location("square").exits and module.npc("paperboy").location == "newsstand"
    assert any("报童凑上来" in n for n in turn.notes)  # the keeper is told how to tell it
    assert [p["kind"] for p in game.state.improv] == ["展开"]
    assert any(e["type"] == "improv" and "报童小六" in e["summary"] for e in events)


async def test_an_outrageous_action_plays_out_and_costs_something(module, fake):
    phone = Action(kind="other", summary="周衡掏出手机给警察打电话")
    ruling = Ruling(needs_roll=False, minutes=5, scope="出格", reason="那个年代没有手机")
    plan = Improv(
        kind="后果",
        reason="时代不对：让他摸个空，旁人把他当成疯子",
        flags=[NewFlag(id="seen_as_mad", meaning="镇上的人觉得调查员神神叨叨")],
        events=[NewEvent(id="e_mad", title="摸了个空", when="now", read_aloud="他在口袋里摸了半天，什么也没有。", damage="1D2", sets=["seen_as_mad"])],
        note="让他尴尬地摸个空，用旁人的眼光说明这样行不通",
    )
    game, _, events = improvised(module, fake, {"x": (phone, ruling)}, [plan])
    hp = game.state.investigator.hp
    fake.queue_totals(1)  # the 1D2 of damage
    turn = await game.resolve(phone, await game.rule(phone))
    assert "seen_as_mad" in game.state.flags and "e_mad" in game.state.fired
    assert game.state.investigator.hp == hp - 1 and any(e["type"] == "damage" for e in events)
    assert [e.id for e in turn.events] == ["e_mad"]


async def test_the_opening_weaves_in_the_investigators(module, fake):
    friend = Improv(
        kind="开局改编",
        reason="把调查员的老同事写进来",
        npcs=[NewNPC(id="old_editor", name="老编辑钱伯", role="周衡在报社的老上司", location="office", knows=["三十年前也有人报道过多出的钟声"])],
        note="周衡的老上司钱伯也在镇上，开场时可以提一句",
    )
    game, cast, _ = improvised(module, fake, {}, [friend])
    await game.start()
    assert cast.asked[0][0] == "开局改编" and module.npc("old_editor").location == "office"
    assert "钱伯" in cast.reports[-1]  # the opening narration knows about it


async def test_a_plan_that_does_not_fit_gets_one_chance_then_is_dropped(module, fake):
    broken = NEWSSTAND.model_copy(update={"clues": [NewClue(id="c_rumor", title="传闻", text="……", where="nowhere")]})
    ask = Action(kind="investigate", summary="周衡向路人打听传闻")
    ruling = Ruling(needs_roll=False, scope="可即兴")
    game, cast, _ = improvised(module, fake, {"x": (ask, ruling)}, [broken, broken])
    assert await game.rule(ask) == ruling  # nothing usable came of it: the turn goes on as ruled
    assert cast.asked[1][2] and "nowhere" in cast.asked[1][2][0]  # the second call saw what was wrong
    assert not game.state.improv and not any(loc.id == "newsstand" for loc in module.locations)


class SlowDirector(Improvising):
    """A director that takes longer than a turn will wait."""

    async def director(self, state, kind, **kw):
        await asyncio.sleep(0.2)
        return await super().director(state, kind, **kw)


async def test_a_slow_director_does_not_hold_up_the_turn(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    look = Action(kind="investigate", summary="周衡四下看看")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴")), "y": (look, Ruling(needs_roll=False))}
    game, *_ = make(module, fake, script)
    game.cast, game.director_wait = SlowDirector(script, [NEWSSTAND]), 0.01
    steered = await game.rule(ask)
    assert steered.note == LATER and not steered.auto_clues  # the keeper tells what happens now
    await game.resolve(ask, steered)
    assert not game.state.improv and len(game.backstage) == 1
    await asyncio.sleep(0.3)
    await game.rule(look)  # the plan lands before the next action is ruled
    assert [p["kind"] for p in game.state.improv] == ["展开"] and module.npc("paperboy").location == "newsstand"
    turn = await game.resolve(look, Ruling(needs_roll=False))
    assert turn.clues == ["c_rumor"] and any("报童凑上来" in n for n in turn.notes)  # and that turn's narration tells it


async def test_a_finished_plan_plays_out_between_turns(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴"))}
    game, *_ = make(module, fake, script)
    cast = SlowDirector(script, [NEWSSTAND])
    game.cast, game.director_wait = cast, PLAY_WAIT
    await game.narrate(await game.resolve(ask, await game.rule(ask)))
    assert await game.interlude() is None  # still being written
    await asyncio.sleep(0.3)
    turn = await game.interlude()  # nobody has to act for it to happen
    assert turn and turn.interlude and "c_rumor" in game.state.clues
    assert "插曲" in cast.reports[-1] and "报童凑上来" in cast.reports[-1]


async def test_the_opening_does_not_wait_for_the_director_at_a_screen(module, fake):
    friend = Improv(kind="开局改编", reason="把老上司写进来", npcs=[NewNPC(id="old_editor", name="老编辑钱伯", role="老上司", location="office")])
    look = Action(kind="investigate", summary="周衡四下看看")
    game, _, _ = make(module, fake, {"y": (look, Ruling(needs_roll=False))})
    game.cast, game.director_wait = SlowDirector({"y": (look, Ruling(needs_roll=False))}, [friend]), PLAY_WAIT
    await game.start()  # the opening is told right away
    assert not game.state.improv and game.backstage
    await asyncio.sleep(0.3)
    await game.rule(look)
    assert module.npc("old_editor").location == "office" and game.state.improv[0]["kind"] == "开局改编"


async def test_the_first_beat_shows_before_the_ruling(module, fake):
    look = Action(kind="investigate", summary="周衡翻看告示栏", echo="周衡凑近告示栏")
    game, cast, events = make(module, fake, {"x": (look, Ruling(needs_roll=False))})
    action = await game.parse("x")
    assert events[-1] == {"type": "echo", "text": "周衡凑近告示栏"}
    await game.narrate(await game.resolve(action, await game.rule(action)))
    assert "「周衡凑近告示栏」" in cast.reports[-1]  # the keeper carries on from it


async def test_improvised_branches_survive_a_reload(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听传闻")
    game, *_ = improvised(module, fake, {"x": (ask, Ruling(needs_roll=False, scope="可即兴"))}, [NEWSSTAND])
    await game.resolve(ask, await game.rule(ask))
    fresh = Module.load(EXAMPLE)
    Game(fresh, game.state, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
    assert fresh.location("newsstand").exits == ["square"] and "newsstand" in fresh.location("square").exits
    assert fresh.clue("c_rumor").where == "square"


async def test_a_stuck_party_gets_a_lead(module, fake):
    lead = Improv(
        kind="推进",
        reason="一直在广场打转，送来一封信指向报社",
        events=[NewEvent(id="e_letter", title="门缝里的信", when="now", read_aloud="有人把一封信塞到你手里：去报社查三十年前的旧报。")],
        note="一封没署名的信把人引向报社",
    )
    wait = Action(kind="wait", summary="周衡在广场上发呆")
    game, cast, _ = improvised(module, fake, {"x": (wait, Ruling(needs_roll=False, minutes=5))}, [lead])
    game.state.turn = 12  # nothing found since the start
    turn = await game.resolve(wait, await game.rule(wait))
    assert cast.asked[0][0] == "推进" and "e_letter" in game.state.fired
    assert game.state.progress_at == 12 and any("没署名的信" in n for n in turn.notes)


async def test_a_failed_roll_does_not_sit_on_a_core_clue(module, fake):
    look = Action(kind="investigate", summary="周衡查看齿轮间")
    ruling = Ruling(needs_roll=True, skill="侦查", difficulty="困难", clues_on_success=["c_gears"], minutes=10)
    game, *_ = make(module, fake, {"x": (look, ruling)}, location="tower")
    fake.queue(99)  # a plain failure: a push is offered first
    turn = await game.resolve(look, ruling)
    assert turn.push and "c_gears" not in game.state.clues
    before = game.state.minutes
    fake.queue(10)  # the gears' sanity check
    dropped = await game.push("drop")
    assert "c_gears" in game.state.clues and game.state.minutes - before == 20  # found, but it cost time
    assert any("关键线索不能卡住" in n for n in dropped.notes)
