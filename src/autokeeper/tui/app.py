"""AutoKeeper terminal UI (Textual). Also runs in a browser via textual-serve."""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle, Vertical, VerticalScroll
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import artkit, config, imagery
from ..agents.cast import Cast
from ..engine import graph as turn_graph
from ..engine.game import Game
from ..engine.state import GameState, time_of_day
from ..module.compiler import compile_module, skill_name
from ..module.schema import Module
from ..rules.coc import Investigator, Rules
from . import art
from .screens import HELP, HandoutModal, NotebookModal, PushModal, SketchModal, TextModal
from .widgets import LEVEL_STYLE, CluePanel, DiceTray, SceneArt, SheetPanel, StoryLog

THEMES = [
    Theme("amber", primary="#ffb000", secondary="#d98e04", accent="#ff6b35", foreground="#f3d7a6", background="#0c0a06",
          surface="#15110a", panel="#211a0f", error="#e5383b", success="#9bd770", warning="#ffd166", dark=True),
    Theme("phosphor", primary="#33ff66", secondary="#1fbf4c", accent="#b3ff66", foreground="#b8ffc8", background="#040a05",
          surface="#08140a", panel="#0d1f10", error="#ff5555", success="#33ff66", warning="#e6ff66", dark=True),
    Theme("lavender", primary="#9a7fc4", secondary="#c3b1e1", accent="#ff8fab", foreground="#ece6f6", background="#0f0c14",
          surface="#17121f", panel="#221a2e", error="#ff6b6b", success="#a0e8af", warning="#ffd6a5", dark=True),
    Theme("blood", primary="#c1121f", secondary="#e85d04", accent="#ffba08", foreground="#f1e3e3", background="#0b0505",
          surface="#160909", panel="#220d0d", error="#ff4d4d", success="#90be6d", warning="#ffba08", dark=True),
]


# -- library and saves ---------------------------------------------------------------
def modules() -> list[Path]:
    found = []
    for base in (config.MODULES, config.LOCAL_MODULES):
        if base.is_dir():
            found += [p for p in sorted(base.iterdir()) if (p / "module.yaml").exists()]
    return found


def save_index() -> dict:
    f = config.SAVES / "index.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def write_save(state: GameState, module: Module) -> None:
    config.SAVES.mkdir(parents=True, exist_ok=True)
    (config.SAVES / f"{state.slot}.json").write_text(state.model_dump_json(indent=1), encoding="utf-8")
    idx = save_index()
    idx[state.slot] = {
        "module": module.id,
        "title": module.title,
        "party": "、".join(i.name for i in state.investigators),
        "clock": state.clock,
        "location": module.location(state.location).name,
        "ending": state.ending,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "path": str(_module_dir(module.id)),
    }
    (config.SAVES / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def _module_dir(module_id: str) -> Path:
    for p in modules():
        if p.name == module_id:
            return p
    raise FileNotFoundError(module_id)


def load_module(path: Path) -> Module:
    m = Module.load(path)
    if not (config.SKILLS / skill_name(m) / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
    return m


def art_text(m: Module, name: str | None, size: tuple[int, int] | None = None, style: str = "picture") -> Text:
    """A picture (chafa) when the module has one and a size is given, else the hand-drawn art."""
    if name and size and name in m.pictures and imagery.available():
        return Text.from_ansi(imagery.render(m.pictures[name], *size, style))
    if not name or name not in m.art:
        return Text("")
    return artkit.colorize(m.art[name], m.art_colors.get(name))


# -- title -------------------------------------------------------------------------------
class TitleScreen(Screen):
    def compose(self) -> ComposeResult:
        with Middle(id="title-box"):
            with Center():
                yield Static(id="title-art")
            with Center():
                yield Static(artkit.colorize(art.EYE, _eye_mask()), id="title-eye")
            yield Static(art.TAGLINE, id="tagline")
            with Center():
                yield OptionList(
                    Option("  新的调查", id="new"),
                    Option("  继续调查", id="continue"),
                    Option("  AI 自动跑团（观战）", id="watch"),
                    Option("  导入模组", id="import"),
                    Option("  离开", id="quit"),
                    id="menu",
                )
            yield Static("↑↓ 选择 · Enter 确认 · Ctrl+T 换配色 · Ctrl+Q 离开", id="title-hint")

    def on_mount(self) -> None:
        self.id = "title-screen"
        self._paint()
        self.set_interval(0.12, self._paint)
        self.query_one("#menu", OptionList).focus()

    def _paint(self) -> None:
        width = self.app.size.width
        rate = random.choice([0.0] * 14 + [0.02, 0.05, 0.12])
        self.query_one("#title-art", Static).update(art.title(width, rate))

    @on(OptionList.OptionSelected, "#menu")
    def choose(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "new":
            self.app.push_screen(SetupScreen(watch=False))
        elif choice == "watch":
            self.app.push_screen(SetupScreen(watch=True))
        elif choice == "continue":
            self.app.push_screen(LoadScreen())
        elif choice == "import":
            self.app.push_screen(TextModal("导入模组", Text(
                "把模组文件（PDF / Word / Markdown）放进 modules/_import/，然后在终端运行：\n\n"
                "    uv run autokeeper import modules/_import/你的模组.pdf --id 英文短名\n\n"
                "导入 Agent 会读文本和页面图片，写出 module.yaml、给每个地点和 NPC 画彩色 ASCII 图，\n"
                "跑校验直到没有错误，再编译成 Skill 包。导入的模组只留在本地 modules/local/。"
            )))
        elif choice == "quit":
            self.app.exit()


def _eye_mask() -> str:
    rows = art.EYE.split("\n")
    out = []
    for i, row in enumerate(rows):
        key = "P" if i < 2 else "p" if i < 6 else "R"
        out.append("".join("y" if ch in "()" and 2 <= i <= 4 else key for ch in row))
    return "\n".join(out)


# -- setup ---------------------------------------------------------------------------------
class SetupScreen(Screen):
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, watch: bool):
        super().__init__()
        self.watch = watch
        self.step = "module"
        self.module: Module | None = None
        self.lead: Investigator | None = None
        self.paths = modules()

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._show_modules()

    def action_back(self) -> None:
        if self.step == "module":
            self.app.pop_screen()
        elif self.step == "investigator":
            self._show_modules()
        else:
            self._show_investigators()

    def _set(self, title: str, options: list[Option], hint: str) -> None:
        self.query_one("#setup-title", Static).update(Text(title, style="bold"))
        ol = self.query_one("#choices", OptionList)
        ol.clear_options()
        ol.add_options(options)
        ol.highlighted = 0
        ol.focus()
        self.query_one("#setup-hint", Static).update(hint)

    def _show_modules(self) -> None:
        self.step = "module"
        opts = []
        for p in self.paths:
            try:
                m = Module.load(p)
                opts.append(Option(f"《{m.title}》 {m.players}", id=str(p)))
            except Exception as exc:  # broken module files should not crash the menu
                opts.append(Option(f"{p.name}（无法读取：{exc.__class__.__name__}）", id=str(p), disabled=True))
        self._set("选择模组" + ("（AI 观战）" if self.watch else ""), opts, "Enter 选择 · Esc 返回")

    def _show_investigators(self) -> None:
        assert self.module
        self.step = "investigator"
        if self.watch:
            opts = [Option(f"{n} 名 AI 调查员", id=f"party-{n}") for n in (1, 2, 3) if n <= max(1, len(self.module.pregens))]
            self._set(f"《{self.module.title}》· 观战人数", opts, "Enter 开始 · Esc 返回")
            return
        opts = [Option(f"{p.name}  {p.occupation}", id=p.id) for p in self.module.pregens]
        self._set(f"《{self.module.title}》· 选择你的调查员", opts, "Enter 选择 · Esc 返回")

    def _show_companions(self) -> None:
        assert self.module and self.lead
        self.step = "companions"
        others = [p for p in self.module.pregens if p.id != self.lead.id]
        opts = [Option("独自调查", id="solo")]
        opts += [Option(f"带 {n} 名 AI 同伴", id=f"with-{n}") for n in (1, 2) if n <= len(others)]
        self._set("同伴", opts, "AI 同伴是 investigator Agent，会在你之后行动 · Esc 返回")

    @on(OptionList.OptionHighlighted, "#choices")
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        body = self.query_one("#preview-body", Static)
        oid = event.option.id or ""
        if self.step == "module" and oid:
            m = Module.load(Path(oid))
            first = next((loc.art for loc in m.locations if loc.art), None)
            t = Text()
            t.append(f"《{m.title}》", style="bold")
            t.append(f"  {m.subtitle}\n", style="dim")
            t.append(f"{m.era} · {m.setting} · {m.players} · {m.duration}\n\n", style="dim")
            t.append_text(art_text(m, first))
            t.append("\n\n" + m.hook.strip() + "\n\n", style="italic")
            t.append(m.source, style="dim")
            body.update(t)
        elif self.step == "investigator" and self.module and not self.watch:
            inv = next((p for p in self.module.pregens if p.id == oid), None)
            if inv:
                body.update(_investigator_card(self.module, inv))

    @on(OptionList.OptionSelected, "#choices")
    def select(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id or ""
        if self.step == "module":
            self.module = load_module(Path(oid))
            if not self.module.pregens:
                self.app.notify("这个模组没有预设调查员", severity="error")
                return
            self._show_investigators()
        elif self.step == "investigator":
            assert self.module
            if self.watch:
                n = int(oid.split("-")[1])
                party = [p.model_copy(deep=True) for p in self.module.pregens[:n]]
                self._start(party, human=[])
            else:
                self.lead = next(p for p in self.module.pregens if p.id == oid)
                self._show_companions()
        elif self.step == "companions":
            assert self.module and self.lead
            n = 0 if oid == "solo" else int(oid.split("-")[1])
            others = [p for p in self.module.pregens if p.id != self.lead.id][:n]
            party = [self.lead.model_copy(deep=True)] + [p.model_copy(deep=True) for p in others]
            self._start(party, human=[self.lead.id])

    def _start(self, party: list[Investigator], human: list[str]) -> None:
        assert self.module
        slot = f"{self.module.id}-{time.strftime('%m%d-%H%M%S')}"
        state = Game.new(self.module, party, slot=slot, human=human)
        self.app.switch_screen(GameScreen(self.module, state, watch=self.watch))


def _investigator_card(m: Module, inv: Investigator) -> Text:
    t = Text()
    t.append_text(art_text(m, inv.portrait))
    t.append(f"\n\n{inv.name}", style="bold")
    t.append(f"  {inv.occupation} · {inv.age}岁\n", style="dim")
    for k, v in inv.backstory.items():
        t.append(f"{k}：", style="bold")
        t.append(f"{v}\n")
    t.append("\n" + "  ".join(f"{k}{v}" for k, v in inv.characteristics.items()) + f"  幸运{inv.luck}\n", style="dim")
    t.append("  ".join(f"{k}{v}" for k, v in sorted(inv.skills.items(), key=lambda kv: -kv[1])))
    return t


class LoadScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(Text("继续调查", style="bold"), id="setup-title")
            yield OptionList(id="choices")
            yield Static("Enter 读档 · Esc 返回", id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        ol = self.query_one("#choices", OptionList)
        idx = save_index()
        for slot, meta in sorted(idx.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            label = f"《{meta['title']}》 {meta['party']} · {meta['clock']} · {meta['location']}"
            if meta.get("ending"):
                label += " · 已结局"
            ol.add_option(Option(f"{label}   [{meta['updated']}]", id=slot))
        if not idx:
            ol.add_option(Option("还没有存档", disabled=True))
        ol.focus()

    @on(OptionList.OptionSelected, "#choices")
    def load(self, event: OptionList.OptionSelected) -> None:
        slot = event.option.id or ""
        meta = save_index()[slot]
        module = load_module(Path(meta["path"]))
        state = GameState.model_validate_json((config.SAVES / f"{slot}.json").read_text(encoding="utf-8"))
        self.app.switch_screen(GameScreen(module, state, resume=True, watch=not state.human))


# -- the game -------------------------------------------------------------------------------
class GameScreen(Screen):
    BINDINGS = [
        Binding("f1", "help", "帮助"),
        Binding("f2", "notes", "线索本"),
        Binding("f4", "sketch", "手绘"),
        Binding("f5", "map", "地图"),
        Binding("ctrl+r", "recap", "上回书说到"),
        Binding("ctrl+s", "save", "存档"),
        Binding("ctrl+a", "art_style", "画法"),
        Binding("escape", "focus_input", "输入", show=False),
    ]

    def __init__(self, module: Module, state: GameState, *, resume: bool = False, watch: bool = False):
        super().__init__()
        self.m, self.state, self.resume, self.watch = module, state, resume, watch
        self.cast: Cast | None = None
        self.game: Game | None = None
        self.graph = None
        self.thread = {"configurable": {"thread_id": state.slot}}
        self.met: set[str] = set()
        self.spinner = 0
        self.status_text = ""
        self.engine_label = ""

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield SceneArt(id="scene")
                yield StoryLog(id="story")
                yield Static(id="status")
                yield Input(placeholder="说出你的行动，或 .ra 侦查 · /draw 手绘 · /notes 线索本 · F1 帮助", id="command")
            with Vertical(id="right"):
                yield SheetPanel(id="sheet")
                yield DiceTray(id="dice")
                yield CluePanel(id="clues")
        yield Footer()

    async def on_mount(self) -> None:
        cfg = config.load()
        keeper = cfg.role("keeper")
        self.engine_label = f"{cfg.preset} · 守秘人 {keeper.model.split(':', 1)[-1]}"
        self.cast = Cast(cfg, self.m)
        self.game = Game(self.m, self.state, self.cast, Rules(), emit=self.handle)
        config.SAVES.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(config.SAVES / "checkpoints.sqlite")
        saver = AsyncSqliteSaver(self._db)
        await saver.setup()
        self.graph = turn_graph.build(self.game, saver)
        self._scene(self.state.location)
        self._panels()
        self.set_interval(0.1, self._spin)
        if self.watch:
            self.query_one(Input).disabled = True
        self.boot()

    async def on_unmount(self) -> None:
        if self.cast:
            await self.cast.close()
        if getattr(self, "_db", None):
            await self._db.close()

    # -- helpers ---------------------------------------------------------------------------
    @property
    def story(self) -> StoryLog:
        return self.query_one(StoryLog)

    def _status(self, text: str) -> None:
        self.status_text = text

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self.spinner = (self.spinner + 1) % len(frames)
        label = self.query_one("#status", Static)
        label.update(Text(f"{frames[self.spinner]} {self.status_text}" if self.status_text else ""))

    def _busy(self, busy: bool) -> None:
        inp = self.query_one(Input)
        inp.disabled = busy or self.watch
        if not busy:
            self.status_text = ""
            if not self.watch:
                inp.focus()

    def _scene(self, loc_id: str) -> None:
        loc = self.m.location(loc_id)
        name = loc.art or ""
        scene = self.query_one(SceneArt)
        scene.style_name = self.app.art_style  # type: ignore[attr-defined]
        scene.when = time_of_day(self.state.minutes)  # lit before the first frame, no bright flash
        scene.show(loc.name, self.m.art.get(name, ""), self.m.art_colors.get(name), self.m.pictures.get(name))
        self._topbar()

    def on_resize(self, event) -> None:
        right = self.query_one("#right")
        width = event.size.width
        right.display = width >= 96
        right.styles.width = 40 if width >= 130 else 34

    def action_art_style(self) -> None:
        self.app.art_style = "ascii" if self.app.art_style == "picture" else "picture"  # type: ignore[attr-defined]
        self.app.notify(f"画面：{'纯 ASCII' if self.app.art_style == 'ascii' else '块字符'}")  # type: ignore[attr-defined]
        self._scene(self.state.location)
        self._panels()

    def _topbar(self) -> None:
        st = self.state
        moon = "☾" if time_of_day(st.minutes) in ("夜里", "深夜", "傍晚") else "☀"
        t = Text()
        t.append(" AUTOKEEPER ", style="bold reverse")
        t.append(f"  《{self.m.title}》  ", style="bold")
        t.append(f"{moon} {st.clock} {time_of_day(st.minutes)}  ", style="bold")
        t.append(f"◆ {self.m.location(st.location).name}  ")
        tokens = sum(int(u.get("output", 0)) for u in st.usage.values())
        t.append(f"  第 {st.turn} 轮 · 输出 {tokens} tokens · {self.engine_label}", style="dim")
        self.query_one("#topbar", Static).update(t)
        self.query_one(SceneArt).set_light(time_of_day(st.minutes))

    def _panels(self) -> None:
        st = self.state
        inv = st.investigator
        self.query_one(SheetPanel).show(inv, art_text(self.m, inv.portrait, (30, 12), self.app.art_style))  # type: ignore[attr-defined]
        titles = [self.m.clue(c).title for c in st.clues]
        self.query_one(CluePanel).show(titles, len(self.m.clues))
        lost = 1 - inv.san / max(1, inv.characteristics.get("意志", inv.san) or 1)
        self.query_one(SceneArt).madness = max(0.0, min(0.6, lost * 2))
        self._topbar()

    # -- event handling ----------------------------------------------------------------------
    async def handle(self, ev: dict) -> None:
        t = ev.get("type")
        story = self.story
        if t == "status":
            self._status(ev["text"])
        elif t == "move":
            self._scene(ev["location"])
        elif t == "time":
            self._topbar()
        elif t == "opening":
            story.add("system", Text(ev["text"], style="italic"))
        elif t == "keeper_start":
            self._status("守秘人正在讲述…")
            story.begin_keeper()
        elif t == "keeper_delta":
            story.feed(ev["text"])
        elif t == "keeper_end":
            story.end_keeper(ev["text"])
            self._status("")
        elif t == "action":
            story.add("system", Text(f"◇ 裁定员只看到：{ev['summary']}", style="dim"))
        elif t == "ruling":
            if ev.get("needs_roll") and ev.get("skill"):
                story.add("system", Text(f"◇ 需要检定：{ev['skill']}（{ev.get('difficulty', '常规')}）", style="dim"))
        elif t == "dice":
            self.query_one(DiceTray).roll(ev)
            line = Text()
            line.append(f"◈ {ev.get('who', '')} ", style="bold")
            line.append(ev["text"].rsplit(" ", 2)[0] + " ")
            line.append(f" {ev['level']} ", style=LEVEL_STYLE.get(ev["level"], "bold"))
            story.add("dice", line)
            self._panels()
        elif t == "command":
            story.add("dice", Text(f"◈ {ev.get('who', '')}：{ev['text']}"))
        elif t == "clue":
            body = Text()
            body.append(f"〔{ev['title']}〕\n", style="bold")
            body.append(ev["text"])
            story.add("clue", body)
            self._panels()
            self.app.notify(f"获得线索：{ev['title']}", timeout=3)
        elif t == "handout":
            story.add("handout", Text(f"▤ 手稿：{ev['title']}（F2 可重看）", style="bold"))
            self.app.push_screen(HandoutModal(ev["title"], ev["text"]))
        elif t == "sanity":
            body = Text()
            body.append(f"※ {ev['who']} 理智 {ev['before']} → {ev['after']}（-{ev['loss']}）", style="bold")
            for k, label in (("temporary", "临时疯狂"), ("indefinite", "不定性疯狂"), ("permanent", "永久疯狂")):
                if ev.get(k):
                    body.append(f"  {label}", style="bold red reverse")
            story.add("sanity", body)
            if ev.get("loss"):
                self.query_one(SceneArt).glitch(10 + min(30, ev["loss"] * 3))
            self._panels()
        elif t == "event":
            body = Text()
            body.append(f"━━━━ {ev['title']} ━━━━\n", style="bold")
            if ev.get("text"):
                body.append(ev["text"], style="italic")
            story.add("event", body)
        elif t == "npc":
            self._npc_line(ev, "npc")
        elif t == "performance":
            self._npc_line(ev, "performance")
            self.query_one(SceneArt).glitch(24)
        elif t == "luck":
            story.add("system", Text(f"◎ {ev['who']} 花掉 {ev['spent']} 点幸运（剩 {ev['left']}）"))
            self._panels()
        elif t == "ending":
            body = Text()
            body.append(f"【结局 · {ev['title']}】\n\n", style="bold")
            body.append(ev["text"] + "\n")
            if ev.get("reward"):
                body.append(f"\n{ev['reward']}", style="dim")
            for note in ev.get("notes", []):
                body.append(f"\n{note}", style="bold")
            story.add("ending", body)
        elif t == "sheet":
            self._panels()

    def _npc_line(self, ev: dict, kind: str) -> None:
        npc = self.m.npc(ev["npc"])
        body = Text()
        if npc.id not in self.met and (npc.portrait in self.m.art or npc.portrait in self.m.pictures):
            body.append_text(art_text(self.m, npc.portrait, (30, 12), self.app.art_style))  # type: ignore[attr-defined]
            body.append("\n")
            self.met.add(npc.id)
        body.append(f"{ev['name']}", style="bold")
        body.append(f"  {npc.role}\n" if kind == "npc" else "  登台\n", style="dim")
        body.append(ev["text"])
        self.story.add(kind, body)

    # -- turns ----------------------------------------------------------------------------------
    @work(exclusive=True, group="turn")
    async def boot(self) -> None:
        assert self.game and self.cast
        self._busy(True)
        warm = asyncio.create_task(self._warm())
        if self.resume:
            self._status("说书人正在回顾…")
            self.story.add("event", Text("━━━━ 上回书说到 ━━━━", style="bold"))
            self.story.begin_keeper()
            text = await self.cast.recap(self.state, on_delta=self._feed)
            self.story.end_keeper(text)
            self._scene(self.state.location)
        else:
            self._status("守秘人正在准备开场…")
            await self.game.start()
            write_save(self.state, self.m)
        await warm
        self._busy(False)
        if self.watch:
            self.autoplay()

    async def _feed(self, text: str) -> None:
        self.story.feed(text)

    async def _warm(self) -> None:
        """Start the agents' processes while the opening plays, so the first turn is quick."""
        assert self.cast
        try:
            await self.cast.warm(self.state)
        except Exception:  # warming is an optimisation; failures surface on first real use
            pass

    def action_focus_input(self) -> None:
        if not self.watch:
            self.query_one(Input).focus()

    @on(Input.Submitted, "#command")
    def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._slash(text)
            return
        if self.state.ending:
            self.app.notify("这局已经结束了。Ctrl+R 听说书人复盘，Esc 回到标题。")
            return
        self.play(text)

    def _slash(self, text: str) -> None:
        cmd = text.split()[0].lower()
        actions = {"/draw": self.action_sketch, "/notes": self.action_notes, "/map": self.action_map,
                   "/help": self.action_help, "/recap": self.action_recap, "/save": self.action_save}
        if cmd in actions:
            actions[cmd]()
        elif cmd == "/theme":
            names = [th.name for th in THEMES]
            self.app.theme = names[(names.index(self.app.theme) + 1) % len(names)] if self.app.theme in names else names[0]
        elif cmd == "/quit":
            self.app.pop_screen() if len(self.app.screen_stack) > 1 else self.app.exit()
        else:
            self.app.notify(f"不认识的命令：{cmd}", severity="warning")

    @work(exclusive=True, group="turn")
    async def play(self, text: str) -> None:
        assert self.graph and self.game
        self._busy(True)
        inv = self.state.investigator
        self.story.add("player", Text(f"▶ {inv.name}：{text}"))
        await self._run_turn({"input": text})
        await self._companions()
        self._busy(False)

    async def _run_turn(self, inp, auto: bool = False) -> None:
        assert self.graph
        while True:
            offer = None
            async for mode, chunk in self.graph.astream(inp, self.thread, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    await self.handle(chunk)
                elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                    offer = chunk["__interrupt__"][0].value
            if offer is None:
                break
            if auto:
                choice = "push" if offer.get("can_push") else "luck" if offer.get("luck") is not None else "drop"
                self.story.add("system", Text(f"◇ AI 选择：{ {'push': '孤注一掷', 'luck': '花幸运', 'drop': '放弃'}[choice] }", style="dim"))
            else:
                choice = await self.app.push_screen_wait(PushModal(offer))
            inp = Command(resume=choice)
        assert self.game
        self.game.emit = self.handle
        write_save(self.state, self.m)
        self._topbar()
        if self.state.ending:
            self._ending_summary()

    async def _companions(self) -> None:
        """AI companions act after the human, every other turn."""
        st = self.state
        ai = [i for i, inv in enumerate(st.investigators) if inv.id not in st.human and inv.can_act]
        if not ai or st.ending or st.turn % 2:
            return
        human_idx = st.active
        for idx in ai:
            if st.ending:
                break
            st.active = idx
            await self._ai_act(idx)
        st.active = human_idx
        self._panels()

    async def _ai_act(self, idx: int) -> None:
        assert self.cast
        st = self.state
        inv = st.investigators[idx]
        self._status(f"{inv.name}在想下一步…")
        situation = self._situation()
        action = await self.cast.investigator(st, inv.id, situation)
        self.story.add("player", Text(f"▶ {inv.name}（AI）：{action}"))
        await self._run_turn({"input": action}, auto=True)

    def _situation(self) -> str:
        from ..engine.autoplay import situation

        return situation(self.m, self.state)

    @work(exclusive=True, group="turn")
    async def autoplay(self) -> None:
        """Watch mode: every round the AI investigators propose in parallel (LangGraph Send)."""
        from ..engine.autoplay import party_graph, situation

        assert self.cast
        st = self.state
        party = party_graph(self.cast, st)
        self._busy(True)
        while not st.ending and st.turn < 60:
            alive = [inv.id for inv in st.investigators if inv.can_act]
            if not alive:
                break
            self._status("AI 调查员在商量…")
            result = await party.ainvoke({"situation": situation(self.m, st), "ids": alive, "proposals": []})
            for inv_id, text in result["proposals"]:
                if st.ending:
                    break
                st.active = next(i for i, inv in enumerate(st.investigators) if inv.id == inv_id)
                self._panels()
                self.story.add("player", Text(f"▶ {st.investigator.name}（AI）：{text}"))
                await self._run_turn({"input": text}, auto=True)
        self._busy(False)

    def _ending_summary(self) -> None:
        st = self.state
        found = len(st.clues)
        total = len(self.m.clues)
        t = Text()
        t.append(f"线索 {found}/{total} · 用时至 {st.clock} · 共 {st.turn} 轮\n", style="bold")
        for inv in st.investigators:
            t.append(f"{inv.name}：HP {inv.hp}/{inv.hp_max}  理智 {inv.san}  {'、'.join(inv.conditions)}\n")
        calls = sum(int(u["calls"]) for u in st.usage.values())
        tokens = sum(int(u["input"] + u["output"]) for u in st.usage.values())
        t.append(f"\n模型调用 {calls} 次 · {tokens} tokens\n", style="dim")
        t.append("Ctrl+R 让说书人复盘这一局 · Esc 回到标题", style="dim")
        self.story.add("ending", t)

    # -- actions -----------------------------------------------------------------------------------
    def action_help(self) -> None:
        self.app.push_screen(TextModal("帮助", HELP))

    @work(group="ui")
    async def action_notes(self) -> None:
        st = self.state
        clues = [(self.m.clue(c).title, self.m.clue(c).text.strip()) for c in st.clues]
        handouts = [(h, self.m.handout(h).title) for h in st.handouts]
        chosen = await self.app.push_screen_wait(NotebookModal(clues, handouts))
        if chosen:
            h = self.m.handout(chosen)
            self.app.push_screen(HandoutModal(h.title, h.text))

    def action_map(self) -> None:
        self.app.push_screen(TextModal("地图", _map(self.m, self.state)))

    @work(exclusive=True, group="turn")
    async def action_sketch(self) -> None:
        assert self.game
        st = self.state
        prompt = next((p for p in self.m.sketches if not p.after or p.after in st.clues), None)
        ask = prompt.ask if prompt else "画下你想给守秘人看的东西"
        png = await self.app.push_screen_wait(SketchModal(ask))
        if not png:
            return
        self._busy(True)
        self.game.emit = self.handle
        self.story.add("player", Text(f"▶ {st.investigator.name} 交出了一幅草图"))
        await self.game.sketch(png, prompt.id if prompt else None)
        write_save(self.state, self.m)
        self._busy(False)

    @work(exclusive=True, group="turn")
    async def action_recap(self) -> None:
        assert self.cast
        self._busy(True)
        self._status("说书人清了清嗓子…")
        self.story.add("event", Text("━━━━ 上回书说到 ━━━━", style="bold"))
        self.story.begin_keeper()
        text = await self.cast.recap(self.state, on_delta=self._feed)
        self.story.end_keeper(text)
        self._busy(False)

    def action_save(self) -> None:
        write_save(self.state, self.m)
        self.app.notify(f"已存档：{self.state.slot}")


def _map(m: Module, st: GameState) -> Text:
    """Breadth-first tree of locations from the module's start, with what you have seen."""
    out = Text()
    seen = {m.start}
    children: dict[str, list[str]] = {}
    queue = [m.start]
    while queue:
        cur = queue.pop(0)
        for nxt in m.location(cur).exits:
            if nxt not in seen and any(x.id == nxt for x in m.locations):
                seen.add(nxt)
                children.setdefault(cur, []).append(nxt)
                queue.append(nxt)

    def walk(node: str, prefix: str, last: bool, root: bool) -> None:
        loc = m.location(node)
        branch = "" if root else ("└─ " if last else "├─ ")
        style = "bold reverse" if node == st.location else "bold" if node in st.visited else "dim"
        out.append(prefix + branch)
        out.append(loc.name, style=style)
        if node == st.location:
            out.append("  ◀ 你在这里", style="bold")
        elif node in st.visited:
            out.append("  ✓", style="dim")
        out.append("\n")
        kids = children.get(node, [])
        for i, kid in enumerate(kids):
            walk(kid, prefix + ("" if root else ("   " if last else "│  ")), i == len(kids) - 1, False)

    walk(m.start, "", True, True)
    return out


class AutoKeeperApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "AutoKeeper"
    BINDINGS = [Binding("ctrl+q", "quit", "离开"), Binding("ctrl+t", "next_theme", "换配色")]

    def __init__(self, theme: str | None = None):
        super().__init__()
        self._theme_name = theme
        self.art_style = config.load().art_style

    def on_mount(self) -> None:
        for th in THEMES:
            self.register_theme(th)
        self.theme = self._theme_name or config.load().theme
        self.push_screen(TitleScreen())

    def action_next_theme(self) -> None:
        names = [th.name for th in THEMES]
        self.theme = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        self.notify(f"配色：{self.theme}")
