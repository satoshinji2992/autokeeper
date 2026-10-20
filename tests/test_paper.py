"""Handouts on their kind of paper (tui/drawing/paper.py)."""

from __future__ import annotations

import pytest
from rich.cells import cell_len

from autokeeper import paths
from autokeeper.module.schema import Module
from autokeeper.tui.drawing import paper

LETTER = "亲爱的调查员：\n我叔叔失踪已经三个星期了。书架上少了六本书，我想请您来看看。\n\n——托马斯·金博尔\n1922年10月3日"


def widths(sheet: paper.Sheet, reveal: int | None = None) -> set[int]:
    lines = sheet.render(reveal).split("\n")
    skip = bool(sheet.caption) + bool(sheet.note)
    return {cell_len(line.plain) for line in lines[skip:]}


@pytest.mark.parametrize("module", ["zhuishuren", "dongxuezhixia", "fallout3-youxierenyouyanbujin"])
def test_every_bundled_handout_is_a_sheet_of_even_width_that_does_not_move_as_the_ink_comes(module):
    m = Module.load(paths.MODULES / module)
    for h in m.handouts:
        sheet = paper.sheet(h.kind, h.title, h.text, width=60)
        assert widths(sheet) == {61}, (h.id, widths(sheet))  # the sheet and its shadow
        assert widths(sheet, sheet.size // 3) == {61}  # half inked: blank paper keeps the place
        assert "AutoKeeper" not in "".join(t for line in sheet.lines for t, _ in line)  # the tool's remark is over it


def test_each_kind_is_laid_out_as_the_thing_itself():
    assert {paper.family(k) for k in ("信件", "便条")} == {"letter"}
    assert {paper.family(k) for k in ("日记", "笔记", "病历")} == {"diary"}
    assert {paper.family(k) for k in ("报纸", "传单", "告示")} == {"news"}
    assert paper.family("电报") == "wire" and paper.family("典籍") == "book" and paper.family("手绘地图") == "card"
    letter = paper.sheet("信件", "托马斯的信", LETTER)
    rows = letter.render().plain.split("\n")
    assert rows[0] == "托马斯的信"  # a letter's title over the sheet
    signed = next(r for r in rows if "托马斯·金博尔" in r)
    assert signed.rstrip().endswith("托马斯·金博尔")  # the signature on the right
    body = next(r for r in rows if "我叔叔" in r)
    assert "　　我叔叔" in body  # a paragraph's first line indented two characters
    diary = paper.sheet("病历", "病历", "01/12/21：患者男，37 岁。")
    dated = next(line for line in diary.lines if any(t == "01/12/21" for t, _ in line))
    assert next(s for t, s in dated if t == "01/12/21").bold  # the day in bold, on the red margin
    assert any("┃" in t for t, _ in dated)


def test_the_ink_comes_in_order_and_a_blank_page_is_paper():
    sheet = paper.sheet("信件", "托马斯的信", LETTER)
    assert "亲" not in sheet.render(0).plain and "亲爱的" in sheet.render(3).plain
    assert sheet.render().plain == sheet.render(sheet.size).plain
