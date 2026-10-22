"""Foreshadowing that changes across visits (Foreshadow.stages, engine/signs.py): one sign per
arrival, timed and flagged stages, waking up where it shows, the picture that changes with it
(planned at compile time, drawn once the story gets near it, never ahead of play), `autokeeper
check`, the compile step, and what the replay eval sees of the hints."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from autokeeper import config, credits
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import check
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module import adapt
from autokeeper.module.graph import audit
from autokeeper.module.schema import Foreshadow, Look, Module, Stage
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, make
from .test_imagegen import RED, codex, picture  # noqa: F401  (codex: a fixture, the codex process stood in for)

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


# -- a step's picture: planned at compile time, drawn once the story gets near it ----------------------
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


async def test_the_compile_step_gives_written_signs_their_stages_and_leaves_their_words(tmp_path, monkeypatch):
    """A module whose signs never change (the bundled ones, written before stages): the pass keeps
    each sign as written and only adds the stages, under the sign's own id."""
    folder = tmp_path / "example"
    shutil.copytree(EXAMPLE, folder)
    written = Foreshadow(id="sign_painting", hint="告示栏旁挂着一幅海边风景画，岸边空无一人", where="square", pays_off="k_tower")
    (folder / "situation.yaml").write_text(yaml.safe_dump({"foreshadow": [written.model_dump(mode="json", exclude_defaults=True)]},
                                                         allow_unicode=True, sort_keys=False), encoding="utf-8")
    m = Module.load(folder)
    assert adapt.wanted(m, "foreshadow")
    answer = adapt.Signs(foreshadow=[adapt.SignDraft(
        id="sign_painting", hint="改写过的端倪", where="office", pays_off="k_tower",  # its words and place are not taken
        stages=[adapt.StageDraft(hint="画里的海岸边多了一个背对着人的身影", picture=FIGURE)])])

    async def ask(cfg, name, m, **_):
        return answer

    monkeypatch.setattr(adapt, "_ask", ask)
    await adapt.adapt(folder, passes=("foreshadow",), cfg=SimpleNamespace(), log=lambda _: None)
    sign = Module.load(folder).foreshadow[0]
    assert (sign.hint, sign.where) == ("告示栏旁挂着一幅海边风景画，岸边空无一人", "square")
    assert [(s.hint, s.flag) for s in sign.stages] == [("画里的海岸边多了一个背对着人的身影", "sign_painting_1")]  # no sign_sign_
    after = Module.load(folder)
    assert after.location("square").looks == [Look(when="flag:sign_painting_1", change=FIGURE)]
    assert not adapt.wanted(after, "foreshadow")  # a sign that changes: the pass is done
    found = adapt.edits(m, "foreshadow", answer)
    assert adapt.without(found, {"foreshadow:sign_painting:1"}) == []  # its only stage held back: nothing left to write


async def drawn(game) -> None:
    """Until the pictures drawn in the background are done (one done may start the next)."""
    for _ in range(8):
        if not (busy := [t for t in game.visuals.tasks.values() if not t.done()]):
            return
        await asyncio.gather(*busy)


def pictured(module: Module, tmp_path) -> Path:
    """Both steps of the painting change the square's picture, which is a picture of its own (blue)."""
    module.flags["figure_shown"] = "广场那幅画里多了一个人影（伏笔露出时由引擎设置）"
    stages = module.foreshadow[0].stages
    stages[0].flag, stages[1].when = "figure_shown", ""
    module.location("square").looks = [Look(when="flag:figure_shown", change=FIGURE), Look(when="flag:painting_turned", change=TURNED)]
    original = picture(tmp_path / "art" / "square.jpg")
    module.pictures["square"] = str(original)
    return original


def ahead_runs(game, key: str) -> int:
    """How many times the debug journal saw the picture for `key` drawn ahead."""
    rows, _ = game.debug_journal.read(kind="task")
    return sum(1 for r in rows if r["data"]["name"] == f"redraw-ahead:{key}" and r["data"]["status"] == "running")


async def test_the_next_steps_picture_is_drawn_once_meanwhile_and_shown_the_moment_it_comes(module, fake, codex, tmp_path):  # noqa: F811
    original = pictured(module, tmp_path)
    module.location("tower").art = "tower"  # a sign where the party never comes: nothing is drawn for it
    module.flags["crack_grew"] = "钟楼的墙裂得更开了（伏笔露出时由引擎设置）"
    module.foreshadow.append(Foreshadow(id="f_crack", hint="钟楼的墙上有一道裂缝", where="tower", pays_off="k_tower",
                                        stages=[Stage(hint="裂缝又长了一截", flag="crack_grew")]))
    module.location("tower").looks = [Look(when="flag:crack_grew", change="墙上的裂缝爬到了窗边")]
    game, _, events = make(module, fake, {}, location="office")
    game.visuals.cfg = config.Config()
    st = game.state
    await visit(game, "square")  # the first hint: the first step's picture drawn meanwhile, from the square's own
    await drawn(game)
    first = st.looks["square_figure_shown"]
    assert first["status"] == "ready" and first["made"] == "1" and first["when"] == "flag:figure_shown"
    assert len(codex.prompts) == 1 and FIGURE in codex.prompts[0] and codex.attached
    assert module.pictures["square"] == str(original) and "square" not in st.looks  # kept, not shown before its step
    assert not [e for e in events if e["type"] == "art"]
    game.visuals.ahead(["figure_shown"])  # asked again, or the game scheduled again: drawn once
    game.visuals.schedule()
    await drawn(game)
    assert len(codex.prompts) == 1 and game.visuals.made() == 1
    await visit(game, "square")  # the first step shows: its picture at once, the second's drawn meanwhile from it
    assert module.pictures["square"] == first["picture"] and st.looks["square"]["applied"] == "flag:figure_shown"
    await drawn(game)
    assert [e["picture"] for e in events if e["type"] == "art" and e["key"] == "square"] == [first["picture"]]
    second = st.looks["square_painting_turned"]
    assert second["status"] == "ready" and len(codex.prompts) == 2 and TURNED in codex.prompts[1]
    assert FIGURE[:20] in credits.get(Path(second["picture"]))["based_on"]["title"]  # drawn from the first step's picture
    assert module.pictures["square"] == first["picture"]  # still the first: the second waits for its step
    saved = GameState.model_validate_json(st.model_dump_json())
    await game.close()
    # A loaded game shows the first step's picture, holds the second, and draws neither again
    loaded = Module.load(EXAMPLE)
    loaded.foreshadow = [f.model_copy(deep=True) for f in module.foreshadow]
    loaded.flags, loaded.locations = dict(module.flags), [x.model_copy(deep=True) for x in module.locations]
    loaded.pictures["square"] = str(original)
    again = Game(loaded, saved, FakeCast({}), Rules(dice=fake))
    again.visuals.cfg = config.Config()
    again.visuals.schedule()
    assert loaded.pictures["square"] == first["picture"] and loaded.pictures["square_painting_turned"] == second["picture"]
    assert not again.visuals.tasks
    await visit(again, "square")  # the second step: its picture, at once
    await drawn(again)
    assert loaded.pictures["square"] == second["picture"] and len(codex.prompts) == 2 and again.visuals.made() == 2
    assert ahead_runs(again, "square_figure_shown") == ahead_runs(again, "square_painting_turned") == 1
    assert "tower_crack_grew" not in saved.looks  # the sign they never met cost nothing
    await again.close()


async def test_a_step_that_comes_while_its_picture_is_drawn_shows_it_when_done(module, fake, codex, tmp_path):  # noqa: F811
    pictured(module, tmp_path)
    game, _, _ = make(module, fake, {}, location="office")
    game.visuals.cfg = config.Config()
    game.visuals.pause()  # a turn is being told: the drawing waits
    await visit(game, "square")
    await visit(game, "square")  # the first step shows before its picture is drawn
    assert "figure_shown" in game.state.flags and "square" not in game.state.looks  # the old picture stays
    game.visuals.resume()
    await drawn(game)
    first = game.state.looks["square_figure_shown"]
    assert module.pictures["square"] == first["picture"] and game.state.looks["square"]["applied"] == "flag:figure_shown"
    assert len([p for p in codex.prompts if FIGURE in p]) == 1  # drawn ahead, never again for its step
    await game.close()


async def test_a_picture_drawn_ahead_that_fails_keeps_play_going_and_is_drawn_when_its_step_comes(module, fake, codex, tmp_path):  # noqa: F811
    original = pictured(module, tmp_path)
    game, _, _ = make(module, fake, {}, location="office")
    game.visuals.cfg = config.Config()
    codex.fail = "usage limit reached"
    first = await visit(game, "square")
    await drawn(game)
    assert game.state.looks["square_figure_shown"]["status"] == "failed" and signs(first)
    assert module.pictures["square"] == str(original)
    codex.fail = ""
    second = await visit(game, "square")  # its step comes: drawn the usual way now
    assert "多了一个背对着人的身影" in signs(second)[0]
    await drawn(game)
    assert near_red(module.pictures["square"]) and game.state.looks["square"]["status"] == "ready"
    assert len([p for p in codex.prompts if FIGURE in p]) == 2  # the failed try, then the step's own drawing
    await game.close()


def near_red(path: str) -> bool:
    from .test_imagegen import colour_of, near

    return near(colour_of(path), RED)


async def test_without_codex_nothing_is_drawn_ahead_and_the_keeper_still_tells_the_change(module, fake, tmp_path):
    original = pictured(module, tmp_path)
    game, _, _ = make(module, fake, {}, location="office")
    game.visuals.cfg = config.Config()  # pictures on, but no Codex here (the conftest's)
    st = game.state
    await visit(game, "square")
    assert not game.visuals.tasks and not st.looks
    turn = await visit(game, "square")
    assert "这次：画里的海岸边多了一个背对着人的身影" in signs(turn)[0] and "figure_shown" in st.flags
    assert module.pictures["square"] == str(original) and st.looks["square"]["status"] == "pending"  # drawn once Codex is here
    assert not any(k.startswith("ahead:") for k in game.visuals.tasks)
    await game.close()


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


async def test_a_hint_whose_telling_failed_is_told_again_on_retry_and_moves_on_once(module, fake):
    """The review of 2026-10-22: the hint was handed out in memory before the keeper was asked; the
    telling failed, /retry reloaded the turn from its journal without the hint, and the sign had
    already moved on: the next try told the next stage, or nothing."""
    from langgraph.checkpoint.memory import InMemorySaver

    from autokeeper.engine import graph

    go = Action(kind="move", summary="周衡去广场", destination="square")
    game, cast, _ = make(module, fake, {"去广场": (go, Ruling(needs_roll=False))}, location="tower")
    told: list[str] = []

    async def keeper(state, report, on_delta=None, lore=""):
        told.append(report)
        if len(told) == 1:
            raise TimeoutError("模型超时")
        return "叙事"

    cast.keeper = keeper
    app = graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "sign"}}
    with pytest.raises(TimeoutError):
        await app.ainvoke({"input": "去广场"}, thread)
    assert game.state.signs == {"f_painting": 1} and game.state.pending_turn
    await app.ainvoke({"input": "/retry"}, thread)
    hints = [[line for line in report.splitlines() if "端倪" in line] for report in told]
    assert len(told) == 2 and hints[0] == hints[1] and "岸边空无一人" in hints[1][0]
    assert game.state.signs == {"f_painting": 1} and not game.state.pending_turn
    await game.close()


async def test_an_interludes_hint_leaves_no_turn_behind(module, fake):
    """Told between turns (an interlude, a sketch), the hint is handed out as before, and no turn is
    left pending for the next input to trip over."""
    from langgraph.checkpoint.memory import InMemorySaver

    from autokeeper.engine import graph

    game, *_ = make(module, fake, {}, location="tower")
    graph.build(game, InMemorySaver())  # the graph's keep is the game's turn checkpoint
    turn = Turn()
    await game._arrive("square", turn)
    turn.interlude = True
    await game.narrate(turn)
    assert signs(turn) and game.state.signs == {"f_painting": 1} and not game.state.pending_turn
    await game.close()
