"""What can be done in a fight, and what it needs: the action vocabulary of a declaration
(engine/encounter/plan.DeclaredAction) checked against the encounter as it stands.

`preconditions` gives, in Chinese and naming ids, every condition a declared action lacks right now
("douglas 没有拿着 revolver", "brennan 已经倒下了"): the keeper plan's check sends them back to the
model (plan_check), an NPC's arrangement falls through to its next preferred action when its first
lacks something, and a player's declaration that went stale at their slot is asked again. The same
words go into the brief, so a plan is shown up front what each action needs.

`Ctx` is the encounter as one unit reads it: the state, everyone's view (engine/encounter/combatant,
rebuilt per unit), the world. Nothing here changes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...rules.melee import maneuver_feasible
from ...rules.weapons import BRAWL, HANDGUN
from . import combatant
from .combatant import AttackOption
from .plan import AttackPick, DeclaredAction

if TYPE_CHECKING:
    from .model import EncounterState, Participant

ATTACKING = ("attack", "shoot", "maneuver")
CHASE_ONLY = ("move", "cross", "hide", "barrier", "stand")
VERBS = {"attack": "攻击", "shoot": "射击", "maneuver": "战技", "aid": "急救", "reload": "装填", "unjam": "排除故障",
         "ready": "拿出武器", "defend": "防守", "flee": "逃跑", "pursue": "追赶", "move": "奔跑", "cross": "越过障碍",
         "hide": "躲藏", "barrier": "设置障碍", "help": "协助", "speak": "喊话", "surrender": "投降", "stand": "回身迎战",
         "use": "使用", "other": "其他"}
REACT_WORDS = {"dodge": "闪避", "fight_back": "反击", "cover": "找掩护", "none": "不应对"}
UNARMED_ID = "natural:徒手"


@dataclass
class Ctx:
    """One encounter as a unit reads it."""

    game: Any
    enc: EncounterState
    views: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, game: Any, enc: EncounterState | None = None) -> Ctx:
        enc = enc if enc is not None else game.state.encounter
        assert enc is not None
        ctx = cls(game, enc)
        for p in enc.participants:
            v = combatant.view(game.world, p.id)
            if v is not None:
                ctx.views[p.id] = v
        return ctx

    @property
    def world(self):
        return self.game.world

    def view(self, pid: str):
        if pid not in self.views and (v := combatant.view(self.world, pid)) is not None:
            self.views[pid] = v
        return self.views.get(pid)

    def p(self, pid: str) -> Participant | None:
        return self.enc.get(pid)

    def present(self, pid: str) -> bool:
        p = self.p(pid)
        return p is not None and p.presence == "in"

    def alive(self, pid: str) -> bool:
        v = self.view(pid)
        return v is not None and not v.body().dead

    def acting(self, pid: str) -> bool:
        """Present and able to act (conscious, not dead, not coming back from 0 HP)."""
        v = self.view(pid)
        return self.present(pid) and v is not None and v.can_act() and v.conscious()

    def side(self, pid: str) -> str:
        p = self.p(pid)
        return p.side if p is not None else ""

    def opposed(self, a: str, b: str) -> bool:
        sa, sb = self.side(a), self.side(b)
        return bool(sa and sb) and sa != sb and "neutral" not in (sa, sb)

    def foes(self, pid: str) -> list[str]:
        return [p.id for p in self.enc.participants if p.presence == "in" and self.opposed(pid, p.id)]

    def allies(self, pid: str) -> list[str]:
        return [p.id for p in self.enc.participants if p.id != pid and p.side == self.side(pid) and p.presence == "in"]

    def engaged(self, a: str, b: str) -> bool:
        return any({e.a, e.b} == {a, b} for e in self.enc.scene.engaged)

    def in_melee(self, pid: str) -> bool:
        return any(pid in (e.a, e.b) for e in self.enc.scene.engaged)

    def held_by(self, pid: str) -> str:
        return next((h.by for h in self.enc.scene.holds if h.held == pid and self.acting(h.by)), "")

    def prone(self, pid: str) -> bool:
        return pid in self.enc.scene.prone

    def hidden_from(self, watcher: str, pid: str) -> str:
        """Why `watcher` cannot see `pid` now ("" when it can): full cover, lost sight in a chase,
        gone from the scene."""
        p = self.p(pid)
        if p is None:
            return "不在这场遭遇里"
        if p.presence in ("escaped", "left"):
            return "已经不在现场"
        if any(c.who == pid and not c.partial for c in self.enc.scene.cover):
            return "藏在掩体后面，看不见"
        if self.enc.chase is not None and f"{watcher}|{pid}" in self.enc.chase.lost_sight:
            return "已经跟丢了"
        return ""

    def name(self, pid: str, audience: str = "keeper") -> str:
        v = self.view(pid)
        return v.public_name(audience) if v is not None else pid  # type: ignore[arg-type]


# -- attacks ---------------------------------------------------------------------------------------
def fists(v) -> AttackOption | None:
    return next((a for a in v.attacks() if a.id == UNARMED_ID), None)


def melee_options(v) -> list[AttackOption]:
    return [a for a in v.attacks() if not a.firearm and a.skill != "投掷"]


def ranged_options(v) -> list[AttackOption]:
    return [a for a in v.attacks() if a.firearm or a.skill == "投掷"]


def best_melee(v) -> AttackOption | None:
    """The melee attack it answers a blow with, fighting back: its best chance."""
    melee = [a for a in melee_options(v) if a.damage is not None]
    return max(melee, key=lambda a: (a.value, a.id != UNARMED_ID)) if melee else None


def option(v, with_: str, do: str) -> AttackOption | None:
    """The attack `with_` names (an attack id or its name; "" the obvious one for `do`): fists or
    the creature's own first attack for a blow, the best loaded gun for a shot."""
    attacks = v.attacks()
    if with_:
        return next((a for a in attacks if a.id == with_), None) or next((a for a in attacks if a.name == with_), None)
    if do == "shoot":
        guns = [a for a in ranged_options(v) if a.usable]
        return max(guns, key=lambda a: a.value) if guns else None
    if do == "maneuver":
        return fists(v) or next((a for a in melee_options(v) if a.natural), None) or best_melee(v)
    melee = melee_options(v)
    return fists(v) or (melee[0] if melee else None)


def volley(opt: AttackOption) -> int:
    """Shots a weapon may fire in a round: up to 3 with a handgun (each at a penalty die), else 1."""
    return 3 if opt.firearm and opt.skill == HANDGUN else 1


def picks(d: DeclaredAction, per_round: int) -> list[AttackPick]:
    """The attacks a declaration makes: as written, and a multi-attack creature's remaining attacks at
    the last one's target (it makes them all on its slot, Keeper Rulebook p. 86)."""
    out = list(d.attacks)
    if out and d.do == "attack" and len(out) < per_round:
        out += [out[-1].model_copy()] * (per_round - len(out))
    return out


# -- what an action needs -------------------------------------------------------------------------
def preconditions(ctx: Ctx, actor: str, d: DeclaredAction, *, takes: list[str] | None = None) -> list[str]:
    """What `actor` lacks to do `d` now, in words naming ids; [] when it can. `takes`: names of
    things this action picks up first (DeclaredAction.takes), which it may then fight with."""
    out: list[str] = []
    v = ctx.view(actor)
    if v is None or ctx.p(actor) is None:
        return [f"{actor} 不在这场遭遇里"]
    if not ctx.present(actor):
        return [f"{actor} 已经不在现场"]
    if not ctx.acting(actor):
        return [f"{actor} 倒下了，不能行动"]
    held = ctx.held_by(actor)
    if ctx.enc.mode == "fight" and d.do in CHASE_ONLY:
        return [f"{d.do} 只在追逐中用"]
    if d.do in ATTACKING:
        if not d.attacks:
            return [f"{actor} 的 {d.do} 没写 attacks（打谁）"]
        if d.do == "maneuver" and not d.maneuver:
            out.append(f"{actor} 的战技没写是哪一种（maneuver）")
        if len(d.attacks) > max(1, v.attacks_per_round()):
            out.append(f"{actor} 每轮只有 {v.attacks_per_round()} 次攻击")
        for pick in d.attacks:
            out += _pick(ctx, actor, v, pick, d, takes or [])
    elif d.do == "aid":
        out += _aid(ctx, actor, d.target)
    elif d.do == "reload":
        guns = [a for a in v.attacks() if a.firearm and a.capacity and (a.loaded or 0) < a.capacity
                and (not d.target or d.target in (a.id, a.name))]
        if not guns:
            out.append(f"{actor} 没有要装填的枪" + (f"（{d.target}）" if d.target else ""))
        elif all(_spare(ctx, a) == 0 for a in guns):
            out.append(f"{actor} 没有备弹了")
    elif d.do == "unjam":
        if not any(a.jammed and (not d.target or d.target in (a.id, a.name)) for a in v.attacks()):
            out.append(f"{actor} 没有卡壳的枪")
    elif d.do == "ready":
        if d.target and not any(d.target in (a.id, a.name) for a in v.attacks()):
            out.append(f"{actor} 身上没有 {d.target}")
    elif d.do == "flee":
        if held:
            out.append(f"{actor} 被 {held} 制住了，逃不掉")
    elif d.do == "pursue":
        p = ctx.p(d.target)
        if p is None or p.presence != "fled":
            out.append(f"{d.target or '（没写对象）'} 没有在逃")
    elif d.do in ("help", "speak", "surrender"):
        if d.target and ctx.p(d.target) is None:
            out.append(f"{d.target} 不在这场遭遇里")
        if d.do == "help" and d.target and not ctx.present(d.target):
            out.append(f"{d.target} 已经不在现场")
    elif d.do == "use":
        if not d.target:
            out.append(f"{actor} 的 use 没写用什么（target）")
    return out


def _spare(ctx: Ctx, a: AttackOption) -> int | None:
    ent = ctx.world.rec.entities.get(a.item or "")
    return ent.weapon.spare if ent is not None and ent.weapon is not None else None


def _pick(ctx: Ctx, actor: str, v, pick: AttackPick, d: DeclaredAction, takes: list[str]) -> list[str]:
    out = []
    target = pick.target
    if ctx.p(target) is None:
        return [f"{target or '（没写对象）'} 不在这场遭遇里"]
    if target == actor:
        return [f"{actor} 不能攻击自己"]
    if not ctx.present(target):
        return [f"{target} 已经不在现场"]
    if not ctx.alive(target):
        return [f"{target} 已经死了"]
    if why := ctx.hidden_from(actor, target):
        return [f"{actor} 看不见 {target}：{why}"]
    if pick.with_ and pick.with_ in takes:
        return []  # picked up first (DeclaredAction.takes), then used
    opt = option(v, pick.with_, d.do)
    if opt is None:
        held = "、".join(a.id for a in v.attacks()) or "徒手"
        return [f"{actor} 没有拿着 {pick.with_}（能用的：{held}）" if pick.with_ else f"{actor} 没有能这样用的攻击"]
    if d.do == "shoot":
        if not (opt.firearm or opt.skill == "投掷"):
            out.append(f"{opt.id} 不是枪或投掷物，用 attack")
        elif opt.jammed:
            out.append(f"{opt.id} 卡壳了")
        elif opt.firearm and opt.loaded is not None and opt.loaded <= 0:
            out.append(f"{opt.id} 没有子弹了")
        if pick.shots > volley(opt):
            out.append(f"{opt.id} 一轮最多开 {volley(opt)} 枪")
    elif d.do == "attack" and opt.firearm:
        out.append(f"{opt.id} 是枪，开枪用 shoot")
    elif d.do == "maneuver":
        tv = ctx.view(target)
        ok, _ = maneuver_feasible(v.build(), tv.build() if tv is not None else 0)
        if not ok:
            out.append(f"{target} 体格比 {actor} 大得多，战技做不到")
    if opt.damage is None and d.do != "maneuver":
        out.append(f"{opt.id} 的伤害式无法使用：{opt.problem}")
    return out


def _aid(ctx: Ctx, actor: str, target: str) -> list[str]:
    from ...rules.wounds import first_aid_allowed

    if not target or ctx.p(target) is None:
        return [f"{target or '（没写对象）'} 不在这场遭遇里"]
    v = ctx.view(target)
    if v is None:
        return [f"{target} 不在这场遭遇里"]
    ok, why = first_aid_allowed(v.body())
    return [] if ok else [f"{target}：{why}"]


def skill_of(opt: AttackOption, do: str) -> str:
    """The skill an attack rolls: a manoeuvre is a Fighting (Brawl) roll whatever is in hand."""
    return BRAWL if do == "maneuver" else opt.skill


__all__ = ["ATTACKING", "REACT_WORDS", "VERBS", "Ctx", "best_melee", "option", "picks", "preconditions"]
