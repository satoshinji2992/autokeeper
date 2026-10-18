"""Paths, role -> model routing, web search and the spending cap.

Every agent role picks its own backend and model. A preset sets all of them at once:

    claude    (default) local play on a Claude subscription: Opus 5.5 runs the game and
              builds modules, Haiku 5.5 plays the NPCs.
    deepseek  DeepSeek API (deepseek-flash, thinking on, effort by role). Needs
              DEEPSEEK_API_KEY.

Pick one with `autokeeper --preset deepseek`, AUTOKEEPER_PRESET, or `preset = "deepseek"`
in the config file. A mode moves the preset's thinking levels: 高级剧情 lets the roles that
tell the story think one level harder, 经济 lets every role think one level less (--mode,
AUTOKEEPER_MODE, `mode = "经济"`); roles given their own effort keep it. AUTOKEEPER_MODEL=ollama:qwen3:14b (any init_chat_model string; a bare
claude-* name means Claude Code) points every role at one model, e.g. a local server with
no internet. AUTOKEEPER_FALLBACK names a model to switch to when a role's own model fails
or the budget runs out. Single roles are overridden in autokeeper.toml or
~/.config/autokeeper/config.toml:

    preset = "deepseek"
    search = "auto"            # auto | tavily | ddgs | wikipedia | none
    budget_usd = 5             # stop metered API calls past this estimate
    mode = "标准"              # 经济 | 标准 | 高级剧情
    improv = "即兴"            # 严格: only what the module wrote; 即兴: the director fills the gaps

    [roles.all]                # applies to every role
    base_url = "http://localhost:8000/v1"
    fallback = "ollama:qwen3:14b"

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
    fallback: str = ""  # model to switch to when this one fails or the budget runs out


def route(model: str) -> tuple[str, str]:
    """The channel a model string goes through: a bare claude-* name is Claude Code,
    codex:<model> the Codex CLI, replay:<cassette> a recorded game, anything else an
    init_chat_model string
    (deepseek:, ollama:, openai:, ...)."""
    if model.startswith("claude:"):
        return "claude", model.removeprefix("claude:")
    if model.startswith("replay:"):  # a recorded game (harness/replay.py)
        return "replay", model.removeprefix("replay:")
    if model.startswith("codex:"):  # the Codex CLI; "codex:default" keeps the login's model
        return "codex", model.removeprefix("codex:")
    if ":" not in model and model.startswith("claude-"):
        return "claude", model
    return "langchain", model


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
    "director": OPUS,  # improvises where the module is silent (agents/director.py)
}


def _deepseek(effort: str | None) -> Role:
    return Role("langchain", "deepseek:deepseek-flash", effort)


PRESETS: dict[str, dict[str, Role]] = {
    "claude": DEFAULT_ROLES,
    "deepseek": {
        **{r: _deepseek("high") for r in ("keeper", "arbiter", "importer", "artist")},
        **{r: _deepseek("low") for r in ("vision", "npc", "intent", "investigator", "lore", "director")},
        # Summaries and recaps need no reasoning. With thinking on, half the summarizer's output
        # was thinking, and the recap's first word came after 1.6-3.8 s instead of 0.5 s.
        **{r: _deepseek(None) for r in ("summarizer", "chronicler")},
    },
}


IMPROV = ("严格", "即兴")
MODES = ("经济", "标准", "高级剧情")
STORY = ("keeper", "director", "npc", "investigator")  # the roles 高级剧情 lets think harder
# One step is one level the backend can tell apart. DeepSeek has no medium (it runs as
# high); Claude Code without an effort uses its own default, which is not "less".
LADDERS: dict[str, tuple[str | None, ...]] = {
    "deepseek": (None, "low", "high", "max"),
    "claude": ("low", "medium", "high", "max"),
    "": (None, "low", "medium", "high", "max"),
}


def shift(role: Role, steps: int) -> Role:
    """The role thinking `steps` levels harder (or less, when negative), within its backend."""
    if role.backend == "claude":
        ladder = LADDERS["claude"]
    elif role.model.startswith("deepseek:"):
        ladder = LADDERS["deepseek"]
    else:
        ladder = LADDERS[""]
    effort = "high" if role.effort == "medium" and "medium" not in ladder else role.effort
    if effort not in ladder:  # e.g. "minimal": leave a hand-picked level alone
        return role
    i = min(max(ladder.index(effort) + steps, 0), len(ladder) - 1)
    return replace(role, effort=ladder[i])


# Thinking it never gives up, even in 经济: without it the director's plans kept failing
# validation (12 DeepSeek turns: 5 calls after the opening, 1 plan that held).
KEEPS_THINKING = ("director",)


def moded(roles: dict[str, Role], mode: str) -> dict[str, Role]:
    if mode == "高级剧情":
        return {name: shift(r, 1) if name in STORY else r for name, r in roles.items()}
    if mode == "经济":
        less = {name: shift(r, -1) for name, r in roles.items()}
        return {name: r if r.effort or name not in KEEPS_THINKING else roles[name] for name, r in less.items()}
    return dict(roles)


@dataclass
class Config:
    roles: dict[str, Role] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    preset: str = "claude"
    module: str = ""
    theme: str = "amber"
    art_style: str = "picture"  # imagery.STYLES: picture (blocks + outlines), fine (sextants + outlines), photo, ascii
    search: str = "auto"
    budget_usd: float | None = None
    improv: str = "即兴"  # 严格: only what the module wrote; 即兴: the director fills the gaps
    mode: str = "标准"  # 经济 / 标准 / 高级剧情: the preset's thinking levels, one step down or up

    @property
    def improvising(self) -> bool:
        return self.improv == "即兴"

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
    mode = os.environ.get("AUTOKEEPER_MODE") or data.get("mode", "标准")
    if mode not in MODES:
        raise ValueError(f"未知的模式：{mode}（可选：{'、'.join(MODES)}）")
    cfg = Config(roles=moded(PRESETS[preset], mode), preset=preset, mode=mode)
    roles = data.get("roles", {})
    every = dict(roles.get("all", {}))
    if os.environ.get("AUTOKEEPER_MODEL"):
        backend, model = route(os.environ["AUTOKEEPER_MODEL"])
        every.update(backend=backend, model=model)
    if os.environ.get("AUTOKEEPER_FALLBACK"):
        every.update(fallback=os.environ["AUTOKEEPER_FALLBACK"])
    for name in set(DEFAULT_ROLES) | set(roles) - {"all"}:
        cfg.roles[name] = _role(_role(cfg.role(name), every), roles.get(name, {}))
    cfg.module = data.get("module", cfg.module)
    cfg.theme = data.get("theme", cfg.theme)
    cfg.art_style = data.get("art_style", cfg.art_style)
    cfg.search = os.environ.get("AUTOKEEPER_SEARCH") or data.get("search", cfg.search)
    budget = os.environ.get("AUTOKEEPER_BUDGET_USD") or data.get("budget_usd")
    cfg.budget_usd = float(budget) if budget not in (None, "") else None
    cfg.improv = os.environ.get("AUTOKEEPER_IMPROV") or data.get("improv", cfg.improv)
    if cfg.improv not in IMPROV:
        raise ValueError(f"未知的自由度：{cfg.improv}（可选：{'、'.join(IMPROV)}）")
    return cfg
