from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import skills_ref
from PIL import Image

from autokeeper import credits, paths
from autokeeper.module import graph, package
from autokeeper.module.compiler import compile_module, ensure_compiled
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE


def test_example_module_loads_and_is_consistent():
    m = Module.load(EXAMPLE)
    assert m.id == "example" and len(m.locations) == 3 and len(m.pregens) == 1
    assert {"square", "warden"} <= set(m.art) and "square" in m.art_colors
    issues = graph.audit(m)
    assert not [i for i in issues if i.level == "error"], issues
    assert graph.reachable_locations(m) == {loc.id for loc in m.locations}
    assert graph.build(m).nodes["c_gears"]["kind"] == "clue"


def test_graph_search_brings_neighbours():
    m = Module.load(EXAMPLE)
    hits = {h["id"]: h for h in graph.search(m, "老守钟人知道什么")}
    assert hits["warden"]["via"] == "命中"
    assert "c_diary" in hits and "tower" in hits


def test_compile_skill_package(tmp_path):
    m = Module.load(EXAMPLE)
    root = compile_module(m, tmp_path)
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: module-example\n")
    assert "齿轮间" in (root / "references" / "locations" / "tower.md").read_text(encoding="utf-8")
    assert (root / "assets" / "ascii" / "square.txt").exists()
    assert len(skill.splitlines()) < 500
    assert skills_ref.validate(root) == []


def test_a_stale_skill_package_is_compiled_again(tmp_path):
    folder = tmp_path / "example"
    shutil.copytree(EXAMPLE, folder)
    root = ensure_compiled(Module.load(folder), folder, tmp_path / "skills")
    (root / "assets" / "leftover.txt").write_text("from an older compile", encoding="utf-8")
    assert ensure_compiled(Module.load(folder), folder, tmp_path / "skills") == root
    assert (root / "assets" / "leftover.txt").exists()  # unchanged: kept as it is
    text = (folder / "places.yaml").read_text(encoding="utf-8")
    (folder / "places.yaml").write_text(text.replace("齿轮间", "齿轮室", 1), encoding="utf-8")
    ensure_compiled(Module.load(folder), folder, tmp_path / "skills")
    assert not (root / "assets" / "leftover.txt").exists()  # changed: compiled from scratch
    assert "齿轮室" in "".join(f.read_text(encoding="utf-8") for f in root.rglob("*.md"))


def _content(m: Module) -> dict:
    return {"data": m.model_dump(mode="json"), "art": m.art, "colors": m.art_colors,
            "pictures": {key: Path(file).read_bytes() for key, file in m.pictures.items()}}


def test_the_split_format_round_trips(tmp_path):
    for folder in [EXAMPLE, *sorted(p.parent for p in paths.MODULES.glob("*/module.yaml"))]:
        m = Module.load(folder)
        m.save(tmp_path / m.id)
        again = Module.load(tmp_path / m.id)
        assert again.model_dump(mode="json") == m.model_dump(mode="json") and (again.art, again.art_colors) == (m.art, m.art_colors)
        assert {p.name for p in (tmp_path / m.id).glob("*.yaml")} <= {"module.yaml", *package.PARTS}


def test_a_module_in_one_file_still_loads(tmp_path):
    folder = tmp_path / "small"
    (folder / "art").mkdir(parents=True)
    (folder / "module.yaml").write_text(package.joined_text(EXAMPLE), encoding="utf-8")
    for f in (EXAMPLE / "art" / "ascii").iterdir():
        shutil.copy(f, folder / "art" / f.name)  # a flat art/ folder, as written by hand
    assert _content(Module.load(folder)) == _content(Module.load(EXAMPLE))
    assert Module.load(folder / "module.yaml").model_dump() == Module.load(EXAMPLE).model_dump()


def test_a_draft_is_filed_into_the_package_layout(tmp_path):
    """What the importer's agent leaves (one module.yaml, a flat art/ folder, notes.md and its
    reference copy) becomes the package, with the same content."""
    draft = tmp_path / "draft"
    (draft / "art").mkdir(parents=True)
    (draft / "_example").mkdir()
    (draft / "module.yaml").write_text(package.joined_text(EXAMPLE), encoding="utf-8")
    (draft / "notes.md").write_text("要点", encoding="utf-8")
    for f in (EXAMPLE / "art" / "ascii").iterdir():
        shutil.copy(f, draft / "art" / f.name)
    Image.new("RGB", (64, 48), (90, 60, 30)).save(draft / "art" / "square.jpg")  # a place
    Image.new("RGB", (48, 64), (90, 60, 30)).save(draft / "art" / "warden.jpg")  # a person
    Image.new("RGB", (48, 64), (90, 60, 30)).save(draft / "art" / "letter.png")  # anything else
    credits.put(draft / "art" / "letter.png", {"source": "source/images/p3.png", "original": True})
    before = _content(Module.load(draft))
    package.normalize(draft)
    assert _content(Module.load(draft)) == before
    assert sorted(p.name for p in draft.iterdir()) == ["art", "clues.yaml", "module.yaml", "people.yaml", "places.yaml", "source", "story.yaml"]
    assert sorted(p.relative_to(draft / "art").as_posix() for p in (draft / "art").rglob("*") if p.is_file() and p.parent.name != "ascii") == [
        "credits.yaml", "items/letter.png", "people/warden.jpg", "places/square.jpg"]
    assert credits.get(draft / "art" / "items" / "letter.png")["original"] is True
    assert (draft / "source" / "notes.md").read_text(encoding="utf-8") == "要点"


def test_the_bundled_modules_kept_their_content_when_split(tmp_path):
    """Against the single-file modules of 0.0.1 (commit 9589c4d), where git history is here; the
    important NPCs and the looks were added by hand after the split."""
    for folder in sorted(p.parent for p in paths.MODULES.glob("*/module.yaml")):
        rel = folder.relative_to(paths.ROOT).as_posix()
        old = subprocess.run(["git", "show", f"9589c4d:{rel}/module.yaml"], cwd=paths.ROOT, capture_output=True, text=True, encoding="utf-8")
        if old.returncode:
            pytest.skip("no git history of 0.0.1 here")
        (tmp_path / folder.name).mkdir()
        (tmp_path / folder.name / "module.yaml").write_text(old.stdout, encoding="utf-8")
        shutil.copytree(folder / "plugins", tmp_path / folder.name / "plugins")
        then, now = Module.load(tmp_path / folder.name), Module.load(folder)
        for old, new in zip([*then.npcs, *then.locations], [*now.npcs, *now.locations], strict=True):  # marked by hand since
            if hasattr(old, "important"):
                old.important = new.important
            old.looks = new.looks
            if folder.name == "zhuishuren" and old.id == "thomas":
                # The original temperament and investigation goal remain; the added
                # guidance makes his existing commission an active cooperation motive.
                assert new.personality.startswith(old.personality + "。")
                assert new.goal.startswith("协助调查员" + old.goal + "；")
                assert "力所能及的帮助" in new.personality and "履行承诺" in new.goal
                old.personality, old.goal = new.personality, new.goal
        # What compile time adds since (module/adapt.py): the situation layer, the opening performance,
        # clues for a conclusion short of three. Added, never changed: the original clues stay as they were.
        for name in ("relations", "agendas", "foreshadow", "ties", "mechanics", "history", "prelude"):
            setattr(then, name, getattr(now, name))
        original = {c.id for c in then.clues}
        now = now.model_copy(update={"clues": [c for c in now.clues if c.id in original]})
        if folder.name == "zhuishuren":  # 未解之谜 had a sentence for a condition, which never fires
            ending = next(e for e in then.endings if e.id == "unsolved")
            assert ending.condition.startswith("以上都没有发生")
            ending.condition = "flag:douglas_escaped"
        assert then.model_dump(mode="json") == now.model_dump(mode="json")
        assert {k: p.fingerprint for k, p in then.extensions.items()} == {k: p.fingerprint for k, p in now.extensions.items()}


def test_custom_kg_for_lightrag_has_unique_names_and_no_self_loops():
    from autokeeper.knowledge import custom_kg

    kg = custom_kg(Module.load(EXAMPLE))
    names = [e["entity_name"] for e in kg["entities"]]
    assert len(names) == len(set(names))
    assert "守钟人的日记（手稿）" in names  # the handout shares its clue's title
    assert all(r["src_id"] != r["tgt_id"] for r in kg["relationships"])
    assert {"src_id", "tgt_id", "description", "keywords", "source_id"} <= set(kg["relationships"][0])


def test_cli_prints_chinese_through_a_non_utf8_pipe():
    """Windows pipes use the ANSI code page; the CLI must not crash on Chinese output."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    r = subprocess.run([sys.executable, "-m", "autokeeper", "check", str(EXAMPLE)], env=env, capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "通过" in r.stdout.decode("utf-8")


def test_the_bundled_paper_chase_is_playable():

    m = Module.load(paths.MODULES / "zhuishuren")
    assert not [i for i in graph.audit(m) if i.level == "error"]
    assert len(m.locations) >= 8 and m.endings and m.pregens
    assert all(m.art.get(loc.art) or m.pictures.get(loc.art) for loc in m.locations if loc.art)
