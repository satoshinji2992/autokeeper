"""Foreshadowing that changes across visits (Foreshadow.stages, engine/signs.py): one sign per
arrival, timed and flagged stages, waking up where it shows, the picture that changes with it
(planned at compile time), `autokeeper check`, the compile step, and what the replay eval sees of
the hints."""

from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import check
from autokeeper.engine.game import Turn
from autokeeper.module import adapt
from autokeeper.module.graph import audit
from autokeeper.module.schema import Foreshadow, Look, Module, Stage
from autokeeper.paths import EXAMPLE

from .test_engine import make
from .test_imagegen import RED, picture

PAINTING = Foreshadow(id="f_painting", hint="告示栏旁挂着一幅海边风景画，岸边空无一人", where="square", pays_off="k_tower",
                      stages=[Stage(hint="画里的海岸边多了一个背对着人的身影"),
                              Stage(hint="画里那个身影转过了半张脸，脸上没有五官", when="D2 08:00", flag="painting_turned")])


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.flags["painting_turned"] = "广场那幅画里的人转过了脸（伏笔露出时由引擎设置）"
    m.foreshadow = [PAINTING.model_copy(deep=True)]
    return m


async def visit(game, place: str, then: Turn | None = None) -> Turn:
    """Walk away and back: a later arrival there, told."""
    await game._arrive("office" if place != "office" else "square", Turn())
    turn = then or Turn()
    await game._arrive(place, turn)
    await game._foreshadow(turn)
    return turn


def signs(turn: Turn) -> list[str]:
    return [n for n in turn.notes if "端倪" in n]


async def test_a_sign_moves_on_one_step_a_visit_and_the_keeper_hears_what_changed(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    st = game.state
    first = await visit(game, "square")
    assert signs(first) == ["这里可以顺手露出一点端倪，一笔带过，不点破、不解释：告示栏旁挂着一幅海边风景画，岸边空无一人"]
    assert st.signs == {"f_painting": 1}
    second = await visit(game, "square")
    assert len(signs(second)) == 1 and "先前：告示栏旁挂着一幅海边风景画" in signs(second)[0]
    assert "这次：画里的海岸边多了一个背对着人的身影" in signs(second)[0] and st.signs == {"f_painting": 2}
    early = await visit(game, "square")
    assert signs(early) == [] and st.signs == {"f_painting": 2}  # its time has not come
    st.minutes = 1440 + 9 * 60  # D2 09:00
    third = await visit(game, "square")
    assert "这次：画里那个身影转过了半张脸" in signs(third)[0] and st.signs == {"f_painting": 3}
    assert "painting_turned" in st.flags  # its flag, set as it shows
    assert signs(await visit(game, "square")) == []  # nothing past the last
    await game.close()


async def test_a_failed_search_keeps_the_next_stage_back_and_waking_there_brings_it(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    st = game.state
    await visit(game, "square")
    fake.queue(99)
    missed = Turn()
    await game._arrive("office", Turn())
    await game._arrive("square", missed)
    missed.checks.append(game.rules.check(st.investigator, "侦查"))
    await game._foreshadow(missed)
    assert signs(missed) == [] and st.signs == {"f_painting": 1}
    rested = Turn(action=Action(kind="wait", summary="周衡在广场的长椅上睡了一觉"), ruling=Ruling(needs_roll=False, rest=True, minutes=480))
    await game._foreshadow(rested)  # waking up where it shows is coming to it again
    assert "多了一个背对着人的身影" in signs(rested)[0] and st.signs == {"f_painting": 2}
    await game.close()


async def test_a_stage_can_show_somewhere_else_and_hold_off_for_a_flag(module, fake):
    module.foreshadow[0].stages = [Stage(hint="报社墙上挂着同一片海岸的照片，岸边站着一个人", where="office", when="flag:bell_stopped")]
    game, *_ = make(module, fake, {}, location="tower")
    st = game.state
    await visit(game, "square")
    assert signs(await visit(game, "square")) == []  # its next sign is not here
    assert signs(await visit(game, "office")) == []  # nor yet: the bell has not stopped
    st.flags.append("bell_stopped")
    assert "岸边站着一个人" in signs(await visit(game, "office"))[0]
    await game.close()


async def test_a_stages_flag_switches_the_places_picture_with_the_telling(module, fake, tmp_path):
    module.location("square").looks = [Look(when="flag:painting_turned", change="告示栏旁的画里，一个人转过了没有五官的脸")]
    drawn = picture(tmp_path / "art" / "square_painting_turned.jpg", RED)
    module.pictures["square_painting_turned"] = str(drawn)  # drawn by Codex at import
    module.foreshadow[0].stages[1].when = ""
    game, _, events = make(module, fake, {}, location="tower")
    await visit(game, "square")
    await visit(game, "square")
    assert module.pictures.get("square") != str(drawn)
    await visit(game, "square")
    await asyncio.gather(*game.visuals.tasks.values())
    assert module.pictures["square"] == str(drawn)  # the scene's picture is the changed one now
    assert any(e["type"] == "art" and e["key"] == "square" for e in events)
    assert any(e["type"] == "flag" and e["flag"] == "painting_turned" for e in events)
    await game.close()


async def test_the_hints_reach_the_report_and_the_replay_evals_record(module, fake):
    game, *_ = make(module, fake, {}, location="tower")
    await visit(game, "square")
    turn = await visit(game, "square")
    report = game._report(turn)
    hints = [line.removeprefix("备注：") for line in report.splitlines() if "端倪" in line]  # eval/session.py reads them so
    assert len(hints) == 1 and "多了一个背对着人的身影" in hints[0]
    record = check.account(game.m, game.state, game.state.turn, {"minutes": game.state.minutes}, None, hints)
    assert "这一轮守秘人可以露出的端倪" in record and "多了一个背对着人的身影" in record
    await game.close()


def test_check_names_a_stage_whose_place_flag_or_time_is_wrong(module):
    assert not [i for i in audit(module) if i.level == "error"]
    module.foreshadow[0].stages = [Stage(hint="x", where="nowhere"), Stage(hint="y", flag="no_such_flag"),
                                   Stage(hint="z", when="明天早上"), Stage(hint=" ")]
    errors = [i.text for i in audit(module) if i.level == "error"]
    assert any("第 1 步的地点 nowhere 不存在" in e for e in errors)
    assert any("第 2 步设的标记 no_such_flag 未声明" in e for e in errors)
    assert any("第 3 步的 when" in e for e in errors) and any("第 4 步没有写 hint" in e for e in errors)


# -- the compile step (autokeeper upgrade --only foreshadow) ----------------------------------------
def test_the_compile_step_writes_signs_with_their_stages_and_leaves_out_what_gives_a_clue_away(module):
    from autokeeper.module import adapt

    module.foreshadow = []
    clue = module.clue("c_archive").text.strip()
    out = adapt.Signs(foreshadow=[
        adapt.SignDraft(id="f_clock", hint="报社的钟总比广场的慢一刻", where="office", pays_off="k_tower",
                        stages=[adapt.StageDraft(hint="报社的钟停了", where="office", when="D2 08:00"),
                                adapt.StageDraft(hint=clue), adapt.StageDraft(hint="钟面背后写着字", where="nowhere", when="明天")]),
        adapt.SignDraft(id="f_lost", hint="哪里都不指向", where="office", pays_off="nothing"),
    ])
    found = adapt.edits(module, "foreshadow", out)
    assert found == [("append", "foreshadow", [{"id": "f_clock", "hint": "报社的钟总比广场的慢一刻", "where": "office",
                                               "pays_off": "k_tower", "stages": [{"hint": "报社的钟停了", "when": "D2 08:00"}]}])]
    after = adapt.preview(module, found)
    assert "foreshadow:f_clock:1" in {p.ref for p in adapt.conditions.pieces(after)}  # held to the conditions too
    assert not [i for i in audit(after) if i.level == "error"]


def test_a_stage_the_review_flags_is_taken_out_and_its_sign_kept(tmp_path):
    from autokeeper.module import adapt

    from .test_compile_prep import SITUATION, copy

    folder = copy("zhuishuren", tmp_path)
    (folder / "situation.yaml").write_text(SITUATION, encoding="utf-8")
    sign = {"id": "f_window", "hint": "书房窗台上积着一线干土", "where": "shufang", "pays_off": "k_not_burglar",
            "stages": [{"hint": "窗台上的土多了一道新的"}, {"hint": "窗框上多了指甲的划痕"}]}
    found = [("append", "foreshadow", [sign])]
    assert adapt.without(found, {"foreshadow:f_window:1"}) == [("append", "foreshadow", [{**sign, "stages": sign["stages"][1:]}])]
    assert adapt.without(found, {"foreshadow:f_window"}) == []
    adapt.land(folder, found)
    landed = Module.load(folder)
    assert [s.hint for s in landed.foreshadow[-1].stages] == ["窗台上的土多了一道新的", "窗框上多了指甲的划痕"]
    assert not [i for i in audit(landed) if i.level == "error"]
    assert "[foreshadow:f_window:2]" in "\n".join(adapt._rows(landed, {"foreshadow:f_window:2"}))
    adapt.take_out(folder, ["foreshadow:f_window:1"])
    kept = Module.load(folder)
    assert [f.id for f in kept.foreshadow] == ["f_mud", "f_hollow", "f_window"]
    assert [s.hint for s in kept.foreshadow[-1].stages] == ["窗框上多了指甲的划痕"]


# -- a step's picture: planned at compile time ------------------------------------------------------
FIGURE = "告示栏旁的画里，海岸边多了一个背对着人的小小人影"
TURNED = "告示栏旁的画里，那个人影转过了没有五官的脸"


def stage(n: int, where: str = "", picture: str = "") -> adapt.StageDraft:
    return adapt.StageDraft(hint=f"画里的人影又近了一步，这是第 {n} 回看见", where=where, picture=picture)


async def test_the_compile_step_hangs_a_picture_on_a_stage_whose_change_shows_and_draws_none(tmp_path, monkeypatch):
    folder = tmp_path / "example"
    shutil.copytree(EXAMPLE, folder)
    leak = adapt.StageDraft(hint="画里的人影抬起手，指着钟楼的方向", picture="最大的那只齿轮背后缠着一圈不属于钟的东西")  # c_gears' words
    answer = adapt.Signs(foreshadow=[adapt.SignDraft(
        id="f_painting", hint="告示栏旁挂着一幅海边风景画，岸边空无一人", where="square", pays_off="k_tower",
        stages=[adapt.StageDraft(hint="画里的海岸边多了一个背对着人的身影", picture=f" {FIGURE}\n"),
                adapt.StageDraft(hint="报社墙上的挂钟停了，指针停在午夜前", where="office", picture="报社墙上的挂钟停了"),  # no picture there
                leak])])
    asked = []

    async def ask(cfg, name, m, **_):
        asked.append(adapt._material(m, name))
        return answer

    monkeypatch.setattr(adapt, "_ask", ask)
    before = {p.name: p.read_text(encoding="utf-8") for p in folder.glob("*.yaml")}
    await adapt.adapt(folder, passes=("foreshadow",), cfg=SimpleNamespace(), log=lambda _: None)
    assert "【有场景图的地点】（全模组最多再写 6 处 picture）\n- square 广场" in asked[0]  # the tower and the office have none
    m = Module.load(folder)
    assert [s.flag for s in m.foreshadow[0].stages] == ["sign_f_painting_1", "", ""]
    assert m.location("square").looks == [Look(when="flag:sign_f_painting_1", change=FIGURE)]  # no art: drawn in a game
    assert m.flags["sign_f_painting_1"] == f"伏笔 f_painting 第 1 步露出了，场景图换成：{FIGURE}（露出时由引擎设置）"
    assert not m.location("office").looks and m.stage_flags() == {"sign_f_painting_1"}
    assert not [i for i in audit(m) if i.level == "error"]
    for name, text in before.items():  # what was there is still there, line for line
        assert all(line in (folder / name).read_text(encoding="utf-8").split("\n") for line in text.split("\n")), name
    assert "场景图换成：" + FIGURE in "\n".join(adapt._rows(m, {"foreshadow:f_painting:1"}))  # the review reads it too
    adapt.take_out(folder, ["foreshadow:f_painting:1"])  # the review takes the stage out: its picture goes with it
    kept = Module.load(folder)
    assert [s.hint for s in kept.foreshadow[0].stages] == ["报社墙上的挂钟停了，指针停在午夜前", "画里的人影抬起手，指着钟楼的方向"]
    assert not kept.location("square").looks and "sign_f_painting_1" not in kept.flags
    assert {p.name: p.read_text(encoding="utf-8") for p in folder.glob("*.yaml") if p.name in before} == before


def test_picture_stages_keep_to_the_looks_a_module_and_a_place_may_have(module):
    module.foreshadow = []
    module.location("office").art = "office"  # a second place with a picture
    module.location("square").looks = [Look(when="flag:bell_stopped", change="钟楼的影子里一片死寂")]
    module.npc("warden").looks = [Look(when="flag:bell_stopped", change="守钟人瘫坐在楼梯口")]
    module.npc("preacher").looks = [Look(when="flag:bell_stopped", change="布道者哑了嗓子"), Look(when="flag:bell_stopped", change="布道者跪在台阶上")]
    out = adapt.Signs(foreshadow=[
        adapt.SignDraft(id="f_a", hint="告示栏旁挂着一幅海边风景画", where="square", pays_off="k_tower",
                        stages=[stage(1, picture="画里多了一个人影"), stage(2, picture="画里的人影转过了身"), stage(3, "office", "窗上多了一道裂纹")]),
        adapt.SignDraft(id="f_b", hint="报社的窗玻璃上有一道细细的裂纹", where="office", pays_off="k_tower",
                        stages=[stage(4, picture="裂纹爬满了半扇窗")])])
    found = adapt.edits(module, "foreshadow", out)
    signs = found[0][2]
    # Four looks already: two more for the module, one more for the square (two a place)
    assert [s.get("flag", "") for s in signs[0]["stages"]] == ["sign_f_a_1", "", "sign_f_a_3"]
    assert "flag" not in signs[1]["stages"][0]  # none left for the module
    assert found[1:] == [("key", "flags", "sign_f_a_1", "伏笔 f_a 第 1 步露出了，场景图换成：画里多了一个人影（露出时由引擎设置）"),
                         ("key", "flags", "sign_f_a_3", "伏笔 f_a 第 3 步露出了，场景图换成：窗上多了一道裂纹（露出时由引擎设置）"),
                         ("add", "locations", "square", "looks", [{"when": "flag:sign_f_a_1", "change": "画里多了一个人影"}]),
                         ("add", "locations", "office", "looks", [{"when": "flag:sign_f_a_3", "change": "窗上多了一道裂纹"}])]
    after = adapt.preview(module, found)
    assert sum(len(x.looks) for x in [*after.locations, *after.npcs]) == adapt.LOOKS
    assert not [i for i in audit(after) if i.level == "error"]
    assert "（场景图：画里多了一个人影）" in next(p.text for p in adapt.conditions.pieces(after) if p.ref == "foreshadow:f_a:1")
    # A stage held back takes its flag and its look along; the others keep theirs
    kept = adapt.without(found, {"foreshadow:f_a:1"})
    assert [e[:3] for e in kept[1:]] == [("key", "flags", "sign_f_a_3"), ("add", "locations", "office")]
    assert adapt.without(found, {"foreshadow:f_a"}) == [("append", "foreshadow", [signs[1]])]
    module.location("square").looks.append(Look(when="flag:bell_stopped", change="广场上空无一人"))
    module.npc("warden").looks.append(Look(when="flag:bell_stopped", change="守钟人不见了"))  # six: no more anywhere
    assert "不写 picture" in adapt._material(module, "foreshadow")
    assert [e[0] for e in adapt.edits(module, "foreshadow", out)] == ["append"]


def test_check_wants_a_steps_picture_on_a_place(module):
    module.foreshadow[0].stages[0].flag = "figure_shown"  # not declared
    module.npc("warden").looks = [Look(when="flag:painting_turned", change="守钟人回头看那幅画")]
    errors = [i.text for i in audit(module) if i.level == "error"]
    assert any("第 1 步设的标记 figure_shown 未声明" in e for e in errors)
    assert any("NPC warden 的变样图挂在伏笔步骤的标记 painting_turned 上" in e for e in errors)
    module.flags["figure_shown"] = "画里多了一个人影"
    module.npc("warden").looks = []
    module.location("square").looks = [Look(when="flag:painting_turned", change=TURNED)]
    assert not [i for i in audit(module) if i.level == "error"]
