"""Pictures from Codex's built-in image tool (image_gen), on the local Codex login.

    the picture it shows now (or none) + what to draw  ->  codex exec: one image_gen call
    ->  $CODEX_HOME/generated_images/<thread>/*.png  ->  <target>.jpg (at most 1024 px)
        + <target>.credit.json (based_on: the original's credit)  ->  imagery.preprocess

Used where a picture is missing (a scene or a face no photo was found for, at import and for
the director's new places and people) and where the story visibly changes a place or a person
(engine/visuals.py). Codex runs with instructions of our own and without skills, shell or
plugins, so it calls the tool at once: about 4k tokens of prompt where reading its imagegen
skill first took 128k. Every preset uses this same path, and without a codex CLI that is
logged in and has the image_generation feature it is simply off. Every picture counts against
its module's LIMIT (import and every game together, saves/imagegen/<module>.json); a game
also keeps to PER_GAME and an import to AT_IMPORT and LOOKS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import config

LIMIT = 100  # pictures one module may ever get: its import and all its games together
PER_GAME = 12  # pictures one game (a save) may make
AT_IMPORT = 40  # pictures an import may make for scenes and faces with no photo
LOOKS = 6  # changed states (a fire, a death) an import may draw from the module's own pictures
DRIVER = "gpt-5.6-luna"  # drives the tool; the login's default model may be one exec does not take
TIMEOUT = 300.0  # seconds for one picture; measured, one takes about a minute
LONG_SIDE = 1024
LICENSE = "AI 生成（Codex image_gen）"
KINDS = {"scene": "场景", "portrait": "人物头像", "item": "物件特写"}

# Prompt sections Codex adds by default, and features whose tools it would otherwise get.
# Features go through -c features.<name>, which an older Codex ignores; --disable would fail on
# a name it does not know. The code-mode host stays: the image tool is called through it.
QUIET = ("include_permissions_instructions", "include_apps_instructions", "include_environment_context",
         "include_collaboration_mode_instructions", "skills.include_instructions")
OFF = ("plugins", "apps", "shell_tool", "unified_exec", "browser_use", "browser_use_external", "computer_use",
       "in_app_browser", "multi_agent", "goals", "hooks", "tool_suggest", "sleep_tool", "skill_search",
       "skill_mcp_dependency_install", "workspace_dependencies", "memories")

INSTRUCTIONS = """You make exactly one picture with the image_gen tool, then stop.
- Call image_gen right away. Do not run commands, read files, browse or ask questions.
- Pass the request on faithfully: keep every detail it gives, add nothing it does not ask for.
- When an image is attached, edit that image: keep its composition, viewpoint, framing, light and style, and change only what the request asks.
- No text, letters, captions, signatures, watermarks or borders in the picture. Keep violence non-graphic: no gore.
- After the tool returns, reply with only the full path of the saved image file; if it failed, reply "failed:" and the reason."""

STYLE = ("Style: like a period photograph or a muted oil painting of that time; moody light, clear shapes and strong "
         "contrast, so it still reads when shrunk to a few dozen characters; nothing modern.")
FRAMING = {
    "scene": "A wide landscape view of the place (about 3:2).",
    "portrait": "A head-and-shoulders portrait of this one person, the face clearly visible and lit, against a plain dark background.",
    "item": "A close-up of this one object on a plain dark background.",
}


class Unavailable(RuntimeError):
    """No usable Codex here, or the switch is off."""


class OverBudget(RuntimeError):
    """The module (or the game) has had all the pictures it may have."""


class Failed(RuntimeError):
    """Codex ran but no picture came back."""


@dataclass(frozen=True)
class Status:
    ok: bool
    reason: str = ""  # why not, for the settings and `autokeeper doctor`
    exe: str = ""


@lru_cache(maxsize=1)
def status() -> Status:
    """Whether this machine can make pictures: a codex CLI on PATH, logged in, with the
    image_generation feature. Checked once per process (two quick local commands)."""
    exe = shutil.which("codex")
    if not exe:
        return Status(False, "没有找到 codex 命令（npm i -g @openai/codex，再 codex login）")
    try:
        login = subprocess.run([exe, "login", "status"], capture_output=True, timeout=20)
        features = subprocess.run([exe, "features", "list"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return Status(False, "codex 命令没有响应")
    said = (login.stdout + login.stderr).decode("utf-8", "replace").lower()  # read, never shown: it may name the key
    if login.returncode != 0 or "logged in" not in said or "not logged in" in said:
        return Status(False, "Codex 还没登录（运行 codex login）")
    listed = features.stdout.decode("utf-8", "replace")
    if not re.search(r"^image_generation\s+.*\btrue\s*$", listed, re.M):
        return Status(False, "这个 Codex 不能生图：没有开启 image_generation（codex update 后再试）")
    return Status(True, "", exe)


def why_off(cfg: config.Config | None) -> str:
    """Why pictures are not generated with this configuration ("" when they are)."""
    if cfg is None or not getattr(cfg, "imagegen", False):
        return "设置里关掉了"
    return status().reason


def enabled(cfg: config.Config | None) -> bool:
    return not why_off(cfg)


# -- the budget ---------------------------------------------------------------------------
_lock = threading.Lock()
_held: dict[str, int] = {}  # module -> pictures being made right now


def ledger(module: str) -> Path:
    return config.SAVES / "imagegen" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', module) or '_'}.json"


def _read(module: str) -> dict[str, Any]:
    path = ledger(module)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"module": module, "used": 0, "pictures": []}
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("used"), int):
        return {"module": module, "used": LIMIT, "pictures": []}  # unreadable: spend nothing more on a guess
    return data


def used(module: str) -> int:
    with _lock:
        return int(_read(module)["used"])


def left(module: str) -> int:
    with _lock:
        return max(0, LIMIT - int(_read(module)["used"]) - _held.get(module, 0))


def _reserve(module: str) -> None:
    with _lock:
        if int(_read(module)["used"]) + _held.get(module, 0) >= LIMIT:
            raise OverBudget(f"这个模组的生图额度（{LIMIT} 张）已经用完")
        _held[module] = _held.get(module, 0) + 1


def _settle(module: str, made: dict[str, Any] | None) -> None:
    """Release a reservation, and count the picture if one was made."""
    with _lock:
        _held[module] = max(0, _held.get(module, 0) - 1)
        if made is None:
            return
        data = _read(module)
        data["used"] = int(data["used"]) + 1
        data["pictures"] = [*data.get("pictures", []), made][-LIMIT:]
        path = ledger(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)


# -- prompts --------------------------------------------------------------------------------
def prompt(subject: str, kind: str, *, era: str = "", setting: str = "", change: str = "") -> str:
    """What Codex is asked for: a new picture of `subject`, or with `change` the attached
    picture of it as it looks after the change."""
    world = "，".join(x for x in (era, setting) if x)
    head = f"A picture for a Call of Cthulhu horror game set in {world}." if world else "A picture for a Call of Cthulhu horror game."
    if change:
        return (f"{head}\nEdit the attached picture of {KINDS.get(kind, '场景')}：{subject}\n"
                f"Show it after this change: {change}\n"
                "Keep the same place or person, the composition, viewpoint, framing and style; change only what this calls for.")
    return f"{head}\n{FRAMING.get(kind, FRAMING['scene'])}\nSubject ({KINDS.get(kind, '场景')}): {subject}\n{STYLE}"


# -- running Codex ----------------------------------------------------------------------------
def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def command(exe: str, folder: Path, image: Path | None = None) -> list[str]:
    """`codex exec` for one picture: our instructions instead of Codex's, read-only, no skills,
    plugins, shell or browser, so the image tool is all there is to call. Not --ephemeral:
    without a session Codex keeps no picture (measured); the session is deleted afterwards."""
    args = [
        exe, "exec", "--json", "--skip-git-repo-check", "--ignore-user-config",
        "-s", "read-only", "-C", str(folder), "-m", os.environ.get("AUTOKEEPER_IMAGEGEN_MODEL") or DRIVER,
        "-c", f"model_instructions_file={json.dumps(str(folder / 'instructions.md'))}",
        "-c", 'model_reasoning_effort="low"', "-c", 'web_search="disabled"', "-c", "features.image_generation=true",
    ]  # fmt: skip
    for key in QUIET:
        args += ["-c", f"{key}=false"]
    for feature in OFF:
        args += ["-c", f"features.{feature}=false"]
    if image is not None:
        args += ["-i", str(image)]
    return [*args, "-"]  # the prompt comes on stdin: -i takes any number of files


def events(out: bytes) -> tuple[str, dict[str, int], str, str]:
    """From exec's JSON lines: the thread id, the token usage, a failure, the last reply."""
    thread, usage, failure, said = "", {}, "", ""
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind, item = ev.get("type"), ev.get("item") or {}
        if kind == "thread.started":
            thread = str(ev.get("thread_id") or "")
        elif kind == "turn.completed":
            u = ev.get("usage") or {}
            usage = {"input_tokens": int(u.get("input_tokens", 0)), "cache_read": int(u.get("cached_input_tokens", 0)),
                     "output_tokens": int(u.get("output_tokens", 0)) + int(u.get("reasoning_output_tokens", 0))}
        elif kind == "turn.failed":
            failure = str((ev.get("error") or {}).get("message", "")) or "turn.failed"
        elif kind == "error":
            failure = failure or str(ev.get("message", ""))
        elif kind == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            said = str(item.get("text", ""))
    return thread, usage, failure, said


IMAGES = (".png", ".jpg", ".jpeg", ".webp")


def generated(thread: str, said: str = "", folder: Path | None = None) -> Path | None:
    """The picture Codex made in this thread: the path its reply names, else the newest in
    $CODEX_HOME/generated_images/<thread>/ (or in the run's own folder). Only files in those
    two places count, whatever the reply says."""
    places = [p for p in (folder,) if p is not None]
    if re.fullmatch(r"[0-9A-Za-z-]{8,80}", thread):
        places.insert(0, codex_home() / "generated_images" / thread)
    for word in re.findall(r"[^\s`'\"<>()（）]+", said):
        path = Path(word.rstrip(".,;:，。；"))
        if path.suffix.lower() in IMAGES and path.is_file() and any(_inside(path, place) for place in places):
            return path
    found = [p for place in places if place.is_dir() for p in place.rglob("*")
             if p.suffix.lower() in IMAGES and p.is_file() and p.name != ATTACHED]
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


ATTACHED = "attached.jpg"  # the picture we hand Codex, in the run's folder


async def _run(text: str, image: Path | None, timeout: float) -> tuple[bytes, str, dict[str, int], float]:
    """One codex exec: the picture it made (read before its folder goes), the thread, the
    token usage and the seconds it took."""
    from .harness.models import _communicate  # kills the process group on cancel or time-out

    exe = status().exe or "codex"
    with tempfile.TemporaryDirectory(prefix="autokeeper-imagegen-") as tmp:
        folder = Path(os.path.realpath(tmp))
        (folder / "instructions.md").write_text(INSTRUCTIONS, encoding="utf-8")
        attached = None
        if image is not None:
            attached = folder / ATTACHED
            with Image.open(image) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((LONG_SIDE, LONG_SIDE))
                im.save(attached, "JPEG", quality=90)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *command(exe, folder, attached), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=folder, start_new_session=os.name == "posix",
        )
        try:
            out, err = await asyncio.wait_for(_communicate(proc, text.encode("utf-8")), timeout)
        except TimeoutError:
            raise Failed(f"Codex 生图超过 {timeout:.0f} 秒没有完成") from None
        seconds = time.monotonic() - start
        thread, usage, failure, said = events(out)
        picture = generated(thread, said, folder)
        data = picture.read_bytes() if picture is not None else b""
    await _forget(exe, thread)
    if not data:
        why = failure or said or err.decode("utf-8", "replace").strip()[-300:] or f"退出码 {proc.returncode}"
        raise Failed(f"Codex 没有画出图：{why[:300]}")
    return data, thread, usage, seconds


async def _forget(exe: str, thread: str) -> None:
    """Our one-picture session out of the user's Codex history, and its picture folder (both
    ours alone; best effort)."""
    if not re.fullmatch(r"[0-9A-Za-z-]{8,80}", thread):
        return
    shutil.rmtree(codex_home() / "generated_images" / thread, ignore_errors=True)
    try:
        proc = await asyncio.create_subprocess_exec(exe, "delete", "--force", thread, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), 20)
    except (OSError, TimeoutError):
        pass


def _credit(picture: Path | None) -> dict[str, Any] | None:
    """The credit of the picture a new one is made from, so the chain of sources stays."""
    if picture is None:
        return None
    record = picture.with_name(picture.stem + ".credit.json")
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"title": picture.name}
    return data if isinstance(data, dict) else {"title": picture.name}


def _store(data: bytes, target: Path, credit: dict[str, Any]) -> Path:
    """The picture at a sane size as <target>.jpg, with <target>.credit.json beside it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
        im.save(target, "JPEG", quality=90)
        size = im.size
    record = credit | {"width": size[0], "height": size[1]}
    target.with_name(target.stem + ".credit.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


async def make(
    text: str, target: Path, *, kind: str, module: str, original: Path | None = None, title: str = "",
    where: str = "game", timeout: float = TIMEOUT, on_reply: Callable[[Any], None] | None = None,
) -> Path:
    """One picture into `target` (a .jpg), from `original` when given, else from the words alone;
    with its credit and the preprocessed copy the renderer reads. Raises Unavailable,
    OverBudget or Failed; nothing is counted unless a picture comes back."""
    from . import imagery

    if not status().ok:
        raise Unavailable(status().reason)
    target = Path(target)
    if target.suffix.lower() != ".jpg":
        target = target.with_name(target.name + ".jpg")
    _reserve(module)
    made = None
    try:
        data, thread, usage, seconds = await _run(text, original, timeout)
        credit = {"title": title or "Codex 生成的图", "page": "", "thumb": "", "license": LICENSE,
                  "artist": "Codex image_gen", "kind": kind, "prompt": text, "based_on": _credit(original)}
        await asyncio.to_thread(_store, data, target, credit)
        made = {"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "where": where,
                "file": str(target), "thread": thread}
    finally:
        _settle(module, made)
    if on_reply is not None:
        from .harness import Reply

        on_reply(Reply(usage=usage, seconds=seconds))
    await asyncio.to_thread(imagery.preprocess, target, kind if kind in imagery.PIPELINES else "scene")
    return target


def filler(cfg: config.Config | None, module, art_dir: Path, *, cap: int = AT_IMPORT, where: str = "import",
           on_reply: Callable[[Any], None] | None = None) -> Callable[[str, str, str], Awaitable[bool]] | None:
    """For imagery.fetch_folder: when no photo fits, a picture made from the description, up
    to `cap` of them. None where pictures are not generated: switched off, no Codex, or a
    folder in the repository (bundled modules get licensed photos only)."""
    from . import imagery

    if not enabled(cfg) or not imagery.private(art_dir):
        return None
    made = 0

    async def generate(name: str, subject: str, kind: str) -> bool:
        nonlocal made
        if made >= cap:
            return False
        made += 1
        try:
            await make(prompt(subject, kind, era=module.era, setting=module.setting), art_dir / f"{name}.jpg",
                       kind=kind, module=module.id, title=subject[:80], where=where, on_reply=on_reply)
            return True
        except (Unavailable, OverBudget, Failed, OSError, ValueError):
            made -= 1  # nothing was counted
            return False

    return generate


async def looks(cfg: config.Config | None, module, art_dir: Path, *, log=print, cap: int = LOOKS) -> list[str]:
    """The pictures for the changed states a module gives its places and people
    (Location.looks, NPC.looks), each made from the picture the place or person has
    otherwise, at most `cap` tries per import. Pictures already there stay."""
    from . import imagery

    if not enabled(cfg) or not imagery.private(art_dir):
        return []
    wanted = [(x.art, look, "scene", f"{x.name}：{x.read_aloud.strip()[:160]}") for x in module.locations if x.art for look in x.looks]
    wanted += [(n.portrait, look, "portrait", f"{n.name}，{n.look or n.name}") for n in module.npcs if n.portrait for look in n.looks]
    wanted = [w for w in wanted if not (art_dir / f"{w[1].key(w[0])}.jpg").exists()][:cap]
    done: list[str] = []
    for base, look, kind, subject in wanted:
        key = look.key(base)
        original = next((p for p in (art_dir / f"{base}{ext}" for ext in IMAGES) if p.is_file()), None)
        world = {"era": module.era, "setting": module.setting}
        text = (prompt(subject, kind, change=look.change, **world) if original is not None
                else prompt(f"{subject}。现在：{look.change}", kind, **world))
        try:
            await make(text, art_dir / f"{key}.jpg", kind=kind, module=module.id, original=original,
                       title=f"{subject[:40]}（{look.change[:40]}）", where="import")
        except (Unavailable, OverBudget) as exc:
            log(f"  · 变样图停在这里：{exc}")
            break
        except (Failed, OSError, ValueError) as exc:
            log(f"  ✗ {key}: {str(exc)[:120]}")
            continue
        done.append(key)
        log(f"  ✓ {key}: {look.change[:40]}")
    return done
