from __future__ import annotations

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
