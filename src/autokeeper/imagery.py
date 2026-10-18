"""Real pictures for scenes and portraits, rendered as terminal character art.

    queries (Haiku)  ->  Wikimedia Commons search (licence-filtered)  ->  contact sheet
    ->  vision agent picks one and a crop box (Opus)  ->  art/<name>.jpg + art/<name>.credit.json

At play time a scene is cropped to the panel's shape and chafa renders it in Unicode block
characters, with ASCII strokes drawn along its lines so the shapes still read ("picture",
the default). STYLES has the others: finer 2×3 sextants, no outlines, plain ASCII glyphs.
Hand-drawn art/<name>.txt stays as the fallback when there is no picture or no chafa.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import random
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from pydantic import BaseModel, Field

from . import artkit
from .config import Config
from .harness import Session

# rembg pulls in numba, whose default TBB layer prints "Attempted to fork from a non-main
# thread" straight onto the terminal when a worker thread starts chafa. The workqueue layer
# has no such check.
os.environ.setdefault("NUMBA_THREADING_LAYER", "workqueue")

COMMONS = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AutoKeeper/0.1 (https://github.com/satoshinji2992/autokeeper)"
OPEN_LICENSES = ("public domain", "pd", "cc0", "cc by", "cc-by", "attribution", "no restrictions", "no known copyright")
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/tiff")
# chafa's symbols per art style (Ctrl+A cycles them; the first is the default). Colour is
# always 24-bit, where chafa's dither options do nothing. Box-drawing ("border") symbols are left
# out: their thin lines read as noise in grass and leaves, and as stray strokes next to outlines.
STYLES = {
    "picture": ["--symbols", "block+space-wide-inverted", "--color-space", "din99d"],
    "fine": ["--symbols", "block+sextant+space-wide-inverted", "--color-space", "din99d"],
    "photo": ["--symbols", "block+space-wide-inverted", "--color-space", "din99d"],
    "ascii": ["--symbols", "ascii", "--color-space", "din99d"],
}
STYLE_NAMES = {
    "picture": "块字符 + 轮廓线",
    "fine": "六分块 + 轮廓线（更细；终端字体不支持时会显示成方框）",
    "photo": "块字符，不描轮廓",
    "ascii": "纯 ASCII",
}
OUTLINED = {"picture", "fine"}  # styles that draw ASCII outlines over scenes (see outlines())


@dataclass
class Candidate:
    title: str
    page: str
    thumb: str
    license: str
    artist: str
    width: int
    height: int


class Queries(BaseModel):
    queries: list[str] = Field(description="2–3 条英文检索词")


class Pick(BaseModel):
    index: int = Field(description="选中的候选编号，都不合适填 -1")
    crop: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0], description="裁剪框 x0,y0,x1,y1，取值 0–1")
    reason: str = ""


QUERY_SYSTEM = """你为一个 1920 年代背景的克苏鲁跑团游戏找配图。根据描述写 3 条英文检索词，用于在 Wikimedia Commons 搜索：
- 每条只有 2–4 个常见英文词，越宽泛越容易搜到，比如 "old graveyard"、"Victorian house night"、"portrait old man"；
- 第一条写画面主体，第二条加一个年代或画种词（painting / photograph / 1920s），第三条换一个近义说法；
- 人物写性别、年龄段和身份（portrait elderly woman、portrait policeman），不要写名字；不要引号和语法符号。"""

PICK_SYSTEM = """你是游戏美术，从一张编号联系表里挑最适合做终端字符画的图：
- 内容贴合描述，年代和气氛对得上；
- 主体清楚、明暗对比强、背景简单（转成字符画后还能认出来）；
- 头像要能看清一张脸；
- 不选带大段文字、水印、现代物件或明显不合适的图。
给出裁剪框（相对坐标）：头像裁到头肩，场景裁掉无关边缘。都不合适就填 -1。"""


async def _get(client: httpx.AsyncClient, url: str, **params) -> httpx.Response:
    r = await client.get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r


def _licence_ok(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in OPEN_LICENSES) and "nc" not in low.split() and "-nc" not in low and "nd" not in low.split()


async def search(client: httpx.AsyncClient, query: str, limit: int = 8, width: int = 360) -> list[Candidate]:
    data = (
        await _get(
            client, COMMONS, action="query", generator="search", gsrsearch=f"{query} filetype:bitmap", gsrnamespace=6, gsrlimit=limit,
            prop="imageinfo", iiprop="url|mime|size|extmetadata", iiurlwidth=width, format="json",
        )
    ).json()
    out = []
    for p in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        lic = str(md.get("LicenseShortName", {}).get("value", ""))
        if ii.get("mime") not in IMAGE_MIMES or ii.get("width", 0) < 300 or not _licence_ok(lic):
            continue
        artist = str(md.get("Artist", {}).get("value", ""))
        out.append(Candidate(p["title"], ii.get("descriptionurl", ""), ii.get("thumburl", ""), lic, _strip_html(artist), ii["width"], ii["height"]))
    return out


WEB_LICENCE = "网页图片，版权归原作者，只在本机使用"


async def web_images(client: httpx.AsyncClient, query: str, limit: int = 8) -> list[Candidate]:
    """Pictures from a web image search: Tavily when there is a key, else ddgs. They carry
    no licence, so they are only for what stays on this machine (see fetch_folder)."""
    found: list[tuple[str, str, str]] = []  # (image url, page, title)
    if key := os.environ.get("TAVILY_API_KEY"):
        r = await client.post(
            "https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"}, timeout=30,
            json={"query": query, "include_images": True, "include_image_descriptions": True, "max_results": 5},
        )
        r.raise_for_status()
        for item in r.json().get("images", []):
            url, said = (item, "") if isinstance(item, str) else (item.get("url") or "", item.get("description") or "")  # either may be null
            found.append((url, url, said[:80]))
    else:
        import importlib.util

        if importlib.util.find_spec("ddgs"):
            from ddgs import DDGS

            rows = await asyncio.to_thread(lambda: DDGS().images(query, max_results=limit))
            found += [(r.get("image", ""), r.get("url", ""), r.get("title", "")[:80]) for r in rows or []]
    return [Candidate(title or url.rsplit("/", 1)[-1][:80], page, url, WEB_LICENCE, "", 0, 0) for url, page, title in found if url.startswith("http")][:limit]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()[:120]


def contact_sheet(images: list[Image.Image], cell: int = 300) -> bytes:
    cols = 3
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for i, im in enumerate(images):
        thumb = ImageOps.contain(im.convert("RGB"), (cell - 8, cell - 8))
        x, y = (i % cols) * cell + 4, (i // cols) * cell + 4
        sheet.paste(thumb, (x, y))
        draw.rectangle([x, y, x + 34, y + 30], fill=(255, 200, 0))
        draw.text((x + 10, y + 6), str(i), fill=(0, 0, 0))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def prepare(im: Image.Image, crop: list[float]) -> Image.Image:
    """Apply the vision agent's crop box; everything else happens in preprocess()."""
    im = im.convert("RGB")
    w, h = im.size
    x0, y0, x1, y1 = (max(0.0, min(1.0, v)) for v in [*crop, 0, 0, 1, 1][:4])
    if x1 - x0 > 0.1 and y1 - y0 > 0.1:
        im = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    return im


# -- preprocessing ------------------------------------------------------------------
# Each step is one call into OpenCV, rembg or Pillow. Portraits get their background cut
# out so the face reads on a dark terminal; scenes get local contrast and an edge-keeping
# smoothing that calms grain and leaves. Scenes are not quantized or sharpened: fewer
# colours and sharper texture turned the character grid into a grey mosaic. Their vignette
# comes from light(), after frame() has picked the part of the picture that shows.
PIPELINES: dict[str, tuple[str, ...]] = {
    "portrait": ("face_crop", "remove_bg", "clahe", "saturate", "bilateral", "unsharp", "vignette"),
    "scene": ("clahe", "bilateral", "saturate"),
}
BACKDROP = (6, 10, 12)  # BGR of the terminal background the art sits on


def _face_crop(arr):
    import cv2

    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        return arr
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    H, W = arr.shape[:2]
    x0, x1 = max(0, int(x - 0.9 * w)), min(W, int(x + 1.9 * w))
    y0, y1 = max(0, int(y - 0.7 * h)), min(H, int(y + 2.1 * h))
    return arr[y0:y1, x0:x1]


@lru_cache(maxsize=2)
def _rembg_session(model: str):
    from rembg import new_session

    return new_session(model)


def _remove_bg(arr):
    import cv2
    import numpy as np
    from rembg import remove

    rgba = np.array(remove(Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)), session=_rembg_session("u2net_human_seg")))
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    fg = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR).astype(np.float32)
    bg = np.empty_like(fg)
    bg[:] = BACKDROP
    return (fg * alpha + bg * (1 - alpha)).astype(np.uint8)


def _clahe(arr):
    import cv2

    lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)
    light, a, b = cv2.split(lab)
    light = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(light)
    return cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2BGR)


def _bilateral(arr):
    import cv2

    return cv2.bilateralFilter(arr, 9, 60, 60)


def _saturate(arr, factor: float = 1.3):
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = (hsv[:, :, 1] * factor).clip(0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _quantize(arr, k: int = 20):
    import cv2
    import numpy as np

    data = arr.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    _, labels, centers = cv2.kmeans(data, k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    return centers.astype(np.uint8)[labels.flatten()].reshape(arr.shape)


def _unsharp(arr):
    import cv2

    return cv2.addWeighted(arr, 1.6, cv2.GaussianBlur(arr, (0, 0), 2.0), -0.6, 0)


def _vignette(arr, strength: float = 0.45):
    import cv2
    import numpy as np

    h, w = arr.shape[:2]
    mask = cv2.getGaussianKernel(h, h * 0.65) @ cv2.getGaussianKernel(w, w * 0.65).T
    mask = mask / mask.max()
    mask = (1 - strength) + strength * mask
    return (arr.astype(np.float32) * mask[:, :, None]).clip(0, 255).astype(np.uint8)


STEPS = {
    "face_crop": _face_crop,
    "remove_bg": _remove_bg,
    "clahe": _clahe,
    "saturate": _saturate,
    "bilateral": _bilateral,
    "quantize": _quantize,
    "unsharp": _unsharp,
    "vignette": _vignette,
}


def preprocessing_available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def preprocess(src: Path, kind: str, steps: tuple[str, ...] | None = None, *, keep_stages: bool = False) -> Path:
    """Run the pipeline for `kind` and cache the result next to the source as <name>.prep.png."""
    src = Path(src)
    out = src.with_name(src.stem + ".prep.png")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime and not keep_stages:
        return out
    im = Image.open(src).convert("RGB")
    if not preprocessing_available():
        im = ImageEnhance.Contrast(ImageOps.autocontrast(im, cutoff=1)).enhance(1.1)
        im.save(out)
        return out
    import cv2
    import numpy as np

    arr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    for i, step in enumerate(steps or PIPELINES.get(kind, PIPELINES["scene"])):
        try:
            arr = STEPS[step](arr)
        except Exception:  # a missing model or odd image skips that step, never the picture
            continue
        if keep_stages:
            cv2.imwrite(str(src.with_name(f"{src.stem}.{i}-{step}.png")), arr)
    cv2.imwrite(str(out), arr)
    return out


async def _structured(cfg: Config, role: str, system: str, prompt: str, schema: type[BaseModel], images: list[bytes] | None = None, on_reply=None):
    s = Session(role, cfg.role(role), system, schema=schema.model_json_schema())
    try:
        reply = await s.send(prompt, images=images)
        if on_reply:
            on_reply(role, reply)
        return schema.model_validate(reply.data or {})
    finally:
        await s.close()


async def find_picture(
    cfg: Config, client: httpx.AsyncClient, subject: str, kind: str, *, on_reply=None, web: bool = False,
) -> tuple[Image.Image, Candidate] | None:
    """Commons first (licensed); with `web`, a web image search when Commons has nothing that fits."""
    label = {"portrait": "人物头像", "scene": "场景", "item": "物件特写"}.get(kind, "场景")
    q = await _structured(cfg, "vision", QUERY_SYSTEM, f"{label}：{subject}", Queries, on_reply=on_reply)  # art: Opus
    seen: dict[str, Candidate] = {}
    queries = list(q.queries[:3])
    for query in queries:
        for c in await search(client, query):
            seen.setdefault(c.title, c)
    if len(seen) < 3:  # broaden: the first two words of each query
        for query in {" ".join(x.split()[:2]) for x in queries}:
            for c in await search(client, query):
                seen.setdefault(c.title, c)
    found = await _pick(cfg, client, list(seen.values())[:6], subject, kind, label, on_reply)
    if found is None and web:
        pictures: dict[str, Candidate] = {}
        for query in queries[:2]:
            try:
                for c in await web_images(client, query + (" vintage photograph" if kind == "portrait" else "")):
                    pictures.setdefault(c.thumb, c)
            except httpx.HTTPError:
                continue
        found = await _pick(cfg, client, list(pictures.values())[:6], subject, kind, label, on_reply)
    return found


async def _pick(cfg, client, cands: list[Candidate], subject: str, kind: str, label: str, on_reply) -> tuple[Image.Image, Candidate] | None:
    """The vision model's choice from a contact sheet, cropped; None if nothing fits."""
    shown, thumbs = [], []
    for c in cands:  # a picture that will not download or open is left out
        try:
            thumbs.append(Image.open(io.BytesIO((await _get(client, c.thumb)).content)).convert("RGB"))
            shown.append(c)
        except (httpx.HTTPError, OSError, ValueError):
            continue
    if not shown:
        return None
    stand_in = "\n这是虚构人物，不必是本人：年代、性别、年龄段和身份气质大致对得上就选，脸要清楚。" if kind == "portrait" else ""
    pick = await _structured(
        cfg, "vision", PICK_SYSTEM, f"描述（{label}）：{subject}\n候选编号 0–{len(shown) - 1}。{stand_in}",
        Pick, images=[contact_sheet(thumbs)], on_reply=on_reply,
    )
    if not 0 <= pick.index < len(shown):
        return None
    chosen = shown[pick.index]
    big = chosen.thumb.replace("/360px-", "/1024px-") if "/360px-" in chosen.thumb else chosen.thumb
    try:
        im = Image.open(io.BytesIO((await _get(client, big)).content)) if big != chosen.thumb else thumbs[pick.index]
    except (httpx.HTTPError, OSError):
        im = thumbs[pick.index]
    return prepare(im, pick.crop), chosen


def targets(m) -> list[tuple[str, str, str]]:
    """What a module wants photos for: every scene and face that has an art key, described
    with the era so the search finds a 1920s street, not today's (module.schema.Module)."""
    era = f"{m.era}，{m.setting}"
    out = [(loc.art, f"{loc.name}（{era}）：{loc.read_aloud.strip()[:160]}", "scene") for loc in m.locations if loc.art]
    out += [(n.portrait, f"{n.name}，{n.role}；{n.look}（{era}）", "portrait") for n in m.npcs if n.portrait]
    out += [(p.portrait, f"{p.name}，{p.occupation}，{p.age} 岁，{p.backstory.get('个人描述', '')}（{era}）", "portrait") for p in m.pregens if p.portrait]
    return out


def private(folder: Path) -> bool:
    """A folder that stays on this machine (an imported module, a save), where a web picture
    without a licence may go; the bundled modules in the repository get licensed ones only."""
    from . import config

    where = folder.resolve()
    return any(where.is_relative_to(p.resolve()) for p in (config.LOCAL_MODULES, config.SAVES))


async def fetch_folder(cfg: Config, art_dir: Path, targets: list[tuple[str, str, str]], *, force: bool = False, parallel: int = 3, log=print, on_reply=None) -> list[str]:
    """targets: (name, subject, kind) with kind 'scene' or 'portrait'. Writes <name>.jpg and <name>.credit.json."""
    art_dir.mkdir(parents=True, exist_ok=True)
    web = private(art_dir) and cfg.search != "none"
    sem = asyncio.Semaphore(parallel)
    done: list[str] = []
    async with httpx.AsyncClient() as client:

        async def one(name: str, subject: str, kind: str) -> None:
            if (art_dir / f"{name}.jpg").exists() and not force:
                return
            async with sem:
                try:
                    if on_reply is None:
                        found = await find_picture(cfg, client, subject, kind, web=web)
                    else:
                        found = await find_picture(cfg, client, subject, kind, on_reply=on_reply, web=web)
                except Exception as exc:  # one bad download should not stop the batch
                    log(f"  ✗ {name}: {exc.__class__.__name__}")
                    return
            if not found:
                log(f"  · {name}: 没有合适的图，保留手绘")
                return
            im, c = found
            im.save(art_dir / f"{name}.jpg", quality=92)
            (art_dir / f"{name}.credit.json").write_text(json.dumps(asdict(c) | {"kind": kind}, ensure_ascii=False, indent=1), encoding="utf-8")
            await asyncio.to_thread(preprocess, art_dir / f"{name}.jpg", kind)
            done.append(name)
            log(f"  ✓ {name}: {c.title}（{c.license}）")

        await asyncio.gather(*(one(*t) for t in targets))
    return done


# -- rendering ------------------------------------------------------------------
def available() -> bool:
    return shutil.which("chafa") is not None


def _chafa(source: str | Image.Image, width: int, height: int, style: str, *, work: int = 9) -> str:
    """chafa on a file, or on an in-memory image piped through stdin."""
    data = None
    if isinstance(source, Image.Image):
        buf = io.BytesIO()
        source.save(buf, format="PNG")
        data, source = buf.getvalue(), "-"
    cmd = ["chafa", "-f", "symbols", "-c", "full", "--work", str(work), "--size", f"{width}x{height}", *STYLES.get(style, STYLES["picture"]), source]
    out = subprocess.run(cmd, input=data, capture_output=True, timeout=20).stdout.decode("utf-8", "replace")
    return out.replace("\x1b[?25l", "").replace("\x1b[?25h", "").rstrip("\n")


def picture_source(path: str | Path) -> str:
    """The preprocessed image if there is one, else the original."""
    p = Path(path)
    prep = p.with_name(p.stem + ".prep.png")
    return str(prep if prep.exists() else p)


def light(im: Image.Image, when: str) -> Image.Image:
    """Grade a picture for the time of day (artkit.LIGHTS): exposure, colour, contrast, a
    tint, and a vignette that closes in after dark."""
    if when not in artkit.LIGHTS:
        return im
    return _expose(im, *artkit.LIGHTS[when])


def _expose(
    im: Image.Image, bright: float, sat: float, contrast: float, tint: tuple[int, int, int] | None, amount: float, vignette: float,
    *, glaze: bool = False,
) -> Image.Image:
    """One row of artkit.LIGHTS (an hour) or artkit.TONES (a mood) on a picture. An hour's tint
    fills the air; a mood's is a `glaze` over what is lit, so a portrait's dark backdrop stays
    dark instead of turning brown or red."""
    im = ImageEnhance.Contrast(ImageEnhance.Color(ImageEnhance.Brightness(im).enhance(bright)).enhance(sat)).enhance(contrast)
    if tint and amount:
        top = max(tint)
        colour = ImageChops.multiply(im, Image.new("RGB", im.size, tuple(255 * c // top for c in tint))) if glaze else Image.new("RGB", im.size, tint)
        im = Image.blend(im, colour, amount)
    if vignette:
        mask = Image.radial_gradient("L").resize(im.size).point(lambda v: int(v * vignette))
        im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im, mask)
    return im


def frame(im: Image.Image, cols: int, rows: int, *, cell: float = 0.5, widest: float = 4.0) -> Image.Image:
    """Crop a scene to the panel's shape so it fills it (terminal cells are about half as wide
    as tall): squeezed in whole, a photo becomes a strip of mosaic too small to read. The crop
    slides to the band with the most coherent structure (walls, stones, shelves rather than
    foliage), pulled a little towards the middle. Panels wider than `widest` get a letterbox."""
    target = min(widest, cols * cell / rows)
    w, h = im.size
    sideways = w / h > target
    share = h * target / w if sideways else w / target / h  # the window's part of the picture
    if share > 0.98:
        return im
    across = max(1, round(rows * target / cell))  # the window's width in cells
    grid = (round(across / share), rows) if sideways else (across, round(rows / share))
    strength = _line_strength(im, *grid).sum(axis=0 if sideways else 1)
    sums = np.convolve(strength, np.ones(across if sideways else rows), "valid") + 1e-9  # flat: the middle
    best = int(np.argmax(sums * (1 - 0.3 * np.abs(np.linspace(-1, 1, len(sums))))))
    if sideways:
        nw = round(h * target)
        left = min(w - nw, round(best * w / len(strength)))
        return im.crop((left, 0, left + nw, h))
    nh = round(w / target)
    top = min(h - nh, round(best * h / len(strength)))
    return im.crop((0, top, w, top + nh))


def grade(im: Image.Image, name: str, when: str = "") -> Image.Image:
    """A copy for the weather layer: torchlight and flicker exposures, fog, lightning. Or a
    portrait in a speaker's mood (artkit.TONES)."""
    if name in artkit.TONES:
        return _expose(im, *artkit.TONES[name], glaze=True)
    if name in artkit.GRADES:
        return ImageEnhance.Brightness(im).enhance(artkit.GRADES[name])
    if name in artkit.HAZE:
        return Image.blend(im, Image.new("RGB", im.size, artkit.fog_colour(when)), artkit.HAZE[name])
    if name == "flash":
        return Image.blend(ImageEnhance.Brightness(im).enhance(1.6), Image.new("RGB", im.size, artkit.FLASH), 0.55)
    if name == "red":
        return ImageOps.colorize(im.convert("L"), black=(18, 0, 0), white=(255, 70, 48))
    return im


# -- outlines -------------------------------------------------------------------------
# A photo in a few dozen cells keeps its colours but loses its shapes, so scenes get ASCII
# strokes along their lines, the recipe of ASCII shaders like Acerola's: the direction of the
# picture's gradient picks | / - \ _ for a cell. A cell's gradients are summed into a structure
# tensor, the standard way to read one direction off a patch; where they agree (coherent) the
# cell sits on a line, while foliage and grain point every way and get no stroke. numpy and
# Pillow only, so outlines work without the art extra's OpenCV.
CELL_PX = (8, 16)  # pixels per cell when looking for lines; terminal cells are twice as tall
GLYPHS = np.array(["", "-", "/", "|", "\\", "_"])
ACROSS = {1: ((-1, 0), (1, 0)), 2: ((-1, -1), (1, 1)), 3: ((0, -1), (0, 1)), 4: ((-1, 1), (1, -1))}  # each line's two sides
ALONG = {  # where the line through a cell can go on, and which strokes may carry it there
    1: (((0, -1), (0, 1)), (1, 2, 4)),
    2: (((-1, 1), (1, -1), (0, -1), (0, 1), (-1, 0), (1, 0)), (2, 1, 3)),
    3: (((-1, 0), (1, 0), (-1, -1), (-1, 1), (1, -1), (1, 1)), (3, 2, 4)),
    4: (((-1, -1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0)), (4, 1, 3)),
}
PAPER = "#e8dec4"  # light strokes on dark cells, lit like the hand-drawn art's lines


def _neighbour(a: np.ndarray, dy: int, dx: int, fill=0) -> np.ndarray:
    """a[y + dy, x + dx] at (y, x), `fill` past the edge."""
    h, w = a.shape
    return np.pad(a, 1, constant_values=fill)[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]


def _gradients(im: Image.Image, cols: int, rows: int) -> tuple[np.ndarray, np.ndarray]:
    cw, ch = CELL_PX
    grey = im.convert("L").resize((cols * cw, rows * ch), Image.Resampling.BOX).filter(ImageFilter.GaussianBlur(2.5))
    gy, gx = np.gradient(np.asarray(grey, dtype=np.float32) / 255)
    return gx, gy


def _cells(v: np.ndarray, cols: int, rows: int) -> np.ndarray:
    return v.reshape(rows, CELL_PX[1], cols, CELL_PX[0]).sum(axis=(1, 3))


def _coherence(jxx: np.ndarray, jyy: np.ndarray, jxy: np.ndarray) -> np.ndarray:
    """0 where the gradients point every way, 1 where they all lie along one line."""
    return np.sqrt((jxx - jyy) ** 2 + 4 * jxy**2) / (jxx + jyy + 1e-9)


def _line_strength(im: Image.Image, cols: int, rows: int) -> np.ndarray:
    gx, gy = _gradients(im, cols, rows)
    jxx, jyy, jxy = (_cells(v, cols, rows) for v in (gx * gx, gy * gy, gx * gy))
    return (jxx + jyy) * _coherence(jxx, jyy, jxy) ** 2


def outlines(im: Image.Image, cols: int, rows: int, *, keep: float = 0.3) -> np.ndarray:
    """The stroke for each cell of a cols × rows grid laid over the picture: | / - \\ or _
    where a clear line crosses the cell, "" elsewhere. At most `keep` of the cells get one."""
    gx, gy = _gradients(im, cols, rows)
    jxx, jyy, jxy = (_cells(v, cols, rows) for v in (gx * gx, gy * gy, gx * gy))
    energy = jxx + jyy
    around = [sum(_neighbour(j, dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)) for j in (jxx, jyy, jxy)]
    on = (energy >= max(float(np.quantile(energy, 1 - keep)), 0.01)) & (_coherence(jxx, jyy, jxy) >= 0.45) & (_coherence(*around) >= 0.4)
    # The line runs across the gradient; its angle on screen (y up) picks the stroke.
    angle = (90 - np.degrees(np.arctan2(2 * jxy, jxx - jyy)) / 2) % 180
    way = np.digitize(angle, (30, 72, 108, 150)) % 4 + 1  # index into GLYPHS: - / | or backslash
    for k, (a, b) in ACROSS.items():  # one stroke wide: a cell must beat its neighbours across its line
        on &= (way != k) | (energy >= np.maximum(_neighbour(energy, *a), _neighbour(energy, *b)))
    for k in (2, 4):  # a slanted line crosses two cells per row; the blur would make it three
        same = np.where(way == k, energy, 0)
        on &= (way != k) | (energy >= 0.75 * np.maximum(_neighbour(same, 0, -1), _neighbour(same, 0, 1)))
    drawn = np.where(on, way, 0)
    carried = np.zeros_like(on)
    for k, (steps, fits) in ALONG.items():  # no lone specks: the line goes on into a neighbour
        fit = np.isin(drawn, fits)
        carried |= (drawn == k) & np.logical_or.reduce([_neighbour(fit, dy, dx, False) for dy, dx in steps])
    rows_gy = (gy * gy).reshape(rows, CELL_PX[1], cols, CELL_PX[0]).sum(axis=3)
    depth = (rows_gy * np.linspace(0, 1, CELL_PX[1])[None, :, None]).sum(axis=1) / (rows_gy.sum(axis=1) + 1e-9)
    way = np.where((way == 1) & (depth > 0.6), 5, way)  # a horizontal line low in its cell: _
    return np.where(carried, GLYPHS[way], "")


def ink(colours: np.ndarray, when: str = "") -> np.ndarray:
    """A stroke colour for each cell colour (rows × cols × RGB): dark on light cells, light on
    dark ones. The light ink is lit like hand-drawn strokes (artkit.shade), so after dark the
    lines stay readable over a picture that has gone almost black."""
    colours = colours.astype(np.float32)
    paper = np.array(ImageColor.getrgb(artkit.shade(PAPER, when, strokes=True)), dtype=np.float32)
    light_cell = (colours @ np.array([0.299, 0.587, 0.114], dtype=np.float32) > 110)[..., None]
    return np.where(light_cell, colours * 0.25, colours * 0.2 + paper * 0.8).clip(0, 255).astype(np.uint8)


# chafa writes a cell as its character, after an SGR when the colours change: 24-bit colours
# and resets, nothing else. overlay() splices strokes into that text instead of decoding it with
# rich: a scene has a few thousand cells, decoded again for every weather layer.
RGB = tuple[int, ...]
_TOKEN = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z])|(.)", re.S)  # an escape, or a cell's character
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _sgr(fg: RGB | None, bg: RGB | None, *, bold: bool = False) -> str:
    codes = ["0", "1"] if bold else ["0"]
    codes += [f"{n};2;{';'.join(map(str, rgb))}" for n, rgb in ((38, fg), (48, bg)) if rgb]
    return f"\x1b[{';'.join(codes)}m"


def _colours(escapes: str, fg: RGB | None, bg: RGB | None) -> tuple[RGB | None, RGB | None]:
    """The colours chafa's escapes leave set (None: the terminal's own)."""
    for params in _SGR.findall(escapes):
        codes = [int(c) for c in params.split(";") if c] or [0]
        i = 0
        while i < len(codes):
            if codes[i] == 0:
                fg = bg = None
            elif codes[i] in (38, 48) and codes[i + 1 : i + 2] == [2]:
                rgb = tuple(codes[i + 2 : i + 5])
                fg, bg = (rgb, bg) if codes[i] == 38 else (fg, rgb)
                i += 4
            i += 1
    return fg, bg


def overlay(art: str, strokes: np.ndarray, under: np.ndarray, colour: np.ndarray) -> str:
    """chafa's art (ANSI) with the strokes drawn over it. A stroke cell shows its glyph in
    `colour` on `under` (the picture's colour there), then chafa's colours are set back for
    the cells after it, which are left exactly as chafa wrote them."""
    rows, cols = strokes.shape
    fg = bg = None
    lines = []
    for y, line in enumerate(art.split("\n")):
        pieces, x = [], 0
        for escape, ch in _TOKEN.findall(line):
            if escape:
                fg, bg = _colours(escape, fg, bg)
                pieces.append(escape)
                continue
            glyph = strokes[y, x] if y < rows and x < cols else ""
            if glyph:
                ch = _sgr(tuple(map(int, colour[y, x])), tuple(map(int, under[y, x])), bold=True) + glyph + _sgr(fg, bg)
            pieces.append(ch)
            x += 1
        lines.append("".join(pieces))
    return "\n".join(lines)


@lru_cache(maxsize=32)
def _framed(path: str, width: int, height: int, fill: bool, face: bool = False) -> Image.Image:
    im = Image.open(picture_source(path)).convert("RGB")
    if face:
        return frame_face(im, width, height)
    return frame(im, width, height) if fill else im


def _find_face(im: Image.Image) -> tuple[int, int, int, int] | None:
    """The largest face (x, y, w, h), when OpenCV is installed and sees one. Finding it is a
    nicety: anything going wrong just means the default framing."""
    try:
        import cv2

        gray = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    except Exception:  # not installed, a stripped build, an odd image
        return None
    return tuple(int(v) for v in max(faces, key=lambda f: f[2] * f[3])) if len(faces) else None  # type: ignore[return-value]


FACE_SHAPE = 0.9  # width over height of a head-and-shoulders crop, about a passport photo


def _trim_backdrop(im: Image.Image, margin: float = 0.04) -> Image.Image:
    """The person without the plain backdrop round them: a portrait with its background cut
    out (preprocess) stands in a wide dark field, which on the stage reads as a hole."""
    arr = np.asarray(im.convert("RGB")).astype(np.int16)
    ground = np.abs(arr - np.array(BACKDROP[::-1], dtype=np.int16)).sum(axis=2) > 30  # BACKDROP is BGR
    rows, cols = np.flatnonzero(ground.any(axis=1)), np.flatnonzero(ground.any(axis=0))
    if not len(rows) or not len(cols):
        return im
    h, w = ground.shape
    pad_x, pad_y = round(w * margin), round(h * margin)
    box = (max(0, cols[0] - pad_x), max(0, rows[0] - pad_y), min(w, cols[-1] + 1 + pad_x), min(h, rows[-1] + 1 + pad_y))
    return im.crop(box) if (box[2] - box[0]) * (box[3] - box[1]) < 0.9 * w * h else im


def frame_face(im: Image.Image, cols: int, rows: int, *, cell: float = 0.5) -> Image.Image:
    """Crop a portrait round the face to a head-and-shoulders shape (the panel's, if that is
    narrower), so a tall old studio photo no longer stands in the speaker's panel as a thin
    strip between two black bands, and a wide panel does not get a face spread across it.
    Without a face found, heads are taken to sit in the upper third."""
    target = min(cols * cell / max(1, rows), FACE_SHAPE)  # width over height, in pixels
    im = _trim_backdrop(im)
    w, h = im.size
    if not w or not h or abs(w / h - target) < 0.05:
        return im
    found = _find_face(im)
    cx, cy = (found[0] + found[2] / 2, found[1] + found[3] / 2) if found else (w / 2, h * 0.33)
    if w / h > target:  # wider than the panel: trim the sides
        nw = h * target
        left = min(max(0.0, cx - nw / 2), w - nw)
        return im.crop((round(left), 0, round(left + nw), h))
    nh = w / target  # taller: keep the head and shoulders, a little more below the face than above
    top = min(max(0.0, cy - nh * 0.4), h - nh)
    return im.crop((0, round(top), w, round(top + nh)))


@lru_cache(maxsize=32)
def _strokes(path: str, width: int, height: int, cols: int, rows: int) -> np.ndarray:
    """A scene's strokes, found on the unlit picture: the same lines at every hour and in every
    weather layer, only their colours change."""
    return outlines(_framed(path, width, height, True), cols, rows)


def _prepared(path: str, width: int, height: int, when: str, fill: bool, graded: str = "") -> Image.Image:
    return grade(light(_framed(path, width, height, fill), when), graded, when)


@lru_cache(maxsize=256)
def render(path: str, width: int, height: int, style: str = "picture", when: str = "", fill: bool = False, graded: str = "", face: bool = False) -> str:
    """ANSI text for a picture at the given character size, lit for `when`, optionally
    cropped to fill the panel and graded for the weather layer (cached per combination).
    Scenes (`fill`) in an outlined style get strokes along their lines, drawn into every
    weather layer so fog veils them and the dark outside a torch beam swallows them."""
    if face:  # a portrait: cropped round the face to the panel's shape, then coloured as asked
        return _chafa(grade(light(_framed(path, width, height, False, face=True), when), graded, when), width, height, style)
    if not when and not fill and not graded:
        return _chafa(picture_source(path), width, height, style)
    lit = light(_framed(path, width, height, fill), when)
    im = grade(lit, graded, when)
    out = _chafa(im, width, height, style)
    if not (fill and style in OUTLINED and out):
        return out
    lines = _ESCAPES.sub("", out).split("\n")
    size = (max(map(len, lines)), len(lines))  # chafa keeps the aspect, so maybe a cell short
    under = np.asarray(im.resize(size, Image.Resampling.BOX))
    stroke = grade(Image.fromarray(ink(np.asarray(lit.resize(size, Image.Resampling.BOX)), when)), graded, when)
    return overlay(out, _strokes(path, width, height, *size), under, np.asarray(stroke))


def glitch(path: str, width: int, height: int, strength: float, style: str = "picture", when: str = "", fill: bool = False) -> str:
    """A glitched frame: RGB split and torn scanlines, rendered fresh (not cached)."""
    im = _prepared(path, width, height, when, fill)
    r, g, b = im.split()
    shift = max(1, int(im.width * 0.02 * strength))
    im = Image.merge("RGB", (r.transform(r.size, Image.Transform.AFFINE, (1, 0, shift, 0, 1, 0)), g, b.transform(b.size, Image.Transform.AFFINE, (1, 0, -shift, 0, 1, 0))))
    for _ in range(int(10 * strength) + 1):
        y = random.randrange(im.height)
        band = im.crop((0, y, im.width, min(im.height, y + random.randint(2, 12))))
        im.paste(band, (random.randint(-shift * 4, shift * 4), y))
    return _chafa(im, width, height, style, work=3)


def credits(art_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) | {"name": p.name.split(".")[0]} for p in sorted(Path(art_dir).glob("*.credit.json"))]
