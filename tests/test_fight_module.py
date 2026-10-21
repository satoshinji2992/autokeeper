"""What a fight and a chase read of a module, from the compile side: the schema the import models
fill, the checks of `autokeeper check`, the skill package for external agents, the compile-time pass
that fills what the rules cannot read (module/adapt.py), and the players' copy at a LAN table."""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import skills_ref

from autokeeper import paths
from autokeeper.module import adapt, graph, yamledit
from autokeeper.module.compiler import compile_module
from autokeeper.module.schema import Creature, Event, Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.chase import Leg, Obstacle

WAY = [Leg(name="齿轮间门口", look="咔哒声就在头顶"),
       Leg(name="又窄又陡的楼梯", obstacle=Obstacle(kind="hazard", what="陡窄的楼梯", skill="敏捷", harm="1D3")),
       Leg(name="楼梯口", look="守钟人打盹的椅子翻倒在地"),
       Leg(name="钟楼大门", obstacle=Obstacle(kind="barrier", what="沉重的木门", skill="力量")),
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
    assert "4. **钟楼大门**（▦ 沉重的木门，力量）" in tower
    assert "## 追逐路线" not in (root / "references" / "locations" / "square.md").read_text(encoding="utf-8")
    warden = (root / "references" / "npcs" / "warden.md").read_text(encoding="utf-8")
    assert "## 打起来时（引擎读到的）" in warden and "棍棒 45%" in warden  # the wrench he holds is a club, at his fighting
    assert "当场打起来：`coil`（调查员被偷袭）" in (root / "references" / "events.md").read_text(encoding="utf-8")
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "## 战斗与追逐" in skill and "references/creatures.md" in skill and "`coc7-combat`" in skill and "`tower` 钟楼" in skill
    assert skills_ref.validate(root) == []
    plain = compile_module(Module.load(EXAMPLE), tmp_path / "plain")
    assert not (plain / "references" / "creatures.md").exists() and "## 战斗与追逐" not in (plain / "SKILL.md").read_text(encoding="utf-8")


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
ANSWER = {
    "creatures": [{"creature": "coil", "called": "缠在齿轮上的东西", "stats": {"力量": 99, "生命": 9, "敏捷": 60, "体型": "+1D4"},  # 力量 is the module's: kept
                   "attacks": ["缠绕 45%，伤害 1D4+DB", "抽打 30"], "notes": "护甲 2，枪伤减半"},
                  {"creature": "nobody", "stats": {"生命": 5}}],
    "npcs": [{"npc": "warden", "stats": {"力量": 60, "敏捷": 50, "生命": 11, "格斗(斗殴)": 45, "射击(手枪)": 40}, "weapon": ".38 左轮手枪"},
             {"npc": "preacher", "stats": {}}, {"npc": "ghost", "stats": {"生命": 1}}],
    "routes": [{"location": "tower", "legs": [*LEGS[:5], {"name": "钟绳", "obstacle": {"kind": "barrier", "what": "垂下的钟绳", "skill": "爬绳"}}]},
               {"location": "nowhere", "legs": LEGS}, {"location": "office", "legs": LEGS[:3]}],
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
    assert [leg.name for leg in way] == [leg.name for leg in WAY[:5]] + ["钟绳"] and way[-1].obstacle is None  # 爬绳 is no skill
    assert not m.location("office").chase and "open" not in (folder / "places.yaml").read_text(encoding="utf-8")
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
