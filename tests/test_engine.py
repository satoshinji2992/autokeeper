"""Engine and turn-graph tests with scripted agents and dice (no model calls)."""

from __future__ import annotations

import asyncio
import io

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from PIL import Image

from autokeeper import paths
from autokeeper.agents import guard
from autokeeper.agents.cast import Action, Ruling, Sketch
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import PLAY_WAIT, Game, Turn
from autokeeper.engine.state import parse_time
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.rules.commands import Asked


class FakeCast:
    def __init__(self, script: dict[str, tuple[Action, Ruling]]):
        self.script = script
        self.heard: list[str] = []  # what the intent agent was given
        self.ruled: list[Action] = []  # what the arbiter was given
        self.npc_calls: list[tuple[str, str]] = []
        self.performances: list[str] = []
        self.reports: list[str] = []

    async def intent(self, state, text):
        self.heard.append(text)
        return self.script[text][0]

    async def arbiter(self, state, action, at=None):
        self.ruled_at = at
        self.ruled.append(action)
        return next(r for a, r in self.script.values() if a.summary == action.summary)

    async def npc(self, state, npc_id, message):
        self.npc_calls.append((npc_id, message))
        return f"{npc_id} 的回答"

    async def perform(self, state, npc_id, instruction, deeds):
        self.performances.append(deeds)
        return "诸位，钟声会替我们作证……"

    async def keeper(self, state, report, on_delta=None, lore=""):
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


async def test_locked_clue_cannot_leak_its_handout_even_if_an_agent_requests_it(module, fake):
    clue = module.clue("c_diary")
    clue.requires = ["c_notice", "flag:ready"]
    module.flags["ready"] = "已实际发生的前置事件"
    game, _, events = make(module, fake, {}, location=clue.where)
    assert game._valid_clues([clue.id], None) == []
    await game._gain([clue.id], Turn(), None)
    assert not game.state.clues and not game.state.handouts and not events
    game.state.flags.append("ready")
    assert game._valid_clues([clue.id], None) == []
    game.state.clues.append("c_notice")
    assert game._valid_clues([clue.id], None) == [clue.id]
    await game._gain([clue.id], Turn(), None)
    assert clue.handout in game.state.handouts
    assert [event["type"] for event in events] == ["clue", "handout", "sheet"]


async def test_start_fires_the_starting_locations_arrival_event_once(module, fake):
    module.events.append(Event(id="arrival", title="必发开场", when=f"visit:{module.start}", text="事实", read_aloud="两位访客"))
    game, cast, events = make(module, fake, {})
    await game.start()
    assert "arrival" in game.state.fired and "两位访客" in cast.reports[0]
    await game._arrive(module.start, Turn())
    assert sum(event["type"] == "event" and event.get("id") == "arrival" for event in events) == 1


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
    assert "见过的人" not in situation(module, game.state)
    game.state.npc("warden").met = True
    first, *rest = situation(module, game.state).split("\n")
    met = next(line for line in rest if line.startswith("见过的人"))
    assert "老守钟人（钟楼的守钟人，在钟楼）" in met and "见过" not in first  # the first line is what they remember


async def test_narration_sets_the_sky_and_it_passes(module, fake):
    game, cast, events = make(module, fake, {})
    lines = iter(["钟楼背后滚过一声闷雷，雨点砸在广场的石板上。", "守钟人点起油灯。", "守钟人又打了个盹。"])

    async def keeper(state, report, on_delta=None, lore=""):
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
    assert game.state.location == "office" and game.state.clock == "D1 14:20"  # two fixed ten-minute roads
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
    assert turn.performance and "旧报" not in cast.performances[0]  # the performer did not witness that private trip
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


def test_a_shared_place_name_is_not_a_leak_and_a_real_one_is_cut_out():
    """The station clerk gives the way to 218 Aylesbury Street, which the truth also names:
    that is directions, not a secret. What only the secret says still counts."""
    truth = {"truth": "道格拉斯没有死，他住在艾尔斯伯里大街 218 号后面公墓底下的隧道里，夜里回来偷自己的书。"}
    knows = "出站沿路到镇中心，再沿艾尔斯伯里大街走，218 号金博尔家在公墓旁"
    directions = "您顺着这条路到镇中心，再沿艾尔斯伯里大街一直走，218 号就在公墓旁边。"
    assert guard.leaks(directions, truth) == ["truth"]  # without knowing what is public: a false alarm
    assert guard.leaks(directions, truth, public=knows) == []
    slip = directions + "听说道格拉斯没有死，他住在公墓底下的隧道里。"
    assert guard.leaks(slip, truth, public=knows) == ["truth"]
    assert guard.redact(slip, truth, public=knows) == directions  # the safe sentence stays


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


async def test_opening_is_told_once_from_the_arrival_with_the_investigators_background(module, fake):
    game, cast, events = make(module, fake, {})
    game.state.investigator.backstory = {"信念": "答应的事一定办到"}
    await game.start()
    assert [e.text for e in game.state.log if e.kind in ("opening", "keeper")] == ["叙事"]
    assert not any(e["type"] == "opening" for e in events)
    assert module.opening.strip() in cast.reports[-1] and "答应的事一定办到" in cast.reports[-1]
    assert game.state.location == module.start and game.state.visited == [module.start]


async def test_paper_chase_arrives_at_the_station_and_can_walk_or_stop_along_the_way(fake):

    m = Module.load(paths.MODULES / "zhuishuren")
    game, cast, _ = make(m, fake, {})
    await game.start()
    assert game.state.location == "huochezhan" and game.state.visited == ["huochezhan"]
    assert not game.state.clues and not game.state.npcs
    assert "蒸汽" in cast.reports[-1] and "托马斯把你领进" not in cast.reports[-1]
    go = Action(kind="move", summary="哈罗德直接按信上的地址去托马斯家", destination="jinboer_jia")
    start = game.state.minutes
    turn = await game.resolve(go, Ruling(needs_roll=False, minutes=0))
    assert turn.route == ["huochezhan", "zhenzhongxin", "jiefang", "jinboer_jia"]
    assert game.state.minutes - start == 45 and not game.state.clues
    report = game._report(turn)
    assert "【沿途】阿诺兹堡火车站 → 阿诺兹堡镇中心 → 艾尔斯伯里大街 → 金博尔家" in report
    assert "图书馆的石阶" in report and "莉拉·奥黛尔" not in report.split("【进入新地点")[0]
    assert "食尸鬼" not in report.split("【进入新地点")[0]
    game, _, _ = make(m, fake, {})
    turn = await game.resolve(Action(kind="move", summary="先去镇中心", destination="zhenzhongxin"), Ruling(needs_roll=False, minutes=0))
    assert game.state.location == "zhenzhongxin" and "jinboer_jia" not in game.state.visited


async def test_daily_life_is_not_stuck_and_npcs_can_chat_without_giving_clues(module, fake):
    chat = Action(kind="daily", summary="周衡陪守钟人喝茶聊家常", npc="warden", speech="今天忙不忙？")
    ruling = Ruling(needs_roll=False, minutes=20)
    game, cast, events = improvised(module, fake, {}, [])
    game.state.location, game.state.turn, game.state.progress_at = "tower", 10, 0
    turn = await game.resolve(chat, ruling)
    await game.talk(turn)
    await game.narrate(turn)
    assert game.state.progress_at == 10 and cast.asked == []
    assert len(cast.npc_calls) == 1 and cast.npc_calls[0][0] == "warden"
    assert "今天忙不忙？" in cast.npc_calls[0][1] and "尚无可公开的门槛线索" in cast.npc_calls[0][1]
    assert not game.state.clues and not turn.checks and not any(e["type"] == "dice" for e in events)
    assert not cast.reports and "【日常】" in game._report(turn)  # the NPC's reply stands without a second retelling


@pytest.mark.parametrize("kind", ["talk", "daily"])
async def test_a_conversation_does_not_get_a_second_narration(module, fake, kind):
    action = Action(kind=kind, summary="询问守钟人", npc="warden", speech="钟楼几点关门？")
    game, cast, events = make(module, fake, {}, location="tower")
    turn = await game.resolve(action, Ruling(needs_roll=False, minutes=0))
    await game.talk(turn)
    assert await game.narrate(turn) == ""
    assert len(cast.npc_calls) == 1 and not cast.reports
    assert [ev["type"] for ev in events].count("npc") == 1
    assert not any(ev["type"] == "keeper_start" for ev in events)


async def test_a_conversation_keeps_new_world_events_without_repeating_the_reply(module, fake):
    game, cast, _ = make(module, fake, {}, location="tower")
    turn = Turn(npc_lines=[("warden", "钟楼晚上八点关门。")], world=["守钟人放下工具，开始检查门锁"])
    await game.narrate(turn)
    assert "开始检查门锁" in cast.reports[-1]
    assert "钟楼晚上八点关门" not in cast.reports[-1]
    assert "不要引用、概述或解释刚才的对话" in cast.reports[-1]


async def test_daily_life_still_takes_time_and_triggers_scheduled_events(module, fake):
    game, _, events = make(module, fake, {})
    game.state.minutes = 17 * 60 + 50
    action = Action(kind="daily", summary="周衡坐在广场吃晚饭")
    turn = await game.resolve(action, Ruling(needs_roll=False, minutes=20))
    assert game.state.minutes == 18 * 60 + 10 and "e_dusk" in game.state.fired
    assert [e.id for e in turn.events] == ["e_dusk"]
    assert any(e["type"] == "event" for e in events)


async def test_a_plan_that_does_not_fit_is_written_again_then_the_action_settles_without_it(module, fake):
    broken = NEWSSTAND.model_copy(update={"clues": [NewClue(id="c_rumor", title="传闻", text="……", where="nowhere")]})
    ask = Action(kind="investigate", summary="周衡向路人打听传闻")
    ruling = Ruling(needs_roll=False, scope="可即兴")
    game, cast, _ = improvised(module, fake, {"x": (ask, ruling)}, [broken, broken])
    assert await game.rule(ask) is not None  # no usable scene: the action still settles, no /retry
    assert cast.asked[1][2] and "nowhere" in cast.asked[1][2][0]  # the second call saw what was wrong
    assert len(cast.asked) == 3  # then a fresh plan was asked for (this director had nothing more)
    assert not game.state.improv and not any(loc.id == "newsstand" for loc in module.locations)


class FailingDirector(Improvising):
    """A director whose every call fails outright."""

    async def director(self, state, kind, **kw):
        self.asked.append((kind, kw.get("at", ""), kw.get("problems")))
        raise ConnectionError("service down")


async def test_a_scene_that_cannot_be_prepared_in_time_lets_the_action_settle_once(module, fake, monkeypatch):
    import autokeeper.engine.game as engine

    monkeypatch.setattr(engine, "PREPARE_LIMIT", 0.3)
    monkeypatch.setattr(engine, "PREPARE_PAUSE", 0.05)
    ask = Action(kind="investigate", summary="周衡向路人打听传闻")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴"))}
    game, _, _ = make(module, fake, script)
    game.cast = cast = FailingDirector(script, [])
    game.state.pending_turn = {"input": "x"}
    before = game.state.minutes
    ruling = await game.rule(ask)
    assert ruling is not None and 1 < len(cast.asked) < 10  # tried again, with pauses, then gave up
    assert game.state.pending_turn["scene_package"]["given_up"] and game.state.minutes == before
    asked = len(cast.asked)
    assert await game.rule(ask) is not None and len(cast.asked) == asked  # a resumed turn does not wait again


class SlowDirector(Improvising):
    """A director that takes longer than a turn will wait."""

    async def director(self, state, kind, **kw):
        await asyncio.sleep(0.2)
        return await super().director(state, kind, **kw)


async def test_a_current_action_waits_for_its_scene_without_advancing_time(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    look = Action(kind="investigate", summary="周衡四下看看")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴")), "y": (look, Ruling(needs_roll=False))}
    game, _, events = make(module, fake, script)
    game.cast, game.director_wait = SlowDirector(script, [NEWSSTAND]), 0.01
    before = game.state.minutes
    pending = asyncio.create_task(game.rule(ask))
    await asyncio.sleep(0.02)
    assert not pending.done() and game.state.minutes == before and not game.state.improv
    steered = await pending
    assert steered.auto_clues == ["c_rumor"] and not game.backstage
    assert any(e.get("text") == "KP 正在构思…" for e in events)
    assert [p["kind"] for p in game.state.improv] == ["展开"] and module.npc("paperboy").location == "newsstand"
    turn = await game.resolve(ask, steered)
    assert turn.clues == ["c_rumor"] and any("报童凑上来" in n for n in turn.notes)  # and that turn's narration tells it


async def test_a_finished_plan_plays_out_between_turns(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    script = {"x": (ask, Ruling(needs_roll=False, minutes=10, scope="可即兴"))}
    game, *_ = make(module, fake, script)
    cast = SlowDirector(script, [NEWSSTAND])
    game.cast, game.director_wait = cast, PLAY_WAIT
    await game._improvise("推进", at=game.state.location, patience=0.0)
    assert await game.interlude() is None  # still being written
    await asyncio.sleep(0.3)
    turn = await game.interlude()  # nobody has to act for it to happen
    assert turn and turn.interlude and "c_rumor" in game.state.clues
    assert "插曲" in cast.reports[-1] and "报童凑上来" in cast.reports[-1]


async def test_the_opening_waits_for_its_adaptation_before_introducing_the_story(module, fake):
    friend = Improv(kind="开局改编", reason="把老上司写进来", npcs=[NewNPC(id="old_editor", name="老编辑钱伯", role="老上司", location="office")])
    look = Action(kind="investigate", summary="周衡四下看看")
    game, _, _ = make(module, fake, {"y": (look, Ruling(needs_roll=False))})
    game.cast, game.director_wait = SlowDirector({"y": (look, Ruling(needs_roll=False))}, [friend]), PLAY_WAIT
    await game.start()
    assert game.state.improv and not game.backstage
    assert module.npc("old_editor").location == "office" and game.state.improv[0]["kind"] == "开局改编"


async def test_unresolved_flavour_never_becomes_a_player_action(module, fake):
    look = Action(kind="investigate", summary="周衡翻看告示栏", echo="周衡凑近告示栏")
    game, cast, events = make(module, fake, {"x": (look, Ruling(needs_roll=False))})
    action = await game.parse("x")
    assert not action.echo and not any(e["type"] == "echo" for e in events)
    await game.narrate(await game.resolve(action, await game.rule(action)))
    assert "周衡凑近告示栏" not in cast.reports[-1]
    assert "周衡翻看告示栏" in cast.reports[-1]


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
    await game._curtain(wait)
    turn.notes += game._late.notes
    assert cast.asked[0][0] == "推进" and "e_letter" in game.state.fired
    assert game.state.progress_at == 12 and any("没署名的信" in n for n in turn.notes)


async def _asked_on(game, cast, plays):
    """The turns on which the director was asked, playing (turn, action) in order."""
    on = []
    for turn, action in plays:
        game.state.turn = turn
        before = len(cast.asked)
        await game.rule(action)
        on += [turn] * (len(cast.asked) > before)
    return on


async def test_explicit_unwritten_actions_are_not_blocked_by_expansion_cooldown(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    phone = Action(kind="other", summary="周衡掏出手机给警察打电话")
    script = {"x": (ask, Ruling(needs_roll=False, scope="可即兴")), "p": (phone, Ruling(needs_roll=False, scope="出格"))}
    game, cast, _ = improvised(module, fake, script, [])  # the director has nothing to add: asking still counts
    assert await _asked_on(game, cast, [(t, ask) for t in range(1, 8)]) == list(range(1, 8))
    assert game.state.expanded == {"square": 7}
    assert await _asked_on(game, cast, [(8, phone)]) == [8]  # a consequence is not a new line
    game.state.investigators.append(game.state.investigator.model_copy(update={"id": "li", "name": "李雁"}, deep=True))
    assert await _asked_on(game, cast, [(t, ask) for t in range(9, 15)]) == list(range(9, 15))


async def test_moving_on_and_a_stuck_party_are_not_held_back(module, fake):
    ask = Action(kind="investigate", summary="周衡向路人打听镇上关于钟楼的传闻")
    away = Action(kind="move", summary="周衡去找传闻里说的那家茶馆")  # a place the module never wrote
    there = Action(kind="investigate", summary="周衡走进钟楼，打听守钟人的徒弟", destination="tower")
    script = {"x": (ask, Ruling(needs_roll=False, scope="可即兴")), "a": (away, Ruling(needs_roll=False, scope="可即兴")),
              "t": (there, Ruling(needs_roll=False, scope="可即兴"))}  # fmt: skip
    game, cast, _ = improvised(module, fake, script, [])
    assert await _asked_on(game, cast, [(1, ask), (2, away), (3, there), (4, ask)]) == [1, 2, 3, 4]
    assert game.state.expanded == {"square": 4, "tower": 3}
    game.state.turn = game.state.progress_at + 12  # and nothing found for a long while
    await game._nudge(Turn())
    assert cast.asked[-1][0] == "推进"


async def test_a_failed_roll_neither_sits_on_a_core_clue_nor_hands_it_out(module, fake):
    look = Action(kind="investigate", summary="周衡查看齿轮间", target="齿轮")
    ruling = Ruling(needs_roll=True, skill="侦查", difficulty="困难", clues_on_success=["c_gears"], minutes=10)
    game, *_ = make(module, fake, {"x": (look, ruling)}, location="tower")
    fake.queue(99)  # a plain failure: a push is offered first
    turn = await game.resolve(look, ruling)
    assert turn.push and "c_gears" not in game.state.clues
    first = await game.push("drop")
    assert "c_gears" not in game.state.clues and any("尚未取得线索" in n for n in first.notes)
    fake.queue(99)  # looking again, and failing again
    await game.resolve(look, ruling)
    before = game.state.minutes
    fake.queue(10)  # the gears' sanity check
    second = await game.push("drop")
    assert "c_gears" in game.state.clues and game.state.minutes - before == 20  # found the second time, at a cost
    assert any("关键线索不能卡住" in n for n in second.notes)


async def test_a_failed_push_brings_the_core_clue_at_once(module, fake):
    look = Action(kind="investigate", summary="周衡查看齿轮间", target="齿轮")
    ruling = Ruling(needs_roll=True, skill="侦查", difficulty="困难", clues_on_success=["c_gears"], minutes=10)
    game, *_ = make(module, fake, {"x": (look, ruling)}, location="tower")
    fake.queue(99)
    await game.resolve(look, ruling)
    fake.queue(99, 10)  # the pushed roll fails too; then the gears' sanity check
    await game.push("push")
    assert "c_gears" in game.state.clues


async def test_a_look_around_does_not_empty_a_room(module, fake):
    vague = Action(kind="investigate", summary="周衡在钟楼里四处查看")
    ruling = Ruling(needs_roll=False, auto_clues=["c_gears", "c_diary"], minutes=10)
    game, *_ = make(module, fake, {"x": (vague, ruling)}, location="tower")
    fake.queue(10, 10)  # sanity checks that finding the clues may call for
    await game.resolve(vague, ruling)
    assert len(game.state.clues) == 1  # one plain thing; the rest waits for a closer look
    pointed = Action(kind="investigate", summary="周衡翻看齿轮旁的日记", target="日记")
    await game.resolve(pointed, ruling)
    assert set(game.state.clues) == {"c_gears", "c_diary"}


# -- dice commands -----------------------------------------------------------------------
SCARE = Action(kind="talk", summary="周衡逼近一步，威吓守钟人说出钟里的事", target="老守钟人", npc="warden", approach="恐吓")


async def test_a_dice_check_goes_to_the_keeper_and_the_npc_answers_it(module, fake):
    """`.ra 恐吓` is the investigator's attempt, read in context and ruled like any action:
    rolled once, and the NPC answers the outcome without a second telling."""
    game, cast, _ = make(module, fake, {".ra 恐吓": (SCARE, Ruling(needs_roll=True, skill="恐吓", minutes=5))}, location="tower")
    fake.queue(52)  # 恐吓 15: a failure
    app = turn_graph.build(game, InMemorySaver())
    events, _ = await run_turn(app, ".ra 恐吓", {"configurable": {"thread_id": "t-dice"}})
    assert cast.heard == [".ra 恐吓"] and cast.ruled[0].asked == Asked(skill="恐吓")  # the request, not the words
    assert [e["text"] for e in events if e["type"] == "dice"] == ["恐吓 15 → 52 失败 ✗"]
    assert not [e for e in events if e["type"] == "command"]
    npc, said = cast.npc_calls[0]
    assert npc == "warden" and "方式：恐吓" in said and "检定：恐吓 失败，本次交涉未达成" in said
    assert not cast.reports and any(e["type"] == "npc" for e in events)
    record = game.state.checks[-1]
    assert (record.skill, record.aim, record.passed, game.state.turn) == ("恐吓", "warden", False, 1)
    cast.script["我盯着他"] = (SCARE.model_copy(update={"summary": "周衡盯着守钟人", "asked": Asked(skill="说服")}), Ruling(needs_roll=False))
    events, _ = await run_turn(app, "我盯着他", {"configurable": {"thread_id": "t-dice"}})
    assert cast.ruled[-1].asked is None  # only a dice command makes a request, whatever the model says
    assert not [e for e in events if e["type"] == "waived"]
    cast.script[".ra 侦查"] = (SCARE.model_copy(update={"kind": "investigate", "summary": "周衡看了看钟楼的门"}), Ruling(needs_roll=False))
    events, _ = await run_turn(app, ".ra 侦查", {"configurable": {"thread_id": "t-dice"}})
    assert [(e["who"], e["skill"]) for e in events if e["type"] == "waived"] == [(game.state.investigator.name, "侦查")]  # said, not swallowed
    assert "玩家要求侦查检定，裁定这次不用掷" in cast.reports[-1]


async def test_a_failed_check_is_not_rolled_again(module, fake):
    plead = Action(kind="talk", summary="周衡换了口气，好言相劝", npc="warden", approach="说服")
    script = {
        ".ra 恐吓": (SCARE, Ruling(needs_roll=True, skill="恐吓", minutes=5)),  # this arbiter wants a roll every time
        ".ra 说服": (plead, Ruling(needs_roll=True, skill="说服", minutes=5)),
    }
    game, cast, _ = make(module, fake, script, location="tower")
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "t-again"}}
    fake.queue(52)  # one die for 恐吓; a second roll would find the fake dice empty
    await run_turn(app, ".ra 恐吓", thread)
    for _ in range(2):  # the same attempt again, and again: the failure stands
        events, _ = await run_turn(app, ".ra 恐吓", thread)
        assert not [e for e in events if e["type"] == "dice"]
        assert [e["text"] for e in events if e["type"] == "repeat"] == ["恐吓 15 → 52 失败 ✗"]
        assert "同样的恐吓刚才已经试过，不重掷，结果照旧：本次请求仍未达成" in cast.npc_calls[-1][1]
        assert not cast.reports  # the repeat notice and NPC reply already convey the result
    fake.queue(30)  # another skill is another attempt
    events, _ = await run_turn(app, ".ra 说服", thread)
    assert [e["skill"] for e in events if e["type"] == "dice"] == ["说服"]


async def test_something_new_makes_it_a_fresh_attempt(module, fake):
    """Only the plain re-roll is stopped: news, time gone or an action in between, and the
    arbiter's call stands (this one asks for a roll every time)."""
    search = Action(kind="investigate", summary="周衡搜查齿轮间", target="齿轮间")
    glance = Action(kind="investigate", summary="周衡四下看看")
    script = {".ra 侦查": (search, Ruling(needs_roll=True, skill="侦查", minutes=10)), "看看": (glance, Ruling(needs_roll=False, minutes=5))}
    game, *_ = make(module, fake, script, location="tower")
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "t-fresh"}}

    async def rolls(text: str) -> int:
        events, _ = await run_turn(app, text, thread)
        return len([e for e in events if e["type"] == "dice"])

    fake.queue(99, 99, 99, 99)  # 侦查 55: failures
    assert await rolls(".ra 侦查") == 1
    assert await rolls(".ra 侦查") == 0  # a plain re-roll
    game.state.clues.append("c_notice")  # something turned up meanwhile
    assert await rolls(".ra 侦查") == 1
    game.state.minutes += 90  # an hour and a half later
    assert await rolls(".ra 侦查") == 1
    assert await rolls("看看") == 0
    assert await rolls(".ra 侦查") == 1  # they did something else in between
    assert not fake.values


async def test_the_arbiter_can_call_a_repeat_itself(module, fake):
    """A repeat the engine's own rule would let through (an action in between) can still be
    called by the arbiter: then the earlier result stands, for the NPC and the keeper too."""
    nod = Action(kind="talk", summary="周衡点点头，没说话", npc="warden")
    again = SCARE.model_copy(update={"summary": "周衡又一次威吓守钟人"})
    script = {
        ".ra 恐吓": (SCARE, Ruling(needs_roll=True, skill="恐吓", minutes=5)),
        "点头": (nod, Ruling(needs_roll=False, minutes=1)),
        "再吓他": (again, Ruling(needs_roll=False, repeat=True, skill="恐吓", clues_on_success=["c_diary"])),
    }
    game, cast, _ = make(module, fake, script, location="tower")
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "t-arbiter"}}
    fake.queue(52)
    for text in (".ra 恐吓", "点头"):
        await run_turn(app, text, thread)
    events, _ = await run_turn(app, "再吓他", thread)
    assert [e["type"] for e in events if e["type"] in ("dice", "repeat")] == ["repeat"]
    assert "c_diary" not in game.state.clues and "本次请求仍未达成" in cast.npc_calls[-1][1]


async def test_a_plain_roll_is_answered_at_once(module, fake):
    game, cast, _ = make(module, fake, {})
    fake.queue_totals(3)
    app = turn_graph.build(game, InMemorySaver())
    events, _ = await run_turn(app, ".r 1d6", {"configurable": {"thread_id": "t-r"}})
    assert [e["text"] for e in events if e["type"] == "command"] == ["1d6 → 3"]
    assert not cast.heard and not cast.reports and game.state.turn == 0  # no agent asked, no turn used


async def test_the_sheet_and_sanity_stay_with_the_keeper(module, fake):
    game, _, events = make(module, fake, {})
    inv = game.state.investigator
    refused = await game.command(".st 侦查99")
    assert refused.kind == "refused" and "不能改人物卡" in refused.text and inv.skills["侦查"] == 55
    assert (await game.command(".sc 1/1d6")).kind == "refused" and inv.san == 55  # nothing rolled
    sheet = await game.command(".st")
    assert sheet.kind == "sheet" and "侦查55" in sheet.text and "理智 55" in sheet.text
    assert [e["kind"] for e in events if e["type"] == "command"] == ["refused", "refused", "sheet"]
    assert game.state.turn == 0 and not game.state.log


async def test_growth_waits_for_the_ending_and_comes_once(module, fake):
    game, *_ = make(module, fake, {})
    inv = game.state.investigator
    inv.marks = ["侦查"]
    early = await game.command(".en")
    assert early.kind == "refused" and "结局" in early.text and inv.marks == ["侦查"]
    game.state.ending = "stopped"
    fake.queue_totals(80, 6)  # 80 > 55: 侦查 grows by 1D10 = 6
    grown = await game.command(".en 图书馆使用")  # only what was ticked in play grows
    assert grown.kind == "develop" and inv.skills["侦查"] == 61 and inv.skills["图书馆使用"] == 60
    assert "已经做过" in (await game.command(".en")).text


# -- the player's own roll ----------------------------------------------------------------
PLEAD = Action(kind="talk", summary="周衡劝守钟人说出钟里的事", target="老守钟人", npc="warden", approach="说服")
HARD_TALK = Ruling(
    needs_roll=True, skill="说服", difficulty="困难", bonus=1, clues_on_success=["c_diary"], minutes=10,
    reason="守钟人就在一旁盯着你，他护着钟楼；替代门槛，成功可取得 c_diary 守钟人的日记",
)  # fmt: skip


async def stream(app, inp, thread) -> tuple[list[dict], list[dict]]:
    out, asks = [], []
    async for mode, chunk in app.astream(inp, thread, stream_mode=["custom", "updates"]):
        if mode == "custom":
            out.append(chunk)
        elif "__interrupt__" in chunk:
            asks.append(chunk["__interrupt__"][0].value)
    return out, asks


async def test_the_player_throws_their_own_dice(module, fake):
    """With someone at the screen the turn stops before the dice: the prompt shows what is
    asked, the numbers and why, and only the throw rolls them. Under the result comes what
    it led to."""
    game, cast, _ = make(module, fake, {"劝他": (PLEAD, HARD_TALK)}, location="tower")
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "t-hand"}}
    fake.queue(8)  # 说服 40, hard: 8 is an extreme success
    out, asks = await stream(app, {"input": "劝他", "by_hand": True}, thread)
    ask = asks[0]
    assert ask["kind"] == "roll" and fake.values and not [e for e in out if e["type"] == "dice"]  # nothing thrown yet
    assert (ask["skill"], ask["target"], ask["hard"], ask["extreme"], ask["need"], ask["bonus"]) == ("说服", 40, 20, 8, 20, 1)
    assert ask["why"] == "守钟人就在一旁盯着你，他护着钟楼" and "c_diary" not in str(ask) and "日记" not in str(ask)
    out, asks = await stream(app, Command(resume="roll"), thread)
    kinds = [e["type"] for e in out]
    assert not asks and kinds.index("dice") < kinds.index("outcome") < kinds.index("npc")
    dice = next(e for e in out if e["type"] == "dice")
    assert (dice["tens"], dice["units"], dice["kept"], dice["level"]) == ([0], 8, 0, "极难成功")
    assert next(e for e in out if e["type"] == "outcome")["lines"] == ["老守钟人松了口：〔守钟人的日记〕"]
    assert game.state.clues == ["c_diary"] and "已解锁" in cast.npc_calls[-1][1]


async def test_ai_dice_do_not_wait(module, fake):
    game, *_ = make(module, fake, {"劝他": (PLEAD, HARD_TALK)}, location="tower")
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90)
    out, asks = await stream(app, {"input": "劝他", "by_hand": False}, {"configurable": {"thread_id": "t-ai"}})
    assert [e["type"] for e in out].count("dice") == 1 and [a["kind"] for a in asks] == ["push"]  # rolled at once
    assert "clues" not in asks[0] and asks[0]["need"] == 20 and asks[0]["left"] == 55 and asks[0]["luck"] is None
    outcome = next(e for e in out if e["type"] == "outcome")["lines"]
    assert outcome == ["还有一次机会：孤注一掷，或者花幸运"]  # ids stay home, the stakes travel


def test_the_roll_prompt_never_gives_the_module_away(module, fake):
    game, *_ = make(module, fake, {}, location="tower")

    def why(reason: str) -> str:
        return game._why(Ruling(needs_roll=True, skill="侦查", reason=reason))

    assert why("守钟人就在场，绕到他柜前翻看需潜行避开注意；难度不低于说服·困难，成功可取得日记。") == "守钟人就在场，绕到他柜前翻看需潜行避开注意"
    assert why("齿轮间里很暗，齿轮转得飞快") == "齿轮间里很暗，齿轮转得飞快"  # a place on screen, not a secret
    assert why("c_gears 就在那里") == ""  # an id
    assert why("他在钟里藏了东西，所以拦着人") == ""  # echoes a secret
    assert why("守钟人的日记就锁在柜子里") == ""  # a clue not found yet
    assert len(why("、".join(["守钟人坐在楼梯口打盹手里还攥着一块油布"] * 3))) == 40


async def test_losing_a_fifth_of_san_in_one_day_is_indefinite_insanity_and_the_count_starts_again_each_day(fake):
    """The daily count was never started or reset, so 不定性疯狂 could not happen at all."""
    from autokeeper.engine.game import Game
    from autokeeper.module.schema import Module
    from autokeeper.rules.coc import Rules

    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    inv = st.investigators[0]
    assert inv.san_start_of_day == inv.san == 55 and inv.san_lost_today == 0
    rules = Rules(dice=fake)
    fake.queue(99, 99)  # a failed SAN roll losing 6 of 55, then the INT roll for 5 or more
    assert not rules.sanity(inv, "0", "6").indefinite
    game = Game(m, st, FakeCast({}), rules)
    st.fired = [ev.id for ev in m.events]  # only the day changes here, no timed event fires
    st.minutes += 24 * 60  # the next day
    await game._time()
    assert inv.san_start_of_day == 49 and inv.san_lost_today == 0
    fake.queue(99, 99)
    result = rules.sanity(inv, "0", "10")  # 10 of 49 in one day: more than a fifth
    assert result.indefinite and "不定性疯狂" in inv.conditions
