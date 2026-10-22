"""What an NPC or a creature fights with, read once from its sheet: a `CombatProfile`.

The module writes people and creatures in words and numbers (module/schema.NPC.stats, .attacks,
.look, the items they hold; Creature.stats, .attacks, .notes). The engine needs them as rules:
characteristics, skills with their catalog bases, attacks with damage expressions it can roll and
the items they are, how many attacks a round, armour, guns halved, healing, a knockout, whether it
is human. `profile` reads all of that, with the numbers the keeper fixed for a sheet that lacked
them (world/record.Entity.fills) on top, and lists in `unread` what the sheet says that the engine
cannot use (an attack line it cannot read, a damage it cannot roll, a power in the notes it has
no rule for), so the keeper and the module checker see it instead of a silent default.

Identity: a creature sheet that is an NPC's fighting form (Creature.npc: the ghoul the missing
uncle has become) is that NPC's: one life, one place, under the NPC's id (`combatant`), and the same
person: the form's sheet over the NPC's, so what it leaves out (Jump, Climb, Spot Hidden for a chase,
INT) is the NPC's, not an ordinary creature's.

Read-only: nothing here changes the world. Bodies are seeded from a profile by engine/effects.seed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ...rules.catalog import base_value, canonical
from ...rules.combat import damage_bonus, stats_of, traits
from ...rules.dice_expr import problem as damage_problem
from ...rules.weapons import BRAWL, UNARMED, Weapon, attacks_per_round, carried, parse_attack, unread, weapon_in

if TYPE_CHECKING:
    from ...module.schema import NPC, Creature
    from ...world.record import Entity, Life
    from ...world.store import World

HUMAN = {"力量": 50, "体质": 50, "体型": 60, "敏捷": 50, "意志": 50, "智力": 50}  # an empty sheet: an ordinary person
BEAST = {"力量": 60, "体质": 60, "体型": 60, "敏捷": 50}  # a creature sheet that leaves them out
CHARS = ("力量", "体质", "体型", "敏捷", "意志", "智力", "外貌", "教育", "幸运")
NATURAL = ("爪", "咬", "撕", "抓", "踢", "触手", "刺", "撞", "角", "獠牙", "拳")  # a sheet's own attacks
SWARM = re.compile(r"数量多|成群|几十|一大群|无法对抗|蜂拥")
LETS_GO = re.compile(r"不追|不会追|任其离开|放他们走")  # a creature's notes: it does not go after those who run
# Words in a creature's notes that name a rule; a clause with one the engine does not read is unread.
_RULEISH = re.compile(r"免疫|抗性|法术|咒语|施法|飞行|隐身|隐形|毒|吸血|魔法|不受|无视|穿透|只能被|只有.{0,6}才能|再生|复活|变形|附身|催眠|麻痹")
_READ = re.compile(r"护甲|减半|恢复|回复|愈合|打晕|击晕|伤害加[值成]|体格|移动|MOV|成群|几十|无法对抗|不追|不会追|每(?:轮|回合)")


class AttackProfile(BaseModel):
    """One attack someone can make: a natural one (claws, fists) or a weapon, with the item it is."""

    name: str
    skill: str
    value: int
    damage: str  # as written: "1D6+DB"; `problem` says why it cannot be rolled, when it cannot
    firearm: bool = False
    impale: bool = False
    natural: bool = False  # claws and teeth, fists: never disarmed, never dropped
    thrown: bool = False
    item: str | None = None  # the item entity it is (or will be: "<holder>:<name>" for a weapon on the sheet)
    range: int = 0
    capacity: int = 0  # a full load; 0 for none
    malfunction: int = 100
    loaded: int | None = None  # rounds in it now, from the item's WeaponState; None: not counted yet (a full load)
    jammed: bool = False
    problem: str = ""


class CombatProfile(BaseModel):
    id: str  # the combatant id: the NPC's for a creature sheet that is an NPC's form
    kind: Literal["npc", "creature"]
    name: str
    called: str = ""  # what the players call it before they know it (NPC.called, Creature.called)
    sheet: str = ""  # the module object the numbers come from: npc:<id> or creature:<id>
    chars: dict[str, int] = Field(default_factory=dict)
    hp_max: int
    mov: int = 8
    db: str = "0"
    build: int = 0
    dodge: int = 25
    fighting: int = 25
    skills: dict[str, int] = Field(default_factory=dict)
    attacks: list[AttackProfile] = Field(default_factory=list)
    per_round: int = 1
    armor: int = 0
    guns_halved: bool = False
    regen: int = 0
    knockout: int = 0
    human: bool = True
    swarm: bool = False
    sanity: str = ""
    look: str = ""
    notes: str = ""
    preset: str = ""  # the module's own word for its state at the start (NPC.status: 受伤, 重伤)
    source: Literal["module", "fill", "ordinary"] = "module"
    unread: list[str] = Field(default_factory=list)

    def skill(self, key: str) -> int:
        """A skill or characteristic: the sheet's (or the keeper's fill), else the catalog base."""
        k = canonical(key)
        if k in self.chars:
            return self.chars[k]
        if k == "闪避":
            return self.dodge
        if k == BRAWL:
            return self.fighting
        if k in self.skills:
            return self.skills[k]
        return base_value(k, {**self.chars, "教育": self.chars.get("教育", 50)})


# -- who ------------------------------------------------------------------------------------------
def _npc(w: World, ident: str) -> NPC | None:
    return next((n for n in w.m.npcs if n.id == ident), None)


def _creature(w: World, ident: str) -> Creature | None:
    return next((c for c in w.m.creatures if c.id == ident), None)


def combatant(w: World, ident: str) -> str:
    """The id someone fights under: a creature sheet that is an NPC's form is that NPC."""
    c = _creature(w, ident)
    return c.npc if c is not None and getattr(c, "npc", "") and _npc(w, c.npc) is not None else ident


def form_of(w: World, npc: str) -> Creature | None:
    """The creature sheet that is this NPC's fighting form, if the module gives one."""
    return next((c for c in w.m.creatures if getattr(c, "npc", "") == npc), None)


# -- reading a sheet ----------------------------------------------------------------------------
def profile(w: World, ident: str) -> CombatProfile | None:
    """The profile of an NPC or a creature (by either id of a creature form); None for anyone else."""
    who = combatant(w, ident)
    npc = _npc(w, who)
    creature = _creature(w, ident) if npc is None else (_creature(w, ident) or form_of(w, who))
    if npc is None and creature is None:
        return None
    ent = w.rec.entities.get(who)
    fills = dict(ent.fills) if ent is not None else {}
    if creature is not None:
        p = _from_creature(creature, fills, under=stats_of(npc.stats) if npc is not None else None)
        if npc is not None:  # its form: the NPC's id, life and place; its name for the keeper
            p.id, p.kind, p.called = npc.id, "npc", creature.called or npc.called
            p.preset = npc.status
    else:
        assert npc is not None
        held = {e.id: e.name for e in w.rec.entities.values() if e.kind == "item" and e.at == npc.id}
        p = _from_npc(npc, fills, held)
    _worldly(w, p)
    return p


def of_creature(c: Creature) -> CombatProfile:
    """A creature's sheet as the rules read it, without a game (the module compiler and checker)."""
    return _from_creature(c, {})


def of_npc(npc: NPC, things: list[str] | None = None) -> CombatProfile:
    """An NPC's sheet as the rules read it, holding `things` (the module's items it owns), without a game."""
    return _from_npc(npc, {}, {f"{npc.id}:{t}": t for t in things or []})


def _apply_fills(s: dict[str, int], fills: dict[str, int]) -> dict[str, int]:
    return {**s, **stats_of(fills)}


def _natural(stats: dict[str, int]) -> list[AttackProfile]:
    """Attacks a sheet names by themselves ("爪击": 50): the claws and teeth of a creature-like NPC."""
    return [AttackProfile(name=k, skill=BRAWL, value=v, damage="1D6+DB", impale="咬" in k or "刺" in k, natural=True)
            for k, v in stats.items() if any(x in k for x in NATURAL) and isinstance(v, int)]


def _written(holder: str, line: str, *, creature: bool) -> AttackProfile | None:
    """One line of a sheet's attacks as an attack; its weapon (a gun, a knife) an item on the sheet."""
    wr = parse_attack(line)
    if wr is None:
        return None
    if not wr.firearm and (wr.name in ("斗殴", "格斗") or weapon_in(wr.name) is UNARMED):  # "格斗(斗殴) 45%": the bare hands
        return AttackProfile(name=UNARMED.name, skill=BRAWL, value=wr.value, damage=wr.damage, natural=True)
    known = weapon_in(wr.name) or (weapon_in(line) if wr.firearm else None)
    natural = not wr.firearm and (known is None or known is UNARMED) and (creature or any(x in wr.name for x in NATURAL))
    return AttackProfile(name=wr.name, skill=wr.skill, value=wr.value, damage=wr.damage, firearm=wr.firearm, impale=wr.impale,
                         natural=natural, thrown=wr.skill == "投掷", item=None if natural else f"{holder}:{wr.name}",
                         range=wr.range, capacity=wr.ammo if wr.firearm else 0,
                         malfunction=known.malfunction if known and wr.firearm else 100)


def _weapon(w: Weapon, value: int, item: str | None) -> AttackProfile:
    return AttackProfile(name=w.name, skill=w.skill, value=value, damage=w.damage, firearm=w.firearm, impale=w.impale,
                         natural=w is UNARMED, thrown=w.skill == "投掷", item=None if w is UNARMED else item, range=w.range,
                         capacity=w.ammo if w.firearm else 0, malfunction=w.malfunction)


def _from_npc(npc: NPC, fills: dict[str, int], held: dict[str, str]) -> CombatProfile:
    """A module NPC as he fights: an empty sheet is an ordinary person's numbers; what he carries is
    what the record has him hold, and what his look says he carries."""
    s = _apply_fills({**HUMAN, **stats_of(npc.stats)}, fills)
    db, build = damage_bonus(s["力量"], s["体型"])
    fighting = s.get(BRAWL, 25)
    attacks = _natural(s)
    unread_lines: list[str] = []
    for line in getattr(npc, "attacks", []) or []:
        if why := unread(line):
            unread_lines.append(f"攻击“{line}”：{why}")
        if (a := _written(npc.id, line, creature=False)) is not None:
            attacks.append(a)
    for weapon in carried([*held.values(), npc.look]):
        if any(a.name == weapon.name for a in attacks):
            continue
        item = next((i for i, name in held.items() if weapon_in(name) is weapon), f"{npc.id}:{weapon.name}")
        value = s.get(weapon.skill) or (fighting if weapon.melee else s.get(canonical(weapon.skill)) or base_value(weapon.skill, s))
        attacks.append(_weapon(weapon, value, item))
    return CombatProfile(id=npc.id, kind="npc", name=npc.name, called=getattr(npc, "called", ""), sheet=f"npc:{npc.id}",
                         chars={k: s[k] for k in CHARS if k in s}, hp_max=s.get("生命") or max(1, (s["体质"] + s["体型"]) // 10),
                         mov=s.get("移动", 8), db=db, build=build, dodge=s.get("闪避", s["敏捷"] // 2),
                         fighting=max([fighting, *(a.value for a in attacks if not a.firearm)]),
                         skills={k: v for k, v in s.items() if k not in CHARS and k not in ("生命", "移动", "闪避", "魔法")},
                         attacks=attacks, look=npc.look, notes=npc.goal, preset=npc.status,
                         source="fill" if "生命" in fills else "module" if npc.stats else "ordinary",
                         unread=unread_lines)


def _from_creature(c: Creature, fills: dict[str, int], under: dict[str, int] | None = None) -> CombatProfile:
    """A creature as it fights: its sheet's numbers (over `under`, the NPC's when it is that NPC's
    form), its attacks as written, what its notes say the rules can use (rules/combat.traits). A swarm
    (几十只, 无法对抗) fights as one, hard to put down."""
    s = _apply_fills({**(under or {}), **stats_of(c.stats)}, fills)
    t = traits(f"{c.notes} {' '.join(c.attacks)}")
    chars = {**BEAST, **{k: s[k] for k in CHARS if k in s}}
    db, build = damage_bonus(chars["力量"], chars["体型"])
    unread_lines = [f"攻击“{line}”：{why}" for line in c.attacks if (why := unread(line))]
    attacks = [a for line in c.attacks if (a := _written(c.id, line, creature=True)) is not None] or _natural(s)
    swarm = bool(SWARM.search(c.notes + c.description)) and not c.stats
    if not attacks:
        attacks = [AttackProfile(name="撕扯" if swarm else "攻击", skill=BRAWL, value=50 if swarm else 45, damage="1D6+DB",
                                 natural=True)]
        unread_lines.append("没写攻击：按" + ("成群撕扯 50%" if swarm else "攻击 45%") + "、伤害 1D6+DB 算")
    hp = 60 if swarm and "生命" not in fills else s.get("生命") or max(1, (chars["体质"] + chars["体型"]) // 10)
    for clause in re.split(r"[。；;\n]", c.notes):
        if _RULEISH.search(clause) and not _READ.search(clause):
            unread_lines.append(f"特性“{clause.strip()}”：引擎没有对应的规则，由守秘人裁定")
    melee = [a.value for a in attacks if not a.firearm]
    return CombatProfile(id=c.id, kind="creature", name=c.name, called=getattr(c, "called", ""), sheet=f"creature:{c.id}",
                         chars=chars, hp_max=hp, mov=t.get("move") or s.get("移动", 8), db=t.get("db", db),
                         build=t.get("build", build), dodge=s.get("闪避", chars["敏捷"] // 2), fighting=max(melee) if melee else 40,
                         skills={k: v for k, v in s.items() if k not in CHARS and k not in ("生命", "移动", "闪避", "魔法")},
                         attacks=attacks, per_round=4 if swarm else attacks_per_round(c.attacks, c.notes),
                         armor=t.get("armor", 0), guns_halved=t.get("guns_halved", False), regen=t.get("regen", 0),
                         knockout=t.get("knockout", 0), human=False, swarm=swarm, sanity=c.sanity, look=c.description,
                         notes=c.notes, source="fill" if "生命" in fills else "module", unread=unread_lines)


def _worldly(w: World, p: CombatProfile) -> None:
    """The attacks as the world has them now: a weapon that left its holder is no longer theirs
    (disarmed, handed over), a gun's rounds and jam are its item's. Damage that cannot be rolled is
    said in `problem` and in `unread`."""
    kept = []
    for a in p.attacks:
        ent = w.rec.entities.get(a.item) if a.item else None
        if ent is not None and ent.at != p.id:
            continue
        if ent is not None and ent.weapon is not None:
            a.loaded, a.jammed = ent.weapon.loaded, ent.weapon.jammed
        if why := damage_problem(a.damage.replace("+灼烧", "").replace("灼烧", "")):
            a.problem = why
            p.unread.append(f"攻击 {a.name} 的伤害“{a.damage}”：{why}")
        kept.append(a)
    p.attacks = kept


def life(w: World, p: CombatProfile) -> Life:
    """The body a profile starts a game with (engine/effects.seed): full HP, or as the module has
    it (a preset 受伤 or 重伤 is half, a preset 死亡 dead)."""
    from ...world.record import Life

    ent: Entity | None = w.rec.entities.get(p.id)
    status = ent.status if ent is not None and ent.status else p.preset
    hp, conditions = p.hp_max, []
    if status == "死亡":
        hp, conditions = 0, ["死亡"]
    elif status in ("受伤", "重伤"):
        hp = max(1, p.hp_max // 2)
        conditions = ["重伤"] if status == "重伤" else []
    return Life(hp=hp, hp_max=p.hp_max, conditions=conditions, source=p.source)
