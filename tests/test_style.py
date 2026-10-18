"""The keeper-style eval (no model calls): the cliché counter counts what it should, reads
narration back from saves, autoplay logs and traces, and the scripted run is the same
game every time."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from autokeeper import config
from autokeeper.cli import app as cli
from autokeeper.config import Role
from autokeeper.eval import style
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.prefs import Prefs


def test_the_counter_counts_stock_phrases_per_thousand_characters():
    told = [
        "那不是风声，而是脚步。门不是锁着的，是虚掩的。树影里不是风——是个人。",  # three ways to say it
        "他仿佛没听见，脸上闪过一丝笑意，某种东西似乎在动。",
        "是不是有人来过，是谁？他不是本地人。",  # a question and a plain negative do not count
    ]
    c = style.count(told)
    assert c["hits"]["不是……而是……"] == 3
    assert [c["hits"][k] for k in ("仿佛", "一丝", "某种", "似乎")] == [1, 1, 1, 1]
    assert c["core"] == 7 and c["chars"] == sum(style.han(t) for t in told)
    assert c["density"] == round(7 * 1000 / c["chars"], 2)
    assert style.han("你好，世界！ok 123") == 4  # 字 are Chinese characters, not punctuation or Latin
    assert style.count(["墙上挂着一幅画像，像样的东西不多，他好像睡着了，像一截木头。"])["similes"] == 2
    assert style.count([])["density"] == 0.0


def test_narration_comes_back_from_saves_autoplay_logs_traces_and_runs(tmp_path):
    save = tmp_path / "slot.json"
    save.write_text(json.dumps({"log": [
        {"turn": 1, "time": "D1 09:00", "kind": "keeper", "text": "雾还没散。"},
        {"turn": 1, "time": "D1 09:00", "kind": "npc", "text": "钟有钟的规矩。"},
    ]}, ensure_ascii=False), encoding="utf-8")  # fmt: skip
    autoplay = tmp_path / "auto.jsonl"
    autoplay.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"type": "keeper_delta", "text": "雾"}, {"type": "keeper_end", "text": "雾还没散。"}, {"type": "npc", "text": "嗯。"},
    ]) + "\n{ cut short", encoding="utf-8")  # fmt: skip
    trace = tmp_path / "2026-10-18.jsonl"
    trace.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"agent": "keeper", "output": "雾还没散。"}, {"agent": "npc", "output": "嗯。"},
        {"agent": "keeper", "output": "半句", "error": "TimeoutError"}, {"agent": "keeper", "chars_out": 9},
    ]), encoding="utf-8")  # fmt: skip
    run = tmp_path / "style-before-1.json"
    run.write_text(json.dumps({"told": ["雾还没散。"]}, ensure_ascii=False), encoding="utf-8")
    for f in (save, autoplay, trace, run):
        assert style.narration(f) == ["雾还没散。"], f.name
    result = CliRunner().invoke(cli, ["eval", "cliche", str(save), str(trace)])
    assert result.exit_code == 0 and "前五项合计" in result.output


class Canned(hm.Model):
    """A keeper that answers the same line whatever it is told, and notes what it was told."""

    told: list[str] = []

    async def _call(self, messages, *, schema, images, on_delta):
        Canned.told.append(messages[-1]["content"])
        text = "雾还没散，你仿佛听见了什么。"
        if on_delta:
            await on_delta(text)
        return Reply(text=text, data={} if schema else None)


async def test_the_scripted_run_is_the_same_game_every_time(monkeypatch, tmp_path, skills_dir):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    Prefs(lines=["虫"]).save()  # a player's taste: the eval measures the keeper, not it
    reports = []
    for _ in range(2):
        Canned.told = []
        s = await style.run(config.MODULES / "zhuishuren", label="t", out=tmp_path / "out.json")
        reports.append(Canned.told)
    assert s["narrations"] == 9 and s["hits"]["仿佛"] == 9 and s["usage"]["calls"] == 9
    assert reports[0] == reports[1]  # same turns, same dice: only the keeper could differ
    told = "\n".join(reports[0])
    assert "侦查 60 → 24 困难成功" in told and "魅惑 15 → 9 常规成功" in told and "侦查 60 → 83 失败" in told
    assert "一个佝偻的身影翻过矮墙" in told and "雷区" not in told
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["told"] == ["雾还没散，你仿佛听见了什么。"] * 9


def test_the_keeper_learns_the_voice_from_examples(skills_dir):
    from autokeeper import skills

    body = skills.body("keeper-craft")
    examples = [line[2:] for line in body.splitlines() if line.startswith("> ") and len(line) > 10]
    assert len(examples) >= 2
    for text in examples:
        assert style.count([text])["core"] == 0
    assert "你要做什么" not in body  # a quoted phrase in a ban list is a phrase the model sees
