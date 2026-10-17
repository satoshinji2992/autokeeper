"""Reading Agent Skills from disk.

Claude Code, Codex and deepagents load these folders natively. In standalone play the
engine itself decides what each agent needs (the keeper gets the module overview
up front and a location file only when the party walks in), so it just reads the
same files; metadata and the <available_skills> index come from skills-ref.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import skills_ref

from .config import SKILLS


def path(name: str) -> Path:
    return SKILLS / name


@lru_cache(maxsize=64)
def body(name: str) -> str:
    """SKILL.md without its YAML frontmatter."""
    text = (path(name) / "SKILL.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


def reference(name: str, rel: str) -> str:
    return (path(name) / rel).read_text(encoding="utf-8").strip()


def wrap(name: str, text: str | None = None) -> str:
    return f'<skill name="{name}">\n{text if text is not None else body(name)}\n</skill>'


def available(names: list[str] | None = None) -> str:
    """Level-1 metadata of the installed skills, in the format Anthropic recommends."""
    dirs = [path(n) for n in names] if names else sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").exists())
    return skills_ref.to_prompt(dirs)


def clear_cache() -> None:
    body.cache_clear()
