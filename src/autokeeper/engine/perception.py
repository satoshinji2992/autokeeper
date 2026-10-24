"""Personal appearances over real entities. A changed appearance never creates a second body.

The keeper retains the real record. Only outgoing views are projected; input choices carry
signed, seat-specific handles which are matched back to real declarations on the server.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .state import GameState


class Appearance(BaseModel):
    handle: str = Field(default_factory=lambda: "seen-" + uuid.uuid4().hex[:20])
    name: str = ""
    portrait: str = ""
    side: Literal["", "party", "foe", "neutral"] = ""
    visible: bool = True
    reason: str = ""
    original_name: str = ""  # keeper-side label, for delivery-time log headers
    original_portrait: str = ""


class PerceptionChange(BaseModel):
    to: list[str] = Field(min_length=1, description="产生该知觉的调查员 id；只改变这些人的呈现")
    target: str = Field(min_length=1, description="被误认或看不见的真实人物/实体 id；伤害仍落实到它，不另造怪物")
    name: str = Field("", description="这些人看见的称呼；隐藏时可留空，不能在公共正文解释真实对应")
    portrait: str = Field("", description="已有图片资源 key；空白表示不展示原画像，不能填写文件路径")
    side: Literal["", "party", "foe", "neutral"] = Field("", description="主观认为的阵营，foe 可选为攻击对象；不修改真实阵营")
    visible: bool = Field(True, description="这名调查员能否感知到它；false 不提供这个人的目标和画像")
    clear: bool = Field(False, description="恢复该对象原本的呈现；其他字段不再采用")
    reason: str = Field(min_length=1, description="幻觉、误认、伪装或恢复的依据；仅供主持记录")


def appearances(state: GameState, viewer: str | None) -> dict[str, Appearance]:
    return state.perceptions.get(viewer, {}) if viewer is not None else {}


def get(state: GameState, viewer: str | None, target: str) -> Appearance | None:
    return appearances(state, viewer).get(target)


def check_changes(game: Any, changes: list[dict | PerceptionChange]) -> list[str]:
    party = {i.id for i in game.state.investigators}
    targets = party | set(game.world.rec.entities)
    out = []
    for raw in changes:
        try:
            change = raw if isinstance(raw, PerceptionChange) else PerceptionChange.model_validate(raw)
        except ValueError as exc:
            out.append(f"个人知觉格式不正确：{exc}")
            continue
        if set(change.to) - party:
            out.append("个人知觉的收件人必须是本局调查员")
        if change.target not in targets:
            out.append(f"个人知觉对象 {change.target} 尚未登记")
        if change.target in change.to:
            out.append("个人知觉目前只改变看见的他人，不覆盖自己的角色卡")
        if change.portrait and change.portrait not in (game.m.art.keys() | game.m.pictures.keys()):
            out.append(f"个人画像 {change.portrait} 不是已有图片资源")
    return out


def apply(game: Any, changes: list[dict | PerceptionChange]) -> None:
    """Apply an adopted keeper response; repeated application preserves the same handles."""
    problems = check_changes(game, changes)
    if problems:
        raise ValueError("；".join(problems))
    for raw in changes:
        change = raw if isinstance(raw, PerceptionChange) else PerceptionChange.model_validate(raw)
        for viewer in dict.fromkeys(change.to):
            masks = game.state.perceptions.setdefault(viewer, {})
            if change.clear:
                masks.pop(change.target, None)
            else:
                old = masks.get(change.target)
                from .encounter.views import portrait

                masks[change.target] = Appearance(
                    **change.model_dump(exclude={"to", "target", "clear"}),
                    original_name=game.world.name(change.target), original_portrait=portrait(game, change.target),
                    **({"handle": old.handle} if old is not None else {}))


def brief(state: GameState) -> str:
    rows = [f"{viewer}把{target}看作{p.name or '未辨明身影'}（{p.side or '阵营照旧'}，"
            f"{'可见' if p.visible else '不可见'}；{p.reason}）"
            for viewer, masks in state.perceptions.items() for target, p in masks.items()]
    return "\n".join(rows) or "无个人知觉覆盖"


def resolve_target(state: GameState, viewer: str, handle: str) -> str:
    return next((target for target, p in appearances(state, viewer).items() if p.handle == handle), handle)


def picture_visible(game: Any, viewer: str | None, key: str) -> bool:
    own = next((inv.portrait for inv in game.state.investigators if inv.id == viewer), "")
    source = game.state.visuals.get(key, {}).get("source", key)
    return key == own or not any(
        game.state.visuals.get(p.original_portrait, {}).get("source", p.original_portrait) == source
        and (not p.visible or p.portrait not in {key, source})
        for p in appearances(game.state, viewer).values())


def choice_handle(game: Any, viewer: str, value: dict) -> str:
    enc = game.state.encounter
    # Tab changes the response to blows after a declaration was collected. It does not
    # change the intended target or weapon, nor invalidate that action's opaque handle.
    action = {key: child for key, child in value.items() if key not in {"react", "summary"}}
    payload = json.dumps([viewer, enc.id if enc else "", enc.round if enc else 0, action],
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hmac.new(game.state.perception_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]
    return "act-" + digest


def input_label(game: Any, viewer: str, value: str) -> str:
    if not value.startswith("act-") or game.state.encounter is None:
        return value
    from .encounter import choices

    return next((option.label for option in choices.options(game, viewer)
                 if hmac.compare_digest(value, choice_handle(game, viewer, option.value))), "选择战斗行动")


def _identities(game: Any, viewer: str | None) -> tuple[dict[str, str], dict[str, str]]:
    from .encounter import profiles, views

    ids, words = {}, {}
    masked = views.masks(game.state.encounter)
    for target, p in appearances(game.state, viewer).items():
        ids[target] = p.handle
        if target in masked:
            ids[masked[target]] = p.handle
        name = p.name or "未辨明的身影"
        original = game.world.name(target)
        shown = profiles.shown_name(game.world, target, viewer=viewer)
        for text in (original, shown):
            if text and text != name:
                words[text] = name
    return ids, words


def text(game: Any, viewer: str | None, value: str) -> str:
    """Project a structured identity label, never prose or the contents of a handout."""
    ids, words = _identities(game, viewer)
    return ids.get(value, words.get(value, value))


def project_state(game: Any, viewer: str | None, state: dict) -> dict:
    """A disguised party member must not also appear as their real investigator card."""
    masks = appearances(game.state, viewer)
    if not masks:
        return state
    out = dict(state)
    if "investigators" in out:
        old_active = out.get("active", 0)
        investigators = out["investigators"]
        active = investigators[old_active]["id"] if 0 <= old_active < len(investigators) else viewer
        kept = [inv for inv in investigators if inv["id"] not in masks]
        out["investigators"] = kept
        out["active"] = next((i for i, inv in enumerate(kept) if inv["id"] == active),
                             next((i for i, inv in enumerate(kept) if inv["id"] == viewer), 0))
    for key in ("human", "identified"):
        if key in out:
            out[key] = [who for who in out[key] if who not in masks]
    if "deeds" in out:
        out["deeds"] = [d for d in out["deeds"] if d.get("who") not in masks]
    return out


IDENTITY_FIELDS = {"id", "who", "from", "name", "investigator", "attacker", "defender", "fighter", "hurt", "hunter",
                   "attacker_name", "defender_name", "target", "owner", "to", "order", "acting", "seat", "npc", "actor"}


def _walk(game: Any, viewer: str | None, value: Any, *, field: str = "") -> Any:
    if isinstance(value, str):
        return text(game, viewer, value) if field in IDENTITY_FIELDS else value
    if isinstance(value, list):
        out = []
        for row in value:
            target = row.get("id", row.get("investigator")) if isinstance(row, dict) else None
            p = get(game.state, viewer, target) if target else None
            if p is not None and not p.visible:
                continue
            if p is not None:
                row = {**row, "name": p.name or "未辨明的身影", "portrait": p.portrait}
                for key in ("occupation", "look", "description", "role", "player", "origin"):
                    if key in row:
                        row[key] = ""
            out.append(_walk(game, viewer, row, field=field))
        return out
    if isinstance(value, dict):
        return {text(game, viewer, key): (_walk(game, viewer, child, field=key) if key != "log" else child)
                for key, child in value.items() if key != "presented"}
    return value


def project_event(game: Any, viewer: str | None, event: dict) -> dict:
    """Project deterministic feedback as well as narration; the stored event stays original."""
    from .encounter import views
    from .encounter.actions import Ctx
    from .encounter.telling import render

    out = dict(event)
    template = out.pop("_entity_line", None)
    templates = out.pop("_entity_lines", None)
    if game.state.encounter is not None:
        names = views.names(Ctx.of(game), viewer or "players")
        if template is not None:
            out["line"] = render(template, names)
        if templates is not None:
            out["lines"] = [render(line, names) for line in templates]
    if not appearances(game.state, viewer):
        return out
    target = out.get("npc") or out.get("actor") or out.get("fighter") or out.get("id")
    if isinstance(target, str):
        actual = {masked: real for real, masked in views.masks(game.state.encounter).items()}
        target = actual.get(target, resolve_target(game.state, viewer, target))
    if isinstance(target, str) and (appearance := get(game.state, viewer, target)) is not None:
        out["name"] = appearance.name or "未辨明的身影"
        if "portrait" in out:
            out["portrait"] = appearance.portrait if appearance.visible else ""
    if "fight" in out and game.state.encounter is not None:
        out["fight"] = views.fight_view(game, viewer=viewer)
    if "chase" in out and game.state.encounter is not None:
        out["chase"] = views.chase_view(game, viewer=viewer)
    for key in ("attacker", "defender", "hurt"):
        target = out.get(key)
        p = get(game.state, viewer, target) if isinstance(target, str) else None
        if p is not None:
            out[key + "_name"] = p.name or "未辨明的身影"
            if key + "_side" in out and p.side:
                out[key + "_side"] = p.side
    if out.get("type") == "damage" and get(game.state, viewer, out.get("fighter", "")) is not None:
        out["text"] = "受了伤。"
    return _walk(game, viewer, out)


def project_payload(game: Any, viewer: str | None, payload: dict) -> dict:
    """The final outgoing envelope, including restored log, lobby names and module copies."""
    out = dict(payload)
    if isinstance(out.get("state"), dict):
        out["state"] = project_state(game, viewer, out["state"])
    if isinstance(out.get("ev"), dict):
        out["ev"] = project_event(game, viewer, out["ev"])
    if not appearances(game.state, viewer):
        return out
    module = out.get("module")
    if isinstance(module, dict):
        module = dict(module)
        masks = appearances(game.state, viewer)
        module["pregens"] = [inv for inv in module.get("pregens", []) if inv.get("id") not in masks]
        out["module"] = module
    for key in ("art", "colors", "picture_data"):
        if isinstance(out.get(key), dict):
            out[key] = {asset: data for asset, data in out[key].items() if picture_visible(game, viewer, asset)}
    return _walk(game, viewer, out)
