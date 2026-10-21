"""What a fight and a chase read of a module, from the compile side: the schema the import models
fill, the checks of `autokeeper check`, the skill package for external agents, the compile-time pass
that fills what the rules cannot read (module/adapt.py), and the players' copy at a LAN table."""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import networkx as nx
import pytest
import skills_ref

from autokeeper import paths
from autokeeper.module import adapt, graph, yamledit
from autokeeper.module.compiler import compile_module
from autokeeper.module.schema import Creature, Event, Item, Method, Module, Stake
from autokeeper.paths import EXAMPLE
from autokeeper.rules.chase import Leg, Obstacle
from autokeeper.rules.combat import traits
from autokeeper.rules.weapons import attacks_per_round, parse_attack

WAY = [Leg(name="齿轮间门口", look="咔哒声就在头顶"),
       Leg(name="又窄又陡的楼梯", obstacle=Obstacle(kind="hazard", what="陡窄的楼梯", skill="敏捷", harm="1D3")),
       Leg(name="楼梯口", look="守钟人打盹的椅子翻倒在地"),
       Leg(name="钟楼大门", obstacle=Obstacle(kind="barrier", what="沉重的木门", skill="力量", hp=15)),
       Leg(name="广场", look="告示栏在雾里只剩一个影子"),
       Leg(name="去报社的街", look="路灯一盏接一盏")]


def armed() -> Module:
    """The format example with something to fight: the thing behind the gears, a warden who fights
    with a wrench, a way down from the tower, and an event that scripts the attack."""
    m = Module.load(EXAMPLE)
    m.flags["gears_opened"] = "齿轮间的门被打开了"
    m.creatures.append(Creature(id="coil", name="齿轮背后的藤", called="缠在齿轮上的东西", description="缠在齿轮背后、还在动的东西", sanity="1/1D6",
                                stats={"生命": 9, "敏捷": 60, "力量": 70, "体型": 50},
                                attacks=["缠绕 45%，伤害 1D4+DB", "每轮 2 次"], notes="护甲 2；枪伤减半；每轮恢复 1 点；不追"))
    m.npc("warden").stats = {"力量": 60, "敏捷": 50, "生命": 11, "格斗(斗殴)": 45}
    m.items.append(Item(id="warden_wrench", name="大扳手", description="油乎乎的大扳手", where="tower", owner="warden"))
    m.location("tower").chase = [leg.model_copy(deep=True) for leg in WAY]
    m.events.append(Event(id="e_coil", title="藤扑了出来", when="flag:gears_opened", text="门一开，藤就扑向离它最近的人。",
                          fight=["coil"], surprise="party"))
    return m


def test_the_schema_tells_the_import_models_what_a_fight_reads():
    schema = json.dumps(Module.model_json_schema(), ensure_ascii=False)
    defs = Module.model_json_schema()["$defs"]
    creature, event, location = defs["Creature"]["properties"], defs["Event"]["properties"], defs["Location"]["properties"]
    assert "爪击 50%，伤害 1D6+DB" in creature["attacks"]["description"] and "生命和敏捷" in creature["stats"]["description"]
    assert "护甲 2" in creature["notes"]["description"] and "枪伤减半" in creature["notes"]["description"]
    assert "格斗(斗殴)" in defs["NPC"]["properties"]["stats"]["description"]
    assert "4–12 段" in location["chase"]["description"] and location["chase"]["items"]["$ref"].endswith("/Leg")
    assert "当场动手" in event["fight"]["description"] and event["surprise"]["enum"] == ["", "party", "foe"]
    assert event["chase_party"]["enum"] == ["", "逃", "追"] and "不用写" in defs["Obstacle"]["properties"]["open"]["description"]
    assert "追逐" not in defs["Mechanic"]["properties"]["plugin"]["description"] and "战斗与追逐" in schema
    assert "不写怪物的学名、真名或剧情真相" in creature["called"]["description"] and creature["called"]["default"] == ""


def test_check_reads_a_modules_fights_as_the_engine_would():
    m = armed()
    assert not graph.fighting(m)  # a module that writes it all as the rules read it
    m.creatures += [Creature(id="shade", name="影子", description="墙上的影子", stats={"力量": 50},
                             attacks=["扑咬 50，伤害 1D6", "闪避 40%", "触手 45%"]),
                    Creature(id="rats", name="鼠群", called="鼠群里的一团黑影", description="几十只老鼠蜂拥而出",
                             notes="成群，不追")]  # a swarm needs no sheet
    m.npc("preacher").stats = {"射击(手枪)": 40}
    m.location("square").chase = WAY[:3]
    m.location("office").chase = [*WAY[:3], Leg(name="钟绳", obstacle=Obstacle(kind="barrier", what="垂下的钟绳", skill="爬绳", harm="一点"))]
    m.events.append(Event(id="e_bad", title="乱写的", when="flag:gears_opened", text="", fight=["nobody"], chase=["warden"], ending="toll"))
    found = [str(i) for i in graph.fighting(m)]
    assert any("shade 的 stats 缺生命、敏捷" in x for x in found) and any("shade 没写 called" in x and "「影子」" in x for x in found)
    assert any("rats 的 called「鼠群里的一团黑影」带着它的名字" in x for x in found)  # what it is, said to the players
    assert not any("rats" in x and "called" not in x for x in found)
    assert any("「扑咬 50，伤害 1D6」读不出命中率" in x for x in found) and any("闪避不是攻击" in x for x in found)
    assert any("「触手 45%」没写伤害" in x for x in found)
    assert any("preacher 会射击(手枪)" in x for x in found) and not any("NPC warden" in x for x in found)  # his wrench is no gun, but he has no gun skill
    assert any("square 的追逐路线有 3 段" in x for x in found) and any("「爬绳」，不是技能或属性名" in x for x in found)
    assert any("伤害「一点」" in x for x in found)
    errors = [str(i) for i in graph.audit(m) if i.level == "error"]
    assert errors == ["[错误] 事件 e_bad 的 fight 里的 nobody 不是 NPC 或怪物"]  # an unknown id is wrong; the rest only warned of
    assert any("同时写了 fight 和 chase" in x for x in found) and any("直接进入结局" in x for x in found)
    for folder in [EXAMPLE, *sorted(p.parent for p in paths.MODULES.glob("*/module.yaml"))]:
        assert not [i for i in graph.fighting(Module.load(folder)) if i.level == "error"], folder.name


def test_the_skill_package_carries_the_sheets_the_routes_and_the_scripted_attacks(tmp_path):
    root = compile_module(armed(), tmp_path)
    beasts = (root / "references" / "creatures.md").read_text(encoding="utf-8")
    assert "## 齿轮背后的藤（`coil`）" in beasts and "生命 9，敏捷 60" in beasts and "缠绕 45%（格斗(斗殴)，伤害 1D4+DB）" in beasts
    assert "调查员认出它以前叫它「缠在齿轮上的东西」" in beasts
    assert "护甲 2（每次受伤先减掉）" in beasts and "枪伤减半" in beasts and "每轮 2 次攻击" in beasts and "理智：1/1D6" in beasts
    assert "AutoKeeper MCP 服务器的战斗与追逐工具" in beasts
    tower = (root / "references" / "locations" / "tower.md").read_text(encoding="utf-8")
    assert "## 追逐路线" in tower and "2. **又窄又陡的楼梯**（⚠ 陡窄的楼梯，敏捷，失败受 1D3 伤）" in tower
    assert "4. **钟楼大门**（▦ 沉重的木门，力量，耐久 15）" in tower
    assert "## 追逐路线" not in (root / "references" / "locations" / "square.md").read_text(encoding="utf-8")
    warden = (root / "references" / "npcs" / "warden.md").read_text(encoding="utf-8")
    assert "## 打起来时（引擎读到的）" in warden and "棍棒 45%" in warden  # the wrench he holds is a club, at his fighting
    assert "当场打起来：`coil`（调查员被偷袭）" in (root / "references" / "events.md").read_text(encoding="utf-8")
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "## 战斗与追逐" in skill and "references/creatures.md" in skill and "`coc7-combat`" in skill and "`tower` 钟楼" in skill
    assert skills_ref.validate(root) == []
    plain = compile_module(Module.load(EXAMPLE), tmp_path / "plain")  # the format example: a route and a warden who fights, no creature
    assert not (plain / "references" / "creatures.md").exists() and "## 战斗与追逐" in (plain / "SKILL.md").read_text(encoding="utf-8")
    route = (plain / "references" / "locations" / "office.md").read_text(encoding="utf-8")
    assert "2. **报社的楼梯**（`office_stairs`，在 `office`）：楼梯上也堆着一捆捆旧报（⚠ 堆着报纸的楼梯：冲下去〔敏捷〕／顺着扶手滑下去〔攀爬〕，失败受 1D3 伤）" in route
    assert "（▦ 锁上的大门：撬锁〔锁匠，成功后打开了，后面的人直接过〕／撞开〔力量，成功后打开了，后面的人直接过〕，耐久 15）" in route
    assert "4. **去广场的街**（`lamp_street`）：路灯一盏接一盏；岔到 `back_alley`" in route and "6. **报社后巷**（`back_alley`）" in route
    assert route.count("；🚪 出口\n") == 2
    warden = (plain / "references" / "npcs" / "warden.md").read_text(encoding="utf-8")
    assert "调查员认出他以前叫他「楼梯口打盹的老人」" in warden and "原文的攻击：格斗(斗殴) 45%，伤害 1D3+DB" in warden


def _unready(tmp_path):
    """The example with a creature half written up and an event whose words tell of an attack."""
    folder = tmp_path / "example"
    shutil.copytree(EXAMPLE, folder)
    people = folder / "people.yaml"
    people.write_text(yamledit.append(people.read_text(encoding="utf-8"), "creatures", [
        {"id": "coil", "name": "齿轮背后的藤", "description": "缠在齿轮背后、还在动的东西", "stats": {"力量": 70},
         "attacks": ["缠绕 45"], "notes": "怕火"}]), encoding="utf-8")
    story = folder / "story.yaml"
    story.write_text(yamledit.append(story.read_text(encoding="utf-8"), "events", [
        {"id": "e_coil", "title": "藤扑了出来", "when": "flag:gears_opened", "text": "齿轮间的门一开，藤就扑向离它最近的人。"}]),
        encoding="utf-8")
    main = folder / "module.yaml"
    main.write_text(yamledit.put_key(main.read_text(encoding="utf-8"), "flags", "gears_opened", "齿轮间的门被打开了"), encoding="utf-8")
    return folder


LEGS = [leg.model_dump(mode="json", exclude_defaults=True) for leg in WAY]
# The way down from the tower, as the pass draws it: ids, places, a branch, an exit.
TOWER = [{"id": "gears", "name": "齿轮间门口", "look": "咔哒声就在头顶", "place": "tower", "links": ["side_alley", "nowhere", "钟绳"]},
          {"id": "tower_stairs", "name": "又窄又陡的楼梯", "place": "tower",
           "obstacle": {"kind": "hazard", "what": "堆着报纸的楼梯", "skill": "敏捷", "hp": 5,
                        "methods": [{"id": "run", "label": "冲下去", "skill": "敏捷", "opens": True}, {"id": "rope", "label": "顺绳滑下", "skill": "爬绳"}]}},
          {"id": "tower_door", "name": "钟楼大门", "place": "tower",
           "obstacle": {"kind": "barrier", "what": "闩着的木门", "skill": "力量", "hp": 5,
                        "methods": [{"id": "lift", "label": "拔掉门闩", "skill": "力量", "opens": True}]}},
          {"id": "to_square", "name": "台阶下", "place": "street_nowhere"},
          {"id": "square_edge", "name": "广场边上", "place": "square", "exit": True},
          {"id": "side_alley", "name": "钟楼边的窄巷", "look": "墙根挤着木箱", "exit": True},
          {"name": "钟绳", "obstacle": {"kind": "barrier", "what": "垂下的钟绳", "skill": "爬绳"}}]
ANSWER = {
    "creatures": [{"creature": "coil", "called": "缠在齿轮上的东西", "stats": {"力量": 99, "生命": 9, "敏捷": 60, "体型": "+1D4"},  # 力量 is the module's: kept
                   "attacks": ["缠绕 45%，伤害 1D4+DB", "抽打 30"], "notes": "护甲 2，枪伤减半"},
                  {"creature": "nobody", "stats": {"生命": 5}}],
    "npcs": [{"npc": "warden", "stats": {"力量": 60, "敏捷": 50, "生命": 11, "格斗(斗殴)": 45, "射击(手枪)": 40}, "weapon": ".38 左轮手枪"},
             {"npc": "preacher", "stats": {}}, {"npc": "ghost", "stats": {"生命": 1}}],
    "routes": [{"location": "tower", "legs": TOWER}, {"location": "office", "legs": LEGS},  # the office has one already, with ids
               {"location": "nowhere", "legs": LEGS}, {"location": "square", "legs": LEGS[:3]}],
    "triggers": [{"event": "e_coil", "fight": ["coil", "nobody"], "surprise": "party"},
                 {"event": "e_finale", "fight": ["warden"]},  # it ends the game: no fight begins in it
                 {"event": "missing", "fight": ["coil"]}],
}


async def test_the_combat_pass_fills_what_the_rules_cannot_read_and_leaves_the_rest(tmp_path, monkeypatch):
    folder = _unready(tmp_path)
    m = Module.load(folder)
    assert adapt.wanted(m, "combat") and "coil" in adapt._material(m, "combat") and "e_coil" in adapt._material(m, "combat")
    asked = []

    async def ask(cfg, name, m, **kw):
        asked.append(name)
        return adapt.Combat.model_validate(ANSWER)

    monkeypatch.setattr(adapt, "_ask", ask)
    before = {p.name: p.read_text(encoding="utf-8") for p in folder.glob("*.yaml")}
    results = await adapt.adapt(folder, passes=("combat",), cfg=SimpleNamespace(), log=lambda _: None)
    assert asked == ["combat"] and results[0].written and not results[0].problems
    m = Module.load(folder)
    coil = m.creatures[0]
    assert coil.called == "缠在齿轮上的东西" and coil.stats == {"力量": 70, "生命": 9, "敏捷": 60}  # added to, never changed; a bonus is no number
    assert coil.attacks == ["缠绕 45%，伤害 1D4+DB"] and coil.notes == "怕火 护甲 2，枪伤减半"  # the line with no chance in it goes
    assert m.npc("warden").stats["射击(手枪)"] == 40 and not m.npc("preacher").stats
    gun = next(i for i in m.items if i.owner == "warden")
    assert (gun.id, gun.name, gun.where) == ("warden_weapon", ".38 左轮手枪", "tower")
    way = m.location("tower").chase
    assert [leg.name for leg in way] == [x["name"] for x in TOWER] and way[-1].obstacle is None  # 爬绳 is no skill
    assert way[0].links == ["side_alley", "钟绳"] and way[3].place == ""  # a link to no leg, a place the module lacks: left out
    stairs = way[1].obstacle
    assert stairs.hp is None and [(x.id, x.opens) for x in stairs.methods] == [("run", False)]  # a hazard breaks and opens nothing
    assert way[2].obstacle.hp == 5 and way[2].obstacle.methods[0].opens
    assert [leg.name for leg in m.location("office").chase] == [leg.name for leg in Module.load(EXAMPLE).location("office").chase]
    assert not m.location("square").chase and "open: " not in (folder / "places.yaml").read_text(encoding="utf-8")
    event = next(e for e in m.events if e.id == "e_coil")
    assert (event.fight, event.surprise) == (["coil"], "party") and not next(e for e in m.events if e.id == "e_finale").fight
    assert not [i for i in graph.audit(m) if i.level == "error"] and not graph.fighting(m)
    assert not adapt.wanted(m, "combat")  # run again: nothing left
    bare = m.model_copy(update={"creatures": [coil.model_copy(update={"called": ""})]})
    assert adapt.wanted(bare, "combat")  # the players' word for it is still to write
    told = adapt.Combat.model_validate({"creatures": [{"creature": "coil", "called": "齿轮背后的藤"}]})
    assert not adapt._armed(bare, told)  # one that says what it is goes unwritten
    for name, text in before.items():  # what was there is still there, but the fields written whole again
        now = (folder / name).read_text(encoding="utf-8").split("\n")
        assert all(line in now for line in text.split("\n") if line.strip() not in ("stats:", "attacks:", "- 缠绕 45", "notes: 怕火")
                   and "力量: 70" not in line), name


def test_the_mechanics_pass_leaves_fights_and_chases_to_the_engine():
    task = adapt.ASK["mechanics"][0]
    assert "追逐、特殊的检定" not in task and "战斗和追逐引擎按规则自己结算，不列" in task
    from autokeeper.module import importer

    assert "降神会、调查板" in importer.BRIEF and "追逐、调查板" not in importer.BRIEF and "4–12 段路" in importer.BRIEF


async def test_a_players_copy_at_a_lan_table_keeps_the_routes_back(fake):
    from autokeeper.net.hub import table

    from .test_engine import make

    m = armed()
    game, _, _ = make(m, fake, {}, location="tower")
    game.state.visited.append("tower")
    copy = table(m, game.state)
    assert all(loc["chase"] == [] for loc in copy["locations"]) and copy["creatures"] == []
    await game.close()


def test_a_second_answer_for_the_same_one_is_not_written_twice():
    """A field written twice would not land (yamledit.put refuses a field that has a value): the
    whole pass would be taken back. The first answer for each creature, NPC and event stands."""
    m = Module.load(EXAMPLE)
    m.creatures.append(Creature(id="coil", name="齿轮背后的藤", description="缠在齿轮背后的东西"))
    m.events.append(Event(id="e_coil", title="藤扑了出来", when="flag:x", text=""))
    out = adapt.Combat.model_validate({
        "creatures": [{"creature": "coil", "called": "缠在齿轮上的东西"}, {"creature": "coil", "called": "黑影"}],
        "npcs": [{"npc": "warden", "stats": {"生命": 9}}, {"npc": "warden", "stats": {"生命": 7}}],
        "triggers": [{"event": "e_coil", "fight": ["coil"]}, {"event": "e_coil", "fight": ["warden"]}]})
    assert adapt._armed(m, out) == [("put", "creatures", "coil", "called", "缠在齿轮上的东西"), ("put", "npcs", "warden", "stats", {"生命": 9}),
                                    ("put", "events", "e_coil", "fight", ["coil"])]


# -- who a sheet is, how the routes join, what a fight decides, which first sights are paid (0.0.4) --------
def test_check_reads_identity_routes_stakes_and_sights():
    m = armed()
    m.npc("warden").called = "老守钟人在打盹"  # his name, to the players
    m.npc("warden").attacks = ["扳手 45"]
    m.creatures += [Creature(id="warden_beast", name="守钟人的另一张脸", called="齿轮间里的东西", description="", npc="warden", stats={"生命": 20, "敏捷": 40},
                             attacks=["抓 40%，伤害 1D6"]),
                    Creature(id="warden_twin", name="守钟人的影子", called="影子", description="", npc="warden", stats={"生命": 11, "敏捷": 40},
                             attacks=["抓 40%，伤害 1D6"]),
                    Creature(id="ghost", name="鬼", called="白影", description="", npc="nobody", stats={"生命": 5, "敏捷": 40}, attacks=["抓 40%，伤害 1D3"],
                             pursues=True, notes="不追")]
    m.location("square").chase = [Leg(id="Plaza", name="广场"), Leg(id="lane", name="小巷", place="nowhere", links=["tower_top", "lane"]),
                                   Leg(id="lane", name="又一条小巷",
                                       obstacle=Obstacle(kind="hazard", what="碎玻璃", skill="闪避", harm="1D3点", hp=5,
                                                         methods=[Method(id="Jump", label="跳过去", skill="飞", opens=True)])),
                                   Leg(id="gate", name="镇口", exit=True), Leg(id="lost", name="没人去的角落")]
    m.location("office").chase = [Leg(id="desk", name="写字台"), Leg(id="hall", name="走廊"),
                                  Leg(id="door", name="报社大门", obstacle=Obstacle(kind="barrier", what="锁着的门", skill="锁匠")),
                                  Leg(id="gate", name="镇口外", exit=True)]
    e = next(x for x in m.events if x.id == "e_coil")
    e.sanity = "0/1D3"
    e.stakes = [Stake(when="down:coil | ended:won", flags=["gears_opened"]), Stake(when="dead:nobody & ended:draw", flags=["missing"]),
                Stake(when="escaped:players", flags=[])]
    e.sights = ["warden"]
    m.events.append(Event(id="e_quiet", title="安静", when="flag:x", text="", stakes=[Stake(when="ended:won", flags=["gears_opened"])],
                          sights=["coil"], fight=["warden", "warden_beast"]))
    found = [str(i) for i in graph.fighting(m)]
    errors = [str(i) for i in graph.fighting(m) if i.level == "error"]
    assert "[错误] 怪物 ghost 的 npc nobody 不是模组里的 NPC" in errors
    assert "[错误] NPC warden 有两张怪物卡（warden_beast、warden_twin）：一个人只有一种打起来的样子" in errors
    assert any("warden_beast 是 NPC warden 打起来的样子，两张卡的生命不一样（20、11）" in x for x in found)
    assert any("ghost 写了 pursues: true，notes 里却写着不追" in x for x in found)
    assert any("NPC warden 的 called「老守钟人在打盹」带着他的名字「老守钟人」" in x for x in found)
    assert any("NPC warden 的攻击「扳手 45」读不出命中率" in x for x in found)
    assert "[错误] 地点 square 的追逐路线里有两段都叫 lane" in errors
    assert "[错误] 地点 square 追逐路线第 2 段「小巷」的 place nowhere 不是模组里的地点" in errors
    assert any("「小巷」的 links 里的「tower_top」不是哪条路线里的段" in x for x in errors) and any("links 连到了它自己" in x for x in found)
    assert any("id「Plaza」只用小写字母" in x for x in found) and any("「没人去的角落」从起点跑不到" in x for x in found)
    assert any("的碎玻璃的伤害「1D3点」用不了" in x for x in found) and any("办法「跳过去」用「飞」，不是技能或属性名" in x for x in found)
    assert any("办法 id「Jump」" in x for x in found) and any("办法「跳过去」的 opens 不起作用" in x for x in found)
    assert any("碎玻璃是险阻，hp 不起作用" in x for x in found)
    assert any("锁着的门是路障，没写 hp" in x and "坚固的外门 15" in x for x in found)
    assert any("「镇口外」和地点 square 的同一段 gate 写得不一样" in x for x in found)
    assert "[错误] 事件 e_coil 的第 2 条胜负后果 dead:nobody 不对：dead 写这场遭遇里的 NPC、怪物、预设调查员的 id，或 players（调查员一方）" in errors
    assert any("e_coil 的第 2 条胜负后果 ended:draw 不对：ended 只能是 won" in x for x in errors)
    assert "[错误] 事件 e_coil 的第 2 条胜负后果设了未声明的标记 missing" in errors
    assert any("e_coil 的第 3 条胜负后果没有 flags" in x for x in found)
    assert "[错误] 事件 e_coil 的 sights 里的 warden 不是怪物" in errors
    assert any("e_coil 有自己的理智检定（0/1D3），打起来的 coil 照面还要再扣一次" in x for x in found)
    assert any("e_quiet 写了 sights，自己却没有 sanity" in x for x in found)
    assert any("e_quiet 的 fight 同时写了 warden 和他打起来的样子 warden_beast" in x for x in found)
    assert not any("e_quiet 写了 stakes，却没有" in x for x in found)  # it begins a fight: its stakes count
    assert not any("第 1 条胜负后果" in x for x in found)  # an encounter's atoms and the world's, all known


def test_the_stakes_speak_the_condition_language_and_the_encounters():
    m = Module.load(paths.find_module("zhuishuren"))
    assert graph.stake_problems(m, "ended:fled | escaped:players & flag:recognized") == []
    assert graph.stake_problems(m, "down:douglas_ghoul & at:thomas:jinboer_jia & gone:douglas") == []
    assert graph.stake_problems(m, "D1 22:00") == ["D1 22:00 不对：胜负后果看遭遇怎么收场，不写时间"]
    assert graph.stake_problems(m, "caught:brennan | surrendered:hale") == []  # the pregens are the investigators
    assert graph.stake_problems(m, "won") and graph.stake_problems(m, "at:douglas:nowhere") and graph.stake_problems(m, " ")


def _site(m: Module) -> nx.Graph:
    g = nx.Graph()
    names = {name: graph.leg_key(leg) for name, leg in graph.ways(m).items()}
    for loc in m.locations:
        g.add_edges_from(graph.route_edges(loc.chase, names))
    return g


def test_zhuishuren_is_one_douglas_and_one_graph_from_the_house_to_the_tunnels():
    """The reviewer's findings, re-authored by hand from the module's own text: Douglas is the ghoul
    (one life), the routes join the house, the graveyard, the tomb and the tunnels into one graph
    whose legs lie in real places, the horde's fight says what fleeing and fighting bring, and no
    first sight is charged on top of the roll the module asks for it."""
    m = Module.load(paths.find_module("zhuishuren"))
    ghoul, horde = (next(c for c in m.creatures if c.id == x) for x in ("douglas_ghoul", "ghouls"))
    assert ghoul.npc == "douglas" and ghoul.pursues is False and horde.pursues is False
    assert m.npc("douglas").called == "佝偻的人影" and "道格拉斯" not in m.npc("douglas").called
    # The module's own sheet: one attack a round (its note on the simplification kept); the horde's from the rulebook's ghoul.
    assert attacks_per_round(ghoul.attacks, ghoul.notes) == 1 and "正常食尸鬼每回合 3 次" in ghoul.attacks[0]
    assert attacks_per_round(horde.attacks, horde.notes) == 3 and parse_attack(horde.attacks[1]).damage == "1D6+DB"
    assert traits(horde.notes)["guns_halved"] and traits(horde.notes)["db"] == "+1D4" and not horde.stats  # still a swarm
    assert [x for x in m.npc("jefferson").attacks if parse_attack(x)] == m.npc("jefferson").attacks
    g = _site(m)
    legs = {graph.leg_key(leg): leg for loc in m.locations for leg in loc.chase}
    places = {x.id for x in m.locations}
    assert all(leg.id and leg.place in places for leg in legs.values())
    exits = {k for k, leg in legs.items() if leg.exit}
    assert exits == {"back_door", "street", "forks", "surface_exit"}
    for loc in ("jinboer_jia", "mudi", "lingmu", "suidao"):
        start = graph.leg_key(m.location(loc).chase[0])
        assert exits <= nx.node_connected_component(g, start), loc  # every way out, from every route's start
    assert nx.shortest_path(g, "backyard_path", "low_tunnel") == ["backyard_path", "low_wall", "shrubs", "dirt_road", "graves", "old_trees",
                                                                   "deep_graves", "tomb_mud", "tomb_door", "tunnel_mouth", "low_tunnel"]
    assert {legs[k].place for k in nx.shortest_path(g, "backyard_path", "forks")} == {"jinboer_jia", "mudi", "lingmu", "suidao"}
    wall, door = legs["low_wall"].obstacle, legs["tomb_door"].obstacle
    assert wall.kind == "hazard" and {x.skill for x in wall.methods} == {"攀爬", "跳跃"}  # a low wall slows, it never stops (p. 115)
    assert door.kind == "barrier" and door.hp == 25 and door.methods[0].opens
    events = {e.id: e for e in m.events}
    horde_event = events["e_horde"]
    assert horde_event.sights == ["ghouls"] and horde_event.fight == ["ghouls"]
    assert {f for s in horde_event.stakes for f in s.flags} == {"taken_by_ghouls", "fled_ghouls"}
    for eid in ("e_unmasked", "e_hello", "e_tunnel_maze", "e_night_visit", "e_fainted"):
        assert events[eid].sights == ["douglas_ghoul"] and events[eid].sanity, eid
    assert not graph.fighting(m)


@pytest.mark.parametrize("key", ["zhuishuren", "dongxuezhixia", "fallout3-youxierenyouyanbujin", "example"])
def test_the_bundled_modules_and_the_example_read_whole(key):
    m = Module.load(EXAMPLE if key == "example" else paths.find_module(key))
    assert [str(i) for i in graph.fighting(m)] == []
    g = _site(m)
    exits = {graph.leg_key(leg) for loc in m.locations for leg in loc.chase if leg.exit}
    for loc in m.locations:
        if loc.chase:  # every route has ids and a way out, of its own or through the routes it joins
            assert all(leg.id for leg in loc.chase) and exits & nx.node_connected_component(g, loc.chase[0].id), loc.id
            for leg in loc.chase:
                ob = leg.obstacle
                assert ob is None or (ob.methods and all(x.skill for x in ob.methods)), leg.id
                assert ob is None or ob.kind == "barrier" or ob.hp is None


def test_the_other_bundled_sheets_follow_their_pages():
    winter = Module.load(paths.find_module("dongxuezhixia"))
    erie = next(c for c in winter.creatures if c.id == "erie_entity")
    assert erie.npc == "erie" and erie.sanity == "1/1D4+1"  # the module's page: 1/1D4+1 when it shows itself
    assert winter.npc("caroline").attacks == ["格斗(斗殴) 25%，伤害 1D3"]
    stowell = Module.load(paths.find_module("fallout3-youxierenyouyanbujin"))
    shot = parse_attack(stowell.npc("stanley").attacks[1])
    assert shot.firearm and shot.skill == "射击(手枪)" and shot.damage == "1D10"
    assert parse_attack(stowell.npc("jensen").attacks[1]).firearm and stowell.npc("mccurdy").called == "衣衫褴褛的闯入者"
    attack = next(e for e in stowell.events if e.id == "e_mccurdy_attack")
    assert attack.stakes[0].when == "dead:mccurdy" and attack.stakes[0].flags == ["mccurdy_dead"]
    town = {leg.id: leg for leg in stowell.location("main_street").chase}  # the module's typical town obstacles
    assert town["roadblock"].obstacle.hp == 15 and town["fire"].obstacle.skill == "跳跃" and town["sniper"].obstacle.harm == "1D6"


def test_the_skill_package_says_who_a_sheet_is_and_what_a_fight_decides(tmp_path):
    root = compile_module(Module.load(paths.find_module("zhuishuren")), tmp_path)
    beasts = (root / "references" / "creatures.md").read_text(encoding="utf-8")
    assert "这是 NPC `douglas` 道格拉斯·金博尔打起来的样子：同一个人" in beasts and "调查员逃跑时：不追" in beasts
    assert "`e_horde` 的理智检定已经算了这一次" in beasts
    douglas = (root / "references" / "npcs" / "douglas.md").read_text(encoding="utf-8")
    assert "他打起来就是怪物 `douglas_ghoul`" in douglas and "调查员认出他以前叫他「佝偻的人影」" in douglas
    events = (root / "references" / "events.md").read_text(encoding="utf-8")
    assert "- `ended:lost | down:players` → `taken_by_ghouls`" in events and "这次理智检定就是 `ghouls` 的照面，不再另扣" in events
    house = (root / "references" / "locations" / "jinboer_jia.md").read_text(encoding="utf-8")
    assert "1. **屋后的小路**（`backyard_path`，在 `jinboer_jia`）" in house and "岔到 `back_door`" in house
    assert "（▦ 紧闭的石门：推开石门〔力量，成功后打开了，后面的人直接过〕，耐久 25）" in house
    assert skills_ref.validate(root) == []


async def test_the_combat_pass_writes_who_a_sheet_is_the_stakes_and_the_sights(tmp_path, monkeypatch):
    folder = tmp_path / "zhuishuren"
    shutil.copytree(paths.find_module("zhuishuren"), folder, ignore=shutil.ignore_patterns("source"))
    for name, cut in (("people.yaml", ("    npc: douglas", "    pursues: false", "    called: 佝偻的人影")),
                      ("story.yaml", ("    sights: [ghouls]",))):
        text = (folder / name).read_text(encoding="utf-8").split("\n")
        (folder / name).write_text("\n".join(x for x in text if not x.startswith(cut)), encoding="utf-8")
    m = Module.load(folder)
    assert adapt._forms(m) == [("douglas_ghoul", "douglas")] and adapt._twice(m) == ["e_horde"] and adapt.wanted(m, "combat")
    material = adapt._material(m, "combat")
    assert "名字里有 NPC douglas 的名字，是他变的就写 npc" in material and "有自己的理智检定、打起来的怪物照面还要再扣一次的事件" in material
    assert "段 backyard_path、low_wall" in material and "forks（出口）" not in material.split("- jinboer_jia")[1].split("\n")[0]
    answer = adapt.Combat.model_validate({
        "creatures": [{"creature": "douglas_ghoul", "npc": "douglas", "pursues": False}, {"creature": "ghouls", "npc": "thomas"}],  # the horde is no one
        "npcs": [{"npc": "douglas", "called": "佝偻的人影"}, {"npc": "thomas", "called": "托马斯"},  # his name, to the players: left out
                 {"npc": "odell", "attacks": ["手提包 20"]}],  # no chance to hit
        "triggers": [{"event": "e_horde", "sights": ["ghouls", "nobody"],
                      "stakes": [{"when": "ended:fled", "flags": ["fled_ghouls"]}]},  # it has its stakes: kept as they are
                     {"event": "e_unmasked", "sights": ["douglas_ghoul"]}, {"event": "e_figure", "sights": ["douglas_ghoul"]},  # no roll: none
                     {"event": "e_jailed", "stakes": [{"when": "ended:won", "flags": ["jailed"]}]}]})  # no fight: no stakes
    async def ask(cfg, name, m, **kw):
        return answer

    monkeypatch.setattr(adapt, "_ask", ask)
    results = await adapt.adapt(folder, passes=("combat",), cfg=SimpleNamespace(), log=lambda _: None)
    assert results[0].written and not results[0].problems
    m = Module.load(folder)
    ghoul, horde = m.creatures
    assert (ghoul.npc, ghoul.pursues, horde.npc) == ("douglas", False, "") and m.npc("douglas").called == "佝偻的人影"
    assert not m.npc("thomas").called and not m.npc("odell").attacks
    events = {e.id: e for e in m.events}
    assert events["e_horde"].sights == ["ghouls"] and len(events["e_horde"].stakes) == 2
    assert not events["e_figure"].sights and not events["e_jailed"].stakes
    assert not adapt._forms(m) and not adapt._twice(m) and not graph.fighting(m)


def test_an_old_route_is_drawn_again_and_a_new_one_is_cut_to_what_runs():
    m = Module.load(EXAMPLE)
    m.location("tower").chase = [leg.model_copy(deep=True) for leg in WAY]  # the old way: no ids
    assert adapt._unlinked(m) == ["tower"] and adapt.wanted(m, "combat")
    legs = [Leg(id="square", name="广场", place="square"), *(Leg(id=f"t{i}", name=f"第{i}段") for i in range(1, 4)),
            Leg(id="t1", name="重了的 id", obstacle=Obstacle(kind="barrier", what="门", skill="力量", harm="1D3点"))]
    out = adapt._armed(m, adapt.Combat(routes=[adapt.Route(location="tower", legs=legs), adapt.Route(location="office", legs=legs)]))
    assert [(e[0], e[2]) for e in out] == [("replace", "tower")]  # the office has its own, with ids
    written = out[0][4]
    assert "id" not in written[0] and "id" not in written[4] and "obstacle" not in written[4]  # a place's id, a second t1, a harm that is no dice


def test_the_prompts_ask_for_the_new_fields():
    from autokeeper.module import generator, importer

    task = adapt.ASK["combat"][0]
    for word in ("npc 写那个 NPC 的 id", "pursues", "called 写那时的叫法", "exit: true", "links", "hazard 险阻", "barrier 路障", "opens: true",
                 "坚固的外门 15", "stakes", "sights", "ended:won"):
        assert word in task, word
    for word in ("npc 写那个 NPC 的 id", "pursues", "exit: true", "methods", "hp", "stakes", "sights", "called 写那时的叫法"):
        assert word in importer.FIGHTS, word
    for word in ("写 npc", "pursues", "called", "links", "exit", "methods", "hp", "stakes", "sights"):
        assert word in generator.GENERATE, word
    sheet = adapt.CreatureSheet.model_json_schema()["properties"]
    assert {"npc", "pursues"} <= set(sheet) and {"called", "attacks"} <= set(adapt.NpcSheet.model_json_schema()["properties"])
    trigger = adapt.Trigger.model_json_schema()
    assert {"stakes", "sights"} <= set(trigger["properties"])
