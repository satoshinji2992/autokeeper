"""Role -> model routing, web search and the spending cap. Locations on disk are in paths.py.

Every agent role picks its own backend and model. A preset sets all of them at once:

    claude    (default) local play on a Claude subscription: Sonnet 5.5 tells the story,
              Opus 5.5 rules, directs and builds modules, Haiku 5.5 plays the NPCs.
    deepseek  DeepSeek API (deepseek-flash, thinking on, effort by role; ASCII art on
              deepseek-v4-pro, which cannot read images). Needs DEEPSEEK_API_KEY.

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
    imagegen = true            # pictures from Codex's image tool, when codex is installed and logged in
    showcase = true            # 大场景: a big picture over the screen at the turning points of the story
    minds = 2                  # important NPCs' backstage decisions a turn (twice that on a long skip); 0: only their plans
    tips = false               # no first-time hints in the story (also switched in the guide, F1)

    [roles.all]                # applies to every role
    base_url = "http://localhost:8000/v1"
    fallback = "ollama:qwen3:14b"

    [roles.npc]
    backend = "langchain"      # claude | langchain | codex
    model = "deepseek:deepseek-flash"
    effort = "low"             # none | low | medium | high | max
    window = 8000              # characters of conversation the harness resends

    [roles.world]              # optional; otherwise the resolved arbiter route, thinking low
    backend = "langchain"
    model = "deepseek:deepseek-flash"
    effort = "low"             # NPC plans and world consequences; separate from NPC dialogue

Keys come from the environment; ~/.config/autokeeper/.env and ./.env are read too.
"""

from __future__ import annotations

import contextlib
import json
import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from . import paths


@dataclass(frozen=True)
class Role:
    backend: str = "claude"
    model: str = "claude-opus-5-5"
    effort: str | None = "medium"
    base_url: str | None = None
    window: int = 0  # 0: the caller's default
    fallback: str = ""  # model to switch to when this one fails or the budget runs out
    max_input_chars: int = 0  # optional total text/schema budget; 0 means no configured hard limit


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
SONNET = Role("claude", "claude-sonnet-5-5", "medium")
HAIKU = Role("claude", "claude-haiku-5-5", "medium")

DEFAULT_ROLES: dict[str, Role] = {
    # Narrates the game. Against Opus on the same scripted turns (docs/eval.md): as few stock
    # phrases, closer to the length asked for, half the latency, under half the price.
    "keeper": SONNET,
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
    "card": OPUS,  # makes an investigator out of a public figure (agents/card.py)
}

# Optional roles inherit the effective route, including local/global overrides. Leaving
# them out of presets keeps older configurations and custom arbiter routes compatible.
ROLE_PARENTS = {"world": "arbiter", "plan": "keeper"}
# What an inherited role changes of its parent's route. Important NPCs' own decisions think
# low, not at the arbiter's high (the user's call, 2026-10-20): at high one took 6.4 s and they
# were four tenths of a replayed session's spend. A configured effort still wins.
# The keeper's plan (agents/cast.Cast.plan) is on the player's wait, before any roll: the keeper's
# model, thinking low (DeepSeek's keeper thinks high, which the telling can afford, not this).
ROLE_EFFORT = {"world": "low", "plan": "low"}


def _deepseek(effort: str | None) -> Role:
    return Role("langchain", "deepseek:deepseek-flash", effort)


# Pictures and how they look (drawing and colouring ASCII art, reading sketches and module
# images, picking photos) go to the strongest model of the vendor a game is on: Opus on Claude,
# V4 Pro on DeepSeek, GPT-6-Astra on Codex. Always the vendor's own: a DeepSeek game never
# calls Claude.
ART = ("artist", "vision")
TOP = {"claude": "claude-opus-5-5", "deepseek": "deepseek:deepseek-v4-pro", "codex": "gpt-6-astra"}
# What reads pictures needs eyes: V4 Pro has none, so DeepSeek's reader stays on flash.
SEEING = {**TOP, "deepseek": "deepseek:deepseek-flash"}


def art_role(role: Role, *, seeing: bool = False) -> Role:
    """The same vendor's strongest model for this role (`seeing`: the strongest that reads
    images), its thinking level kept; a vendor without one here (a local model) as it is."""
    top = SEEING if seeing else TOP
    if role.backend == "claude":
        return replace(role, model=top["claude"])
    if role.backend == "codex":
        return replace(role, model=top["codex"])
    if role.model.startswith("deepseek:"):
        return replace(role, model=top["deepseek"])
    return role


def _top_art(roles: dict[str, Role]) -> dict[str, Role]:
    return roles | {"artist": art_role(roles["artist"]), "vision": art_role(roles["vision"], seeing=True)}


PRESETS: dict[str, dict[str, Role]] = {
    "claude": DEFAULT_ROLES,
    "codex": _top_art({r: Role("codex", "default", None) for r in DEFAULT_ROLES}),
    "deepseek": _top_art({
        **{r: _deepseek("high") for r in ("keeper", "arbiter", "importer")},
        **{r: _deepseek("low") for r in ("vision", "npc", "intent", "investigator", "lore", "director", "card")},
        # Summaries and recaps need no reasoning. With thinking on, half the summarizer's output
        # was thinking, and the recap's first word came after 1.6-3.8 s instead of 0.5 s.
        # The artist neither: V4 Pro thinking over an ASCII drawing ran past 5 minutes at low and
        # high alike (in a 40-turn game all 11 drawings hit the 90 s limit, none made); without
        # thinking one took 8 s.
        **{r: _deepseek(None) for r in ("summarizer", "chronicler", "artist")},
    }),
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
    art_style: str = "photo"  # imagery.STYLES: photo (plain blocks, the one players read best), picture, fine, ascii
    plugins: bool = True
    search: str = "auto"
    budget_usd: float | None = None
    visual_budget_usd: float = 1.0  # separate estimated allowance; jobs already in flight may finish
    visual_limit: int = 24  # illustration attempts per save, including unmetered backends
    imagegen: bool = True  # pictures from Codex's image tool where the story changes a place or a person (imagegen.py)
    showcase: bool = True  # 大场景: one big picture over the screen at the story's turning points (engine/showcase.py)
    minds: int = 2  # important NPCs' backstage decisions a player turn; a long skip gets twice (world/scheduler.py)
    improv: str = "即兴"  # 严格: only what the module wrote; 即兴: the director fills the gaps
    mode: str = "标准"  # 经济 / 标准 / 高级剧情: the preset's thinking levels, one step down or up
    tips: bool = True  # first-time hints for new players (tui/widgets/tips.py)
    animations: bool = True
    dice: str = "bone"  # tui/drawing/dice.py SKINS

    @property
    def improvising(self) -> bool:
        return self.improv == "即兴"

    def role(self, name: str) -> Role:
        if name not in self.roles and name in ROLE_PARENTS:
            parent = self.role(ROLE_PARENTS[name])
            return replace(parent, effort=ROLE_EFFORT.get(name, parent.effort))
        return self.roles.get(name, DEFAULT_ROLES.get(name, OPUS))

    @property
    def role_names(self) -> set[str]:
        """Configured and inherited roles, for route inspection without creating overrides."""
        return set(self.roles) | set(ROLE_PARENTS)


def load_env() -> None:
    """Read keys from ~/.config/autokeeper/.env and ./.env; the real environment wins."""
    from dotenv import load_dotenv

    for f in (paths.USER_DIR / ".env", paths.ROOT / ".env"):
        if f.is_file():
            load_dotenv(f, override=False)


def save_key(name: str, value: str) -> Path:
    """Put NAME=value in ~/.config/autokeeper/.env (the file load_env reads first), in place of
    an older line for the same name; the file is readable by its owner only. The running process
    gets the key too."""
    paths.USER_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.USER_DIR / ".env"
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    lines = [line for line in lines if line.split("=", 1)[0].strip().removeprefix("export ").strip() != name]
    lines.append(f"{name}={value}")
    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):  # Windows keeps its own permissions
        temporary.chmod(0o600)
    temporary.replace(target)
    os.environ[name] = value
    return target


CLAUDE_EXTRA = ("用 Claude 要先装上它的组件：uv tool install --force \"autokeeper[claude]\"（pipx 用 pipx inject autokeeper claude-agent-sdk，"
                "源码目录用 uv sync --extra claude）")


def claude_ready() -> bool:
    """Whether the Claude backend's package is installed: an extra, not part of the game."""
    import importlib.util

    return importlib.util.find_spec("claude_agent_sdk") is not None


def remove_key(name: str) -> bool:
    """Take NAME out of ~/.config/autokeeper/.env (and out of this process, when the value came
    from there). True when the file had it."""
    target = paths.USER_DIR / ".env"
    if env_file(name) != target:
        return False
    lines = target.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if line.split("=", 1)[0].strip().removeprefix("export ").strip() != name]
    temporary = target.with_suffix(".tmp")
    temporary.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    with contextlib.suppress(OSError):
        temporary.chmod(0o600)
    temporary.replace(target)
    os.environ.pop(name, None)
    return True


def env_file(name: str) -> Path | None:
    """The .env file that gave this process NAME, or None when it came from the shell (or is unset)."""
    from dotenv import dotenv_values

    value = os.environ.get(name)
    for f in (paths.USER_DIR / ".env", paths.ROOT / ".env"):
        if value is not None and f.is_file() and dotenv_values(f).get(name) == value:
            return f
    return None


def preset_overrides(preset: str) -> list[str]:
    """What keeps roles off `preset` once it is the saved choice (as `autokeeper login` and the
    settings save it), in words for the player. Empty when every role follows it."""
    try:
        cfg = load()
    except ValueError as exc:
        return [str(exc)]
    off = [name for name, r in PRESETS[preset].items() if (cfg.role(name).backend, cfg.role(name).model) != (r.backend, r.model)]
    if not off:
        return []

    def where(name: str) -> str:
        f = env_file(name)
        return f"{f} 里写着 " if f else "终端里设置了环境变量 "

    reasons = []
    if (value := os.environ.get("AUTOKEEPER_PRESET")) and value != preset:
        reasons.append(f"{where('AUTOKEEPER_PRESET')}AUTOKEEPER_PRESET={value}，它优先于这里的选择")
    if value := os.environ.get("AUTOKEEPER_MODEL"):
        reasons.append(f"{where('AUTOKEEPER_MODEL')}AUTOKEEPER_MODEL={value}，所有角色都改用这个模型")
    for f in (paths.USER_DIR / "config.toml", paths.ROOT / "autokeeper.toml"):
        try:
            roles = tomllib.loads(f.read_text(encoding="utf-8")).get("roles", {})
        except (OSError, tomllib.TOMLDecodeError):
            continue
        named = [n for n, spec in roles.items() if isinstance(spec, dict) and {"model", "backend"} & set(spec)]
        if named:
            reasons.append(f"{f} 里的 {'、'.join(f'[roles.{n}]' for n in named)} 指定了别的模型")
    return reasons or [f"{'、'.join(off)} 没有用 {preset}"]


def ui_settings() -> dict:
    """UI choices persist separately; advanced TOML and secrets are never rewritten."""
    try:
        data = json.loads((paths.USER_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    choices = {"preset": PRESETS, "mode": MODES, "improv": IMPROV,
               "art_style": ("picture", "fine", "photo", "ascii"),
               "theme": ("amber", "phosphor", "lavender", "blood"),
               "dice": ("bone", "obsidian", "blood", "brass", "eldritch", "phosphor"),
               "search": ("auto", "tavily", "ddgs", "wikipedia", "none")}
    for key, values in choices.items():
        if isinstance(data.get(key), str) and data[key] in values:
            out[key] = data[key]
    for key in ("tips", "animations", "plugins", "imagegen", "showcase"):
        if type(data.get(key)) is bool:
            out[key] = data[key]
    for key in ("model", "fallback"):
        if isinstance(data.get(key), str) and len(data[key]) <= 512:
            out[key] = data[key].strip()
    for key in ("visual_limit", "visual_budget_usd"):
        value = data.get(key)
        if type(value) in (float, int) and 0 <= value <= 10000:
            out[key] = int(value) if key == "visual_limit" else float(value)
    return out


def save_ui_settings(changes: dict) -> None:
    paths.USER_DIR.mkdir(parents=True, exist_ok=True)
    target = paths.USER_DIR / "settings.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ui_settings() | changes, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _role(base: Role, spec: dict) -> Role:
    keys = {f.name for f in fields(Role)}
    changes = {k: v for k, v in spec.items() if k in keys}
    if changes.get("effort") in ("none", ""):
        changes["effort"] = None
    return replace(base, **changes)


def load(path: Path | None = None) -> Config:
    load_env()
    data: dict = {}
    for candidate in (paths.USER_DIR / "config.toml", paths.ROOT / "autokeeper.toml", path):
        if candidate and Path(candidate).is_file():
            for key, value in tomllib.loads(Path(candidate).read_text(encoding="utf-8")).items():
                if key == "roles":
                    for name, spec in value.items():
                        data.setdefault("roles", {}).setdefault(name, {}).update(spec)
                else:
                    data[key] = value
    data.update(ui_settings())
    preset = os.environ.get("AUTOKEEPER_PRESET") or data.get("preset", "claude")
    if preset not in PRESETS:
        raise ValueError(f"未知预设：{preset}（可选：{', '.join(PRESETS)}）")
    mode = os.environ.get("AUTOKEEPER_MODE") or data.get("mode", "标准")
    if mode not in MODES:
        raise ValueError(f"未知的模式：{mode}（可选：{'、'.join(MODES)}）")
    cfg = Config(roles=moded(PRESETS[preset], mode), preset=preset, mode=mode)
    roles = data.get("roles", {})
    every = dict(roles.get("all", {}))
    if picked := os.environ.get("AUTOKEEPER_MODEL") or data.get("model"):
        backend, model = route(picked)
        every.update(backend=backend, model=model)
    if fallback := os.environ.get("AUTOKEEPER_FALLBACK") or data.get("fallback"):
        every.update(fallback=fallback)
    for name in (set(DEFAULT_ROLES) | set(roles)) - {"all", *ROLE_PARENTS}:
        cfg.roles[name] = _role(_role(cfg.role(name), every), roles.get(name, {}))
    for name, parent in ROLE_PARENTS.items():
        if name in roles:
            inherited = cfg.role(parent)
            cfg.roles[name] = _role(replace(inherited, effort=ROLE_EFFORT.get(name, inherited.effort)), roles[name])
    cfg.module = data.get("module", cfg.module)
    cfg.theme = data.get("theme", cfg.theme)
    cfg.art_style = data.get("art_style", cfg.art_style)
    cfg.dice = data.get("dice", cfg.dice)
    cfg.plugins = data.get("plugins", cfg.plugins)
    cfg.tips = data.get("tips", cfg.tips) is not False
    cfg.animations = data.get("animations", cfg.animations) is not False
    cfg.imagegen = data.get("imagegen", cfg.imagegen) is not False
    cfg.showcase = data.get("showcase", cfg.showcase) is not False
    cfg.search = os.environ.get("AUTOKEEPER_SEARCH") or data.get("search", cfg.search)
    budget = os.environ.get("AUTOKEEPER_BUDGET_USD") or data.get("budget_usd")
    cfg.budget_usd = float(budget) if budget not in (None, "") else None
    cfg.visual_budget_usd = max(0.0, float(data.get("visual_budget_usd", cfg.visual_budget_usd)))
    cfg.visual_limit = max(0, int(data.get("visual_limit", cfg.visual_limit)))
    cfg.minds = max(0, min(8, int(data.get("minds", cfg.minds))))
    cfg.improv = os.environ.get("AUTOKEEPER_IMPROV") or data.get("improv", cfg.improv)
    if cfg.improv not in IMPROV:
        raise ValueError(f"未知的自由度：{cfg.improv}（可选：{'、'.join(IMPROV)}）")
    return cfg
