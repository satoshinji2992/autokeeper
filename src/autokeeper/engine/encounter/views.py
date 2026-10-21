"""The encounter as each audience sees it: the screens' fight view (the same dict the TUI and the LAN
table draw), and the keeper plan's brief (agents/encounter.Brief).

Every view that leaves the engine is made here (invariant I6): the players get public names (an
NPC they have not met by what they see of it, a creature by what it looks like), a foe's wounds in
words and never its hit points; the keeper gets everything, true names beside the public ones, and,
for each of its people, what that person can perceive (the keeper knows all, the NPC does not).
Raw GameState.encounter is private.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...rules.wounds import Body
from . import actions, profiles
from .actions import ATTACKING, VERBS, Ctx

if TYPE_CHECKING:
    from .model import EncounterState

STANCES = {"dodge": "dodge", "fight_back": "fight", "cover": "cover", "none": "dodge"}  # the screens' old words
GONE = {"fled": "逃走", "escaped": "逃走", "left": "逃走", "surrendered": "投降", "captured": "被抓住"}


def wound_word(body: Body) -> str:
    """How hurt someone looks, in words: what the table sees of a foe, never its numbers."""
    if body.dead:
        return "死亡"
    if body.dying:
        return "濒死"
    if body.regenerating:
        return "倒下"
    if "昏迷" in body.conditions:
        return "昏迷"
    if body.hp_max <= 0 or body.hp >= body.hp_max:
        return "无伤"
    share = body.hp / body.hp_max
    if body.major or share <= 0.34:
        return "重伤"
    return "受伤" if share <= 0.75 else "轻伤"


def status(ctx: Ctx, pid: str) -> str:
    """The screens' status word: 死亡 濒死 昏迷 倒下 (coming back), 逃走 投降, 重伤, or nothing."""
    p, v = ctx.p(pid), ctx.view(pid)
    if v is not None:
        b = v.body()
        for word in ("死亡", "濒死"):
            if word in b.conditions:
                return word
        if b.regenerating:
            return "倒下"
        if "昏迷" in b.conditions:
            return "昏迷"
    if p is not None and p.presence in GONE:
        return GONE[p.presence]
    if v is not None and v.body().major:
        return "重伤"
    return ""


def down(ctx: Ctx, pid: str) -> bool:
    return status(ctx, pid) in ("死亡", "濒死", "昏迷", "倒下")


def masks(enc: EncounterState | None) -> dict[str, str]:
    """The ids the screens get for everyone who is not an investigator: one per encounter seat ("f2"),
    never the NPC's or the creature's own (a creature form's id would name the NPC behind it)."""
    if enc is None:
        return {}
    return {p.id: f"f{i}" for i, p in enumerate(enc.participants) if p.kind != "investigator"}


def masked(ev: dict, enc: EncounterState | None) -> dict:
    """An event for the screens with every id in it masked (masks)."""
    m = masks(enc)
    if not m:
        return ev
    ev = dict(ev)
    for key in ("attacker", "defender", "hurt", "who", "id", "hunter"):
        if isinstance(ev.get(key), str) and ev[key] in m:
            ev[key] = m[ev[key]]
    if isinstance(ev.get("order"), list):
        ev["order"] = [m.get(x, x) for x in ev["order"]]
    for key, people in (("fight", "fighters"), ("chase", "runners")):
        if isinstance(ev.get(key), dict) and isinstance(ev[key].get(people), list):
            ev[key] = {**ev[key], people: [{**f, "id": m.get(f.get("id", ""), f.get("id", ""))} for f in ev[key][people]]}
    return ev


def names(ctx: Ctx, audience: str = "players") -> dict[str, str]:
    return {p.id: ctx.name(p.id, audience) for p in ctx.enc.participants}


def portrait(game: Any, pid: str) -> str:
    inv = next((i for i in game.state.investigators if i.id == pid), None)
    if inv is not None:
        return inv.portrait
    form = profiles.form_of(game.world, pid)  # an NPC fighting as its creature sheet: that face (犬脸的佝偻身影)
    if form is not None and f"creature-{form.id}" in game.m.pictures:
        return f"creature-{form.id}"
    npc = next((n for n in game.m.npcs if n.id == pid), None)
    if npc is not None:
        return npc.portrait or ""
    key = f"creature-{pid}"
    return key if key in game.m.pictures else ""


def readiest(v) -> Any:
    """The attack someone holds ready: a gun that can fire, else their best melee attack."""
    guns = [a for a in v.attacks() if a.firearm and a.usable]
    if guns:
        return max(guns, key=lambda a: a.value)
    return actions.best_melee(v) or (v.attacks()[0] if v.attacks() else None)


def fight_view(game: Any, enc: EncounterState | None = None) -> dict:
    """The fight as the screens draw it (the shape engine/combat.view had): public names, a foe's
    wounds in words, an investigator's and an ally's hit points."""
    ctx = Ctx.of(game, enc)
    out = []
    for p in ctx.enc.participants:
        v = ctx.view(p.id)
        if v is None:
            continue
        b = v.body()
        weapon = readiest(v)
        party = p.side == "party"
        look = v.profile.look[:60] if hasattr(v, "profile") else ""
        out.append({"id": p.id, "name": ctx.name(p.id, "players"), "kind": p.kind, "side": "party" if party else "foe",
                    "status": status(ctx, p.id), "wound": wound_word(b), "hp": b.hp if party else None,
                    "hp_max": b.hp_max if party else None, "portrait": portrait(game, p.id), "look": look,
                    "weapon": weapon.name if weapon is not None else "徒手", "stance": STANCES.get(p.react or "dodge", "dodge"),
                    "prone": p.id in ctx.enc.scene.prone, "held": bool(ctx.held_by(p.id)), "dex": v.char("敏捷")})
    m = masks(ctx.enc)
    for f in out:
        f["id"] = m.get(f["id"], f["id"])
    return {"round": ctx.enc.round, "place": ctx.enc.place, "fighters": out}


def initiative(game: Any, enc: EncounterState | None = None) -> list[str]:
    """Who goes first among those who can act, by public name (DEX, the better fighter on a tie)."""
    from .resolve import round_order

    ctx = Ctx.of(game, enc)
    return [ctx.name(i, "players") for i in round_order(ctx, {})]


# -- the keeper plan's brief ------------------------------------------------------------------------
def _attack(ctx: Ctx, a, per_round: int):
    from ...agents.encounter import BriefAttack

    ent = ctx.world.rec.entities.get(a.item or "")
    spare = ent.weapon.spare if ent is not None and ent.weapon is not None else None
    return BriefAttack(id=a.id, name=a.name, skill=a.skill, value=a.value, damage=a.damage_text, firearm=a.firearm,
                       thrown=a.skill == "投掷", natural=a.natural, range=str(a.range) if a.range else "",
                       loaded=a.loaded if a.firearm else None, capacity=a.capacity, spare=spare, jammed=a.jammed,
                       per_round=per_round if not a.firearm else 1)


def brief_actions(ctx: Ctx, pid: str) -> list:
    """What `pid` can do now and why not, by the same preconditions the engine settles with."""
    from ...agents.encounter import BriefAction
    from .plan import AttackPick, DeclaredAction

    v = ctx.view(pid)
    out = []
    seen = [x for x in ctx.foes(pid) if ctx.alive(x) and not ctx.hidden_from(pid, x)]
    for do in ATTACKING:
        if do == "shoot" and not actions.ranged_options(v):
            continue
        ok_targets, why = [], ""
        for t in seen:
            problems = actions.preconditions(ctx, pid, DeclaredAction(do=do, attacks=[AttackPick(target=t)],
                                                                      maneuver="推倒" if do == "maneuver" else "",
                                                                      summary="-"))
            if problems:
                why = why or problems[0]
            else:
                ok_targets.append(t)
        if ok_targets:
            using = [a.id for a in (actions.ranged_options(v) if do == "shoot" else actions.melee_options(v)) if a.usable]
            out.append(BriefAction(do=do, why=f"打：{'、'.join(ok_targets)}" + (f"；用：{'、'.join(using)}" if using else "")))
        else:
            out.append(BriefAction(do=do, ok=False, why=why or "没有看得见的对手"))
    for do in ("aid", "reload", "unjam", "flee", "pursue"):
        target = ""
        if do == "aid":
            hurt = [x for x in ctx.allies(pid) if not actions._aid(ctx, pid, x)]
            if not hurt:
                continue
            target = hurt[0]
        if do == "pursue":
            fled = [p.id for p in ctx.enc.participants if p.presence == "fled" and ctx.opposed(pid, p.id)]
            if not fled:
                continue
            target = fled[0]
        problems = actions.preconditions(ctx, pid, DeclaredAction(do=do, target=target, summary="-"))
        if do in ("reload", "unjam") and problems:
            continue
        out.append(BriefAction(do=do, ok=not problems, why=problems[0] if problems else (f"可：{target}" if target else "")))
    out.append(BriefAction(do="defend"))
    if not hasattr(v, "profile") or v.profile.human or v.kind == "npc":
        out.append(BriefAction(do="speak"))
    out += [BriefAction(do="surrender"), BriefAction(do="other")]
    return out


def perception(ctx: Ctx, pid: str) -> tuple[list[str], list[str]]:
    """Who `pid` perceives now and who not (with why), by id."""
    sees, unseen = [], []
    for p in ctx.enc.participants:
        if p.id == pid:
            continue
        why = ctx.hidden_from(pid, p.id)
        if why:
            unseen.append(f"{p.id}（{why}）")
        else:
            sees.append(p.id)
    return sees, unseen


def luck_pool(ctx: Ctx, pid: str) -> int | None:
    """An important NPC's Luck pool left (Keeper Rulebook p. 168): its POW until it spends some."""
    npc = next((n for n in ctx.game.m.npcs if n.id == pid), None)
    if npc is None or not npc.important:
        return None
    ent = ctx.world.rec.entities.get(pid)
    if ent is not None and ent.luck is not None:
        return ent.luck
    v = ctx.view(pid)
    return v.char("意志") if v is not None else None


def arranged_line(p) -> str:
    a = p.arrangement
    if a is None:
        return ""
    acts = "；".join(f"{VERBS.get(x.do, x.do)} " + "、".join(k.target for k in x.attacks) if x.attacks
                     else f"{VERBS.get(x.do, x.do)} {x.target}".strip() for x in a.actions)
    return f"{a.goal}：{acts}"


def brief(game: Any, *, waiting: str = "", unsettled: list[str] | None = None, lines: list[str] | None = None) -> Any:
    """The keeper plan's whole view of the encounter (agents/encounter.Brief)."""
    from ...agents.encounter import Brief, BriefPerson, BriefStake

    ctx = Ctx.of(game)
    enc = ctx.enc
    from .choices import needs_arrangement

    people = []
    for p in enc.participants:
        v = ctx.view(p.id)
        if v is None:
            continue
        b = v.body()
        prof = getattr(v, "profile", None)
        per = v.attacks_per_round()
        attacks = [_attack(ctx, a, per) for a in v.attacks()]
        skills = {"闪避": v.skill("闪避"), "格斗(斗殴)": v.skill("格斗(斗殴)")}
        traits: list[str] = []
        missing: list[str] = []
        goal = ""
        if prof is not None:
            traits += [f"护甲 {prof.armor}"] * bool(prof.armor) + ["枪伤减半"] * prof.guns_halved
            traits += [f"每轮恢复 {prof.regen}"] * bool(prof.regen) + ["人类"] * prof.human + ["成群"] * prof.swarm
            traits += [f"引擎读不懂：{x}" for x in prof.unread]
            npc = next((n for n in game.m.npcs if n.id == p.id), None)
            goal = p.arrangement.goal if p.arrangement else (npc.goal if npc else "")
            missing = [f"{a.id} 的备弹" for a in attacks if a.firearm and a.spare is None]
            creature = profiles.form_of(game.world, p.id) or next((c for c in game.m.creatures if c.id == p.id), None)
            if creature is not None and creature.pursues is not None:
                traits.append("模组写明：调查员逃跑时它" + ("会追" if creature.pursues else "不追"))
        sees, unseen = perception(ctx, p.id) if p.controller == "keeper" else (None, [])
        conditions = list(b.conditions) + (["倒地"] if p.id in enc.scene.prone else []) + (["被制住"] if ctx.held_by(p.id) else [])
        people.append(BriefPerson(
            id=p.id, name=v.true_name, public=v.public_name("players"), kind=p.kind, side=p.side, controller=p.controller,
            presence=p.presence, node=p.node, hp=b.hp, hp_max=b.hp_max, conditions=conditions, dex=v.char("敏捷"),
            build=v.build(), mov=v.mov(), speed=p.speed, db=v.db(), skills=skills, attacks=attacks, goal=goal,
            arranged=arranged_line(p), react=p.arrangement.react if p.arrangement else (p.react or ""),
            needs_arrangement=p.controller == "keeper" and needs_arrangement(enc, p), speaks=prof is None or prof.human or p.kind == "npc",
            surprised=p.surprised, lost_next=enc.budget.lost_next.get(p.id, ""),
            actions=brief_actions(ctx, p.id) if p.controller != "human" and ctx.acting(p.id) else [] if p.controller != "human" else None,
            traits=traits, missing=missing, sees=sees, unseen=unseen,
            luck=luck_pool(ctx, p.id) if p.controller == "keeper" else None))
    fled = [p.id for p in enc.participants if p.presence == "fled"]
    held = [f"{h.by} 制住了 {h.held}" for h in enc.scene.holds]
    cover = [f"{c.who} 躲在{c.what}后（{'部分掩体' if c.partial else '完全遮住'}）" for c in enc.scene.cover]
    recent = list(lines or [])
    if not recent:
        last = [r for r in enc.receipts if r.round == enc.round and r.phase in ("committed", "lapsed")]
        recent = [line for r in last for line in r.lines]
    return Brief(id=enc.id, mode=enc.mode, round=enc.round, phase=enc.phase, place=game.world.name(enc.place), reason=enc.reason,
                 pace=enc.clock.pace, people=people, engaged=[(e.a, e.b) for e in enc.scene.engaged], cover=cover,
                 prone=list(enc.scene.prone), holds=held, distances=dict(enc.scene.distance), fled=fled,
                 stakes=[BriefStake(when=s.when, flags=list(s.flags), note=s.note, fired=s.fired) for s in enc.stakes],
                 lines=recent, history=list(enc.history), unsettled=list(unsettled or []), waiting=waiting)


def keeper_text(game: Any, **kw) -> str:
    from ...agents.encounter import encounter_brief

    return encounter_brief(brief(game, **kw))


__all__ = ["brief", "fight_view", "initiative", "keeper_text", "names", "status", "wound_word"]


# -- what the table sees: the players' screens, the LAN clients, the AI players ------------------------
def table(game: Any) -> dict | None:
    """The fight or the chase going on as the players' screens hold it, with each investigator's
    choices for this round in the words the engine matches (one list per answer to blows), or None
    outside one. Nothing in it is the keeper's: public names, a foe's wounds in words."""
    from .. import combat as battle
    from . import choices
    from .runner import chase_options

    st = game.state
    if st.chase is not None:
        ch = st.chase
        out = {"kind": "chase", "round": ch.round, "chase": masked({"chase": battle.chase_view(ch)}, st.encounter)["chase"],
               "choices": {}, "presets": {}}
        for f in ch.runners:
            if f.kind == "investigator" and f.standing and f.id not in ch.hidden:
                out["choices"][f.id] = {"run": [[label, note] for label, note, _ in chase_options(f, ch)]}
                out["presets"][f.id] = "run"
        return out
    enc = st.encounter
    if enc is None or enc.phase == "ended":
        return None
    ctx = Ctx.of(game, enc)
    out = {"kind": "fight", "round": enc.round, "fight": fight_view(game, enc), "choices": {}, "presets": {}, "acting": []}
    for p in enc.participants:
        if p.kind != "investigator":
            continue
        if ctx.acting(p.id):
            out["acting"].append(p.id)
            out["choices"][p.id] = {r: [[o.label, o.note] for o in choices.options(game, p.id, r, ctx)]
                                    for r in choices.presets(ctx, p.id)}
            out["presets"][p.id] = p.react if p.react in out["choices"][p.id] else "dodge"
    return out


def situation(now: dict, who: str | None, names: dict[str, str]) -> str:
    """A fight or a chase in words for an AI player (engine/autoplay.situation): what the screens
    show, and the round's choices in the words the engine matches."""
    if now["kind"] == "fight":
        fighters = now["fight"]["fighters"]
        foes = "；".join(f"{f['name']}（{f['wound']}，{f['weapon']}）" for f in fighters if f["side"] != "party")
        party = "；".join(f"{f['name']} HP {f['hp']}/{f['hp_max']}" + (f"，{f['status']}" if f["status"] else "")
                          for f in fighters if f["side"] == "party")
        head = f"正在战斗（第 {max(1, now['round'])} 轮）。对手：{foes}。你们：{party}。"
    else:
        ch = now["chase"]
        runners = ch["runners"]
        others = "、".join(r["name"] for r in runners if r["side"] != "party")
        head = f"正在追逐（第 {max(1, now['round'])} 轮）：{'你们在逃' if ch['quarry'] == 'party' else '你们在追'}，{others}。"
    out = [head]
    for pid, lists in now.get("choices", {}).items():
        if who not in (None, pid):
            continue
        preset = now.get("presets", {}).get(pid) or next(iter(lists), "")
        out.append(f"{names.get(pid, pid)}这一轮可以做的（照原样写出其中一行“——”前面的文字；别的具体动作也可以，写清楚做什么）：")
        out += [f"  {label} —— {note}" for label, note in lists.get(preset, [])]
    out.append("这一轮你做什么？")
    return "\n".join(out)
