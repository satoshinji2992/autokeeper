"""Importing a module from the title screen, with no model calls: dropped paths, ids from
file names, zips from Chinese Windows, and the screen driving a stand-in importer."""

from __future__ import annotations

import asyncio
import io
import os
import re
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from rich.console import Console
from textual import events
from textual.widgets import Button, Input, OptionList, RichLog, Select, Static

from autokeeper import config
from autokeeper.config import EXAMPLE
from autokeeper.module import importer, sources
from autokeeper.module.sources import dropped_path, from_windows
from autokeeper.tui import app as tui
from autokeeper.tui.app import AutoKeeperApp, SetupScreen, TitleScreen
from autokeeper.tui.importing import ImportScreen, ModuleFiles, StopModal, page_spec


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


def legacy_zip(path: Path, members: dict[str, bytes], encoding: str = "gbk") -> Path:
    """A zip whose names are stored in `encoding` without the UTF-8 flag, as Chinese Windows
    (GBK) or macOS (UTF-8) make them. zipfile flags any non-ASCII name it writes, so each
    entry goes in under an ASCII stand-in of the same byte length, swapped afterwards."""
    swaps = {}
    with zipfile.ZipFile(path, "w") as zf:
        for letter, (name, data) in zip("QRSTUVWXYZ", members.items(), strict=False):
            raw = name.encode(encoding)
            stand_in = bytes(b if b < 0x80 else ord(letter) for b in raw)
            zf.writestr(stand_in.decode("ascii"), data)
            swaps[stand_in] = raw
    blob = path.read_bytes()
    for stand_in, raw in swaps.items():
        assert blob.count(stand_in) == 2  # the local header and the central directory
        blob = blob.replace(stand_in, raw)
    path.write_bytes(blob)
    return path


# -- dropped and pasted paths ---------------------------------------------------------------------
def test_dropped_paths_come_quoted_plain_or_as_uris(tmp_path):
    f = tmp_path / "模组 文件" / "冬雪之下 (1).pdf"
    f.parent.mkdir()
    f.write_bytes(b"%PDF-1.4")
    for pasted in (str(f), f"'{f}'", f'"{f}"', f"  '{f}' \n", f.as_uri(), f"{f.as_uri()}\r\n{f.as_uri()}\r\n"):
        assert dropped_path(pasted) == f, pasted
    assert dropped_path(f'"{f.parent}"') == f.parent  # a folder: the browser goes there
    assert dropped_path(str(tmp_path / "没有这个.pdf")) is None
    assert dropped_path(tmp_path.as_uri() + "/%E6%B2%A1%E6%9C%89.pdf") is None
    assert dropped_path("") is None and dropped_path("   \n ") is None and dropped_path("''") is None
    assert dropped_path("一段" * 600) is None  # a pasted paragraph
    assert dropped_path("一段" * 100) is None  # one name past 255 bytes: "file name too long"
    assert dropped_path("a\0b") is None


@pytest.mark.skipif(os.name == "nt", reason="on Windows a backslash separates folders")
def test_dropped_paths_with_backslash_escapes(tmp_path):
    f = tmp_path / "模组 文件" / "冬雪之下 (1).pdf"
    f.parent.mkdir()
    f.write_bytes(b"%PDF-1.4")
    second, quote = tmp_path / "第二份.zip", tmp_path / "it's.zip"
    second.write_bytes(b"PK")
    quote.write_bytes(b"PK")
    assert dropped_path(re.sub(r"([ ()])", r"\\\1", str(f))) == f  # iTerm2, Terminal.app, kitty
    assert dropped_path(f"'{f}' '{second}' ") == f  # two files dropped at once: the first
    assert dropped_path("'" + str(quote).replace("'", "'\\''") + "'") == quote  # GNOME Terminal quoting a quote


def test_windows_paths_as_wsl_sees_them():
    assert from_windows(r"C:\Users\me\Downloads\冬雪之下.pdf") == "/mnt/c/Users/me/Downloads/冬雪之下.pdf"
    assert from_windows("D:/模组/x.zip") == "/mnt/d/模组/x.zip"
    assert from_windows(r"\\wsl.localhost\Ubuntu-24.04\root\code\x.zip") == "/root/code/x.zip"
    assert from_windows("\\\\wsl$\\Ubuntu\\home\\me") == "/home/me"
    assert from_windows("/home/me/x.pdf") is None


def test_page_ranges_however_they_are_typed():
    assert page_spec(" 7 - 18 ") == "7-18"
    assert page_spec("７～１８，２０") == "7-18,20"  # full-width, as a Chinese IME types it
    assert page_spec("3、5至9") == "3,5-9"
    assert importer._pages(page_spec("7–9"), 8) == [7, 8]


# -- ids -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, expected",
    [
        ("冬雪之下", "dongxuezhixia"),
        ("fallout3 - 有些人油盐不进", "fallout3-youxierenyouyanbujin"),
        ("追书人", "zhuishuren"),  # the bundled module's id
        ("重庆森林", "chongqingsenlin"),  # a reading that depends on the word
        ("冬雪之下（上）", "dongxuezhixia-shang"),
        ("The Haunting (CoC 7e)", "the-haunting-coc-7e"),
        ("Café Noir", "cafe-noir"),
    ],
)
def test_module_id_from_a_file_name(name, expected):
    assert sources.module_id(name) == expected


def test_module_ids_are_skill_names():
    hashed = sources.module_id("！！！")
    assert re.fullmatch(r"mozu-[0-9a-f]{6}", hashed) and hashed == sources.module_id("！！！") != sources.module_id("？？？")
    for name in ("！！！", "一" * 80, "第 3 章：雪夜", "--a__b--"):
        mid = sources.module_id(name)
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", mid) and len(f"module-{mid}") <= 64, mid


# -- zips ----------------------------------------------------------------------------------------
def test_a_zip_from_chinese_windows_unpacks_with_chinese_names(tmp_path):
    archive = legacy_zip(
        tmp_path / "冬雪之下.zip",
        {
            "冬雪之下/正文.md": "# 冬雪之下\n\n调查员们来到山村。\n".encode() * 20,
            "冬雪之下/说明.txt": "先看正文。".encode("gbk"),
            "冬雪之下/地图.png": png(),
            "冬雪之下/安装.exe": b"MZ",
        },
    )
    with zipfile.ZipFile(archive) as zf:
        assert "冬雪之下/正文.md" not in zf.namelist()  # what zipfile alone makes of GBK names
    found = sources.unpack(archive, tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in found.documents] == ["冬雪之下/正文.md", "冬雪之下/说明.txt"]
    assert [p.name for p in found.images] == ["地图.png"]
    assert "调查员们来到山村" in found.documents[0].read_text(encoding="utf-8")
    assert not (tmp_path / "out" / "冬雪之下" / "安装.exe").exists()  # only documents and pictures come out


def test_zips_with_utf8_names_flagged_or_not(tmp_path):
    flagged = tmp_path / "flagged.zip"
    with zipfile.ZipFile(flagged, "w") as zf:
        zf.writestr("模组/正文.pdf", b"%PDF-1.4")
        zf.writestr("__MACOSX/模组/._正文.pdf", b"x")
        zf.writestr("模组/.DS_Store", b"x")
        zf.writestr("模组/~$正文.docx", b"x")
    assert [p.name for p in sources.unpack(flagged, tmp_path / "a").documents] == ["正文.pdf"]
    mac = legacy_zip(tmp_path / "mac.zip", {"模组/正文.docx": b"PK"}, encoding="utf-8")  # macOS's own zips
    assert [p.name for p in sources.unpack(mac, tmp_path / "b").documents] == ["正文.docx"]


# -- the importer's side -----------------------------------------------------------------------
async def test_pictures_from_a_zip_reach_the_importer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOCAL_MODULES", tmp_path / "local")

    async def no_agent(work, *, brief, prepare, console, cfg):  # prepare only: no model is called
        assert all(cfg.role(name).backend == "codex" for name in ("importer", "vision", "summarizer", "lore"))
        await prepare(console)
        return work

    monkeypatch.setattr(importer, "pipeline", no_agent)
    doc = tmp_path / "正文.md"
    doc.write_text("# 冬雪之下\n\n山村。\n", encoding="utf-8")
    maps = [tmp_path / d / "地图.png" for d in ("a", "b")]
    for m in maps:
        m.parent.mkdir()
        m.write_bytes(png())
    out = io.StringIO()
    work = await importer.run_import(
        doc, "dongxuezhixia", images=maps, console=Console(file=out), model="codex:default", vision_model="codex:default",
    )
    assert sorted(p.name for p in (work / "source" / "images").iterdir()) == ["地图.png", "地图_2.png"]
    text = (work / "source" / "source.md").read_text(encoding="utf-8")
    assert text.startswith("（随模组附带 2 张图片") and "source/images/地图_2.png" in text and "山村" in text
    assert "附图" in out.getvalue()


def test_the_cli_imports_a_zip_and_names_the_module(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from autokeeper import cli

    archive = legacy_zip(
        tmp_path / "冬雪之下.zip",
        {"冬雪之下/正文.md": b"# x\n" * 50, "冬雪之下/说明.txt": "先看正文。".encode("gbk"), "冬雪之下/地图.png": png()},
    )
    seen = {}

    async def fake_import(file, module_id, *, pages=None, images=(), console=None):
        seen.update(file=Path(file).name, module_id=module_id, images=[Path(p).name for p in images])

    monkeypatch.setattr(importer, "run_import", fake_import)
    result = CliRunner().invoke(cli.app, ["import", str(archive)], input="1\n")
    assert result.exit_code == 0, result.output
    assert seen == {"file": "正文.md", "module_id": "dongxuezhixia", "images": ["地图.png"]}
    assert "模组短名：dongxuezhixia" in result.output and "2. 冬雪之下/说明.txt" in result.output


# -- the screen ------------------------------------------------------------------------------------
@pytest.fixture
def library(monkeypatch, tmp_path):
    """modules/ and saves/ in a temp folder."""
    modules = tmp_path / "modules"
    modules.mkdir()
    monkeypatch.setattr(config, "MODULES", modules)
    monkeypatch.setattr(config, "LOCAL_MODULES", modules / "local")
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    return modules


def finished_module(module_id: str) -> Path:
    """What a successful import leaves behind: here, the format sample under the new id."""
    work = config.LOCAL_MODULES / module_id
    shutil.copytree(EXAMPLE, work)
    yaml = work / "module.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8").replace("id: example", f"id: {module_id}", 1), encoding="utf-8")
    return work


async def until(pilot, condition, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("timed out")


def shown(screen, selector: str) -> str:
    return str(screen.query_one(selector, Static).content)


async def open_import(app, pilot) -> ImportScreen:
    menu = app.screen.query_one("#menu", OptionList)
    menu.highlighted = menu.get_option_index("import")
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, ImportScreen)
    return app.screen


def test_import_models_are_independent_of_game_models(monkeypatch):
    original = config.Config()
    monkeypatch.setattr(config, "load", lambda: original)
    chosen = importer.import_config("codex:default", "openai:reader-model")
    assert chosen.role("importer").backend == "codex" and chosen.role("importer").model == "default"
    assert chosen.role("vision").backend == "langchain" and chosen.role("vision").model == "openai:reader-model"
    assert chosen.role("keeper") == original.role("keeper")
    assert chosen.role("summarizer").backend == "codex" and chosen.role("lore").backend == "codex"
    assert original.role("importer").backend == "claude" and original.role("vision").backend == "claude"
    assert original.role("summarizer").backend == "claude" and original.role("lore").backend == "claude"


async def test_import_screen_passes_separate_models_to_the_job(monkeypatch, library):
    doc = library / "抵达.md"
    doc.write_text("# 一座小镇", encoding="utf-8")
    calls = []

    async def fake_import(file, module_id, **kwargs):
        calls.append(kwargs)
        return finished_module(module_id)

    monkeypatch.setattr(importer, "run_import", fake_import)
    original = config.load().role("keeper")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await open_import(app, pilot)
        screen._pick(doc)
        await until(pilot, lambda: screen.step == "confirm")
        screen.query_one("#import-model", Select).value = "codex:default"
        screen.query_one("#vision-model", Input).value = "codex:default"
        await pilot.pause()
        assert "default" in shown(screen, "#engine")
        screen._begin()
        await until(pilot, lambda: screen.step == "done")
        assert calls[0]["model"] == "codex:default" and calls[0]["vision_model"] == "codex:default"
        assert config.load().role("keeper") == original


async def test_import_a_zip_from_the_title_screen_and_start_playing(monkeypatch, library, skills_dir):
    archive = legacy_zip(
        library / "冬雪之下.zip",
        {
            "冬雪之下/正文.md": "# 冬雪之下\n\n调查员们来到山村。\n".encode() * 40,
            "冬雪之下/说明.txt": "先看正文。".encode("gbk"),
            "冬雪之下/地图.png": png(),
        },
    )
    calls = []

    async def fake_import(file, module_id, *, pages=None, images=(), console=None):
        text = Path(file).read_text(encoding="utf-8")
        calls.append({"file": Path(file), "module_id": module_id, "pages": pages, "images": [Path(p) for p in images], "text": text})
        console.print(f"[bold]提取[/] {Path(file).name}：0 页，{len(text)} 字 → {config.LOCAL_MODULES / module_id}/source/")
        console.print("[bold]构建[/] fake:fake 开始读原文、写 module.yaml、画图…")
        console.print("  [cyan]· read_file[/] source/source.md  [dim]（共 81 行）[/]")
        await asyncio.sleep(0.2)
        work = finished_module(module_id)
        console.print("[bold]校验[/] 通过")
        console.print("[bold green]完成[/] 《格式样例·钟楼》")
        return work

    monkeypatch.setattr(importer, "run_import", fake_import)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = await open_import(app, pilot)
        assert screen.step == "browse" and isinstance(app.focused, ModuleFiles)
        screen.post_message(events.Paste(f"'{archive}' "))  # dragged onto the terminal: quoted, with a space after
        await until(pilot, lambda: screen.step == "confirm")
        docs = screen.query_one("#documents", OptionList)
        assert docs.display and docs.option_count == 2
        assert docs.get_option_at_index(0).prompt.plain.startswith("冬雪之下/正文.md")  # GBK name read right, biggest first
        assert screen.query_one("#module-id", Input).value == "dongxuezhixia"
        assert screen.query_one("#pages", Input).disabled  # pages are for PDFs
        assert "地图.png" in shown(screen, "#extras") and "modules/local/dongxuezhixia/" in shown(screen, "#id-note")
        await pilot.press("enter", "enter")  # this document, then 开始导入
        await until(pilot, lambda: screen.step == "done")
        assert "《格式样例·钟楼》导入好了" in shown(screen, "#import-status")
        (call,) = calls
        assert call["file"].name == "正文.md" and call["module_id"] == "dongxuezhixia" and call["pages"] is None
        assert [p.name for p in call["images"]] == ["地图.png"] and "调查员们来到山村" in call["text"]
        log = "\n".join(line.text for line in screen.query_one("#import-log", RichLog).lines)
        assert "提取 正文.md" in log and "· read_file" in log
        assert screen.steps == 1 and all("done" in screen.query_one(f"#stage-{i}").classes for i in range(4))
        assert config.LOCAL_MODULES / "dongxuezhixia" in tui.modules()  # in the module list now
        assert app.focused is screen.query_one("#play", Button)
        await pilot.press("enter")  # 开始游戏
        await until(pilot, lambda: isinstance(app.screen, SetupScreen) and app.screen.step == "investigator")
        assert app.screen.module and app.screen.module.id == "dongxuezhixia"


async def test_failures_are_shown_plainly_and_an_import_can_be_stopped(monkeypatch, library):
    doc = library / "冬雪 之下.md"
    doc.write_text("# 冬雪之下\n", encoding="utf-8")
    finished_module("dongxue-zhixia")  # imported before: a new import would overwrite it

    async def over_budget(file, module_id, *, console=None, **kw):
        console.print(f"[bold]提取[/] {Path(file).name}：0 页，8 字")
        raise RuntimeError("API 花费估计已达 $5.001，超过预算 $5")

    monkeypatch.setattr(importer, "run_import", over_budget)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = await open_import(app, pilot)
        box = screen.query_one("#drop", Input)
        box.focus()
        box.value = doc.as_uri()
        await pilot.press("enter")
        await until(pilot, lambda: screen.step == "confirm")
        assert not screen.query_one("#documents").display
        assert screen.query_one("#module-id", Input).value == "dongxue-zhixia"
        assert "覆盖" in shown(screen, "#id-note") and "warning" in screen.query_one("#id-note").classes
        screen.query_one("#module-id", Input).value = "zhuishuren 2"  # typed by hand: made into an id
        await pilot.pause()
        assert "modules/local/zhuishuren-2/" in shown(screen, "#id-note")
        field = screen.query_one("#module-id", Input)
        field.value = "example"  # a bundled module's name
        field.focus()
        await pilot.pause()
        assert "error" in screen.query_one("#id-note").classes
        await pilot.press("enter")
        await pilot.pause()
        assert screen.step == "confirm"  # refused
        field.value = ""  # empty: back to the name from the file
        await pilot.press("enter")
        await until(pilot, lambda: screen.step == "done")
        status = shown(screen, "#import-status")
        assert "导入没有完成" in status and "超过预算 $5" in status
        assert screen.query_one("#retry").display and not screen.query_one("#play").display

        async def gave_up(file, module_id, **kw):  # ran out of fix rounds: the module still has errors
            work = config.LOCAL_MODULES / module_id
            shutil.rmtree(work, ignore_errors=True)
            yaml = finished_module(module_id) / "module.yaml"
            yaml.write_text(yaml.read_text(encoding="utf-8").replace("start: square", "start: nowhere", 1), encoding="utf-8")
            return work

        monkeypatch.setattr(importer, "run_import", gave_up)
        await pilot.press("enter")  # 重新导入 has the focus
        await until(pilot, lambda: screen.step == "done" and "问题" in shown(screen, "#import-status"))
        assert "还有 3 个问题没修好" in shown(screen, "#import-status") and not screen.query_one("#play").display
        log = "\n".join(line.text for line in screen.query_one("#import-log", RichLog).lines)
        assert "起始地点 nowhere 不存在" in log

        started = asyncio.Event()

        async def endless(file, module_id, **kw):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(importer, "run_import", endless)
        await until(pilot, lambda: not screen.query_one("#retry").has_class("-active"))  # a button ignores presses for 0.2 s after one
        await pilot.press("enter")  # 重新导入 has the focus
        await until(pilot, started.is_set)
        assert screen.step == "run"
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, StopModal)
        await pilot.press("escape")  # Esc in the question keeps importing
        await pilot.pause()
        assert app.screen is screen and screen.step == "run"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.click("#halt")
        await until(pilot, lambda: screen.step == "done")
        assert "导入已停下" in shown(screen, "#import-status")
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, TitleScreen)
