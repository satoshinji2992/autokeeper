"""Where AutoKeeper keeps things on disk. Every other module asks here instead of joining
paths itself.

Three layouts, picked once at import:

- From a checkout (`uv run autokeeper` in the repository), or with AUTOKEEPER_HOME set to a
  folder laid out like one, everything sits in that folder:

    modules/<id>/       bundled modules (module/package.py has the layout of one)
    modules/local/<id>/ your imports and generated modules, each with its original in source/
    modules/inbox/      drop module files here to find them in 导入模组
    skills/             shared skills; skills/module-<id>/ are compiled from the modules
    bridge/             the BCDice bridge and its node_modules/
    rule/               the rulebooks and card decks you keep beside the code, not in git (rulebooks())
    data/               everything the game writes while you play (below)
    dev/output/         evaluation runs, autoplay logs and other developer output

- Installed as a package (`uv tool install autokeeper`, `pipx install autokeeper`): the bundled
  modules, shared skills and bridge script come with the package (BUNDLE); what the game writes
  goes to the user's own folders, found by platformdirs (XDG on Linux and macOS, so
  XDG_DATA_HOME and XDG_CACHE_HOME are honoured; %LOCALAPPDATA% on Windows):

    ~/.local/share/autokeeper/   the data folder below, and
      modules/<id>/  inbox/      your imports, and files to import
      skills/                    skills compiled from the modules (the shared ones stay in the package)
      bridge/                    the bridge script, copied from the package by prepare(), and node_modules/
      rule/                      the rulebooks and card decks (rulebooks())
      dev-output/
    ~/.cache/autokeeper/         the cache below (Windows: %LOCALAPPDATA%\\autokeeper\\Cache)
    ~/Documents/AutoKeeper/角色卡导出/

The data folder:

    data/
      games/            <slot>.json, index.json, checkpoints.sqlite
        art/            pictures a game made or a LAN player sent
        debug/          <slot>.jsonl, a game's private developer record
        rooms/          <port>.key of a running LAN room
        trash/          deleted games and cards, restorable by moving them back
      cards/            your own investigators (<id>.json and photo); .work/ while one is made
      角色卡导出/        where 导出 puts a card unless you pick another folder
      cache/            safe to delete; rebuilt when needed
        models/         local embedding models (about 90 MB)
        kb/             LightRAG knowledge base per module; kb/rules/ the rulebooks' index
        showcases/      big-scene pictures per module
        art/<module>/   pictures found or drawn for what a module has none of (pictures.py, module_art())
        cards/          the keeper card decks read from their PDFs (cards.py)
        pictures/       the renderer's prepared copy of every photo
        sessions/       working folder of the Claude Code and Codex processes
        tools/          chafa's official build, where no package manager put one (imagery.fetch_chafa)
      logs/             traces/<date>.jsonl, stderr.log, web.log
      preferences.json  tips.json  spend.json  imagegen/<module>.json

Settings chosen in the game live in USER_DIR beside config.toml and .env, in every layout:
~/.config/autokeeper/ (XDG_CONFIG_HOME) on Linux and macOS, %APPDATA%\\autokeeper on Windows.

Saves made from a checkout stay in its data/ folder; adopt() copies them into an installed
copy's folders (automatically on its first run for the checkouts it knows of, or with
`autokeeper migrate <folder>`).

Tests point DATA (and the other constants) at a temporary folder with monkeypatch, so every
function below reads them when called.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import NamedTuple

PACKAGE = Path(__file__).resolve().parent
SOURCE = PACKAGE.parents[1]  # the repository, when the game runs from a checkout
BUNDLE = PACKAGE / "bundle"  # what the package carries (pyproject.toml): modules, skills, bridge


def is_checkout(folder: Path) -> bool:
    """A folder laid out like the repository."""
    return (folder / "pyproject.toml").is_file() and (folder / "modules").is_dir() and (folder / "src" / "autokeeper").is_dir()


def _user_dirs() -> tuple[Path, Path, Path]:
    """This user's config, data and cache folders for AutoKeeper."""
    if os.name == "nt":
        from platformdirs.windows import Windows

        roaming, local = Windows("autokeeper", appauthor=False, roaming=True), Windows("autokeeper", appauthor=False)
        return Path(roaming.user_config_dir), Path(local.user_data_dir), Path(local.user_cache_dir)
    from platformdirs.unix import Unix  # XDG on macOS too: ~/.config/autokeeper as before

    xdg = Unix("autokeeper")
    return Path(xdg.user_config_dir), Path(xdg.user_data_dir), Path(xdg.user_cache_dir)


def _documents() -> Path:
    from platformdirs import user_documents_dir

    return Path(user_documents_dir()) / "AutoKeeper"


CONFIG_HOME, DATA_HOME, CACHE_HOME = _user_dirs()
USER_DIR = CONFIG_HOME


class Layout(NamedTuple):
    root: Path
    modules: Path
    local_modules: Path
    inbox: Path
    skills: Path
    bridge: Path
    data: Path
    dev_output: Path
    cache: Path | None  # None: data/cache
    exports: Path | None  # None: data/角色卡导出


def layout(home: str | None, installed: bool, data_home: Path, cache_home: Path) -> Layout:
    """The folders of one layout (see the top of this file): an AUTOKEEPER_HOME, a checkout, or
    an installed package (`installed`, with the user's data and cache folders)."""
    if installed:
        return Layout(BUNDLE, BUNDLE / "modules", data_home / "modules", data_home / "inbox", data_home / "skills",
                      data_home / "bridge", data_home, data_home / "dev-output", cache_home, _documents() / "角色卡导出")
    root = Path(home).expanduser().resolve() if home else SOURCE
    return Layout(root, root / "modules", root / "modules" / "local", root / "modules" / "inbox", root / "skills",
                  root / "bridge", root / "data", root / "dev" / "output", None, None)


_HOME = os.environ.get("AUTOKEEPER_HOME")
INSTALLED = not _HOME and not is_checkout(SOURCE)  # a package install: the folders are the user's
ROOT, MODULES, LOCAL_MODULES, INBOX, SKILLS, BRIDGE, DATA, DEV_OUTPUT, CACHE, EXPORTS = layout(_HOME, INSTALLED, DATA_HOME, CACHE_HOME)
EXAMPLE = PACKAGE / "module" / "example"  # format sample for tests and the importer


def module_folders() -> list[Path]:
    """The modules to play: the bundled ones, then the player's own imports. The format sample
    (EXAMPLE) is for the importer and the tests, not for playing. An installed package under an
    AUTOKEEPER_HOME without its own modules/ still offers the ones it carries."""
    found: list[Path] = []
    for base in dict.fromkeys((MODULES, BUNDLE / "modules", LOCAL_MODULES)):
        if base.is_dir():
            names = {p.name for p in found}
            found += [p for p in sorted(base.iterdir()) if (p / "module.yaml").exists()
                      and not (base == BUNDLE / "modules" and p.name in names)]
    return found


def find_module(name: str | Path) -> Path | None:
    """A module folder from a path, or from an id such as zhuishuren (the format sample is example)."""
    path = Path(name)
    if (path / "module.yaml").is_file():
        return path
    return next((p for p in [*module_folders(), EXAMPLE] if p.name == str(name)), None)


def skill(name: str) -> Path:
    """A skill's folder: compiled and shared ones in SKILLS, else the shared one the package carries."""
    here = SKILLS / name
    if not here.exists() and (BUNDLE / "skills" / name).is_dir():
        return BUNDLE / "skills" / name
    return here


def skill_folders() -> list[Path]:
    """Every skill there is: SKILLS, then the package's shared skills it lacks."""
    found: dict[str, Path] = {}
    for base in (SKILLS, BUNDLE / "skills"):
        if base.is_dir():
            for p in sorted(base.iterdir()):
                if (p / "SKILL.md").exists():
                    found.setdefault(p.name, p)
    return sorted(found.values(), key=lambda p: p.name)


def bridge_script() -> Path:
    return BRIDGE / "bcdice_bridge.mjs"


def bridge_ready() -> bool:
    """The bridge's npm packages are installed (`autokeeper setup`, or the first start)."""
    return (BRIDGE / "node_modules" / "bcdice" / "package.json").is_file()


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


def game_debug(slot: str) -> Path:
    """Complete private developer records, kept beside saves rather than in shared state."""
    return games() / "debug" / f"{_safe(slot)}.jsonl"


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
    return EXPORTS if EXPORTS is not None else DATA / "角色卡导出"


# -- cache -----------------------------------------------------------------------------------
def cache() -> Path:
    return CACHE if CACHE is not None else DATA / "cache"


def models() -> Path:
    return cache() / "models"


def tools() -> Path:
    """Programs the game fetched for itself (imagery.fetch_chafa: chafa's official build where no
    package manager put one); in the cache, as they come back if it is cleared."""
    return cache() / "tools"


def kb(module_id: str) -> Path:
    return cache() / "kb" / module_id


def rules_index() -> Path:
    """The rules lookup's index of the rulebooks (rules_lookup.py, `autokeeper rules index`)."""
    return cache() / "kb" / "rules"


def showcases(module_id: str) -> Path:
    return cache() / "showcases" / _safe(module_id)


def module_art(module_id: str) -> Path:
    """Pictures found or drawn on this machine for a module's places, people, things and creatures
    that it has none of (pictures.py), for all its games: places/, people/, items/, each with its
    credits.yaml. The module's own folder is left alone."""
    return cache() / "art" / _safe(module_id)


def keeper_decks() -> Path:
    """The keeper card decks read from their PDFs (cards.py): decks.json and faces/<deck>/<id>.png."""
    return cache() / "cards"


def prepared_pictures() -> Path:
    return cache() / "pictures"


def sessions() -> Path:
    """cwd for Claude Code and Codex processes, which keeps them out of other projects."""
    return cache() / "sessions"


# -- the rulebooks ---------------------------------------------------------------------------
def rulebooks() -> Path:
    """The rulebooks and card decks the user keeps beside the code (rule/, not in git; in the data
    folder when installed), or the folder AUTOKEEPER_RULES names (AUTOKEEPER_RULEBOOKS too). Read by
    `autokeeper cards import` (cards.py) and `autokeeper rules index` (rules_lookup.py, whose
    `rulebooks` setting in config.toml comes first)."""
    if env := os.environ.get("AUTOKEEPER_RULES") or os.environ.get("AUTOKEEPER_RULEBOOKS"):
        return Path(env).expanduser()
    return (DATA if INSTALLED else ROOT) / "rule"


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


# -- an installed copy: its folders, and the saves of a checkout ------------------------------
MARK = ".from-package"  # in a folder prepare() fills: the digest of the package files it holds
CHECKOUTS = "checkouts.json"  # in USER_DIR: the checkouts this user has played from


def _digest(folder: Path) -> str:
    h = hashlib.sha1()
    for f in sorted(p for p in folder.rglob("*") if p.is_file()):
        h.update(f.relative_to(folder).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def _fill(source: Path, target: Path, *, keep: tuple[str, ...] = ()) -> bool:
    """Copy the package's `source` folder over `target` when the package changed since the last
    copy. What the game added there (`keep`, such as node_modules/) stays. A folder prepare()
    did not fill, such as a checkout's own bridge/, is left alone. True when it copied."""
    if not source.is_dir():
        return False
    mark, digest = target / MARK, _digest(source)
    try:
        before = json.loads(mark.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
        if target.is_dir() and any(p.name not in keep for p in target.iterdir()):
            return False
    if isinstance(before, dict) and before.get("digest") == digest:
        return False
    target.mkdir(parents=True, exist_ok=True)
    old = set(before.get("items", [])) if isinstance(before, dict) else set()
    items = sorted(p.name for p in source.iterdir())
    for name in old - set(items) - set(keep):  # gone from the package
        with contextlib.suppress(OSError):
            shutil.rmtree(target / name) if (target / name).is_dir() else (target / name).unlink()
    for name in items:
        here, there = source / name, target / name
        if there.is_dir():
            shutil.rmtree(there)
        if here.is_dir():
            shutil.copytree(here, there)
        else:
            shutil.copy2(here, there)
    mark.write_text(json.dumps({"digest": digest, "items": items}), encoding="utf-8")
    return True


def prepare() -> None:
    """Ready the folders an installed copy plays from, at every start: copy the package's bridge
    script to where its npm packages go."""
    lock = BRIDGE / "package-lock.json"
    old_lock = lock.read_bytes() if lock.is_file() else b""
    if _fill(BUNDLE / "bridge", BRIDGE, keep=("node_modules",)) and old_lock and lock.read_bytes() != old_lock:
        shutil.rmtree(BRIDGE / "node_modules", ignore_errors=True)  # other packages now: install them again


def remember_checkout() -> None:
    """Note this checkout, when the game runs from one, for adopt() on an installed copy's first
    start. Writing a save calls this."""
    if INSTALLED or not is_checkout(ROOT):
        return
    try:
        known = json.loads((USER_DIR / CHECKOUTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = []
    if not isinstance(known, list):
        known = []
    where = str(ROOT.resolve())
    if where in known:
        return
    with contextlib.suppress(OSError):
        USER_DIR.mkdir(parents=True, exist_ok=True)
        (USER_DIR / CHECKOUTS).write_text(json.dumps([*known, where], ensure_ascii=False, indent=1), encoding="utf-8")


def known_checkouts() -> list[Path]:
    """Checkouts this user played from that still have saves, and the current folder if it is one."""
    try:
        known = [Path(p) for p in json.loads((USER_DIR / CHECKOUTS).read_text(encoding="utf-8")) if isinstance(p, str)]
    except (OSError, ValueError, TypeError):
        known = []
    return [root for root in dict.fromkeys([*known, Path.cwd()])
            if is_checkout(root) and _holds_saves(root) and (root / "data").resolve() != DATA.resolve()]


def _holds_saves(root: Path) -> bool:
    return any((root / where).is_dir() for where in ("data/games", "data/cards", "modules/local"))


def _merge(source: Path, target: Path, skip: tuple[str, ...] = (), join: str = "") -> int:
    """Copy every file of `source` that `target` does not have, leaving out the top-level names
    in `skip`; the JSON file named `join` (the save index) gets the entries it lacks. Nothing in
    `target` is overwritten. The number of files (or index entries) copied."""
    copied = 0
    for item in sorted(source.iterdir()):
        there = target / item.name
        if item.name in skip or item.name.endswith((".lock", ".tmp")) or item.is_symlink():
            continue
        if item.is_dir():
            copied += _merge(item, there)
        elif item.name == join and there.is_file():
            try:
                old, new = json.loads(item.read_text(encoding="utf-8")), json.loads(there.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(old, dict) and isinstance(new, dict) and (missing := set(old) - set(new)):
                there.write_text(json.dumps(old | new, ensure_ascii=False, indent=1), encoding="utf-8")
                copied += len(missing)
        elif item.is_file() and not there.exists():
            there.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, there)
            copied += 1
    return copied


def adopt(root: Path) -> dict[str, int]:
    """Copy the saves, cards, imported modules and slow-to-rebuild caches of a checkout (or of
    its data/ folder) into this copy's folders. Nothing is moved or overwritten, so the checkout
    keeps working, and adopting it again copies only what is new. Files copied, by what they are;
    empty when `root` holds none of these."""
    root = Path(root).expanduser().resolve()
    if not is_checkout(root) and is_checkout(root.parent):  # its data/ folder
        root = root.parent
    data = root / "data" if is_checkout(root) else root
    if not is_checkout(root) and not any((root / where).is_dir() for where in ("games", "cards")):
        return {}
    if not data.is_dir() or data == DATA.resolve():
        return {}
    plan = [("存档", data / "games", games(), ("rooms",), "index.json"),
            ("角色卡", data / "cards", cards(), (".work",), ""),
            ("导出的角色卡", data / "角色卡导出", card_exports(), (), ""),
            ("本地模型与知识库", data / "cache", cache(), ("sessions", "pictures"), ""),
            ("偏好与花费记录", data, DATA, (), "")]
    if is_checkout(root):
        plan += [("导入的模组", root / "modules" / "local", LOCAL_MODULES, (), ""),
                 ("待导入的文件", root / "modules" / "inbox", INBOX, (), "")]
    counts: dict[str, int] = {}
    for label, source, target, skip, join in plan:
        if not source.is_dir() or source.resolve() == target.resolve():
            continue
        if source == data:  # only the small files at the top: preferences, tips, spend, imagegen/
            n = 0
            for name in ("preferences.json", "tips.json", "spend.json", "imagegen"):
                if (source / name).is_dir():
                    n += _merge(source / name, target / name)
                elif (source / name).is_file() and not (target / name).exists():
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source / name, target / name)
                    n += 1
        else:
            n = _merge(source, target, skip, join)
        if n:
            counts[label] = counts.get(label, 0) + n
    return counts


def adopt_known() -> dict[Path, dict[str, int]]:
    """On an installed copy's first start (no games folder yet): adopt() every checkout found."""
    if not INSTALLED or games().exists():
        return {}
    found = {root: n for root in known_checkouts() if (n := adopt(root))}
    games().mkdir(parents=True, exist_ok=True)  # first start done: later ones leave checkouts alone
    return found
