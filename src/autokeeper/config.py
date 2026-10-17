"""Paths and role -> model routing.

Every agent role picks its own backend and model. Defaults follow the plan for
local play on a Claude subscription: Opus 5.5 (medium effort) runs the game and
builds modules, Haiku 5.5 (medium) plays the NPCs. Override in autokeeper.toml:

    [roles.npc]
    backend = "claude"          # claude | langchain | codex
    model = "claude-haiku-5-5"
    effort = "medium"
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(os.environ.get("AUTOKEEPER_HOME", Path(__file__).resolve().parents[2]))
MODULES = ROOT / "modules"
LOCAL_MODULES = MODULES / "local"
IMPORT_INBOX = MODULES / "_import"
SKILLS = ROOT / "skills"
SAVES = ROOT / "saves"
SESSIONS = SAVES / "claude"  # cwd for Claude sessions, keeps them out of other projects
EXAMPLE = Path(__file__).resolve().parent / "module" / "example"  # format sample for tests and the importer


@dataclass(frozen=True)
class Role:
    backend: str = "claude"
    model: str = "claude-opus-5-5"
    effort: str | None = "medium"


OPUS = Role("claude", "claude-opus-5-5", "medium")
HAIKU = Role("claude", "claude-haiku-5-5", "medium")

DEFAULT_ROLES: dict[str, Role] = {
    "keeper": OPUS,  # narrates the game
    "arbiter": OPUS,  # rules on checks without seeing the player's wording
    "chronicler": OPUS,  # memory, recaps, replays
    "importer": OPUS,  # builds modules from PDFs
    "vision": OPUS,  # reads sketches and module images
    "artist": OPUS,  # ASCII art
    "npc": HAIKU,  # every NPC gets its own session
    "intent": replace(HAIKU, effort="low"),  # turns player input into a neutral action
    "investigator": HAIKU,  # AI players for companions and autoplay
}


@dataclass
class Config:
    roles: dict[str, Role] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    module: str = ""
    theme: str = "amber"

    def role(self, name: str) -> Role:
        return self.roles.get(name, DEFAULT_ROLES.get(name, OPUS))


def load(path: Path | None = None) -> Config:
    cfg = Config()
    for candidate in [path, ROOT / "autokeeper.toml", Path.home() / ".config" / "autokeeper" / "config.toml"]:
        if candidate and Path(candidate).exists():
            data = tomllib.loads(Path(candidate).read_text(encoding="utf-8"))
            for name, spec in data.get("roles", {}).items():
                base = cfg.role(name)
                cfg.roles[name] = Role(
                    spec.get("backend", base.backend), spec.get("model", base.model), spec.get("effort", base.effort)
                )
            cfg.module = data.get("module", cfg.module)
            cfg.theme = data.get("theme", cfg.theme)
            break
    return cfg
