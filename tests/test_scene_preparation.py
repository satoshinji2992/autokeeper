"""Prepared conditions reach the shared keeper/director context without model calls."""

from autokeeper import paths
from autokeeper.agents import director
from autokeeper.engine.game import Game
from autokeeper.engine.scenes import SceneView
from autokeeper.engine.state import SceneState
from autokeeper.module import compiler
from autokeeper.module.schema import Check, Event, Module, RoundGate
from autokeeper.world import context
from autokeeper.world import ops as O
from autokeeper.world.store import World

from .test_world import fresh


def test_keeper_gets_complete_local_discovery_conditions_and_original_text():
    w = fresh("tower")
    clue = w.m.clue("c_gears")
    clue.check = Check(skill="侦查", difficulty="困难", bonus=1)
    clue.requires = ["flag:bell_stopped", "c_notice"]
    text = "旧齿轮之间有一处不均匀的磨痕。" * 20 + "只在卸下盖板后才能看清刻字。"
    assert w.apply(O.Rewrite(target="fact:clue:c_gears", text=text, reason="测试完整的现场备课。")).ok
    brief = context.keeper_brief(w)
    assert text in brief
    assert "侦查·困难，奖励骰+1" in brief
    assert "全部前置：flag:bell_stopped、c_notice（当前尚未满足）" in brief
    assert "不是已经发生的发现" in brief
    assert w.st.clues == []


def test_current_scene_preparation_obeys_actual_presence_and_learned_testimony():
    w = fresh("tower")
    assert w.apply(O.Move(entity="warden", to="square")).ok
    assert "c_diary" not in context.scene_preparation(w.m, w.st)
    assert "c_gears" in context.scene_preparation(w.m, w.st)
    assert w.apply(O.Learn(fact="clue:c_diary", who=["preacher"], actor="engine")).ok
    w.st.location = "square"
    block = context.scene_preparation(w.m, w.st)
    assert w.m.clue("c_diary").text in block
    assert "街头布道者能说出；无个人交涉门槛" in block
    assert "图书馆使用" not in block  # no inherited discovery roll for a witness's words


def test_record_rewrites_and_retractions_win_over_a_stale_module_copy():
    w = fresh("tower")
    assert w.apply(O.Rewrite(target="fact:clue:c_gears", text="齿轮背后藏着新写的便条。", reason="测试。")).ok
    assert w.apply(O.Rewrite(target="fact:location:tower:keeper", text="新的现场规则，不能拆下齿轮。", reason="测试。")).ok
    w.m.clue("c_gears").text = "已经过时的线索。"
    w.m.location("tower").keeper = "已经过时的地点规则。"
    block = context.scene_preparation(w.m, w.st)
    assert "齿轮背后藏着新写的便条。" in block and "新的现场规则，不能拆下齿轮。" in block
    assert "已经过时" not in block
    assert w.apply(O.Rewrite(target="fact:clue:c_gears", retract=True, reason="撤回错误准备。")).ok
    assert "c_gears" not in context.scene_preparation(w.m, w.st)


def test_pending_local_events_keep_all_gates_and_do_not_become_already_happened():
    w = fresh("tower")
    event = Event(id="later", title="门外异响", where="tower", when="flag:bell_stopped",
                  after_rounds=RoundGate(event="e_chimes", completed=3),
                  check=Check(skill="聆听", difficulty="困难", bonus=-1),
                  text="必须铃停且先前遭遇完成三轮，聆听成功才会察觉。", read_aloud="门外传来两声敲击。")
    w.m.events += [event, Event(id="remote", title="远处的人", where="office", when="D1 23:00", text="异地独有准备。")]
    w.sync()
    block = context.scene_preparation(w.m, w.st)
    assert "条件：flag:bell_stopped；且 e_chimes 开启的遭遇已经完成 3 轮；且 还须 聆听·困难" in block
    assert "奖励骰-1" in block and event.text in block and event.read_aloud in block
    assert "条件全部成立、引擎实际结算后才发生" in block
    assert "异地独有准备" not in block
    w.st.fired.append(event.id)
    assert "门外异响" not in context.scene_preparation(w.m, w.st)


def test_revised_local_event_and_a_retracted_event_do_not_restore_old_preparation():
    w = fresh("tower")
    w.m.events.append(Event(id="call", title="门口来访", where="tower", when="D1 20:00",
                           text="来访者求助。", read_aloud="陌生人递来一封信。"))
    w.sync()
    assert w.apply(O.Rewrite(target="fact:event:call", text="来访者其实是来还一本旧书。", reason="改变来意。")).ok
    w.m.events[-1].text = "已经作废的来意。"
    block = context.scene_preparation(w.m, w.st)
    assert "来访者其实是来还一本旧书。" in block and "已经作废" not in block
    assert "当前正文已改写，冲突部分以当前守秘人须知为准" in block
    assert w.apply(O.Rewrite(target="fact:event:call", retract=True, reason="撤回未发生的来访。")).ok
    assert "门口来访" not in context.scene_preparation(w.m, w.st)


def test_regular_director_gets_relevant_material_even_when_global_tail_omits_it():
    module = Module.load(paths.MODULES / "dongxuezhixia")
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "clinic"
    w = World(module, state)
    view = context.director_view(w).text
    assert module.clue("c_notes").text not in view  # the former insertion-order cut
    prepared = director.context(module, state, "展开", world=view)
    assert module.location("clinic").keeper in prepared
    assert module.clue("c_notes").text in prepared
    assert module.clue("c_examine").text in prepared
    assert "原载体" in prepared
    assert module.clue("c_map").text not in prepared  # do not add the whole module every turn


def test_director_receives_current_local_identity_knowledge_and_motive_whole():
    w = fresh("tower")
    assert w.apply(O.Rewrite(target="fact:npc:warden:knows:0", text="来访者拿的是我旧同事写的介绍信。",
                             reason="建立眼前联系。")).ok
    assert w.apply(O.Enlist(npc="warden", goals=["弄清这封介绍信为什么没有署名"])).ok
    block = director.context(w.m, w.st, "展开", world=context.director_view(w).text)
    assert "身份：" + w.m.npc("warden").role in block
    assert "来访者拿的是我旧同事写的介绍信。" in block
    assert "当前想要：弄清这封介绍信为什么没有署名" in block


def test_director_can_prepare_local_people_before_world_record_is_initialized():
    module = Module.load(paths.EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    npc = module.npc("warden")
    block = director.context(module, state, "展开")
    assert npc.goal in block and npc.knows[0] in block
    assert module.npc("preacher").goal not in block
    assert not state.record.seeded  # context assembly cannot initialize or mutate the world


def test_an_earlier_scene_does_not_treat_later_shared_prerequisites_as_met():
    w = fresh("tower")
    clue = w.m.clue("c_gears")
    clue.requires = ["flag:bell_stopped"]
    w.st.flags.append("bell_stopped")
    view = SceneView(w.st, SceneState(id="early", members=[w.st.investigator.id], place="tower", at=w.now - 5))
    block = context.scene_preparation(w.m, view)
    assert "全部前置：flag:bell_stopped" in block
    assert "不以其他场景的后续结果提前满足" in block
    assert "当前已满足" not in block


def test_npc_compiled_reference_keeps_the_prerequisite_without_revealing_other_content():
    w = fresh("tower")
    w.m.clue("c_diary").requires = ["flag:bell_stopped", "c_notice"]
    block = compiler._npc_md(w.m, w.m.npc("warden"))
    assert "全部前置条件：flag:bell_stopped、c_notice" in block
    assert w.m.clue("c_notice").text not in block


def test_long_personal_secrets_reach_keeper_without_losing_conditional_tail():
    w = fresh("tower")
    text = "他曾经藏起一些属于家人的东西。" * 20 + "只有收到亡子的信之后才愿意归还。"
    assert w.apply(O.Rewrite(target="fact:npc:warden:secrets:0", text=text, reason="测试人物完整动机。")).ok
    assert text in context.keeper_brief(w)
