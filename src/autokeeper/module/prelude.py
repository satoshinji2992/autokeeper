"""开局演出: a few shots, pictures and words, played before the game screen (tui/screens/prelude.py).

Written at compile time (module/adapt.py, the prelude pass) and kept in the module, so every
table sees the same opening. While it plays, the director adapts the module to these
investigators (engine/game.Game.prepare_opening); the game screen comes once both are done, and
the keeper begins at once. It tells only the story's background, what the title, hook and
setting already give away: the era, the place, the mood, how the matter came about. Its pictures
are the cover, the starting place, places the hook or setting already names, and the public places
anyone in town could point the way to. A module
without one gets a plain one made from those same fields (frames).
"""

from __future__ import annotations

import re

from .schema import Module, PreludeFrame

MAX_FRAMES = 6
TEXT_LIMIT = 80  # characters a caption may run to
_SENTENCE = re.compile(r"(?<=[。！？!?])")


def public_art(m: Module) -> dict[str, str]:
    """The pictures an opening may show, with what each one is."""
    from ..engine import fog

    out: dict[str, str] = {}
    if m.cover:
        out[m.cover] = "封面"
    named = fog.mentions(m, [m.setting, m.hook])  # not the subtitle: it may name the mystery's own place
    public = [loc.id for loc in m.locations if loc.public]  # anyone in town could point the way there
    for pid in dict.fromkeys([m.start, *named, *public]):
        loc = next((x for x in m.locations if x.id == pid), None)
        if loc is not None and loc.art:
            out.setdefault(loc.art, f"{loc.name}：{_flat(loc.read_aloud)[:60]}")
    for sc in m.showcases:
        if sc.when == "opening" and sc.art:
            out.setdefault(sc.art, sc.title)
    return out


def public_text(m: Module) -> str:
    """What the players are told before anything happens: the opening may say this much."""
    start = next((x for x in m.locations if x.id == m.start), None)
    return "\n".join(x for x in (m.title, m.subtitle, m.era, m.setting, m.hook, start.read_aloud if start else "") if x)


def problems(m: Module, frames: list[PreludeFrame]) -> list[str]:
    """What keeps an opening out: too few or too many shots, or a shot that does (shot)."""
    out = [] if 2 <= len(frames) <= MAX_FRAMES else [f"开局演出要 2–{MAX_FRAMES} 幕，这次有 {len(frames)} 幕"]
    return out + [f"第 {i} 幕{p}" for i, f in enumerate(frames, 1) for p in shot(m, f)]


def shot(m: Module, frame: PreludeFrame) -> list[str]:
    """What keeps one shot out: a picture that is not public yet, an empty or overlong caption,
    words from the truth, the keeper's notes, a secret or a clue."""
    from ..agents import guard

    protected = {"真相": m.truth, "守秘人须知": m.keeper_notes}
    protected |= {f"{n.name}的秘密": s for n in m.npcs for s in n.secrets}
    protected |= {f"线索 {c.id}": c.text for c in m.clues}
    out = []
    if frame.art and frame.art not in public_art(m):
        out.append(f"的图片 {frame.art} 不在开局可以公开的图里")
    if not frame.text.strip() or len(frame.text) > TEXT_LIMIT:
        out.append(f"的字幕要写，且不超过 {TEXT_LIMIT} 字")
    if leaked := guard.leaks(frame.text, protected, public=public_text(m)):
        out.append(f"的字幕说出了{'、'.join(leaked)}里的内容")
    return out


def _flat(text: str) -> str:
    """One line: a line break inside Chinese prose joins without a space."""
    return " ".join(re.sub(r"\s*\n\s*", "", text.strip()).split())


def _clip(text: str) -> str:
    """The first sentences of a public text that fit in a caption."""
    out = ""
    for sentence in _SENTENCE.split(_flat(text)):
        if out and len(out) + len(sentence) > TEXT_LIMIT:
            break
        out += sentence
    return out[:TEXT_LIMIT]


def frames(m: Module) -> list[PreludeFrame]:
    """The module's own opening, or a plain one made from its title, era, setting and hook."""
    if m.prelude:
        return [f for f in m.prelude if f.text.strip()][:MAX_FRAMES]
    art = list(public_art(m))
    start = next((x.art for x in m.locations if x.id == m.start and x.art), "")
    places = [a for a in art if a not in (m.cover, start)]
    shots = [PreludeFrame(art=m.cover or (places[0] if places else start), text=f"《{m.title}》" + (f"\n{m.subtitle}" if m.subtitle else ""))]
    if m.era or m.setting:
        shots.append(PreludeFrame(art=places[1] if len(places) > 1 else "", text=_clip("，".join(x for x in (m.era, m.setting) if x))))
    if m.hook.strip():
        shots.append(PreludeFrame(art=start, text=_clip(m.hook)))
    return shots
