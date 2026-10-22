"""角色卡: every investigator on this machine, from the title screen, in three groups: 我的角色
(the cards kept in data/cards/, from 车卡, an import or a copy), 存档里的 (the
player's investigator in each save, as they stand now, with what they went through in that
game) and 模组预设 (each module's pregens). The highlighted one's full sheet is beside the
list, Enter opens it on its own.

新建角色 opens the flows of 新的调查 (tui/screens/card.py: AI 协助车卡 from a description or a public
figure, or 导入已有的卡; a .st line there is the way to bring numbers of one's own). Only
我的角色 can be changed: edited (everything but the numbers, tui/modals/cardedit.py), exported,
deleted (into data/games/trash/). A card from a save or a module is copied into 我的角色 first;
the save and the module's files are never written to."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from ... import artkit, credits, imagery, paths, roster
from ...engine.state import GameState, check_save
from ...module.schema import Module
from ...rules.coc import Investigator
from ..library import FACE
from ..modals.menu import AskText, Confirm, GameMenu
from ..modals.sheet import SheetModal, sheet_view
from .saves import to_trash, when

ROOM = 31  # the list's width inside its border, padding and scrollbar: longer lines are cut, not wrapped


def photo(inv: Investigator, m: Module | None) -> Path | None:
    """The card's photo: a kept card's beside it, else the module's picture."""
    path = roster.folder() / f"{inv.portrait}.jpg"
    if inv.portrait and path.is_file():
        return path
    if m is not None and inv.portrait in m.pictures and Path(m.pictures[inv.portrait]).is_file():
        return Path(m.pictures[inv.portrait])
    return None


def portrait(inv: Investigator, m: Module | None, style: str) -> Text | None:
    """The card's face for the sheet beside a list: its photo drawn, else the module's art. Slow
    (chafa): call it in a thread."""
    path = photo(inv, m)
    if path is not None and imagery.available():
        try:
            return Text.from_ansi(imagery.render(str(path), *FACE, style, face=True))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if m is not None and inv.portrait in m.art:
        return artkit.colorize(m.art[inv.portrait], m.art_colors.get(inv.portrait))
    return None


def kept_file(inv: Investigator) -> Path:
    return roster.folder() / f"{inv.id}.json"


def keep_copy(inv: Investigator, m: Module | None, source: str) -> Investigator:
    """A save's or a module's investigator as a card of the player's own, with an id and a
    photo of its own, so deleting either never takes the other's files."""
    new_id = f"card-{uuid.uuid4().hex[:6]}"
    card = inv.model_copy(deep=True, update={"id": new_id, "portrait": new_id})
    roster.keep(card, {"copied": source})
    if (src := photo(inv, m)) is not None:
        try:
            from PIL import Image

            with Image.open(src) as im:
                im.convert("RGB").save(roster.folder() / f"{new_id}.jpg", "JPEG", quality=92)
        except (OSError, ValueError):  # no photo then; the card itself is kept
            pass
    return card


NUMBERS = ("characteristics", "luck", "hp", "hp_max", "mp", "mp_max", "san", "san_start_of_day", "san_lost_today", "skills", "marks")


def update_kept(inv: Investigator, changes: dict, photo_file: Path | None = None) -> Investigator:
    """New words on the card, and a new photo if one is given; the numbers are never among
    them. It keeps its place in the list (the file's time)."""
    f = kept_file(inv)
    stat = f.stat()
    record = json.loads(f.read_text(encoding="utf-8"))
    card = Investigator.model_validate(record["investigator"])
    fields = {k: v for k, v in changes.items() if k in Investigator.model_fields and k not in NUMBERS and k != "id"}
    if photo_file is not None:
        fields["portrait"] = card.portrait or card.id
    card = card.model_copy(update=fields)
    if photo_file is not None:
        from PIL import Image

        target = roster.folder() / f"{card.portrait}.jpg"
        with Image.open(photo_file) as im:
            im.convert("RGB").save(target, "JPEG", quality=92)
        credits.put(target, None)  # it was about the old photo
        try:
            imagery.preprocess(target, "portrait")  # face crop, as 车卡 does
        except (OSError, ValueError):
            pass
    record["investigator"] = card.model_dump(mode="json")
    f.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.utime(f, (stat.st_atime, stat.st_mtime))
    return card


def delete_kept(inv: Investigator) -> Path:
    """Into the trash folder: the card, and its photo unless another kept card shows it too."""
    files = [kept_file(inv)]
    shared = any(c.portrait == inv.portrait and c.id != inv.id for c in roster.saved())
    if inv.portrait and not shared:
        files += [f for f in roster.folder().glob(f"{glob_escape(inv.portrait)}.*") if f not in files and f.is_file()]
    return to_trash(files, inv.id)


def glob_escape(name: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", name)


def export(inv: Investigator, m: Module | None, folder: Path) -> Path:
    """<name>.json (the card as data/cards/ keeps it) in `folder`, its photo beside it."""
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", inv.name).strip("._") or "调查员"
    target, n = folder / f"{stem}.json", 2
    while target.exists():
        target, n = folder / f"{stem}-{n}.json", n + 1
    target.write_text(json.dumps({"investigator": inv.model_dump(mode="json")}, ensure_ascii=False, indent=1), encoding="utf-8")
    if (src := photo(inv, m)) is not None:
        shutil.copy2(src, target.with_suffix(src.suffix.lower()))
    return target


class CardsScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "返回", show=False),
        Binding("e,r", "edit", "编辑", show=False),
        Binding("d,delete", "delete", "删除", show=False),
        Binding("c", "copy", "复制成新角色", show=False),
        Binding("x", "export", "导出", show=False),
        Binding("a", "create", "新建角色", show=False),
        Binding("i", "import_card", "导入", show=False),
        Binding("n", "new_game", "开新游戏", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        # option id -> (the investigator, the group it is shown with, the module its pictures come from)
        self.cards: dict[str, tuple[Investigator, list[Investigator], Module | None]] = {}
        self.saves: dict[str, dict | GameState | None] = {}  # option tag -> its save, read when first shown (本局经历)
        self.paths: dict[str, Path] = {}  # option tag of a module's pregens -> the module's folder
        self.titles: dict[str, str] = {}  # option tag -> where the card is from, in words

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(Text("角色卡", style="bold"), id="setup-title")
            with Horizontal(id="setup-body"):
                yield OptionList(id="choices")
                with VerticalScroll(id="preview"):
                    yield Static(id="preview-body")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._fill()

    def _fill(self, select: str | None = None) -> None:
        from ..library import load_module, save_index

        self.cards.clear()
        self.saves.clear()
        loaded: dict[str, Module] = {}

        def module(path: Path) -> Module | None:
            if str(path) not in loaded:
                try:
                    loaded[str(path)] = load_module(path)
                except Exception:  # a broken module still lets the rest of the list show
                    return None
            return loaded[str(path)]

        # (heading or None, sub-heading or None, cards, module, tag)
        groups: list[tuple[str | None, str | None, list[Investigator], Module | None, str]] = []
        groups.append(("我的角色", None, roster.saved(), None, "r"))
        seen: set[tuple[str, str]] = set()  # (module, investigator): only the newest save of each
        first = True
        for slot, meta in sorted(save_index().items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            try:
                state = json.loads(paths.save(slot).read_text(encoding="utf-8"))
                party = [Investigator.model_validate(p) for p in state["investigators"] if p["id"] in state.get("human", [])]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            party = [p for p in party if (meta.get("module", ""), p.id) not in seen]
            seen.update((meta.get("module", ""), p.id) for p in party)
            if party:  # games only watched have no one of the player's
                title = state.get("label") or f"《{meta.get('title', '?')}》"
                self.titles[f"s{slot}"] = f"{title} 的存档"
                groups.append(("存档里的" if first else None, f"{title} {when(str(meta.get('updated', '')))}", party,
                               module(Path(meta.get("path", ""))), f"s{slot}"))  # fmt: skip
                self.saves[f"s{slot}"] = state
                first = False
        first = True
        for path in paths.module_folders():
            m = module(path)
            if m and m.pregens:
                self.paths[f"m{path.name}"], self.titles[f"m{path.name}"] = path, f"《{m.title}》"
                groups.append(("模组预设" if first else None, f"《{m.title}》", list(m.pregens), m, f"m{path.name}"))
                first = False

        ol = self.query_one("#choices", OptionList)
        ol.clear_options()
        lit = f"bold {self.app.current_theme.primary}"
        for heading, sub, party, m, tag in groups:
            if heading:
                ol.add_option(Option(Text(heading, style=lit), disabled=True))
            if sub:
                head = Text(f"  {sub}", style="dim")
                head.truncate(ROOM, overflow="ellipsis")
                ol.add_option(Option(head, disabled=True))
            for inv in party:
                oid = f"{tag}:{inv.id}"
                self.cards[oid] = (inv, party, m)
                label = Text(f"{'    ' if sub else '  '}{inv.name}")
                label.append(f"  {inv.occupation.split('（')[0]}", style="dim")
                label.truncate(ROOM, overflow="ellipsis")
                ol.add_option(Option(label, id=oid))
            if tag == "r":
                ol.add_option(Option(Text("  ＋ 新建角色"), id="create"))
        if select in self.cards or select == "create":
            ol.highlighted = ol.get_option_index(select)
        else:
            ol.highlighted = ol.get_option_index(next(iter(self.cards), "create"))  # the first card, else 新建角色
            self._hint(None)
        ol.focus()

    # -- looking -------------------------------------------------------------------------------
    def _highlighted(self) -> str | None:
        ol = self.query_one("#choices", OptionList)
        return ol.get_option_at_index(ol.highlighted).id if ol.highlighted is not None else None

    def _chosen(self) -> str | None:
        oid = self._highlighted()
        return oid if oid in self.cards else None

    def _hint(self, oid: str | None) -> None:
        keys = [("open", "Enter 打开")] if oid else []
        if oid and oid.startswith("r"):
            keys += [("edit", "E 编辑"), ("export", "X 导出"), ("delete", "D 删除"), ("new_game", "N 开新游戏")]
        elif oid:
            keys += [("copy", "C 复制成新角色"), ("export", "X 导出")]
            keys += [("new_game", "N 开新游戏")] if oid.startswith("m") else []
        keys += [("create", "A 新建角色"), ("app.pop_screen", "Esc 返回")]
        self.query_one("#setup-hint", Static).update(
            " · ".join(f"[@click={a if '.' in a else 'screen.' + a}]{w}[/]" for a, w in keys))  # fmt: skip

    def _portrait(self, inv: Investigator, m: Module | None, style: str) -> Text | None:
        return portrait(inv, m, style)

    @on(OptionList.OptionHighlighted, "#choices")
    def preview(self, event: OptionList.OptionHighlighted) -> None:
        oid = event.option.id if event.option.id in self.cards else None
        self._hint(oid)
        if oid:
            self._show(oid)
        elif event.option.id == "create":
            self.workers.cancel_group(self, "card")
            self.query_one("#preview-body", Static).update(Text("由 AI 协助创建调查员，或导入已有的人物卡。保存后可在“我的角色”中查看。", style="dim"))

    def _game(self, oid: str) -> GameState | None:
        """The save a card comes from, for its 本局经历; None for kept cards and pregens."""
        tag = oid.split(":", 1)[0]
        raw = self.saves.get(tag)
        if isinstance(raw, dict):
            try:
                raw = GameState.model_validate(check_save(raw))
            except ValueError:  # a save this version cannot read (another version's too) still shows the card
                raw = None
            self.saves[tag] = raw
        return raw

    @work(exclusive=True, group="card")
    async def _show(self, oid: str) -> None:
        inv, _, m = self.cards[oid]
        body = self.query_one("#preview-body", Static)
        width = body.content_size.width or None
        game = self._game(oid)
        body.update(sheet_view(inv, width=width, state=game))  # the words at once, the face when it is drawn
        face = await asyncio.to_thread(self._portrait, inv, m, self.app.art_style)  # type: ignore[attr-defined]
        if face is not None:
            body.update(sheet_view(inv, face, width, state=game))

    @on(OptionList.OptionSelected, "#choices")
    def open(self) -> None:
        self.action_open()

    def action_open(self) -> None:
        if self._highlighted() == "create":
            self.action_create()
            return
        oid = self._chosen()
        if oid is None:
            return
        inv, party, m = self.cards[oid]
        style = self.app.art_style  # type: ignore[attr-defined]
        self.app.push_screen(SheetModal(party, party.index(inv), lambda i: self._portrait(i, m, style), self._game(oid)))

    # -- changing ------------------------------------------------------------------------------
    def _mine(self) -> Investigator | None:
        """The highlighted card if it is one of 我的角色; else a word on why it cannot change."""
        oid = self._chosen()
        if oid is None:
            return None
        if not oid.startswith("r"):
            self.app.notify("此人物卡不可直接编辑，按 C 创建副本后修改。", severity="warning")
            return None
        return self.cards[oid][0]

    def action_edit(self) -> None:
        from ..modals.cardedit import CardEditModal

        inv = self._mine()
        if inv is None:
            return

        def done(changes: dict | None) -> None:
            if changes is None:
                return
            try:
                update_kept(inv, changes["investigator"], changes["photo"])
            except (OSError, ValueError, KeyError):
                self.app.notify("保存失败", severity="error")
                return
            self._fill(f"r:{inv.id}")
            self.app.notify("已保存")

        self.app.push_screen(CardEditModal(inv, photo(inv, None) is not None), done)

    def action_delete(self) -> None:
        inv = self._mine()
        if inv is None:
            return

        def done(answer: str | None) -> None:
            if answer != "yes":
                return
            try:
                delete_kept(inv)
            except OSError:
                self.app.notify("删除失败", severity="error")
                return
            self._fill()
            self.app.notify(f"已删除 {inv.name}")

        self.app.push_screen(Confirm(f"删除 {inv.name}？", [("yes", "删除"), ("no", "取消")]), done)

    def action_copy(self) -> None:
        oid = self._chosen()
        if oid is None:
            return
        if oid.startswith("r"):
            self.app.notify("此人物卡已保存在“我的角色”中。")
            return
        inv, _, m = self.cards[oid]
        try:
            card = keep_copy(inv, m, self.titles.get(oid.split(":", 1)[0], ""))
        except OSError:
            self.app.notify("复制失败", severity="error")
            return
        self._fill(f"r:{card.id}")
        self.app.notify(f"{card.name} 已保存至“我的角色”")

    def action_export(self) -> None:
        oid = self._chosen()
        if oid is None:
            return
        inv, _, m = self.cards[oid]

        def done(where: str | None) -> None:
            if where is None:
                return
            try:
                target = export(inv, m, Path(where).expanduser() if where else paths.card_exports())
            except OSError:
                self.app.notify("无法写入该文件夹，请检查权限或选择其他位置。", severity="error")
                return
            self.app.notify(f"已导出到 {target}", timeout=10)

        self.app.push_screen(AskText("选择导出文件夹", str(paths.card_exports())), done)

    def action_create(self) -> None:
        items = [("figure", "AI 协助车卡"), ("import", "导入已有的卡")]
        self.app.push_screen(GameMenu(items, "figure", title="新建角色", hint="Enter 选择 · Esc 返回"),
                             lambda how: self._make(how) if how else None)  # fmt: skip

    def action_import_card(self) -> None:
        self._make("import")

    def _make(self, start: str) -> None:
        """The card flows of 新的调查 (tui/screens/card.py), in the first module's era; the card they
        keep lands in 我的角色."""
        from .card import CardScreen

        try:
            screen = CardScreen(Module.load(paths.module_folders()[0]), start=start)
        except (IndexError, OSError, ValueError):
            self.app.notify("创建调查员前需先导入或选择模组。", severity="error")
            return
        self.app.push_screen(screen, lambda card: self._fill(f"r:{card.id}" if card else None))

    def action_new_game(self) -> None:
        """Straight to 新的调查 with this card picked: a pregen in its own module; one of the
        player's own in whichever module they choose next."""
        from .setup import SetupScreen

        oid = self._chosen()
        if oid is None:
            return
        tag, inv_id = oid.split(":", 1)
        if tag.startswith("s"):
            self.app.notify("按 C 创建角色副本后，可用于新的调查。", severity="warning")
        elif tag.startswith("m"):
            self.app.switch_screen(SetupScreen(spectate=False, module=self.paths[tag], card=inv_id))
        else:
            self.app.switch_screen(SetupScreen(spectate=False, card=inv_id))
