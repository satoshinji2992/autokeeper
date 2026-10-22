"""Story discoveries and completed conversations settle before their consequences. No model calls."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Failure, Ruling
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState, parse_time
from autokeeper.module.schema import Check, Clue, Ending, Event, Module
from autokeeper.paths import EXAMPLE, MODULES
from autokeeper.rules.coc import Rules
from autokeeper.world import scene
from tests.test_engine import FakeCast, make


@pytest.fixture
def module():
    m = Module.load(EXAMPLE)
    m.events = []
    m.endings = []
    return m


async def test_a_handout_only_becomes_a_held_object_when_the_keeper_places_it(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    who = game.state.investigator.id
    raw = {"holds": [{"item": "守钟人的日记", "holder": who}]}
    context = scene.Context(travel=game._travel)
    assert scene.plan(game.world, raw, context).problems
    await game._gain(["c_diary"], Turn(), None)
    assert not [e for e in game.world.rec.entities.values() if e.kind == "item"]
    placed = scene.plan(game.world, raw, context)
    assert not placed.problems
    assert scene.land(game.world, placed.held)
    document = game.world.rec.entities["document:c_diary"]
    assert document.at == who and document.important
    assert "守钟人的日记" in game.state.investigator.inventory
    assert not scene.plan(game.world, raw, context).units
    # The saved record still has it; seeing a handout did not silently transfer a separate original.
    saved = GameState.model_validate_json(game.state.model_dump_json())
    assert saved.record.entities[document.id].at == who
    await game.close()


async def test_a_promised_testimony_cannot_finish_the_game_before_it_is_actually_told(module, fake):
    truth = Clue(id="c_truth", title="守钟人的自白", text="他知道钟里的东西为何敲钟。", where="office",
                 npc="warden", sanity="1D4/1D4", mythos=3)
    module.clues.append(truth)
    module.npc("warden").clues.append(truth.id)
    module.flags["confessed"] = "守钟人说完了真相"
    module.endings = [Ending(id="truth", title="真相", condition="flag:confessed", requires=[truth.id],
                             text="调查结束。", sanity_gain="1D6")]
    game, cast, events = make(module, fake, {}, location="tower")
    # Testimony goes with its speaker, whose meeting place is not a restriction on the conversation.
    assert game._valid_clues([truth.id], None) == [truth.id]
    action = Action(kind="talk", npc="warden", summary="可以聊聊吗？", speech="可以聊聊吗？")
    ruling = Ruling(needs_roll=False, auto_clues=[truth.id], set_flags=["confessed"])
    turn = await game.resolve(action, ruling)
    assert not game.state.ending and "confessed" not in game.state.flags
    await game.talk(turn)  # a greeting, no Dialogue.told
    assert truth.id not in game.state.clues and not game.state.ending
    assert game.state.pending_story_flags == {"confessed": [truth.id]}
    assert GameState.model_validate_json(game.state.model_dump_json()).pending_story_flags == game.state.pending_story_flags
    fake.queue(20).queue_totals(2, 4)
    cast.told["warden"] = [truth.id]
    turn = await game.resolve(action, ruling)
    assert not game.state.ending
    await game.talk(turn)
    assert truth.id in game.state.clues and game.state.ending == "truth"
    assert game.state.investigator.san == 57 and game.state.investigator.value("克苏鲁神话") == 3
    assert not game.state.pending_story_flags
    kinds = [e["type"] for e in events]
    assert max(i for i, kind in enumerate(kinds) if kind == "npc") < kinds.index("sanity") < kinds.index("ending")
    await game.close()


async def test_a_timed_event_elsewhere_does_not_move_or_harm_the_party_and_is_not_replayed(module, fake):
    module.events.append(Event(id="sermon", title="广场布道", when="D1 21:00", move_to="square", present=["preacher"],
                               text="布道在广场举行。", read_aloud="布道者登上台阶。", sanity="0/1D3",
                               performer="preacher", performance="讲个故事。", advance_to="D2 08:00"))
    game, cast, events = make(module, fake, {}, location="office")
    game.state.minutes = parse_time("D1 21:05")
    turn = Turn()
    await game._time(turn)
    assert game.state.location == "office" and game.state.investigator.san == 55
    assert game.state.clock == "D1 21:05"
    assert "sermon" in game.state.fired and not turn.events
    assert not any(e["type"] in ("event", "sanity", "move") for e in events)
    await game._arrive("square", Turn())
    await game.talk(Turn())
    assert not cast.performances and game.state.fired.count("sermon") == 1
    await game.close()


@pytest.mark.parametrize("failed", [False, True])
async def test_arrival_event_time_covers_the_attempt_but_failure_costs_are_still_additional(module, fake, failed):
    module.events = [Event(id="late", title="天色已晚", when="visit:tower", text="这段经历结束时已到晚上。",
                           advance_to="20:00")]
    game, _, _ = make(module, fake, {}, location="square")
    game.state.minutes = parse_time("D1 09:00")
    action = Action(kind="investigate", summary="走到钟楼查看齿轮", destination="tower", target="齿轮")
    ruling = Ruling(needs_roll=failed, skill="侦查" if failed else "", minutes=30,
                    on_fail=Failure(text="失手后清理残片", minutes=7))
    if failed:
        fake.queue(100)  # fumble: no second choice, the extra cost settles immediately
    turn = await game.resolve(action, ruling)
    assert not turn.push and game.state.clock == ("D1 20:07" if failed else "D1 20:00")
    assert game.state.fired == ["late"]
    await game.close()


async def test_arrival_event_and_action_end_survive_reload_without_double_counting_time(module, fake):
    module.events = [Event(id="late", title="晚间抵达", when="visit:tower", text="天黑了。",
                           read_aloud="天已经黑了。", advance_to="20:00")]
    game, _, _ = make(module, fake, {}, location="square")
    game.state.minutes = parse_time("D1 09:00")
    game.state.pending_turn = {"input": "去钟楼检查"}
    saved = []
    game.checkpoint = lambda: saved.append(game.state.model_dump_json())

    async def disconnected(event):
        if event["type"] == "event":
            raise ConnectionError("notification interrupted")

    game.emit = disconnected
    action = Action(kind="investigate", summary="去钟楼检查", destination="tower")
    ruling = Ruling(needs_roll=False, minutes=30)
    with pytest.raises(ConnectionError):
        await game.resolve(action, ruling)
    resumed = Game(module.model_copy(deep=True), GameState.model_validate_json(saved[-1]), FakeCast({}), Rules(fake))
    await resumed.resolve(action, ruling)
    assert resumed.state.clock == "D1 20:00" and resumed.state.fired == ["late"]
    await game.close()
    await resumed.close()


async def test_a_conditioned_event_waits_for_the_hour_and_checks_once_each_day(module, fake):
    module.flags["watching"] = "有人设岗监视"
    module.events.append(Event(id="visitor", title="来客", when="flag:watching & hour:0-6", text="有人来了。",
                               read_aloud="午夜的人影出现了。", check=Check(skill="幸运")))
    game, _, events = make(module, fake, {}, location="tower")
    game.state.minutes = parse_time("D1 22:00")
    await game._flags(["watching"], Turn())
    assert not game.state.fired and not game.state.event_attempts
    fake.queue(90)
    game.state.minutes = parse_time("D2 00:05")
    await game._time(Turn())
    assert "visitor" not in game.state.fired
    game.state.minutes += 5
    await game._time(Turn())
    assert len([e for e in events if e["type"] == "dice"]) == 1
    fake.queue(10)
    game.state.minutes = parse_time("D3 00:05")
    await game._time(Turn())
    await game._time(Turn())
    assert game.state.fired == ["visitor"]
    assert len([e for e in events if e["type"] == "dice"]) == 2
    await game.close()


async def test_after_midnight_sanity_counts_towards_the_new_day(module, fake):
    module.events = [Event(id="midnight", title="午夜的发现", when="D2 00:00", text="新的异常。", sanity="1/1")]
    game, _, _ = make(module, fake, {}, location="tower")
    investigator = game.state.investigator
    investigator.san_start_of_day, investigator.san_lost_today = 60, 5
    fake.queue(20).queue_totals(1)
    game.state.minutes = parse_time("D2 00:05")
    await game._time(Turn())
    assert investigator.san == 54
    assert investigator.san_start_of_day == 55 and investigator.san_lost_today == 1
    await game.close()


async def test_a_successful_event_check_is_not_lost_if_interrupted_before_the_event(module, fake):
    module.flags["ready"] = "在场等待"
    event = Event(id="visitor", title="来客", when="flag:ready", text="有人来了。", check=Check(skill="幸运"))
    module.events.append(event)
    game, _, events = make(module, fake, {}, location="tower")
    fake.queue(10)
    original = game._fire

    async def fail_once(ev, turn):
        game._fire = original
        raise RuntimeError("interrupt after roll")

    game._fire = fail_once
    with pytest.raises(RuntimeError, match="interrupt"):
        await game._flags(["ready"], Turn())
    await game._time(Turn())
    assert event.id in game.state.fired and not fake.values
    assert len([e for e in events if e["type"] == "dice"]) == 1
    await game.close()


async def test_a_social_success_reaches_the_keeper_with_its_actual_effect_not_only_the_dice(module, fake):
    game, _, _ = make(module, fake, {}, location="square")
    action = Action(kind="talk", summary="劝会众带孩子回家", npc="preacher")
    ruling = Ruling(needs_roll=True, skill="魅惑", reason="成功后几个母亲带孩子先走；失败则他们留下。")
    fake.queue(1)
    turn = await game.resolve(action, ruling)
    turn.npc_lines.append(("preacher", "谁也不准走。"))  # he spoke before the successful check
    report = game._report(turn)
    assert ruling.reason in report and "骰前的阻拦不能推翻骰后成功" in report
    assert "依实际成败兑现当前请求" in report and "不等于成功没有效果" in report
    assert "不能另加可追查的脚印" not in report
    await game.close()


@pytest.mark.parametrize(("choice", "ending"), [("douglas_departed", "farewell"), ("followed_douglas", "below")])
async def test_the_reader_can_answer_and_the_player_can_choose_after_learning_the_truth(fake, choice, ending):
    module = Module.load(MODULES / "zhuishuren")
    module.events = [e for e in module.events if e.id in {"e_recognized", "e_horde"}]
    module.npc("douglas").location = "mudi"
    game, cast, events = make(module, fake, {}, location="mudi")
    action = Action(kind="talk", npc="douglas", summary="请告诉我您的近况", speech="您还好吗？")
    ruling = Ruling(needs_roll=False, auto_clues=["c_douglas_truth"], set_flags=["talked_douglas", "recognized"])
    turn = await game.resolve(action, ruling)
    assert not game.state.encounter and not game.state.ending and not game.state.fired
    cast.told["douglas"] = ["c_douglas_truth"]
    fake.queue(10).queue_totals(2)
    await game.talk(turn)
    assert "c_douglas_truth" in game.state.clues and not game.state.ending
    assert game.state.investigator.san == 53 and game.state.investigator.value("克苏鲁神话") == 3
    # Only the next actual decision closes this meeting; either choice remains available.
    if ending == "farewell":
        fake.queue_totals(3)
    turn = await game.resolve(Action(kind="talk", npc="douglas", summary="作出决定"),
                              Ruling(needs_roll=False, set_flags=[choice]))
    assert game.state.ending == ending and not game.state.encounter
    assert sum(e["type"] == "ending" for e in events) == 1
    await game.close()


async def test_recognizing_the_living_reader_does_not_trigger_the_attack_branch(fake):
    module = Module.load(MODULES / "zhuishuren")
    module.events = [e for e in module.events if e.id in {"e_recognized", "e_horde"}]
    game, _, _ = make(module, fake, {}, location="mudi")
    await game._flags(["recognized", "unmasked"], Turn())
    assert not game.state.fired and game.state.encounter is None
    fake.queue(10, 10).queue_totals(1)
    await game._flags(["douglas_down"], Turn())
    assert {"e_recognized", "e_horde"} <= set(game.state.fired)
    assert game.state.encounter is not None
    await game.close()


async def test_an_overnight_event_advances_the_real_clock_and_due_events_without_rewinding(module, fake):
    module.flags["fainted"] = "昏倒了"
    module.flags["bells"] = "晚钟敲过了"
    module.events = [Event(id="waking", title="夜醒", when="flag:fainted", text="夜间苏醒。", advance_to="20:00"),
                     Event(id="bells", title="晚钟", when="D1 18:00", text="敲过晚钟。", sets=["bells"])]
    game, _, _ = make(module, fake, {}, location="tower")
    game.state.minutes = parse_time("D1 11:48")
    await game._flags(["fainted"], Turn())
    assert game.state.clock == "D1 20:00" and {"waking", "bells"} <= set(game.state.fired)
    assert "bells" in game.state.flags and game.world.rec.simulated == game.state.minutes
    assert next(e for e in game.state.log if e.kind == "event" and "夜醒" in e.text).time == "D1 20:00"
    await game.close()
    # A late-night trigger stays late-night; it neither rewinds nor adds a spurious whole day.
    game, _, _ = make(module, fake, {}, location="tower")
    game.state.minutes = parse_time("D2 22:00")
    await game._flags(["fainted"], Turn())
    assert game.state.clock == "D2 22:00"
    await game.close()


async def test_an_overnight_event_keeps_its_saved_target_after_a_failed_waking_notice(module, fake):
    from tests.test_resolution_recovery import reload_game, snapshots, turn_going

    module.flags["fainted"] = "昏倒了"
    module.events = [Event(id="waking", title="醒来", when="flag:fainted", text="翌日清晨醒来。",
                           read_aloud="天已经亮了。", advance_to="D2 08:00", sanity="1/1")]
    action = Action(kind="investigate", summary="打开石门")
    ruling = Ruling(needs_roll=False, set_flags=["fainted"], minutes=0)
    game, _, _ = make(module, fake, {}, location="tower")
    game.state.minutes = parse_time("D1 11:48")
    turn_going(game, action.summary)
    saved = snapshots(game)

    async def broken(event):
        if event["type"] == "event":
            raise ConnectionError("waking notice lost")

    game.emit = broken
    with pytest.raises(ConnectionError, match="waking notice"):
        await game.resolve(action, ruling)
    assert game.state.clock == "D2 08:00"
    resumed, events = reload_game(module, saved[-1], fake)
    fake.queue(10).queue_totals(1)
    await resumed.resolve(action, ruling)
    assert resumed.state.clock == "D2 08:00" and resumed.state.fired.count("waking") == 1
    assert resumed.state.investigator.san == 54 and resumed.state.investigator.san_lost_today == 1
    assert sum(e["type"] == "sanity" for e in events) == 1
    await game.close()
    await resumed.close()
