"""The world's actual knowers provide testimony, without becoming new physical clue copies.

Scripted replies exercise the same permission path as live NPCs; no model calls are made.
"""

from __future__ import annotations

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, Dialogue, Failure, Initiative, Ruling
from autokeeper.engine import presentation
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module import graph
from autokeeper.module.schema import Check, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import context, knowledge
from autokeeper.world import ops as O
from autokeeper.world.record import PLAYERS

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401
from .test_npc_acts import setup


def witness_table(fake) -> tuple[Game, Cast]:
    return setup(fake, location="square")


def learn(game: Game) -> None:
    assert game.world.apply(O.Learn(fact="clue:c_diary", who=["preacher"], actor="engine")).ok


def rewrite_knowers(game: Game, who: list[str], *, retract: bool = False) -> None:
    assert game.world.apply(O.Rewrite(target="fact:clue:c_diary", text=game.m.clue("c_diary").text,
                                      known_by=who, retract=retract, actor="director")).ok


async def test_new_witness_can_tell_what_it_actually_learned_when_original_holder_is_absent(fake):
    game, cast = witness_table(fake)
    st, m, rec = game.state, game.m, game.world.rec
    clue = m.clue("c_diary")
    assert game.world.place("warden") == "tower" and "c_diary" not in m.npc("preacher").clues
    assert game._told_clues("preacher", [clue.id]) == []
    learn(game)
    assert st.clues == [] and not knowledge.sees(rec.facts["clue:c_diary"], PLAYERS)
    assert graph.tellers(m, clue, record=rec) == ["warden", "preacher"]
    assert clue in graph.at_hand(m, "square", ["preacher"], record=rec)
    assert "c_diary" in cast._npc_system(m.npc("preacher"))
    assert "c_diary" in game._npc_disclosure(Turn(), "preacher")
    assert game._valid_clues([clue.id], Action(kind="talk", summary="问布道者日记的内容", npc="preacher")) == [clue.id]
    assert game._told_clues("warden", [clue.id]) == []  # actual presence still matters
    assert graph.disclosure_gate(m, clue, "preacher") is None  # not the original holder's personal persuasion gate
    assert graph.disclosure_gate(m, clue, "warden") == m.npc("warden").gates[clue.id]
    SCRIPT.append({"needs_roll": False})
    await cast.arbiter(st, Action(kind="talk", summary="问布道者日记的内容", npc="preacher"))
    prompt = SEEN[-1]["messages"][-1]["content"]
    assert "街头布道者(preacher) 能说出" in prompt and "preacher 的 c_diary：无个人披露门槛" in prompt
    assert "街头布道者能说出" in context.keeper_brief(game.world)
    SCRIPT.append({"text": clue.text, "told": [clue.id]})
    turn = Turn(action=Action(kind="talk", summary="问日记", npc="preacher"))
    await game._reply(turn, "witness", "preacher", lambda: cast.npc(st, "preacher", "你听说日记里写了什么？"),
                      lambda _: None)
    assert st.clues == [clue.id] and turn.replies["witness"].told == [clue.id]
    assert m.npc("preacher").clues == []  # no mutable source projection
    await game.close()


async def test_removing_knowledge_removes_all_dynamic_sources_without_stale_projection(fake):
    game, cast = witness_table(fake)
    clue, rec, st = game.m.clue("c_diary"), game.world.rec, game.state
    rewrite_knowers(game, ["preacher"])
    assert graph.tellers(game.m, clue, record=rec) == ["preacher"]
    assert game._spoken([clue.id], "preacher") == [clue.id]
    assert clue.id in st.npc("preacher").unlocked
    rewrite_knowers(game, [])
    for _ in range(3):
        game.world.sync()
    assert graph.tellers(game.m, clue, record=rec) == []
    assert clue not in graph.at_hand(game.m, "tower", ["warden"], record=rec)
    assert clue not in graph.neighbourhood(game.m, "square", ["preacher"], record=rec)["clues"]
    assert game._valid_clues([clue.id], None) == []
    assert game._spoken([clue.id], "preacher") == []
    assert game._told_clues("preacher", [clue.id]) == []  # stale unlocked is not knowledge
    assert clue.id not in game._npc_disclosure(Turn(), "preacher")
    assert clue.id not in cast._npc_system(game.m.npc("preacher"))
    assert game.m.npc("warden").clues == [clue.id]  # unchanged source declaration cannot grant it back
    SCRIPT.append({"text": "日记的事我说不上来。", "told": [clue.id]})
    await cast.npc(st, "preacher", "日记呢？")
    assert cast.told["preacher"] == [] and st.clues == []
    await game.close()


async def test_new_witness_preserves_prerequisites_and_its_own_disclosure_gate(fake):
    game, cast = witness_table(fake)
    st, clue = game.state, game.m.clue("c_diary")
    clue.requires = ["flag:bell_stopped", "c_notice"]
    gate = Check(skill="图书馆使用", difficulty="困难")
    game.m.npc("preacher").gates[clue.id] = gate
    learn(game)
    assert graph.disclosure_gate(game.m, clue, "preacher") == gate
    assert game._valid_clues([clue.id], None) == []
    assert game._told_clues("preacher", [clue.id]) == []
    st.npc("preacher").unlocked = [clue.id]
    assert game._told_clues("preacher", [clue.id]) == []  # unlocked never skips prerequisites
    assert game.world.apply(O.SetFlag(flag="bell_stopped")).ok
    assert game._told_clues("preacher", [clue.id]) == []
    st.clues.append("c_notice")
    st.npc("preacher").unlocked.clear()
    assert game._told_clues("preacher", [clue.id]) == []  # prerequisites never skip a check
    assert clue.id not in game._npc_disclosure(Turn(), "preacher")
    assert "图书馆使用·困难" in cast._npc_system(game.m.npc("preacher"))
    SCRIPT.append({"text": "我还得想想。", "told": [clue.id]})
    await cast.npc(st, "preacher", "日记里写了什么？")
    assert cast.told["preacher"] == []
    assert game._spoken([clue.id], "preacher") == [clue.id]  # a passed ruling authorizes this speaker
    assert game._told_clues("preacher", [clue.id]) == [clue.id]
    await game.close()


@pytest.mark.parametrize("skill", ["医学", "图书馆使用", "侦查"])
async def test_source_discovery_check_remains_but_does_not_become_a_witness_speech_gate(fake, skill):
    game, cast = setup(fake, location="office")
    clue = game.m.clue("c_archive")
    clue.check = Check(skill=skill, difficulty="困难")
    action = Action(kind="investigate", summary="在旧报里查找钟楼的报道", target="旧报合订本")
    SCRIPT.append({"needs_roll": True, "skill": skill, "difficulty": "困难", "clues_on_success": [clue.id]})
    ruling = await cast.arbiter(game.state, action)
    prompt = SEEN[-1]["messages"][-1]["content"]
    assert f"原载体发现参考：{skill}·困难" in prompt
    assert "不自动变成人物口述门槛" in prompt
    assert ruling.skill == skill and ruling.needs_roll
    fake.queue(99)
    turn = await game.resolve(action, ruling)
    assert not turn.checks[0].passed and clue.id not in game.state.clues
    await game.close()

    game, cast = setup(fake, location="square")
    clue = game.m.clue("c_archive")
    clue.check = Check(skill=skill, difficulty="困难")
    clue.requires = ["flag:bell_stopped"]
    assert game.world.apply(O.Learn(fact=f"clue:{clue.id}", who=["preacher"], actor="engine")).ok
    assert game._told_clues("preacher", [clue.id]) == []  # knowing does not bypass prerequisites
    assert game.world.apply(O.SetFlag(flag="bell_stopped")).ok
    assert graph.disclosure_gate(game.m, clue, "preacher") is None
    assert clue.id in knowledge.disclosable_clues(game.m, game.state, "preacher", record=game.world.rec)
    assert clue.id not in game.state.clues  # permission and actual disclosure remain separate
    system = cast._npc_system(game.m.npc("preacher"))
    assert f"披露门槛：{skill}" not in system
    SCRIPT.append({"text": "报纸的事我今天不想谈。"})
    turn = Turn()
    await game._reply(turn, "declined", "preacher", lambda: cast.npc(game.state, "preacher", "报道写了什么？"),
                      lambda _: None)
    assert game.state.clues == [] and turn.replies["declined"].told == []
    SCRIPT.append({"text": clue.text, "told": [clue.id]})
    await game._reply(turn, "volunteered", "preacher", lambda: cast.npc(game.state, "preacher", "请告诉我你读到的内容。"),
                      lambda _: None)
    assert game.state.clues == [clue.id] and turn.replies["volunteered"].told == [clue.id]
    assert clue.check == Check(skill=skill, difficulty="困难")  # source rule is intact
    await game.close()


async def test_an_observation_cannot_unlock_the_current_witness_personal_disclosure_gate(fake):
    game, _ = witness_table(fake)
    learn(game)
    game.m.npc("preacher").gates["c_diary"] = Check(skill="说服")
    action = Action(kind="investigate", summary="观察布道者的反应", npc="preacher", approach="心理学")
    fake.queue(1)
    turn = await game.resolve(action, Ruling(needs_roll=True, skill="心理学", clues_on_success=["c_diary"]))
    assert turn.checks[0].passed and game.state.clues == []
    assert "c_diary" not in game.state.npc("preacher").unlocked
    assert game._told_clues("preacher", ["c_diary"]) == []
    await game.close()


async def test_retracted_clue_is_not_available_even_to_an_unlocked_witness(fake):
    game, cast = witness_table(fake)
    learn(game)
    game.state.npc("preacher").unlocked = ["c_diary"]
    rewrite_knowers(game, ["preacher"], retract=True)
    assert graph.tellers(game.m, game.m.clue("c_diary"), record=game.world.rec) == []
    assert game._valid_clues(["c_diary"], None) == []
    assert game._told_clues("preacher", ["c_diary"]) == []
    assert "c_diary" not in cast._npc_system(game.m.npc("preacher"))
    await game.close()


async def test_current_save_reload_keeps_dynamic_knowers_and_revocations(fake):
    game, _ = witness_table(fake)
    rewrite_knowers(game, ["preacher"])
    saved = game.state.model_dump_json()
    await game.close()
    m = Module.load(EXAMPLE)
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    loaded = Game(m, GameState.model_validate_json(saved), cast, Rules(dice=fake))
    assert graph.tellers(m, m.clue("c_diary"), record=loaded.world.rec) == ["preacher"]
    assert loaded._told_clues("preacher", ["c_diary"]) == ["c_diary"]
    assert "c_diary" in cast._npc_system(m.npc("preacher"))
    assert loaded.state.clues == [] and m.npc("preacher").clues == []
    rewrite_knowers(loaded, [])
    saved = loaded.state.model_dump_json()
    await loaded.close()
    m = Module.load(EXAMPLE)
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    loaded = Game(m, GameState.model_validate_json(saved), cast, Rules(dice=fake))
    assert graph.tellers(m, m.clue("c_diary"), record=loaded.world.rec) == []
    assert loaded._valid_clues(["c_diary"], None) == []
    await loaded.close()


async def test_summary_with_a_handout_waits_for_speech_and_reload_never_supplies_the_original_or_repeats_rules(fake):
    game, cast = witness_table(fake)
    st, clue = game.state, game.m.clue("c_diary")
    clue.sanity, clue.mythos = "0/1D2", 2
    learn(game)
    look = Action(kind="investigate", summary="看看广场上有什么日记", target="日记")
    assert game._valid_clues([clue.id], look) == []
    empty = await game.resolve(look, Ruling(needs_roll=False, auto_clues=[clue.id]))
    assert empty.clues == [] and st.handouts == []  # a bystander's knowledge is not a physical find
    action = Action(kind="talk", summary="问布道者听说的日记内容", npc="preacher", speech="你听说日记写了什么？")
    turn = await game.resolve(action, Ruling(needs_roll=False, auto_clues=[clue.id]))
    assert turn.clues == [] and st.clues == [] and st.handouts == []
    assert clue.id in st.npc("preacher").unlocked
    assert "handout:h_diary" not in presentation.npc_refs(game.m, st, "preacher")
    SCRIPT.append({"text": "我暂时不想谈这个。", "media": [{"ref": "handout:h_diary"}]})
    await game._reply(turn, "declined", "preacher", lambda: cast.npc(st, "preacher", action.speech), lambda _: None)
    assert st.clues == [] and st.handouts == [] and st.presentations == []
    SCRIPT.append({"text": clue.text, "told": [clue.id]})
    saved: dict = {}

    def crash(kept: Turn) -> None:
        saved.update(state=st.model_dump_json(), turn=TURN_RECORD.dump_json(kept))
        raise RuntimeError("收到证词后断线")

    with pytest.raises(RuntimeError, match="断线"):
        await game._reply(turn, "spoke", "preacher", lambda: cast.npc(st, "preacher", action.speech), crash)
    m = game.m.model_copy(deep=True)
    await game.close()
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    loaded = Game(m, GameState.model_validate_json(saved["state"]), cast, Rules(dice=fake))
    kept = TURN_RECORD.validate_json(saved["turn"])
    before_san = loaded.state.investigator.san
    before_mythos = loaded.state.investigator.value("克苏鲁神话")

    async def do_not_ask_again() -> str:
        raise AssertionError("已保存的证词不能重问")

    fake.queue(1)
    await loaded._reply(kept, "spoke", "preacher", do_not_ask_again, lambda _: None)
    assert loaded.state.clues == [clue.id] and loaded.state.handouts == [] and loaded.state.presentations == []
    assert "c_diary" not in loaded.state.npc("warden").unlocked  # a witness did not grant the original holder's gate
    assert loaded.state.investigator.value("克苏鲁神话") == before_mythos + 2
    assert loaded.state.investigator.san == before_san and len(kept.sanity) == 1
    assert "原文在玩家手里" not in loaded._report(Turn(clues=[clue.id]))
    await loaded._reply(kept, "spoke", "preacher", do_not_ask_again, lambda _: None)
    assert len(kept.sanity) == 1 and loaded.state.investigator.value("克苏鲁神话") == before_mythos + 2
    assert not fake.values and loaded.state.handouts == []

    # Explicit full-document knowledge authorizes the existing media path independently of its summary.
    assert loaded.world.apply(O.AddFact(fact="handout:h_diary", text=m.handout("h_diary").text,
                                        known_by=["preacher"], actor="keeper")).ok
    assert "handout:h_diary" in presentation.npc_refs(m, loaded.state, "preacher")
    SCRIPT.append({"text": "这次给你看看我拿到的原文。", "media": [{"ref": "handout:h_diary"}]})
    await loaded._reply(kept, "original", "preacher", lambda: cast.npc(loaded.state, "preacher", "原文也给我看看。"),
                        lambda _: None)
    assert "h_diary" in presentation.papers(loaded.state, None)
    assert "原文在玩家手里" in loaded._report(Turn(clues=[clue.id]))
    assert len(kept.sanity) == 1 and loaded.state.investigator.value("克苏鲁神话") == before_mythos + 2
    await loaded.close()


async def test_private_testimony_does_not_automatically_attach_its_underlying_handout(fake):
    game, cast = witness_table(fake)
    learn(game)
    who = game.state.investigator.id
    SCRIPT.append({"text": game.m.clue("c_diary").text, "told": ["c_diary"]})
    turn = Turn()
    said = await game._reply(turn, "private", "preacher", lambda: cast.npc(game.state, "preacher", "私下告诉我。", private=True),
                             lambda _: None, audience=[who])
    assert said.told == ["c_diary"] and not said.media
    assert not game.state.handouts and not game.state.presentations
    assert game.state.clues == ["c_diary"] and game.state.clue_settled["c_diary"] == [who]
    assert knowledge.sees(game.world.rec.facts["clue:c_diary"], who)
    await game.close()


@pytest.mark.parametrize("told", [False, True])
async def test_preparation_reply_is_its_actual_testimony_and_not_a_later_automatic_find(fake, told):
    game, _ = witness_table(fake)
    learn(game)
    clue = game.m.clue("c_diary")
    game.state.pending_turn = {"input": "问布道者日记的事", "initiative": [
        Initiative(npc="preacher", does="对调查员说起日记", first=True).model_dump()]}
    SCRIPT.append({"text": clue.text if told else "日记的事以后再说。", "told": [clue.id] if told else []})
    action = Action(kind="talk", summary="问布道者日记的内容", npc="preacher", speech="日记里写了什么？")
    await game.prepare(action)
    assert game.state.clues == ([clue.id] if told else [])
    turn = await game.resolve(action, Ruling(needs_roll=False, auto_clues=[clue.id]))
    assert game._asks(action, turn) == ""  # the preparation already answered; no second line is requested
    assert game.state.clues == ([clue.id] if told else []) and turn.clues == ([clue.id] if told else [])
    assert game.state.handouts == [] and game.state.presentations == []
    assert len(SEEN) == 1
    await game.close()


async def test_success_on_the_original_source_still_delivers_the_original_handout(fake):
    game, _ = setup(fake)
    fake.queue(1)
    action = Action(kind="talk", summary="请守钟人交出日记", npc="warden", speech="请让我看看日记。")
    turn = await game.resolve(action, Ruling(needs_roll=True, skill="说服", clues_on_success=["c_diary"]))
    assert turn.checks[0].passed and game.state.clues == ["c_diary"] and game.state.handouts == ["h_diary"]
    assert "原文在玩家手里" in game._report(turn)
    await game.close()


@pytest.mark.parametrize("original_source", [False, True])
async def test_explicit_failure_awards_its_clue_but_only_an_actual_original_source_supplies_the_handout(fake, original_source):
    game, _ = setup(fake, location="tower" if original_source else "square")
    if not original_source:
        learn(game)
    speaker = "warden" if original_source else "preacher"
    game.state.investigator.luck = 0
    action = Action(kind="talk", summary="交涉日记的事", npc=speaker, speech="请告诉我日记的内容。")
    ruling = Ruling(needs_roll=True, skill="说服", on_fail=Failure(text="交涉失败，但仍得到这条发现。", clues=["c_diary"]))
    fake.queue(99)
    turn = await game.resolve(action, ruling)
    assert turn.push is None and not turn.checks[0].passed
    assert game.state.clues == ["c_diary"] and turn.clues == ["c_diary"]
    assert not turn.npc_lines and not turn.replies and not SEEN  # the rule awarded it, nobody has spoken yet
    assert game.state.handouts == (["h_diary"] if original_source else [])
    assert ("c_diary" in game.state.npc("warden").unlocked) == original_source
    report = game._report(turn)
    assert ("原文在玩家手里" in report) == original_source
    assert ("【讲出发现】" in report) != original_source
    await game.close()


@pytest.mark.parametrize("original_source", [False, True])
async def test_failure_source_receipt_and_costs_survive_a_crash_after_the_clue_commit(fake, original_source):
    game, _ = setup(fake, location="tower" if original_source else "square")
    if not original_source:
        learn(game)
    st = game.state
    st.pending_turn = {"input": "交涉日记"}
    st.investigator.luck = 0
    game.m.clue("c_diary").sanity = "1/1"
    game.m.clue("c_diary").mythos = 2
    action = Action(kind="talk", summary="交涉日记", npc="warden" if original_source else "preacher", speech="说说日记。")
    ruling = Ruling(needs_roll=True, skill="说服", on_fail=Failure(
        text="仍取得这条发现，但耽搁并被碎片划伤。", clues=["c_diary"], minutes=17, damage="1D3"))
    start, san, hp = st.minutes, st.investigator.san, st.investigator.hp
    mythos = st.investigator.value("克苏鲁神话")
    saved: dict = {}

    async def disconnect(event: dict) -> None:
        if event["type"] == "sanity":
            saved["state"] = st.model_dump_json()
            raise RuntimeError("发现已入账后的断线")

    game.emit = disconnect
    fake.queue(99, 1)
    with pytest.raises(RuntimeError, match="断线"):
        await game.resolve(action, ruling)
    m = game.m.model_copy(deep=True)
    await game.close()
    state = GameState.model_validate_json(saved["state"])
    award = state.pending_turn["resolution"]["steps"]["fail-clue-awards"]["value"]
    assert award == {"clues": ["c_diary"], "originals": ["c_diary"] if original_source else []}
    assert state.minutes == start + 17 and state.investigator.san == san - 1
    assert state.investigator.value("克苏鲁神话") == mythos + 2
    assert state.handouts == (["h_diary"] if original_source else [])
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), m)
    loaded = Game(m, state, cast, Rules(dice=fake))
    fake.queue_totals(1)  # only the failure's not-yet-settled injury remains
    turn = await loaded.resolve(action, ruling)
    assert turn.push is None and len(turn.sanity) == 1 and loaded.state.clues == ["c_diary"]
    assert loaded.state.minutes == start + 17 and loaded.state.investigator.san == san - 1
    assert loaded.state.investigator.hp == hp - 1 and loaded.state.investigator.value("克苏鲁神话") == mythos + 2
    assert loaded.state.handouts == (["h_diary"] if original_source else [])
    assert not fake.values and not fake.totals and not SEEN
    await loaded.resolve(action, ruling)  # the completed receipt is harmless to replay too
    assert loaded.state.investigator.hp == hp - 1 and loaded.state.investigator.san == san - 1
    assert loaded.state.minutes == start + 17 and loaded.state.investigator.value("克苏鲁神话") == mythos + 2
    await loaded.close()


async def test_a_witness_can_finish_its_testimony_then_leave_but_remote_speech_cannot_grant_a_clue(fake):
    game, cast = witness_table(fake)
    learn(game)
    SCRIPT.append({"text": "日记里说，钟停了就要出事。我先去钟楼看看。", "told": ["c_diary"], "leave": "tower"})
    turn = Turn()
    await game._reply(turn, "departing", "preacher", lambda: cast.npc(game.state, "preacher", "日记怎么说？"),
                      lambda _: None)
    assert game.world.place("preacher") == "tower" and turn.replies["departing"].told == ["c_diary"]
    assert game.state.clues == ["c_diary"]
    await game.close()
    game, cast = witness_table(fake)
    learn(game)
    assert game.world.apply(O.Move(entity="preacher", to="tower")).ok
    SCRIPT.append({"text": "日记里说，钟停了就要出事。", "told": ["c_diary"]})
    turn = Turn()
    await game._reply(turn, "remote", "preacher", lambda: cast.npc(game.state, "preacher", "日记怎么说？"),
                      lambda _: None)
    assert turn.replies["remote"].told == [] and game.state.clues == []
    await game.close()


async def test_independent_npcs_may_privately_plan_to_destroy_similar_evidence(fake):
    game, cast = setup(fake, location="tower")
    intent = "烧毁房间里的旧信件，不让外人看见上面的署名。"
    motive = "不愿让人追查自己曾替旧主人送过信。"
    assert game.world.apply(O.AddFact(fact="warden_letters", text=intent, fact_kind="secret",
                                      about=["warden"], known_by=["warden"])).ok
    claim = "我会替你把这些旧信件好好收起来。"
    SCRIPT.append({"text": claim, "deceptions": [{"claim": claim, "intent": intent, "motive": motive}]})
    assert await cast.npc(game.state, "warden", "替我保管这些旧信件。") == claim
    assert game.world.apply(O.Move(entity="preacher", to="tower")).ok
    SCRIPT.append({"text": claim, "deceptions": [{"claim": claim, "intent": intent, "motive": motive}]})
    assert await cast.npc(game.state, "preacher", "替我保管这些旧信件。") == claim
    assert len(SEEN) == 2 and not cast.unsettled
    private = [f for f in game.world.rec.facts.values() if f.id.startswith("deception:") and f.kind == "secret"]
    assert any(intent in f.text and f.known_by == ["warden"] for f in private)
    assert any(intent in f.text and f.known_by == ["preacher"] for f in private)
    await game.close()


@pytest.mark.parametrize("npc_id", ["warden", "preacher"])
async def test_deception_still_rejects_unknown_basis_and_claims_not_actually_spoken(fake, npc_id):
    game, cast = setup(fake, location="tower")
    assert game.world.apply(O.Move(entity="preacher", to="tower")).ok
    other = "preacher" if npc_id == "warden" else "warden"
    assert game.world.apply(O.AddFact(fact="other_private", text="只有另一人知道的密信", fact_kind="secret", known_by=[other])).ok
    dialogue = Dialogue(text="信件我收好了。", deceptions=[{
        "claim": "信件已经烧掉了。", "intent": "烧掉信件。", "motive": "避免自己卷入。", "basis": ["other_private"],
    }])
    problems = cast._dialogue_problems(game.state, npc_id, dialogue, dialogue.text, {}, private=False)
    assert "欺骗依据引用了角色不知道的资料" in problems
    assert "欺骗说明必须逐字对应本次实际台词" in problems
    assert not any(f.id.startswith("deception:") for f in game.world.rec.facts.values())
    await game.close()
