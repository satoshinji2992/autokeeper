"""Prompt variants for evaluations (eval/variants.py): one prompt swapped for a run, nothing on disk
edited, and refused when it would not be the same prompt with only that change."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from autokeeper import paths, skills
from autokeeper.agents import cast
from autokeeper.cli import app
from autokeeper.eval import variants


@pytest.fixture(autouse=True)
def _undone():
    yield
    variants.undo()


def _write(tmp_path: Path, text: str, **files: str) -> Path:
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    path = tmp_path / "v.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_sentence_is_swapped_for_the_run_and_taken_back(tmp_path):
    before = cast.KEEPER_SYSTEM
    old = "失败只说明本次尝试未达成，不额外制造敌意、伤害或丢失物品。"
    v = variants.load(_write(tmp_path, f"note: 第 10 条\nreplace:\n  KEEPER_SYSTEM:\n    - old: {old}\n      new: 失败照结算写。\n"))
    assert v.texts["KEEPER_SYSTEM"] == before.replace(old, "失败照结算写。") and v.digest
    variants.apply(v)
    assert "失败照结算写。" in cast.KEEPER_SYSTEM and old not in cast.KEEPER_SYSTEM
    assert variants.current() == {"name": "v", "digest": v.digest, "note": "第 10 条"}
    variants.undo()
    assert cast.KEEPER_SYSTEM == before and variants.current() == {}


def test_a_skill_is_read_from_the_variant(tmp_path):
    before = skills.body("keeper-craft")
    v = variants.load(_write(tmp_path, "files:\n  keeper-craft: craft.md\n",
                             **{"craft.md": "---\nname: keeper-craft\n---\n\n# 守秘人的叙事\n\n句子短。\n"}))
    variants.apply(v)
    assert skills.body("keeper-craft") == "# 守秘人的叙事\n\n句子短。"
    assert "句子短。" in skills.wrap("keeper-craft")  # what the keeper is given
    assert skills.body("coc7-core") != v.texts["keeper-craft"]  # the other skills as they are
    variants.undo()
    assert skills.body("keeper-craft") == before


@pytest.mark.parametrize("text, why", [
    ("replace:\n  KEEPER_SYSTEM:\n    - old: 这句不在里面\n      new: 随便\n", "出现了 0 次"),
    ("replace:\n  KEEPER_SYSTEM:\n    - old: '{craft}'\n      new: 没有技能\n", "占位符变了"),
    ("replace:\n  NO_SUCH_SYSTEM:\n    - old: a\n      new: b\n", "不知道要换的是什么"),
])  # fmt: skip
def test_a_variant_that_is_not_a_clean_swap_is_refused(tmp_path, text, why):
    with pytest.raises(ValueError, match=why):
        variants.load(_write(tmp_path, text))


def test_the_variants_kept_in_dev_prompts_still_fit_the_code():
    folder = Path(__file__).resolve().parents[1] / "dev" / "prompts"
    found = sorted(folder.glob("*.yaml"))
    assert found
    for path in found:
        v = variants.load(path)  # every replacement found once, every placeholder kept
        assert v.texts, path.name


def test_eval_takes_a_variant_before_its_command(tmp_path, monkeypatch):
    path = _write(tmp_path, "note: 试一下\nreplace:\n  NPC_SYSTEM:\n    - old: 仅以该角色身份回应。\n      new: 只用这个角色说话。\n")
    seen = {}

    def ledger(module):
        seen["npc"] = cast.NPC_SYSTEM
        return {}

    monkeypatch.setattr("autokeeper.eval.context.ledger", ledger)
    result = CliRunner().invoke(app, ["eval", "--prompts", str(path), "context", str(paths.EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "提示词变体：v" in result.output and "只用这个角色说话。" in seen["npc"]
