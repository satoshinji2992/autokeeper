"""Making an investigator: out of a public figure (look them up, pick their photo, answer
three questions), or from a card the player already has; then keep it or say what to change."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import config, imagery, paths, roster
from ..agents.card import Builder, CardError, Figure, Photo, Proposal, local_file
from ..module.schema import Module
from ..rules.coc import Investigator

ANY = "你来定"
PHOTO = (40, 18)  # the photo in the preview pane, in cells
SHOWN = 8  # photo candidates offered


def sheet(inv: Investigator, picture: Text | None = None, note: str = "", fixes: list[str] | None = None) -> Text:
    """The whole card: photo, numbers, story."""
    t = Text()
    if picture:
        t.append_text(picture)
        t.append("\n")
    t.append(f"\n{inv.name}", style="bold")
    t.append(f"  {inv.occupation} · {inv.age}岁{' · ' + inv.sex if inv.sex else ''}\n", style="dim")
    t.append("  ".join(f"{k}{v}" for k, v in inv.characteristics.items()) + "\n")
    t.append(f"HP {inv.hp_max}  MP {inv.mp_max}  理智 {inv.san}  幸运 {inv.luck}  MOV {inv.move}  DB {inv.damage_bonus}\n\n", style="bold")
    t.append("  ".join(f"{k} {v}" for k, v in inv.skills.items()) + "\n\n")
    for k, v in inv.backstory.items():
        if k != "说明":
            t.append(f"{k}：", style="bold")
            t.append(f"{v}\n")
    if inv.inventory:
        t.append("随身：", style="bold")
        t.append("、".join(inv.inventory) + "\n")
    if note:
        t.append(f"\n{note}\n", style="italic")
    for fix in fixes or []:
        t.append(f"· {fix}\n", style="dim")
    return t


def _host(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.") or "网页"


IMPORTS = Text.assemble(
    ("能读的卡\n", "bold"),
    "· 骰娘的 .st 指令，比如 .st 力量60敏捷50…侦查60\n",
    "· 车卡器导出的表格、PDF 或 Word 人物卡、纸质卡的照片\n",
    "· 酒馆（SillyTavern）的角色卡 PNG 或 JSON：按人设重新车成这个模组的调查员\n",
    "· 一段人物描述\n\n",
    ("原卡的数值照原样录入，不合规则的地方会标出来。文件可以直接拖进终端。", "dim"),
)


class CardScreen(Screen[Investigator | None]):
    """name → photo → questions → anything else → the card → keep it, or change it.
    Imported: the card as it was (or, for a character without numbers, the questions)."""

    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, module: Module, builder: Builder | None = None, *, start: str = "figure"):
        super().__init__()
        self.m = module
        self.start = start  # "figure": a public figure; "import": a card the player brings
        self.builder = builder or Builder(config.load(), module)
        self.step = "name"
        self.proposal: Proposal | None = None
        self.question = 0
        self.shots: dict[int, Path | None] = {}  # downloaded photo candidates (None: would not open)
        self.chosen: Path | None = None
        self.portrait: Text | None = None  # the chosen photo, rendered once
        self.from_card = False  # changing the photo of a finished card
        self.work_dir = paths.card_work(self.builder.id)

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(id="setup-title")
            with Horizontal(id="setup-body"):
                with Vertical(id="card-left"):
                    yield Input(id="card-input")
                    yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._restart()

    def on_unmount(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)

    # -- widgets ----------------------------------------------------------------------------
    @property
    def style(self) -> str:
        return getattr(self.app, "art_style", "photo")

    def _show(self, title: str, hint: str, preview: Text | None = None) -> None:
        self.query_one("#setup-title", Static).update(Text(title, style="bold"))
        self.query_one("#setup-hint", Static).update(Text(hint, style="dim"))
        if preview is not None:
            self.query_one("#preview-body", Static).update(preview)

    def _type(self, title: str, placeholder: str, hint: str, preview: Text | None = None) -> None:
        self.query_one(OptionList).display = False
        inp = self.query_one(Input)
        inp.display, inp.disabled, inp.value, inp.placeholder = True, False, "", placeholder
        inp.focus()
        self._show(title, hint, preview)

    def _choose(self, title: str, options: list[Option], hint: str, preview: Text | None = None) -> None:
        self.query_one(Input).display = False
        ol = self.query_one(OptionList)
        ol.display, ol.disabled = True, False
        ol.clear_options()
        ol.add_options(options)
        ol.highlighted = 0
        ol.focus()
        self._show(title, hint, preview)

    def _wait(self, text: str) -> None:
        self.query_one(Input).disabled = True
        self.query_one(OptionList).disabled = True
        self.query_one("#setup-hint", Static).update(Text(f"{text}  Esc 取消", style="bold"))

    def _about(self) -> Text:
        """Who they are, their photo, the answers so far."""
        t = Text(f"{self.proposal.who if self.proposal else ''}\n", style="bold")
        if self.portrait:
            t.append("\n")
            t.append_text(self.portrait)
            t.append("\n")
        for q, a in self.builder.answers:
            t.append(f"\n{q}  ", style="dim")
            t.append(a)
        return t

    # -- steps ------------------------------------------------------------------------------
    def _restart(self) -> None:
        self._source() if self.start == "import" else self._ask_name()

    def _source(self) -> None:
        self.step = "source"
        self._type("导入已有的卡", "粘贴人物卡文字，或者把卡片文件拖进来", "回车读卡 · Esc 返回", IMPORTS)

    def _find_photo(self) -> None:
        self.step = "find"
        hint = Text("输入一个人名，上网找他的照片；或者把一张图片文件拖进来。", style="dim")
        self._type("配一张照片", "人名，或者图片文件", "回车 · Esc 返回", hint)

    def _ask_name(self) -> None:
        self.step = "name"
        intro = Text(
            "描述想扮演的人物，"
            "比如“在码头长大、胆子很大的女记者”。也可以用公众人物作为参考。回答几个背景问题后，系统会按模组年代生成可修改的人物卡。",
            style="dim",
        )
        self._type("创建调查员", "一个名字，或者一句描述", "回车开始 · Esc 返回", intro)

    def _photos(self) -> None:
        fig = self.builder.figure
        if not fig or not fig.photos:
            if fig and fig.source != "描述":  # a description never had photos to find
                self.app.notify("未找到照片，将继续创建人物卡。")
            self.chosen, self.portrait = None, None
            self._next_after_photo()
            return
        self.step = "photo"
        opts = [Option(f"照片 {i + 1} · {_host(p.page or p.url)}", id=f"photo-{i}") for i, p in enumerate(fig.photos[:SHOWN])]
        again = "不是这个人，换个名字" if self.start == "figure" else "重新导入"
        opts += [Option("不用照片", id="photo-none"), Option(again, id="again")]
        self._choose("是这个人吗？挑一张照片", opts, "↑↓ 看照片 · Enter 或空格选定 · Esc 返回")

    def _next_after_photo(self) -> None:
        if self.from_card and self.builder.card:
            self.from_card = False
            self._card()
        else:
            self._ask(0)

    def _ask(self, i: int) -> None:
        assert self.proposal
        questions = self.proposal.questions
        if i >= len(questions):
            self._extra()
            return
        self.step, self.question = "ask", i
        q = questions[i]
        opts = [Option(o, id=f"answer-{j}") for j, o in enumerate(q.options)] + [Option(ANY, id="answer-any")]
        self._choose(f"（{i + 1}/{len(questions)}）{q.ask}", opts, "Enter 或空格选定 · Esc 上一步", self._about())

    def _extra(self) -> None:
        self.step = "extra"
        self._type("还有什么要求？", "比如“让她会开枪”“年纪大一点”，没有就直接回车", "回车生成人物卡 · Esc 上一步", self._about())

    def _card(self) -> None:
        b = self.builder
        assert b.card
        self.step = "card"
        if b.kind == "sheet":  # brought as it was: no questions to answer again
            opts = [Option("就用这张卡", id="keep"), Option("提要求修改", id="tweak"), Option("配一张照片", id="find"), Option("重新导入", id="again")]
        else:
            photo = Option("换一张照片", id="photo") if b.figure and b.figure.photos else Option("配一张照片", id="find")
            opts = [Option("就用这张卡", id="keep"), Option("提要求修改", id="tweak"), photo, Option("重新回答问题", id="redo")]
        self._choose(f"{b.card.name} · {b.card.occupation}", opts, "Enter 或空格选定 · Esc 上一步", sheet(b.card, self.portrait, b.note, b.fixes))

    def _tweak(self) -> None:
        assert self.builder.card
        self.step = "tweak"
        preview = sheet(self.builder.card, self.portrait, self.builder.note, self.builder.fixes)
        example = "比如“名字叫沈芸，职业记者”“侦查改成 70”" if self.builder.kind == "sheet" else "比如“射击加到 60”“改成女性”“更年轻些”"
        self._type("想怎么改？", example, "回车 · Esc 返回", preview)

    # -- input --------------------------------------------------------------------------------
    @on(Input.Submitted, "#card-input")
    def submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self.step == "name" and text:
            self.look_up(text)
        elif self.step == "source" and text:
            self.read_card(text)
        elif self.step == "find" and text:
            self.photo_from(text)
        elif self.step == "extra":
            self.build(text)
        elif self.step == "tweak":
            self.build(text) if text else self._card()

    @on(OptionList.OptionHighlighted, "#choices")
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option.id or ""
        if self.step == "photo" and oid.startswith("photo-") and oid != "photo-none":
            self.show_photo(int(oid.removeprefix("photo-")))

    @on(OptionList.OptionSelected, "#choices")
    def selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id or ""
        b = self.builder
        if self.step == "photo":
            if oid == "again":
                self._restart()
            elif oid == "photo-none":
                self.chosen, self.portrait, b.photo = None, None, None
                self._next_after_photo()
            else:
                self.pick_photo(int(oid.removeprefix("photo-")))
        elif self.step == "ask":
            assert self.proposal
            q = self.proposal.questions[self.question]
            answer = ANY if oid == "answer-any" else q.options[int(oid.removeprefix("answer-"))]
            b.answers = [*b.answers[: self.question], (q.ask, answer)]
            self._ask(self.question + 1)
        elif self.step == "card":
            if oid == "keep":
                self.keep()
            elif oid == "tweak":
                self._tweak()
            elif oid == "photo":
                self.from_card = True
                self._photos()
            elif oid == "redo":
                b.answers = []
                self._ask(0)
            elif oid == "find":
                self._find_photo()
            elif oid == "again":
                self._restart()

    def action_back(self) -> None:
        if self.workers:
            self.workers.cancel_group(self, "card")
        if self.step in ("name", "source"):
            self.dismiss(None)
        elif self.step == "find":
            self._card()
        elif self.step == "photo":
            self._card() if self.from_card and self.builder.card else self._restart()
        elif self.step == "ask":
            self._ask(self.question - 1) if self.question else (self._photos() if self.builder.figure and self.builder.figure.photos else self._restart())
        elif self.step == "extra":
            self._ask(len(self.proposal.questions) - 1) if self.proposal and self.proposal.questions else self._restart()
        elif self.step == "card":
            self._extra()
        elif self.step == "tweak":
            self._card()

    # -- work ---------------------------------------------------------------------------------
    async def _fetch(self, indexes) -> None:
        """Download the photo candidates that are not here yet."""
        fig = self.builder.figure
        assert fig

        async def one(i: int) -> None:
            if i not in self.shots:
                dest = self.work_dir / f"photo-{i}.jpg"
                self.shots[i] = dest if await self.builder.download(i, dest) else None

        await asyncio.gather(*(one(i) for i in indexes))

    async def _picture(self, path: Path) -> Text | None:
        if not imagery.available():
            return None
        return Text.from_ansi(await asyncio.to_thread(imagery.render, str(path), *PHOTO, self.style))

    @work(exclusive=True, group="card")
    async def look_up(self, query: str) -> None:
        self._wait(f"正在查 {query} 是谁…")
        await self.builder.look_up(query)
        await self._questions()

    @work(exclusive=True, group="card")
    async def read_card(self, source: str) -> None:
        self._wait("正在读卡…")
        try:
            kind = await self.builder.read(source)
        except Exception as exc:  # an unreadable file, a model that cannot see images, a dropped call
            self.app.notify(f"读不了这张卡：{exc}", severity="error", timeout=8)
            self._source()
            return
        if kind == "sheet":
            self._card()
            return
        await self._questions()

    async def _questions(self) -> None:
        """The person (or character) is known: questions, and their photos meanwhile."""
        fig = self.builder.figure
        assert fig
        self._wait("正在准备照片和背景问题…")
        self.query_one("#preview-body", Static).update(Text(fig.brief(800), style="dim"))
        try:
            self.proposal, _ = await asyncio.gather(self.builder.propose(), self._fetch(range(min(4, len(fig.photos)))))
        except Exception as exc:  # the model is down or answered nonsense twice
            self.app.notify(str(exc) if isinstance(exc, CardError) else f"未能生成问题：{exc.__class__.__name__}，请重试。", severity="error")
            self._restart()
            return
        self._photos()

    @work(exclusive=True, group="card")
    async def photo_from(self, text: str) -> None:
        """A photo for a card that came without one: a file, or a name to look up."""
        if path := local_file(text):
            self.builder.figure = self.builder.figure or Figure(path.stem)
            self.builder.figure.photos = [Photo(str(path), title=path.name)]
            self.shots.clear()
            self.from_card = True
            self.pick_photo(0)
            return
        self._wait(f"正在找 {text} 的照片…")
        fig = await self.builder.find(text)
        self.shots, self.from_card = {}, True
        if not fig.photos:
            self.app.notify("没找到照片", severity="warning")
            self._card()
            return
        self._photos()

    @work(exclusive=True, group="preview")
    async def show_photo(self, i: int) -> None:
        fig = self.builder.figure
        assert fig
        await self._fetch([i])
        path = self.shots.get(i)
        t = Text(f"{self.proposal.who if self.proposal else fig.name}\n\n", style="bold")
        shown = await self._picture(path) if path else None
        if path is None:
            t.append("这张打不开，换一张吧。", style="dim")
        elif shown is None:
            t.append("装上 chafa 才能在终端里看照片，选了照片照样会存下来。", style="dim")
        else:
            t.append_text(shown)
        photo = fig.photos[i]
        t.append(f"\n\n{photo.title}\n{photo.page or photo.url}", style="dim")
        if self.step == "photo":
            self.query_one("#preview-body", Static).update(t)

    @work(exclusive=True, group="card")
    async def pick_photo(self, i: int) -> None:
        fig = self.builder.figure
        assert fig
        self._wait("正在处理照片…")
        await self._fetch([i])
        path = self.shots.get(i)
        if path is None:
            self.app.notify("无法打开照片，请换一张。", severity="warning")
            self._photos()
            return
        await asyncio.to_thread(imagery.preprocess, path, "portrait")  # face crop and cleanup, like module portraits
        self.chosen, self.builder.photo = path, fig.photos[i]
        self.portrait = await self._picture(path)
        self._next_after_photo()

    @work(exclusive=True, group="card")
    async def build(self, request: str = "") -> None:
        back = self.step
        self._wait("正在车卡…" if not self.builder.card else "正在按你的要求改…")
        try:
            await self.builder.build(request)
        except Exception as exc:
            self.app.notify(str(exc) if isinstance(exc, CardError) else f"未能创建人物卡：{exc.__class__.__name__}，请重试。", severity="error")
            self._extra() if back == "extra" else self._tweak()
            return
        self._card()

    def keep(self) -> None:
        b = self.builder
        assert b.card
        b.keep(self.chosen)
        roster.attach(self.m, [b.card])
        self.dismiss(b.card)
