"""A save from before 0.0.4's encounters, brought in: its fight (GameState.fight) becomes an
encounter, its chase gets one around it, and the creatures it already paid for are its sightings.

The old engine kept copies of everyone (engine/combat.Fighter). Those the world does not keep a body
for yet are adopted with the copy's numbers (an NPC's hit points, a creature's state); a gun's rounds
and jam go onto its item. The engagements, the holds and who lies on the ground carry over; the
round in flight does not: an old turn cut short in the middle of a fight's round is dropped with a
line in the log (design §4), and play goes on from what was settled.
"""

from __future__ import annotations

from typing import Any

from ...world.ops import EncounterStep
from ...world.record import ENGINE, Life
from .. import effects
from . import profiles
from .model import Begin, EncounterState, Engagement, FightScene, Hold, Participant, RoundBudget

DROPPED = "上一轮战斗的结算方式已更新，从已结算的局面继续"
CONDITIONS = {"死亡": ["死亡"], "濒死": ["重伤", "濒死", "昏迷"], "昏迷": ["昏迷"], "重伤": ["重伤"]}
STANCE = {"dodge": "dodge", "fight": "fight_back", "cover": "cover"}


def legacy(game: Any) -> list[str]:
    """Bring an old save's fight and chase into the encounter engine; what was done, in words."""
    from .. import combat as battle

    st, w = game.state, game.world
    done: list[str] = []
    for inv in st.investigators:  # the old engine's one list of creatures seen: each investigator has seen them
        for c in st.seen:
            if c not in st.sightings.setdefault(inv.id, []):
                st.sightings[inv.id].append(c)
    st.seen = []
    old = st.fight
    people = old.fighters if old is not None else st.chase.runners if st.chase is not None else []
    if (old is None and st.chase is None) or st.encounter is not None:
        st.fight = None
        return done
    eid = f"legacy{st.turn}"
    adopt: dict[str, Life] = {}
    arms: list = []
    parts: list[Participant] = []
    for f in people:
        holder = battle.holder_of(w, f) if f.kind != "investigator" else f.id
        if f.kind != "investigator" and profiles.profile(w, holder) is None:
            continue  # nobody the world can keep (a made-up foe of an old test or save): left out
        ent = w.rec.entities.get(holder)
        if f.kind != "investigator" and (ent is None or ent.life is None):
            adopt[holder] = Life(hp=f.hp, hp_max=f.hp_max, conditions=CONDITIONS.get(f.status, []), regenerating=f.status == "倒下")
        for a in f.attacks:
            item = battle.item_for(w, f, a) if a.firearm else None
            if item and a.ammo is not None:
                arms.append(effects.Ammo(item=item, loaded=a.ammo))
            if item and a.jammed:
                arms.append(effects.Jam(item=item, jammed=True))
        presence = {"逃走": "fled", "投降": "surrendered"}.get(f.status, "in")
        human = f.kind == "investigator" and f.id in st.human
        parts.append(Participant(id=holder, kind=f.kind, side="party" if f.side == "party" else "foe",  # type: ignore[arg-type]
                                 controller="human" if human else "ai" if f.kind == "investigator" else "keeper",
                                 presence=presence, surprised=f.surprised, react=STANCE.get(f.stance, "dodge"),  # type: ignore[arg-type]
                                 node=st.location, speed=f.speed or None))
    scene = FightScene(prone=[p.id for p, f in zip(parts, people, strict=False) if f.prone])
    if old is not None:
        scene.engaged = [Engagement(a=a, b=b, since=old.round) for a, b in (pair[:2] for pair in old.engaged if len(pair) >= 2)]
        scene.holds = [Hold(held=f.id, by=f.held_by, since=old.round) for f in people if f.held_by]
    lost = {f.id: ("爬起来" if f.prone else "扑向掩体") for f in people if f.lost_action}
    rnd = old.round if old is not None else 0
    enc = EncounterState(id=eid, mode="fight" if old is not None else "chase", place=st.location,
                         reason=(old.reason if old is not None else st.chase.reason)[:120],  # type: ignore[union-attr]
                         started_turn=old.started if old is not None else st.chase.started,  # type: ignore[union-attr]
                         participants=parts, round=rnd, order=[], cursor=2 if rnd else 0,
                         budget=RoundBudget(lost_next=lost), scene=scene)
    if adopt or arms:
        result = effects.commit(w, arms, op_id=f"enc/{eid}/adopt", source="migrate", adopt=adopt)
        if not result.ok:
            done.append("；".join(result.problems))
    result = w.apply(EncounterStep(id=f"enc/{eid}/migrate", encounter=eid, unit="migrate", changes=[Begin(state=enc)], actor=ENGINE))
    if not result.ok:
        done.append("；".join(result.problems))
        return done
    st.fight = None
    journal = st.pending_turn.get("resolution") or st.pending_resolution or {}
    if any(k.split("/")[0].startswith(("fight-", "battle")) for k in journal.get("steps", {})):
        st.pending_turn.clear()
        st.pending_resolution = {}
        st.add("system", DROPPED)
        done.append(DROPPED)
        game._debug("encounter", {"phase": "migrate", "dropped": True})
    return done


__all__ = ["DROPPED", "legacy"]
