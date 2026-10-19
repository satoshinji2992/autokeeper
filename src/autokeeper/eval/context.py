"""Context ledger: what progressive disclosure saves an agent that hosts the game from the
compiled Skill package (Claude Code or Codex through skills/MCP). The built-in engine's keeper
reads neither: it gets the public scene only (docs/harness.md).

Compares the standing context in two designs:

    full         the whole module (every reference file of the Skill package) in the prompt
    progressive  SKILL.md overview + the file of the location the party is in

Token counts use tiktoken's o200k_base as a model-neutral proxy; Chinese runs at
roughly one token per character in either case, so the ratio is what matters.
"""

from __future__ import annotations

from pathlib import Path

import tiktoken

from .. import skills
from ..module.compiler import ensure_compiled, skill_name
from ..module.schema import Module

_enc = tiktoken.get_encoding("o200k_base")


def tokens(text: str) -> int:
    return len(_enc.encode(text))


def ledger(module_dir: Path) -> dict:
    m = Module.load(module_dir)
    root = ensure_compiled(m, module_dir)
    skills.clear_cache()
    overview = tokens(skills.body(skill_name(m)))
    refs = sorted((root / "references").rglob("*.md"))
    full = overview + sum(tokens(p.read_text(encoding="utf-8")) for p in refs)
    per_location = {
        loc.id: tokens((root / "references" / "locations" / f"{loc.id}.md").read_text(encoding="utf-8")) for loc in m.locations
    }
    progressive = [overview + v for v in per_location.values()]
    avg = sum(progressive) / len(progressive)
    return {
        "module": m.id,
        "full": full,
        "overview": overview,
        "progressive_avg": round(avg),
        "progressive_max": max(progressive),
        "saving": round(1 - avg / full, 3),
        "reference_files": len(refs),
    }
