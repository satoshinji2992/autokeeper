"""Prompt variants for evaluations: the same code with one prompt swapped, nothing edited on disk.

An A/B of a prompt has to change that prompt and nothing else. A variant is a YAML file that
names what it changes; `autokeeper eval --prompts FILE ...` puts it in place for that one run,
and the reports say which variant they were made with (current()).

    note: 守秘人第 10 条换成 5c24ee0 的说法
    replace:                       # exact text; each `old` must occur once in its target
      KEEPER_SYSTEM:
        - old: 失败只说明本次尝试未达成，不额外制造敌意、伤害或丢失物品。
          new: 失败的代价以本轮结算为准，已结算的敌意、伤害或物品损失照实呈现，不自行追加。
    files:                         # whole texts, paths relative to the variant file
      keeper-craft: keeper-craft-mix.md      # a skill's SKILL.md (front matter optional)
      NPC_SYSTEM: npc-system-02.txt          # a prompt of agents/cast.py

A target is a prompt constant of agents/cast.py (KEEPER_SYSTEM, NPC_SYSTEM, ARBITER_SYSTEM, ...)
or a shared skill by its folder name (keeper-craft). A prompt that is filled in with .format()
keeps the same {fields}, or the variant is refused before anything runs.
"""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import skills
from ..agents import cast

_ACTIVE: list[Variant] = []


@dataclass
class Variant:
    name: str
    note: str = ""
    texts: dict[str, str] = field(default_factory=dict)  # target -> its whole text as the variant has it

    @property
    def digest(self) -> str:
        body = "\n".join(f"{k}\n{v}" for k, v in sorted(self.texts.items()))
        return hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]


def _is_prompt(target: str) -> bool:
    return target.isupper() and isinstance(getattr(cast, target, None), str)


def original(target: str) -> str:
    """The target as the code has it now (a skill without its front matter)."""
    if _is_prompt(target):
        return getattr(cast, target)
    if (skills.path(target) / "SKILL.md").is_file():
        return _strip(skills.path(target).joinpath("SKILL.md").read_text(encoding="utf-8"))
    raise ValueError(f"不知道要换的是什么：{target}（agents/cast.py 里的 *_SYSTEM，或 skills/ 下的技能名）")


def _strip(text: str) -> str:
    return text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}


def load(path: str | Path) -> Variant:
    """A variant file read and checked against the code as it is: every replacement found once,
    every prompt keeping its {fields}."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    v = Variant(name=path.stem, note=str(data.get("note", "")))
    for target, rel in (data.get("files") or {}).items():
        text = (path.parent / rel).read_text(encoding="utf-8")
        v.texts[target] = text if _is_prompt(target) else _strip(text)
    for target, pairs in (data.get("replace") or {}).items():
        text = v.texts.get(target, original(target))
        for pair in pairs or []:
            old, new = str(pair["old"]), str(pair["new"])
            if text.count(old) != 1:
                raise ValueError(f"{path.name}：{target} 里「{old[:30]}」出现了 {text.count(old)} 次，要正好一次")
            text = text.replace(old, new)
        v.texts[target] = text
    for target, text in v.texts.items():
        before = original(target)
        if _is_prompt(target) and _fields(text) != _fields(before):
            raise ValueError(f"{path.name}：{target} 的占位符变了（{sorted(_fields(before))} → {sorted(_fields(text))}）")
    return v


def apply(v: Variant) -> None:
    """Put the variant in place for this process; undo() takes it back."""
    if _ACTIVE:
        raise RuntimeError("已经换过一组提示词了")
    saved = {t: getattr(cast, t) for t in v.texts if _is_prompt(t)}
    for target, text in v.texts.items():
        if _is_prompt(target):
            setattr(cast, target, text)
    swapped = {t: text for t, text in v.texts.items() if not _is_prompt(t)}
    body = skills.body

    def swapped_body(name: str) -> str:
        return swapped[name] if name in swapped else body(name)

    if swapped:
        skills.body = swapped_body  # skills.wrap and every reader look it up when called
    v._undo = (saved, body)  # type: ignore[attr-defined]
    _ACTIVE.append(v)


def undo() -> None:
    while _ACTIVE:
        v = _ACTIVE.pop()
        saved, body = v._undo  # type: ignore[attr-defined]
        for target, text in saved.items():
            setattr(cast, target, text)
        skills.body = body


def current() -> dict[str, str]:
    """Which variant this process runs with, for a report: {} for the code as it is."""
    return {"name": _ACTIVE[0].name, "digest": _ACTIVE[0].digest, "note": _ACTIVE[0].note} if _ACTIVE else {}
