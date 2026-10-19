"""The world record and its operations: validation, repeats, undo, who knows what, saves kept apart."""

from __future__ import annotations

import json

import pytest

from autokeeper import config
from autokeeper.engine import world as legacy_world
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState, WorldChange
from autokeeper.module.schema import Item, Module
from autokeeper.world import adapters, context, index, knowledge
from autokeeper.world import ops as O
from autokeeper.world.record import PLAYERS, Step
from autokeeper.world.store import World


def fresh(location: str = "square") -> World:
    m = Module.load(config.EXAMPLE)
    m.items.append(Item(id="coat", name="雨衣", description="油布雨衣", where="tower", owner="warden"))
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.location = location
    return World(m, st)


def staged(location: str = "office") -> World:
    """Two important NPCs, a fact only the warden knows, a promise, a thread, a plan."""
    w = fresh(location)
    me = w.st.investigator.id
    for op in [
        O.Enlist(npc="warden", goals=["守住钟楼"]),
        O.Enlist(npc="preacher"),
        O.AddFact(fact="f_key", text="齿轮间的钥匙挂在守钟人腰上", about=["warden"], known_by=["warden"]),
        O.MakePromise(promise="p1", by=me, to="warden", text="把雨衣还回来", due=w.now + 600),
        O.OpenThread(thread="th1", title="钟里的东西", facts=["npc:warden:secrets:0"]),
        O.SetPlan(npc="preacher", steps=[Step(id="a1", text="去钟楼布道")]),
    ]:
        assert w.apply(op).ok
    return w


def VALID(me: str, now: int) -> list:
    return [
        O.Move(entity="warden", to="square"),
        O.Relate(a="warden", b=me, rel="信任", delta=10, note="帮他修了钟"),
        O.Transfer(item="coat", to=me, giver="warden"),
        O.Condition(entity=me, add="受伤"),
        O.Condition(entity="warden", status="受伤"),
        O.SetFlag(flag="bell_stopped"),
        O.AddFact(fact="f_new", text="守钟人昨晚去过报社", about=["warden"], known_by=["warden"]),
        O.Learn(fact="f_key", who=["preacher"], actor="warden"),
        O.Remember(npc="warden", text="那个记者问过钟的事"),
        O.Occur(text="广场上起了雾", place="square"),
        O.MakePromise(promise="p2", by="warden", to=me, text="明早带你上钟楼", due=now + 600),
        O.Fulfil(promise="p1", actor=me),
        O.Break(promise="p1", actor="warden"),
        O.OpenThread(thread="th2", title="布道者知道什么"),
        O.AdvanceThread(thread="th1", beat="调查员听见齿轮间有动静"),
        O.CloseThread(thread="th1", resolution="钟停了"),
        O.Schedule(event="s1", at=now + 60, text="布道者去钟楼", ops=[{"kind": "move", "entity": "preacher", "to": "tower"}]),
        O.Enlist(npc="preacher", goals=["让全镇听他讲完"]),
        O.SetPlan(npc="warden", steps=[Step(id="s1", text="去报社", due=now + 120)]),
        O.AdvancePlan(npc="preacher", step="a1", status="done"),
        O.Rewrite(target="fact:npc:warden:secrets:0", text="他在钟里藏了亡子的怀表", reason="更有人情味"),
    ]


INVALID = [
    O.Move(entity="warden", to="nowhere"),
    O.Move(entity="coat", to="square"),
    O.Relate(a="warden", b="warden", rel="信任", delta=1),
    O.Transfer(item="coat", to="square", giver="preacher"),
    O.Transfer(item="gold", to="square"),
    O.Condition(entity="warden"),
    O.SetFlag(flag="undeclared"),
    O.AddFact(fact="truth", text="重复的 id"),
    O.Learn(fact="npc:warden:secrets:0", who=["preacher"], actor="preacher"),
    O.Remember(npc="nobody", text="x"),
    O.Occur(text="", place="square"),
    O.MakePromise(promise="p1", by="warden", to="preacher", text="又一个 p1"),
    O.Fulfil(promise="nope"),
    O.Break(promise="p1", actor="preacher"),
    O.OpenThread(thread="th1", title="重复"),
    O.AdvanceThread(thread="nope", beat="x"),
    O.Schedule(event="late", at=0, text="早就过了"),
    O.Enlist(npc="square"),
    O.SetPlan(npc="square", steps=[]),
    O.AdvancePlan(npc="warden", step="nope", status="done"),
    O.Rewrite(target="fact:nope", text="x"),
    O.Rewrite(target="flag:x", text="x"),
]


@pytest.mark.parametrize("i", range(len(VALID("x", 0))))
def test_every_op_settles_once_and_undoes_cleanly(i):
    w = staged()
    op = VALID(w.st.investigator.id, w.now)[i]
    before = json.dumps(w.domain(), sort_keys=True)
    first = w.apply(op)
    assert first.ok, first.problems
    after = json.dumps(w.domain(), sort_keys=True)
    assert after != before
    again = w.apply(op)  # a retried turn hands in the same op again
    assert again.ok and again.repeat and json.dumps(w.domain(), sort_keys=True) == after
    assert w.undo() == []
    assert json.dumps(w.domain(), sort_keys=True) == before
    assert w.apply(op).ok  # undone, it can be settled again


@pytest.mark.parametrize("op", INVALID, ids=lambda op: op.kind)
def test_invalid_ops_are_refused_in_plain_words_and_change_nothing(op):
    w = staged()
    before = json.dumps(w.domain(), sort_keys=True)
    r = w.apply(op)
    assert not r.ok and r.problems and all(isinstance(p, str) and p for p in r.problems)
    assert json.dumps(w.domain(), sort_keys=True) == before


def test_every_kind_is_covered():
    covered = {op.kind for op in VALID("x", 0)} | {"improvise"}
    assert covered == set(O.KINDS)


def test_the_same_op_id_with_another_payload_is_refused():
    w = staged()
    assert w.apply(O.Move(id="t3:go", entity="warden", to="square")).ok
    r = w.apply(O.Move(id="t3:go", entity="warden", to="office"))
    assert not r.ok and "已经用于另一个操作" in r.problems[0]
    assert w.entity("warden").at == "square"


def test_a_settle_that_fails_halfway_leaves_nothing(monkeypatch):
    w = staged()
    before = json.dumps(w.domain(), sort_keys=True)

    def broken(self, world, oid):
        world.rec.entities["warden"].at = "office"
        raise RuntimeError("boom")

    monkeypatch.setattr(O.Move, "settle", broken)
    r = w.apply(O.Move(entity="warden", to="square"))
    assert not r.ok and "结算失败" in r.problems[0]
    assert json.dumps(w.domain(), sort_keys=True) == before


def test_undo_keeps_what_the_players_saw_and_refuses_when_the_state_moved_on():
    w = staged(location="tower")
    assert w.apply(O.Move(entity="warden", to="square")).ok  # in front of the party: public
    w.st.turn += 1
    assert "玩家已经看到了" in w.undo()[0]
    w = staged()
    assert w.apply(O.Move(entity="warden", to="square")).ok
    w.st.flags.append("bell_stopped")  # the game changed the state outside the world API
    assert "状态又变了" in w.undo()[0]
    w.st.flags.remove("bell_stopped")
    assert w.undo() == [] and w.entity("warden").at == "tower"


def test_undo_to_restores_several_ops_in_order_and_keeps_the_trace():
    w = staged()
    mark = w.apply(O.Move(entity="warden", to="square")).op
    w.apply(O.Relate(a="warden", b="preacher", rel="盟友", value=30))
    w.apply(O.Transfer(item="coat", to="preacher"))
    assert w.undo_to(mark) == []
    assert w.entity("warden").at == "tower" and not w.rec.relations and w.entity("coat").at == "warden"
    assert w.m.npc("warden").location == "tower"  # the module copy follows the record
    assert [e.kind for e in w.rec.journal if e.undone] == ["move", "relate", "transfer"]


def test_moving_an_npc_moves_it_in_this_games_module_and_who_saw_it():
    w = staged(location="office")
    w.apply(O.Move(entity="warden", to="square", trace="钟楼的门没锁"))
    e = w.rec.events[-1]
    assert w.m.npcs_at("square") and {n.id for n in w.m.npcs_at("square")} == {"warden", "preacher"}
    assert e.visibility == "private" and "preacher" in e.witnesses  # the preacher was there; the party was not
    assert "钟楼的门没锁" not in context.keeper_view(w)
    assert "钟楼的门没锁" not in context.keeper_view(w, "tower")  # traces are left where the event happened
    assert "老守钟人" in context.keeper_view(w, "square")


# -- layers and knowledge ----------------------------------------------------------------------------
def test_the_module_is_seeded_as_the_base_layer_and_left_as_it_was():
    m = Module.load(config.EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    before = m.model_dump()
    w = World(m, st)
    assert m.model_dump() == before
    f = w.rec.facts["npc:warden:secrets:0"]
    assert f.origin == "module" and f.known_by == ["warden"] and f.revealed is None
    assert w.rec.facts["conclusion:k_tower"].explains == ["c_gears", "c_diary", "c_archive"]
    assert w.rec.facts["clue:c_diary"].known_by == ["warden"]
    assert World(m, st).rec.seeded == w.rec.seeded  # seeding again adds nothing


def test_an_npc_sees_only_what_it_knows():
    w = staged()
    secret = w.rec.facts["npc:warden:secrets:0"].text
    preacher = context.npc_view(w, "preacher")
    assert secret not in preacher.text and "齿轮间的钥匙" not in preacher.text
    assert "npc:warden:secrets:0" not in preacher.facts and "truth" not in preacher.facts
    assert w.m.truth not in preacher.text
    assert w.apply(O.Learn(fact="f_key", who=["preacher"], actor="engine")).ok
    assert "齿轮间的钥匙" in context.npc_view(w, "preacher").text
    assert "齿轮间的钥匙挂在守钟人腰上" in w.m.npc("preacher").knows  # its sheet's 知道的事, for its dialogue
    assert secret not in context.keeper_view(w) and w.m.truth not in context.keeper_view(w)


async def test_recall_is_scoped_to_the_viewer():
    w = staged()
    w.apply(O.Learn(fact="npc:warden:secrets:0", who=["warden"], how="自己藏的", actor="engine"))
    recall = index.Recall()
    assert any("钟里藏" in h.text for h in await recall.search(w, "钟里藏了什么", viewer="warden"))
    assert not any("钟里藏" in h.text for h in await recall.search(w, "钟里藏了什么", viewer="preacher"))
    assert not any("钟里藏" in h.text for h in await recall.search(w, "钟里藏了什么", viewer=PLAYERS))


def test_what_reaches_the_table_is_marked_revealed_for_good():
    w = staged(location="tower")
    w.st.clues.append("c_gears")
    w.st.add("npc", "老守钟人嘀咕：钟每到午夜多敲一下，你别管。", who="老守钟人")
    revealed = w.sync()
    assert {"clue:c_gears", "npc:warden:knows:0"} <= set(revealed)
    assert PLAYERS in w.rec.facts["npc:warden:knows:0"].known_by
    w.st.log.clear()  # the log is folded into a summary later; revealed stays
    w.sync()
    assert w.rec.facts["npc:warden:knows:0"].revealed == w.st.turn


# -- saves stay apart --------------------------------------------------------------------------------
def test_saves_never_share_a_record(tmp_path, monkeypatch):
    from autokeeper.net.hub import PRIVATE
    from autokeeper.tui import saves

    a, b = staged(), staged()
    a.apply(O.Move(entity="warden", to="square"))
    assert b.entity("warden").at == "tower" and b.m.npc("warden").location == "tower"
    loaded = GameState.model_validate_json(a.st.model_dump_json())
    assert loaded.record == a.st.record
    assert "record" in PRIVATE and "record" not in a.st.model_dump(exclude=PRIVATE)
    monkeypatch.setattr(config, "SAVES", tmp_path)
    a.st.slot = "one"
    (tmp_path / "one.json").write_text(a.st.model_dump_json(), encoding="utf-8")
    slot = saves.copy(saves.Save("one", {}, a.st))
    copy = World(Module.load(config.EXAMPLE), GameState.model_validate_json((tmp_path / f"{slot}.json").read_text(encoding="utf-8")))
    copy.apply(O.Move(entity="warden", to="office"))
    original = GameState.model_validate_json((tmp_path / "one.json").read_text(encoding="utf-8"))
    assert original.record.entities["warden"].at == "square" and copy.entity("warden").at == "office"


async def test_recall_of_one_save_never_finds_another_saves_events():
    a, b = staged(), staged()
    b.apply(O.Occur(text="布道者在地窖里烧掉了一本账簿", place="square", participants=["preacher"]))
    recall = index.Recall()
    assert await recall.search(b, "烧掉账簿")
    assert not any("账簿" in h.text for h in await recall.search(a, "烧掉账簿"))


# -- retrieval -----------------------------------------------------------------------------------------
async def test_a_new_relation_can_be_found_every_way():
    w = staged()
    me = w.st.investigator.id
    assert w.apply(O.Relate(a="preacher", b=me, rel="敌对", value=-40, note="记者当众顶撞了他")).ok
    rels = index.relations(w, "preacher")
    assert [(r.b, r.kind, r.value) for r in rels] == [(me, "敌对", -40)]
    assert index.neighbours(w, me) == {"preacher": 1, "warden": 1}  # the warden through the promise
    assert index.neighbours(w, "preacher", viewer="warden") == {}  # what the warden knows of the preacher: nothing
    assert index.relations(w, "preacher", viewer="warden") == []  # the warden was not told
    assert "敌对 -40" in context.npc_view(w, "preacher").text
    hits = await index.Recall().search(w, "布道者和记者结仇了吗", viewer="preacher")
    assert any("敌对" in h.text for h in hits)


async def test_recall_ranks_by_embedding_when_an_embedder_is_given():
    import numpy as np

    vocab = ["钥匙", "雾", "雨衣"]

    async def embed(texts):  # a stand-in for bge-small-zh: one axis per word
        return np.array([[float(v in t) for v in vocab] + [0.01] for t in texts])

    w = staged()
    w.apply(O.Occur(text="广场上起了雾", place="square"))
    hits = await index.Recall(embed).search(w, "雾", k=1)
    assert hits[0].text == "广场上起了雾"


def test_structured_queries():
    w = staged()
    me = w.st.investigator.id
    w.apply(O.Move(entity="warden", to="square"))
    assert [e.kind for e in index.happened_at(w, "square")] == ["plan", "move"]
    assert [e.kind for e in index.happened_at(w, "square", viewer="warden")] == ["move"]  # not the preacher's plan
    assert [p.id for p in index.promises(w, "warden")] == ["p1"]
    assert {f.id for f in index.knows(w, "warden")} >= {"f_key", "npc:warden:secrets:0"}
    assert index.promises(w, me, status="fulfilled") == []


# -- the game's existing changes as ops -----------------------------------------------------------------
def _changes(me: str) -> list[tuple[list[WorldChange], str, str]]:
    return [
        ([WorldChange(kind="transfer", key="lend", text="你把雨衣借给守钟人。", item="雨衣", owner="warden", npc="warden")], "arbiter", ""),
        ([WorldChange(kind="promise", key="evening", npc="warden", text="今晚我在塔门口等你。")], "warden", "（擦手）今晚我在塔门口等你。"),
        ([WorldChange(kind="relationship", key="trust", npc="warden", text="守钟人开始信任你。")], "arbiter", ""),
        ([WorldChange(kind="fact", key="chat", text="聊了天气。")], "arbiter", ""),
        ([WorldChange(kind="transfer", key="back", text="雨衣还你。", npc="warden", item="雨衣", owner=me)], "warden", "雨衣还你。"),
        ([WorldChange(kind="fulfill", key="evening", text="在塔门口碰面。", npc="warden")], "arbiter", ""),
        ([WorldChange(kind="transfer", key="theft", text="拿走金条", item="gold", owner=me),
          WorldChange(kind="promise", key="invented", text="我把秘密全告诉你。", npc="preacher")], "preacher", "今天天气很好。"),
    ]  # fmt: skip


def test_world_changes_through_ops_leave_the_game_state_exactly_as_before():
    old, new = fresh("tower"), fresh("tower")
    for w in (old, new):
        w.st.investigator.inventory.append("雨衣")
    for changes, source, spoken in _changes(old.st.investigator.id):
        legacy_world.apply(old.m, old.st, changes, source=source, spoken=spoken)
        adapters.settle_changes(new, changes, source=source, spoken=spoken)
        assert new.st.world == old.st.world
        assert new.st.item_owners == old.st.item_owners
        assert [i.inventory for i in new.st.investigators] == [i.inventory for i in old.st.investigators]
    rec = new.rec
    assert rec.promises["evening"].status == "fulfilled" and rec.promises["evening"].by == "warden"
    assert rec.relations["warden|zhouheng|交情"].note == "守钟人开始信任你。"
    assert "invented" not in rec.promises and "gold" not in rec.entities
    # retried turn: the same changes again settle nothing twice
    journal = len(rec.journal)
    adapters.settle_changes(new, _changes(new.st.investigator.id)[2][0], source="arbiter")
    assert len(rec.journal) == journal


def test_an_older_saves_world_is_mirrored_once():
    w = fresh("tower")
    legacy_world.apply(w.m, w.st, [WorldChange(kind="promise", key="evening", npc="warden", text="今晚我在塔门口等你。")],
                       source="warden", spoken="今晚我在塔门口等你。")  # fmt: skip
    legacy_world.apply(w.m, w.st, [WorldChange(kind="fact", key="chat", text="聊了天气。")], source="arbiter")
    assert all(r.ok for r in adapters.mirror(w))
    assert w.rec.promises["evening"].by == "warden" and w.rec.facts["wc:chat"].revealed is not None
    assert adapters.mirror(w) == []


def test_an_improvisation_lands_through_the_world_api():
    from autokeeper.agents.director import Improv, NewClue, NewNPC

    w = fresh()
    plan = Improv(kind="展开", reason="给布道者一个帮手", npcs=[NewNPC(id="boy", name="报童", role="卖报的孩子", location="square")],
                  clues=[NewClue(id="c_boy", title="报童的话", text="报童说夜里看见守钟人在广场上转悠。", where="square", npc="boy")])  # fmt: skip
    bad = adapters.improvise(w, plan.model_copy(update={"npcs": []}))
    assert not bad.ok and any("boy" in p for p in bad.problems)
    r = adapters.improvise(w, plan)
    assert r.ok and r.data.summary == "新人物 报童"
    assert w.entity("boy").origin == "improv" and w.rec.facts["clue:c_boy"].known_by == ["boy"]
    assert w.st.improv[-1]["kind"] == "展开" and any(t.facts == ["clue:c_boy"] for t in w.rec.threads.values())
    assert adapters.improvise(w, plan).repeat
    assert "不能撤销" in w.undo()[0]


def test_plugin_effects_that_touch_the_world():
    from autokeeper.plugins.schema import Effect

    w = fresh()
    me = w.st.investigator.id
    out = adapters.plugin_ops(w, "cold", [Effect(kind="flag", key="bell_stopped"), Effect(kind="condition", who=me, key="风寒", amount=1),
                                         Effect(kind="note", text="寒气钻进了骨头"), Effect(kind="damage", who=me, amount=1)])  # fmt: skip
    assert [r.ok for r in w.apply_all(out)] == [True, True, True]
    assert "bell_stopped" in w.st.flags and "风寒" in w.st.investigator.conditions
    assert w.rec.events[-1].visibility == "public"


def test_an_npc_learning_through_an_event_keeps_it_in_memory():
    w = staged()
    w.apply(O.Move(entity="warden", to="square"))
    w.apply(O.Occur(text="守钟人把钥匙的事告诉了布道者", place="square", participants=["warden", "preacher"], facts=["f_key"]))
    assert "preacher" in w.rec.facts["f_key"].known_by
    assert any("钥匙" in m.text for m in w.agent("preacher").memory)
    assert knowledge.sees(w.rec.facts["f_key"], "preacher") and not knowledge.sees(w.rec.facts["f_key"], PLAYERS)


def test_what_an_npc_learns_reaches_its_sheet_even_when_it_knew_nothing_before():
    w = staged()
    w.m.npc("preacher").knows.clear()
    w.rec.facts.pop("npc:preacher:knows:0")
    w.apply(O.AddFact(fact="f_rumour", text="报社的档案室晚上不锁门", known_by=["preacher"]))
    assert w.m.npc("preacher").knows == ["报社的档案室晚上不锁门"]
