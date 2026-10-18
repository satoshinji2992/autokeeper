"""Small reference pictures travel with the table, never as host filesystem paths."""

from __future__ import annotations

import base64
import hashlib
import io

from PIL import Image

from .. import config

LIMIT = 256_000


def pack(path: str, *, max_size: int = 512) -> str:
    try:
        with Image.open(path) as original:
            picture = original.convert("RGB")
            size = min(1600, max(1, max_size))
            picture.thumbnail((size, size))
            out = io.BytesIO()
            picture.save(out, "JPEG", quality=85)
            if out.tell() > LIMIT:
                out = io.BytesIO()
                picture.save(out, "JPEG", quality=60)
        data = out.getvalue()
        return base64.b64encode(data).decode("ascii") if len(data) <= LIMIT else ""
    except (OSError, ValueError):
        return ""


def unpack(encoded: str, *, max_size: int = 512) -> str:
    if not isinstance(encoded, str) or len(encoded) > LIMIT * 2:
        return ""
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > LIMIT:
            return ""
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != "JPEG" or max(picture.size) > min(1600, max(1, max_size)):
                return ""
            picture.verify()
        folder = config.SAVES / "art" / "table"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{hashlib.sha256(data).hexdigest()}.jpg"
        target.write_bytes(data)
        return str(target)
    except (OSError, ValueError, Image.DecompressionBombError):
        return ""
