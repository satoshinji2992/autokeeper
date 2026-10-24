"""A malformed hosted envelope cannot hide its parse failure or replay settled dice/tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, ContinuedHostedReply, HostedReply, Ruling
from autokeeper.engine.game import TURN_RECORD, Game
from autokeeper.engine.graph import build
from autokeeper.engine.state import GameState
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world.ops import Follow
from autokeeper.world.record import KEEPER
from autokeeper.world.store import World

from .test_engine import FakeCast, make
from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401

BAD_BRACE = (Path(__file__).parent / "fixtures" / "hosted_reply_extra_brace.txt").read_text(encoding="utf-8")
BLANK = " " * 92
PENDING = "后续等待仍需玩家决定。"
FINAL = {"kind": "tell", "telling": {"scene": {}, "text": "雨水遮住视野，你没有看清门外的动静。",
                                     "continuation": {"next": None, "pending": PENDING}}}


def real_cast(module):
    return Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}, minds=0,
                              visual_limit=0, imagegen=False, search="none"), module)


@pytest.mark.parametrize("continuing", [False, True])
@pytest.mark.parametrize("voices", [[], ["warden"]])
async def test_hosted_examples_validate_for_the_actual_branch_and_available_people(continuing, voices):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0]])
    world = World(module, state)
    assert world.apply(Follow(npc="preacher", leader=state.investigator.id, actor=KEEPER)).ok
    if continuing:
        state.pending_turn["request"] = {"actor": state.investigator.id, "remaining": "等待新的决定"}
    answer = FINAL if continuing else {"kind": "tell", "telling": {"scene": {}, "text": "现场没有变化。"}}
    SCRIPT.append(answer)
    cast = real_cast(module)
    cast.world = world
    try:
        await cast.keeper(state, "本段已结算。", voices=voices, check=lambda _: True)
        schema = SEEN[-1]["schema"]
        model = ContinuedHostedReply if continuing else HostedReply
        for sample in schema["examples"]:
            model.model_validate(sample)
            assert hm.problems(sample, schema) == []
            if sample["kind"] == "npc":
                assert sample["npc"] in voices and sample["instruction"].strip()
        assert hm.example(schema) == schema["examples"][0]
        assert hm.problems(hm.example(schema), schema) == []
        assert any(sample["kind"] == "npc" for sample in schema["examples"]) is bool(voices)
        assert schema["examples"][0]["telling"]["scene"] == {"here": ["preacher"], "follow": ["preacher"]}
        if continuing:
            controls = [sample["telling"]["continuation"] for sample in schema["examples"] if sample["kind"] == "tell"]
            assert any(control.get("next") for control in controls)
            assert any(control.get("pending") for control in controls)
            assert any(not control.get("next") and not control.get("pending") for control in controls)
        assert world.entity("preacher").following == state.investigator.id
        assert "不是本轮正文、指令或续办决定" in SEEN[-1]["messages"][-1]["content"]
    finally:
        await cast.close()


@pytest.mark.parametrize("raw,schema,expected", [
    ('["one", "two"]', {"type": "array", "items": {"type": "string"}}, ["one", "two"]),
    ("null", {"type": "null"}, None),
])
async def test_generic_structure_validation_respects_valid_array_and_null_roots(raw, schema, expected):
    SCRIPT.append(raw)
    reply = await hm.make_model(SCRIPTED, "root-contract").complete([{"role": "user", "content": "按合同输出。"}], schema=schema)
    assert reply.data == expected and reply.repairs == 0


async def test_original_extra_brace_then_blank_reports_both_parse_failures_instead_of_missing_kind():
    assert len(BAD_BRACE) == 807 and hm.parse_json(BAD_BRACE) is None
    SCRIPT.extend([BAD_BRACE, BLANK])
    with pytest.raises(hm.StructuredOutputError) as error:
        await hm.make_model(SCRIPTED, "keeper").complete(
            [{"role": "user", "content": "仅叙述已经结算的结果。"}], schema=ContinuedHostedReply.model_json_schema())
    assert "多余内容" in str(error.value) and "空白" in str(error.value)
    assert "kind" not in str(error.value) and "Field required" not in str(error.value)
    assert len(SEEN) == 2 and not SCRIPT
    assert SEEN[1]["messages"][-2]["content"] == BAD_BRACE
    assert "多余内容" in SEEN[1]["messages"][-1]["content"]


class HostedCast(FakeCast):
    """Real keeper/harness calls with offline ordinary rules and NPC performances."""

    def __init__(self, module):
        super().__init__({})
        self.host = real_cast(module)

    async def keeper(self, state, report, on_delta=None, lore="", *, check=None, fix="", voices=None,
                     remember=None, settled=False):
        return await self.host.keeper(state, report, on_delta, lore, check=check, fix=fix, voices=voices,
                                      remember=remember, settled=settled)

    async def close(self):
        await self.host.close()


async def run(app):
    events = []
    async for mode, chunk in app.astream({"input": "/retry", "by_hand": False}, stream_mode=["custom", "updates"]):
        if mode == "custom":
            events.append(chunk)
        elif "__interrupt__" in chunk:
            pytest.fail("The failed check was already dropped; no roll or push may be requested again")
    return events


async def test_real_graph_retry_recovers_only_telling_and_preserves_failed_check_tools_and_continuation(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings, module.agendas, module.foreshadow = [], [], [], []
    game, _, _events = make(module, fake, {}, location="tower")
    state = game.state
    action = Action(kind="investigate", summary="从窗边查看门外的动静", target="窗外")
    ruling = Ruling(needs_roll=True, skill="侦查")
    state.pending_turn = {"input": action.summary, "action": action.model_dump(mode="json"),
                          "ruling": ruling.model_dump(mode="json"), "request": {
                              "id": "offline:17", "actor": state.investigator.id, "original": action.summary,
                              "origin_turn": 17, "part": 0, "started": state.minutes,
                              "remaining": "查看后决定是否等待", "completed": [],
                          }}
    fake.queue(80)
    turn = await game.resolve(action, ruling)
    turn.absorb(await game.push("drop"))
    state.pending_turn.update(turn=TURN_RECORD.dump_python(turn, mode="json"), push_merged=True, stage="narrate")
    before = (state.investigator.luck, state.investigator.hp, state.minutes, len(state.checks))
    cast = HostedCast(module)
    game.cast, cast.host.world = cast, game.world
    SCRIPT.extend([{"kind": "npc", "npc": "warden", "instruction": "按你本人看见的情境简短回应。"}, BAD_BRACE, BLANK])
    with pytest.raises(hm.StructuredOutputError):
        await run(build(game))
    assert len(cast.npc_calls) == 1 and not fake.values
    assert (state.investigator.luck, state.investigator.hp, state.minutes, len(state.checks)) == before
    assert state.pending_turn["keeper"]["calls"][0]["done"]
    assert not state.pending_turn["keeper"].get("body_shown")
    saved = GameState.model_validate_json(state.model_dump_json())
    await game.close()

    resumed_cast = HostedCast(module)
    resumed = Game(module, saved, resumed_cast, Rules(dice=fake))
    resumed_cast.host.world = resumed.world
    SCRIPT.append(FINAL)
    try:
        events = await run(build(resumed))
        assert not fake.values and not SCRIPT and not resumed_cast.npc_calls
        assert [(check.skill, check.passed) for check in saved.checks] == [("侦查", False)]
        assert (saved.investigator.luck, saved.investigator.hp, saved.minutes, len(saved.checks)) == before
        assert saved.unfinished[saved.investigator.id]["remaining"] == PENDING
        assert any(event["type"] == "keeper_delta" and event["text"] == FINAL["telling"]["text"] for event in events)
        assert not any(event["type"] in ("dice", "performance") for event in events)
        assert SEEN[-1]["schema"]["title"] == "ContinuedHostedReply"
    finally:
        await resumed.close()
