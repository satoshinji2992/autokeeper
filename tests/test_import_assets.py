from __future__ import annotations

import io
import json
import shutil
from types import SimpleNamespace

import pytest
import yaml
from PIL import Image
from rich.console import Console

from autokeeper import config, credits, paths
from autokeeper.harness.tools import ToolError, Workspace
from autokeeper.module import importer, package
from autokeeper.module.compiler import compile_module
from autokeeper.module.graph import audit
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE


async def test_original_handout_retains_pixels_text_and_provenance(tmp_path):
    work = tmp_path / "module"
    shutil.copytree(EXAMPLE, work)
    source = work / "source" / "map.png"
    source.parent.mkdir()
    (source.parent / "source.md").write_text("完整原文，包括未整理进摘要的细节。", encoding="utf-8")
    Image.new("RGB", (900, 1200), (15, 40, 70)).save(source)
    ws = Workspace(work, writable=("module.yaml", "art/*"))
    tool = importer.original_image_tool(ws)
    await tool.run("source/map.png", "map")
    m = Module.load(work)
    m.handouts[0].images = ["map"]
    m.save(work)
    root = compile_module(m, tmp_path / "skills", source=work / "source")
    assert (root / "assets/images/map.png").read_bytes() == source.read_bytes()
    assert credits.get(root / "assets/images/map.png")["original"] is True
    assert (root / "assets/source/source.md").read_text(encoding="utf-8") == "完整原文，包括未整理进摘要的细节。"
    assert "完整原始资料" in (root / "SKILL.md").read_text(encoding="utf-8")
    handout = (root / f"references/handouts/{m.handouts[0].id}.md").read_text(encoding="utf-8")
    assert m.handouts[0].text in handout and "../../assets/images/map.png" in handout
    assert not importer.validate(work)
    source.unlink()
    assert Module.load(work).pictures["map"] == str(work / "art/map.png")
    for path, name in (("../secret.png", "map"), ("art/map.png", "map"), ("source/map.png", "../escape")):
        with pytest.raises(ToolError):
            await tool.run(path, name)
    m.handouts[0].images = ["missing"]
    m.save(work)
    assert any("missing" in error for error in importer.validate(work))


async def test_art_batch_rejects_invalid_drawings_without_partial_writes(tmp_path):
    shutil.copytree(EXAMPLE, tmp_path / "module")
    ws = Workspace(tmp_path / "module", writable=("art/*",))
    tool = importer.artwork_tool(ws)
    with pytest.raises(ToolError):
        await tool.run(json.dumps({"new_scene": "/___\\", "too_wide": "x" * 57}))
    assert not (ws.root / "art/new_scene.txt").exists()
    await tool.run(json.dumps({"new_scene": "  /___\\\n  |___|"}))
    m = Module.load(ws.root)
    assert m.art["new_scene"] == "  /___\\\n  |___|"
    assert "new_scene" in m.art_colors and not importer.validate(ws.root)


def test_unknown_nested_fields_cannot_silently_disappear_during_import(tmp_path):
    work = tmp_path / "module"
    shutil.copytree(EXAMPLE, work)
    path = work / "clues.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["clues"][0]["sets"] = ["impossible_flag"]
    raw["pictures"] = {"map": "not a real file"}
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    issues = importer.validate(work)
    assert any("clues[0].sets" in error for error in issues)
    assert any("pictures" in error for error in issues)


def test_clue_requirements_must_reference_existing_clues_or_declared_flags():
    module = Module.load(EXAMPLE)
    clue = module.clues[0]
    clue.requires = [clue.id, "missing_clue", "flag:missing_flag"]
    errors = [issue.text for issue in audit(module) if issue.level == "error"]
    assert len(errors) == 3


async def test_an_import_leaves_the_module_package_and_its_notes_in_source(monkeypatch, tmp_path, skills_dir):
    work = tmp_path / "local" / "example"

    class Builder:  # writes what the importer's agent writes: one module.yaml, flat art/, notes.md
        async def run(self, brief):
            (work / "module.yaml").write_text(package.joined_text(EXAMPLE), encoding="utf-8")
            (work / "notes.md").write_text("要点", encoding="utf-8")
            (work / "art").mkdir(exist_ok=True)
            for f in (EXAMPLE / "art" / "ascii").iterdir():
                shutil.copy(f, work / "art" / f.name)
            return SimpleNamespace(steps=1, finished=True, answer="done")

        async def close(self):
            pass

    monkeypatch.setattr(importer, "builder", lambda *a: Builder())
    await importer.pipeline(work, brief="test", cfg=config.Config(), console=Console(file=io.StringIO()), index=False, photos=False, adapting=False)
    assert sorted(p.name for p in work.iterdir()) == ["art", "clues.yaml", "module.yaml", "people.yaml", "places.yaml", "source", "story.yaml"]
    assert sorted(p.name for p in (work / "art").iterdir()) == ["ascii"] and (work / "source" / "notes.md").is_file()
    assert Module.load(work).model_dump() == Module.load(EXAMPLE).model_dump()
    assert (skills_dir / "module-example" / "SKILL.md").is_file()


async def test_import_failure_keeps_the_draft_and_never_reports_compile_success(monkeypatch, tmp_path):
    class Builder:
        calls = 0
        closed = False

        async def run(self, brief):
            self.calls += 1
            return SimpleNamespace(steps=1, finished=True, answer="draft")

        async def close(self):
            self.closed = True

    builder = Builder()
    monkeypatch.setattr(importer, "builder", lambda *a: builder)
    monkeypatch.setattr(importer, "validate", lambda work: ["still broken"])
    output = io.StringIO()
    with pytest.raises(ValueError, match="未编译"):
        await importer.pipeline(tmp_path, brief="test", cfg=config.Config(), console=Console(file=output), index=False)
    assert builder.calls == 1 + importer.MAX_FIX_ROUNDS and builder.closed
    assert "完成" not in output.getvalue()


def test_pdf_embedded_images_are_extracted_once_with_all_page_references(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    image = Image.new("RGB", (200, 240), (31, 62, 93))
    for _ in range(2):
        page = doc.new_page(200, 240)
        obj = pdfium.PdfImage.new(doc)
        obj.set_bitmap(pdfium.PdfBitmap.from_pil(image))
        obj.set_matrix(pdfium.PdfMatrix(200, 0, 0, 240, 0, 0))
        page.insert_obj(obj)
        page.gen_content()
    pdf = tmp_path / "source.pdf"
    doc.save(pdf)
    doc.close()
    count, _ = importer.extract(pdf, tmp_path / "module", None)
    images = json.loads((tmp_path / "module/source/images/index.json").read_text(encoding="utf-8"))
    assert count == 2 and len(images) == 1 and images[0]["pages"] == [1, 2]
    with Image.open(tmp_path / "module" / images[0]["path"]) as extracted:
        assert extracted.size == image.size and extracted.tobytes() == image.tobytes()


async def test_an_import_ends_by_looking_for_photos_unless_offline(monkeypatch, tmp_path):
    """Scenes and faces get real photos like 《追书人》's; offline, or without chafa, nothing is fetched."""
    from autokeeper import imagery

    m = Module.load(paths.EXAMPLE)
    asked: list[list[tuple[str, str, str]]] = []

    async def fetch(cfg, folder, targets, **kwargs):
        asked.append(targets)
        return [targets[0][0]]

    monkeypatch.setattr(imagery, "fetch_folder", fetch)
    monkeypatch.setattr(imagery, "available", lambda: True)
    output = io.StringIO()
    done = await importer.find_photos(m, tmp_path, config.Config(search="auto"), Console(file=output))
    assert done and asked and {kind for _, _, kind in asked[0]} == {"scene", "portrait"}
    assert all(m.era in subject for _, subject, _ in asked[0])  # searched for the period, not today
    assert "找到 1/" in output.getvalue()
    assert await importer.find_photos(m, tmp_path, config.Config(search="none"), Console(file=io.StringIO())) == []
    monkeypatch.setattr(imagery, "available", lambda: False)
    assert await importer.find_photos(m, tmp_path, config.Config(search="auto"), Console(file=io.StringIO())) == []
    assert len(asked) == 1


async def test_another_importer_hands_the_drawing_to_the_art_model(monkeypatch, tmp_path):
    """Built by DeepSeek or Codex, a module still gets its pictures from the art model (Opus 5.5)."""
    from autokeeper.agents import artist
    from autokeeper.config import Role
    from autokeeper.harness.tools import Workspace

    work = tmp_path / "m"
    shutil.copytree(EXAMPLE, work)
    m = Module.load(work)
    face = next(n.portrait for n in m.npcs if n.portrait)
    deepseek = config.Config(roles={**config.DEFAULT_ROLES, "importer": Role("langchain", "deepseek:deepseek-flash", "high")})
    assert importer.drawn_by_artist(deepseek) and not importer.drawn_by_artist(config.Config())
    drawn = []

    async def draw(cfg, subject, *, portrait=False, on_reply=None):
        drawn.append((subject, portrait))
        if "坏" in subject:
            raise ValueError("twice invalid")
        return "/\\\n||", "..\nWW"

    monkeypatch.setattr(artist, "draw", draw)
    tool = importer.artist_tool(Workspace(work, writable=("art/*",)), deepseek)
    out = await tool.run(json.dumps({"tower_view": "雾里的钟楼", face: "一个驼背的老人", "broken": "坏图"}, ensure_ascii=False))
    assert "画好 2 张" in out and "broken（ValueError）" in out
    assert (work / "art/tower_view.txt").read_text(encoding="utf-8") == "/\\\n||\n"
    assert (work / f"art/{face}.color").read_text(encoding="utf-8") == "..\nWW\n"
    assert (f"一个驼背的老人（{m.era}，{m.setting}）", True) in drawn and ("雾里的钟楼（" + m.era + "，" + m.setting + "）", False) in drawn


async def test_photo_search_words_come_from_the_art_model(monkeypatch):
    from autokeeper import imagery

    roles = []

    class Stop(Exception):
        pass

    async def structured(cfg, role, *args, **kwargs):
        roles.append(role)
        raise Stop

    monkeypatch.setattr(imagery, "_structured", structured)
    with pytest.raises(Stop):
        await imagery.find_picture(config.Config(), None, "雾里的钟楼", "scene")
    assert roles == ["vision"]


async def test_web_pictures_come_from_tavily_and_only_into_folders_kept_on_this_machine(monkeypatch, tmp_path):
    import httpx

    from autokeeper import imagery

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tavily.com" and json.loads(request.content)["include_images"] is True
        return httpx.Response(200, json={"images": [
            {"url": "https://example.org/a.jpg", "description": None},  # a null description must not break the search
            "https://example.org/b.jpg", {"url": None}, {"url": "ftp://example.org/c.jpg", "description": "x"},
        ]})

    monkeypatch.setenv("TAVILY_API_KEY", "test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        found = await imagery.web_images(client, "portrait old man 1920s")
    assert [c.thumb for c in found] == ["https://example.org/a.jpg", "https://example.org/b.jpg"]
    assert all(c.license == imagery.WEB_LICENCE for c in found)
    monkeypatch.setattr(paths, "LOCAL_MODULES", tmp_path / "modules" / "local")
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    assert imagery.private(tmp_path / "modules" / "local" / "x" / "art") and imagery.private(tmp_path / "saves" / "games" / "art" / "y")
    assert not imagery.private(tmp_path / "modules" / "zhuishuren" / "art")  # the bundled module keeps licensed photos only


def test_an_import_on_another_vendor_draws_with_that_vendors_strongest_model(monkeypatch):
    """A Codex import draws and reads pictures on GPT-6-Astra, not on the game's Claude."""
    monkeypatch.setattr(config, "load", lambda *a, **k: config.Config())
    codex = importer.import_config("codex:default")
    assert (codex.role("artist").backend, codex.role("artist").model) == ("codex", "gpt-6-astra")
    assert codex.role("vision").model == "gpt-6-astra" and importer.drawn_by_artist(codex)
    chosen = importer.import_config("codex:default", "claude-opus-5-5")  # a reading model chosen by hand stays
    assert chosen.role("vision").model == "claude-opus-5-5" and chosen.role("artist").model == "gpt-6-astra"
    assert not importer.drawn_by_artist(config.Config())  # Opus builds and draws its own
    deepseek = importer.import_config("deepseek:deepseek-flash")
    assert deepseek.role("artist").model == "deepseek:deepseek-v4-pro" and deepseek.role("vision").model == "deepseek:deepseek-flash"


async def test_an_interrupted_import_goes_on_from_its_notes_and_draft(monkeypatch, tmp_path, skills_dir):
    """Run again on the same source: no second extraction, and the agent is told to take up its
    own notes.md and draft instead of reading the whole text again (importer.RESUME)."""
    work = tmp_path / "local" / "example"
    briefs: list[str] = []
    extracted: list[int] = []

    class Crashing:
        async def run(self, brief):
            briefs.append(brief)
            (work / "notes.md").write_text("读到第 3 页：钟楼、守钟人", encoding="utf-8")
            raise RuntimeError("连接断了")

        async def close(self):
            pass

    class Finishing(Crashing):
        async def run(self, brief):
            briefs.append(brief)
            (work / "module.yaml").write_text(package.joined_text(EXAMPLE), encoding="utf-8")
            (work / "art").mkdir(exist_ok=True)
            for f in (EXAMPLE / "art" / "ascii").iterdir():
                shutil.copy(f, work / "art" / f.name)
            return SimpleNamespace(steps=1, finished=True, answer="done")

    async def prepare(console):
        extracted.append(1)
        (work / "source").mkdir(parents=True, exist_ok=True)
        (work / "source" / "source.md").write_text("原文", encoding="utf-8")

    quiet = {"cfg": config.Config(), "console": Console(file=io.StringIO()), "index": False, "photos": False, "adapting": False}
    monkeypatch.setattr(importer, "builder", lambda *a: Crashing())
    with pytest.raises(RuntimeError):
        await importer.pipeline(work, brief="整理模组", prepare=prepare, key="import:example:abc", **quiet)
    assert importer.progress(work)["stage"] == "prepared" and extracted == [1]
    monkeypatch.setattr(importer, "builder", lambda *a: Finishing())
    await importer.pipeline(work, brief="整理模组", prepare=prepare, key="import:example:abc", **quiet)
    assert extracted == [1]  # the text was not extracted again
    assert "接着上次做" not in briefs[0] and "接着上次做" in briefs[1]
    assert importer.progress(work)["stage"] == "compiled" and (work / "source" / "notes.md").is_file()


async def test_an_import_of_another_file_starts_afresh(monkeypatch, tmp_path):
    work = tmp_path / "local" / "example"
    work.mkdir(parents=True)
    importer.mark(work, "import:example:abc", "built")
    assert importer.reached(work, "import:example:abc") == 2
    assert importer.reached(work, "import:example:another") == 0 and importer.reached(work, "") == 0
