"""What can be done in a fight, and what it needs: the action vocabulary of a declaration
(engine/encounter/plan.DeclaredAction) checked against the encounter as it stands.

`preconditions` gives, in Chinese and naming ids, every condition a declared action lacks right now
("douglas 没有拿着 revolver", "brennan 已经倒下了"): the keeper plan's check sends them back to the
model (plan_check), an NPC's arrangement falls through to its next preferred action when its first
lacks something, and a player's declaration that went stale at their slot is asked again. The same
words go into the brief, so a plan is shown up front what each action needs. Each is a `Lack`: those
words are the model's, and `Lack.shown` says the same to the players (`said`), with the names they
know and the things' own names, never an id: the window asking a player to choose again and the
account of a declaration that lapsed use that.

`Ctx` is the encounter as one unit reads it: the state, everyone's view (engine/encounter/combatant,
rebuilt per unit), the world. Nothing here changes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...rules.melee import maneuver_feasible
from ...rules.weapons import BRAWL, HANDGUN, weapon_in
from . import combatant
from .combatant import AttackOption
from .plan import AttackPick, DeclaredAction

if TYPE_CHECKING:
    from .model import EncounterState, Participant

ATTACKING = ("attack", "shoot", "maneuver")
CHASE_ONLY = ("move", "cross", "barrier", "stand")
VERBS = {"attack": "攻击", "shoot": "射击", "maneuver": "战技", "aid": "急救", "reload": "装填", "unjam": "排除故障",
         "ready": "拿出武器", "defend": "防守", "flee": "逃跑", "pursue": "追赶", "move": "奔跑", "cross": "越过障碍",
         "hide": "躲藏", "aim": "瞄准", "barrier": "设置障碍", "help": "协助", "speak": "喊话", "surrender": "投降", "stand": "回身迎战",
         "use": "使用", "other": "其他", "delay": "推迟"}
REACT_WORDS = {"dodge": "闪避", "fight_back": "反击", "cover": "找掩护", "none": "不应对"}
UNARMED_ID = "natural:徒手"


class Lack(str):
    """Something an action lacks: the string itself is the keeper plan's wording (ids, as the plan
    writes them), `shown` the players' (keeper form: `{id}` for a person, read out per audience by
    telling.render; things by their own names; no id of a thing, no natural: prefix)."""

    shown: str

    def __new__(cls, model: str, shown: str) -> Lack:
        out = super().__new__(cls, model)
        out.shown = shown
        return out


def said(problem: str) -> str:
    """A problem in the players' words, in keeper form: a Lack's own; any other string is the
    model's alone and is not shown."""
    return getattr(problem, "shown", "") or "这一下做不成了"


def _who(ctx: Ctx, pid: str) -> str:
    """Someone in a player's words, in keeper form: `{id}` when they are in the encounter (read out
    as the players call them), else nothing of the id the model wrote."""
    return f"{{{pid}}}" if pid and ctx.p(pid) is not None else "对方"


@dataclass
class Ctx:
    """One encounter as a unit reads it."""

    game: Any
    enc: EncounterState
    views: dict[str, Any] = field(default_factory=dict)
    _shown: dict[str, str] | None = None

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

    def perceived_foes(self, pid: str) -> list[str]:
        """Targets the investigator believes hostile; this does not change real sides."""
        from .. import perception

        out = []
        for p in self.enc.participants:
            if p.id == pid or p.presence != "in":
                continue
            appearance = perception.get(self.game.state, pid, p.id)
            hostile = appearance.side == "foe" if appearance is not None and appearance.side else self.opposed(pid, p.id)
            if hostile and (appearance is None or appearance.visible):
                out.append(p.id)
        return out

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
        """Why `watcher` cannot perceive `pid` now ("" when it can). The keeper sees all; an NPC only
        what is in front of it (guide #25), and only established facts hide anyone: gone from the
        scene, hidden behind full cover (a hide that worked, a position the keeper declared), lost in a
        chase (it hid, or ran out of sight and its trail was lost), or what the keeper declared it
        cannot see (dark, smoke, a wall between: FightScene.unseen). The picture's weather and the
        names of what someone carries decide nothing."""
        from .. import perception

        appearance = perception.get(self.game.state, watcher, pid)
        if appearance is not None and not appearance.visible:
            return "没有感知到这个人"
        p = self.p(pid)
        if p is None:
            return "不在这场遭遇里"
        if p.presence in ("escaped", "left"):
            return "已经不在现场"
        if not self.opposed(watcher, pid) and self.side(watcher) == self.side(pid):
            return ""  # one's own side is known where it is
        if any(c.who == pid and not c.partial for c in self.enc.scene.cover):
            return "藏在掩体后面，看不见"
        if self.enc.chase is not None and f"{watcher}|{pid}" in self.enc.chase.lost_sight:
            return "已经跟丢了"
        if u := next((u for u in self.enc.scene.unseen if u.watcher == watcher and u.who == pid), None):
            return f"看不见（{u.why}）" if u.why else "看不见"
        return ""

    def told(self, watcher: str, pid: str) -> list[str]:
        """What `watcher` was told or saw of `pid` (the world's facts it knows about them)."""
        from ...world import knowledge

        out = [f.text for f in self.world.rec.facts.values()
               if pid in f.about and not f.retracted and knowledge.sees(f, watcher)]
        return out[-3:]

    def name(self, pid: str, audience: str = "keeper") -> str:
        """What `audience` calls them; for the players, two who look alike are told apart by a number
        (两个「楼梯口打盹的老人」: the second is 楼梯口打盹的老人2), so every choice's words are its own."""
        from .. import perception

        if appearance := perception.get(self.game.state, audience, pid):
            return appearance.name or "未辨明的身影"
        if audience != "keeper" and self.enc.get(pid) is not None:
            return self.shown().get(pid, pid)
        v = self.view(pid)
        return v.public_name(audience) if v is not None else pid  # type: ignore[arg-type]

    def shown(self) -> dict[str, str]:
        if self._shown is None:
            out: dict[str, str] = {}
            counts: dict[str, int] = {}
            for p in self.enc.participants:
                v = self.view(p.id)
                base = v.public_name("players") if v is not None else p.id
                counts[base] = counts.get(base, 0) + 1
                out[p.id] = base if counts[base] == 1 else f"{base}{counts[base]}"
            self._shown = out
        return self._shown


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
    """The attack `with_` names ("" the obvious one for `do`: fists or the creature's own first attack
    for a blow, the best loaded gun for a shot): an attack's id, its own name, the weapon table's
    name of what it is, or words naming a weapon that just one of their attacks is (左轮手枪 for the
    柯尔特左轮手枪 they hold, rules/weapons.weapon_in)."""
    attacks = v.attacks()
    if with_:
        for key in ("id", "name"):
            if (found := next((a for a in attacks if getattr(a, key) == with_), None)) is not None:
                return found
        kind = weapon_in(with_)
        same = [a for a in attacks if kind is not None and a.weapon == kind.name]
        return same[0] if len(same) == 1 else None
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
def refers(a: AttackOption, ref: str) -> bool:
    """Whether `ref` (a declaration's target) names attack `a`: its id, its own name, or the weapon
    table's name of what it is."""
    return bool(ref) and ref in (a.id, a.name, a.weapon)


def _things(v) -> str:
    """What someone can fight with, by the things' own names (徒手、柯尔特左轮手枪)."""
    return "、".join(dict.fromkeys(a.name for a in v.attacks())) or "徒手"


def preconditions(ctx: Ctx, actor: str, d: DeclaredAction, *, takes: list[str] | None = None) -> list[Lack]:
    """What `actor` lacks to do `d` now, each a `Lack`: in words naming ids (the model's), and as the
    players would read it (`Lack.shown`); [] when it can. `takes`: names of things this action picks
    up first (DeclaredAction.takes), which it may then fight with."""
    out: list[Lack] = []
    me = f"{{{actor}}}"
    v = ctx.view(actor)
    if v is None or ctx.p(actor) is None:
        return [Lack(f"{actor} 不在这场遭遇里", "这个人不在这里")]
    if not ctx.present(actor):
        return [Lack(f"{actor} 已经不在现场", f"{me}已经不在现场")]
    if not ctx.acting(actor):
        return [Lack(f"{actor} 倒下了，不能行动", f"{me}倒下了，动不了")]
    held = ctx.held_by(actor)
    if ctx.enc.mode == "fight" and d.do in CHASE_ONLY:
        return [Lack(f"{d.do} 只在追逐中用", "这只能在追逐中做")]
    if d.after and (d.after == actor or ctx.p(d.after) is None):
        out.append(Lack(f"after 里的 {d.after} 不是这场遭遇里的另一个人", "要等的人不在这里"))
    if d.do in ATTACKING:
        if not d.attacks:
            return [Lack(f"{actor} 的 {d.do} 没写 attacks（打谁）", "没说打谁")]
        if d.do == "maneuver" and not d.maneuver:
            out.append(Lack(f"{actor} 的战技没写是哪一种（maneuver）", "没说用哪一种战技"))
        if len(d.attacks) > max(1, v.attacks_per_round()):
            out.append(Lack(f"{actor} 每轮只有 {v.attacks_per_round()} 次攻击", f"{me}一轮只能攻击 {v.attacks_per_round()} 次"))
        for pick in d.attacks:
            out += _pick(ctx, actor, v, pick, d, takes or [])
    elif d.do == "aid":
        out += _aid(ctx, actor, d.target)
    elif d.do == "reload":
        guns = [a for a in v.attacks() if a.firearm and a.capacity and (a.loaded or 0) < a.capacity
                and (not d.target or refers(a, d.target))]
        if not guns:
            out.append(Lack(f"{actor} 没有要装填的枪" + (f"（{d.target}）" if d.target else ""), "没有要装填的枪"))
        elif all(_spare(ctx, a) == 0 for a in guns):
            out.append(Lack(f"{actor} 没有备弹了", f"{me}没有备弹了"))
    elif d.do == "unjam":
        if not any(a.jammed and (not d.target or refers(a, d.target)) for a in v.attacks()):
            out.append(Lack(f"{actor} 没有卡壳的枪", "没有卡壳的枪"))
    elif d.do == "ready":
        if d.target and not any(refers(a, d.target) for a in v.attacks()):
            out.append(Lack(f"{actor} 身上没有 {d.target}", f"{me}身上没有这件武器（能用的：{_things(v)}）"))
    elif d.do == "flee":
        if held:
            out.append(Lack(f"{actor} 被 {held} 制住了，逃不掉", f"{me}被{{{held}}}制住了，逃不掉"))
    elif d.do == "pursue" and ctx.enc.chase is not None:
        ch = ctx.enc.chase
        p = ctx.p(d.target)
        if held:
            out.append(Lack(f"{actor} 被 {held} 制住了，跑不了", f"{me}被{{{held}}}制住了，跑不了"))
        if p is None or d.target not in ch.quarry or p.presence != "in":
            out.append(Lack(f"{d.target or '（没写对象）'} 不是还在跑的人（在逃的：{'、'.join(ch.quarry) or '无'}）",
                            f"{_who(ctx, d.target)}没有在跑"))
        elif why := ctx.hidden_from(actor, d.target):
            out.append(Lack(f"{actor} 追不了 {d.target}：{why}", f"{me}追不了{{{d.target}}}：{why}"))
        for pick in d.attacks:
            out += _pick(ctx, actor, v, pick, d.model_copy(update={"do": "attack"}), takes or [])
    elif d.do == "pursue":
        p = ctx.p(d.target)
        if p is None or p.presence != "fled":
            out.append(Lack(f"{d.target or '（没写对象）'} 没有在逃", f"{_who(ctx, d.target)}没有在逃"))
        elif why := ctx.hidden_from(actor, d.target):
            out.append(Lack(f"{actor} 追不了 {d.target}：{why}", f"{me}追不了{{{d.target}}}：{why}"))
    elif d.do in ("move", "cross") and ctx.enc.chase is not None:
        out += _route(ctx, actor, d, held)
    elif d.do == "stand" and ctx.enc.chase is not None:
        from .chase import at
        from .site import distance

        near = [x for x in ctx.foes(actor) if (n := distance(ctx.enc.chase.site, at(ctx.enc, actor), at(ctx.enc, x))) is not None
                and n <= 1]
        if not near:
            out.append(Lack(f"{actor} 附近没有对手，站住迎战没有对象", "附近没有对手"))
    elif d.do in ("help", "speak", "surrender"):
        if d.target and ctx.p(d.target) is None:
            out.append(Lack(f"{d.target} 不在这场遭遇里", "对方不在这里"))
        if d.do == "help" and d.target and ctx.p(d.target) is not None and not ctx.present(d.target):
            out.append(Lack(f"{d.target} 已经不在现场", f"{{{d.target}}}已经不在现场"))
    elif d.do == "use":
        if not d.target:
            out.append(Lack(f"{actor} 的 use 没写用什么（target）", "没说用什么"))
    return out


def _route(ctx: Ctx, actor: str, d: DeclaredAction, held: str) -> list[Lack]:
    """What a run in a chase lacks: somewhere on the site to run to, a way there, not held fast."""
    from . import site

    ch = ctx.enc.chase
    assert ch is not None
    out = [Lack(f"{actor} 被 {held} 制住了，跑不了", f"{{{actor}}}被{{{held}}}制住了，跑不了")] if held else []
    if d.do == "cross":
        if not any(d.target in (n.obstacle, n.id) and n.obstacle for n in ch.site.nodes):
            out.append(Lack(f"{d.target or '（没写对象）'} 不是路线上的障碍（障碍：{'、'.join(ch.site.obstacles) or '无'}）",
                            "路上没有这道障碍"))
        return out
    if d.to in ("", "away"):
        return out
    nodes = [n.id for n in ch.site.nodes if d.to in (n.id, n.place)]
    if not nodes:
        out.append(Lack(f"{d.to} 不是追逐路线上的节点（节点：{'、'.join(n.id for n in ch.site.nodes)}）", "路上没有那个去处"))
    elif not any(site.path(ch.site, ctx.p(actor).node, x) for x in nodes):  # type: ignore[union-attr]
        out.append(Lack(f"从 {actor} 那里到不了 {d.to}", "从那里过不去"))
    if d.method and not any(d.method == m.id for o in ch.site.obstacles.values() for m in o.methods):
        out.append(Lack(f"{d.method} 不是路线上哪个障碍的办法", "没有这种过法"))
    return out


def out_of_reach(ctx: Ctx, actor: str, d: DeclaredAction) -> list[str]:
    """In a chase, what keeps an attack from being made at the slot now (p. 116): a blow only at the
    same location; and an action point it costs. [] outside a chase. In keeper form, for everyone."""
    if ctx.enc.mode != "chase" or ctx.enc.chase is None or not (d.do in ATTACKING or (d.do == "pursue" and d.attacks)):
        return []
    from .chase import at, points

    out = []
    v = ctx.view(actor)
    for pick in d.attacks:
        opt = option(v, pick.with_, "attack" if d.do == "pursue" else d.do) if v is not None else None
        ranged = opt is not None and (opt.firearm or opt.skill == "投掷")
        if not ranged and at(ctx.enc, actor) != at(ctx.enc, pick.target):
            out.append(f"{{{pick.target}}}不在同一个地点，够不着")
    if points(ctx.enc, actor) < 1 and not (d.do == "shoot" and d.to):
        out.append("这一轮的行动点用完了")
    return out


def _spare(ctx: Ctx, a: AttackOption) -> int | None:
    ent = ctx.world.rec.entities.get(a.item or "")
    return ent.weapon.spare if ent is not None and ent.weapon is not None else None


def _pick(ctx: Ctx, actor: str, v, pick: AttackPick, d: DeclaredAction, takes: list[str]) -> list[Lack]:
    out: list[Lack] = []
    target = pick.target
    me, them = f"{{{actor}}}", f"{{{target}}}"
    if ctx.p(target) is None:
        return [Lack(f"{target or '（没写对象）'} 不在这场遭遇里", "要打的人不在这里")]
    if target == actor:
        return [Lack(f"{actor} 不能攻击自己", "不能攻击自己")]
    if not ctx.present(target):
        return [Lack(f"{target} 已经不在现场", f"{them}已经不在现场")]
    if not ctx.alive(target):
        return [Lack(f"{target} 已经死了", f"{them}已经死了")]
    if why := ctx.hidden_from(actor, target):
        return [Lack(f"{actor} 看不见 {target}：{why}", f"{me}看不见{them}：{why}")]
    if pick.with_ and pick.with_ in takes:
        return []  # picked up first (DeclaredAction.takes), then used
    opt = option(v, pick.with_, d.do)
    if opt is None:
        ids = "、".join(a.id for a in v.attacks()) or "徒手"
        if pick.with_:
            return [Lack(f"{actor} 没有拿着 {pick.with_}（能用的：{ids}）", f"{me}手里没有这件武器（能用的：{_things(v)}）")]
        return [Lack(f"{actor} 没有能这样用的攻击", f"{me}没有能这样用的武器")]
    if d.do == "shoot":
        if not (opt.firearm or opt.skill == "投掷"):
            out.append(Lack(f"{opt.id} 不是枪或投掷物，用 attack", f"{opt.name}不是枪，也不能投掷"))
        elif opt.jammed:
            out.append(Lack(f"{opt.id} 卡壳了", f"{opt.name}卡壳了"))
        elif opt.firearm and opt.loaded is not None and opt.loaded <= 0:
            out.append(Lack(f"{opt.id} 没有子弹了", f"{opt.name}没有子弹了"))
        if pick.shots > volley(opt):
            out.append(Lack(f"{opt.id} 一轮最多开 {volley(opt)} 枪", f"{opt.name}一轮最多开 {volley(opt)} 枪"))
    elif d.do == "attack" and opt.firearm:
        out.append(Lack(f"{opt.id} 是枪，开枪用 shoot", f"{opt.name}是枪，要用射击"))
    elif d.do == "maneuver":
        tv = ctx.view(target)
        ok, _ = maneuver_feasible(v.build(), tv.build() if tv is not None else 0)
        if not ok:
            out.append(Lack(f"{target} 体格比 {actor} 大得多，战技做不到", f"{them}的体格比{me}大得多，战技做不到"))
    if opt.damage is None and d.do != "maneuver":
        out.append(Lack(f"{opt.id} 的伤害式无法使用：{opt.problem}", f"{opt.name}的伤害没法结算"))
    return out


def _aid(ctx: Ctx, actor: str, target: str) -> list[Lack]:
    from ...rules.wounds import first_aid_allowed

    if not target or ctx.p(target) is None:
        return [Lack(f"{target or '（没写对象）'} 不在这场遭遇里", "要救的人不在这里")]
    v = ctx.view(target)
    if v is None:
        return [Lack(f"{target} 不在这场遭遇里", "要救的人不在这里")]
    ok, why = first_aid_allowed(v.body())
    return [] if ok else [Lack(f"{target}：{why}", f"{{{target}}}：{why}")]


def skill_of(opt: AttackOption, do: str) -> str:
    """The skill an attack rolls: a manoeuvre is a Fighting (Brawl) roll whatever is in hand."""
    return BRAWL if do == "maneuver" else opt.skill


__all__ = ["ATTACKING", "REACT_WORDS", "VERBS", "Ctx", "Lack", "best_melee", "option", "out_of_reach", "picks", "preconditions",
           "refers", "said"]
