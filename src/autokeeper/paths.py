"""Where AutoKeeper keeps things on disk. Every other module asks here instead of joining
paths itself.

The repository (or AUTOKEEPER_HOME, for an installed copy):

    modules/            bundled modules; modules/local/ holds imports, modules/_import/ the inbox
    skills/             shared skills; skills/module-<id>/ are compiled from the modules
    data/               everything the game writes while you play (below)
    dev/output/         evaluation runs, autoplay logs and other developer output

The data folder:

    data/
      games/            <slot>.json, index.json, checkpoints.sqlite
        art/            pictures a game made or a LAN player sent
        rooms/          <port>.key of a running LAN room
        trash/          deleted games and cards, restorable by moving them back
      cards/            your own investigators (<id>.json and photo); .work/ while one is made
      角色卡导出/        where 导出 puts a card unless you pick another folder
      cache/            safe to delete; rebuilt when needed
        models/         local embedding models (about 90 MB)
        kb/             LightRAG knowledge base per module
        showcases/      big-scene pictures per module
        sessions/       working folder of the Claude Code and Codex processes
      logs/             traces/<date>.jsonl, stderr.log, web.log
      preferences.json  tips.json  spend.json  imagegen/<module>.json

Settings chosen in the game live in ~/.config/autokeeper/ (USER_DIR), beside config.toml.

Tests point DATA (and the other constants) at a temporary folder with monkeypatch, so every
function below reads them when called.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

ROOT = Path(os.environ.get("AUTOKEEPER_HOME", Path(__file__).resolve().parents[2]))
MODULES = ROOT / "modules"
LOCAL_MODULES = MODULES / "local"
IMPORT_INBOX = MODULES / "_import"
SKILLS = ROOT / "skills"
DATA = ROOT / "data"
DEV_OUTPUT = ROOT / "dev" / "output"
OLD_SAVES = ROOT / "saves"  # the single folder used before 0.0.2; see adopt_old()
EXAMPLE = Path(__file__).resolve().parent / "module" / "example"  # format sample for tests and the importer
USER_DIR = Path.home() / ".config" / "autokeeper"


def module_folders() -> list[Path]:
    """The modules to play: the bundled ones, then the player's own imports. The format sample
    (EXAMPLE) is for the importer and the tests, not for playing."""
    found = []
    for base in (MODULES, LOCAL_MODULES):
        if base.is_dir():
            found += [p for p in sorted(base.iterdir()) if (p / "module.yaml").exists()]
    return found


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "_"


# -- games -----------------------------------------------------------------------------------
def games() -> Path:
    return DATA / "games"


def save(slot: str) -> Path:
    return games() / f"{slot}.json"


def save_index() -> Path:
    return games() / "index.json"


def checkpoints() -> Path:
    return games() / "checkpoints.sqlite"


def game_art(digest: str) -> Path:
    """Pictures one game made as the story changed places and people."""
    return games() / "art" / digest


def table_art() -> Path:
    """Pictures LAN players sent with their cards."""
    return games() / "art" / "table"


def room_key(port: int) -> Path:
    return games() / "rooms" / f"{port}.key"


def trash() -> Path:
    return games() / "trash"


# -- cards -----------------------------------------------------------------------------------
def cards() -> Path:
    return DATA / "cards"


def card_work(name: str) -> Path:
    """Scratch space while a card is being made."""
    return cards() / ".work" / name


def card_exports() -> Path:
    return DATA / "角色卡导出"


# -- cache -----------------------------------------------------------------------------------
def cache() -> Path:
    return DATA / "cache"


def models() -> Path:
    return cache() / "models"


def kb(module_id: str) -> Path:
    return cache() / "kb" / module_id


def showcases(module_id: str) -> Path:
    return cache() / "showcases" / _safe(module_id)


def sessions() -> Path:
    """cwd for Claude Code and Codex processes, which keeps them out of other projects."""
    return cache() / "sessions"


# -- logs ------------------------------------------------------------------------------------
def logs() -> Path:
    return DATA / "logs"


def traces() -> Path:
    return logs() / "traces"


def stderr_log() -> Path:
    return logs() / "stderr.log"


def web_log() -> Path:
    return logs() / "web.log"


# -- small settings and ledgers ------------------------------------------------------------
def preferences() -> Path:
    return DATA / "preferences.json"


def tips() -> Path:
    return DATA / "tips.json"


def spend() -> Path:
    return DATA / "spend.json"


def imagegen_ledger(module: str) -> Path:
    return DATA / "imagegen" / f"{_safe(module)}.json"


# -- developer output ------------------------------------------------------------------------
def dev_output(*parts: str) -> Path:
    """dev/output/<parts>: eval reports, autoplay logs, the public-share run files."""
    return DEV_OUTPUT.joinpath(*parts)


# -- the old saves/ folder -------------------------------------------------------------------
ADOPTED = {"models": "cache/models", "kb": "cache/kb", "showcases": "cache/showcases", "investigators": "cards"}


def adopt_old() -> list[str]:
    """Move what is slow to rebuild (local models, knowledge bases, big-scene pictures, your
    own cards) from the old saves/ folder, once. Old games are not read any more; saves/ can
    be deleted afterwards."""
    moved = []
    for old, new in ADOPTED.items():
        source, target = OLD_SAVES / old, DATA / new
        if source.is_dir() and not target.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved.append(old)
            except OSError:
                pass
    return moved
