"""Related older memories survive busy scenes without crossing character knowledge boundaries."""

from autokeeper.world import context
from autokeeper.world import ops as O

from .test_world import staged


def _unrelated_days(w, count=55):
    for index in range(count):
        w.st.minutes += 1
        assert w.apply(O.Occur(id=f"noise:{index}", actor="warden", place="tower", visibility="private",
                              text=f"清扫台阶后核对当日气温记录，第{index}次。")).ok


def test_a_current_question_recalls_an_old_witnessed_event_after_working_memory_was_trimmed():
    w = staged(location="tower")
    old = "调查员把铜钥匙交给守钟人，托他保管到晚饭后。"
    assert w.apply(O.Occur(id="old_key", actor=w.st.investigator.id, place="tower",
                          participants=["warden"], visibility="private", text=old)).ok
    _unrelated_days(w)
    assert not any(m.text == old for m in w.agent("warden").memory)
    query = "我交给你的铜钥匙呢？"
    brief = context.npc_brief(w, "warden", query=query)
    view = context.npc_view(w, "warden", query=query).text
    assert old in brief and old in view
    assert "亲历或当面获知" in brief
    assert "第54次" in brief and "第53次" in brief  # preserve a continuous recent scene too
    assert "第15次" not in brief  # do not simply enlarge the complete window


def test_an_unfulfilled_promise_retrieves_related_old_context_for_autonomous_decisions():
    w = staged(location="tower")
    old = "调查员指出旧仓库北墙的铜钥匙记号，守钟人当场查看了那道刻痕。"
    assert w.apply(O.Occur(id="key_mark", actor="warden", place="tower", visibility="private", text=old)).ok
    assert w.apply(O.MakePromise(promise="key_visit", by="warden", to=w.st.investigator.id,
                                text="陪调查员去旧仓库核对铜钥匙记号", public=False)).ok
    _unrelated_days(w)
    view = context.npc_view(w, "warden").text
    assert old in view and "陪调查员去旧仓库核对铜钥匙记号" in view


def test_related_old_known_facts_are_recalled_without_matching_another_npcs_secret():
    w = staged(location="tower")
    allowed = "铜钥匙已经封存在报社的红色信封里，交由夜班编辑保管。"
    forbidden = "铜钥匙秘密能开启地底祭坛，布道者藏着祭坛地图。"
    assert w.apply(O.AddFact(fact="key_storage", text=allowed, known_by=["warden"], visibility="private")).ok
    assert w.apply(O.AddFact(fact="preacher_key", text=forbidden, known_by=["preacher"],
                            fact_kind="secret", about=["preacher"], visibility="private")).ok
    for index in range(45):
        w.st.minutes += 1
        assert w.apply(O.AddFact(fact=f"weather:{index}", text=f"本周气象登记完成第{index}页。",
                                known_by=["warden"], visibility="private")).ok
    assert w.apply(O.Occur(text=forbidden, actor="preacher", place="square",
                          visibility="private", participants=["preacher"])).ok
    query = "铜钥匙现在由谁保管？"
    brief = context.npc_brief(w, "warden", query=query)
    view = context.npc_view(w, "warden", query=query)
    assert allowed in brief and allowed in view.text and "key_storage" in view.facts
    assert forbidden not in brief and forbidden not in view.text and "preacher_key" not in view.facts
    assert "weather:44" in view.facts and "weather:43" in view.facts
    assert "weather:15" not in view.facts


def test_recalled_reports_and_personal_judgments_do_not_turn_into_firsthand_truth():
    w = staged(location="tower")
    rumor = "据说旧仓库后墙有一把铜钥匙，但没人确认。"
    assert w.apply(O.AddFact(fact="key_rumor", text=rumor, fact_kind="rumor", known_by=["preacher"],
                            visibility="private")).ok
    assert w.apply(O.Learn(fact="key_rumor", who=["warden"], actor="preacher", visibility="private")).ok
    assert w.apply(O.Remember(npc="warden", text="我怀疑铜钥匙是昨晚有人遗落的。", actor="warden")).ok
    brief = context.npc_brief(w, "warden", query="铜钥匙")
    assert "传闻，尚未证实" in brief
    assert "听说，来源：街头布道者" in brief
    assert "个人判断，尚未证实" in brief


def test_small_knowledge_sets_preserve_all_known_facts_even_without_a_query_match():
    w = staged()
    assert w.apply(O.AddFact(fact="unrelated_known", text="柜台上登记着周二的营业时间。",
                            known_by=["warden"], visibility="private")).ok
    view = context.npc_view(w, "warden", query="铜钥匙")
    assert "unrelated_known" in view.facts and "周二的营业时间" in view.text
