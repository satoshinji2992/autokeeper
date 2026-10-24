"""Unrepaired tool markup is not a spoken reply or a recoverable missing-field draft."""

from __future__ import annotations

import pytest

from autokeeper import config
from autokeeper.agents.cast import CONTINUED_TELLING, TELLING, Cast, Dialogue, Telling
from autokeeper.engine.game import Game
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.world.store import World

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401

MARKUP = '地图给你。</text><parameter name="items">[{"name":"地图","to":"zhouheng"}]'


async def test_unrepaired_tool_markup_cannot_become_an_npc_reply_or_world_action():
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    cast = Cast(config.Config(roles={role: SCRIPTED for role in config.DEFAULT_ROLES}), module)
    cast.world = World(module, state)
    SCRIPT.extend([{"text": MARKUP}, {"text": MARKUP}])
    before = state.record.model_dump_json()
    try:
        with pytest.raises(hm.StructuredOutputError, match="工具调用的标记"):
            await cast.npc(state, "warden", "能给我地图吗？")
        assert len(SEEN) == 2 and not SCRIPT
        assert state.record.model_dump_json() == before
        assert state.npc("warden").lines == 0 and not state.transcripts["npc:warden"]
        assert state.usage["npc"]["input"] == 200
    finally:
        await cast.close()


async def test_repaired_tool_markup_preserves_the_complete_dialogue_and_its_receipts():
    repaired = {"text": "地图给你。", "told": ["c_map"], "items": [{"name": "地图", "to": "zhouheng"}]}
    SCRIPT.extend([{"text": MARKUP}, repaired])
    model = hm.make_model(SCRIPTED, "npc")
    try:
        reply = await model.complete([{"role": "user", "content": "能给我地图吗？"}], schema=Dialogue.model_json_schema())
        assert reply.data == repaired and not reply.validation_errors
        assert reply.repairs == 1 and len(SEEN) == 2
    finally:
        await model.close()


@pytest.mark.parametrize("schema,draft", [
    (TELLING, {"text": "雨已经停了。"}),
    (CONTINUED_TELLING, {"scene": {}, "text": "雨已经停了。"}),
])
async def test_missing_scene_or_continuation_still_reaches_domain_recovery(schema, draft):
    SCRIPT.extend([draft, draft])
    model = hm.make_model(SCRIPTED, "keeper")
    try:
        reply = await model.complete([{"role": "user", "content": "叙述已经结算的经过。"}], schema=schema)
        assert reply.data == draft and reply.validation_errors
        assert reply.repairs == 1 and len(SEEN) == 2
    finally:
        await model.close()


async def test_a_clean_repair_draft_replaces_contaminated_words_without_discarding_the_valid_scene():
    clean = {"scene": {}, "text": "雨已经停了。"}  # continuation still needs its ordinary domain recovery
    SCRIPT.extend([{"scene": {}, "text": MARKUP}, clean])
    model = hm.make_model(SCRIPTED, "keeper")
    try:
        reply = await model.complete([{"role": "user", "content": "叙述已经结算的经过。"}], schema=CONTINUED_TELLING)
        assert reply.data == clean and Telling.model_validate(reply.data).text == clean["text"]
        assert reply.validation_errors and not hm._markup(reply.data)
        assert reply.repairs == 1 and len(SEEN) == 2
    finally:
        await model.close()


async def test_a_contaminated_repair_cannot_replace_a_clean_completed_scene():
    clean = {"scene": {}, "text": "雨已经停了。"}
    SCRIPT.extend([clean, {"scene": {}, "text": MARKUP}])
    model = hm.make_model(SCRIPTED, "keeper")
    try:
        reply = await model.complete([{"role": "user", "content": "叙述已经结算的经过。"}], schema=CONTINUED_TELLING)
        assert reply.data == clean and reply.validation_errors and not hm._markup(reply.data)
        assert reply.repairs == 1 and len(SEEN) == 2
    finally:
        await model.close()
