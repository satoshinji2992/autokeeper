"""Compile-time adaptation (`autokeeper upgrade`): what a module lacks is added as lines of their own,
in the file's own layout; a pass that breaks the module's checks is taken back."""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest
import yaml

from autokeeper import paths
from autokeeper.module import adapt, yamledit
from autokeeper.module.schema import Module

HAND = """locations:
  - id: square
    name: 广场
    features: [告示栏, 钟楼大门]
    exits: [tower]

  - id: tower
    name: 钟楼
    read_aloud: |
      楼梯又窄又陡。
    exits: [square]
"""

MACHINE = """locations:
- id: square
  name: 广场
  read_aloud: 广场中央立着钟楼，告示栏上贴着一张泛黄的告示。 很长的一句话被折了行，
    接着写在这里。
  exits:
  - tower
- id: tower
  name: 钟楼
  exits:
  - square
npcs: []
"""


@pytest.mark.parametrize("text", [HAND, MACHINE])
def test_a_new_field_goes_into_its_entry_and_nothing_else_moves(text):
    views = [{"key": "square_1", "label": "告示栏近景", "features": ["告示栏"], "prompt": "贴满旧告示的木栏"}]
    out = yamledit.put(text, "locations", "square", "views", views)
    out = yamledit.put(out, "locations", "tower", "art", "tower")
    data = yaml.safe_load(out)
    square, tower = data["locations"]
    assert square["views"] == views and square["exits"] == ["tower"] and tower["art"] == "tower"
    old, new = text.split("\n"), out.split("\n")
    assert [line for line in new if line not in old] == [line for line in new if "views" in line or "square_1" in line
                                                          or "告示栏近景" in line or "features: [告示栏]" in line
                                                          or "- 告示栏" in line or "features:" == line.strip()
                                                          or "贴满旧告示" in line or "art: tower" in line]  # fmt: skip
    assert all(line in new for line in old)  # every line that was there is still there


def test_an_empty_one_line_field_is_filled_in_place():
    text = "events:\n- id: bell\n  title: 钟声\n  art: ''\n  when: D1 21:00\n"
    out = yamledit.put(text, "events", "bell", "art", "moment_bell")
    assert yaml.safe_load(out)["events"][0] == {"id": "bell", "title": "钟声", "art": "moment_bell", "when": "D1 21:00"}
    with pytest.raises(KeyError):
        yamledit.put(text, "events", "nothing", "art", "x")


def test_lists_and_maps_grow_in_their_own_layout():
    hand = "flags:\n  bell: 钟停了\nendings:\n  - id: e1\n    title: 一\n"
    out = yamledit.put_key(yamledit.append(hand, "endings", [{"id": "e2", "title": "二"}]), "flags", "left", "走了")
    assert out.startswith("flags:\n  bell: 钟停了\n  left: 走了\nendings:\n  - id: e1\n    title: 一\n  - id: e2\n")
    assert yaml.safe_load(yamledit.append("", "ties", [{"id": "t"}])) == {"ties": [{"id": "t"}]}


ANSWERS = {
    "npcs": {"npcs": [{"npc": "preacher", "personality": "固执，好面子", "goal": "这条不该写：他原来有 goal"}]},
    "relations": {"relations": [{"a": "preacher", "b": "warden", "kind": "敌对", "value": -40, "why": "不让他上钟楼"},
                                {"a": "ghost", "b": "warden", "kind": "信任"}]},
    "agendas": {"agendas": [{"npc": "warden", "goals": ["守住齿轮间"], "steps": [{"id": "lock", "text": "半夜锁上齿轮间", "when": "D1 23:00",
                                                                             "meet": False}]}]},
    "foreshadow": {"foreshadow": [{"id": "f1", "hint": "咔哒声慢半拍", "where": "tower", "pays_off": "k_motive",
                                   "stages": [{"hint": "咔哒声慢了整整一拍"}]},
                                  {"id": "f2", "hint": "不存在的结论", "where": "tower", "pays_off": "nothing"}]},
    "clueweb": {"clues": [{"id": "c_extra", "title": "旧账本", "text": "账本上记着守钟人每月买一次新弦", "where": "office",
                           "feature": "旧报合订本", "skill": "图书馆使用", "points_to": ["k_motive"]}]},
    "ties": {"ties": [{"id": "t1", "hook": "守钟人是他父亲的老战友", "npc": "warden", "fits": ["重要之人"]}]},
    "endings": {"endings": [{"id": "e_leave", "title": "离开", "flag": "left_town", "meaning": "调查员放弃调查离开了小镇",
                             "text": "火车开出小镇的时候，钟又多敲了一下。"}]},
    "mechanics": {"mechanics": []},
    "history": {"history": [{"when": "二十年前", "text": "守钟人的儿子在钟楼摔死", "who": ["warden", "ghost"], "where": "tower"}]},
    "prelude": {"prelude": [{"art": "square", "text": "小镇的广场中央立着一座老钟楼。"},
                            {"art": "tower", "text": "钟楼里藏着守钟人的秘密。"},  # a place not public yet: shown without it
                            {"text": "最近，钟总是多敲一下。"}]},
}


@pytest.fixture
def example(tmp_path, monkeypatch):
    folder = tmp_path / "example"
    shutil.copytree(paths.EXAMPLE, folder)

    async def judge(cfg, material):  # the review of what was added (verify): finds nothing here
        return adapt.Verdict()

    async def clues(cfg, material):  # and of the clues: nothing either
        return adapt.ClueVerdict()

    monkeypatch.setattr(adapt, "_judge", judge)
    monkeypatch.setattr(adapt, "_judge_clues", clues)
    return folder


async def test_each_pass_adds_only_what_the_module_lacks_and_checks_what_it_adds(example, monkeypatch):
    async def ask(cfg, name, m, **_):
        return adapt.ASK[name][1].model_validate(ANSWERS[name])

    monkeypatch.setattr(adapt, "_ask", ask)
    before = {p.name: p.read_text(encoding="utf-8") for p in example.glob("*.yaml")}
    results = await adapt.adapt(example, cfg=SimpleNamespace(), log=lambda _: None)
    m = Module.load(example)
    assert m.npc("preacher").personality == "固执，好面子" and m.npc("preacher").goal == "让镇上的人听他讲完"
    assert [(b.a, b.b) for b in m.relations] == [("preacher", "warden")]
    assert m.agendas[0].steps[0].when == "D1 23:00" and [f.id for f in m.foreshadow] == ["f1"]
    assert [s.hint for s in m.foreshadow[0].stages] == ["咔哒声慢了整整一拍"]  # with its later stage
    assert m.clue("c_extra").check.skill == "图书馆使用" and m.ties[0].npc == "warden"
    assert m.flags["left_town"] and any(e.condition == "flag:left_town" for e in m.endings)
    assert not [i for i in adapt.audit(m) if i.level == "error"]
    assert (example / "situation.yaml").is_file()
    for name, text in before.items():  # what was there is still there, line for line
        now = (example / name).read_text(encoding="utf-8")
        assert all(line in now.split("\n") for line in text.split("\n")), name
    assert {r.name for r in results if r.written} >= {"npcs", "relations", "agendas", "foreshadow", "clueweb", "ties", "endings", "prelude"}
    assert [f.art for f in m.prelude] == ["square", "", ""]
    assert m.history[0].who == ["warden"] and m.history[0].where == "tower"  # the one not in the module is left out
    again = await adapt.adapt(example, cfg=SimpleNamespace(), log=lambda _: None)  # run again: resumes, nothing left to do
    assert all(r.name in ("clueweb", "npcs", "mechanics") or (r.name == "verify" and not r.written) for r in again)  # still short, or nothing to add


async def test_a_step_on_a_condition_the_module_cannot_read_is_asked_again_then_left_out(example, monkeypatch):
    """A flag nobody declared: asked again once with the reason, then that step is left out, never
    its premise cut away (the review of 2026-10-20). With nothing else in the pass, nothing is written."""
    asked = []

    async def ask(cfg, name, m, **kw):
        asked.append(kw.get("problems", ()))
        return adapt.Agendas.model_validate({"agendas": [{"npc": "warden", "steps": [{"id": "x", "text": "等钟停", "when": "flag:nope"}]}]})

    monkeypatch.setattr(adapt, "_ask", ask)
    text = {p.name: p.read_text(encoding="utf-8") for p in example.glob("*.yaml")}
    results = await adapt.adapt(example, passes=("agendas",), cfg=SimpleNamespace(), log=lambda _: None)
    assert len(asked) == 2 and any("标记 nope 没有声明" in x for x in asked[1])
    assert results[0].left_out and {p.name: p.read_text(encoding="utf-8") for p in example.glob("*.yaml")} == text


def test_the_score_asks_for_a_situation_that_moves_on_its_own():
    from autokeeper.module.schema import Agenda, Bond, Move

    m = Module.load(paths.EXAMPLE)
    bare = adapt.score(m)
    m.agendas = [Agenda(npc="warden", steps=[Move(id="a", text="等人来问", when="flag:bell_stopped")])]
    waiting = adapt.score(m).parts["局面"][0]
    m.agendas[0].steps.append(Move(id="b", text="半夜锁上齿轮间", when="D1 23:00"))
    moving = adapt.score(m).parts["局面"][0]
    assert 0 < waiting < moving  # one that only waits for the party moves half as much
    m.relations = [Bond(a="warden", b="players", kind="戒备", value=-40)]
    assert adapt.score(m).parts["冲突"][0] == 0  # wary of the party alone is not a conflict
    m.relations.append(Bond(a="preacher", b="warden", kind="敌对", value=-40))
    assert adapt.score(m).parts["冲突"][0] == 15 and adapt.score(m).total > bare.total


AGENDAS = """agendas:
- npc: warden
  goals:
  - 守住钟楼
  steps:
  - id: door
    text: 应门招呼来人
  - id: lock
    text: 半夜锁上齿轮间
    when: D1 23:00
  - id: leave
    text: 天亮前下楼离开
    when: D2 05:00
    does: at:warden:square
- npc: preacher
  steps:
  - id: door
    text: 在广场等人来听
"""


def test_a_mark_goes_into_the_agenda_step_it_is_for():
    import yaml

    from autokeeper.module import yamledit

    out = yamledit.put_step(AGENDAS + "foreshadow: []\n", "agendas", "preacher", "door", "meet", False)
    out = yamledit.put_step(out, "agendas", "warden", "door", "meet", True)
    data = yaml.safe_load(out)
    warden, preacher = data["agendas"]
    assert warden["steps"][0]["meet"] is True and "meet" not in warden["steps"][1] and preacher["steps"][0]["meet"] is False
    assert data["foreshadow"] == []
    again = yamledit.put_step(out, "agendas", "warden", "door", "meet", False)  # its own line, put in place
    assert yaml.safe_load(again)["agendas"][0]["steps"][0]["meet"] is False and again.count("meet:") == 2
    with pytest.raises(KeyError):
        yamledit.put_step(out, "agendas", "warden", "nope", "meet", True)


async def test_the_meeting_pass_marks_only_the_steps_left_to_judge(example, monkeypatch):
    (example / "situation.yaml").write_text(AGENDAS, encoding="utf-8")
    asked = []

    async def ask(cfg, name, m, **_):
        asked.append(adapt._material(m, name))
        return adapt.Meets.model_validate({"steps": [
            {"npc": "warden", "step": "door", "meet": True, "once": True},
            {"npc": "preacher", "step": "door", "meet": False, "once": True},  # backstage: once means nothing
            {"npc": "warden", "step": "lock", "meet": False},
            {"npc": "warden", "step": "leave", "meet": True},  # an outcome of its own: not its to mark
        ]})

    monkeypatch.setattr(adapt, "_ask", ask)
    assert adapt.wanted(Module.load(example), "meet")
    await adapt.adapt(example, passes=("meet",), cfg=SimpleNamespace(), log=lambda _: None)
    m = Module.load(example)
    steps = {(a.npc, s.id): s.meet for a in m.agendas for s in a.steps}
    assert steps == {("warden", "door"): True, ("warden", "lock"): False, ("warden", "leave"): None, ("preacher", "door"): False}
    assert "天亮前下楼离开" not in asked[0]  # only what is left to judge is asked about
    assert [s.once for a in m.agendas for s in a.steps if s.id == "door"] == [True, None]
    assert not adapt.wanted(m, "meet")  # run again: nothing left


@pytest.fixture
def dry(monkeypatch):
    """Every way adapt reaches a model, and the configuration it would load, noted and refused (a
    pass's call fails inside asyncio.gather, so the note is what the test reads)."""
    calls: list[str] = []

    def refuse(name: str):
        def call(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"a dry run asks no model ({name})")

        return call

    for name in ("_ask", "_judge", "_judge_clues", "review", "Session"):
        monkeypatch.setattr(adapt, name, refuse(name))
    monkeypatch.setattr(adapt.config, "load", refuse("config.load"))
    return calls


async def test_a_dry_run_says_which_passes_would_run_and_why(example, dry):
    """`upgrade --dry-run` asked the model for every pass it meant to run (the review of 2026-10-22):
    now it only reports, by what the module lacks (need), and writes nothing."""
    files = {p: p.read_bytes() for p in example.rglob("*") if p.is_file()}
    lines: list[str] = []
    results = await adapt.adapt(example, dry=True, log=lines.append)
    m = Module.load(example)
    assert [r.name for r in results] == list(adapt.PASSES)
    assert all(r.need == adapt.need(m, r.name) and bool(r.need) == adapt.wanted(m, r.name) for r in results[:-1])
    assert results[0].need == "缺性格、口吻、目标或所知的人物：街头布道者" and not results[-1].written
    assert any("人物补全：要补（缺性格" in line for line in lines) and {p: p.read_bytes() for p in files} == files
    alone = await adapt.adapt(example, passes=("verify",), dry=True, log=lines.append)  # the whole layer, by rule alone
    assert [r.name for r in alone] == ["verify"] and {p: p.read_bytes() for p in files} == files
    assert dry == []


def test_upgrade_dry_run_from_the_command_line_finds_the_example_and_asks_no_model(dry):
    from typer.testing import CliRunner

    from autokeeper.cli import app

    files = {p: p.read_bytes() for p in paths.EXAMPLE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    out = CliRunner().invoke(app, ["upgrade", "example", "--dry-run"])
    text = out.output.replace("\n", "")
    assert out.exit_code == 0, out.output
    m = Module.load(paths.EXAMPLE)
    for name in adapt.PASSES[:-1]:
        assert f"{adapt.LABELS[name]}：{'要补' if adapt.need(m, name) else '已经有了'}" in text, name
    assert f"可玩度 {adapt.score(m).total} / 100" in text and "推演" in text
    assert {p: p.read_bytes() for p in files} == files
    out = CliRunner().invoke(app, ["upgrade", "example", "--dry-run", "--review"])  # the review is a call
    assert out.exit_code == 1 and "--review" in out.output and dry == []


async def test_the_present_pass_names_who_each_event_puts_before_the_investigators(example, monkeypatch):
    """2026-10-22: the read-aloud had the truck's driver step down, the record had him nowhere. The pass
    writes Event.present from the read-aloud and the original, and only for events that lack it."""
    story = example / "story.yaml"
    text = story.read_text(encoding="utf-8").replace("    present: [preacher]", "    # present")  # as an import leaves it
    story.write_text(yamledit.append(text, "events", [{"id": "truck", "title": "卡车停下", "when": "visit:office",
                                                       "text": "开车的人下车", "read_aloud": "驾驶座上的人下来，他开口前先咳了一声。"}]),
                     encoding="utf-8")
    m = Module.load(example)
    assert adapt.need(m, "present") == "朗读里有人到场、没写出场人物的事件：truck"
    assert "驾驶座上的人下来" in adapt._material(m, "present") and "布道者登上台阶" in adapt._material(m, "present")
    asked = []

    async def ask(cfg, name, m, **_):
        asked.append(name)
        return adapt.Appearances(events=[adapt.Appearance(event="truck", present=["warden", "nobody"]),
                                         adapt.Appearance(event="e_sermon", present=["preacher"]),
                                         adapt.Appearance(event="e_dusk", present=[])])

    monkeypatch.setattr(adapt, "_ask", ask)
    [result] = await adapt.adapt(example, passes=("present",), cfg=SimpleNamespace(), log=lambda _: None)
    m = Module.load(example)
    assert asked == ["present"] and result.written == 2
    assert {e.id: e.present for e in m.events} == {"e_dusk": [], "e_sermon": ["preacher"], "e_finale": [], "truck": ["warden"]}
    assert not adapt.wanted(m, "present") and not [i for i in adapt.audit(m) if i.level == "error"]


async def test_the_clue_review_reports_what_the_original_gives_on_a_condition_and_writes_nothing(example, monkeypatch):
    """A clue's words that make plain fact of what the original has someone say only when asked
    (2026-10-22: the shrapnel in the driver's chest): reported with the original's words, never rewritten."""
    seen = []

    async def clues(cfg, material):
        seen.append(material)
        return adapt.ClueVerdict(flags=[
            adapt.ClueFlag(clue="c_diary", kind="条件", original="被说服以后他才拿出日记", problem="写成了一见面就有", fix="去掉……"),
            adapt.ClueFlag(clue="c_nothing", kind="出处", original="", problem="没有这条", fix="")])

    monkeypatch.setattr(adapt, "_judge_clues", clues)
    files = {p: p.read_bytes() for p in example.rglob("*") if p.is_file()}
    lines: list[str] = []
    out = await adapt.verify(example, SimpleNamespace(), log=lines.append)
    assert "【要核对的线索】" in seen[0] and "[c_diary]「守钟人的日记」在 tower，出自 warden" in seen[0]
    assert "[c_notice]「广场告示」在 square，不出自某个人" in seen[0] and "线索原文：" in seen[0]
    assert out.problems == ["线索 c_diary（条件）：写成了一见面就有｜原文：被说服以后他才拿出日记｜改：去掉……"]
    assert any(line.startswith("  [yellow]要改 线索 c_diary") for line in lines)
    assert {p: p.read_bytes() for p in files} == files  # the clue's words are the module's: reported, never rewritten


def test_every_place_that_writes_a_clues_words_keeps_conditions_out_of_them():
    """What someone says only when asked, or what comes only on a condition, is no plain fact of a clue:
    the importer's brief, the schema it reads, the clue web's new clues and the clue review all say so."""
    from autokeeper.module import importer
    from autokeeper.module.schema import Clue

    for text in (importer.BRIEF, Clue.model_fields["text"].description, adapt.ASK["clueweb"][0],
                 adapt.NewClue.model_fields["text"].description, adapt.CLUES_SYSTEM):
        assert "被问到才说的话" in text, text[:40]
    assert "朗读里出现在调查员眼前的 NPC" in importer.BRIEF and "present" in importer.BRIEF
