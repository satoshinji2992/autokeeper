"""A pushed roll's announced cost is not its private result or its success contract."""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, Failure, PushRuling, Ruling
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import Game
from autokeeper.engine.offers import PUSH_SHOWN, public_push
from autokeeper.module.schema import Clue, Location, Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make, run_turn
from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401


async def test_local_and_network_push_offers_share_the_same_public_contract(fake):
    module = Module.load(EXAMPLE)
    action = Action(kind="investigate", summary="周衡翻检旧报合订本", target="旧报合订本")
    hidden = "夹板暗格里找到了旧报原稿，但打翻墨水，赔偿五美元"
    failure = Failure(text=hidden, risk="可能打翻墨水，需要赔偿五美元", clues=["c_archive"], minutes=15)
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], on_push_fail=failure)
    game, *_ = make(module, fake, {"搜索": (action, ruling)}, location="office")
    fake.queue(90)
    try:
        app = turn_graph.build(game, InMemorySaver())
        _, interruptions = await run_turn(app, "搜索", {"configurable": {"thread_id": "private-push"}})
        assert len(interruptions) == 1
        offer = interruptions[0]
        assert set(offer) <= {"kind", *PUSH_SHOWN}
        assert offer == {"kind": "push", **public_push(game.state.pending_push)}
        assert offer["risk"] == failure.risk
        encoded = json.dumps(offer, ensure_ascii=False)
        assert all(secret not in encoded for secret in (hidden, "夹板", "c_archive", "on_push_fail", "success", "action_record"))
        hub = Hub(EXAMPLE, state=game.state, cast=game.cast, rules=Rules(dice=fake))
        assert hub._view(game.state.investigator.id)["pending_push"] == public_push(game.state.pending_push)
        assert hub._view()["pending_push"] is None  # an unseated reader does not inherit this scene's offer
        assert game.state.pending_push["on_push_fail"]["text"] == hidden
        assert game.state.pending_push["success"]["clues_on_success"] == ["c_archive"]
    finally:
        await game.close()


async def test_missing_public_risk_never_falls_back_to_the_full_failure_result(fake):
    module = Module.load(EXAMPLE)
    action = Action(kind="investigate", summary="周衡翻检旧报合订本", target="旧报合订本")
    failure = Failure(text="夹板暗格里的密信会被发现，管理员随即过来", clues=["c_archive"])
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], on_push_fail=failure)
    game, *_ = make(module, fake, {}, location="office")
    debug = []
    game._debug = lambda kind, data: debug.append((kind, data))
    fake.queue(90)
    try:
        turn = await game.resolve(action, ruling)
        assert turn.push["risk"] == ""
        assert turn.push["on_push_fail"]["text"] == failure.text
        assert any(kind == "push" and data["phase"] == "risk-missing" for kind, data in debug)
        assert not turn.notes  # reporting a schema omission must not add a keeper call to an otherwise complete reply
        assert "夹板" not in json.dumps(public_push(turn.push), ensure_ascii=False)
    finally:
        await game.close()


async def test_repush_receives_the_full_private_contract_separately_from_the_announced_risk(fake):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "office"
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES},
                             visual_limit=0, imagegen=False, search="none"), module)
    full = Failure(text="移开夹板后仍得到旧报原稿，但弄脏管理员的账本", risk="可能弄脏账本并赔偿", clues=["c_archive"], minutes=15)
    pending = {"skill": "图书馆使用", "target": 60, "difficulty": "常规", "risk": full.risk,
               "on_push_fail": full.model_dump(), "action": "翻旧报"}
    SCRIPT.append({"achievable": True, "minutes": 10})
    try:
        await cast.repush(state, pending, "先把墨水放远，再逐页检查")
        assert len(SEEN) == 1
        prompt = SEEN[0]["messages"][-1]["content"]
        assert "【一般风险】" + full.risk in prompt
        assert "【原定失败结算，仅供后台】" in prompt and full.text in prompt and "c_archive" in prompt
        assert "模组写明的特殊失败所得要保留" in SEEN[0]["system"]
    finally:
        await cast.close()


@pytest.mark.parametrize(("place", "name", "find", "old_cost", "new_cost", "extra"), [
    ("pump", "码头泵房", "货运清单", "cut_hand", "alarm", True),
    ("hospital", "医院锅炉房", "夜班表", "scalded", "wet_floor", False),
])
async def test_a_revised_push_retains_explicit_failure_finds_but_rejudges_its_costs(
    fake, place, name, find, old_cost, new_cost, extra,
):
    inv = Module.load(EXAMPLE).pregens[0].model_copy(deep=True)
    module = Module(
        id=f"push-{place}", title=name, hook="查清失踪的工人去了哪里", truth="工人藏起了当晚的记录", start=place,
        locations=[Location(id=place, name=name, read_aloud="一间堆满器具的工作间")],
        clues=[Clue(id="record", title=find, text="记录上有工人当晚的去处", where=place),
               Clue(id="stamp", title="工具印记", text="记录被搬动留下的印记", where=place),
               Clue(id="locked", title="尚未成立的证据", text="需要另一个前提", where=place, requires=["flag:permission"])],
        flags={old_cost: "原做法的代价", new_cost: "新做法的代价", "permission": "另一个前提"}, pregens=[inv],
    )
    original = Failure(text=f"仍找到{find}，但被器具划伤或烫伤", risk="可能受伤", clues=["record", "invalid", "locked"],
                       flags=[old_cost], damage="1D3", minutes=15)
    revised = Failure(text="做好防护后没有受伤，但工作间里弄出了动静", risk="可能弄出动静",
                      clues=["stamp", "record", "record"] if extra else [], flags=[new_cost], minutes=3)

    class Reconsider(FakeCast):
        async def repush(self, state, pending, how):
            return PushRuling(achievable=True, minutes=4, on_push_fail=revised)

    game, *_ = make(module, fake, {})
    game.cast = Reconsider({})
    action = Action(kind="investigate", summary=f"拆开器具查看{find}", target="器具底下")
    ruling = Ruling(needs_roll=True, skill="侦查", clues_on_success=["record"], on_push_fail=original)
    fake.queue(90, 95)
    hp, start = game.state.investigator.hp, game.state.minutes
    try:
        await game.resolve(action, ruling)
        pushed = await game.push({"choice": "push", "how": "先关掉装置，包住锋利和滚烫的部分，再仔细拆开"})
        assert game.state.clues == (["record", "stamp"] if extra else ["record"])
        assert pushed.clues == game.state.clues
        assert game.state.flags == [new_cost]
        assert old_cost not in game.state.flags and game.state.investigator.hp == hp
        assert game.state.minutes - start == 4 + 3  # the revised method and cost, never the old 15 minutes
        assert not fake.totals  # the old 1D3 injury was not rolled
        report = game._report(pushed)
        assert module.clue("record").text in report and f"没有取得：{find}" not in report
    finally:
        await game.close()


async def test_an_impossible_push_cannot_receive_its_original_failure_find(fake):
    module = Module.load(EXAMPLE)
    game, *_ = make(module, fake, {}, location="office")

    class Impossible(FakeCast):
        async def repush(self, state, pending, how):
            return PushRuling(achievable=False, why="档案已经移走，这里不能继续找", minutes=0,
                              on_push_fail=Failure(text="无可继续查找的内容"))

    game.cast = Impossible({})
    action = Action(kind="investigate", summary="翻旧报", target="旧报合订本")
    ruling = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"],
                    on_push_fail=Failure(text="原先可以找到旧报", clues=["c_archive"]))
    fake.queue(90)
    try:
        await game.resolve(action, ruling)
        pushed = await game.push({"choice": "push", "how": "在已经搬空的架子继续翻找"})
        assert not game.state.clues and not pushed.clues and not pushed.checks
    finally:
        await game.close()
