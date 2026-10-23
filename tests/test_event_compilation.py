"""Prepared and improvised event consequences survive the real module compilation paths."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from autokeeper.agents.director import Improv, NewEvent, NewNPC
from autokeeper.module import adapt, graph, importer, improv, package
from autokeeper.module.compiler import ensure_compiled
from autokeeper.module.schema import Check, Event, Module, RoundGate
from autokeeper.module.simulate import drift, reach
from autokeeper.paths import EXAMPLE
from autokeeper.rules.consequences import Die


def source_module() -> Module:
    module = Module.load(EXAMPLE)
    module.flags["alarm"] = "警报已经发出"
    module.events = [
        Event(id="e_attack", title="钟楼中的袭击", when="visit:tower", where="tower",
              text="守钟人向调查员扑上来，交锋由战斗规则处理。"),
        Event(id="e_rescue", title="巡警到场", when="D1 14:15", where="tower",
              text="双方交锋3轮后，巡警赶来。当场击毙守钟人，此次死亡无须掷骰。",
              read_aloud="巡警开枪，守钟人当场死亡。", ending=module.endings[0].id),
    ]
    return module


def event(module: Module, identity: str) -> Event:
    return next(e for e in module.events if e.id == identity)


def imported_draft(module: Module, folder: Path) -> Path:
    module.save(folder)  # Keep the real artwork while recreating the importer's single-file draft.
    package.start_draft(folder)
    (folder / "module.yaml").write_text(yaml.safe_dump(module.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    return folder


def compiled_events(module: Module, folder: Path, tmp_path: Path) -> str:
    root = ensure_compiled(module, folder, tmp_path / "compiled")
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "references/events.md" in skill
    return (root / "references" / "events.md").read_text(encoding="utf-8")


def test_imported_terminal_consequence_survives_normalization_reload_and_compilation(tmp_path):
    module = source_module()
    event(module, "e_attack").fight = ["warden"]
    rescue = event(module, "e_rescue")
    rescue.when = "flag:alarm"
    rescue.after_rounds = RoundGate(event="e_attack", completed=3)
    rescue.effects = [Die(target="warden", why="原文明确当场击毙，无须检定")]
    folder = imported_draft(module, tmp_path / "imported")

    assert importer.validate(folder) == []
    package.normalize(folder)
    assert (folder / "story.yaml").is_file()
    loaded = Module.load(folder)
    assert importer.validate(folder) == []
    refs = compiled_events(loaded, folder, tmp_path)
    assert "`e_attack` 开启的遭遇完成 3 轮后" in refs
    assert "flag:alarm" in refs and "进入结局" in refs
    assert "`warden` 死亡" in refs and "原文明确当场击毙，无须检定" in refs and "引擎结算，不再掷骰" in refs
    assert event(loaded, "e_rescue").model_dump() == rescue.model_dump()


def test_static_reachability_understands_round_dependencies_without_inventing_unattended_combat():
    module = source_module()
    origin = event(module, "e_attack")
    rescue = event(module, "e_rescue")
    origin.when, origin.fight = "visit:tower", ["warden"]
    rescue.when, rescue.after_rounds = "", RoundGate(event=origin.id, completed=3)
    assert rescue.id in reach(module).events
    assert rescue.id not in reach(module, without_npcs=frozenset({"warden"})).events
    origin.fight = []
    assert rescue.id not in reach(module).events
    origin.fight, rescue.when = ["warden"], "D1 14:15"
    assert rescue.id in reach(module).events
    assert rescue.ending not in drift(module).endings  # A clock alone cannot finish three combat rounds.


@pytest.mark.parametrize("when,clear_clock,expected", [
    ("D1 14:15", False, "D1 14:15"),
    ("D1 14:15", True, ""),
    ("flag:alarm", True, "flag:alarm"),
    ("flag:alarm & at:players:tower", True, "flag:alarm & at:players:tower"),
])
async def test_combat_pass_resolves_new_origin_and_terminal_body_effect_without_erasing_other_conditions(
    tmp_path, monkeypatch, when, clear_clock, expected,
):
    module = source_module()
    rescue = event(module, "e_rescue")
    rescue.when = when
    rescue.check = Check(skill="聆听", difficulty="困难")
    rescue.sets = ["alarm"]
    folder = tmp_path / "adapted"
    module.save(folder)
    assert adapt.wanted(module, "combat")  # Complete sheets and existing routes must not hide missing event consequences.
    calls = []

    async def ask(cfg, name, current, **kwargs):
        calls.append(name)
        return adapt.Combat.model_validate({"triggers": [
            {"event": "e_rescue", "after_rounds": {"event": "e_attack", "completed": 3}, "clear_clock": clear_clock,
             "effects": [{"kind": "die", "target": "warden", "why": "原文明确当场击毙，无须检定"}]},
            {"event": "e_attack", "fight": ["warden"]},
        ]})  # The consequence precedes its new origin in the answer, just as it may in a model response.

    monkeypatch.setattr(adapt, "_ask", ask)
    result = await adapt.adapt(folder, passes=("combat",), cfg=SimpleNamespace(), log=lambda _: None)
    assert calls == ["combat"] and result[0].written and not result[0].problems
    loaded = Module.load(folder)
    landed = event(loaded, "e_rescue")
    assert landed.when == expected and landed.check == rescue.check and landed.sets == ["alarm"]
    assert landed.ending == rescue.ending and not landed.fight and not landed.chase
    assert landed.after_rounds == RoundGate(event="e_attack", completed=3)
    assert landed.effects == [Die(target="warden", why="原文明确当场击毙，无须检定")]
    assert not [issue for issue in graph.audit(loaded) if issue.level == "error"]
    assert not adapt.wanted(loaded, "combat")
    refs = compiled_events(loaded, folder, tmp_path)
    assert "完成 3 轮后" in refs and "`warden` 死亡" in refs and "还须 聆听·困难 成功" in refs
    await adapt.adapt(folder, passes=("combat",), cfg=SimpleNamespace(), log=lambda _: None)
    assert calls == ["combat"]  # A completed pass is not asked again on the next preparation.


async def test_compile_pass_preserves_an_explicit_pregen_death_target(tmp_path, monkeypatch):
    module = source_module()
    event(module, "e_attack").fight = ["warden"]
    rescue = event(module, "e_rescue")
    rescue.text = "巡警将预设调查员周衡当场击毙，这一确定死亡无须掷骰。"
    rescue.read_aloud = "巡警开枪，周衡当场死亡。"
    folder = tmp_path / "pregen"
    module.save(folder)

    async def ask(*args, **kwargs):
        return adapt.Combat.model_validate({"triggers": [{"event": "e_rescue", "effects": [
            {"kind": "die", "target": "zhouheng", "why": "原文明定该预设调查员死亡，无须掷骰"},
        ]}]})

    monkeypatch.setattr(adapt, "_ask", ask)
    result = await adapt.adapt(folder, passes=("combat",), cfg=SimpleNamespace(), log=lambda _: None)
    assert result[0].written and not result[0].problems
    loaded = Module.load(folder)
    assert event(loaded, "e_rescue").effects[0].target == "zhouheng"
    assert "`zhouheng` 死亡" in compiled_events(loaded, folder, tmp_path)


@pytest.mark.parametrize("origin", ["missing", "e_rescue", "e_attack"])
async def test_an_invalid_proposed_round_gate_never_clears_the_authors_clock(tmp_path, monkeypatch, origin):
    module = source_module()
    folder = tmp_path / "unchanged-clock"
    module.save(folder)

    async def ask(*args, **kwargs):
        return adapt.Combat.model_validate({"triggers": [{
            "event": "e_rescue", "after_rounds": {"event": origin, "completed": 3}, "clear_clock": True,
            "effects": [{"kind": "die", "target": "warden", "why": "原文明确当场击毙，无须检定"}],
        }]})

    monkeypatch.setattr(adapt, "_ask", ask)
    result = await adapt.adapt(folder, passes=("combat",), cfg=SimpleNamespace(), log=lambda _: None)
    assert result[0].written and not result[0].problems
    landed = event(Module.load(folder), "e_rescue")
    assert landed.when == "D1 14:15" and landed.after_rounds is None


@pytest.mark.parametrize("origin,expected", [
    ("missing", "起源事件 missing 不存在"),
    ("e_rescue", "不能等待本事件自己"),
    ("e_attack", "没有开启战斗或追逐"),
])
def test_import_validation_rejects_round_gates_without_a_real_other_encounter(tmp_path, origin, expected):
    module = source_module()
    rescue = event(module, "e_rescue")
    rescue.when = ""
    rescue.after_rounds = RoundGate(event=origin, completed=3)
    folder = imported_draft(module, tmp_path / "invalid")
    assert any(expected in problem for problem in importer.validate(folder))


def test_completed_round_count_is_positive_in_import_and_director_schemas():
    with pytest.raises(ValidationError):
        RoundGate(event="e_attack", completed=0)
    with pytest.raises(ValidationError):
        NewEvent(id="e_cut", title="过早的后果", read_aloud="尚未完成第一轮。",
                 after_rounds={"event": "e_attack", "completed": 0})


def test_improvised_event_consequences_survive_saved_plan_replay_yaml_and_compilation(tmp_path):
    module = source_module()
    plan = Improv(kind="展开", reason="补入门房的冲突与确定后果", npcs=[NewNPC(
        id="driver", name="车夫", role="来访者", location="tower",
    )], events=[
        NewEvent(id="e_driver", title="车夫冲入", when="now", read_aloud="车夫向众人扑来。", fight=["driver"]),
        NewEvent(id="e_driver_end", title="车夫死亡", when="flag:alarm", where="tower", read_aloud="车夫当场死亡。",
                 after_rounds=RoundGate(event="e_driver", completed=2),
                 effects=[Die(target="driver", why="此次已经确定的身体后果")]),
    ])
    assert improv.check(module, plan, at="tower") == []
    improv.apply(module, plan)
    saved = json.loads(json.dumps([{"plan": plan.model_dump(mode="json")}], ensure_ascii=False))
    restored = source_module()
    improv.restore(restored, saved)
    improv.restore(restored, saved)
    assert [e.model_dump() for e in restored.events] == [e.model_dump() for e in module.events]
    assert len([e for e in restored.events if e.id == "e_driver_end"]) == 1
    folder = tmp_path / "improvised"
    restored.save(folder)
    loaded = Module.load(folder)
    assert event(loaded, "e_driver").fight == ["driver"]
    refs = compiled_events(loaded, folder, tmp_path)
    assert "`e_driver` 开启的遭遇完成 2 轮后" in refs and "`driver` 死亡" in refs
    assert event(loaded, "e_driver_end").model_dump() == event(module, "e_driver_end").model_dump()


@pytest.mark.parametrize("origin,expected", [
    ("missing", "起源事件 missing 不存在"),
    ("e_cut", "不能等待本事件自己"),
    ("e_attack", "没有开启战斗或追逐"),
])
def test_director_round_gate_is_checked_before_it_can_be_saved(origin, expected):
    plan = Improv(kind="展开", reason="尚无合法起源的后果", events=[NewEvent(
        id="e_cut", title="巡警到场", read_aloud="巡警赶来。", after_rounds=RoundGate(event=origin, completed=1),
    )])
    assert any(expected in problem for problem in improv.check(source_module(), plan, at="tower"))


@pytest.mark.parametrize("target,why,expected", [
    ("unknown", "有明确原文依据", "目标 unknown 不存在"),
    ("warden", " ", "缺少明确依据"),
])
def test_import_rejects_a_death_without_a_valid_target_and_basis(tmp_path, target, why, expected):
    module = source_module()
    event(module, "e_rescue").effects = [Die(target=target, why=why)]
    folder = imported_draft(module, tmp_path / "invalid-death")
    assert any(expected in problem for problem in importer.validate(folder))
