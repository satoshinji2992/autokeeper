"""继续调查: the saves, newest first, the highlighted one's picture and party beside the list,
and what can be done with a save: go on with it, name it, copy it, put it in the trash.

A save is data/games/<slot>.json (the whole GameState) plus its line in index.json (what
the list shows). The slot is the file name and also the game's LangGraph thread id in
checkpoints.sqlite, so it never changes: a new name lives in the save (GameState.label),
a copy gets a slot of its own (a fresh thread; everything a turn needs is in the state,
pending_turn included), and a deleted save leaves its checkpoints where they are, so putting
the file back from data/games/trash/ brings the game back whole. Nothing here touches another
slot's file, line or thread.

A save of another version (engine/state.SaveRefused: 0.0.3's, with no save format) is listed as
such and never opened, so nothing writes over it; renaming one changes its index line only, and
deleting it moves the file to the trash as it is.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ... import imagery, paths
from ...engine.state import SAVE_FORMAT, GameState, SaveRefused, read_save, time_of_day
from ...module.schema import Module
from .. import notes
from ..modals.menu import AskText, Confirm


# -- the files ---------------------------------------------------------------------------------
@dataclass
class Save:
    slot: str
    meta: dict
    state: GameState | None  # None: missing, or this version cannot read it (读不了)
    refused: str = ""  # why not, for a save of another version (SaveRefused): 旧版本存档，… 无法读取

    @property
    def name(self) -> str:
        label = self.state.label if self.state else self.meta.get("label", "")
        return label or f"《{self.meta.get('title') or self.slot}》"

    @property
    def lan(self) -> bool:
        return self.slot.startswith("lan-")  # tui/screens/net.py's Hub names its tables so


def _index() -> dict:
    try:
        idx = json.loads(paths.save_index().read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no index yet, or one cut short: no saves to list
        return {}
    return idx if isinstance(idx, dict) else {}


def _write_index(idx: dict) -> None:
    paths.games().mkdir(parents=True, exist_ok=True)
    target = paths.save_index()
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(target)


def _write_json(target: Path, data: dict) -> None:
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(target)


def saves() -> list[Save]:
    """Every save in the index, the last played first; a file that will not read is kept."""
    out = []
    for slot, meta in _index().items():
        meta = meta if isinstance(meta, dict) else {}
        file = paths.save(slot)
        state: GameState | None = None
        refused = ""
        try:
            state = read_save(file.read_text(encoding="utf-8"))
        except SaveRefused as exc:
            refused = str(exc)
        except (OSError, ValueError):
            pass
        out.append(Save(slot, meta, state, refused))
    mtime = {s.slot: paths.save(s.slot).stat().st_mtime if paths.save(s.slot).exists() else 0.0 for s in out}
    return sorted(out, key=lambda s: (str(s.meta.get("updated", "")), mtime[s.slot]), reverse=True)


def to_trash(files: list[Path], name: str, note: dict | None = None) -> Path:
    """Move files into data/games/trash/<when>-<name>/ instead of deleting them, so an accident
    can be put back. `note` goes beside them as index.json (a save's line in the index)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    box = paths.trash() / f"{stamp}-{name}"
    n = 2
    while box.exists():
        box = paths.trash() / f"{stamp}-{name}-{n}"
        n += 1
    box.mkdir(parents=True)
    for f in files:
        if f.exists():
            shutil.move(str(f), str(box / f.name))
    if note is not None:
        (box / "index.json").write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    return box


def rename(slot: str, label: str) -> None:
    """A name of the player's own, in the save and its index line. Empty: the module title.
    A save of another version keeps its file as it is: the name goes in its index line only."""
    file = paths.save(slot)
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("不是存档")
    if data.get("save_format") == SAVE_FORMAT:
        data["label"] = label
        _write_json(file, data)
    idx = _index()
    if slot in idx:
        idx[slot]["label"] = label
        _write_index(idx)


def delete(slot: str) -> Path:
    """Into the trash: the file and its index line. Its checkpoints stay (see the module doc)."""
    idx = _index()
    meta = idx.pop(slot, {})
    box = to_trash([paths.save(slot)], slot, {slot: meta})
    _write_index(idx)
    return box


def copy(save: Save) -> str:
    """A second save of the same game, to try something else from here; its slot is new."""
    assert save.state is not None
    base = ("lan-" if save.lan else "") + f"{save.state.module}-{time.strftime('%m%d-%H%M%S')}"
    slot, n = base, 2
    while paths.save(slot).exists():
        slot, n = f"{base}-{n}", n + 1
    data = json.loads(paths.save(save.slot).read_text(encoding="utf-8"))
    data["slot"], data["label"] = slot, f"{save.name} 副本"
    paths.games().mkdir(parents=True, exist_ok=True)
    _write_json(paths.save(slot), data)
    idx = _index()
    idx[slot] = {**save.meta, "label": data["label"], "updated": time.strftime("%Y-%m-%d %H:%M")}
    _write_index(idx)
    return slot


# -- the words -----------------------------------------------------------------------------------
def when(updated: str, now: float | None = None) -> str:
    """今天 21:30, 昨天 21:30, 10月17日; another year with its year."""
    try:
        t = time.strptime(updated, "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    today = time.localtime(now)
    day = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 12, 0, 0, 0, 0, -1))
    days = round((time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1)) - day) / 86400)
    if days == 0:
        return f"今天 {t.tm_hour:02d}:{t.tm_min:02d}"
    if days == 1:
        return f"昨天 {t.tm_hour:02d}:{t.tm_min:02d}"
    if t.tm_year == today.tm_year:
        return f"{t.tm_mon}月{t.tm_mday}日"
    return f"{t.tm_year}年{t.tm_mon}月{t.tm_mday}日"


def game_time(minutes: int) -> str:
    day, rest = divmod(minutes, 1440)
    return f"第{day + 1}天 {rest // 60:02d}:{rest % 60:02d}"


def line(save: Save, playing: bool = False) -> Group:
    """One save in the list: its name and when it was played, then who, when and where."""
    top = Text(save.name, style="bold" if save.state else "dim", no_wrap=True, overflow="ellipsis")
    tags = Text()
    if save.state is None:
        tags.append(" 旧版本" if save.refused else " 无法读取", style="bold red")
    if playing:
        tags.append(" 当前游戏", style="bold")
    if save.lan:
        tags.append(" 联机", style="bold")
    if save.state is not None and save.state.ending:
        tags.append(" 已结束", style="bold")
    top.append_text(tags)
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(top, Text(when(str(save.meta.get("updated", ""))), style="dim"))
    st = save.state
    if st is None:
        below = Text(f"{save.refused}。" if save.refused else "无法读取此存档，可选择删除。", style="dim")
    else:
        party = "、".join(inv.name for inv in st.investigators)
        place = save.meta.get("location", "")
        below = Text(" · ".join(x for x in (party, game_time(st.minutes), place, f"第 {st.turn} 轮") if x), style="dim")
    below.no_wrap, below.overflow = True, "ellipsis"
    return Group(grid, below)


def details(save: Save, m: Module | None) -> Text:
    """The preview's words: where and when, the party, the start of the player's own notes (F2), the
    story so far. No clue's title: what they learnt the players keep in those notes."""
    from ..widgets.pages import tight

    st = save.state
    out = Text()
    if st is None:
        out.append(f"{save.name}\n\n", style="bold")
        out.append(f"{save.refused}。按 R 改名，按 D 删除。" if save.refused else "无法读取此存档。按 D 删除。", style="dim")
        return out
    out.append(save.name + "\n", style="bold")
    if st.label:
        out.append(f"《{save.meta.get('title', '')}》\n", style="dim")
    place = m.location(st.location).name if m and any(loc.id == st.location for loc in m.locations) else save.meta.get("location", "")
    out.append(" · ".join(x for x in (f"{game_time(st.minutes)} {time_of_day(st.minutes)}", place, f"第 {st.turn} 轮") if x) + "\n")
    if st.ending:
        end = next((e.title for e in m.endings if e.id == st.ending), "") if m else ""
        out.append(f"已结束{'：' + end if end else ''}\n", style="bold")
    out.append("\n")
    for inv in st.investigators:
        out.append(inv.name, style="bold")
        out.append(f"  {inv.occupation.split('（')[0]}\n", style="dim")
        out.append(f"  HP {inv.hp}/{inv.hp_max}  SAN {inv.san}", style="bold red" if inv.hp * 3 <= inv.hp_max else "")
        if inv.conditions:
            out.append("  " + "、".join(inv.conditions), style="red")
        out.append("\n")
    owners = dict.fromkeys([*st.human, *(inv.id for inv in st.investigators)])  # the player's own first
    if jotted := next((notes.head(st.notebook[i]) for i in owners if st.notebook.get(i, "").strip()), []):
        out.append("\n笔记\n", style="bold")
        out.append("".join(f"  {line}\n" for line in jotted[:4]))
    if st.progress.text.strip():
        out.append("\n进展\n", style="bold")
        out.append(tight(st.progress.text.strip()) + "\n")
    return out


# -- the screen ------------------------------------------------------------------------------------
class LoadScreen(Screen):
    """From the title: Enter starts the game. From the game's Esc menu (`playing`): the chosen
    game goes back to that screen to take its place (GameScreen.load_other), and the one being
    played cannot be deleted from here."""

    BINDINGS = [
        Binding("escape", "back", "返回", show=False),
        Binding("r", "rename", "改名", show=False),
        Binding("c", "copy", "复制", show=False),
        Binding("d,delete", "delete", "删除", show=False),
    ]
    PICTURE = 9  # rows of the scene picture in the preview

    def __init__(self, playing: Any = None):
        super().__init__()
        self.playing = playing  # the GameScreen under this one, if opened from its menu
        self.list: list[Save] = []
        self.modules: dict[str, Module | None] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(Text("读档" if self.playing else "继续调查", style="bold"), id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices", classes="saves")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-picture")
                    yield Static(id="preview-body")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._fill()

    def on_resize(self) -> None:
        self.call_after_refresh(self._resize_preview)

    def _resize_preview(self) -> None:
        if self.is_attached and self.list and (save := self._chosen()) is not None:
            self._show(save)

    @property
    def current(self) -> str | None:
        return self.playing.state.slot if self.playing is not None else None

    def _fill(self, select: str | None = None) -> None:
        self.list = saves()
        ol = self.query_one("#choices", OptionList)
        ol.clear_options()
        for i, s in enumerate(self.list):
            if i:
                ol.add_option(None)
            ol.add_option(Option(line(s, s.slot == self.current), id=s.slot))
        if not self.list:
            ol.add_option(Option("暂无存档", disabled=True))
            self.query_one("#preview-picture", Static).update("")
            self.query_one("#preview-body", Static).update(Text("新的调查开始后，每一轮都会自动存档。", style="dim"))
            self._hint(None)
        else:
            ids = [s.slot for s in self.list]
            at = ids.index(select) if select in ids else 0  # Enter goes on with the newest at once
            ol.highlighted = ol.get_option_index(ids[at])
        ol.focus()

    def _chosen(self) -> Save | None:
        ol = self.query_one("#choices", OptionList)
        if ol.highlighted is None:
            return None
        slot = ol.get_option_at_index(ol.highlighted).id
        return next((s for s in self.list if s.slot == slot), None)

    def _hint(self, save: Save | None) -> None:
        keys = []
        if save is not None and save.state is not None:
            keys.append(("open", "Enter 返回游戏" if save.slot == self.current else "Enter 继续"))
            keys += [("rename", "R 改名"), ("copy", "C 复制")]
        elif save is not None and save.refused:
            keys.append(("rename", "R 改名"))
        if save is not None and save.slot != self.current:
            keys.append(("delete", "D 删除"))
        keys.append(("back", "Esc 返回"))
        self.query_one("#setup-hint", Static).update(" · ".join(f"[@click=screen.{a}]{w}[/]" for a, w in keys))

    @staticmethod
    def _played(save: Save) -> str:
        """（今天 21:30）: which of several saves of one module is meant."""
        played = when(str(save.meta.get("updated", "")))
        return f"（{played}）" if played else ""

    def _module(self, save: Save) -> Module | None:
        """The save's module for its picture and place names (not compiled: only to look at)."""
        from ..library import module_dir

        key = str(save.meta.get("module", ""))
        if key not in self.modules:
            path = Path(str(save.meta.get("path", "")))
            try:
                self.modules[key] = Module.load(path if (path / "module.yaml").exists() else module_dir(key))
            except Exception:  # a module moved away or broken: the words still show
                self.modules[key] = None
        return self.modules[key]

    @on(OptionList.OptionHighlighted, "#choices")
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        save = self._chosen()
        self._hint(save)
        if save is not None:
            self._show(save)

    @work(exclusive=True, group="save-preview")
    async def _show(self, save: Save) -> None:
        from ..library import art_text

        m = self._module(save) if save.state is not None else None
        picture = self.query_one("#preview-picture", Static)
        self.query_one("#preview-body", Static).update(details(save, m))
        picture.update("")
        if m is None or save.state is None:
            return
        try:
            key = m.location(save.state.location).art
        except Exception:  # a place the module no longer has
            return
        width = max(1, picture.content_size.width or self.query_one("#preview").scrollable_content_region.width)
        style = getattr(self.app, "art_style", "photo")

        def draw() -> Text:
            if key in m.pictures and imagery.available():
                try:
                    return Text.from_ansi(imagery.render(m.pictures[key], width, self.PICTURE, style, fill=True),
                                          no_wrap=True, overflow="crop")
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass  # an unreadable picture: the hand-drawn art instead
            return art_text(m, key)

        art = await asyncio.to_thread(draw)
        if self._chosen() is save and art.plain.strip():
            art.append("\n")
            picture.update(art)

    # -- what can be done -------------------------------------------------------------------------
    @on(OptionList.OptionSelected, "#choices")
    def chosen(self) -> None:
        self.action_open()

    def action_open(self) -> None:
        from ..library import load_module, module_dir
        from .game import GameScreen

        save = self._chosen()
        if save is None:
            return
        if save.state is None:
            self.app.notify(f"{save.refused}。" if save.refused else "无法读取此存档，当前仅支持删除。", severity="warning")
            return
        if save.slot == self.current:
            self.dismiss(None)
            return
        path = Path(str(save.meta.get("path", "")))
        try:
            module = load_module(path if (path / "module.yaml").exists() else module_dir(str(save.meta.get("module", save.state.module))))
        except Exception:  # the module was removed or will not read
            self.app.notify("未找到存档对应的模组。", severity="error")
            return
        if self.playing is not None:
            self.dismiss((module, save.state))
        else:
            self.app.switch_screen(GameScreen(module, save.state, resume=True, spectate=not save.state.human))

    def action_back(self) -> None:
        self.dismiss(None) if self.playing is not None else self.app.pop_screen()

    def action_rename(self) -> None:
        save = self._chosen()
        if save is None or (save.state is None and not save.refused):
            return

        def done(name: str | None) -> None:
            if name is None:
                return
            try:
                rename(save.slot, name)
            except (OSError, ValueError):
                self.app.notify("重命名失败", severity="error")
                return
            if save.slot == self.current:
                self.playing.state.label = name  # or its next autosave would bring the old name back
            self._fill(save.slot)

        label = save.state.label if save.state else str(save.meta.get("label", ""))
        self.app.push_screen(AskText("重命名存档", label, f"留空使用《{save.meta.get('title', '')}》"), done)

    def action_copy(self) -> None:
        save = self._chosen()
        if save is None or save.state is None:
            return
        try:
            slot = copy(save)
        except (OSError, ValueError):
            self.app.notify("复制失败", severity="error")
            return
        self._fill(slot)
        self.app.notify("存档已复制")

    def action_delete(self) -> None:
        save = self._chosen()
        if save is None:
            return
        if save.slot == self.current:
            self.app.notify("无法删除当前游戏的存档。", severity="warning")
            return

        def done(answer: str | None) -> None:
            if answer != "yes":
                return
            try:
                delete(save.slot)
            except OSError:
                self.app.notify("删除失败", severity="error")
                return
            self._fill()
            self.app.notify("已删除")

        self.app.push_screen(Confirm(f"删除 {save.name}{self._played(save)}？", [("yes", "删除"), ("no", "取消")]), done)
