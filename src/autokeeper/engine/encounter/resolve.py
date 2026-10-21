"""One unit of a fight, worked out: the dice it throws, what they mean by the rules, and the effects
and encounter changes they come to (invariant I2: nothing changes until the unit's one op lands).

A unit works on `Work`: a copy of the encounter and of everyone's body as the unit leaves them. It
throws its dice through the game's current dice (the turn's journal: a die thrown before a crash
is the same die after it), asks rules/melee, rules/firearms and rules/wounds what they mean, and
writes effects (engine/effects) and the encounter as it should be. `Work.changes` turns that copy
into the encounter's absolute changes; the runner lands both in one world op (EncounterStep).

Lines are the keeper's account, in keeper form (`{id}` for whoever is named: engine/encounter/telling.py
reads them out per audience); notices are what the screens are sent, in the shapes they always had
(fight_act, fight_state, damage, dice), sent only after the op landed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...rules import combat as rules
from ...rules import firearms, melee, wounds
from ...rules.bcdice import Level
from ...rules.coc import DIFFICULTY_ZH, Check
from ...rules.dice_expr import DamageExpr
from ...rules.weapons import BRAWL, weapon_in
from .. import effects
from . import actions, choices, telling
from .actions import UNARMED_ID, Ctx
from .model import (
    ActionReceipt,
    AddChanges,
    AddStakes,
    Change,
    Close,
    Compact,
    Cover,
    Edge,
    EncounterState,
    Engagement,
    Hold,
    RollRecord,
    SetBudget,
    SetChase,
    SetClock,
    SetCursor,
    SetFills,
    SetMode,
    SetParticipant,
    SetPending,
    SetPhase,
    SetReceipt,
    SetRound,
    SetScene,
    SetStake,
)
from .plan import DeclaredAction

OLD_REACTION = {"dodge": "dodge", "fight_back": "fight", "cover": "cover", "none": "none"}


@dataclass
class Unit:
    effects: list = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # keeper form, {id}
    notices: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    rest: dict | None = None  # an `other` ruling's remainder for the turn's investigator (clues, takes, changes)
    stop: bool = False
    fired: list[str] = field(default_factory=list)  # flags of the stakes an encounter's end fired (set after it is archived)
    extra: dict = field(default_factory=dict)  # what the runner does after the op (an end's party arrival)


def check_view(c: Check) -> dict:
    from ..game import _check_view

    return _check_view(c)


def record(purpose: str, who: str, c: Check) -> RollRecord:
    return RollRecord(purpose=purpose, who=who, skill=c.name, target=c.target,
                      difficulty={Level.REGULAR: "常规", Level.HARD: "困难", Level.EXTREME: "极难"}.get(c.difficulty, "常规"),
                      bonus=c.bonus, value=c.value, level=c.level.zh, view=check_view(c))


class Work:
    """A unit's working copy of the encounter and the bodies in it."""

    def __init__(self, game: Any, ctx: Ctx, key: str):
        self.game, self.ctx, self.key = game, ctx, key
        self.enc: EncounterState = ctx.enc.model_copy(deep=True)
        self.bodies: dict[str, wounds.Body] = {}
        self.unit = Unit()
        self.wounds = 0
        self.luck_left: dict[str, int] = {}
        self.after: list = []  # what happens outside the world once the unit's op has landed

    # -- who ------------------------------------------------------------------------------------
    def investigator(self, pid: str):
        return next((i for i in self.game.state.investigators if i.id == pid), None)

    def body(self, pid: str) -> wounds.Body:
        if pid not in self.bodies:
            v = self.ctx.view(pid)
            assert v is not None, pid
            self.bodies[pid] = v.body()
        return self.bodies[pid]

    def standing(self, pid: str) -> bool:
        b = self.body(pid)
        return not b.dead and b.conscious

    # -- dice -----------------------------------------------------------------------------------
    def check(self, pid: str, skill: str, value: int | None = None, difficulty: Level = Level.REGULAR, bonus: int = 0) -> Check:
        """A roll of anyone's, through the game's current dice. An investigator's sheet is not ticked
        here: a success is a Mark effect, landing with the unit."""
        inv = self.investigator(pid)
        if inv is not None:
            return self.game.rules.check(inv, skill, difficulty, bonus, mark=False, target=value)
        v = self.ctx.view(pid)
        return rules.check(self.game.rules.dice, skill, value if value is not None else v.skill(skill), difficulty, bonus)

    def mark(self, pid: str, c: Check) -> None:
        """An investigator's success ticked for improvement (not with a bonus die, p. 94's spirit)."""
        from ...rules.catalog import CHARACTERISTICS, NO_IMPROVEMENT

        if self.investigator(pid) is not None and c.passed and c.bonus <= 0 and c.name not in NO_IMPROVEMENT \
                and c.name not in CHARACTERISTICS:
            self.unit.effects.append(effects.Mark(investigator=pid, skill=c.name))

    def luck(self, pid: str, c: Check) -> Check:
        """An important NPC spends from its Luck pool on its own failed roll, as far as the keeper's
        arrangement allows (Keeper Rulebook p. 168, optional)."""
        from .views import luck_pool

        p = self.enc.get(pid)
        if c.passed or p is None or p.arrangement is None or not p.arrangement.luck:
            return c
        need = c.luck_needed()
        pool = self.luck_left.get(pid, luck_pool(self.ctx, pid))
        if need is None or pool is None or need > pool or need > p.arrangement.luck:
            return c
        c.luck_spent = need
        self.luck_left[pid] = pool - need
        self.unit.effects.append(effects.Luck(who=pid, left=pool - need))
        # The keeper's alone: the players see a roll that passed, not the pool behind it.
        self.unit.lines.append(f"【幸运池】{{{pid}}}花了 {need} 点幸运，这一下算成功（幸运池还剩 {pool - need}）")
        return c

    # -- harm -----------------------------------------------------------------------------------
    def hurt(self, pid: str, amount: int, means: wounds.Means) -> effects.Hurt:
        """A wound, rolled (the CON a major wound calls for) and applied to the working body."""
        self.wounds += 1
        injury = f"{self.key}:w{self.wounds}"
        w = effects.harm(self.game, pid, amount, means=means, injury=injury, body=self.body(pid))
        w.round = self.enc.round
        hurt = effects.wound(self.body(pid), w, minutes=self.game.state.minutes, round=self.enc.round)
        self.bodies[pid] = hurt.body
        self.unit.effects.append(w)
        if hurt.result.major and not hurt.body.dead and pid not in self.enc.scene.prone:
            self.enc.scene.prone.append(pid)  # a major wound knocks them down at once (p. 100)
        if not self.standing(pid):
            self.fell(pid)
        inv = self.investigator(pid)
        if inv is not None:
            self.unit.notices.append({"type": "damage", "who": inv.name, "text": hurt.describe(), "fighter": pid,
                                      "injury": injury})
        return hurt

    def apply(self, pid: str, e) -> None:
        """A healing, waking, stabilizing or killing effect, on the working body too."""
        b = self.body(pid)
        if isinstance(e, effects.Heal):
            b = effects.heal(b, e)
        elif isinstance(e, effects.Stabilize):
            b = effects.stabilize(b, self.game.state.minutes)
        elif isinstance(e, effects.Wake):
            b = wounds.wake(b)
        elif isinstance(e, effects.Die):
            b = effects.die(b)
            self.fell(pid)
        self.bodies[pid] = b
        self.unit.effects.append(e)

    def fell(self, pid: str) -> None:
        """Down: out of every melee and hold, a material change for the keeper's people."""
        sc = self.enc.scene
        sc.engaged = [e for e in sc.engaged if pid not in (e.a, e.b)]
        sc.holds = [h for h in sc.holds if h.by != pid]
        self.change("down", pid)

    def change(self, kind: str, who: str) -> None:
        c = Change(round=self.enc.round, kind=kind, who=who)  # type: ignore[arg-type]
        if c not in self.enc.changes:
            self.enc.changes.append(c)

    def engage(self, a: str, b: str) -> None:
        if not any({e.a, e.b} == {a, b} for e in self.enc.scene.engaged):
            self.enc.scene.engaged.append(Engagement(a=a, b=b, since=self.enc.round))

    # -- the encounter as it should be ---------------------------------------------------------
    def changes(self) -> list:
        return diff(self.ctx.enc, self.enc)


def diff(old: EncounterState, new: EncounterState) -> list:
    """The absolute changes that make `old` into `new` (engine/encounter/model.apply of them gives it)."""
    out: list = []
    kept = {r.id for r in new.receipts}
    if new.history != old.history or any(r.id not in kept for r in old.receipts):
        out.append(Compact(history=list(new.history), receipts=[r.id for r in old.receipts if r.id in kept]))
    olds = {p.id: p for p in old.participants}
    out += [SetParticipant(participant=p) for p in new.participants if olds.get(p.id) != p]
    oldr = {r.id: r for r in old.receipts}
    out += [SetReceipt(receipt=r) for r in new.receipts if oldr.get(r.id) != r]
    if (new.round, new.order) != (old.round, old.order):
        out.append(SetRound(round=new.round, order=list(new.order), cursor=new.cursor, budget=new.budget))
    else:
        if new.cursor != old.cursor:
            out.append(SetCursor(cursor=new.cursor))
        if new.budget != old.budget:
            out.append(SetBudget(budget=new.budget))
    if new.scene != old.scene:
        out.append(SetScene(scene=new.scene))
    if new.chase != old.chase:
        out.append(SetChase(chase=new.chase))
    if new.mode != old.mode or new.switches != old.switches:
        out.append(SetMode(mode=new.mode, switch=new.switches[-1] if len(new.switches) > len(old.switches) else ""))
    if len(new.stakes) > len(old.stakes):
        out.append(AddStakes(stakes=new.stakes[len(old.stakes):]))
    out += [SetStake(index=i, fired=s.fired) for i, s in enumerate(new.stakes) if i < len(old.stakes) and s.fired != old.stakes[i].fired]
    if new.clock != old.clock:
        out.append(SetClock(clock=new.clock))
    if new.fills != old.fills:
        out.append(SetFills(fills=new.fills))
    if len(new.changes) > len(old.changes):
        out.append(AddChanges(changes=new.changes[len(old.changes):]))
    if new.ended is not None and old.ended is None:
        out.append(Close(record=new.ended))
    else:
        if new.phase != old.phase and new.phase in ("open", "aftermath"):
            out.append(SetPhase(phase=new.phase))  # type: ignore[arg-type]
        if new.pending != old.pending:
            out.append(SetPending(pending=new.pending))
    return out


# -- the round's order ------------------------------------------------------------------------------
def firing(ctx: Ctx, pid: str, d: DeclaredAction | None) -> bool:
    if d is None or d.do != "shoot":
        return False
    v = ctx.view(pid)
    opt = actions.option(v, d.attacks[0].with_ if d.attacks else "", "shoot") if v is not None else None
    return opt is not None and opt.firearm and opt.usable


def round_order(ctx: Ctx, declared: dict[str, DeclaredAction | None]) -> list[str]:
    """Who acts in what order (Keeper Rulebook p. 86): those present and able to act, by DEX, +50
    for a readied firearm the actor means to fire this round (p. 95), the better fighter on a tie,
    an investigator first on a tie still (the project's)."""
    slots = []
    for p in ctx.enc.participants:
        if not ctx.acting(p.id):
            continue
        v = ctx.view(p.id)
        best = max([v.skill(BRAWL), *(a.value for a in v.attacks())])
        slots.append(rules.Slot(p.id, v.char("敏捷"), best, 50 if firing(ctx, p.id, declared.get(p.id)) else 0,
                                p.kind == "investigator"))
    return rules.turn_order(slots)


# -- an attack -------------------------------------------------------------------------------------
def _side(ctx: Ctx, pid: str) -> str:
    return "party" if ctx.side(pid) == "party" else "foe"


def _means(opt, kind: str) -> wounds.Means:
    if kind == "firearm":
        return "firearm"
    return "unarmed" if opt.id == UNARMED_ID else "blow"


def _damage(w: Work, pid_db: str, opt, mode: str, *, band: int = 0) -> tuple[int, str]:
    """A blow's damage, rolled: (amount, how it came, as "1D3 = 2" or "最大 6")."""
    db = "0" if opt.firearm else pid_db
    plan = melee.damage_plan(opt.damage, db, mode, impale=opt.impale, band=band)  # type: ignore[arg-type]
    total = plan.total(w.game.rules.dice)
    if mode == "extreme":
        return total, plan.text if not plan.roll else f"{plan.text} = {total}"
    return total, f"{plan.text} = {total}" if plan.roll else str(total)


def react_choice(ctx: Ctx, defender: str, kind: str) -> tuple[str, str]:
    """How a defender who is not asked answers: the player's preset when it fits, the keeper's
    arrangement, the rules' default for a keeper's person (fight back, p. 87): (reaction, by)."""
    p = ctx.p(defender)
    opts = choices.reaction_options(ctx, defender, kind)
    if opts == ["none"]:
        return "none", "rule"
    if p is not None and p.controller == "keeper":
        want = p.arrangement.react if p.arrangement is not None else p.react or melee.keeper_default()
        if want in opts:
            return want, "keeper"
        return next(x for x in ("fight_back", "dodge", "none") if x in opts), "rule"
    if p is not None and p.react in opts:
        return p.react, "preset"  # type: ignore[return-value]
    # Diving for cover costs the next action: never chosen for anyone who did not choose it.
    return next(x for x in ("dodge", "none") if x in opts), "rule"


def strike(w: Work, r: ActionReceipt, k: int, pick) -> None:
    """Attack `k` of receipt `r` settled: the attack roll, the answer chosen for it, the damage and
    what it does, the gun's rounds and jam; a manoeuvre's effect instead of damage."""
    ctx, enc = w.ctx, w.enc
    a, d = r.actor, pick.target
    av, dv = ctx.view(a), ctx.view(d)
    do = r.declared.do
    opt = actions.option(av, pick.with_, do)
    receipt = next(x for x in enc.receipts if x.id == r.id)
    reaction = receipt.reactions[k].chosen if k < len(receipt.reactions) else "none"
    an, dn = f"{{{a}}}", f"{{{d}}}"
    ev = {"type": "fight_act", "round": enc.round, "attacker": a, "defender": d, "attacker_name": ctx.name(a, "players"),
          "defender_name": ctx.name(d, "players"), "attacker_side": _side(ctx, a), "defender_side": _side(ctx, d),
          "hurt_side": "", "kind": "melee", "attack": opt.name if opt else "徒手", "verb": "", "roll": None, "answer": None,
          "reaction": OLD_REACTION.get(reaction or "none", "none"), "winner": "none", "hurt": "", "damage": 0,
          "damage_text": "", "effect": "", "extreme": False, "jammed": False, "empty": False, "notes": [], "line": "",
          "dice": None, "dice_who": ""}
    done = lambda line: _done(w, receipt, k, ev, line)  # noqa: E731
    if opt is None:
        return done(f"{an}手里没有能用的攻击，这一下落空了")
    kind = choices.attack_kind(opt)
    maneuver = do == "maneuver"
    if any(c.who == a and not c.partial for c in enc.scene.cover):  # striking from hiding gives them away
        enc.scene.cover = [c for c in enc.scene.cover if c.who != a]
    head = f"{an}用{opt.name}{'射击' if kind == 'firearm' else ('施展' + r.declared.maneuver) if maneuver else '攻击'}{dn}"
    bonus, reasons, aimed = 0, [], False
    # edges waiting for the attacker's next roll (a companion's help); a round's aim only for the next shot (p. 95)
    me = enc.get(a)
    for e in list(me.edges if me else []):
        if e.until != "next_roll":
            continue
        if e.source.startswith("aim"):
            if kind == "firearm":
                aimed = True
                me.edges.remove(e)
            continue
        bonus += e.dice
        reasons.append(e.why)
        me.edges.remove(e)
    if kind == "firearm":
        ev["kind"] = "shot"
        return _shot(w, receipt, k, pick, opt, reaction, head, bonus, reasons, ev, aimed=aimed)
    ev["kind"] = "maneuver" if maneuver else "melee"
    if maneuver:
        ok, penalty = melee.maneuver_feasible(av.build(), dv.build())
        if not ok:
            return done(f"{head}：对方体格大得多，这个战技做不到")
        if penalty:
            bonus -= penalty
            reasons.append(f"对方体格大 {penalty}")
    answering = reaction not in (None, "none")
    allowance = max(1, dv.attacks_per_round())
    if answering and kind == "melee" and melee.outnumbered_bonus(enc.budget.responses.get(d, 0), allowance):
        bonus += 1
        reasons.append("以多打少")
    if melee.disadvantage_bonus(prone=d in enc.scene.prone, held=any(h.held == d for h in enc.scene.holds)):
        bonus += 1
        reasons.append("对方倒在地上" if d in enc.scene.prone else "对方被制住")
    skill = actions.skill_of(opt, do)
    value = av.skill(BRAWL) if maneuver and opt.id == UNARMED_ID else opt.value
    roll = w.luck(a, w.check(a, skill, value, bonus=melee.clamp(bonus)))
    receipt.rolls.append(record("maneuver" if maneuver else "attack", a, roll))
    ev["roll"] = check_view(roll)
    answer = None
    parts = [f"{roll.name} {roll.target} → {roll.value} {roll.level.zh}" + (f"（{'、'.join(reasons)}）" if reasons else "")]
    if answering:
        if reaction == "fight_back":
            best = actions.best_melee(dv)
            answer = w.check(d, best.skill if best and best.id != UNARMED_ID else BRAWL, best.value if best else dv.skill(BRAWL))
            how = "反击"
        else:
            answer = w.check(d, "闪避")
            how = "闪避"
        answer = w.luck(d, answer)
        receipt.rolls.append(record(reaction, d, answer))
        ev["answer"] = check_view(answer)
        parts.append(f"{dn}{how}：{answer.value} {answer.level.zh}")
        if kind == "melee":
            enc.budget.responses[d] = enc.budget.responses.get(d, 0) + 1
        w.mark(d, answer)
    out = melee.melee_outcome(roll.level, answer.level if answer else None,
                              "none" if not answering else reaction, maneuver=maneuver)  # type: ignore[arg-type]
    ev["winner"] = out.winner
    if kind == "melee":
        w.engage(a, d)
    if out.winner == "attacker":
        w.mark(a, roll)
    elif out.winner == "defender":
        w.mark(d, answer)  # type: ignore[arg-type]
    acc = f"{head}：" + "；".join(parts)
    if out.winner == "none":
        return done(acc + "。" + ("被躲开了" if reaction == "dodge" and answer is not None and answer.passed else "没有得手"))
    if out.effect:
        return done(acc + "。" + _maneuver(w, a, d, r.declared.maneuver, ev, attacker_won=out.winner == "attacker"))
    if out.winner == "attacker":
        amount, text = _damage(w, av.db(), opt, out.mode)
        hurt, hit, by = d, opt, ""
    else:
        best = actions.best_melee(dv)
        amount, text = _damage(w, dv.db(), best, "normal") if best is not None else (0, "0")
        hurt, hit, by = a, best, "反击得手，"
    ev["extreme"] = out.mode == "extreme"
    result = _wound(w, hurt, amount, _means(hit, "melee") if hit is not None else "blow", text, ev, firearm=False)
    return done(acc + f"。{by}{result}")


def _wound(w: Work, pid: str, amount: int, means, text: str, ev: dict, *, firearm: bool) -> str:
    hurt = w.hurt(pid, amount, means)
    notes = [hurt.describe()] if w.investigator(pid) is not None else list(hurt.notes)
    ev.update(hurt=pid, hurt_side=_side(w.ctx, pid), damage=amount, damage_text=text, notes=notes)
    v = w.ctx.view(pid)
    prof = getattr(v, "profile", None)
    if prof is not None and (cue := telling.blow_cue(prof, firearm=firearm, absorbed=hurt.result.absorbed, taken=hurt.result.taken)):
        w.unit.lines.append(telling.cue(cue))
    words = "、".join(n for n in notes if n)
    return f"{{{pid}}}受到 {text}" + (f"，{words}" if words else "")


def _maneuver(w: Work, a: str, d: str, which: str, ev: dict, *, attacker_won: bool) -> str:
    """A manoeuvre that worked (p. 89): disarm or seize (the real item moves), knock down (prone, the
    next action spent getting up), hold (held fast until they break free)."""
    enc, ctx = w.enc, w.ctx
    if not attacker_won:  # the defender's own manoeuvre answering: it frees itself and throws the attacker off
        ev["effect"] = "挣脱"
        return f"{{{d}}}反手一推，挣开了{{{a}}}"
    ev["effect"] = which
    dv = ctx.view(d)
    if enc.mode == "chase" and which not in ("缴械", "夺取", "制住"):  # in a chase: as a failed hazard, 1D3 points lost (p. 116)
        from ...rules.chase import RULES as CHASE
        from .chase import pay

        loss = w.game.rules.dice.total(CHASE.maneuver_loss)
        pay(w, d, loss)
        if which == "推倒" and d not in enc.scene.prone:
            enc.scene.prone.append(d)
        return f"把{{{d}}}撞倒在地，他耽搁了 {loss} 行动点"
    if which in ("缴械", "夺取"):
        held = [x for x in dv.attacks() if not x.natural and x.item]
        if not held:
            ev["effect"] = ""
            if any(x.natural and x.id != UNARMED_ID for x in dv.attacks()):
                return f"{{{d}}}的爪牙长在身上，夺不走"
            return f"{{{d}}}手里本来就没有武器"
        item = max(held, key=lambda x: (x.firearm and x.usable, x.value))
        to = a if which == "夺取" else enc.place
        w.unit.effects.append(effects.Drop(item=item.item, to=to))
        w.change("disarmed", d)
        return f"把{{{d}}}手里的{item.name}" + ("夺了过来" if which == "夺取" else "打掉在地上")
    if which == "推倒":
        if d not in enc.scene.prone:
            enc.scene.prone.append(d)
        enc.budget.lost_next[d] = "爬起来"
        return f"把{{{d}}}摔倒在地，他下一次行动得先爬起来"
    enc.scene.holds = [h for h in enc.scene.holds if h.held != d] + [Hold(held=d, by=a, since=enc.round)]
    return f"死死制住了{{{d}}}"


def _shot(w: Work, receipt: ActionReceipt, k: int, pick, opt, reaction, head: str, bonus: int, reasons: list[str],
          ev: dict, *, aimed: bool = False) -> None:
    """Shots (pp. 95–98): one roll each against the firearms skill, the dice for the circumstances;
    rounds spent, a malfunction a dud or a jam by the gun's action."""
    ctx, enc = w.ctx, w.enc
    a, d = receipt.actor, pick.target
    loaded = opt.loaded
    parts: list[str] = []
    dived = False
    if reaction == "cover":
        dive = w.check(d, "闪避")
        receipt.rolls.append(record("cover", d, dive))
        ev["answer"] = check_view(dive)
        cover = firearms.dive_for_cover(dive.level, attacks_per_round=ctx.view(d).attacks_per_round())
        dived = cover.penalty < 0
        enc.budget.lost_next[d] = "扑向掩体"
        if dived and not any(c.who == d for c in enc.scene.cover):  # behind something now: half hidden for later shots
            enc.scene.cover.append(Cover(who=d, what="掩体", partial=True, since=enc.round))
        parts.append(f"{{{d}}}扑向掩体：{dive.value} {dive.level.zh}")
        w.mark(d, dive)
    melee_with = [x for e in enc.scene.engaged if d in (e.a, e.b) for x in (e.a, e.b)
                  if x != d and x != a and ctx.side(x) == ctx.side(a) and w.standing(x)]
    into = bool(melee_with)
    shots = max(1, min(pick.shots, actions.volley(opt)))
    av, dv = ctx.view(a), ctx.view(d)
    # The circumstances the book counts (pp. 95–96): the range band from the distance the keeper set, point
    # blank (engaged, or within DEX/5 feet), half hidden, running flat out, the target's size.
    yards = enc.scene.distance.get(f"{a}|{d}", enc.scene.distance.get(f"{d}|{a}"))
    shotgun = "/" in opt.damage_text
    band = 0
    if yards is not None and opt.range:
        found = firearms.range_band(yards, opt.range, shotgun=shotgun)
        if found is None:
            return _done(w, receipt, k, ev, f"{head}：{yards} 码，超出{opt.name}射程的四倍，打不着")
        band = found
    point_blank = ctx.engaged(a, d) or (yards is not None and firearms.point_blank(yards * 3, av.char("敏捷")))
    partial = any(c.who == d and c.partial for c in enc.scene.cover)
    running = _running(ctx, d)
    mods = firearms.shot_modifiers(firearms.Shot(band=band, shotgun=shotgun, point_blank=point_blank, dived=dived,
                                                 partial_cover=partial and not dived, fast_target=running and dv.mov() >= 8,
                                                 target_build=dv.build(), into_melee=into, volley=shots, aimed=aimed,
                                                 moving=enc.mode == "chase" and bool(receipt.declared.to),
                                                 prone_target=d in enc.scene.prone, bonus=bonus))
    if band:
        reasons = [*reasons, f"{firearms.BAND_NAMES[band]}（{yards} 码）"]
    reasons = [*reasons, *(r.lstrip("+−") for r in mods.reasons)]
    known = weapon_in(opt.name)
    text, amount_total, results = "", 0, []
    for n in range(shots):
        if opt.jammed or (loaded is not None and loaded <= 0):
            ev["empty"] = loaded is not None and loaded <= 0
            results.append("没有子弹了" if ev["empty"] else "卡壳了")
            break
        roll = w.check(a, opt.skill, opt.value, mods.difficulty, mods.dice)
        receipt.rolls.append(record("shoot", a, roll))
        if n == 0:
            ev["roll"] = check_view(roll)
        enc.budget.shots[a] = enc.budget.shots.get(a, 0) + 1
        parts.append(f"{roll.name} {roll.target} → {roll.value} {roll.level.zh}" + (f"（{'、'.join(reasons)}）" if reasons and n == 0 else ""))
        what = firearms.malfunction(roll.value, opt.malfunction, (known.mechanism if known else "") or "")  # type: ignore[arg-type]
        if what == "jam":
            w.unit.effects.append(effects.Jam(item=opt.item, jammed=True)) if opt.item else None
            ev["jammed"] = True
            results.append(f"{opt.name}卡壳了")
            break
        if loaded is not None:
            loaded -= 1
        if roll.level == Level.FUMBLE and into:  # a fumble into a melee hits a friend, the unluckiest (p. 96)
            luck = {x: (w.investigator(x).luck if w.investigator(x) else ctx.view(x).char("幸运") or 50) for x in melee_with}
            friend = firearms.fumble_hits(luck)
            amount, text = _damage(w, "0", opt, "normal", band=band)
            results.append("失手打中了自己人：" + _wound(w, friend, amount, "firearm", text, ev, firearm=True))
            continue
        if what == "dud":
            results.append("哑弹，没打响")
            continue
        if not roll.passed:
            results.append("没打中")
            continue
        w.mark(a, roll)
        if not w.body(d).dead:
            mode = "extreme" if roll.level >= mods.impale_needs else "normal"
            amount, text = _damage(w, "0", opt, mode, band=band)
            ev["extreme"] = ev["extreme"] or mode == "extreme"
            ev["winner"] = "attacker"
            amount_total += amount
            results.append(_wound(w, d, amount, "firearm", text, ev, firearm=True))
    if opt.item and loaded is not None and loaded != opt.loaded:
        w.unit.effects.append(effects.Ammo(item=opt.item, loaded=max(0, loaded)))
    if amount_total:
        ev["damage"] = amount_total
    _done(w, receipt, k, ev, f"{head}：" + "；".join(parts) + "。" + "；".join(results))


RUNNING = ("flee", "move", "pursue")


def _running(ctx: Ctx, who: str) -> bool:
    """Moving flat out this round (p. 96): declared so, or a keeper's person whose first arranged action
    it can do is running (its receipt comes only on its slot), or still running from last round."""
    enc = ctx.enc
    if any(r.actor == who and r.round in (enc.round, enc.round - 1) and r.declared.do in RUNNING for r in enc.receipts):
        return True
    p = ctx.p(who)
    if p is None or p.controller != "keeper" or p.arrangement is None:
        return False
    able = [a for a in p.arrangement.actions if not actions.preconditions(ctx, who, a)]
    return bool(able) and able[0].do in RUNNING


def _done(w: Work, receipt: ActionReceipt, k: int, ev: dict, line: str) -> None:
    """One attack of a receipt settled: its line, its card; the receipt's count moves on."""
    receipt.lines.append(line)
    w.unit.lines.append(line)
    ev["line"] = line
    mine = ev["roll"] if w.investigator(receipt.actor) is not None else ev["answer"] if w.investigator(ev["defender"]) else None
    ev["dice"] = mine
    ev["dice_who"] = ev["attacker_name"] if mine is ev["roll"] else ev["defender_name"]
    receipt.settled = k + 1
    w.unit.notices.append(ev)


# -- everything else on one's slot ---------------------------------------------------------------
def act(w: Work, r: ActionReceipt, *, ruling: Any = None, by_hand: bool = False) -> None:
    """A non-attack action settled whole."""
    ctx, enc = w.ctx, w.enc
    a = r.actor
    d = r.declared
    receipt = next(x for x in enc.receipts if x.id == r.id)
    me = enc.get(a)
    an = f"{{{a}}}"
    lines: list[str] = []
    if d.do == "defend":
        lines.append(f"{an}没有出手，被攻击时{actions.REACT_WORDS.get(me.react or 'dodge', '闪避')}")
    elif d.do == "flee":
        me.presence = "fled"
        sc = enc.scene
        sc.engaged = [e for e in sc.engaged if a not in (e.a, e.b)]
        sc.holds = [h for h in sc.holds if a not in (h.by, h.held)]
        w.change("fled", a)
        lines.append(f"【{an} 脱身逃跑】")
    elif d.do == "surrender":
        me.presence = "surrendered"
        w.change("surrendered", a)
        lines.append(f"{an}丢下手里的东西，举手投降" + (f"，向{{{d.target}}}求饶" if d.target else ""))
    elif d.do == "aid":
        lines.append(_aid(w, receipt, a, d.target))
    elif d.do == "reload":
        lines.append(_reload(w, a, d.target))
    elif d.do == "unjam":
        lines.append(_unjam(w, receipt, a, d.target))
    elif d.do == "ready":
        v = ctx.view(a)
        opt = next((x for x in v.attacks() if d.target in (x.id, x.name) and x.item), None)
        if opt is not None:
            w.unit.effects.append(effects.Ready(who=a, item=opt.item))
            lines.append(f"{an}把{opt.name}拿在手里")
        else:
            lines.append(f"{an}摸了摸身上")
    elif d.do == "help":
        t = enc.get(d.target)
        if t is not None:
            t.edges.append(Edge(dice=1, why=f"{ctx.name(a)}帮忙", source=a, until="next_roll"))
        lines.append(f"{an}帮着{{{d.target}}}" + (f"：{d.summary}" if d.summary else ""))
    elif d.do == "pursue":
        lines.append(f"{an}追向{{{d.target}}}")
    elif d.do == "hide":
        lines.append(_hide(w, receipt, a, d.summary))
    elif d.do == "aim":  # the next shot, next round: lost if it is not fired then (round_end)
        me.edges = [e for e in me.edges if not e.source.startswith("aim")]
        me.edges.append(Edge(dice=1, why="瞄准了一轮", source=f"aim:{d.target}:r{enc.round}", until="next_roll"))
        lines.append(f"{an}稳住手，瞄准了{{{d.target}}}" if d.target else f"{an}稳住手瞄准")
    elif d.do == "speak":
        said = d.speech.strip()
        lines.append(f"{an}喊：「{said}」" if said else f"{an}{d.summary}")
        if said:
            w.unit.notices.append({"type": "fight_say", "who": a, "name": ctx.name(a, "players"), "text": said})
    elif d.do in ("other", "use"):
        lines += _other(w, receipt, a, ruling)
    elif enc.mode == "fight":
        lines.append(f"{an}这一轮没能做成：{actions.VERBS.get(d.do, d.do)}只在追逐中用")
    receipt.lines += lines
    w.unit.lines += lines


def hidden(w: Work, who: str, where: str) -> None:
    """`who` is out of sight now: behind full cover, which their next attack gives away."""
    sc = w.enc.scene
    sc.cover = [c for c in sc.cover if c.who != who] + [Cover(who=who, what=where, partial=False, since=w.enc.round)]
    sc.engaged = [e for e in sc.engaged if who not in (e.a, e.b)]


def _hide(w: Work, receipt: ActionReceipt, a: str, summary: str) -> str:
    """Hiding in a fight: Stealth at the difficulty the best Spot Hidden among those watching sets
    (p. 72's opposed difficulty); a success and they lose sight of them."""
    watchers = [f for f in w.ctx.foes(a) if w.standing(f)]
    best = max((w.ctx.view(f).skill("侦查") for f in watchers), default=0)
    roll = w.check(a, "潜行", None, melee.opposed_difficulty(best))
    receipt.rolls.append(record("hide", a, roll))
    w.mark(a, roll)
    if w.investigator(a) is not None:
        w.unit.notices.append({"type": "dice", "who": w.ctx.name(a, "players"), **check_view(roll)})
    if roll.passed:
        hidden(w, a, "藏身处")
        return f"{{{a}}}躲了起来（潜行 {roll.value} {roll.level.zh}），对手看不见他了"
    return f"{{{a}}}想躲起来，没藏住（潜行 {roll.value} {roll.level.zh}）"


def _aid(w: Work, receipt: ActionReceipt, a: str, target: str) -> str:
    """First Aid (pp. 54, 100–101): +1 and awake, or on the dying only stabilized."""
    if not target or w.enc.get(target) is None:
        return f"{{{a}}}想去救人，可身边没有能救的人"
    body = w.body(target)
    ok, why = wounds.first_aid_allowed(body)
    if not ok:
        return f"{{{a}}}想给{{{target}}}急救：{why}"
    roll = w.check(a, "急救")
    receipt.rolls.append(record("first_aid", a, roll))
    w.mark(a, roll)
    if w.investigator(a) is not None:
        w.unit.notices.append({"type": "dice", "who": w.ctx.name(a, "players"), **check_view(roll)})
    care = wounds.first_aid(body, roll.passed, minutes=w.game.state.minutes)
    head = f"{{{a}}}给{{{target}}}急救（急救 {roll.value} {roll.level.zh}）"
    if not roll.passed:
        if not body.dying:  # the try is spent on that injury either way (a second is a push)
            injury = wounds._last_injury(body)
            if injury:
                w.apply(target, effects.Heal(target=target, amount=0, means="first_aid", injury=injury))
        return head + "，手忙脚乱没能止住血"
    if care.stabilized:  # 1 temporary HP, no more (p. 101): Stabilize gives it
        w.apply(target, effects.Stabilize(target=target))
        return head + "，暂时稳住了伤势"
    injury = wounds._last_injury(body)
    w.apply(target, effects.Heal(target=target, amount=1, means="first_aid", injury=injury))
    if care.woke:
        w.apply(target, effects.Wake(target=target))
        w.change("freed", target)
        return head + "，把他弄醒了"
    return head + "，止住了血"


def _reload(w: Work, a: str, target: str) -> str:
    v = w.ctx.view(a)
    gun = next((x for x in v.attacks() if x.firearm and x.capacity and (x.loaded or 0) < x.capacity
                and (not target or target in (x.id, x.name))), None)
    if gun is None:
        return f"{{{a}}}摸了摸枪，子弹是满的"
    ent = w.ctx.world.rec.entities.get(gun.item or "")
    spare = ent.weapon.spare if ent is not None and ent.weapon is not None else None
    known = weapon_in(gun.name)
    done = firearms.reload(gun.loaded or 0, gun.capacity, spare, loading=(known.loading if known else "rounds"))  # type: ignore[arg-type]
    if gun.item:
        w.unit.effects.append(effects.Ammo(item=gun.item, loaded=done.loaded, spare=done.spare))
    return f"{{{a}}}给{gun.name}装了 {done.loaded - (gun.loaded or 0)} 发子弹（现在 {done.loaded}/{gun.capacity}）"


def _unjam(w: Work, receipt: ActionReceipt, a: str, target: str) -> str:
    """Clearing a jam: a Mechanical Repair or Firearms roll this round (the book's 1D6 rounds of work
    before it are the project's simplification: one round)."""
    v = w.ctx.view(a)
    gun = next((x for x in v.attacks() if x.jammed and (not target or target in (x.id, x.name))), None)
    if gun is None:
        return f"{{{a}}}检查了一下枪，没有卡住"
    skill = max(firearms.UNJAM_SKILLS, key=lambda s: v.skill(s) if s != "射击" else gun.value)
    roll = w.check(a, gun.skill if skill == "射击" else skill, gun.value if skill == "射击" else None)
    receipt.rolls.append(record("unjam", a, roll))
    if roll.passed and gun.item:
        w.unit.effects.append(effects.Jam(item=gun.item, jammed=False))
        return f"{{{a}}}排除了{gun.name}的故障（{roll.name} {roll.value} {roll.level.zh}）"
    return f"{{{a}}}摆弄着卡住的{gun.name}，还没修好（{roll.name} {roll.value} {roll.level.zh}）"


def _other(w: Work, receipt: ActionReceipt, a: str, ruling: Any) -> list[str]:
    """Something else, as the arbiter ruled it (Ruling.effects): the check, and on success its
    damage, conditions, edges and things; the rest of the ruling (clues, flags, takes, changes) is
    settled for the turn's investigator after the unit (`Unit.rest`)."""
    d = receipt.declared
    head = d.summary if d.summary.strip() else f"{{{a}}}做了别的事"
    if ruling is None:
        return [head]
    passed = True
    if ruling.get("needs_roll") and ruling.get("skill"):
        roll = w.check(a, ruling["skill"], None, DIFFICULTY_ZH.get(ruling.get("difficulty", "常规"), Level.REGULAR),
                       melee.clamp(int(ruling.get("bonus", 0))))
        receipt.rolls.append(record("other", a, roll))
        w.mark(a, roll)
        if w.investigator(a) is not None:
            w.unit.notices.append({"type": "dice", "who": w.ctx.name(a, "players"), **check_view(roll)})
        passed = roll.passed
        head += f"（{roll.describe()}）"
        if passed and roll.name == "潜行":  # sneaking off, ruled: out of sight as a hide is
            hidden(w, a, "暗处")
    out = [head]
    w.unit.rest = {"ruling": ruling, "passed": passed}
    if not passed:
        return out
    for e in ruling.get("effects") or []:
        target, kind = e.get("target", ""), e.get("kind", "")
        if kind == "damage":
            if w.enc.get(target) is None:
                out.append(f"{{{target}}}不在这场遭遇里，伤害没有落到人身上")
                continue
            try:
                expr = DamageExpr.parse(e.get("dice", ""))
            except ValueError as exc:
                out.append(f"伤害式无法使用：{exc}")
                continue
            amount = expr.total(w.game.rules.dice, w.ctx.view(a).db())
            ev = {"notes": []}
            out.append(_wound(w, target, amount, "other", f"{expr.text()} = {amount}", ev, firearm=False))
        elif kind == "edge":
            t = w.enc.get(target)
            if t is not None and e.get("bonus"):
                t.edges.append(Edge(dice=int(e["bonus"]), why=e.get("what", "") or d.summary, source=a, until="next_roll"))
                out.append(f"{{{target}}}下一次检定{'得奖励骰' if int(e['bonus']) > 0 else '吃惩罚骰'}：{e.get('what', '')}")
        elif kind == "condition":
            if "倒" in e.get("what", "") and w.enc.get(target) is not None and target not in w.enc.scene.prone:
                w.enc.scene.prone.append(target)
            out.append(f"{{{target}}}：{e.get('what', '')}")
        elif kind == "obstacle" and w.enc.chase is not None:
            out.append(_obstacle(w, a, target, e.get("what", "")))
        elif kind == "item":
            out.append(f"{e.get('what', '')}")
        else:
            out.append(e.get("what", "") or "")
    return [x for x in out if x]


def _obstacle(w: Work, a: str, target: str, what: str) -> str:
    """An obstacle the arbiter ruled changed in a chase: opened or broken for everyone after, or the way
    blocked (a door slammed and wedged: a barrier where there was none, at the place named or where
    the actor stands)."""
    import re

    from ...module.schema import Method
    from .plan import ObstacleState
    from .site import node

    ch = w.enc.chase
    assert ch is not None
    here = node(ch.site, target) or next((n for n in ch.site.nodes if n.obstacle == target), None) \
        or node(ch.site, w.enc.get(a).node if w.enc.get(a) else "")
    ob = ch.site.obstacles.get(target) or (ch.site.obstacles.get(here.obstacle) if here is not None else None)
    if ob is not None and re.search(r"破|毁|砸|塌", what):
        ob.state, ob.by = "broken", a
        return f"{ob.what}被毁掉了，后面的人可以直接过去：{what}"
    if ob is not None and re.search(r"开|移|推倒|搬走|清", what):
        ob.state, ob.by = "open", a
        return f"{ob.what}打开了，后面的人可以直接过去：{what}"
    if re.search(r"堵|挡|锁|闩|封|关", what) and here is not None:
        if ob is not None:
            ob.state, ob.by = "blocked", a
            return f"{ob.what}被堵上了：{what}"
        oid = f"made_{here.id}_{w.enc.round}"
        ch.site.obstacles[oid] = ObstacleState(id=oid, kind="barrier", what=what[:20] or "挡住的路", hp=10, state="blocked", by=a,
                                               methods=[Method(id="force", label="推开", skill="力量", opens=True),
                                                        Method(id="climb", label="翻过去", skill="攀爬")])
        here.obstacle = oid
        return f"{here.name}的路被挡住了：{what}"
    return what


# -- the end of a round ----------------------------------------------------------------------------
def _stale_aim(e: Edge, rnd: int) -> bool:
    """An aim taken before this round and not used for a shot in it: gone (p. 95: the round after)."""
    if not e.source.startswith("aim"):
        return False
    taken = e.source.rsplit(":r", 1)[-1]
    return taken.isdigit() and int(taken) < rnd


def round_end(w: Work) -> None:
    """The dying roll CON from the end of the round after they began dying (p. 101), a creature
    that heals itself heals (and may rise), surprise and this round's edges are over."""
    enc, ctx = w.enc, w.ctx
    for p in enc.participants:
        v = ctx.view(p.id)
        if v is None:
            continue
        body = w.body(p.id)
        if wounds.dying_roll_due(body, enc.round):
            con = w.check(p.id, "体质")
            if not con.passed:
                w.apply(p.id, effects.Die(target=p.id, why=f"濒死时体质检定失败（{con.value}）"))
                w.unit.lines.append(f"{{{p.id}}}没能撑过去（体质 {con.value}）：死了")
                inv = w.investigator(p.id)
                if inv is not None:
                    w.unit.notices.append({"type": "damage", "who": inv.name, "text": "伤重不治", "fighter": p.id})
            else:
                w.unit.lines.append(f"{{{p.id}}}还吊着一口气（体质 {con.value}）")
        if body.regen and not body.dead and body.hp < body.hp_max:
            after, rises = wounds.regen_tick(body)
            w.apply(p.id, effects.Heal(target=p.id, amount=after.hp - body.hp, means="regen"))
            if rises:
                w.unit.lines.append(telling.cue(telling.rising_cue()))
                w.change("freed", p.id)
        if enc.round >= 1:
            p.surprised = False
        p.edges = [e for e in p.edges if e.until != "round_end" and not _stale_aim(e, enc.round)]


__all__ = ["Unit", "Work", "act", "diff", "round_end", "round_order", "strike"]
