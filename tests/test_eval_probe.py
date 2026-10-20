"""Cheap prompt A/B on frozen inputs (eval/probe.py): a variant becomes literal changes to the system
prompt a role was sent, the replies are scored by rule, and the pairs stop once the split is clear."""

from __future__ import annotations

from pathlib import Path

import pytest

from autokeeper import skills
from autokeeper.agents import cast
from autokeeper.eval import probe, variants
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

PROMPTS = Path(__file__).resolve().parents[1] / "dev" / "prompts"


@pytest.mark.parametrize("name", ["kcmix", "ksys-no5c", "c-agency", "c-played"])
def test_every_kept_variant_turns_into_changes_a_built_prompt_takes(name):
    v = variants.load(PROMPTS / f"{name}.yaml")
    changes = probe.pairs(v)
    assert changes
    if "KEEPER_SYSTEM" in v.texts or "keeper-craft" in v.texts:
        built = cast.KEEPER_SYSTEM.format(title="追书人", craft=skills.wrap("keeper-craft"), module="年代：1922")
        want = v.texts.get("KEEPER_SYSTEM", cast.KEEPER_SYSTEM).format(
            title="追书人", craft=skills.wrap("keeper-craft", v.texts.get("keeper-craft")), module="年代：1922")
    else:
        fill = dict(name="托马斯", era="1922", setting="阿诺兹堡", role="委托人", look="—", personality="—", voice="—",
                    goal="—", knows="- 无", secrets="- 无", lies="- 无", clues="- 无", extra="")
        built, want = cast.NPC_SYSTEM.format(**fill), v.texts["NPC_SYSTEM"].format(**fill)
    assert probe.swap(built, changes) == want  # the same prompt the variant would have built


def test_a_change_that_does_not_fit_the_sent_prompt_is_refused():
    with pytest.raises(ValueError, match="出现了 0 次"):
        probe.swap("别的提示词", [("不在里面", "x")])


@pytest.mark.parametrize("wins, losses, n, stop", [(8, 1, 10, "分出高下"), (3, 2, 10, ""), (5, 5, 20, "打平"),
                                                  (8, 1, 9, ""), (9, 3, 20, "")])  # fmt: skip
def test_the_pairs_stop_every_ten_once_the_split_is_clear_or_even(wins, losses, n, stop):
    assert probe._stop(wins, losses, n) == stop


def test_an_npc_reply_scored_by_its_fields():
    m = Module.load(EXAMPLE)
    s = {"said": "陪我去钟楼", "input": []}
    told = probe.score_npc(m, s, {"text": "好，我陪你一起去。"})
    assert told["found"]["said_missing"] == 1 and told["score"] == 1
    assert probe.score_npc(m, s, {"text": "好，我陪你一起去。", "accompany": "tower"})["score"] == 0
    assert probe.score_npc(m, s, {"text": 3})["found"] == {"format": 1}
    plan = probe.score_plan(m, {"input": [{"content": "在场：老守钟人（warden）"}]},
                            {"action": {"destination": "nowhere"}, "initiative": [{"npc": "preacher", "does": "喊"}]})
    assert plan["found"] == {"format": 0, "initiative_absent": 1, "destination": 1}
