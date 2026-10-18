"""Files the player brings: module documents, zip archives of them, module ids made from
file names, and the paths terminals paste when a file is dragged onto them.

Zips made on Chinese Windows store their file names in GBK without the UTF-8 flag, and
zipfile reads such names as cp437, so `unpack` works out the encoding before extracting.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shlex
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from ..harness.tools import IMAGES

DOCUMENTS = (".pdf", ".docx", ".md", ".txt")  # pypdfium2 reads PDFs, MarkItDown the rest
ARCHIVES = (".zip",)
MAX_UNPACKED = 2 * 1024**3  # page scans make a module a few hundred MB at most
UTF8_NAMES = 0x800  # general purpose flag: the name is UTF-8
ENCRYPTED = 0x1


def module_id(name: str) -> str:
    """An id from a file name: lowercase ASCII words joined by hyphens, Chinese as pinyin
    (冬雪之下 → dongxuezhixia, “fallout3 - 有些人油盐不进” → fallout3-youxierenyouyanbujin).
    The id also names the Skill package, module-<id>, and skill names allow nothing else."""
    from pypinyin import lazy_pinyin
    from slugify import slugify

    # The syllables of one run of hanzi stay one word, like zhuishuren; anything else is spaced off.
    spelled = "".join(lazy_pinyin(name, errors=lambda chunk: f" {chunk} "))
    return slugify(spelled, max_length=40, word_boundary=True) or "mozu-" + hashlib.sha1(name.encode()).hexdigest()[:6]


@dataclass
class Unpacked:
    documents: list[Path]  # biggest first: the module's text is usually the biggest file
    images: list[Path]  # maps, handouts and portraits, for the importer's view_image


def unpack(archive: Path, dest: Path) -> Unpacked:
    """Extract the documents and pictures in a zip into dest, and nothing else."""
    with zipfile.ZipFile(archive, metadata_encoding=_name_encoding(archive)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir() and _wanted(i.filename)]
        if any(i.flag_bits & ENCRYPTED for i in members):
            raise ValueError("压缩包加了密码：先用解压软件解开，再导入里面的模组文件")
        if sum(i.file_size for i in members) > MAX_UNPACKED:
            raise ValueError("压缩包解开后超过 2 GB：先手动解开，挑出模组文件再导入")
        paths = [Path(zf.extract(i, dest)) for i in members]
    documents = sorted((p for p in paths if p.suffix.lower() in DOCUMENTS), key=lambda p: (-p.stat().st_size, str(p)))
    return Unpacked(documents, sorted(p for p in paths if p.suffix.lower() in IMAGES))


def _wanted(name: str) -> bool:
    """A document or a picture; not macOS's __MACOSX shadow files, hidden files or Word's ~$ lock files."""
    parts = name.replace("\\", "/").split("/")
    if parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts) or parts[-1].startswith("~$"):
        return False
    return Path(parts[-1]).suffix.lower() in (*DOCUMENTS, *IMAGES)


def _name_encoding(archive: Path) -> str | None:
    """How the names stored without the UTF-8 flag are encoded: UTF-8 (macOS's own zips),
    else GB18030, a superset of GBK; None leaves zipfile's cp437."""
    with zipfile.ZipFile(archive) as zf:
        raw = b"\n".join(i.orig_filename.encode("cp437") for i in zf.infolist() if not i.flag_bits & UTF8_NAMES)
    for encoding in ("utf-8", "gb18030"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return None


def dropped_path(text: str) -> Path | None:
    """The existing file or folder in what a terminal pastes when one is dragged onto it: a
    plain path, a quoted one, one with backslash-escaped spaces, or a file:// URI (the first,
    when several were dropped). Under WSL a Windows path is tried as the Linux one too."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines or len(lines[0]) > 1000:  # a pasted paragraph is not a path
        return None
    s = lines[0]
    unquoted = s[1:-1] if len(s) > 1 and s[0] == s[-1] and s[0] in "'\"" else s
    tries = [s, unquoted]
    if unquoted.lower().startswith("file:"):
        tries.append(url2pathname(urlparse(unquoted).path))  # percent-decoded; /C:/Users/… becomes C:\Users\… on Windows
    if os.name != "nt":  # on Windows a backslash separates folders and escapes nothing
        with contextlib.suppress(ValueError):  # an unbalanced quote: it was not shell quoting
            tries += shlex.split(s)[:1]
        tries += [p for p in (from_windows(unquoted),) if p]
    for t in tries:
        try:
            if t and (p := Path(t).expanduser()).exists():
                return p
        except (OSError, ValueError, RuntimeError):  # "file name too long", a NUL, an unknown ~user
            continue
    return None


def from_windows(text: str) -> str | None:
    """A Windows path as WSL sees it: C:\\Users\\… → /mnt/c/Users/…, \\\\wsl.localhost\\Ubuntu\\root\\… → /root/…
    Explorer drags these in, and older Windows Terminals paste them untranslated."""
    if m := re.fullmatch(r"([A-Za-z]):[\\/](.*)", text, re.S):
        return f"/mnt/{m[1].lower()}/" + m[2].replace("\\", "/")
    if m := re.fullmatch(r"\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)?", text, re.S | re.I):
        return (m[1] or "\\").replace("\\", "/")
    return None
