from __future__ import annotations

from autokeeper import mcp_server as srv
from autokeeper import paths
from autokeeper.module.importer import _pages, validate
from autokeeper.paths import EXAMPLE

from .conftest import needs_node


def test_import_validation_accepts_the_format_example():
    assert validate(EXAMPLE) == []
    assert _pages("8-10,12", 19) == [8, 9, 10, 12]


@needs_node
async def test_mcp_tools_play_a_short_scene(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "SKILLS", tmp_path / "skills")
    monkeypatch.setattr(srv, "_dirs", lambda: {"example": EXAMPLE})
    started = await srv.new_game("example", "zhouheng", slot="t")
    assert started["location"]["id"] == "square" and started["art"]
    check = await srv.skill_check("侦查", clues_on_success=["c_notice"], minutes=30)
    assert check["checks"] and check["time"] == "D1 14:30"
    moved = await srv.move("tower")
    assert moved["arrived"] == "tower" and "齿轮间" in moved["read_aloud"]
    assert srv.scene()["location"]["id"] == "tower"
    found = await srv.lookup("老守钟人")
    assert any(h["id"] == "warden" for h in found["graph"])
    assert paths.save("t").exists()
    loaded = await srv.load_game("t")
    assert loaded["location"]["id"] == "tower"
    await srv.S.game.close()
    await srv.S.game.cast.close()
