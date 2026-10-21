"""A fight and a chase in the game: who is in it, what each of them does this round, what the
screens show and what the keeper is told. The numbers are rules/combat.py's and rules/chase.py's;
the rounds are run by Game (engine/fighting.py).

Everything here is plain: building a fighter from an investigator's sheet, a module NPC or a
creature; reading an order out of what a player typed or picked (the screen offers them as text,
so a LAN table sends them like any action); the rules of thumb the NPCs follow when the tactics
agent cannot be asked; the views and the report."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..module.schema import NPC, Creature
from ..rules import chase as chase_rules
from ..rules.catalog import canonical
from ..rules.chase import Leg, Obstacle, Track
from ..rules.coc import Investigator
from ..rules.combat import (
    DOWN,
    OUT,
    STANCES,
    Attack,
    Fighter,
    Side,
    Stance,
    attack_of,
    attack_written,
    damage_bonus,
    stats_of,
    traits,
)
from ..rules.weapons import BRAWL, UNARMED, attacks_per_round, carried, parse_attack, weapon_in

if TYPE_CHECKING:
    from ..world.store import World

Do = Literal["attack", "shoot", "maneuver", "aid", "reload", "unjam", "flee", "hold", "other",
             "run", "hide", "barrier", "turn"]
VERBS = {"attack": "攻击", "shoot": "射击", "maneuver": "战技", "aid": "急救", "reload": "装填", "unjam": "排除故障",
         "flee": "逃跑", "hold": "防守", "other": "其他", "run": "全力奔跑", "hide": "躲藏", "barrier": "设置障碍",
         "turn": "回身迎战"}
HUMAN = {"力量": 50, "体质": 50, "体型": 60, "敏捷": 50, "意志": 50, "智力": 50}
NATURAL = ("爪", "咬", "撕", "抓", "踢", "触手", "刺", "撞", "角", "獠牙", "拳")  # a sheet's own attacks
SWARM = re.compile(r"数量多|成群|几十|一大群|无法对抗|蜂拥")
LETS_GO = re.compile(r"不追|不会追|任其离开|放他们走")  # a creature's notes: it does not go after those who run


class Order(BaseModel):
    """What one of them does this round."""

    do: Do = Field("attack", description="attack 近战攻击；shoot 开枪；maneuver 战技（缴械、推倒、制住）；aid 急救同伴；reload 装填；"
                   "unjam 排除故障；flee 逃跑；hold 只防守；other 别的事。追逐中：run 全力奔跑；hide 躲藏；barrier 设置障碍；turn 回身迎战")
    target: str = Field("", description="对象的 id")
    weapon: str = Field("", description="用哪件武器或哪种攻击，写名字；留空用最顺手的")
    maneuver: Literal["", "缴械", "推倒", "制住"] = ""
    stance: Stance = Field("dodge", description="这一轮被攻击时：dodge 闪避；fight 反击；cover 扑向掩体（只对开枪有用，会失去下个行动）")
    text: str = Field("", description="other 时，玩家要做的事，原话")


class Fight(BaseModel):
    round: int = 0
    fighters: list[Fighter]
    place: str = ""
    reason: str = ""
    started: int = 0  # the turn it began on
    orders: dict[str, Order] = Field(default_factory=dict)  # this round's, by fighter id
    engaged: list[list[str]] = Field(default_factory=list)  # pairs who traded blows this round: shots into them are at a penalty

    def get(self, fid: str) -> Fighter | None:
        return next((f for f in self.fighters if f.id == fid), None)

    def side(self, side: Side) -> list[Fighter]:
        return [f for f in self.fighters if f.side == side]

    def standing(self, side: Side) -> list[Fighter]:
        return [f for f in self.fighters if f.side == side and f.standing]

    def foes_of(self, f: Fighter) -> list[Fighter]:
        return [x for x in self.fighters if x.side != f.side and x.standing]


class Chase(BaseModel):
    round: int = 0
    runners: list[Fighter]
    track: Track
    quarry: Side  # who runs
    place: str = ""
    reason: str = ""
    started: int = 0
    orders: dict[str, Order] = Field(default_factory=dict)
    hidden: list[str] = Field(default_factory=list)  # quarry who hid and were not found

    def get(self, fid: str) -> Fighter | None:
        return next((f for f in self.runners if f.id == fid), None)

    def running(self, side: Side) -> list[Fighter]:
        return [f for f in self.runners if f.side == side and f.standing and f.id not in self.hidden]


# -- who fights ---------------------------------------------------------------------------------
INV_SKILLS = ("侦查", "潜行", "攀爬", "跳跃", "急救", "医学", "投掷", "游泳", "锁匠", "汽车驾驶", "聆听", "机械维修")


def from_investigator(inv: Investigator, things: list[str] | None = None, side: Side = "party", world: World | None = None) -> Fighter:
    """An investigator in a fight: their sheet's numbers, and every weapon they carry (the bare
    hands always), the readiest first: a loaded gun, else the best melee weapon. With the `world`,
    a gun's rounds and jam are its item's (worldly)."""
    c = inv.characteristics
    weapons = carried([*inv.inventory, *(things or [])])
    attacks = [attack_of(w, inv.value(w.skill)) for w in weapons]
    f = Fighter(id=inv.id, name=inv.name, kind="investigator", side=side, dex=c["敏捷"], con=c["体质"], str=c["力量"],
                hp=inv.hp, hp_max=inv.hp_max, build=inv.build, db=inv.damage_bonus, move=inv.move,
                dodge=inv.value("闪避"), fighting=inv.value(BRAWL), attacks=attacks, portrait=inv.portrait,
                skills={k: inv.value(k) for k in INV_SKILLS})
    sync(f, inv)
    if world is not None:
        worldly(f, world)
    f.weapon = readiest(f)
    return f


def readiest(f: Fighter) -> int:
    """The attack to hold: a gun that can fire, else the melee attack with the best chance."""
    guns = [i for i, a in enumerate(f.attacks) if a.firearm and a.usable()]
    if guns:
        return max(guns, key=lambda i: f.attacks[i].value)
    melee = [i for i, a in enumerate(f.attacks) if not a.firearm]
    return max(melee, key=lambda i: (f.attacks[i].value, not f.attacks[i].unarmed)) if melee else 0


def sync(f: Fighter, inv: Investigator) -> None:
    """The fighter as the investigator's sheet now stands (hit points and conditions are the sheet's)."""
    f.hp, f.hp_max = inv.hp, inv.hp_max
    for state in ("死亡", "濒死", "昏迷"):
        if state in inv.conditions:
            f.status = state
            return
    if f.status not in OUT:
        f.status = "重伤" if "重伤" in inv.conditions else ""


def _natural(stats: dict[str, int], db: str) -> list[Attack]:
    """Attacks a sheet names by themselves ("爪击": 50): the claws and teeth of a creature-like NPC."""
    return [Attack(name=k, skill=BRAWL, value=v, damage="1D6+DB", impale="咬" in k or "刺" in k)
            for k, v in stats.items() if any(w in k for w in NATURAL) and isinstance(v, int)]


def from_npc(npc: NPC, things: list[str] | None = None, side: Side = "foe", world: World | None = None) -> Fighter:
    """A module NPC in a fight. A sheet left empty is an ordinary person's numbers; what they carry
    is what the record has them hold, and what their look says they carry. With the `world`, their
    body is the one it keeps (Entity.life: hurt stays hurt) and their weapons its items (worldly)."""
    s = {**HUMAN, **stats_of(npc.stats)}
    db, build = damage_bonus(s["力量"], s["体型"])
    hp = s.get("生命") or max(1, (s["体质"] + s["体型"]) // 10)
    fighting = s.get(BRAWL, 25)
    attacks = _natural(s, db)
    for w in carried([*(things or []), npc.look]):
        value = s.get(w.skill) or (fighting if w.melee else s.get(canonical(w.skill), 20))
        attacks.append(attack_of(w, value))
    t = traits(" ".join(f"{k}{v}" for k, v in npc.stats.items() if isinstance(v, str)))
    f = Fighter(id=npc.id, name=npc.name, kind="npc", side=side, dex=s["敏捷"], con=s["体质"], str=s["力量"], hp=hp, hp_max=hp,
                build=t.get("build", build), db=t.get("db", db), move=s.get("移动", 8), dodge=s.get("闪避", s["敏捷"] // 2),
                fighting=max([fighting, *(a.value for a in attacks if not a.firearm)]), attacks=attacks,
                portrait=npc.portrait or "", look=npc.look, notes=npc.goal,
                skills={k: v for k, v in s.items() if k not in HUMAN})
    if npc.status in ("受伤", "重伤"):
        f.status = "重伤" if npc.status == "重伤" else ""
        f.hp = max(1, hp // 2)
    if world is not None:
        worldly(f, world)
    f.weapon = readiest(f)
    return f


def from_creature(c: Creature, side: Side = "foe", portrait: str = "", world: World | None = None) -> Fighter:
    """A creature in a fight: its sheet's numbers, its attacks as written, what its notes say the
    rules can use (rules/combat.traits). A swarm (几十只, 无法对抗) fights as one, hard to put down."""
    s = stats_of(c.stats)
    t = traits(f"{c.notes} {' '.join(c.attacks)}")
    strength, con, size, dex = s.get("力量", 60), s.get("体质", 60), s.get("体型", 60), s.get("敏捷", 50)
    db, build = damage_bonus(strength, size)
    attacks = [attack_written(w) for line in c.attacks if (w := parse_attack(line)) is not None] or _natural(s, db)
    swarm = bool(SWARM.search(c.notes + c.description)) and not c.stats
    if not attacks:
        attacks = [Attack(name="撕扯" if swarm else "攻击", skill=BRAWL, value=50 if swarm else 45, damage="1D6+DB")]
    hp = s.get("生命") or max(1, (con + size) // 10)
    f = Fighter(id=c.id, name=c.name, kind="creature", side=side, dex=dex, con=con, str=strength,
                   hp=60 if swarm else hp, hp_max=60 if swarm else hp, build=t.get("build", build), db=t.get("db", db),
                   move=t.get("move") or s.get("移动", 8), dodge=s.get("闪避", dex // 2),
                   fighting=max(a.value for a in attacks if not a.firearm) if any(not a.firearm for a in attacks) else 40,
                   attacks=attacks, per_round=4 if swarm else attacks_per_round(c.attacks, c.notes),
                   armor=t.get("armor", 0), guns_halved=t.get("guns_halved", False), regen=t.get("regen", 0),
                   knockout=t.get("knockout", 0), human=False, look=c.description, notes=c.notes, sanity=c.sanity,
                   portrait=portrait, skills={k: v for k, v in s.items() if k not in ("力量", "体质", "体型", "敏捷", "生命")})
    if world is not None:
        worldly(f, world)
        f.weapon = readiest(f)
    return f


# -- the world's bodies and things (0.0.4 M1: the old engine reads and writes them through effects) ----
def holder_of(world: World, f: Fighter) -> str:
    """The world id whose body and things a fighter's are: a creature sheet that is an NPC's form is the NPC."""
    from .encounter.profiles import combatant

    return f.id if f.kind == "investigator" else combatant(world, f.id)


def item_for(world: World, f: Fighter, a: Attack) -> str | None:
    """The item an attack is: the record's item entity they hold, an investigator's inventory line,
    or a weapon written on their sheet ("<holder>:<name>"); None for their own claws, teeth and
    fists, which nobody can take."""
    if a.unarmed:
        return None
    holder = holder_of(world, f)
    known = weapon_in(a.name)
    for e in world.rec.entities.values():
        if e.kind == "item" and e.at == holder and (e.name == a.name or (known is not None and weapon_in(e.name) is known)):
            return e.id
    if f.kind == "investigator":
        inv = next((i for i in world.st.investigators if i.id == f.id), None)
        lines = inv.inventory if inv is not None else []
        return next((line for line in lines if line == a.name or (known is not None and weapon_in(line) is known)), None)
    if f"{holder}:{a.name}" in world.rec.entities:
        return f"{holder}:{a.name}"
    if not a.firearm and (known is None or known is UNARMED) and (f.kind == "creature" or any(w in a.name for w in NATURAL)):
        return None  # natural: claws, teeth, the sheet's own 爪击
    return f"{holder}:{a.name}"


def life_status(conditions: list[str], regenerating: bool, current: str) -> str:
    """A fighter's status from a body's conditions (the old engine's words)."""
    for c in ("死亡", "濒死"):
        if c in conditions:
            return c
    if regenerating:
        return "倒下"
    if "昏迷" in conditions:
        return "昏迷"
    if current in OUT:
        return current
    return "重伤" if "重伤" in conditions else ""


def worldly(f: Fighter, world: World) -> None:
    """The fighter as the world keeps them: an NPC's or a creature's HP and state from Entity.life;
    a weapon that left them (disarmed, handed over) gone from their attacks; a gun's rounds and jam
    its item's WeaponState. A fight no longer reloads or unjams anything by itself."""
    if f.kind != "investigator":
        ent = world.rec.entities.get(holder_of(world, f))
        if ent is not None and ent.life is not None:
            life = ent.life
            f.hp, f.hp_max = life.hp, life.hp_max
            f.status = life_status(life.conditions, life.regenerating, f.status)
    kept = []
    for a in f.attacks:
        item = item_for(world, f, a)
        ent = world.rec.entities.get(item) if item else None
        if ent is not None and ent.at != holder_of(world, f):
            continue
        if ent is not None and ent.weapon is not None and a.firearm:
            if ent.weapon.loaded is not None:
                a.ammo = ent.weapon.loaded
            a.jammed = ent.weapon.jammed
        kept.append(a)
    f.attacks = kept


# -- an order from the screen or the player's words ------------------------------------------------
_STANCE = {"闪避": "dodge", "躲闪": "dodge", "反击": "fight", "还手": "fight", "掩护": "cover", "掩体": "cover", "找掩护": "cover"}
_PREFIX: list[tuple[tuple[str, ...], Do]] = [
    (("射击", "开枪", "开火", "射"), "shoot"),
    (("缴械", "推倒", "摔倒", "制住", "擒抱", "压制"), "maneuver"),
    (("急救", "包扎", "止血"), "aid"),
    (("装填", "上膛", "换弹"), "reload"),
    (("排除故障", "排故", "修枪"), "unjam"),
    (("逃跑", "逃走", "撤退", "快逃"), "flee"),
    (("防守", "只防守", "闪避", "格挡", "等待"), "hold"),
    (("全力奔跑", "快跑", "奔跑", "跑"), "run"),
    (("躲藏", "藏起来", "躲起来"), "hide"),
    (("设置障碍", "推倒障碍", "挡住"), "barrier"),
    (("回身迎战", "转身迎战", "迎战"), "turn"),
    (("攻击", "打", "砍", "刺", "砸", "踢", "揍", "扑向"), "attack"),
]


def names_of(f: Fighter) -> list[str]:
    """What a player may call them: the whole name, its parts, the name without a quoted epithet."""
    plain = f.name.replace("“", "").replace("”", "").replace("（", "(").replace("）", ")")
    parts = re.split(r"[·・()\s]", plain)
    return sorted({f.name, plain, *(p for p in parts if len(p) >= 2)}, key=len, reverse=True)


def target_in(text: str, fighters: list[Fighter]) -> Fighter | None:
    """The fighter the words name, the longest name first."""
    hits = [(len(n), f) for f in fighters for n in names_of(f) if n and n in text]
    return max(hits, key=lambda h: h[0])[1] if hits else None


def parse_order(text: str, me: Fighter, fighters: list[Fighter], *, chase: bool = False) -> Order | None:
    """An order in the words the screen writes ("攻击 食尸鬼道格拉斯（.38 左轮手枪）｜被攻击时反击"), or
    words that start the same way ("我开枪打它"). None for anything else: the model reads those."""
    t = text.strip().replace("|", "｜")
    stance: Stance = me.stance
    if found := re.search(r"被攻击时\s*(闪避|反击|找掩护)", t):
        stance = _STANCE[found.group(1)]  # type: ignore[assignment]
        t = t[: found.start()].rstrip("｜ ，,")
    head = re.sub(r"^(我|我要|我想|我来)", "", t).strip()
    do: Do | None = next((d for words, d in _PREFIX if head.startswith(words)), None)
    if do is None:
        return None
    if chase and do == "attack":
        do = "turn"
    others = [f for f in fighters if f.id != me.id]
    target = target_in(head, others)
    weapon = ""
    if found := re.search(r"[（(]([^）)]+)[）)]", head):
        weapon = found.group(1).strip()
    elif w := weapon_in(head):
        weapon = w.name
    else:
        weapon = next((a.name for a in me.attacks if a.name in head), "")
    maneuver = next((m for m in ("缴械", "推倒", "制住") if m in head), "")
    if do == "maneuver" and not maneuver:
        maneuver = "推倒" if "摔" in head else "制住"
    if do == "attack" and weapon and (w := next((a for a in me.attacks if a.name == weapon), None)) and w.firearm:
        do = "shoot"
    if do == "hold" and (st := next((v for k, v in _STANCE.items() if k in head), None)):
        stance = st  # type: ignore[assignment]
    return Order(do=do, target=target.id if target else "", weapon=weapon, maneuver=maneuver, stance=stance,  # type: ignore[arg-type]
                 text=text.strip())


def order_text(o: Order, names: dict[str, str]) -> str:
    """The order as the screen writes it, which parse_order reads back."""
    verb = o.maneuver if o.do == "maneuver" and o.maneuver else VERBS[o.do]
    who = names.get(o.target, "")
    out = f"{verb} {who}".strip() if who else verb
    if o.weapon and o.do in ("attack", "shoot", "maneuver"):
        out += f"（{o.weapon}）"
    if o.do not in ("run", "hide", "barrier", "turn"):
        out += f"｜被攻击时{STANCES[o.stance]}"
    return out


def choices(me: Fighter, fight: Fight) -> list[tuple[str, str]]:
    """What the screen offers an investigator this round: (the order's words, a line saying it)."""
    foes = fight.foes_of(me)
    allies = [x for x in fight.side(me.side) if x.id != me.id]
    names = {f.id: f.name for f in fight.fighters}
    stance: Stance = me.stance if me.stance != "cover" or any(a.firearm for f in foes for a in f.attacks) else "dodge"
    out: list[tuple[str, str]] = []
    for f in foes:
        for a in me.attacks:
            if a.firearm:
                if a.jammed or a.ammo == 0:
                    continue
                o = Order(do="shoot", target=f.id, weapon=a.name, stance=stance)
                out.append((order_text(o, names), f"{a.skill} {a.value} · 余弹 {a.ammo}" if a.ammo is not None else f"{a.skill} {a.value}"))
            else:
                o = Order(do="attack", target=f.id, weapon=a.name, stance=stance)
                out.append((order_text(o, names), f"{a.skill} {a.value} · 伤害 {damage_words(a.damage, me.db)}"))
        for m in ("推倒", "缴械", "制住"):
            out.append((order_text(Order(do="maneuver", target=f.id, maneuver=m, stance=stance), names), f"战技 · 体格 {me.build} 对 {f.build}"))
    for f in allies:
        if f.status in ("濒死", "昏迷", "重伤") or f.hp < f.hp_max:
            out.append((order_text(Order(do="aid", target=f.id, stance=stance), names), f"急救 {me.value('急救')}"))
    for a in me.attacks:
        if a.firearm and a.ammo is not None and a.ammo < a.load:
            out.append((order_text(Order(do="reload", stance=stance), names), f"{a.name} 装满 {a.load} 发"))
            break
    if any(a.jammed for a in me.attacks):
        out.append((order_text(Order(do="unjam", stance=stance), names), "这一轮不能射击"))
    out.append((order_text(Order(do="hold", stance="fight" if stance == "dodge" else "dodge"), names), "不出手，换一种防守"))
    out.append((order_text(Order(do="flee", stance=stance), names), "离开战斗"))
    return out


def damage_words(damage: str, db: str) -> str:
    """A weapon's damage as a player reads it: the bonus written in (1D3+1D4), none when it has none."""
    bonus = db.strip()
    has = "DB" in damage
    plain = damage.replace("+半DB", "").replace("+DB", "").replace("半DB", "").replace("DB", "").replace("+灼烧", "+火")
    if not has or bonus in ("", "0", "+0"):
        return plain
    return plain + (bonus if bonus.startswith(("+", "-")) else "+" + bonus) + ("（减半）" if "半DB" in damage else "")


def chase_choices(me: Fighter, ch: Chase) -> list[tuple[str, str]]:
    names = {f.id: f.name for f in ch.runners}
    moving = [f for f in ch.runners if f.standing and f.id not in ch.hidden]
    steps = chase_rules.actions_for(me, min((f.speed for f in moving), default=me.speed))
    out = [(order_text(Order(do="run"), names), f"这一轮能跑 {steps} 段")]
    if me.side == ch.quarry:
        lead = chase_rules.gap(ch.running(ch.quarry), ch.running("foe" if ch.quarry == "party" else "party"))
        if lead >= chase_rules.SIGHT:
            out.append((order_text(Order(do="hide"), names), f"潜行 {me.value('潜行')} 对追兵的侦查"))
        out.append((order_text(Order(do="barrier"), names), "在身后制造障碍，失去一次移动"))
    near = [f for f in ch.runners if f.side != me.side and f.standing and abs(f.pos - me.pos) <= 0]
    for f in near:
        out.append((order_text(Order(do="turn", target=f.id), names), "停下来动手，追逐转为战斗"))
    return out


# -- when nobody is asked ------------------------------------------------------------------------
def rule_of_thumb(fight: Fight, who: Fighter) -> Order:
    """What someone does when the tactics agent cannot be asked: a badly hurt person runs, a
    hurt ally with a first-aider at hand gets help, everyone else hits the foe that looks weakest
    with their best attack, fighting back when they fight better than they dodge."""
    foes = fight.foes_of(who)
    stance: Stance = "fight" if who.fighting >= who.dodge else "dodge"
    if not foes:
        return Order(do="hold", stance=stance)
    if who.human and who.kind == "npc" and who.hp * 4 <= who.hp_max:
        return Order(do="flee", stance=stance)
    if who.kind == "investigator" and who.value("急救") >= 30:
        dying = [x for x in fight.side(who.side) if x.status == "濒死"]
        if dying:
            return Order(do="aid", target=dying[0].id, stance=stance)
    if any(a.jammed for a in who.attacks) and not any(not a.firearm and not a.unarmed for a in who.attacks):
        return Order(do="unjam", stance=stance)
    target = min(foes, key=lambda f: (f.hp / max(1, f.hp_max), -f.dex))
    atk = who.attacks[readiest(who)] if who.attacks else None
    if atk is None:
        return Order(do="attack", target=target.id, stance=stance)
    return Order(do="shoot" if atk.firearm else "attack", target=target.id, weapon=atk.name, stance=stance)


def pursuers(fight: Fight) -> list[str]:
    """Who goes after investigators that ran, when nobody is asked: creatures do, unless their
    notes say they let them go; people do not."""
    return [f.id for f in fight.standing("foe") if not f.human and not LETS_GO.search(f.notes)]


# -- how it ends ---------------------------------------------------------------------------------
def outcome(fight: Fight) -> str:
    """"" while it goes on; else won (no foe standing), fled (the investigators all got away), lost."""
    if not fight.standing("foe"):
        return "won"
    party = fight.side("party")
    if not any(f.standing for f in party):
        return "fled" if any(f.status == "逃走" for f in party) and not all(f.status in DOWN for f in party) else "lost"
    return ""


def summary(fight: Fight, end: str) -> str:
    """The end of a fight in a line: who is down, who ran, who is hurt."""
    rows = []
    for f in fight.fighters:
        word = f.status if f.status in (*DOWN, *OUT) else f.wound_word() if f.kind != "investigator" else (
            f.status or ("受伤" if f.hp < f.hp_max else "无伤"))
        rows.append(f"{f.name}{word}")
    head = {"won": "调查员一方占了上风", "fled": "调查员逃离了", "lost": "调查员一方全部倒下", "": "战斗中止"}[end]
    return f"{head}：" + "，".join(rows)


def view(fight: Fight) -> dict:
    """The fight as the screens draw it. A foe's hit points stay off it: its wounds in words."""
    return {"round": fight.round, "place": fight.place, "fighters": [{
        "id": f.id, "name": f.name, "kind": f.kind, "side": f.side, "status": f.status, "wound": f.wound_word(),
        "hp": f.hp if f.side == "party" else None, "hp_max": f.hp_max if f.side == "party" else None,
        "portrait": f.portrait, "look": f.look[:60], "weapon": f.wielding.name, "stance": f.stance,
        "prone": f.prone, "held": bool(f.held_by), "dex": f.dex} for f in fight.fighters]}


def chase_view(ch: Chase) -> dict:
    return {"round": ch.round, "quarry": ch.quarry, "legs": [{"name": leg.name, "look": leg.look,
            "obstacle": leg.obstacle.model_dump() if leg.obstacle else None} for leg in ch.track.legs],
            "runners": [{"id": f.id, "name": f.name, "side": f.side, "pos": f.pos, "speed": f.speed, "actions": f.actions,
                         "status": f.status, "wound": f.wound_word(), "hidden": f.id in ch.hidden,
                         "hp": f.hp if f.side == "party" else None, "hp_max": f.hp_max if f.side == "party" else None,
                         "portrait": f.portrait} for f in ch.runners]}


# -- a way to run along when nobody draws one ---------------------------------------------------
TRACKS: dict[str, list[tuple[str, str, Obstacle | None]]] = {
    "graveyard": [("墓园小径", "碎石路在墓碑间拐来拐去", None),
                  ("墓碑间的窄道", "两排墓碑挤得只容一人侧身", Obstacle(kind="hazard", what="倒伏的墓碑", skill="跳跃")),
                  ("新挖的墓穴", "一个敞着口的土坑横在路中间", Obstacle(kind="hazard", what="敞口的墓穴", skill="敏捷", harm="1D3")),
                  ("柏树下", "枝叶垂到地面，什么也看不清", None),
                  ("铁栅栏", "一人多高的铁栅栏，尖头锈迹斑斑", Obstacle(kind="barrier", what="锈蚀的铁栅栏", skill="攀爬")),
                  ("墓园外的土路", "泥泞的车辙一直伸进夜色", None),
                  ("田埂", "冻硬的田埂高低不平", Obstacle(kind="hazard", what="冻硬的田埂", skill="敏捷")),
                  ("远处的树林", "黑压压的一片，正好藏身", None)],
    "street": [("街口", "煤气灯一盏接一盏", None),
               ("人行道", "店铺的雨棚压得很低", Obstacle(kind="hazard", what="结冰的路面", skill="敏捷")),
               ("小巷", "两堵墙之间只有一条窄缝", None),
               ("堆满木箱的后院", "木箱摞得歪歪斜斜", Obstacle(kind="hazard", what="倒塌的木箱", skill="闪避", harm="1D3")),
               ("木板围墙", "一人高的木板墙", Obstacle(kind="barrier", what="木板围墙", skill="攀爬")),
               ("另一条街", "几个路人愕然回头", None),
               ("马车道", "一辆运货马车正迎面而来", Obstacle(kind="hazard", what="迎面的马车", skill="闪避", harm="1D6")),
               ("火车站方向", "人多的地方", None)],
    "woods": [("林间小路", "落叶盖住了脚下", None),
              ("灌木丛", "荆棘挂住衣服", Obstacle(kind="hazard", what="荆棘丛", skill="敏捷", harm="1D2")),
              ("倒下的树干", "一棵老树横在路上", Obstacle(kind="hazard", what="倒伏的树干", skill="跳跃")),
              ("溪沟", "沟里的水结了一层薄冰", Obstacle(kind="hazard", what="结冰的溪沟", skill="跳跃", harm="1D3")),
              ("松林深处", "看不见天", None),
              ("陡坡", "坡上全是松针", Obstacle(kind="hazard", what="滑溜的陡坡", skill="攀爬")),
              ("林边的空地", "月光照得一片惨白", None),
              ("大路", "远处有灯火", None)],
    "house": [("走廊", "地板吱呀作响", None),
              ("楼梯", "陡而窄的楼梯", Obstacle(kind="hazard", what="陡窄的楼梯", skill="敏捷", harm="1D3")),
              ("堆满家具的房间", "椅子和箱子挡住去路", Obstacle(kind="hazard", what="挡路的家具", skill="闪避")),
              ("后门", "门闩锈死了", Obstacle(kind="barrier", what="锈死的后门", skill="力量")),
              ("后院", "晾衣绳横七竖八", Obstacle(kind="hazard", what="晾衣绳", skill="敏捷")),
              ("篱笆", "低矮的木篱笆", Obstacle(kind="barrier", what="木篱笆", skill="跳跃")),
              ("邻家的菜地", "冻土高低不平", None),
              ("街上", "路灯下有人影走动", None)],
    "cellar": [("潮湿的通道", "头顶不停滴水", None),
               ("塌了一半的拱顶", "要弯着腰钻过去", Obstacle(kind="hazard", what="低矮的拱顶", skill="敏捷", harm="1D3")),
               ("积水的岔口", "水没过脚踝，冷得刺骨", Obstacle(kind="hazard", what="积水", skill="体质")),
               ("狭窄的缝隙", "只容一人侧身挤过", Obstacle(kind="barrier", what="窄缝", skill="敏捷")),
               ("堆骨的洞室", "脚下咔嚓作响", Obstacle(kind="hazard", what="满地的骨头", skill="闪避")),
               ("向上的斜坡", "能闻到外面的空气", Obstacle(kind="hazard", what="湿滑的斜坡", skill="攀爬")),
               ("出口", "一线灰白的光", None)],
}
TRACK_OF = {"graveyard": "graveyard", "church": "graveyard", "street": "street", "station": "street", "shop": "street",
            "office": "street", "woods": "woods", "hills": "woods", "fields": "woods", "water": "woods",
            "house": "house", "study": "house", "bedroom": "house", "glasshouse": "house", "cellar": "cellar"}


def default_track(kind: str) -> Track:
    """A way to run along for a place of this kind (scenery.kind_of), when the agent draws none."""
    rows = TRACKS[TRACK_OF.get(kind, "street")]
    return Track(legs=[Leg(name=n, look=look, obstacle=ob.model_copy() if ob else None) for n, look, ob in rows])


__all__ = ["DOWN", "OUT", "UNARMED", "Chase", "Fight", "Order"]
