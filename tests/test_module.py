from __future__ import annotations

import os
import subprocess
import sys

import skills_ref

from autokeeper.config import EXAMPLE
from autokeeper.module import graph
from autokeeper.module.compiler import compile_module
from autokeeper.module.schema import Module


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
    from autokeeper import config

    m = Module.load(config.MODULES / "zhuishuren")
    assert not [i for i in graph.audit(m) if i.level == "error"]
    assert len(m.locations) >= 8 and m.endings and m.pregens
    assert all(m.art.get(loc.art) or m.pictures.get(loc.art) for loc in m.locations if loc.art)
