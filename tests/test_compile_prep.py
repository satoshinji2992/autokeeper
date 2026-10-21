"""Compile-time preparation held to the module's own conditions (the review of 2026-10-20,
docs/compilation-review-2026-10-20.md): what the adapter reads, in what order it writes, what it
may not hand over, what the skill package and the keeper's book carry, and how the prepared
plans trigger in play. The six cases of the review's last section are the `test_case_*` tests."""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest
import yaml

from autokeeper import paths
from autokeeper.agents.cast import _book
from autokeeper.engine.game import Game
from autokeeper.module import adapt, compiler, conditions, yamledit
from autokeeper.module.graph import audit
from autokeeper.module.schema import Agenda, Mechanic, Module, Move
from autokeeper.world import adapters, agents, context, scheduler
from autokeeper.world.store import World

BUNDLED = ["zhuishuren", "dongxuezhixia", "fallout3-youxierenyouyanbujin"]


def world(m: Module, where: str, minutes: int | None = None) -> World:
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.location = where
    if minutes is not None:
        st.minutes = minutes
    w = World(m, st)
    for op in agents.marked(w):
        result = w.apply(op)
        assert result.ok, result.problems
    return w


def copy(key: str, to) -> object:
    """A bundled module in a test's own folder. Its source/ is a link to the original pages on
    this machine: the copy gets a folder of its own with the text read from there, never the link."""
    folder = to / key
    shutil.copytree(paths.MODULES / key, folder, ignore=shutil.ignore_patterns("source"))
    if text := adapt.original(paths.MODULES / key):
        (folder / "source").mkdir()
        (folder / "source" / "source.md").write_text(text, encoding="utf-8")
    return folder


def with_plan(m: Module, npc: str, *steps: Move) -> Module:
    m.agendas = [a for a in m.agendas if a.npc != npc] + [Agenda(npc=npc, goals=["测试"], steps=list(steps))]
    return m


@pytest.fixture
def judge(monkeypatch):
    """The review's one call (adapt.verify), scripted: flags what the test puts in `flags`."""
    calls = []

    async def fake(cfg, material):
        calls.append(material)
        return adapt.Verdict(flags=[adapt.Flag(ref=ref, problem="脚本：绕过了条件") for ref in fake.flags])

    fake.flags = []
    fake.calls = calls
    monkeypatch.setattr(adapt, "_judge", fake)
    return fake


# -- 1. what the adapter reads ------------------------------------------------------------------
@pytest.mark.parametrize("key", BUNDLED)
def test_the_adapter_reads_every_condition_and_the_original(key):
    folder = paths.MODULES / key
    m = Module.load(folder)
    m.locations[-1].keeper += "\n标记_局部检定后果不能丢"
    checked = next(c for c in m.clues if c.check)
    checked.check.skill = "标记_专用检定"
    text = adapt.book(m)
    assert "标记_局部检定后果不能丢" in text and "标记_专用检定" in text
    assert all(c.text.strip() in text for c in m.clues)  # every clue's words, whole
    for n in m.npcs:  # every social gate, with the clue it keeps
        for cid, gate in n.gates.items():
            assert f"{cid}（要 {gate.skill}（{gate.difficulty}））" in text
    for c in m.clues:
        if c.requires:
            assert f"前置 {'、'.join(c.requires)}" in text
    assert m.keeper_notes.strip() in text  # no longer cut at 1200 characters
    if not (folder / "source").exists():  # the originals stay on the importer's machine, never in the repository
        pytest.skip("这台机器上没有模组原文（source/）")
    source = adapt.original(folder)
    assert len(source) > 10000 and "追书人" in adapt.original(paths.MODULES / "zhuishuren")


def test_a_long_original_keeps_what_bears_on_the_call_and_its_conditions(tmp_path):
    (tmp_path / "source").mkdir()
    filler = ["这一段只写街景和天气，没有别的。" * 40 for _ in range(30)]
    parts = [*filler[:10], "托马斯·金博尔知道叔叔写日记，他只知道日记在书房里。", *filler[10:20],
             "侦查检定成功才在旧书桌里找到日记；孤注一掷失败会打碎花瓶。", *filler[20:]]
    (tmp_path / "source" / "source.md").write_text("\n\n".join(parts), encoding="utf-8")
    text = adapt.original(tmp_path, ["托马斯·金博尔"], limit=2000)
    assert "知道叔叔写日记" in text and "侦查检定成功才在旧书桌里找到日记" in text
    assert text.index("知道叔叔写日记") < text.index("侦查检定成功")  # in the original's order
    assert len(text) < 2200


# -- 2. in what order it writes -----------------------------------------------------------------
async def test_the_plans_read_the_people_as_just_filled_in(tmp_path, monkeypatch, judge):
    folder = tmp_path / "module"
    shutil.copytree(paths.EXAMPLE, folder)
    people = folder / "people.yaml"
    data = yaml.safe_load(people.read_text(encoding="utf-8"))
    next(n for n in data["npcs"] if n["id"] == "preacher")["knows"] = []
    people.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    seen = {}

    async def ask(cfg, name, m, **_):
        seen[name] = list(m.npc("preacher").knows)
        if name == "npcs":
            return adapt.NpcFills(npcs=[adapt.NpcFill(npc="preacher", knows=[
                adapt.Known(text="钟楼每晚多敲一下", how="住在广场边，夜夜听见", source="square"),
                adapt.Known(text="守钟人杀了人", how="猜的", source="补写")])])  # not the module's: left out
        return adapt.Agendas(agendas=[Agenda(npc="preacher", steps=[Move(id="wait", text="在广场等人问起钟声", meet=True, once=False)])])

    monkeypatch.setattr(adapt, "_ask", ask)
    result = await adapt.adapt(folder, passes=("agendas", "npcs"), cfg=SimpleNamespace(), log=lambda _: None)
    assert all(not r.problems for r in result)
    assert seen["agendas"] == ["钟楼每晚多敲一下（住在广场边，夜夜听见）"]  # the fill landed before the plans were asked
    assert Module.load(folder).npc("preacher").knows == seen["agendas"]


def test_the_waves_follow_what_each_pass_reads():
    order: list[list[str]] = []
    left = [x for x in adapt.PASSES if x != "verify"]
    while left:
        wave = [x for x in left if not set(adapt.AFTER.get(x, ())) & set(left)]
        left = [x for x in left if x not in wave]
        order.append(wave)
    at = {name: i for i, wave in enumerate(order) for name in wave}
    assert at["npcs"] < at["relations"] < at["agendas"] < at["meet"]
    assert at["clueweb"] < at["foreshadow"] and at["history"] < at["agendas"] and at["endings"] < at["agendas"]
    assert len(order) == 4 and {"npcs", "clueweb", "endings", "mechanics", "prelude", "combat"} == set(order[0])


# -- 3. and 4. what it may not hand over --------------------------------------------------------
def test_a_meeting_reaction_that_hands_over_a_clue_behind_a_roll_is_found():
    m = Module.load(paths.MODULES / "zhuishuren")
    bad = Move(id="bad_diary", text="开门就把叔叔的日记交给调查员，直接解释地下隧道。", meet=True)
    with_plan(m, "thomas", bad)
    assert not [i for i in audit(m) if i.level == "error"]  # the structural checks see nothing wrong
    found = [f for f in conditions.findings(m) if f.ref.startswith("agendas:thomas:")]
    assert [f.ref for f in found if f.level == "warning"] == ["agendas:thomas:bad_diary"]  # found by words: a hint, not a verdict
    assert "c_diary" in found[0].text and "侦查" in found[0].text
    for fixed in (bad.model_copy(update={"ruling": True}), bad.model_copy(update={"when": "knows:players:clue:c_diary"}),
                  Move(id="shelf", text="被问到丢书时领人看书架上的空档，书房里别的东西要调查员自己搜。", meet=True)):
        with_plan(m, "thomas", fixed)
        assert not [f for f in conditions.findings(m) if f.ref.startswith("agendas:thomas:")], fixed


def test_a_step_on_the_investigators_progress_must_say_how_the_npc_comes_to_it():
    m = Module.load(paths.MODULES / "fallout3-youxierenyouyanbujin")
    loose = Move(id="arrest", text="带人上门抓人，关进警局后面的牢房。", when="flag:learned_telescope", meet=False)
    with_plan(m, "stanley", loose)
    assert "agendas:stanley:arrest" in [f.ref for f in conditions.findings(m) if f.level == "warning"]
    with_plan(m, "stanley", loose.model_copy(update={"only_if": "有手下或有福者向他报告、或他亲眼看到调查员在追查教授和望远镜"}))
    assert not [f for f in conditions.findings(m) if f.ref == "agendas:stanley:arrest"]


async def test_what_words_flag_is_asked_about_once_and_the_review_that_reads_the_original_decides(tmp_path, monkeypatch, judge):
    """The review of 2026-10-20: matching words took steps out even when the reading call saw nothing
    wrong. Now a word match is put to the call as a hint, and only what the review flags comes out."""
    folder = copy("zhuishuren", tmp_path)
    situation = folder / "situation.yaml"
    data = yaml.safe_load(situation.read_text(encoding="utf-8"))
    data["agendas"] = [a for a in data["agendas"] if a["npc"] != "thomas"]
    situation.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    asked = []

    async def ask(cfg, name, m, *, source="", problems=(), hints=()):
        asked.append((list(problems), list(hints)))
        steps = [Move(id="shelf", text="被问到丢书时领人看书架上的空档。", meet=True, once=True, source="c_shelf_gap"),
                 Move(id="hand_diary", text="被问到叔叔时顺手指出旧书桌里的那本日记。", meet=True, once=True, source="补写")]
        return adapt.Agendas(agendas=[Agenda(npc="thomas", goals=["找回书"], steps=steps)])

    monkeypatch.setattr(adapt, "_ask", ask)
    monkeypatch.setattr(adapt, "wanted", lambda m, name: name == "agendas")
    judge.flags = ["agendas:thomas:hand_diary"]  # the reading call agrees: it hands over what 侦查 finds
    [result, review] = await adapt.adapt(folder, passes=("agendas", "verify"), cfg=SimpleNamespace(), log=lambda _: None)
    assert asked[0] == ([], []) and not asked[1][0] and any("c_diary" in h for h in asked[1][1]) and len(asked) == 2  # a hint, once
    assert not result.left_out  # the words alone took nothing out
    assert "侦查" in judge.calls[0] and "[agendas:thomas:shelf]" in judge.calls[0]  # the review reads the conditions and what was added
    assert "【程序按关键词找出的疑点" in judge.calls[0] and "agendas:thomas:hand_diary" in judge.calls[0]
    steps = [s.id for a in Module.load(folder).agendas if a.npc == "thomas" for s in a.steps]
    assert steps == ["shelf"] and review.written == 1  # taken out because the review said so


SITUATION = """relations:
- a: thomas
  b: players
  kind: 雇佣
  value: 55
foreshadow:
- id: f_mud
  hint: 书房窗台内侧积着一线细干土
  where: shufang
  pays_off: k_not_burglar
- id: f_hollow
  hint: 铁铲杵进土里，回声闷闷的
  where: mudi
  pays_off: k_underground
agendas:
- npc: thomas
  goals:
  - 找回书
  steps:
  - id: greet
    text: 应门、寒暄，把空房间和行李安顿好
    meet: true
    once: true
  - id: show_study
    text: 被问到丢书时领人上书房，顺手指出那本日记
    meet: true
  - id: urge_watch
    text: 建议夜里守着书房窗户
    when: D2 18:00
    meet: true
history:
- when: 一年前
  text: 道格拉斯走进墓中那扇门
  who:
  - douglas
- when: 上周
  text: 道格拉斯从书房的窗户闯入，取走六本书
  who:
  - douglas
"""


async def test_the_review_takes_out_what_it_flags(tmp_path, judge):
    folder = copy("zhuishuren", tmp_path)
    (folder / "situation.yaml").write_text(SITUATION, encoding="utf-8")
    judge.flags = ["foreshadow:f_hollow", "history:1", "knows:thomas:0", "nothing:here"]
    before = Module.load(folder)
    out = await adapt.verify(folder, SimpleNamespace(), log=lambda _: None)
    after = Module.load(folder)
    assert "[agendas:thomas:show_study]" in judge.calls[0]
    if (paths.MODULES / "zhuishuren" / "source").exists():  # the originals stay on the importer's machine
        assert "【原文（整理资料的出处" in judge.calls[0]
    assert "[knows:" not in judge.calls[0]  # the whole layer is reviewed, never the NPCs' own sheets
    assert "【程序按关键词找出的疑点" in judge.calls[0] and "agendas:thomas:show_study" in judge.calls[0]  # put to the call
    assert [s.id for a in after.agendas for s in a.steps] == ["greet", "show_study", "urge_watch"]  # it saw no harm: words take nothing out
    assert [f.id for f in after.foreshadow] == ["f_mud"] and after.history == before.history[:1]
    assert after.npcs == before.npcs and after.clues == before.clues and after.truth == before.truth  # the module's own, untouched
    assert out.written == 2 and not [i for i in audit(after) if i.level == "error"]


# -- 5. what the package and the keeper's book carry --------------------------------------------
def test_the_skill_package_carries_the_plans_and_who_keeps_each_rule(tmp_path):
    m = Module.load(paths.EXAMPLE)
    m.art, m.pictures, m.extensions = {}, {}, {}
    m.agendas = [Agenda(npc="warden", steps=[
        Move(id="prepared", text="标记_预备的遭遇反应", meet=True, once=True, source="tower"),
        Move(id="ruled", text="标记_裁定后的结果", ruling=True, only_if="调查员说服检定孤注一掷成功")])]
    m.mechanics = [Mechanic(name="钟声倒数", rules="标记_守秘人规则"), Mechanic(name="理智", rules="标记_核心规则", runs="核心规则")]
    root = compiler.compile_module(m, tmp_path / "compiled")
    situation = (root / "references" / "situation.md").read_text(encoding="utf-8")
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "标记_预备的遭遇反应（见面反应，只做一次；依据：tower）" in situation
    assert "标记_裁定后的结果（要守秘人裁定后才发生；只在：调查员说服检定孤注一掷成功）" in situation
    assert "### 钟声倒数（守秘人执行）" in situation and "### 理智（核心规则执行）" in situation
    assert "references/situation.md" in skill and "**钟声倒数**（守秘人）：标记_守秘人规则" in skill and "**理智**（核心规则）" in skill
    book = _book(m)
    assert "标记_守秘人规则" in book and "标记_核心规则" not in book  # the keeper's to keep, in its own book


# -- the six cases ------------------------------------------------------------------------------
def test_case_the_diary_not_found_stays_behind_its_roll():
    """日记没找到：托马斯只领人看书架；孤注一掷成功后他来帮忙是守秘人的裁定，不给他当成见面就做。"""
    m = Module.load(paths.MODULES / "zhuishuren")
    with_plan(m, "thomas",
              Move(id="shelf", text="被问到丢书时领人看书架上的空档。", meet=True, once=True, source="c_shelf_gap"),
              Move(id="help_find", text="过来帮忙，从旧书桌的暗格里翻出日记", ruling=True,
                   only_if="调查员在书房侦查失败后孤注一掷成功", source="c_diary"),
              Move(id="after_diary", text="追问日记里叔叔写了什么，脸色发白", meet=True, once=True, when="knows:players:clue:c_diary"))
    assert "c_diary「道格拉斯的日记」在 shufang·旧书桌；检定 侦查（常规）" in adapt.book(m)  # the context says what it takes
    assert not [f for f in conditions.findings(m) if f.level == "error"]
    w = world(m, "jinboer_jia", 11 * 60)
    brief = context.npc_brief(w, "thomas", query="叔叔的书房在哪？")
    assert "领人看书架上的空档" in brief and "日记" not in brief
    assert "要你裁定以后才可能的：过来帮忙，从旧书桌的暗格里翻出日记（只在调查员在书房侦查失败后孤注一掷成功时）" in context.keeper_brief(w)
    # 线索已经发现：the step waiting for the diary comes up once it is found, not before.
    w.st.clues.append("c_diary")
    w.sync()
    assert "追问日记里叔叔写了什么" in context.npc_brief(w, "thomas", query="我们找到了日记")


def test_case_liquor_bought_is_not_liquor_given():
    """买了酒没送：has_liquor 只说明调查员有酒；杰弗逊的见面反应写明要当面递到他手里。"""
    m = Module.load(paths.MODULES / "zhuishuren")
    step = Move(id="take_bribe", text="收下酒，态度松动，说出深夜在那块墓碑旁见过人影", meet=True, once=True, when="flag:has_liquor")
    with_plan(m, "jefferson", step)
    assert any(f.ref == "agendas:jefferson:take_bribe" for f in conditions.findings(m))  # too wide without the grounds
    with_plan(m, "jefferson", step.model_copy(update={"only_if": "调查员当面把酒递给你、你收下以后；只是买到了酒不算"}))
    assert not [f for f in conditions.findings(m) if f.ref == "agendas:jefferson:take_bribe"]
    w = world(m, "mudi", 12 * 60)
    assert "收下酒" not in context.npc_brief(w, "jefferson", query="下午好")
    w.st.flags.append("has_liquor")
    brief = context.npc_brief(w, "jefferson", query="下午好，您在这里干了多久？")
    assert "收下酒，态度松动，说出深夜在那块墓碑旁见过人影（只在调查员当面把酒递给你、你收下以后；只是买到了酒不算时才这样）" in brief


async def test_case_what_the_players_learned_privately_wakes_no_one_without_the_grounds():
    """玩家私下得到情报：斯坦利醒来想的理由里写着要有人报告或亲眼看到，他自己的资料里没有就不抓人。"""
    m = Module.load(paths.MODULES / "fallout3-youxierenyouyanbujin")
    for a in m.agendas:
        for s in a.steps:
            if s.id == "stanley_arrests":
                s.text, s.only_if = "带人上门抓人，关进警局后面的牢房。", "有手下或有福者向他报告、或他亲眼看到调查员在追查教授和望远镜"
    w = world(m, m.start, 12 * 60)
    w.st.flags.append("learned_telescope")
    brain = agents.Canned(lambda ask: agents.Scene(decisions=[agents.Decision(npc=npc, wait=600) for npc in ask.npcs]))
    await scheduler.advance(w, brain, w.now, w.now, events_only=True)
    reasons = [ask.reasons["stanley"] for ask in brain.asks if "stanley" in ask.reasons]
    assert reasons and all("只在有手下或有福者向他报告、或他亲眼看到调查员在追查教授和望远镜时才这样" in r for r in reasons)


def test_case_polite_small_talk_does_not_replay_the_greeting():
    """礼貌闲聊：应门寒暄只做一次，做过就完成；一贯的态度留着，但写明碰上了才带出。"""
    m = Module.load(paths.MODULES / "zhuishuren")
    with_plan(m, "thomas", Move(id="greet", text="应门、寒暄，把空房间和行李安顿好", meet=True, once=True, when="D1 10:00"),
              Move(id="frugal", text="提到花销就念叨一句“每一分都记着账”", meet=True, once=False))
    w = world(m, "jinboer_jia", 11 * 60)
    first = context.npc_brief(w, "thomas", query="您好，我应邀来了")
    assert "应门、寒暄，把空房间和行李安顿好（greet，只做一次，做过填进 played）" in first
    assert "（frugal，一贯如此，碰上了才带出，不必每次都演）" in first
    groups, _ = adapters.reply_ops(w, "thomas", played=["greet", "frugal"])
    assert groups == [[{"kind": "step", "npc": "thomas", "step": "greet", "status": "done"}]]  # its way stays its way
    for group in groups:
        for op in group:
            assert w.apply(agents.AdvancePlan.model_validate({**op, "actor": "thomas"})).ok
    later = context.npc_brief(w, "thomas", query="这房子您打算怎么处理？")
    assert "安顿好" not in later and "每一分都记着账" in later


def test_case_an_npc_offering_help_comes_at_its_time_and_the_ruled_help_is_the_keepers():
    """人物主动协助：到点的主动建议照常出现；要裁定的帮忙只在守秘人那边。"""
    m = Module.load(paths.MODULES / "zhuishuren")
    with_plan(m, "thomas", Move(id="urge_watch", text="主动建议夜里守着书房窗户抓贼", meet=True, once=True, when="D2 18:00"),
              Move(id="lend_lamp", text="把自家的煤油灯借给调查员守夜", ruling=True, only_if="调查员开口借灯具守夜"))
    w = world(m, "jinboer_jia", 24 * 60 + 12 * 60)
    assert "主动建议" not in context.npc_brief(w, "thomas")
    assert "lend_lamp" not in [s.id for s in w.rec.agents["thomas"].plan]  # not his to decide backstage either
    w.st.minutes = 24 * 60 + 18 * 60
    assert "主动建议夜里守着书房窗户抓贼" in context.npc_brief(w, "thomas")
    assert "把自家的煤油灯借给调查员守夜" not in context.npc_brief(w, "thomas")
    assert "要你裁定以后才可能的：把自家的煤油灯借给调查员守夜（只在调查员开口借灯具守夜时）" in context.keeper_brief(w)


# -- the bundled modules ------------------------------------------------------------------------
@pytest.mark.parametrize("key", BUNDLED)
def test_the_bundled_situations_hand_over_nothing_behind_a_roll(key):
    m = Module.load(paths.MODULES / key)
    assert not [str(f) for f in conditions.findings(m) if f.level == "error"]


@pytest.mark.parametrize("key", BUNDLED)
def test_the_bundled_fights_and_chases_leave_the_combat_pass_nothing_to_do(key):
    """Re-authored by hand from their own text (0.0.4): every route joined up with ids and places,
    every NPC's fighting form named, no first sight charged twice. `upgrade` would only rewrite them."""
    m = Module.load(paths.MODULES / key)
    assert not adapt.wanted(m, "combat")
    assert not adapt._unlinked(m) and not adapt._twice(m) and not adapt._forms(m)
    material = adapt._material(m, "combat")
    assert "没有 id" not in material and "还缺" not in material and "引擎读不出的" not in material


def test_the_regenerated_paper_chase_says_the_grounds_of_every_step():
    """《追书人》的局面层按新的补写重做过（2026-10-20）：每个见面反应都标了只做一次还是一贯如此，
    没有一步拿调查员的进展当成 NPC 已经知道，规则清单写明谁执行。"""
    m = Module.load(paths.MODULES / "zhuishuren")
    assert not conditions.findings(m)
    assert all(s.once is not None for a in m.agendas for s in a.steps if s.meet)
    assert all(x.runs or x.plugin for x in m.mechanics)
    assert not [s for a in m.agendas for s in a.steps if "日记" in s.text and not s.ruling]


def test_the_texts_edit_takes_back_one_entry_and_nothing_else():
    text = SITUATION
    data = yaml.safe_load(text)
    out = yaml.safe_load(yamledit.drop(yamledit.drop_entry(yamledit.drop_step(text, "agendas", "thomas", "show_study"),
                                                           "foreshadow", "f_mud"), "history", 0))
    assert out["relations"] == data["relations"]
    assert out["foreshadow"] == data["foreshadow"][1:] and out["history"] == data["history"][1:]
    assert out["agendas"][0]["steps"] == [s for s in data["agendas"][0]["steps"] if s["id"] != "show_study"]
    people = (paths.MODULES / "zhuishuren" / "people.yaml").read_text(encoding="utf-8")
    again = yaml.safe_load(yamledit.replace(people, "npcs", "thomas", "knows", ["只剩这一条"]))
    before = yaml.safe_load(people)
    assert [n for n in again["npcs"] if n["id"] != "thomas"] == [n for n in before["npcs"] if n["id"] != "thomas"]
    assert next(n for n in again["npcs"] if n["id"] == "thomas")["knows"] == ["只剩这一条"]
    assert {k: v for k, v in next(n for n in again["npcs"] if n["id"] == "thomas").items() if k != "knows"} == \
        {k: v for k, v in next(n for n in before["npcs"] if n["id"] == "thomas").items() if k != "knows"}
    assert yamledit.drop_entry("foreshadow:\n- id: a\n  hint: x\nties: []\n", "foreshadow", "a") == "foreshadow: []\nties: []\n"


def test_a_fact_saying_what_a_clue_behind_a_roll_says_is_asked_about_and_kept():
    m = Module.load(paths.MODULES / "zhuishuren")
    diary = m.clue("c_diary").text.strip()
    found = adapt.held(m, "npcs", [("put", "npcs", "odell", "knows", [diary[:40], "叔叔有本日记，在书房里"])])
    assert [(f.ref, f.level) for f in found] == [("knows:odell:0", "note")]  # knowing it is there is no finding


def test_the_condition_language_of_a_plan_is_checked_against_the_module():
    m = Module.load(paths.MODULES / "zhuishuren")
    with_plan(m, "thomas", Move(id="a", text="x", when="knows:players:clue:c_diary & at:players:shufang"),
              Move(id="b", text="y", when="knows:players:clue:nope | at:ghost:shufang"))
    errors = [str(i) for i in audit(m) if i.level == "error"]
    assert not [e for e in errors if " a " in e] and len([e for e in errors if "打算 b " in e]) == 2
    with_plan(m, "thomas", Move(id="c", text="z", when="D1 20:30&flag:watching"))  # at half past eight, once they keep watch
    assert not any("时间只能" in str(i) for i in audit(m) if i.level == "error")
    with_plan(m, "thomas", Move(id="d", text="z", when="D1 20:30 | flag:watching"))
    assert any("时间只能单独写，或者用 & 和条件连写" in str(i) for i in audit(m))
    [(_, _, [agenda])] = adapt.edits(m, "agendas", adapt.Agendas(agendas=[m.agendas[-1]]))
    assert agenda["steps"][-1]["when"] == "D1 20:30 | flag:watching"  # never cut down to its hour: asked again (adapt.held)
    found = adapt.held(Module.load(paths.MODULES / "zhuishuren"), "agendas", [("append", "agendas", [agenda])])  # new there
    assert any(f.level == "error" and "时间只能单独写，或者用 & 和条件连写" in f.text for f in found)


def test_a_step_with_grounds_the_condition_cannot_say_is_judged_not_done():
    m = Module.load(paths.MODULES / "zhuishuren")
    with_plan(m, "douglas", Move(id="take", text="翻进书房抱走几本书", when="D1 23:00", does="flag:figure_seen"),
              Move(id="maybe", text="趁夜翻进书房", when="D1 23:00", does="flag:figure_seen", only_if="当晚没有人守着书房窗户"))
    w = world(m, m.start)
    plan = {s.id: s for s in w.rec.agents["douglas"].plan}
    assert plan["take"].ops and not plan["maybe"].ops  # done at its hour / his to judge, with the grounds in its words
    assert plan["maybe"].achieved == "flag:figure_seen" and "只在当晚没有人守着书房窗户时才这样" in plan["maybe"].text
    from autokeeper.module.simulate import playthrough

    assert [d.step for d in playthrough(m).drift.timeline if d.npc == "douglas"] == ["take"]
