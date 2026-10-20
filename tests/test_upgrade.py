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
    "foreshadow": {"foreshadow": [{"id": "f1", "hint": "咔哒声慢半拍", "where": "tower", "pays_off": "k_motive"},
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
def example(tmp_path):
    folder = tmp_path / "example"
    shutil.copytree(paths.EXAMPLE, folder)
    return folder


async def test_each_pass_adds_only_what_the_module_lacks_and_checks_what_it_adds(example, monkeypatch):
    async def ask(cfg, name, m):
        return adapt.ASK[name][1].model_validate(ANSWERS[name])

    monkeypatch.setattr(adapt, "_ask", ask)
    before = {p.name: p.read_text(encoding="utf-8") for p in example.glob("*.yaml")}
    results = await adapt.adapt(example, cfg=SimpleNamespace(), log=lambda _: None)
    m = Module.load(example)
    assert m.npc("preacher").personality == "固执，好面子" and m.npc("preacher").goal == "让镇上的人听他讲完"
    assert [(b.a, b.b) for b in m.relations] == [("preacher", "warden")]
    assert m.agendas[0].steps[0].when == "D1 23:00" and [f.id for f in m.foreshadow] == ["f1"]
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
    assert all(r.name in ("clueweb", "npcs", "mechanics") for r in again)  # still short, or nothing to add


async def test_a_pass_that_breaks_the_module_is_taken_back(example, monkeypatch):
    async def ask(cfg, name, m):  # an agenda on a flag nobody declared
        return adapt.Agendas.model_validate({"agendas": [{"npc": "warden", "steps": [{"id": "x", "text": "等钟停", "when": "flag:nope"}]}]})

    monkeypatch.setattr(adapt, "_ask", ask)
    text = {p.name: p.read_text(encoding="utf-8") for p in example.glob("*.yaml")}
    results = await adapt.adapt(example, passes=("agendas",), cfg=SimpleNamespace(), log=lambda _: None)
    assert results[0].problems and {p.name: p.read_text(encoding="utf-8") for p in example.glob("*.yaml")} == text


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

    async def ask(cfg, name, m):
        asked.append(adapt._material(m, name))
        return adapt.Meets.model_validate({"steps": [
            {"npc": "warden", "step": "door", "meet": True},
            {"npc": "preacher", "step": "door", "meet": False},
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
    assert not adapt.wanted(m, "meet")  # run again: nothing left
