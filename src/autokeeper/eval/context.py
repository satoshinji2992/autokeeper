"""Context ledger: what progressive disclosure saves the keeper.

Compares the keeper's standing context in two designs:

    full         the whole module (every reference file of the Skill package) in the prompt
    progressive  SKILL.md overview + the file of the location the party is in

Token counts use tiktoken's o200k_base as a model-neutral proxy; Chinese runs at
roughly one token per character in either case, so the ratio is what matters.
"""

from __future__ import annotations

from pathlib import Path

import tiktoken

from .. import config, skills
from ..module.compiler import compile_module, skill_name
from ..module.schema import Module

_enc = tiktoken.get_encoding("o200k_base")


def tokens(text: str) -> int:
    return len(_enc.encode(text))


def ledger(module_dir: Path) -> dict:
    m = Module.load(module_dir)
    root = config.SKILLS / skill_name(m)
    if not (root / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
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
