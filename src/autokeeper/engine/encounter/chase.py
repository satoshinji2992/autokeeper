"""A chase's units, worked out (Keeper Rulebook ch. 7): how one begins, a round's action points, a
runner's movement across the site, hiding, a barrier put up behind, turning to fight, and what the
end of a round decides (who lost sight of whom, who got away, whether it comes to blows).

The same encounter as the fight (engine/encounter/model.EncounterState in mode "chase"), the same
units: each works on `resolve.Work`, throws its dice through the turn's journal and lands as one
op (runner._unit). The numbers are rules/chase.ChaseRules'; the graph is engine/encounter/site.py's.

Who is in it: everyone still present. The quarry (`ChaseState.quarry`) run, the pursuers
(`ChaseState.pursuers`) go after them, anyone else is where they were and may join by running.
An attack in a chase is the fight's (resolve.strike) at the same location, a gun at any it sees;
it costs an action point, shooting on the move a penalty die instead (p. 120). Nothing here asks
anyone anything: the keeper plan arranges the keeper's people, the players declare their own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...rules import chase as rules
from ...rules.bcdice import Level
from ...rules.coc import DIFFICULTY_ZH
from ...rules.dice_expr import DamageExpr
from .. import effects
from . import actions, site
from .model import ChaseState, Engagement
from .plan import ObstacleState
from .resolve import record

if TYPE_CHECKING:
    from .actions import Ctx
    from .model import ActionReceipt, ChaseSite, EncounterState
    from .resolve import Work

RULES = rules.RULES
SPOT = ("侦查", "追踪", "聆听", "导航")  # a sheet with no number for these reads them off INT (p. 121)
MOVING = ("move", "pursue", "flee", "cross")
FREE = ("defend", "speak", "stand", "surrender")  # no time spent: no action point
TRACK = ("追踪", "侦查")  # a pursuer that cannot see its quarry any more follows its trail (p. 119)


# -- who ----------------------------------------------------------------------------------------------
def runners(enc: EncounterState) -> list[str]:
    ch = enc.chase
    return [*ch.quarry, *ch.pursuers] if ch else []


def lost(enc: EncounterState, pursuer: str, quarry: str) -> bool:
    return enc.chase is not None and f"{pursuer}|{quarry}" in enc.chase.lost_sight


def unseen(ctx: Ctx, pid: str) -> bool:
    """No investigator who can act perceives `pid` (Ctx.hidden_from): a keeper's person that hid, was
    lost, or got away. Where it is and what it does then is the keeper's alone."""
    p = ctx.p(pid)
    if p is None or p.kind == "investigator":
        return False
    return all(ctx.hidden_from(i.id, pid) for i in ctx.enc.participants if i.kind == "investigator" and ctx.acting(i.id))


def at(enc: EncounterState, pid: str) -> str:
    p = enc.get(pid)
    return p.node if p is not None else ""


def value(w: Work, pid: str, skill: str) -> int | None:
    """A runner's number for a chase's roll: an investigator's own sheet (None: the sheet's), else the
    sheet's number, and for a creature with none, DEX (or INT for noticing) halved, an ordinary
    aptitude (p. 121)."""
    if w.investigator(pid) is not None:
        return None
    v = w.ctx.view(pid)
    prof = getattr(v, "profile", None)
    if prof is None or prof.human or skill in prof.skills or skill in prof.chars or skill in ("闪避",):
        return v.skill(skill)
    base = v.char("智力") if skill in SPOT else v.char("敏捷")
    return rules.monster_skill(base, "plain")


def points(enc: EncounterState, pid: str) -> int:
    return enc.budget.movement.get(pid, 0)


def pay(w: Work, pid: str, cost: int) -> None:
    """Action points spent; what this round cannot pay is owed to the next (the farmer, p. 114)."""
    left, owed = rules.spend(points(w.enc, pid), cost)
    w.enc.budget.movement[pid] = left
    if owed:
        w.enc.budget.owed[pid] = w.enc.budget.owed.get(pid, 0) + owed


def node_name(enc: EncounterState, nid: str) -> str:
    n = site.node(enc.chase.site, nid) if enc.chase else None
    return n.name if n is not None else nid


# -- beginning ----------------------------------------------------------------------------------------
def goal_of(ctx: Ctx, enc: EncounterState, pid: str) -> str:
    """Where a runner of the quarry makes for: the node the keeper's arrangement (or the player's flight)
    names, or a node in the world place it names; else the nearest way out."""
    ch = enc.chase
    if ch is None:
        return ""
    p = enc.get(pid)
    wanted = [d.to for d in (p.arrangement.actions if p is not None and p.arrangement else []) if d.to]
    wanted += [r.declared.to for r in enc.receipts if r.actor == pid and r.declared.do == "flee" and r.declared.to]
    for to in wanted:
        if site.node(ch.site, to) is not None:
            return to
        placed = [n.id for n in ch.site.nodes if n.place == to]
        if placed:
            far = site.distances(ch.site, at(enc, pid) or ch.site.origin)
            return min(placed, key=lambda x: (not site.node(ch.site, x).exit, far.get(x, 99)))  # type: ignore[union-attr]
    return site.nearest_exit(ch.site, at(enc, pid) or ch.site.origin)


def begin(w: Work, quarry: list[str], pursuers: list[str], chase_site: ChaseSite, *, why: str = "") -> None:
    """A chase begins (pp. 112, 124): everyone present whose speed is not rolled yet rolls CON for it
    (an extreme success one MOV faster, a failure one slower); a quarry faster than every pursuer is
    away at once, a pursuer slower than every quarry left behind; the rest are placed along the way
    the quarry runs, the slowest pursuer two locations behind the slowest quarry."""
    enc = w.enc
    lines = []
    for p in enc.participants:
        if p.presence not in ("in", "fled") or not w.standing(p.id) or p.speed is not None:
            continue
        v = w.ctx.view(p.id)
        roll = w.check(p.id, "体质", None if w.investigator(p.id) is not None else v.char("体质"))
        p.speed = rules.speed_after(v.mov(), roll.level)
        lines.append(f"{{{p.id}}}的体质 {roll.value} {roll.level.zh}：速度 {p.speed}")
    for x in quarry:
        p = enc.get(x)
        if p is not None and p.presence == "fled":
            p.presence = "in"
    speeds = {p.id: p.speed or 0 for p in enc.participants}
    lineup = rules.placement({q: speeds[q] for q in quarry if w.standing(q)}, {h: speeds[h] for h in pursuers if w.standing(h)})
    for x in lineup.escaped:
        p = enc.get(x)
        if p is not None:
            p.presence = "escaped"
            lines.append(f"{{{x}}}跑得比追的人快，转眼就没了影")
    for x in lineup.left_behind:
        lines.append(f"{{{x}}}跑得太慢，追不上了")
    staying = [q for q in quarry if q in lineup.positions]
    hunting = [h for h in pursuers if h in lineup.positions]
    # The quarry stays the quarry, whoever is already away (presence "escaped"): how it ends reads it.
    chase = ChaseState(site=chase_site.model_copy(deep=True), quarry=list(quarry), pursuers=hunting)
    enc.chase = chase
    origin = chase.site.origin
    for p in enc.participants:
        p.node = origin
    if lineup.on:
        first = min(staying, key=lambda q: lineup.positions[q])
        way, o = site.line(chase.site, origin, goal_of(w.ctx, enc, first))
        # the slowest pursuer where it began (o), the rest on from it; nobody starts on a way out
        idx = {x: o + pos for x, pos in lineup.positions.items()}
        last = len(way) - 1 - (1 if site.node(chase.site, way[-1]) is not None and site.node(chase.site, way[-1]).exit else 0)  # type: ignore[union-attr]
        over = max(0, max(idx.values()) - max(0, last))
        shift = min(over, min(idx.values()))
        for x, i in idx.items():
            p = enc.get(x)
            if p is not None:
                p.node = way[max(0, min(len(way) - 1, i - shift))]
    w.change("switched", (staying or quarry or [""])[0])
    head = "【追逐开始】" + "、".join(f"{{{q}}}" for q in staying) + "在逃，" + "、".join(f"{{{h}}}" for h in hunting) + "在追"
    w.unit.lines += [head + (f"（{why}）" if why else ""), *lines]
    w.unit.notices.append({"type": "chase_start", "chase": None, "line": head, "speeds": lines})


# -- a round --------------------------------------------------------------------------------------------
def round_points(w: Work, order: list[str]) -> None:
    """Everyone's action points this round (p. 113): one, and one more per point of speed over the
    slowest runner; what they owe from the last round paid first (p. 114)."""
    enc = w.enc
    moving = [x for x in runners(enc) if w.ctx.acting(x)]
    speeds = {p.id: p.speed if p.speed is not None else w.ctx.view(p.id).mov() for p in enc.participants if w.ctx.view(p.id)}
    slowest = min((speeds[x] for x in moving if x in speeds), default=min(speeds.values(), default=8))
    owed = dict(enc.budget.owed)
    enc.budget.movement, enc.budget.owed = {}, {}
    for x in order:
        got, still = rules.start_round(rules.action_points(speeds.get(x, slowest), slowest), owed.get(x, 0))
        enc.budget.movement[x] = got
        if still:
            enc.budget.owed[x] = still


def order(w: Work) -> list[str]:
    """Who acts in what order (p. 113): DEX, a tie by an opposed DEX roll."""
    ctx = w.ctx
    able = [p.id for p in w.enc.participants if ctx.acting(p.id)]
    dex = {x: ctx.view(x).char("敏捷") for x in able}
    ties: dict[str, int] = {}
    for x in able:
        if sum(1 for y in able if dex[y] == dex[x]) > 1:
            ties[x] = int(w.check(x, "敏捷", None if w.investigator(x) is not None else dex[x]).level)
    return rules.chase_order(dex, ties)


# -- running ----------------------------------------------------------------------------------------------
def destination(w: Work, r: ActionReceipt) -> str:
    """Where a running declaration goes: the node it names, an obstacle's node, the pursued one's node,
    or (fleeing with nowhere named) the nearest way out."""
    enc, d = w.enc, r.declared
    ch = enc.chase
    assert ch is not None
    if d.do == "pursue":
        return at(enc, d.target)
    if d.do == "cross":
        return next((n.id for n in ch.site.nodes if d.target in (n.obstacle, n.id)), "")
    if d.to and site.node(ch.site, d.to) is not None:
        return d.to
    if d.to and d.to != "away":
        placed = [n.id for n in ch.site.nodes if n.place == d.to]
        if placed:
            far = site.distances(ch.site, at(enc, r.actor))
            return min(placed, key=lambda x: far.get(x, 99))
    if r.actor in ch.quarry or d.do == "flee" or d.to == "away":
        return goal_of(w.ctx, enc, r.actor)
    return ""


def run(w: Work, r: ActionReceipt) -> None:
    """A runner spends its action points along the shortest way to where it goes, a location at a
    time: an intact obstacle on the next one is crossed on entering it (a hazard always passed, late
    and perhaps hurt on a failure; a barrier only on a success), a way out reached is away, a pursuer
    stops where its quarry is (p. 114–116)."""
    enc = w.enc
    ch = enc.chase
    assert ch is not None
    a = r.actor
    rr = next(x for x in enc.receipts if x.id == r.id)
    rr.moved = True
    goal = destination(w, r)
    here = at(enc, a)
    me = enc.get(a)
    assert me is not None
    lines: list[str] = []
    if not goal:
        lines.append(f"{{{a}}}不知道该往哪里跑，原地没动")
    elif goal == here:
        lines.append(f"{{{a}}}停在{node_name(enc, here)}")
    way = site.path(ch.site, here, goal)[1:] if goal and goal != here else []
    if goal and goal != here and not way:
        lines.append(f"{{{a}}}到不了{node_name(enc, goal)}：没有路")
    start = here
    for nid in way:
        cost = next((c for x, c in site.neighbours(ch.site, here) if x == nid), 1)
        if points(enc, a) < 1:
            break
        n = site.node(ch.site, nid)
        assert n is not None
        ob = ch.site.obstacles.get(n.obstacle)
        if ob is not None and ob.state in ("intact", "blocked"):
            passed = _cross(w, rr, a, ob, n.name, cost, lines)
            if not passed:
                break
        else:
            pay(w, a, cost)
        here = me.node = nid
        if not w.standing(a):
            break
        if n.exit and a in ch.quarry:
            me.presence = "escaped"
            w.change("fled", a)
            lines.append(f"{{{a}}}跑到了{n.name}，脱身了")
            break
        if a in ch.pursuers and any(at(enc, q) == nid and q in ch.quarry and enc.get(q).presence == "in"  # type: ignore[union-attr]
                                    for q in ch.quarry):
            break  # caught up: contact, not capture
        if r.declared.do == "pursue" and nid == at(enc, r.declared.target):
            break
    if here != start and me.presence == "in":
        lines.append(f"{{{a}}}跑到了{node_name(enc, here)}" + (f"（还剩 {points(enc, a)} 行动点）" if points(enc, a) else ""))
        _relocate(w, a, start, here)
    if here != start and me.presence == "escaped":
        _relocate(w, a, start, here)
    rr.lines += lines
    secret = unseen(w.ctx, a)  # running where no investigator sees: the players get nothing of it
    for x in lines:
        w.tell(x, "" if secret else None)


def _relocate(w: Work, who: str, before: str, now: str) -> None:
    """A keeper's person who ran into another of the world's places is there now (an investigator
    goes with the party, when the chase ends)."""
    ch = w.enc.chase
    b = site.node(ch.site, now)  # type: ignore[union-attr]
    if b is not None and b.place:
        _move(w, who, b.place)


def _move(w: Work, who: str, place: str) -> None:
    """A keeper's person is at world place `place` now: moved there with the rest of the unit."""
    ent = w.ctx.world.rec.entities.get(place)
    if w.investigator(who) is not None or ent is None or ent.kind != "location" or w.ctx.world.place(who) in ("", place):
        return
    w.unit.effects = [e for e in w.unit.effects if not (isinstance(e, effects.Relocate) and e.who == who)]
    w.unit.effects.append(effects.Relocate(who=who, place=place))


def _gather(w: Work, place: str, ran_to: dict[str, str]) -> None:
    """Everyone still in it is at world place `place` now (a chase come to blows there), in this unit's
    op: the party too, while an investigator is in it, and those of the investigators who are not
    stay where they ran to (effects.Party); the keeper's people in it moved (Relocate). What arriving
    there brings about follows the op (Unit.extra "arrive": runner._round_end)."""
    enc = w.enc
    st = w.game.state
    party = [p for p in enc.participants if p.kind == "investigator"]
    if any(p.presence == "in" for p in party):
        participants = [p.id for p in party if p.presence == "in"]
        apart = {p.id: ran_to[p.id] for p in party if p.presence != "in" and ran_to.get(p.id) not in (None, "", place)}
        if place != st.location or apart or any(w.ctx.world.place(who) != place for who in participants):
            w.unit.effects.append(effects.Party(place=place, apart=apart, participants=participants))
            w.unit.extra["arrive"] = place
            w.unit.extra["arrive_participants"] = participants
    for p in enc.participants:
        if p.presence == "in":
            _move(w, p.id, place)


def _cross(w: Work, rr: ActionReceipt, a: str, ob: ObstacleState, where: str, cost: int, lines: list[str]) -> bool:
    """One obstacle on the way, crossed by the method declared (or its first): a hazard's careful
    points first for as many bonus dice (p. 114); a barrier broken by damage (p. 115)."""
    enc = w.enc
    d = rr.declared
    method = next((m for m in ob.methods if m.id == d.method), None) or next((m for m in ob.methods if m.id != site.BREAK),
                                                                              ob.methods[0])
    if method.id == site.BREAK:
        return _break(w, rr, a, ob, lines)
    bonus = 0
    if ob.kind == "hazard" and d.careful:
        bonus = rules.careful(min(d.careful, max(0, points(enc, a) - cost)))
        if bonus:
            pay(w, a, bonus)
    me = enc.get(a)
    for e in list(me.edges if me else []):  # a companion's help on this roll
        if e.until == "next_roll" and not e.source.startswith("aim"):
            bonus += e.dice
            me.edges.remove(e)  # type: ignore[union-attr]
    roll = w.check(a, method.skill, value(w, a, method.skill), DIFFICULTY_ZH.get(method.difficulty, Level.REGULAR), max(-2, min(2, bonus)))
    rr.rolls.append(record("hazard" if ob.kind == "hazard" else "barrier", a, roll))
    w.mark(a, roll)
    if w.investigator(a) is not None:
        from .resolve import check_view

        w.unit.notices.append({"type": "dice", "who": w.ctx.name(a, "players"), **check_view(roll)})
    said = f"{method.skill} {roll.value} {roll.level.zh}" + ("，小心通过" if bonus and ob.kind == "hazard" and d.careful else "")
    if ob.kind == "barrier":
        pay(w, a, max(cost, RULES.barrier_cost))
        crossed = rules.barrier(roll.passed, opens=method.opens)
        if not crossed.moved:
            lines.append(f"{{{a}}}被{ob.what}挡住了（{said}）")
            return False
        if crossed.broken:
            ob.state, ob.by = "open", a
            lines.append(f"{{{a}}}{method.label}（{ob.what}），过去了，后面的人可以直接通过（{said}）")
        else:
            lines.append(f"{{{a}}}{method.label}（{ob.what}），过去了（{said}）")
        return True
    pay(w, a, cost)
    if roll.passed:
        lines.append(f"{{{a}}}{method.label}，过了{ob.what}（{said}）")
        return True
    crossed = rules.hazard(False, w.game.rules.dice.total(RULES.hazard_loss), harm=bool(ob.harm))  # still past it (p. 114)
    loss = crossed.lost
    pay(w, a, loss)
    hurt = ""
    if crossed.harmed:
        try:
            expr = DamageExpr.parse(ob.harm)
        except ValueError as exc:
            hurt = f"（伤害式无法使用：{exc}）"
        else:
            if not expr.none:
                amount = expr.total(w.game.rules.dice, "0")
                h = w.hurt(a, amount, "other")
                hurt = f"，受了 {expr.text()} = {amount} 点伤" + (f"（{h.describe()}）" if w.investigator(a) else
                                                               (f"（{'、'.join(h.notes)}）" if h.notes else ""))
    owe = enc.budget.owed.get(a, 0)
    lines.append(f"{{{a}}}在{ob.what}上栽了跟头（{said}），耽搁了 {loss} 行动点" + (f"，下一轮还欠 {owe} 点" if owe else "") + hurt)
    return True


def _break(w: Work, rr: ActionReceipt, a: str, ob: ObstacleState, lines: list[str]) -> bool:
    """Breaking a barrier (p. 115): no attack roll, the blow's damage off its hit points; at none it no
    longer blocks. The runner stays where it is this time."""
    from .resolve import _damage

    v = w.ctx.view(a)
    blow = actions.best_melee(v) or actions.fists(v)
    pay(w, a, RULES.barrier_cost)
    if blow is None or blow.damage is None:
        lines.append(f"{{{a}}}砸{ob.what}，可手里没有能用的东西")
        return False
    amount, text = _damage(w, v.db(), blow, "normal")
    left, broken = rules.barrier_hit(ob.hp or 0, amount)
    ob.hp = left or None  # broken: nothing of it left to break (and hit points kept are 1 or more)
    if broken:
        ob.state, ob.by = "broken", a
        lines.append(f"{{{a}}}用{blow.name}砸{ob.what}：{text}，砸开了，后面的人可以直接过去")
    else:
        lines.append(f"{{{a}}}用{blow.name}砸{ob.what}：{text}，还没砸开")
    return False


# -- the rest of what one does in a chase -------------------------------------------------------------
def hide(w: Work, rr: ActionReceipt) -> None:
    """Hiding, at any time (p. 119): Stealth at the difficulty the best Spot Hidden among the pursuers
    sets, a bonus die or two when far ahead; it takes the rest of the round. Hidden, every pursuer has
    lost sight of them."""
    enc = w.enc
    ch = enc.chase
    assert ch is not None
    a = rr.actor
    hunters = [h for h in ch.pursuers if w.ctx.acting(h) and not lost(enc, h, a)] if a in ch.quarry else \
        [f for f in w.ctx.foes(a) if w.ctx.acting(f)]
    spot = [value(w, h, "侦查") or w.ctx.view(h).skill("侦查") for h in hunters]
    lead = min((site.distance(ch.site, at(enc, h), at(enc, a)) or 0 for h in hunters), default=0)
    bonus = rules.hide_bonus(lead)
    roll = w.check(a, "潜行", value(w, a, "潜行"), rules.hide_difficulty(spot), bonus)
    rr.rolls.append(record("hide", a, roll))
    w.mark(a, roll)
    if w.investigator(a) is not None:
        from .resolve import check_view

        w.unit.notices.append({"type": "dice", "who": w.ctx.name(a, "players"), **check_view(roll)})
    enc.budget.movement[a] = 0
    if roll.passed:
        for h in hunters:
            pair = f"{h}|{a}"
            if pair not in ch.lost_sight:
                ch.lost_sight.append(pair)
        line = f"{{{a}}}在{node_name(enc, at(enc, a))}躲了起来（潜行 {roll.value} {roll.level.zh}），追的人没看见"
    else:
        line = f"{{{a}}}想躲起来，没藏住（潜行 {roll.value} {roll.level.zh}）"
    public = None  # what the players get of it: an investigator's own, all; a keeper's person, what they saw
    if w.investigator(a) is None and unseen(w.ctx, a):
        public = ""
    elif w.investigator(a) is None and roll.passed and all(
            i in hunters or w.ctx.hidden_from(i, a) for i in (p.id for p in enc.participants if p.kind == "investigator"
                                                               and w.ctx.acting(p.id))):
        public = f"{{{a}}}不见了踪影"  # the investigators lost it: not where, nor how
    rr.lines.append(line)
    w.tell(line, public)


def barrier(w: Work, rr: ActionReceipt) -> None:
    """A hazard made on purpose (p. 121): something heavy pushed across the way behind (STR and an
    action point); those who come after must get past it, as a barrier (force it, or climb over)."""
    enc = w.enc
    ch = enc.chase
    assert ch is not None
    a = rr.actor
    here = at(enc, a)
    pay(w, a, RULES.wardrobe_cost)
    roll = w.check(a, "力量", value(w, a, "力量"))
    rr.rolls.append(record("barrier", a, roll))
    if w.investigator(a) is not None:
        from .resolve import check_view

        w.unit.notices.append({"type": "dice", "who": w.ctx.name(a, "players"), **check_view(roll)})
    n = site.node(ch.site, here)
    if roll.passed and n is not None and not (n.obstacle and ch.site.obstacles[n.obstacle].state in ("intact", "blocked")):
        from ...module.schema import Method

        oid = f"made_{here}_{enc.round}"
        ch.site.obstacles[oid] = ObstacleState(id=oid, kind="barrier", what="挡在路上的杂物", hp=10, state="blocked", by=a,
                                               methods=[Method(id="force", label="推开", skill="力量", opens=True),
                                                        Method(id="climb", label="翻过去", skill="攀爬")])
        n.obstacle = oid
        line = f"{{{a}}}把身边的东西推倒，挡住了{n.name}（力量 {roll.value} {roll.level.zh}）"
    else:
        line = f"{{{a}}}想挡住身后的路，没能弄成（力量 {roll.value} {roll.level.zh}）"
    rr.lines.append(line)
    w.tell(line, "" if unseen(w.ctx, a) else None)


def stand(w: Work, rr: ActionReceipt) -> None:
    a = rr.actor
    them = [x for x in w.ctx.foes(a) if (d := site.distance(w.enc.chase.site, at(w.enc, a), at(w.enc, x))) is not None and d <= 1]  # type: ignore[union-attr]
    line = f"{{{a}}}停下脚步，转身迎向" + ("、".join(f"{{{x}}}" for x in them) if them else "追来的人")
    rr.lines.append(line)
    w.unit.lines.append(line)


# -- the end of a round -------------------------------------------------------------------------------------
def round_end(w: Work) -> str:
    """What the end of a chase's round decides, in order: who lost sight of whom (a pursuer that cannot
    see its quarry any more rolls Track, p. 119), who got away (lost by every pursuer still after
    them), whether it comes to blows (someone turned to fight with the rest within a location, or a
    quarry held fast). "fight" when it does, else ""."""
    enc, ctx = w.enc, w.ctx
    ch = enc.chase
    assert ch is not None
    hunters = [h for h in ch.pursuers if enc.get(h) and enc.get(h).presence == "in" and w.standing(h)]  # type: ignore[union-attr]
    prey = [q for q in ch.quarry if enc.get(q) and enc.get(q).presence == "in" and w.standing(q)]  # type: ignore[union-attr]
    for h in hunters:
        for q in prey:
            if lost(enc, h, q):
                continue
            far = site.distance(ch.site, at(enc, h), at(enc, q))
            if far is not None and not rules.out_of_sight(far, blocked=site.sight_blocked(ch.site, at(enc, h), at(enc, q))):
                continue
            skill = max(TRACK, key=lambda s: value(w, h, s) or ctx.view(h).skill(s))
            roll = w.check(h, skill, value(w, h, skill))
            mine = None if w.investigator(h) is not None else ""  # a keeper's pursuer out of their sight: its roll is its own
            if rules.lost_track(roll.level):
                ch.lost_sight.append(f"{h}|{q}")
                w.tell(f"{{{q}}}跑出了{{{h}}}的视线，{{{h}}}没找到踪迹，跟丢了（{skill} {roll.value} {roll.level.zh}）", mine)
                w.change("fled", q)
            else:
                w.tell(f"{{{q}}}跑出了{{{h}}}的视线，{{{h}}}循着踪迹追了上去（{skill} {roll.value} {roll.level.zh}）", mine)
    for q in prey:
        if hunters and all(lost(enc, h, q) for h in hunters):
            enc.get(q).presence = "escaped"  # type: ignore[union-attr]
            w.unit.lines.append(f"{{{q}}}甩掉了追的人，脱身了")
    if where := contact(w):
        to_fight(w, where)
        return "fight"
    return ""


def contact(w: Work) -> str:
    """Where it comes to blows (p. 116; design §3.5), "" while it does not: the location of someone at
    the same location as an opponent who turned to fight this round, of a quarry held fast by a
    pursuer, or struck down where a pursuer stands; when everyone still in it is within one location of
    there (no split party, the user's call)."""
    enc, ctx = w.enc, w.ctx
    ch = enc.chase
    assert ch is not None
    stood = [r.actor for r in enc.receipts if r.round == enc.round and r.declared.do == "stand" and r.phase == "committed"
             and any(at(enc, x) == at(enc, r.actor) for x in ctx.foes(r.actor) if w.standing(x))]
    held = [h.held for h in enc.scene.holds if h.held in ch.quarry and h.by in ch.pursuers]
    downed = [q for q in ch.quarry if (p := enc.get(q)) is not None and p.presence == "in" and not w.standing(q)
              and any(at(enc, h) == at(enc, q) and w.standing(h) for h in ch.pursuers)]  # struck down where it was caught
    meet = (stood or held or downed or [""])[0]
    if not meet:
        return ""
    where = at(enc, meet)
    active = [p.id for p in enc.participants if p.presence == "in" and w.standing(p.id)]
    return where if all((site.distance(ch.site, where, at(enc, x)) or 0) <= 1 for x in active) else ""


def to_fight(w: Work, where: str) -> None:
    """The chase becomes a fight at `where` (contact's): everyone still in it there, those face to
    face engaged. The world place is that location's, and the same unit moves the world there: the
    encounter's place (SetMode.place), the keeper's people in it (Relocate), the party with the
    investigators in it (effects.Party), and those of them who got away elsewhere are left apart
    where they ran to (GameState.apart)."""
    enc, ctx = w.enc, w.ctx
    ch = enc.chase
    assert ch is not None
    ran_to = places(ctx, enc)  # where each one is, while the site still says it
    met = [p.id for p in enc.participants if p.presence == "in" and p.node == where]  # face to face there
    meet = met[0] if met else ""
    n = site.node(ch.site, where)
    for a in met:
        for b in met:
            if ctx.opposed(a, b) and not any({e.a, e.b} == {a, b} for e in enc.scene.engaged):
                enc.scene.engaged.append(Engagement(a=a, b=b, since=enc.round))
    for p in enc.participants:
        if p.presence == "in":
            p.node = where
    if n is not None and n.place and ctx.world.rec.entities.get(n.place) is not None:
        enc.place = n.place
    _gather(w, enc.place, ran_to)
    enc.mode = "fight"
    enc.chase = None
    enc.switches.append(f"r{enc.round}:fight")
    w.change("switched", meet or "")
    hunter = next((x for x in ch.pursuers if (p := enc.get(x)) is not None and p.node == where and p.presence == "in"), "")
    from .views import portrait

    w.unit.lines.append(f"【追逐转为战斗】在{n.name if n else '原地'}扭打在一起")
    w.unit.notices.append({"type": "chase_end", "end": "caught", "text": f"追逐结束：在{n.name if n else '原地'}扭打在一起",
                           "hunter": hunter, "hunter_name": ctx.name(hunter, "players") if hunter else "",
                           "portrait": portrait(w.game, hunter) if hunter else ""})
    w.unit.notices.append({"type": "fight_start", "fight": None, "line": "【追逐转为战斗】", "surprise": ""})


def verdict(ctx: Ctx) -> str:
    """How a chase that has run its course ends ("" while some pair is still running): the quarry got
    away (fled, or escaped for the keeper's side), every quarry down (lost, or won). Never lost while
    an investigator can still act: it goes on."""
    enc = ctx.enc
    ch = enc.chase
    if ch is None:
        return ""

    prey = [q for q in ch.quarry if ctx.acting(q)]
    hunters = [h for h in ch.pursuers if ctx.acting(h)]
    if prey and hunters and any(not lost(enc, h, q) for h in hunters for q in prey):
        return ""
    party = any(ctx.side(q) == "party" for q in ch.quarry)
    away = any((p := enc.get(q)) is not None and p.presence == "escaped" for q in ch.quarry)
    if prey and not hunters:
        away = True
    if party:
        if not (away or prey) and any(p.kind == "investigator" and ctx.acting(p.id) for p in enc.participants):
            return ""
        return "fled" if away or prey else "lost"
    return "escaped" if away or prey else "won"


def places(ctx: Ctx, enc: EncounterState) -> dict[str, str]:
    """Where everyone ends up in the world: the place of the location they ran to (an escaped one, of
    their way out), else where the encounter is."""
    ch = enc.chase
    out = {}
    for p in enc.participants:
        n = site.node(ch.site, p.node) if ch is not None else None
        place = n.place if n is not None and n.place else enc.place
        out[p.id] = place if ctx.world.rec.entities.get(place) is not None else enc.place
    return out


def party_place(game, enc: EncounterState, where: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Where the party goes when the chase ends (no split party, the user's call): the place most of
    the human investigators ended at (on a tie, the speaking one's); the others, one out cold where it
    began too, are left there (GameState.apart, which the keeper's brief and the plan's bearings name)
    until the party comes there or someone brings them back (Game._arrive, Game._rejoin)."""
    st = game.state
    humans = [i.id for i in st.investigators if i.id in st.human] or [st.investigator.id]
    counts: dict[str, int] = {}
    for x in humans:
        if x in where:
            counts[where[x]] = counts.get(where[x], 0) + 1
    if not counts:
        return st.location, {}
    top = max(counts.values())
    best = [k for k, v in counts.items() if v == top]
    place = where.get(st.investigator.id) if where.get(st.investigator.id) in best else best[0]
    apart = {i.id: where[i.id] for i in st.investigators if i.id in where and where[i.id] != place
             and (p := enc.get(i.id)) is not None and p.presence in ("in", "escaped")}
    return place or st.location, apart


def view(ctx: Ctx, game) -> dict:
    """The chase as the screens draw it (tui/widgets/fight.chase_route): a straight way through the site
    from the hindmost pursuer to the frontmost quarry and on to the way out, everyone on it by the
    location nearest them; public names, a foe's wounds in words, no obstacle's harm. Only what the
    investigators perceive: a keeper's person none of them sees (`unseen`) has no place on it (no
    `pos`, no `node`), and the way is drawn from those they see."""
    from .views import portrait, status, wound_word

    enc = ctx.enc
    ch = enc.chase
    if ch is None:
        return {"round": enc.round, "quarry": "party", "legs": [], "runners": []}
    quarry_side = "party" if any(ctx.side(q) == "party" for q in ch.quarry) else "foe"
    hid = {p.id for p in enc.participants if unseen(ctx, p.id)}
    prey = [q for q in ch.quarry if enc.get(q) is not None and q not in hid] or [
        p.id for p in enc.participants if ctx.side(p.id) == quarry_side and p.id not in hid]
    hunters = [h for h in ch.pursuers if enc.get(h) is not None and h not in hid] or [
        p.id for p in enc.participants if ctx.side(p.id) != quarry_side and p.id not in hid]
    origin = ch.site.origin
    rear = max(hunters, key=lambda h: max((site.distance(ch.site, at(enc, h), at(enc, q)) or 0 for q in prey), default=0),
               default="")
    start = at(enc, rear) if rear else origin
    front = max(prey, key=lambda q: site.distance(ch.site, start, at(enc, q)) or 0, default="")
    end = at(enc, front) if front else start
    way = site.path(ch.site, start, end) or [start]
    beyond = goal_of(ctx, enc, front) if front else site.nearest_exit(ch.site, end)
    tail = site.path(ch.site, end, beyond)[1:] if beyond else []
    way = way + [x for x in tail if x not in way]
    if len(way) == 1 and (out := site.nearest_exit(ch.site, way[0])):
        way = site.path(ch.site, way[0], out)
    legs = []
    for nid in way:
        n = site.node(ch.site, nid)
        ob = ch.site.obstacles.get(n.obstacle) if n is not None else None
        legs.append({"id": nid, "name": n.name if n else nid, "look": n.look if n else "", "exit": bool(n and n.exit),
                     "obstacle": None if ob is None else {
                         "kind": ob.kind, "what": ob.what, "skill": ob.methods[0].skill if ob.methods else "",
                         "difficulty": ob.methods[0].difficulty if ob.methods else "常规", "open": ob.state in ("open", "broken"),
                         "ways": [m.label for m in ob.methods]}})
    runners_out = []
    for p in enc.participants:
        v = ctx.view(p.id)
        if v is None or (p.presence not in ("in", "escaped") and p.id not in runners(enc)):
            continue
        dist = site.distances(ch.site, p.node) if p.node else {}
        pos = min(range(len(way)), key=lambda i: (dist.get(way[i], 99), -i if p.id in prey else i)) if way else 0
        b = v.body()
        party = p.side == "party"
        gone = p.presence == "escaped"
        # an investigator the pursuers lost is marked hidden where they are; a keeper's person none of them sees has no place
        lost_by_all = p.id in ch.quarry and any(ctx.acting(h) for h in ch.pursuers) and all(
            lost(enc, h, p.id) for h in ch.pursuers if ctx.acting(h))
        runners_out.append({"id": p.id, "name": ctx.name(p.id, "players"), "kind": p.kind, "side": "party" if party else "foe",
                            "pos": None if p.id in hid else len(way) - 1 if gone and p.id in ch.quarry else pos,
                            "speed": p.speed if p.speed is not None else v.mov(),
                            "actions": points(enc, p.id), "status": status(ctx, p.id), "wound": wound_word(b),
                            "hidden": p.id in hid or lost_by_all or gone, "hp": b.hp if party else None, "hp_max": b.hp_max if party else None,
                            "portrait": portrait(game, p.id), "node": "" if p.id in hid else p.node})
    return {"round": enc.round, "quarry": quarry_side, "legs": legs, "runners": runners_out}


__all__ = ["begin", "hide", "order", "round_end", "round_points", "run", "unseen", "verdict", "view"]
