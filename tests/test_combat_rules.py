"""The numbers of a fight and a chase (rules/combat.py, rules/chase.py, rules/weapons.py)."""

from __future__ import annotations

from autokeeper.rules import chase, combat
from autokeeper.rules.combat import Attack, Fighter
from autokeeper.rules.weapons import carried, parse_attack, weapon_in


def fighter(fid: str, side="party", **kw) -> Fighter:
    base = {"id": fid, "name": fid, "kind": "npc", "side": side, "dex": 50, "hp": 12, "hp_max": 12, "dodge": 30, "fighting": 50}
    return Fighter(**(base | kw))


FIST = Attack(name="徒手", skill="格斗(斗殴)", value=50, damage="1D3+DB")
KNIFE = Attack(name="小刀", skill="格斗(斗殴)", value=50, damage="1D4+DB", impale=True)
GUN = Attack(name=".38 左轮手枪", skill="射击(手枪)", value=50, damage="1D10", firearm=True, impale=True, range=15, ammo=6,
             load=6, malfunction=100)


def test_who_acts_first_and_a_readied_gun_goes_before_a_quicker_hand():
    quick = fighter("quick", dex=70, attacks=[FIST])
    gunman = fighter("gunman", dex=40, attacks=[GUN])
    slow = fighter("slow", dex=30, attacks=[FIST])
    assert combat.order([slow, quick, gunman]) == ["gunman", "quick", "slow"]  # 40 + 50 for the gun


def test_a_dodge_wins_a_tie_and_fighting_back_loses_one(fake):
    a, d = fighter("a", attacks=[FIST]), fighter("d", side="foe", dodge=30)
    fake.queue(40, 25)  # 40 of 50 regular, 25 of 30 regular: a tie
    ex = combat.melee(fake, a, FIST, d)
    assert ex.reaction == "dodge" and ex.winner == "none" and d.responded == 1

    d2 = fighter("d2", side="foe", stance="fight", fighting=60)
    fake.queue(40, 50).queue_totals(2)  # both regular: the attacker's
    ex = combat.melee(fake, a, FIST, d2)
    assert ex.reaction == "fight" and ex.winner == "attacker" and ex.hurt == "d2" and ex.damage == 2


def test_fighting_back_better_turns_the_blow_on_the_attacker(fake):
    a = fighter("a", attacks=[FIST])
    d = fighter("d", side="foe", stance="fight", fighting=60, db="+1D4", attacks=[Attack(name="爪击", skill="格斗(斗殴)", value=60, damage="1D6+DB")])
    fake.queue(45, 20).queue_totals(7)  # a regular, d hard
    ex = combat.melee(fake, a, FIST, d)
    assert ex.winner == "defender" and ex.hurt == "a" and ex.damage == 7 and "1D6+1D4" in ex.damage_text


def test_the_outnumbered_give_every_further_attacker_a_bonus_die(fake):
    a, b, d = fighter("a", attacks=[FIST]), fighter("b", attacks=[FIST]), fighter("d", side="foe")
    fake.queue(90, 90)  # misses, but d answered
    combat.melee(fake, a, FIST, d)
    fake.queue(90, 90)
    ex = combat.melee(fake, b, FIST, d)
    assert ex.bonus == 1 and "以多打少" in ex.reasons
    many = fighter("m", side="foe", per_round=3)
    fake.queue(90, 90)
    combat.melee(fake, a, FIST, many)
    fake.queue(90, 90)
    assert combat.melee(fake, b, FIST, many).bonus == 0  # it answers three a round before it is outnumbered


def test_an_extreme_success_with_a_knife_impales(fake):
    a, d = fighter("a", db="+1D4", attacks=[KNIFE]), fighter("d", side="foe", dodge=20)
    fake.queue(5, 90).queue_totals(3)  # extreme (5 of 50), the dodge fails; the knife again: 3
    ex = combat.melee(fake, a, KNIFE, d)
    assert ex.extreme and ex.damage == 4 + 4 + 3 and ex.damage_text.startswith("贯穿")
    fake.queue(5, 90)
    ex = combat.melee(fake, a, FIST, d)  # fists do not impale: the most they can, no more
    assert ex.damage == 3 + 4 and ex.damage_text.startswith("极难成功")


def test_a_surprised_defender_cannot_answer(fake):
    a, d = fighter("a", attacks=[FIST]), fighter("d", side="foe", surprised=True)
    fake.queue(30).queue_totals(3)  # one roll only: the attacker's
    ex = combat.melee(fake, a, FIST, d)
    assert ex.answer is None and ex.winner == "attacker" and d.responded == 0


def test_a_shot_uses_a_round_jams_on_its_number_and_cannot_be_fought_back(fake):
    a = fighter("a", attacks=[GUN.model_copy()])
    gun = a.attacks[0]
    d = fighter("d", side="foe", stance="fight")
    fake.queue(30).queue_totals(6)
    ex = combat.shoot(fake, a, gun, d)
    assert ex.winner == "attacker" and ex.damage == 6 and ex.answer is None and gun.ammo == 5
    jams = gun.model_copy(update={"malfunction": 96})
    fake.queue(97)
    ex = combat.shoot(fake, a, jams, d)
    assert ex.jammed and jams.jammed and not jams.usable()
    empty = gun.model_copy(update={"ammo": 0})
    assert combat.shoot(fake, a, empty, d).empty  # nothing to roll


def test_diving_for_cover_costs_the_next_action_and_halved_guns(fake):
    a = fighter("a", attacks=[GUN.model_copy()])
    d = fighter("d", side="foe", stance="cover", dodge=40)
    fake.queue(10, 30).queue_totals(8)  # the dive succeeds: a penalty die on the shot, which still hits
    ex = combat.shoot(fake, a, a.attacks[0], d)
    assert ex.reaction == "cover" and ex.bonus == -1 and d.lost_action and ex.winner == "attacker"
    ghoul = fighter("g", side="foe", guns_halved=True, stance="dodge")
    fake.queue(30).queue_totals(9)
    assert combat.shoot(fake, a, a.attacks[0], ghoul).damage == 4


def test_a_manoeuvre_against_someone_much_bigger_is_impossible(fake):
    a, d = fighter("a", build=0, attacks=[FIST]), fighter("d", side="foe", build=3)
    ex = combat.melee(fake, a, FIST, d, maneuver="推倒")
    assert ex.roll is None and ex.winner == "none" and "体格" in ex.reasons[0]
    d1 = fighter("d1", side="foe", build=1)
    fake.queue(20, 90)
    ex = combat.melee(fake, a, FIST, d1, maneuver="缴械")
    assert ex.bonus == -1 and ex.effect == "缴械" and ex.damage == 0


def test_what_a_blow_does_to_an_npc_and_to_a_creature(fake):
    man = fighter("man", side="foe", hp=10, hp_max=10, con=40)
    fake.queue(80)  # CON fails
    assert combat.wound(fake, man, 6) == ["重伤", "昏死过去"] and man.status == "昏迷"
    other = fighter("o", side="foe", hp=10, hp_max=10, con=40)
    fake.queue(20)
    combat.wound(fake, other, 5)
    assert other.status == "重伤"
    fake.queue(20)  # another blow of half its hit points: CON again
    combat.wound(fake, other, 5)
    assert other.status == "濒死" and other.hp == 0
    fake.queue(90)
    assert not combat.dying(fake, other).passed and other.status == "死亡"
    beast = fighter("b", side="foe", human=False, hp=8, hp_max=8)
    combat.wound(fake, beast, 9)
    assert beast.status == "死亡"
    healer = fighter("h", side="foe", human=False, hp=8, hp_max=8, regen=1)
    combat.wound(fake, healer, 9)
    assert healer.status == "倒下" and not healer.standing
    assert combat.heal_round(healer) and healer.status == "" and healer.hp == 1  # it gets back up
    ghoul = fighter("g", side="foe", human=False, hp=12, hp_max=12, knockout=6)
    combat.wound(fake, ghoul, 3, unarmed=True)
    assert combat.wound(fake, ghoul, 3, unarmed=True) == ["被打晕了"] and ghoul.status == "昏迷"


def test_a_creatures_notes_and_attacks_as_the_modules_write_them():
    ghoul = "伤害加值 0，体格 0。枪械伤害减半（向下取整）。HP 降到 0 死亡；只被拳头打时损失 6 点即被击晕。"
    assert combat.traits(ghoul) == {"guns_halved": True, "knockout": 6, "db": "0", "build": 0}
    erie = "体格 1，移动 10，伤害加成 +1D4。护甲：无，但每轮恢复 1 点 HP。"
    assert combat.traits(erie) == {"regen": 1, "db": "+1D4", "build": 1, "move": 10}
    claw = parse_attack("爪击 50% (25/10)，伤害 1D6")
    assert (claw.name, claw.value, claw.damage, claw.firearm) == ("爪击", 50, "1D6", False)
    rifle = parse_attack("射击（步枪）30%，春田 M1903 栓式猎枪，1 发/轮，弹夹 5，射程 110 码，2D6+4；弹夹里只有 1 发")
    assert (rifle.skill, rifle.damage, rifle.ammo, rifle.range) == ("射击(步枪/霰弹枪)", "2D6+4", 1, 110)
    assert parse_attack("每回合 1 次（入门模组简化）") is None
    assert combat.stats_of({"STR": 90, "HP": 18, "DEX": 80})["力量"] == 90


def test_the_damage_bonus_goes_with_a_melee_weapon_and_half_of_it_with_a_throw():
    assert combat.split_damage("1D3+DB", "+1D4") == ("1D3", "+1D4")
    assert combat.split_damage("1D4+半DB", "+1D6") == ("1D4", "+1D3")
    assert combat.split_damage("1D10", "+1D4") == ("1D10", "")
    assert combat.split_damage("4D6/2D6/1D6", "0", band=1) == ("2D6", "")
    assert combat.split_damage("1D6+灼烧", "0") == ("1D6", "")
    assert combat.max_total("1D4+2+1D4") == 10


def test_an_investigator_fights_with_what_they_carry():
    assert [w.name for w in carried(["手电筒", "一把 .38 左轮手枪", "折刀"])] == ["徒手", ".38 左轮手枪", "小刀"]
    assert weapon_in("一根拨火棍").name == "棍棒" and weapon_in("自动手枪").name == ".32 自动手枪"


def test_a_chase_speed_head_start_and_actions(fake):
    q = fighter("q", move=8, con=50)
    p = fighter("p", side="foe", move=9, con=50)
    fake.queue(5, 70)  # q extreme: 9; p fails: 8
    chase.speed_roll(fake, q)
    chase.speed_roll(fake, p)
    assert (q.speed, p.speed) == (9, 8) and chase.gone_at_once([q], [p])
    p.speed = 10
    assert not chase.gone_at_once([q], [p])
    assert chase.actions_for(p, slowest=9) == 2 and chase.actions_for(q, slowest=9) == 1


def test_crossing_a_hazard_and_a_barrier(fake):
    runner = fighter("r")
    ice = chase.Leg(name="结冰的台阶", obstacle=chase.Obstacle(kind="hazard", what="结冰的台阶", skill="敏捷", harm="1D3"))
    fake.queue(80).queue_totals(2, 1)
    roll, crossed = chase.cross(fake, runner, ice, value=50)
    assert not roll.passed and crossed.lost == 2 and crossed.harm == 1
    gate = chase.Leg(name="铁门", obstacle=chase.Obstacle(kind="barrier", what="锁着的铁门", skill="力量", difficulty="困难"))
    fake.queue(30)  # 30 of 60 is hard: through, and it stays open
    roll, crossed = chase.cross(fake, runner, gate, value=60)
    assert crossed.passed and gate.obstacle.open
    assert chase.cross(fake, runner, gate, value=60) == (None, None)  # open behind them


def test_out_of_sight_the_quarry_can_hide(fake):
    q = fighter("q", pos=4, skills={"潜行": 60})
    p = fighter("p", side="foe", pos=1, skills={"侦查": 40})
    assert chase.gap([q], [p]) == 3
    fake.queue(10, 30)  # hard against regular
    _, seen, hidden = chase.hide(fake, q.value("潜行"), [p])
    assert hidden and seen[0][0] == "p"
    fake.queue(10, 8)  # hard against hard: not hidden
    assert not chase.hide(fake, 60, [p])[2]


def test_whatever_comes_to_hand_is_a_club():
    from autokeeper.engine.combat import Order
    from autokeeper.engine.fighting import Fighting

    me = fighter("me", fighting=45, attacks=[FIST])
    candlestick = Fighting._weapon(me, Order(do="attack", target="x", weapon="铜烛台"))
    assert (candlestick.name, candlestick.damage, candlestick.value) == ("铜烛台", "1D6+DB", 45)
    poker = Fighting._weapon(me, Order(do="attack", target="x", weapon="拨火棍"))
    assert poker.damage == "1D8+DB" and poker.name == "拨火棍"  # what it is most like
