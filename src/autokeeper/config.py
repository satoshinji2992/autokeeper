"""Paths, role -> model routing, web search and the spending cap.

Every agent role picks its own backend and model. A preset sets all of them at once:

    claude    (default) local play on a Claude subscription: Opus 5.5 runs the game and
              builds modules, Haiku 5.5 plays the NPCs.
    deepseek  DeepSeek API (deepseek-flash, thinking on, effort by role). Needs
              DEEPSEEK_API_KEY.

Pick one with `autokeeper --preset deepseek`, AUTOKEEPER_PRESET, or `preset = "deepseek"`
in the config file. AUTOKEEPER_MODEL=ollama:qwen3:14b (any init_chat_model string) points
every role at one model, e.g. a local server with no internet. Single roles are
overridden in autokeeper.toml or ~/.config/autokeeper/config.toml:

    preset = "deepseek"
    search = "auto"            # auto | tavily | ddgs | wikipedia | none
    budget_usd = 5             # stop metered API calls past this estimate

    [roles.all]                # applies to every role
    base_url = "http://localhost:8000/v1"

    [roles.npc]
    backend = "langchain"      # claude | langchain | codex
    model = "deepseek:deepseek-flash"
    effort = "low"             # none | low | medium | high | max
    window = 8000              # characters of conversation the harness resends

Keys come from the environment; ~/.config/autokeeper/.env and ./.env are read too.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

ROOT = Path(os.environ.get("AUTOKEEPER_HOME", Path(__file__).resolve().parents[2]))
MODULES = ROOT / "modules"
LOCAL_MODULES = MODULES / "local"
IMPORT_INBOX = MODULES / "_import"
SKILLS = ROOT / "skills"
SAVES = ROOT / "saves"
SESSIONS = SAVES / "claude"  # cwd for Claude Code processes, keeps them out of other projects
EXAMPLE = Path(__file__).resolve().parent / "module" / "example"  # format sample for tests and the importer
USER_DIR = Path.home() / ".config" / "autokeeper"


@dataclass(frozen=True)
class Role:
    backend: str = "claude"
    model: str = "claude-opus-5-5"
    effort: str | None = "medium"
    base_url: str | None = None
    window: int = 0  # 0: the caller's default


OPUS = Role("claude", "claude-opus-5-5", "medium")
HAIKU = Role("claude", "claude-haiku-5-5", "medium")

DEFAULT_ROLES: dict[str, Role] = {
    "keeper": OPUS,  # narrates the game
    "arbiter": OPUS,  # rules on checks without seeing the player's wording
    "chronicler": OPUS,  # memory, recaps, replays
    "importer": OPUS,  # builds modules from PDFs, researches generated ones
    "vision": OPUS,  # reads sketches and module images
    "artist": OPUS,  # ASCII art
    "npc": HAIKU,  # every NPC gets its own session
    "intent": replace(HAIKU, effort="low"),  # turns player input into a neutral action
    "investigator": HAIKU,  # AI players for companions and autoplay
    "lore": replace(HAIKU, effort="low"),  # LightRAG entity extraction and lore answers
    "summarizer": replace(HAIKU, effort="low"),  # folds old conversation into summaries
}


def _deepseek(effort: str) -> Role:
    return Role("langchain", "deepseek:deepseek-flash", effort)


PRESETS: dict[str, dict[str, Role]] = {
    "claude": DEFAULT_ROLES,
    "deepseek": {
        **{r: _deepseek("high") for r in ("keeper", "arbiter", "importer", "artist")},
        **{r: _deepseek("low") for r in ("chronicler", "vision", "npc", "intent", "investigator", "lore", "summarizer")},
    },
}


@dataclass
class Config:
    roles: dict[str, Role] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    preset: str = "claude"
    module: str = ""
    theme: str = "amber"
    art_style: str = "picture"  # picture: chafa block characters; ascii: plain ASCII glyphs
    search: str = "auto"
    budget_usd: float | None = None

    def role(self, name: str) -> Role:
        return self.roles.get(name, DEFAULT_ROLES.get(name, OPUS))


def load_env() -> None:
    """Read keys from ~/.config/autokeeper/.env and ./.env; the real environment wins."""
    from dotenv import load_dotenv

    for f in (USER_DIR / ".env", ROOT / ".env"):
        if f.is_file():
            load_dotenv(f, override=False)


def _role(base: Role, spec: dict) -> Role:
    keys = {f.name for f in fields(Role)}
    changes = {k: v for k, v in spec.items() if k in keys}
    if changes.get("effort") in ("none", ""):
        changes["effort"] = None
    return replace(base, **changes)


def load(path: Path | None = None) -> Config:
    load_env()
    data: dict = {}
    for candidate in (USER_DIR / "config.toml", ROOT / "autokeeper.toml", path):
        if candidate and Path(candidate).is_file():
            for key, value in tomllib.loads(Path(candidate).read_text(encoding="utf-8")).items():
                if key == "roles":
                    for name, spec in value.items():
                        data.setdefault("roles", {}).setdefault(name, {}).update(spec)
                else:
                    data[key] = value
    preset = os.environ.get("AUTOKEEPER_PRESET") or data.get("preset", "claude")
    if preset not in PRESETS:
        raise ValueError(f"未知预设：{preset}（可选：{', '.join(PRESETS)}）")
    cfg = Config(roles=dict(PRESETS[preset]), preset=preset)
    roles = data.get("roles", {})
    every = dict(roles.get("all", {}))
    if os.environ.get("AUTOKEEPER_MODEL"):
        every.update(backend="langchain", model=os.environ["AUTOKEEPER_MODEL"])
    for name in set(DEFAULT_ROLES) | set(roles) - {"all"}:
        cfg.roles[name] = _role(_role(cfg.role(name), every), roles.get(name, {}))
    cfg.module = data.get("module", cfg.module)
    cfg.theme = data.get("theme", cfg.theme)
    cfg.art_style = data.get("art_style", cfg.art_style)
    cfg.search = os.environ.get("AUTOKEEPER_SEARCH") or data.get("search", cfg.search)
    budget = os.environ.get("AUTOKEEPER_BUDGET_USD") or data.get("budget_usd")
    cfg.budget_usd = float(budget) if budget not in (None, "") else None
    return cfg
