"""The artist: draws ASCII art and paints colour masks for it (Claude by default)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from .. import artkit, skills
from ..config import Config
from ..harness import Reply, Session


class Mask(BaseModel):
    mask: str = Field(description="与原图同形状的配色蒙版，每行长度与原图对应行相同")


class Drawing(BaseModel):
    art: str = Field(description="ASCII 图")
    mask: str = Field(description="同形状的配色蒙版")


SYSTEM = f"""你是 AutoKeeper 的美术，专画终端里的 ASCII 图，并给它上色。

{skills.wrap("ascii-art")}

配色蒙版：与图同形状的文本，每个字符是一个颜色键，“.”表示用默认色。可用颜色键：
{artkit.PALETTE_DOC}
上色要有氛围：灯火用 o/y，木头用 n/N，水和风用 c/b，夜色用 B，血用 R，诡异的记号和光用 p/P/m，人脸用 s，衣服挑一两种主色，背景结构用 k/K 压暗，让视线落在主体上。"""


async def color(cfg: Config, name: str, text: str, hint: str = "") -> str:
    session = Session("artist", cfg.role("artist"), SYSTEM, schema=Mask.model_json_schema())
    try:
        lines = text.split("\n")
        prompt = (
            f"给这张图上色：{name}。{hint}\n"
            f"图共 {len(lines)} 行，各行长度依次为 {[len(x) for x in lines]}。\n"
            "原图（放在代码块里，代码块本身不算）：\n```\n" + text + "\n```\n"
            "只输出蒙版，行数和每行长度必须和原图一致，空格处也要填“.”。"
        )
        reply = await session.send(prompt)
        data = reply.data or {}
        return artkit.fit_mask(text, str(data.get("mask", "")))
    finally:
        await session.close()


async def draw(cfg: Config, subject: str, *, portrait: bool = False, on_reply: Callable[[Reply], None] | None = None) -> tuple[str, str]:
    w, h = artkit.MAX_PORTRAIT if portrait else artkit.MAX_SCENE
    session = Session("artist", cfg.role("artist"), SYSTEM, schema=Drawing.model_json_schema())
    try:
        prompt = f"画一张{'人物头像' if portrait else '场景或物件插图'}：{subject}\n宽不超过 {w} 列，高不超过 {h} 行，只用 ASCII 字符。同时给出配色蒙版。"
        for _ in range(2):
            reply = await session.send(prompt)
            if on_reply:
                on_reply(reply)
            data = reply.data or {}
            text = str(data.get("art", "")).rstrip("\n")
            mask = artkit.fit_mask(text, str(data.get("mask", "")))
            issues = artkit.check("illustration", text, mask, portrait=portrait)
            if text.strip() and not issues:
                return text, mask
            prompt = "请修复并重新输出完整的 art 和 mask：" + ("；".join(i.text for i in issues) or "图画不能为空")
        raise ValueError("Artist returned an invalid illustration twice")
    finally:
        await session.close()


async def color_folder(cfg: Config, art_dir: Path, hints: dict[str, str], *, force: bool = False, parallel: int = 4) -> list[str]:
    """Paint masks for every art/*.txt that has none yet."""
    sem = asyncio.Semaphore(parallel)
    done: list[str] = []

    async def one(path: Path) -> None:
        mask_path = path.with_suffix(".color")
        if mask_path.exists() and not force:
            return
        async with sem:
            text = path.read_text(encoding="utf-8").rstrip("\n")
            mask = await color(cfg, path.stem, text, hints.get(path.stem, ""))
            mask_path.write_text(mask + "\n", encoding="utf-8")
            done.append(path.stem)

    await asyncio.gather(*(one(p) for p in sorted(art_dir.glob("*.txt"))))
    return done
