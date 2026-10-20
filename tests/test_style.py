"""The style eval (no model calls): the ruler scores patterns against human fiction, finds the
examples' props and the NPCs' habits coming back, reads narration and NPC lines from saves,
autoplay logs, traces and runs, and the scripted runs are the same game every time."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from autokeeper import cli as cli_module
from autokeeper import config, paths, skills
from autokeeper.cli import app as cli
from autokeeper.config import Role
from autokeeper.eval import style
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import NPC
from autokeeper.prefs import Prefs


def test_the_ruler_scores_patterns_against_human_fiction():
    told = [
        "那不是风声，而是脚步。门不是锁着的，是虚掩的。树影里不是风——是个人。",  # three ways to say it
        "他仿佛没听见，脸上闪过一丝笑意，某种东西似乎在动。头发梳得一丝不苟。",  # 一丝不苟 is not 一丝
        "是不是有人来过，是谁？他不是本地人。指甲刮着窗缝，一下，又一下。",  # a question and a plain negative do not count
    ]
    c = style.count(told)
    assert c["hits"]["不是……而是……"] == 3 and c["hits"]["一下，又一下"] == 1
    assert [c["hits"][k] for k in ("仿佛", "一丝", "某种", "似乎")] == [1, 1, 1, 1]
    assert c["chars"] == sum(style.han(t) for t in told)
    per_k = {name: n * 1000 / c["chars"] for name, n in c["hits"].items()}
    assert c["per_1000"]["一丝"] == round(per_k["一丝"], 2)
    assert c["ratio"]["一丝"] == round(per_k["一丝"] / style.FLOOR, 1)  # never in the human text: against the floor
    assert c["ratio"]["似乎"] == round(per_k["似乎"] / 1.164, 1)
    # Only what goes past the human rate counts, so a keeper is not told off for 似乎 used as often as Lao She.
    assert c["excess"] == round(sum(max(0.0, r - style.PATTERNS[n][1]) for n, r in per_k.items()), 2)
    assert style.count(["他似乎睡着了。" + "院子里有人。" * 200])["excess"] == 0.0
    assert style.han("你好，世界！ok 123") == 4  # 字 are Chinese characters, not punctuation or Latin
    assert style.count(["墙上挂着一幅画像，像样的东西不多，他好像睡着了，像一截木头。"])["similes"] == 2
    assert style.count([])["excess"] == 0.0


def test_sentence_lengths_and_the_examples_props():
    even = style.count(["门开着。窗关着。灯亮着。"])
    mixed = style.count(["门开着。他站在门口看了很久，才把手里那盏煤油灯放到窗台上。走吧。"])
    assert even["sentence_cv"] == 0.0 and mixed["sentence_cv"] > 0.5 and mixed["short"] > 0
    examples = ["当铺的门帘是油布做的，一掀开，樟脑和旧棉絮的味道一齐扑出来。"]
    told = ["书房里一股樟脑味。", "你拉开抽屉，樟脑的味道更重了。", "门帘是油布的。"]
    found = style.count(told, examples=examples, own="模组里写着：后门的门帘是油布的。")
    assert found["borrowed"] == {"樟脑": 2}  # 门帘 and 油布 are the module's own; 味道 is no prop
    assert found["carried"] == {"樟脑": 1}  # brought from one narration into the next
    assert style.count(told, examples=examples, own="樟脑 油布 门帘")["borrowed"] == {}


def test_npc_lines_that_open_with_an_action_and_repeat_a_habit():
    thomas = NPC(id="thomas", name="托马斯", role="委托人", look="三十来岁，说话时爱搓手", voice="客气，爱说“说实在的”")
    lines = [
        "（搓着手）说实在的，一点征兆也没有。",
        "（又搓了搓手）书房在楼上。",  # the same habit, a line later
        "（点点头）好。",  # an action, but not one of his
        "说实在的，您尽管去看。",  # said two lines before last: a repeat
        "那本日记我没动过。",
    ]
    habit = style.habits(thomas)
    assert "搓手" in habit[0] and habit[1] == ("说实在的",)
    assert style.repeats(lines[1], lines[:1], habit) == ["搓手"]
    assert style.repeats(lines[3], lines[1:3], habit) == []  # out of reach: three lines back
    assert style.repeats(lines[3], lines[:3], habit) == ["说实在的"]
    v = style.voice({"托马斯": lines}, {"托马斯": habit})
    assert v["lines"] == 5 and v["action_first"] == 0.6 and v["action_first_later"] == 0.5
    assert v["repeats"] == 0.25 and v["repeated"] == {"搓手": 1}  # 说实在的 came back three lines on, not two
    assert style.voice({"托马斯": lines})["repeats"] == 0.0  # without the module, no habits to repeat


def test_narration_and_npc_lines_come_back_from_saves_autoplay_logs_traces_and_runs(tmp_path, monkeypatch):
    save = tmp_path / "slot.json"
    save.write_text(json.dumps({"module": "example", "log": [
        {"turn": 1, "time": "D1 09:00", "kind": "keeper", "text": "雾还没散。"},
        {"turn": 1, "time": "D1 09:00", "kind": "npc", "who": "老守钟人", "text": "钟有钟的规矩。"},
    ]}, ensure_ascii=False), encoding="utf-8")  # fmt: skip
    autoplay = tmp_path / "auto.jsonl"
    autoplay.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"type": "keeper_delta", "text": "雾"}, {"type": "keeper_end", "text": "雾还没散。"},
        {"type": "npc", "npc": "warden", "name": "老守钟人", "text": "钟有钟的规矩。"},
        {"type": "performance", "npc": "preacher", "name": "街头布道者", "text": "诸位……"},
    ]) + "\n{ cut short", encoding="utf-8")  # fmt: skip
    (tmp_path / "auto.summary.json").write_text(json.dumps({"module": "example"}), encoding="utf-8")
    trace = tmp_path / "2026-10-18.jsonl"
    trace.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"agent": "keeper", "output": "雾还没散。"},
        {"agent": "npc", "system": "你在一场 1920 年代的中文跑团里扮演 NPC「老守钟人」，……",
         "output": json.dumps({"text": "钟有钟的规矩。", "changes": []}, ensure_ascii=False)},
        {"agent": "keeper", "output": "半句", "error": "TimeoutError"}, {"agent": "keeper", "chars_out": 9},
    ]), encoding="utf-8")  # fmt: skip
    run = tmp_path / "style-before-1.json"
    run.write_text(json.dumps({"module": "example", "told": ["雾还没散。"], "said": {"老守钟人": ["钟有钟的规矩。"]}}, ensure_ascii=False), encoding="utf-8")
    for f in (save, autoplay, trace, run):
        told, said, _ = style.read(f)
        assert told == ["雾还没散。"] and said == {"老守钟人": ["钟有钟的规矩。"]}, f.name
        assert style.narration(f) == told
    assert style.read(autoplay)[2] == "example" and style.module_of("example").id == "example"
    assert style.score(run)["voice"]["lines"] == 1 and style.module_of("nowhere") is None
    monkeypatch.setattr(cli_module.console, "width", 160)
    result = CliRunner().invoke(cli, ["eval", "cliche", str(save), str(trace)])
    assert result.exit_code == 0 and "超出人类的部分" in result.output and "均值 ± 标准差" in result.output
    assert "动作开头" in result.output


class Canned(hm.Model):
    """A keeper that answers the same line whatever it is told, and notes what it was told;
    as an NPC, it answers with a habit every time."""

    told: list[str] = []

    async def _call(self, messages, *, schema, images, on_delta):
        Canned.told.append(messages[-1]["content"])
        text = "雾还没散，你仿佛听见了什么。"
        if schema and schema.get("title") == "Telling":  # the keeper: the scene as it was, then the words
            return Reply(text="", data={"scene": {}, "text": text})
        if schema:
            return Reply(text="", data={"text": "（搓着手）说实在的，我不知道。", "changes": []})
        if on_delta:
            await on_delta(text)
        return Reply(text=text)


def _canned(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    Prefs(lines=["虫"]).save()  # a player's taste: the eval measures the keeper, not it


async def test_the_scripted_run_is_the_same_game_every_time(monkeypatch, tmp_path, skills_dir):
    _canned(monkeypatch, tmp_path)
    reports = []
    for _ in range(2):
        Canned.told = []
        s = await style.run(paths.MODULES / "zhuishuren", label="t", out=tmp_path / "out.json")
        reports.append(Canned.told)
    assert s["narrations"] == 8 and s["hits"]["仿佛"] == 8 and s["usage"]["calls"] == 8
    assert reports[0] == reports[1]  # same turns, same dice: only the keeper could differ
    told = "\n".join(reports[0])
    assert "侦查 60 → 24 困难成功" in told and "侦查 60 → 83 失败" in told
    assert "魅惑 15 → 9 常规成功" not in told  # the NPC already answered this social check
    assert "一个佝偻的身影翻过矮墙" in told and "雷区" not in told
    saved = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert saved["told"] == ["雾还没散，你仿佛听见了什么。"] * 8 and len(saved["reports"]) == 8
    assert saved["examples"] == style.examples(skills.body("keeper-craft"))  # the passages the keeper was shown


async def test_the_npcs_hear_the_same_questions_every_time(monkeypatch, tmp_path, skills_dir):
    _canned(monkeypatch, tmp_path)
    runs = []
    for _ in range(2):
        Canned.told = []
        s = await style.talk(paths.MODULES / "zhuishuren", label="t", out=tmp_path / "talk.json")
        runs.append(Canned.told)
    assert runs[0] == runs[1] and len(runs[0]) == 12 and s["lines"] == 12
    assert set(s["said"]) == {"托马斯·金博尔", "梅洛迪亚斯·杰弗逊", "莉拉·奥黛尔"}
    assert "收到您的信就赶过来了" in runs[0][0] and "已解锁" in runs[0][0] and "c_shelf_gap" in runs[0][0]
    assert s["action_first"] == 1.0 and s["repeated"].get("说实在的") == 3  # only 托马斯 has it
    assert style.score(tmp_path / "talk.json")["voice"]["repeats"] == s["repeats"]


def test_the_keeper_learns_the_voice_from_examples(skills_dir):
    body = skills.body("keeper-craft")
    examples = style.examples(body)
    assert len(examples) >= 2
    for text in examples:
        assert style.count([text])["excess"] == 0
    assert "你要做什么" not in body  # a quoted phrase in a ban list is a phrase the model sees


class Judge(hm.Model):
    """Prefers the narration with 灯 in it; or, `biased`, always the one shown first."""

    biased = False

    async def _call(self, messages, *, schema, images, on_delta):
        text = messages[-1]["content"]
        first = text.split("【甲】", 1)[1].split("【乙", 1)[0]
        pick = "甲" if Judge.biased or "灯" in first else "乙"
        return Reply(text="", data={"better": pick, "why": "……"})


async def test_the_blind_judge_pairs_runs_and_does_not_reward_going_first(monkeypatch, tmp_path):
    from autokeeper.eval import blind

    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    monkeypatch.setitem(hm.MODELS, "judge", Judge)
    monkeypatch.setattr(blind, "route", lambda model: ("judge", model))
    folder = paths.dev_output("eval")
    folder.mkdir(parents=True)
    for i in (1, 2):
        for label, told in (("old", ["雾还没散。", "门开着。"]), ("new", ["一盏灯亮着。", "灯下有人。"])):
            (folder / f"style-{label}-{i}.json").write_text(json.dumps({"told": told, "reports": ["开场", "第一轮"]}, ensure_ascii=False), encoding="utf-8")
    s = await blind.judge("old", "new", out=folder / "blind.json")
    assert (s["pairs"], s["b_wins"], s["a_wins"], s["ties"]) == (4, 4, 0, 0)
    assert s["p"] == blind.sign_test(4, 0) == 0.125
    Judge.biased = True  # a judge that likes whatever it reads first decides nothing
    s = await blind.judge("old", "new")
    assert (s["b_wins"], s["a_wins"], s["ties"]) == (0, 0, 4) and s["p"] == 1.0
    Judge.biased = False
    assert blind.sign_test(9, 1) < 0.05 < blind.sign_test(7, 3)
