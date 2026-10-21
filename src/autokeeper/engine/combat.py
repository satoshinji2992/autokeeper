"""The old chase engine's pieces (0.0.4 M2: fights run on engine/encounter; WP-D removes this file).

Building a runner from an investigator's sheet, a module NPC or a creature as the world keeps them
(the encounter's people, when a fight turns into a chase), the old chase's orders and their words
(engine/encounter/runner.chase_options offers them, matched exactly), its view for the screens, and
the ways a chase runs along when the module draws none. `Fight` is only an old save's, read by
engine/encounter/migrate."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..module.schema import NPC, Creature
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


# -- an old chase's order in words ------------------------------------------------------------------
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
