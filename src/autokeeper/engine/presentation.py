"""Keeper-directed media deliveries: presentation never grants a shared world discovery."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from . import audience

if TYPE_CHECKING:
    from ..module.schema import Module
    from .state import GameState


class MediaAction(BaseModel):
    to: list[str] | None = Field(None, description="收件调查员id；null公开给全桌，名单仅这些人")
    action: Literal["show", "clear"] = Field("show", description="show展示并保留；clear撤下当前画面，不撤销已读材料；省略动作保持原样")
    ref: str = Field("", description="只引用资源索引里的handout:/scene:/portrait:/image:；clear时留空，不填路径或网址")
    slot: Literal["scene", "overlay"] = Field("overlay", description="scene替换场景图直到clear；overlay打开插图或材料")
    title: str = Field("", description="可选的玩家可见标题，不写真实身份或后台说明")


def ready(module: Module, key: str | None) -> bool:
    return bool(key and (module.art.get(key, "").strip()
                        or (module.pictures.get(key) and Path(module.pictures[key]).is_file())))


def resolve(module: Module, state: GameState, ref: str) -> dict:
    """Resolve only registered module resources; no arbitrary file or URL access."""
    kind, _, ident = ref.partition(":")
    if kind == "handout":
        h = next((h for h in module.handouts if h.id == ident), None)
        if h is None:
            raise ValueError(f"手稿 {ident} 不存在")
        keys = [*h.images, *h.original_keys()]
        return {"kind": "handout", "handout": {"id": h.id, "title": h.title, "kind": h.kind, "text": h.text,
                                                  "images": keys},
                "art": {k: module.art[k] for k in keys if k in module.art},
                "colors": {k: module.art_colors[k] for k in keys if k in module.art_colors},
                "pictures": {k: module.pictures[k] for k in keys if k in module.pictures}}
    if kind == "scene":
        loc = next((loc for loc in module.locations if loc.id == ident), None)
        if loc is None:
            raise ValueError(f"地点 {ident} 不存在")
        key = state.view if ident == state.location and state.view else loc.art
    elif kind == "portrait":
        npc = next((npc for npc in module.npcs if npc.id == ident), None)
        if npc is None:
            raise ValueError(f"人物 {ident} 不存在")
        key = npc.portrait
    elif kind == "image":
        key = ident
    else:
        raise ValueError("图片必须引用资源索引，不能使用路径或网址")
    if not ready(module, key):
        raise ValueError(f"资源 {ref} 尚未准备好")
    return {"kind": "image", "art": module.art.get(key, ""), "colors": module.art_colors.get(key, ""),
            "picture": module.pictures.get(key, "")}


def prepare(module: Module, state: GameState, raw: dict | MediaAction, ident: str) -> dict:
    action = MediaAction.model_validate(raw)
    people = {i.id for i in state.investigators}
    if action.to is not None and (not action.to or any(who not in people for who in action.to)):
        raise ValueError("媒体收件人必须是本局调查员")
    payload = resolve(module, state, action.ref) if action.action == "show" else {}
    if action.slot == "scene" and payload.get("kind") == "handout":
        raise ValueError("手稿使用overlay展示，不替换场景图")
    return {"id": ident, "turn": state.turn, "audience": list(dict.fromkeys(action.to)) if action.to is not None else None,
            "action": action.action, "slot": action.slot, "ref": action.ref, "title": action.title, "payload": payload}


def event(delivery: dict) -> dict:
    return {"type": "presentation", **{k: v for k, v in delivery.items() if k != "ref"}}


def history(state: GameState, viewer: str | None) -> list[dict]:
    return [p for p in state.presentations if audience.visible(p, viewer)]


def current(state: GameState, viewer: str | None) -> dict[str, dict]:
    slots: dict[str, dict] = {}
    for p in history(state, viewer):
        if p["action"] == "clear":
            slots.pop(p["slot"], None)
        else:
            slots[p["slot"]] = p
    return slots


def papers(state: GameState, viewer: str | None) -> dict[str, dict]:
    return {p["payload"]["handout"]["id"]: p for p in history(state, viewer)
            if p["action"] == "show" and p.get("payload", {}).get("kind") == "handout"}


def opaque(ident: str, key: str) -> str:
    return "media-" + hashlib.sha256(f"{ident}:{key}".encode()).hexdigest()[:16]


def index(module: Module, state: GameState, *, at: str = "") -> str:
    """Short candidates for the present scene; a ref never means it has been disclosed."""
    place = at or state.location
    loc = module.location(place)
    people = {n.id for n in module.npcs_at(place)}
    refs = []
    for target in module.locations:
        if target.id in {place, *loc.exits} and ready(module, target.art):
            refs.append(f"scene:{target.id}：{target.name}")
    for npc in module.npcs:
        if npc.id in people and ready(module, npc.portrait):
            refs.append(f"portrait:{npc.id}：{npc.name}（仅图片，不以真名揭示身份）")
    hids = {c.handout for c in module.clues if c.where == place or c.npc in people}
    hids |= set(state.handouts)
    for h in module.handouts:
        if h.id in hids:
            refs.append(f"handout:{h.id}：{h.title}（材料原文；展示不等于全员发现线索）")
    for view in loc.views:
        if ready(module, view.key):
            refs.append(f"image:{view.key}：{view.label}")
    latest = {(tuple(p["audience"]) if p["audience"] is not None else None, p["slot"]): p for p in state.presentations}
    shown = [f"{p['audience'] or '全桌'}：{p['slot']} {p['ref']}（{p['action']}）" for p in latest.values()]
    pending = [f"{p['action']}：{p['problem']}" for p in state.presentation_issues.values()]
    return ("\n【可呈现资源；这里只是候选，不表示玩家已经看过】\n" + "\n".join(refs)
            + "\nmedia省略或留空保持原呈现；show指定受众展示；clear撤下对应槽位。只用已有资源。"
            + ("\n【此前媒体投递】\n" + "\n".join(shown) if shown else "")
            + ("\n【尚未展示；不要写成已看过，下次补齐资源或改用其他材料】\n" + "\n".join(pending) if pending else "")) if refs or shown or pending else ""


def personal_context(state: GameState, viewer: str) -> str:
    docs = papers(state, viewer)
    return "\n".join(f"【{'仅你收到的' if p['audience'] is not None else '已公开的'}材料】"
                     f"{p['payload']['handout']['title']}\n{p['payload']['handout']['text']}" for p in docs.values())


def npc_refs(module: Module, state: GameState, npc: str) -> set[str]:
    """Original material permissions and explicit knowledge of a whole handout. A learned clue
    summary, including an unlocked chance to retell it, does not supply the underlying document."""
    from ..world.knowledge import sees

    sheet = module.npc(npc)
    unlocked = set(state.npc(npc).unlocked) | {cid for cid in sheet.clues if cid not in sheet.gates}
    refs = {f"handout:{c.handout}" for c in module.clues if c.id in sheet.clues and c.id in unlocked
            and c.handout and c.available(state.flags, state.clues)}
    refs |= {f"handout:{h.id}" for h in module.handouts
             if (fact := state.record.facts.get(f"handout:{h.id}")) is not None and sees(fact, npc)}
    return refs
