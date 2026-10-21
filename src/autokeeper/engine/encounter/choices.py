"""What a player is offered in a fight, and when the game stops to ask them.

The screens offer the round's choices as words (`options`): one per attack on each foe, each
manoeuvre, first aid for a hurt ally, reloading, clearing a jam, defending, running. The words carry
the answer to blows the player picked for the round ("｜被攻击时闪避"), so Tab only changes which list
is shown. What comes back is matched against those words exactly (`match`): the option's own
declaration, with no model call and nothing parsed out of the words. Anything else the player types
is the keeper plan's to read.

`should_ask` is the reaction policy (design §3.3): a human defender is asked, before any die of the
attack is thrown, the first time they are attacked in an encounter, when the blow could put them
down, when they asked to be asked every time, or when the answer they set does not fit this attack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...rules.dice_expr import DamageExpr
from ...rules.melee import reactions_against
from . import actions
from .actions import UNARMED_ID, Ctx
from .model import ChoiceOption
from .plan import AttackPick, DeclaredAction

if TYPE_CHECKING:
    from .model import EncounterState, Participant

MATERIAL = ("down", "joined", "fled", "surrendered", "disarmed", "freed", "switched")
STANCE_WORDS = {"dodge": "闪避", "fight_back": "反击", "cover": "找掩护"}
REACTION_LABELS = {"dodge": "闪避", "fight_back": "反击", "cover": "扑向掩体", "none": "不应对"}


def damage_words(damage: str, db: str) -> str:
    """A weapon's damage as a player reads it: the bonus written in (1D3+1D4), none when it has none."""
    bonus = (db or "").strip()
    plain = damage.replace("+半DB", "").replace("+DB", "").replace("半DB", "").replace("DB", "").replace("+灼烧", "+火")
    if "DB" not in damage or bonus in ("", "0", "+0"):
        return plain
    return plain + (bonus if bonus.startswith(("+", "-")) else "+" + bonus) + ("（减半）" if "半DB" in damage else "")


def presets(ctx: Ctx, who: str) -> list[str]:
    """The answers to blows the round's list can carry: dodge, fight back, and diving for cover when a
    foe has a gun."""
    armed = any(a.firearm for f in ctx.foes(who) if (v := ctx.view(f)) is not None for a in v.attacks())
    return ["dodge", "fight_back", *(["cover"] if armed else [])]


def _label(verb: str, target: str = "", weapon: str = "", react: str = "") -> str:
    out = f"{verb} {target}".strip()
    if weapon:
        out += f"（{weapon}）"
    if react:
        out += f"｜被攻击时{STANCE_WORDS[react]}"
    return out


def options(game: Any, who: str, react: str | None = None, ctx: Ctx | None = None) -> list[ChoiceOption]:
    """The round's choices for investigator `who`, carrying the answer `react` (their preset, else
    dodge). [] when they cannot act."""
    ctx = ctx or Ctx.of(game)
    me = ctx.p(who)
    v = ctx.view(who)
    if me is None or v is None or not ctx.acting(who):
        return []
    react = react or me.react or "dodge"
    if react not in presets(ctx, who):
        react = "dodge"
    name = v.public_name("players")
    out: list[ChoiceOption] = []

    def add(d: DeclaredAction, label: str, note: str) -> None:
        d = d.model_copy(update={"react": react, "summary": f"{name}：{label}"})
        out.append(ChoiceOption(id=f"o{len(out)}", label=label, note=note, value=d.model_dump(mode="json", by_alias=True)))

    foes = [f for f in ctx.foes(who) if ctx.alive(f) and not ctx.hidden_from(who, f)]
    for f in foes:
        them = ctx.name(f, "players")
        for a in v.attacks():
            if a.damage is None:
                continue
            if a.firearm or a.skill == "投掷":
                if a.jammed or (a.firearm and a.loaded is not None and a.loaded <= 0):
                    continue
                left = f" · 余弹 {a.loaded}" if a.firearm and a.loaded is not None else ""
                add(DeclaredAction(do="shoot", attacks=[AttackPick(target=f, with_=a.id)], summary="-"),
                    _label("射击", them, a.name, react), f"{a.skill} {a.value}{left}")
            else:
                add(DeclaredAction(do="attack", attacks=[AttackPick(target=f, with_=a.id)], summary="-"),
                    _label("攻击", them, a.name, react), f"{a.skill} {a.value} · 伤害 {damage_words(a.damage_text, v.db())}")
        tv = ctx.view(f)
        for m in ("推倒", "缴械", "制住"):
            add(DeclaredAction(do="maneuver", maneuver=m, attacks=[AttackPick(target=f, with_=UNARMED_ID)], summary="-"),
                _label(m, them, react=react), f"战技 · 体格 {v.build()} 对 {tv.build() if tv is not None else '?'}")
    for f in ctx.allies(who):
        if not actions._aid(ctx, who, f):
            add(DeclaredAction(do="aid", target=f, summary="-"), _label("急救", ctx.name(f, "players"), react=react),
                f"急救 {v.skill('急救')}")
    for a in v.attacks():
        if a.firearm and a.capacity and a.loaded is not None and a.loaded < a.capacity:
            if not actions.preconditions(ctx, who, DeclaredAction(do="reload", target=a.id, summary="-")):
                add(DeclaredAction(do="reload", target=a.id, summary="-"), _label("装填", weapon=a.name, react=react),
                    f"装满 {a.capacity} 发")
                break
    for a in v.attacks():
        if a.jammed:
            add(DeclaredAction(do="unjam", target=a.id, summary="-"), _label("排除故障", weapon=a.name, react=react), "这一轮不能射击")
            break
    add(DeclaredAction(do="defend", summary="-"), _label("防守", react=react), "不出手，只防守")
    if not ctx.held_by(who):
        add(DeclaredAction(do="flee", to="away", summary="-"), _label("逃跑", react=react), "离开战斗")
    return out


def offered(game: Any, who: str) -> list[ChoiceOption]:
    """Every list the screens may show this round (one per answer to blows), for matching."""
    ctx = Ctx.of(game)
    return [o for r in presets(ctx, who) for o in options(game, who, r, ctx)]


def match(game: Any, who: str, text: str) -> DeclaredAction | None:
    """The option whose words are exactly `text`, as its declaration; None for anything else."""
    words = text.strip()
    for o in offered(game, who):
        if o.label == words:
            return DeclaredAction.model_validate(o.value)
    return None


# -- arranging and asking -------------------------------------------------------------------------
def needs_arrangement(enc: EncounterState, p: Participant) -> bool:
    """A keeper's person needs the keeper plan this round: no arrangement, one not to be kept, or
    something material happened since it was made."""
    if p.controller != "keeper" or p.presence != "in":
        return False
    if p.arrangement is None or not p.arrangement.keep or p.arranged_round is None:
        return True
    # `arranged_round` is the round it was made for, before that round began: anything material in that
    # round or later came after it.
    return any(c.round >= p.arranged_round for c in enc.changes if c.kind in MATERIAL)


def attack_kind(opt) -> str:
    if opt.firearm:
        return "firearm"
    if opt.skill == "投掷":
        return "thrown"
    if opt.skill.startswith("射击"):
        return "missile"
    return "melee"


def reaction_options(ctx: Ctx, defender: str, kind: str, *, maneuver: bool = False) -> list[str]:
    """What `defender` may answer this attack with (melee: dodge or fight back; a shot: dive for
    cover; a throw: dodge), none when they cannot answer (surprised, held, out cold)."""
    p, v = ctx.p(defender), ctx.view(defender)
    if p is None or v is None or p.surprised or not ctx.acting(defender) or ctx.held_by(defender):
        return ["none"]
    out = [r for r in reactions_against(kind) if r != "maneuver"]  # type: ignore[arg-type]
    if v.kind == "creature" and "fight_back" in out and actions.best_melee(v) is None:
        out.remove("fight_back")
    return out


def max_damage(opt, db: str, impale: bool) -> int:
    try:
        expr = DamageExpr.parse(opt.damage_text.replace("+灼烧", "").replace("灼烧", ""))
    except ValueError:
        return 0
    top = expr.maximum(db)
    return top + (expr.weapon().maximum("0") if impale else 0)


def should_ask(ctx: Ctx, defender: str, attacker: str, opt, kind: str, choices: list[str]) -> bool:
    """Whether the game stops to ask the player how `defender` answers this attack (design §3.3)."""
    p = ctx.p(defender)
    if p is None or p.controller != "human" or choices == ["none"] or not ctx.present(defender):
        return False
    if p.ask == "never":
        return False
    if p.ask == "always" or not p.asked:
        return True
    if p.react is None or p.react not in choices:
        return True
    av, dv = ctx.view(attacker), ctx.view(defender)
    body = dv.body()
    top = max_damage(opt, av.db() if kind == "melee" else "0", opt.impale)
    return top >= body.hp or top * 2 >= body.hp_max


__all__ = ["match", "needs_arrangement", "offered", "options", "presets", "reaction_options", "should_ask"]
