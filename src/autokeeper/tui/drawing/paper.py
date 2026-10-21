"""Handouts on paper: a letter, a diary page, a newspaper cutting, a telegram, a book's page.

Each kind of handout (Handout.kind) is laid out the way the thing itself looks: a letter's indented
paragraphs and its signature on the right, a diary's ruled lines with the dates in bold and a red
margin, a newspaper's masthead between rules, a telegram's pasted strips of tape, a book's page
between ornaments. Paper and ink have their own colours whatever the theme, with a shadow under
the sheet. The user, 2026-10-21: letters and books were an ASCII box and plain words.

A sheet is a list of lines of (text, style) pieces, so the modal can let the ink soak in (reveal):
characters past the count are drawn as blank paper, and nothing moves as they come."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rich.style import Style
from rich.text import Text
from wcwidth import wcswidth, wcwidth

Piece = tuple[str, Style]
Line = list[Piece]


@dataclass(frozen=True)
class Paper:
    paper: str  # the sheet's colour
    ink: str
    faint: str  # rules, ornaments, stains
    accent: str  # dates, signatures, mastheads
    edge: str  # top and bottom edges of the sheet: torn, cut or plain
    hand: bool = False  # written by hand: the words in italics, where the terminal has them


LETTER = Paper("#eee3c6", "#2e2416", "#cdbf9e", "#6b4422", "▁", hand=True)
DIARY = Paper("#f2ecd9", "#22314f", "#c9c0a8", "#a8463f", "▔", hand=True)
NEWS = Paper("#e7e3d6", "#1d1d1d", "#a9a59a", "#1d1d1d", "▁")
WIRE = Paper("#efe0a3", "#1f1f1f", "#c7b878", "#5c4a12", "▁")
BOOK = Paper("#e4d3a8", "#3a2412", "#c9b47f", "#7a4f1f", "▁")
CARD = Paper("#dcd6c6", "#2a2620", "#b5ad99", "#5b4d36", "▁")

FAMILIES = {
    "letter": ("信件", "信", "便条", "留言", "请柬", "遗书"),
    "diary": ("日记", "笔记", "病历", "手记", "记录", "日志", "供词", "口供", "陈述"),
    "news": ("报纸", "剪报", "传单", "告示", "海报", "广告", "布告", "通告", "封条"),
    "wire": ("电报",),
    "book": ("典籍", "经文", "书", "书页", "古籍", "咒文", "手抄本", "抄本"),
}
PAPERS = {"letter": LETTER, "diary": DIARY, "news": NEWS, "wire": WIRE, "book": BOOK, "card": CARD}
DATE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}\s*年|[一二三四五六七八九十〇零\d]{1,3}\s*月\s*[一二三四五六七八九十\d]{1,3}\s*日)")
NOTE = re.compile(r"^（AutoKeeper[^）]*）\s*$")  # the tool's own remark (依原文大意补写): not part of the paper, never shown
SHEET = set(" 　▁▔┃┈═─◥")  # what the paper is made of, there before the ink


def _structure(text: str) -> bool:
    return all(c in SHEET for c in text)


def family(kind: str) -> str:
    kind = (kind or "").strip()
    for name, kinds in FAMILIES.items():
        if kind in kinds or any(k in kind for k in kinds if len(k) > 1):
            return name
    return "card"  # maps, sketches, pictures: a caption card


def width_of(s: str) -> int:
    w = wcswidth(s)
    return w if w >= 0 else len(s)


def wrap(text: str, width: int, *, indent: int = 0) -> list[str]:
    """A paragraph cut to `width` cells; the first line indented `indent` cells (two full-width spaces
    is how Chinese paragraphs start)."""
    return ["".join(t for t, _ in line) for line in wrap_pieces([(text, Style())], width, indent=indent)]


def wrap_pieces(pieces: list[Piece], width: int, *, indent: int = 0) -> list[Line]:
    """Styled words cut to `width` cells, each piece keeping its style across the cuts."""
    lead = "　" * (indent // 2)
    out: list[Line] = []
    line: Line = [(lead, pieces[0][1])] if lead and pieces else []
    used = width_of(lead)
    for text, style in pieces:
        run = ""
        for ch in text:
            cw = max(0, wcwidth(ch))
            if used + cw > width:
                line.append((run, style))
                out.append([p for p in line if p[0]])
                line, run, used = [], "", 0
            run += ch
            used += cw
        line.append((run, style))
    out.append([p for p in line if p[0]])
    return out


@dataclass
class Sheet:
    lines: list[Line]
    width: int = 60
    caption: str = ""  # the handout's title over a sheet that has no place for it (a letter)

    @property
    def size(self) -> int:
        """Characters the ink has to reach: the words, not the paper's edges, margins and rules."""
        return sum(len(t) for line in self.lines for t, _ in line if not _structure(t))

    def render(self, reveal: int | None = None) -> Text:
        """The sheet with the first `reveal` characters inked (all when None): the rest blank paper."""
        out = Text()
        if self.caption:
            out.append(self.caption + "\n", style="bold")
        left = self.size if reveal is None else reveal
        for i, line in enumerate(self.lines):
            for text, style in line:
                if _structure(text):  # the sheet itself is there from the start
                    out.append(text, style=style)
                elif left >= len(text):
                    out.append(text, style=style)
                    left -= len(text)
                else:
                    shown, hidden = text[:max(0, left)], text[max(0, left):]
                    left = 0
                    out.append(shown, style=style)
                    out.append("".join(" " * max(1, wcwidth(c)) for c in hidden), style=Style(bgcolor=style.bgcolor))
            if i < len(self.lines) - 1:
                out.append("\n")
        return out


class _Page:
    """Builds the lines of one sheet: the paper's width, its margins, and a shadow on the right."""

    def __init__(self, paper: Paper, width: int, margin: int = 3):
        self.p, self.width, self.margin = paper, width, margin
        self.inner = width - 2 * margin
        self.lines: list[Line] = []
        self.bg = Style(bgcolor=paper.paper)
        self.ink = Style(color=paper.ink, bgcolor=paper.paper, italic=paper.hand)
        self.shadow = Style(bgcolor="#14110d")

    def style(self, color: str | None = None, **kw) -> Style:
        kw.setdefault("italic", self.p.hand)
        return Style(color=color or self.p.ink, bgcolor=self.p.paper, **kw)

    def row(self, pieces: list[Piece], *, align: str = "left", left: Piece | None = None) -> None:
        """One line of the sheet: margins, the pieces placed as asked, paper to the edge, a shadow."""
        used = sum(width_of(t) for t, _ in pieces)
        room = max(0, self.inner - used)
        before = room // 2 if align == "center" else room if align == "right" else 0
        margin_left = [left] if left else [(" " * self.margin, self.bg)]
        lead = width_of(left[0]) if left else self.margin
        line: Line = [*margin_left, (" " * before, self.bg), *pieces,
                      (" " * (room - before + self.margin + self.margin - lead), self.bg), (" ", self.shadow)]
        self.lines.append(line)

    def blank(self) -> None:
        self.row([])

    def edge(self, top: bool) -> None:
        """The sheet's top or bottom edge: a thin line of paper against the screen."""
        char = self.p.edge if top else ("▔" if self.p.edge == "▁" else "▁")
        if top:
            self.lines.append([(char * self.width, Style(color=self.p.paper)), (" ", Style())])
        else:
            self.lines.append([(" ", Style()), (char * self.width, Style(color="#14110d"))])

    def text(self, words: str | list[Piece], style: Style | None = None, *, indent: int = 0, align: str = "left",
             left: Piece | None = None) -> None:
        pieces = [(words, style or self.ink)] if isinstance(words, str) else words
        for line in wrap_pieces(pieces, self.inner - (width_of(left[0]) - self.margin if left else 0), indent=indent):
            self.row(line, align=align, left=left)


def _parts(text: str) -> list[str]:
    """The lines of the handout itself, without the tool's remark it may start with (the user,
    2026-10-21: no 「AutoKeeper 补充」 where the players read)."""
    lines = text.strip("\n").split("\n")
    if lines and NOTE.match(lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return lines


def _signature(lines: list[str]) -> int:
    """How many lines at the end are the signature: short, after the body (a dash, a name, a date)."""
    tail = 0
    for line in reversed(lines):
        s = line.strip()
        if not s:
            break
        if len(s) <= 14 and (s.startswith(("——", "—", "你的", "您的", "敬上", "谨上")) or (tail < 2 and len(lines) > 3)):
            tail += 1
            continue
        break
    return tail if tail < len(lines) else 0


def letter(_title: str, lines: list[str], width: int) -> list[Line]:  # the title goes over the sheet (Sheet.caption)
    page = _Page(LETTER, width)
    page.edge(top=True)
    page.blank()
    page.row([("◥", page.style(LETTER.faint))], align="right")  # the corner folded down
    sign = _signature(lines)
    body = lines[: len(lines) - sign]
    for i, line in enumerate(body):
        s = line.strip()
        if not s:
            page.blank()
        elif i == 0 and s.endswith(("：", ":")) and len(s) <= 24:  # 亲爱的…： no indent
            page.text(s, page.style(bold=True))
        elif DATE.match(s) and len(s) <= 20:
            page.text(s, page.style(LETTER.accent, italic=True), align="right")
        else:
            page.text(s, indent=4)
    if sign:
        page.blank()
        for s in lines[len(lines) - sign:]:
            page.text(s.strip(), page.style(LETTER.accent, italic=True), align="right")
    page.blank()
    page.edge(top=False)
    return page.lines


def diary(title: str, lines: list[str], width: int) -> list[Line]:
    page = _Page(DIARY, width, margin=2)
    rule = ("┃ ", page.style(DIARY.accent))  # the red margin
    page.edge(top=True)
    page.text(title, page.style(DIARY.ink, bold=True), left=rule)
    page.row([("┈" * (page.inner - 2), page.style(DIARY.faint))], left=rule)
    for line in lines:
        s = line.strip()
        if not s:
            page.row([], left=rule)
            continue
        found = DATE.match(s)
        if found:  # the day's date in bold, what follows in the hand
            head, rest = s[: found.end()].strip(), s[found.end():].lstrip("：: ")
            page.text(head, page.style(DIARY.accent, bold=True), left=rule)
            if rest:
                page.text(rest, left=rule)
        else:
            page.text(s, left=rule)
    page.row([], left=rule)
    page.edge(top=False)
    return page.lines


def news(title: str, lines: list[str], width: int) -> list[Line]:
    page = _Page(NEWS, width, margin=3)
    page.edge(top=True)
    page.row([("═" * page.inner, page.style(NEWS.accent))])
    page.text(title, page.style(NEWS.accent, bold=True), align="center")
    page.row([("─" * page.inner, page.style(NEWS.faint))])
    first = True
    for line in lines:
        s = line.strip()
        if not s:
            page.blank()
            continue
        lead = re.match(r"^(【[^】]+】)(.*)$", s)
        if lead:  # 【本报讯】 in bold, the story after it
            page.text([(lead.group(1), page.style(bold=True)), (lead.group(2), page.ink)], indent=4)
        elif first and len(s) <= 18:  # a headline
            page.text(s, page.style(bold=True), align="center")
        else:
            page.text(s, indent=4)
        first = False
    page.row([("═" * page.inner, page.style(NEWS.accent))])
    page.edge(top=False)
    return page.lines


def wire(title: str, lines: list[str], width: int) -> list[Line]:
    page = _Page(WIRE, width, margin=2)
    page.edge(top=True)
    page.row([("电  报", page.style(WIRE.accent, bold=True)), ("    TELEGRAM", page.style(WIRE.faint))], align="center")
    page.row([("─" * page.inner, page.style(WIRE.faint))])
    page.text(title, page.style(WIRE.accent), align="center")
    page.blank()
    tape = Style(color=WIRE.ink, bgcolor="#fbf6e2")
    for line in lines:
        s = line.strip()
        if not s:
            page.blank()
            continue
        for chunk in wrap(s, page.inner - 2):  # pasted strips of tape, a line each
            page.row([(" " + chunk + " ", tape)])
    page.blank()
    page.edge(top=False)
    return page.lines


def book(title: str, lines: list[str], width: int) -> list[Line]:
    page = _Page(BOOK, width, margin=4)
    page.edge(top=True)
    page.row([("❦  ✥  ❦", page.style(BOOK.accent))], align="center")
    page.text(title, page.style(BOOK.accent, bold=True), align="center")
    page.row([("─── ✥ ───", page.style(BOOK.faint))], align="center")
    page.blank()
    for line in lines:
        s = line.strip()
        if not s:
            page.blank()
        elif s.startswith(("·", "•", "-")):
            page.text("❧ " + s.lstrip("·•- "), page.style())
        else:
            page.text(s, indent=4)
    page.blank()
    page.row([("❧", page.style(BOOK.faint))], align="center")
    page.edge(top=False)
    return page.lines


def card(title: str, lines: list[str], width: int) -> list[Line]:
    page = _Page(CARD, width, margin=3)
    page.edge(top=True)
    page.text(title, page.style(CARD.accent, bold=True), align="center")
    page.row([("─" * page.inner, page.style(CARD.faint))])
    for line in lines:
        s = line.strip()
        page.text(s, page.style(italic=True)) if s else page.blank()
    page.blank()
    page.edge(top=False)
    return page.lines


LAYOUTS = {"letter": letter, "diary": diary, "news": news, "wire": wire, "book": book, "card": card}


def sheet(kind: str, title: str, text: str, width: int = 60) -> Sheet:
    """The handout as its kind of paper."""
    lines = _parts(text)
    name = family(kind)
    return Sheet(LAYOUTS[name](title, lines, width), width=width + 1, caption=title if name == "letter" else "")
