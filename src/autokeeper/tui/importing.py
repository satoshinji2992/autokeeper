"""导入模组 from the title screen: pick a file in a browser or drop it on the terminal, check
the id and the pages, then watch the importer work and start a game with the result.

The import is the pipeline behind `autokeeper import` (module/importer.py), run in a worker
on the app's loop with its console printing into the log here. A zip is unpacked into a
temporary folder first (module/sources.py): the player picks the document when it holds
several, and its pictures go to the importer along with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import re
import shlex
import tempfile
import time
import traceback
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from rich.console import Console
from rich.filesize import decimal
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, ContentSwitcher, DirectoryTree, Footer, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from .. import config
from ..harness import models as channels
from ..module import importer, sources
from ..module.schema import Module

READABLE = (*sources.DOCUMENTS, *sources.ARCHIVES)
KIND = {".pdf": "PDF", ".docx": "Word 文档", ".md": "Markdown", ".txt": "纯文本", ".zip": "压缩包"}
INSTEAD = {".doc": "用 Word 或 WPS 另存为 .docx 再导入", ".wps": "用 WPS 另存为 .docx 再导入", ".rar": "先解压，再选里面的模组文件", ".7z": "先解压，再选里面的模组文件"}
STAGES = ("提取原文", "读模组 · 写模组 · 画图", "校验", "编译")
STAGE_OF = {"提取": 0, "附图": 0, "构建": 1, "构建结束": 1, "修复": 1, "校验": 2, "完成": 3, "建库": 3, "知识库": 3}  # first word of the importer's lines
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PAGES = re.compile(r"\d+(-\d+)?(,\d+(-\d+)?)*")
WINDOWS_ACCOUNTS = {"Public", "Default", "Default User", "All Users"}  # under C:\Users, not people


# -- where files come from ----------------------------------------------------------------------
def dropped_path(text: str) -> Path | None:
    """The existing file or folder in what a terminal pastes when one is dragged onto it: a
    plain path, a quoted one, one with backslash-escaped spaces, or a file:// URI (the first,
    when several were dropped). Under WSL a Windows path is tried as the Linux one too."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines or len(lines[0]) > 1000:  # a pasted paragraph is not a path
        return None
    s = lines[0]
    unquoted = s[1:-1] if len(s) > 1 and s[0] == s[-1] and s[0] in "'\"" else s
    tries = [s, unquoted]
    if unquoted.lower().startswith("file:"):
        tries.append(url2pathname(urlparse(unquoted).path))  # percent-decoded; /C:/Users/… becomes C:\Users\… on Windows
    if os.name != "nt":  # on Windows a backslash separates folders and escapes nothing
        with contextlib.suppress(ValueError):  # an unbalanced quote: it was not shell quoting
            tries += shlex.split(s)[:1]
        tries += [p for p in (from_windows(unquoted),) if p]
    for t in tries:
        try:
            if t and (p := Path(t).expanduser()).exists():
                return p
        except (OSError, ValueError, RuntimeError):  # "file name too long", a NUL, an unknown ~user
            continue
    return None


def from_windows(text: str) -> str | None:
    """A Windows path as WSL sees it: C:\\Users\\… → /mnt/c/Users/…, \\\\wsl.localhost\\Ubuntu\\root\\… → /root/…
    Explorer drags these in, and older Windows Terminals paste them untranslated."""
    if m := re.fullmatch(r"([A-Za-z]):[\\/](.*)", text, re.S):
        return f"/mnt/{m[1].lower()}/" + m[2].replace("\\", "/")
    if m := re.fullmatch(r"\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)?", text, re.S | re.I):
        return (m[1] or "\\").replace("\\", "/")
    return None


def places() -> list[tuple[str, Path]]:
    """Where module files usually are: the modules folder, downloads, home, and under WSL the
    Windows downloads, where the browser saved them."""
    from platformdirs import user_downloads_dir

    spots = [("模组文件夹", config.MODULES), ("下载", Path(user_downloads_dir())), ("主目录", Path.home())]
    if "microsoft" in platform.uname().release.lower():
        found = [p for p in sorted(Path("/mnt/c/Users").glob("*/Downloads")) if p.parent.name not in WINDOWS_ACCOUNTS]
        spots += [("Windows 下载" + (f"（{p.parent.name}）" if len(found) > 1 else ""), p) for p in found]
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for label, path in spots:
        try:
            if path.is_dir() and (key := path.resolve()) not in seen:
                seen.add(key)
                out.append((label, path))
        except OSError:
            continue
    return out


class ModuleFiles(DirectoryTree):
    """Folders, and only the files the importer can read; the folder shown is in the border."""

    ICON_NODE, ICON_NODE_EXPANDED, ICON_FILE = "▸ ", "▾ ", "  "  # emoji folders are two cells wide, or tofu
    BINDINGS = [Binding("backspace", "up_dir", "上一级")]

    def __init__(self, path: Path, **kw):
        super().__init__(path, **kw)
        self.show_root = False  # its name is the border title
        self.border_title = tidy(path)

    def browse(self, path: Path) -> None:
        self.path = path
        self.border_title = tidy(path)

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".") and (_is_dir(p) or p.suffix.lower() in READABLE)]

    def action_up_dir(self) -> None:
        self.browse(Path(self.path).resolve().parent)


def tidy(path: Path) -> str:
    """A folder as shown: ~ for home."""
    try:
        rel = Path(path).resolve().relative_to(Path.home().resolve())
    except (ValueError, OSError):
        return str(path)
    return "~" if rel == Path() else f"~/{rel.as_posix()}"


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


# -- what was picked ----------------------------------------------------------------------------
@dataclass
class Document:
    path: Path
    label: str  # its name, or its place inside the zip
    size: int
    pages: int | None = None  # PDFs only
    broken: str = ""  # why it cannot be read


@dataclass
class Choice:
    file: Path  # what the player picked: a document or a zip
    documents: list[Document]
    images: list[Path] = field(default_factory=list)


@dataclass
class Job:
    document: Path
    module_id: str
    pages: str | None
    images: list[Path]
    label: str


def look_inside(path: Path, scratch: Path | None) -> Choice:
    """What a picked file holds; runs in a thread, since a zip is unpacked into scratch."""
    if scratch is None:
        return Choice(path, [_document(path, path.name)])
    found = sources.unpack(path, scratch)
    return Choice(path, [_document(p, p.relative_to(scratch).as_posix()) for p in found.documents], found.images)


def _document(path: Path, label: str) -> Document:
    doc = Document(path, label, path.stat().st_size)
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as exc:  # a password, a damaged file
            doc.broken = f"这个 PDF 打不开（{exc}）"
        else:
            doc.pages = len(pdf)
            pdf.close()
    return doc


def _describe(doc: Document) -> str:
    kind = KIND.get(doc.path.suffix.lower(), doc.path.suffix)
    return f"{kind} · {size(doc.size)}" + (f" · {doc.pages} 页" if doc.pages else "")


def size(n: int) -> str:
    return decimal(n).replace("bytes", "字节").replace("byte", "字节")


def page_spec(text: str) -> str:
    """A page range however it was typed: full-width digits, 7–18, 7~18, 7至18, 3，5、7."""
    s = unicodedata.normalize("NFKC", text)
    s = re.sub(r"[–—~至到]", "-", s)
    return re.sub(r"\s+", "", re.sub(r"[、;]", ",", s))


def _reason(exc: BaseException) -> str:
    """An error as a player can read it."""
    if isinstance(exc, zipfile.BadZipFile):
        return "不是完好的 zip 压缩包"
    if isinstance(exc, PermissionError):
        return "没有权限读这个文件"
    text = str(exc).strip()
    return f"{text}（{type(exc).__name__}）" if text else type(exc).__name__


def _engine_note() -> Text:
    try:
        cfg = config.load()
    except ValueError as exc:  # a preset or a mode that does not exist
        return Text(f"模型配置有问题：{exc}", style="bold red")
    role = cfg.role("importer")
    t = Text()
    t.append("导入 Agent  ", style="bold")
    t.append(role.model)
    t.append(f"（{role.backend}" + (f"，思考 {role.effort}" if role.effort else "") + "）\n", style="dim")
    t.append("它会通读全文，整理出地点、人物、线索和结局，给每个地点和人物画图，再自己校验到没有错误。", style="dim")
    t.append("一本几十页的模组大约要二十分钟。", style="dim")
    if cfg.budget_usd is not None:
        t.append(f"按量计费的 API 花费上限 ${cfg.budget_usd:g}。", style="dim")
    return t


class LogFile:
    """A file for rich's Console: every line the importer prints goes to the log as Text."""

    def __init__(self, emit: Callable[[Text], None]):
        self.emit, self.pending = emit, ""

    def write(self, data: str) -> int:
        *lines, self.pending = (self.pending + data).split("\n")
        for line in lines:
            self.emit(Text.from_ansi(line))
        return len(data)

    def flush(self) -> None:
        pass


# -- screens ------------------------------------------------------------------------------------
class StopModal(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss(False)", "继续导入")]

    def __init__(self, module_id: str):
        super().__init__()
        self.module_id = module_id

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static("停下导入？", classes="modal-title")
            yield Static(f"导入 Agent 会马上停下。已经写好的部分留在 modules/local/{self.module_id}/，重新导入会从头来过。", classes="modal-body")
            with Horizontal(classes="buttons"):
                yield Button("停下", id="halt", variant="error")
                yield Button("继续导入", id="keep", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#keep", Button).focus()

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "halt")


class ImportScreen(Screen):
    """Pick a file, confirm the id and pages, watch the import, then play it."""

    CSS_PATH = "importing.tcss"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self.step = "browse"  # browse → confirm → run → done
        self.spots = places()
        self.choice: Choice | None = None
        self.unpacked: tempfile.TemporaryDirectory | None = None  # the picked zip's contents
        self.derived = ""  # the id from the file name
        self.job: Job | None = None
        self.importing: Worker | None = None
        self.imported: Path | None = None  # the module, once it passed
        self.began = 0.0
        self.spin = 0
        self.stage = 0
        self.steps = 0  # the agent's tool calls so far

    def compose(self) -> ComposeResult:
        start = self.spots[0][1] if self.spots else Path.home()
        with Vertical(id="setup"):
            yield Static(id="setup-title")
            with ContentSwitcher(initial="browse", id="import-steps"):
                with Vertical(id="browse"):
                    yield Static(
                        Text("PDF、Word（.docx）、Markdown、纯文本都行，也可以是装着它们的 .zip。导入的模组只存在你自己的电脑上。", style="dim"),
                        id="browse-intro",
                    )
                    with Horizontal(id="browse-body"):
                        yield OptionList(*(Option(label, id=str(path)) for label, path in self.spots), id="places")
                        yield ModuleFiles(start, id="files")
                    yield Input(placeholder="或者把模组文件拖进终端、粘贴它的路径，回车", id="drop")
                with Vertical(id="confirm"):
                    with VerticalScroll(id="confirm-body"):
                        yield Static(id="picked")
                        yield OptionList(id="documents")
                        yield Static(id="extras")
                        with Horizontal(classes="field"):
                            yield Static("模组短名", classes="field-label")
                            yield Input(id="module-id", compact=True)
                        yield Static(id="id-note", classes="field-note")
                        with Horizontal(classes="field"):
                            yield Static("页码", classes="field-label")
                            yield Input(placeholder="空着就是全部，比如 7-18", id="pages", compact=True)
                        yield Static(id="pages-note", classes="field-note")
                        yield Static(id="engine")
                    with Horizontal(classes="buttons"):
                        yield Button("开始导入", id="start", variant="primary")
                        yield Button("换个文件", id="rechoose")
                with Vertical(id="run"):
                    with Horizontal(id="stages"):
                        for i, label in enumerate(STAGES):
                            yield Static(label, id=f"stage-{i}", classes="stage")
                    yield RichLog(id="import-log", wrap=True, min_width=1, max_lines=5000)
                    yield Static(id="import-status")
                    with Horizontal(classes="buttons"):
                        yield Button("停下导入", id="stop", variant="error")
                        yield Button("开始游戏", id="play", variant="primary")
                        yield Button("重新导入", id="retry", variant="primary")
                        yield Button("换个文件", id="another")
                        yield Button("返回标题", id="leave")
            yield Static(id="setup-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)
        self._show_browse()

    def on_unmount(self) -> None:
        self._unlisten()
        if self.unpacked:
            self.unpacked.cleanup()

    def _frame(self, title: str, hint: str) -> None:
        self.query_one("#setup-title", Static).update(Text(title, style="bold"))
        self.query_one("#setup-hint", Static).update(hint)

    def action_back(self) -> None:
        if self.step == "confirm":
            self._show_browse()
        elif self.step == "run":
            self._ask_stop()
        else:
            self.app.pop_screen()

    # -- browse ------------------------------------------------------------------------------------
    def _show_browse(self) -> None:
        self.step = "browse"
        self.query_one(ContentSwitcher).current = "browse"
        self._frame("导入模组 · 选一个模组文件", "↑↓ 选择 · Enter 打开 · Backspace 上一级 · 文件可以直接拖进来 · Esc 返回")
        self.query_one(ModuleFiles).focus()

    @on(OptionList.OptionSelected, "#places")
    def place_chosen(self, event: OptionList.OptionSelected) -> None:
        files = self.query_one(ModuleFiles)
        files.browse(Path(event.option.id or "."))
        files.focus()

    @on(DirectoryTree.FileSelected, "#files")
    def file_chosen(self, event: DirectoryTree.FileSelected) -> None:
        self._pick(event.path)

    @on(Input.Submitted, "#drop")
    def path_entered(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        path = dropped_path(event.value)
        if path is None:
            self.app.notify(f"找不到这个文件：{event.value.strip()}", severity="error")
            return
        event.input.value = ""
        self._pick(path)

    def on_paste(self, event: events.Paste) -> None:
        """A file dragged onto the terminal arrives as a paste (an input box keeps its own)."""
        if self.step not in ("browse", "confirm") or not event.text.strip():
            return
        path = dropped_path(event.text)
        if path is None:
            self.app.notify(f"找不到这个文件：{event.text.strip()[:200]}", severity="error")
        else:
            self._pick(path)

    def _pick(self, path: Path) -> None:
        if _is_dir(path):
            self.query_one(ModuleFiles).browse(path)
            self._show_browse()
        elif path.suffix.lower() not in READABLE:
            instead = INSTEAD.get(path.suffix.lower(), "只认 PDF、Word（.docx）、Markdown、纯文本，和装着它们的 .zip")
            self.app.notify(f"导入不了 {path.name}：{instead}", severity="error", timeout=6)
        else:
            self._open(path)

    @work(exclusive=True, group="open", exit_on_error=False)
    async def _open(self, path: Path) -> None:
        if self.unpacked:
            self.unpacked.cleanup()
            self.unpacked = None
        scratch = None
        if path.suffix.lower() in sources.ARCHIVES:
            self.unpacked = tempfile.TemporaryDirectory(prefix="autokeeper-zip-", ignore_cleanup_errors=True)
            scratch = Path(self.unpacked.name)
            self._frame("导入模组", f"正在解开 {path.name} …")
        try:
            choice = await asyncio.to_thread(look_inside, path, scratch)
        except Exception as exc:  # a damaged zip, a password, no permission: say so and stay
            self.app.notify(f"打不开 {path.name}：{_reason(exc)}", severity="error", timeout=8)
            self._show_browse()
            return
        if not choice.documents:
            self.app.notify(f"{path.name} 里没有 PDF、Word（.docx）、Markdown 或纯文本文件", severity="error", timeout=8)
            self._show_browse()
            return
        self._show_confirm(choice)

    # -- confirm -----------------------------------------------------------------------------------
    def _show_confirm(self, choice: Choice) -> None:
        self.choice = choice
        f = choice.file
        card = Text()
        card.append(f"{f.name}\n", style="bold")
        card.append(f"{f.parent}\n", style="dim")
        if choice.documents[0].path == f:
            card.append(_describe(choice.documents[0]))
        else:
            card.append(f"压缩包 · {size(f.stat().st_size)} · {len(choice.documents)} 份文档")
            card.append(f" · {len(choice.images)} 张图片" if choice.images else "")
            if len(choice.documents) == 1:
                card.append(f"\n模组正文：{choice.documents[0].label}（{_describe(choice.documents[0])}）")
        self.query_one("#picked", Static).update(card)
        docs = self.query_one("#documents", OptionList)
        docs.clear_options()
        for d in choice.documents:
            line = Text(d.label)
            line.append(f"   {_describe(d)}", style="dim")
            line.append("   打不开" if d.broken else "", style="bold red")
            docs.add_option(Option(line))
        docs.display = len(choice.documents) > 1
        docs.border_title = "选一份作模组正文"
        docs.highlighted = 0
        extras = Text()
        if choice.images:
            shown = "、".join(p.name for p in choice.images[:4]) + (" 等" if len(choice.images) > 4 else "")
            extras.append(f"压缩包里的 {len(choice.images)} 张图片（{shown}）会一起交给导入 Agent，地图和手稿它会看。", style="dim")
        self.query_one("#extras", Static).update(extras)
        self.query_one("#extras", Static).display = bool(choice.images)
        self.derived = sources.module_id(f.stem)
        self.query_one("#module-id", Input).value = self.derived
        self.query_one("#pages", Input).value = ""
        self.query_one("#engine", Static).update(_engine_note())
        self._document_changed()
        self.step = "confirm"
        self.query_one(ContentSwitcher).current = "confirm"
        self._frame("导入模组 · 确认", "Enter 开始导入 · Tab 切换 · Esc 换个文件")
        (docs if docs.display else self.query_one("#start", Button)).focus()

    def _document(self) -> Document:
        assert self.choice
        return self.choice.documents[self.query_one("#documents", OptionList).highlighted or 0]

    @on(OptionList.OptionHighlighted, "#documents")
    def document_moved(self) -> None:
        if self.choice:
            self._document_changed()

    @on(OptionList.OptionSelected, "#documents")
    def document_chosen(self) -> None:
        self.query_one("#start", Button).focus()

    def _document_changed(self) -> None:
        pages = self.query_one("#pages", Input)
        pages.disabled = self._document().pages is None
        self._notes()

    @on(Input.Changed, "#module-id, #pages")
    def form_edited(self) -> None:
        if self.choice:
            self._notes()

    @on(Input.Submitted, "#module-id, #pages")
    def form_submitted(self) -> None:
        self._begin()

    def _module_id(self) -> str:
        typed = self.query_one("#module-id", Input).value.strip()
        return sources.module_id(typed) if typed else self.derived

    def _id_check(self) -> tuple[str, str]:
        """(level, note) for the id: where the module goes, or why it cannot go there."""
        from .app import modules

        mid = self._module_id()
        same = next((p for p in modules() if p.name == mid), None)
        if same is None:
            return "", f"导入后放在 modules/local/{mid}/"
        if same.parent != config.LOCAL_MODULES:
            return "error", f"{mid} 是游戏自带模组的名字，换一个"
        try:
            title = f"《{Module.load(same).title}》"
        except Exception:  # a half-finished import
            title = "一个模组"
        return "warning", f"modules/local/{mid}/ 已经有{title}，导入会覆盖它；想另存一份就换个短名"

    def _pages_check(self) -> tuple[str | None, str, str]:
        """(the --pages value, level, note)."""
        doc = self._document()
        if doc.broken:
            return None, "error", doc.broken
        if doc.pages is None:
            return None, "", "只有 PDF 能挑页导入"
        spec = page_spec(self.query_one("#pages", Input).value)
        if not spec:
            return None, "", f"共 {doc.pages} 页，全部导入；只要正文的话填页码，比如 7-18"
        if not PAGES.fullmatch(spec):
            return None, "error", "页码写成 7-18，或者 3,5,7-9"
        chosen = importer._pages(spec, doc.pages)
        if not chosen:
            return None, "error", f"这个 PDF 只有 {doc.pages} 页"
        return spec, "", f"导入其中 {len(chosen)} 页（共 {doc.pages} 页）"

    def _notes(self) -> None:
        for (level, note), target in ((self._id_check(), "#id-note"), (self._pages_check()[1:], "#pages-note")):
            widget = self.query_one(target, Static)
            widget.update(note)
            widget.set_class(level == "warning", "warning")
            widget.set_class(level == "error", "error")

    def _begin(self) -> None:
        if self.step != "confirm" or not self.choice:
            return
        (level, note), (spec, plevel, pnote) = self._id_check(), self._pages_check()
        if "error" in (level, plevel):
            self.app.notify(note if level == "error" else pnote, severity="error")
            return
        doc = self._document()
        label = doc.label if doc.path == self.choice.file else f"{self.choice.file.name} 里的 {doc.path.name}"
        self.job = Job(doc.path, self._module_id(), spec, list(self.choice.images), label)
        self._run()

    # -- run ---------------------------------------------------------------------------------------
    def _run(self) -> None:
        assert self.job
        log = self.query_one("#import-log", RichLog)
        log.clear()
        log.border_title = f"→ modules/local/{self.job.module_id}/"
        self.began, self.stage, self.steps, self.imported = time.monotonic(), 0, 0, None
        self._stages(0)
        self._buttons("stop")
        status = self.query_one("#import-status", Static)
        status.set_classes("")
        status.update("")
        self.step = "run"
        self.query_one(ContentSwitcher).current = "run"
        self._frame(f"正在导入 {self.job.label}", "导入要一阵子，别关游戏 · Esc 停下导入")
        if self._fell_back not in channels.listeners:
            channels.listeners.append(self._fell_back)
        self.importing = self._import(self.job)
        self.query_one("#stop", Button).focus()

    @work(exclusive=True, group="import", exit_on_error=False)
    async def _import(self, job: Job) -> Path:
        console = Console(file=LogFile(self._logged), force_terminal=True, color_system="truecolor", width=200, soft_wrap=True, highlight=False)
        return await importer.run_import(job.document, job.module_id, pages=job.pages, images=job.images, console=console)

    def _logged(self, line: Text) -> None:
        plain = line.plain.strip()
        head = plain.split(maxsplit=1)[0] if plain else ""
        if head in STAGE_OF:
            self._stages(STAGE_OF[head])
        elif plain.startswith("·"):
            self.steps += 1
        self.query_one("#import-log", RichLog).write(line)

    def _fell_back(self, note: str) -> None:
        self.query_one("#import-log", RichLog).write(Text(f"⚠ {note}", style="bold yellow"))

    def _unlisten(self) -> None:
        if self._fell_back in channels.listeners:
            channels.listeners.remove(self._fell_back)

    def _stages(self, current: int) -> None:
        """The strip over the log: done ✓, the one running, the ones to come (len(STAGES): all done)."""
        self.stage = min(current, len(STAGES) - 1)
        for i, label in enumerate(STAGES):
            box = self.query_one(f"#stage-{i}", Static)
            box.update(f"✓ {label}" if i < current else label)
            box.set_class(i < current, "done")
            box.set_class(i == current, "current")
            box.remove_class("failed")

    def _tick(self) -> None:
        if self.step != "run":
            return
        self.spin = (self.spin + 1) % len(SPINNER)
        doing = "正在停下" if self.importing and self.importing.is_cancelled else STAGES[self.stage]
        parts = [doing, *([f"第 {self.steps} 步"] if self.steps else []), f"已用 {self._elapsed()}"]
        self.query_one("#import-status", Static).update(Text(f"{SPINNER[self.spin]} " + " · ".join(parts)))

    def _elapsed(self) -> str:
        secs = int(time.monotonic() - self.began)
        return f"{secs // 60} 分 {secs % 60:02d} 秒" if secs >= 60 else f"{secs} 秒"

    def _ask_stop(self) -> None:
        if self.job:
            self.app.push_screen(StopModal(self.job.module_id), self._stop_answer)

    def _stop_answer(self, stop: bool | None) -> None:
        if stop and self.step == "run" and self.importing:
            self.importing.cancel()

    # -- done --------------------------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self.importing or self.job is None:
            return
        if event.state == WorkerState.SUCCESS:
            self._succeeded(self.job)
        elif event.state == WorkerState.ERROR:
            self._failed(event.worker.error)
        elif event.state == WorkerState.CANCELLED:
            self._done("导入已停下", f"■ 导入已停下（{self._elapsed()}）。写好的部分留在 modules/local/{self.job.module_id}/。", ok=False)

    def _succeeded(self, job: Job) -> None:
        work = config.LOCAL_MODULES / job.module_id
        if issues := importer.validate(work):
            log = self.query_one("#import-log", RichLog)
            for issue in issues:
                log.write(Text(f"  {issue}", style="red"))
            self._done(
                "导入没有完成",
                f"✗ 导入跑完了，还有 {len(issues)} 个问题没修好（见上面的日志）。半成品留在 modules/local/{job.module_id}/，可以重新导入再试一次。",
                ok=False,
            )
            return
        m = Module.load(work)
        self.imported = work
        self._stages(len(STAGES))
        self._done(
            "导入完成",
            f"✓ 《{m.title}》导入好了：{len(m.locations)} 个地点、{len(m.npcs)} 个 NPC、{len(m.clues)} 条线索、{len(m.art)} 张图，"
            f"用时 {self._elapsed()}。以后在“新的调查”里也能找到它。",
            ok=True,
        )

    def _failed(self, exc: BaseException | None) -> None:
        if exc is not None:
            trace = traceback.format_exception(exc, limit=-3)  # the frames nearest the error, not the worker's
            self.query_one("#import-log", RichLog).write(Text("".join(trace).rstrip(), style="dim"))
        reason = _reason(exc) if exc is not None else "原因不明"
        self._done("导入没有完成", f"✗ 导入没有完成：{reason}", ok=False)

    def _done(self, title: str, message: str, *, ok: bool) -> None:
        self._unlisten()
        self.step = "done"
        status = self.query_one("#import-status", Static)
        status.update(message)
        status.set_classes("ok" if ok else "bad")
        if not ok:
            self.query_one(f"#stage-{self.stage}", Static).add_class("failed")
        self.query_one("#another", Button).label = "再导入一个" if ok else "换个文件"
        self._buttons(*(("play", "another", "leave") if ok else ("retry", "another", "leave")))
        self._frame(title, "Esc 返回标题")
        self.query_one("#play" if ok else "#retry", Button).focus()

    def _buttons(self, *shown: str) -> None:
        for button in self.query("#run Button").results(Button):
            button.display = button.id in shown

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        actions = {
            "start": self._begin,
            "rechoose": self._show_browse,
            "another": self._show_browse,
            "stop": self._ask_stop,
            "retry": self._run,
            "play": self._play,
            "leave": self.app.pop_screen,
        }
        if event.button.id in actions:
            actions[event.button.id]()

    def _play(self) -> None:
        from .app import SetupScreen

        self.app.switch_screen(SetupScreen(spectate=False, module=self.imported))
