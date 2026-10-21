"""Reading Agent Skills from disk.

The engine decides what each agent needs and reads the folders itself (the arbiter gets
coc7-core, the keeper keeper-craft, a storyteller NPC pingshu-voice, the artist ascii-art);
metadata and the <available_skills> index come from skills-ref.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import skills_ref

from . import paths


def path(name: str) -> Path:
    return paths.skill(name)  # read at call time, so a relocated skills folder is honoured everywhere


def body(name: str) -> str:
    """SKILL.md without its YAML frontmatter."""
    return _body(path(name) / "SKILL.md")


@lru_cache(maxsize=64)
def _body(file: Path) -> str:
    text = file.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


def reference(name: str, rel: str) -> str:
    return (path(name) / rel).read_text(encoding="utf-8").strip()


def wrap(name: str, text: str | None = None) -> str:
    return f'<skill name="{name}">\n{text if text is not None else body(name)}\n</skill>'


def available(names: list[str] | None = None) -> str:
    """Level-1 metadata of the installed skills, in the format Anthropic recommends."""
    dirs = [path(n) for n in names] if names else paths.skill_folders()
    return skills_ref.to_prompt(dirs)


def clear_cache() -> None:
    _body.cache_clear()
