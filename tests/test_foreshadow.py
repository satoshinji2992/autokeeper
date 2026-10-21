"""Foreshadowing that changes across visits (Foreshadow.stages, engine/signs.py): one sign per
arrival, timed and flagged stages, waking up where it shows, the picture that changes with it,
`autokeeper check`, the compile step, and what the replay eval sees of the hints."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import check
from autokeeper.engine.game import Turn
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
