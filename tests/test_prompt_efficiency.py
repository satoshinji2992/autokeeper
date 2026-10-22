"""Shared prompt material is sent once while facts, sources and original dialogue survive."""

from __future__ import annotations

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, PartyInput, _book, _recent
from autokeeper.engine.game import Game
from autokeeper.harness import Reply
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import context, knowledge
from autokeeper.world.ops import AddFact, Occur


def game_context(fake):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(update={"id": "zhouheng", "name": "周衡"}, deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    module.location("tower").read_aloud = "现场底稿完整句。" * 60 + "现场底稿结束。"
    state = Game.new(module, [lead, friend], human=[lead.id, friend.id])
    state.location, state.turn, state.narrator_context_version = "tower", 20, 1
    state.npc("warden").met = True
    cast = Cast(config.Config(imagegen=False, showcase=False), module)
    game = Game(module, state, cast, Rules(dice=fake))
    game.write_progress = False
    return game, cast


def watch(monkeypatch, cast, data=None):
    sent = []

    async def send(key, session, state, prompt, **kwargs):
        sent.append({"role": key, "system": session.system, "prompt": prompt, "extra": kwargs.get("extra", "")})
        return Reply(text="已经结算的场面。", data=data)

    monkeypatch.setattr(cast, "_send", send)
    return sent


async def test_a_batch_sends_each_shared_scene_and_original_exchange_once_with_less_input(fake, monkeypatch):
    game, cast = game_context(fake)
    state = game.state
    quotes = []
    for index in range(8):
        quote = f"原始对话第{index}段：“" + "先听清对方完整说法，再决定如何做。" * 12 + f"”第{index}段结束。"
        quotes.append(quote)
        state.add("npc", quote, who="老守钟人")
    batch = [PartyInput(token="a", who="zhouheng", text="我查看齿轮"),
             PartyInput(token="b", who="lin", text="我替周衡举灯"),
             PartyInput(token="c", who="zhouheng", text="接着查看铭文")]
    # The old composition repeated the whole actor context for each action, after a
    # separate full recent window. This conservative baseline omits its task preamble.
    previous = _recent(state)
    for item in batch:
        view = state.model_copy(update={"active": 0 if item.who == "zhouheng" else 1})
        previous += cast._heard(view, item.text)
    sent = watch(monkeypatch, cast, {"steps": [
        {"token": item.token, "plan": {"action": {"kind": "investigate", "summary": "查看眼前事物"}}} for item in batch
    ]})
    assert await cast.party_plan(state, batch) is not None
    prompt = sent[0]["prompt"]
    assert len(prompt) < len(previous) * 0.7
    assert all(prompt.count(quote) == 1 for quote in quotes)
    assert prompt.count(game.m.location("tower").read_aloud) == 1
    assert all(prompt.count(item.text) == 1 and item.token in prompt for item in batch)
    assert game.m.truth.strip() in sent[0]["system"] and _book(game.m) in sent[0]["system"]
    await game.close()


async def test_shared_windows_keep_each_authors_own_private_exchange_when_another_is_busy(fake, monkeypatch):
    game, cast = game_context(fake)
    state = game.state
    personal = "周衡独自听到的完整敲门节奏：三声、停顿、两声。"
    state.add("keeper", personal, audience=["zhouheng"])
    others = []
    for index in range(12):
        quote = f"林秋单独收到第{index}条纸条，内容不应挤掉周衡的对话。"
        others.append(quote)
        state.add("keeper", quote, audience=["lin"])
    sent = watch(monkeypatch, cast, {"steps": [
        {"token": token, "plan": {"action": {"kind": "investigate", "summary": "查看现场"}}} for token in ("a", "b")
    ]})
    await cast.party_plan(state, [PartyInput(token="a", who="zhouheng", text="我继续听"),
                                  PartyInput(token="b", who="lin", text="我读纸条")])
    prompt = sent[0]["prompt"]
    assert prompt.count(personal) == 1 and "私密收件人：周衡" in prompt
    assert all(prompt.count(quote) == 1 for quote in others[-10:])
    assert "私密收件人：林秋" in prompt
    await game.close()


async def test_long_private_messages_keep_each_participants_original_window_budget(fake):
    game, _ = game_context(fake)
    state = game.state
    personal = "周衡独自听到三声敲门，仍在等对方答复。"
    state.add("keeper", personal, audience=["zhouheng"])
    others = []
    for index in range(10):
        start, end = f"林秋第{index}封长纸条开头：", f"第{index}封纸条结束。"
        quote = start + "字" * (4000 - len(start) - len(end)) + end
        others.append(quote)
        state.add("keeper", quote, audience=["lin"])
    first = _recent(state, viewer="zhouheng", keeper=False)
    common = _recent(state, viewers=("zhouheng", "lin"))
    assert personal in first and common.count(personal) == 1
    assert others[-1] in common and others[-2] in common and others[0] not in common
    assert "私密收件人：周衡" in common and "私密收件人：林秋" in common
    # The same long B window is shared once, rather than enlarging a combined budget
    # enough to fill three full windows before considering A's older private message.
    assert len(common) < len(_recent(state)) + len(first) + 100
    await game.close()


async def test_overlapping_windows_prefer_the_full_original_even_if_an_omission_marker_is_longer(fake):
    game, _ = game_context(fake)
    state = game.state
    text = "共同观察的开头。" + "完整原文中的中间内容。" * 12 + "共同观察的结尾。"
    state.add("keeper", text)
    private = "林秋的私有长信：" + "字" * (10000 - len(text) + 1 - len("林秋的私有长信："))
    state.add("keeper", private, audience=["lin"])
    # The global/B windows have one character less than needed for the first entry;
    # their rendered omission marker makes the shortened string longer than the original.
    assert "〔中段省略〕" in _recent(state)
    assert text in _recent(state, viewer="zhouheng", keeper=False)
    shared = _recent(state, viewers=("zhouheng", "lin"))
    assert shared.count(text) == 1 and shared.count(private) == 1 and "〔中段省略〕" not in shared
    await game.close()


async def test_equal_utterances_are_separate_log_entries_when_windows_are_merged(fake):
    game, _ = game_context(fake)
    text = "调查员又问了一遍，守钟人仍然答应在门口等。"
    game.state.add("npc", text, who="老守钟人")
    game.state.add("npc", text, who="老守钟人")
    assert _recent(game.state).count(text) == 2
    assert _recent(game.state, viewers=("zhouheng", "lin")).count(text) == 2
    await game.close()


async def test_distinct_scene_material_is_not_merged_or_hidden_when_sharing_batch_context(fake, monkeypatch):
    game, cast = game_context(fake)
    state = game.state
    state.apart["lin"] = "square"
    game.m.location("square").read_aloud = "广场单独的完整场景，警员站在报摊旁。"
    sent = watch(monkeypatch, cast, {"steps": [
        {"token": token, "plan": {"action": {"kind": "investigate", "summary": "查看各自现场"}}} for token in ("a", "b")
    ]})
    await cast.party_plan(state, [PartyInput(token="a", who="zhouheng", text="我查塔楼"),
                                  PartyInput(token="b", who="lin", text="我看报摊")])
    prompt = sent[0]["prompt"]
    assert prompt.count(game.m.location("tower").read_aloud) == 1
    assert prompt.count(game.m.location("square").read_aloud) == 1
    assert "【共用场景 tower】" in prompt and "【共用场景 square】" in prompt
    assert "地点：钟楼(tower)，使用【共用场景 tower】" in prompt
    assert "地点：广场(square)，使用【共用场景 square】" in prompt
    await game.close()


async def test_keeper_plan_and_narration_keep_full_truth_local_rules_and_current_rewrites(fake, monkeypatch):
    game, cast = game_context(fake)
    rules = "本地规则必须从头到尾提供。" * 300 + "末尾规则：失败后保留痕迹。"
    game.m.location("tower").keeper = rules
    game.world.rec.facts["location:tower:keeper"].text = rules
    sent = watch(monkeypatch, cast, {"action": {"kind": "investigate", "summary": "查看齿轮"}})
    await cast.plan(game.state, "我看看齿轮")
    await cast.keeper(game.state, "原始裁定结果与当面对话。", lore=game._brief())
    for row in sent:
        whole = row["system"] + row["prompt"] + row["extra"]
        assert _book(game.m) in whole and game.m.truth.strip() in whole
        assert rules in whole and "末尾规则：失败后保留痕迹。" in whole
    await game.close()


async def test_npc_clue_and_learned_knowledge_use_scoped_source_references_without_repeating_full_text(fake, monkeypatch):
    game, cast = game_context(fake)
    npc = game.m.npc("warden")
    cid = npc.clues[0]
    clue = game.m.clue(cid)
    clue.text = "守钟人自己知道的完整旧事：" + "那天下午他看见钟锤偏离原位。" * 40 + "原作条件仍须满足。"
    game.world.rec.facts[f"clue:{cid}"].text = clue.text
    learned = "调查员当面告诉我且我已记下的内容：" + "铜匣由旅店老板收下，尚未转交给别人。" * 9
    assert game.world.apply(AddFact(id="learned-budget", actor="keeper", fact="learned-budget", text=learned,
                                    known_by=["warden"], fact_kind="knows", visibility="private")).ok
    game.world.sync()
    standard = context.npc_brief(game.world, "warden", query="铜匣")
    fact = game.world.rec.facts["learned-budget"]
    covered = {f"clue:{cid}": f"角色资料的线索 {cid}", fact.id: "【你在本局新知道的】"}
    compact = context.npc_brief(game.world, "warden", query="铜匣", covered_facts=covered)
    assert len(compact) < len(standard)
    assert knowledge.public_ref(fact, "warden") in compact and "来自亲历或当面获知的记录" in compact
    assert "见【你在本局新知道的】中的原文" in compact
    sent = watch(monkeypatch, cast, {"text": "我记得您说过铜匣的事。"})
    await cast.npc(game.state, "warden", "铜匣现在由谁拿着？")
    whole = sent[0]["system"] + sent[0]["prompt"] + sent[0]["extra"]
    assert whole.count(clue.text) == 1
    assert learned in whole and f"线索 {cid}" in whole and "本局新知道的" in whole
    assert "原有知识" in whole and "来自亲历或当面获知的记录" in whole
    assert game.m.truth.strip() not in whole
    await game.close()


async def test_npc_event_sources_survive_once_instead_of_repeating_a_second_recent_block(fake):
    game, _ = game_context(fake)
    text = "我亲眼看见铜钥匙交到旅店老板手里，但不知道他之后做了什么。"
    assert game.world.apply(Occur(id="observed-budget", actor="warden", participants=["warden"],
                                  place="tower", text=text, visibility="private")).ok
    brief = context.npc_brief(game.world, "warden", query="铜钥匙")
    view = context.npc_view(game.world, "warden", query="铜钥匙").text
    assert brief.count(text) == 1 and view.count(text) == 1
    assert "记录者：老守钟人" in brief and "亲历或当面获知" in brief
    assert text not in context.npc_brief(game.world, "preacher", query="铜钥匙")
    await game.close()


async def test_the_arbiter_reads_a_performance_once_without_losing_the_full_local_rules(fake, monkeypatch):
    game, cast = game_context(fake)
    speech = "完整演出的原话：" + "说书人把亲历、传闻与推测分开讲清。" * 100 + "演出到这里结束。"
    rules = "本地完整裁定规则：" + "检定条件和失败结果都必须保留。" * 80
    game.m.location("tower").keeper = rules
    sent = watch(monkeypatch, cast, {"needs_roll": False, "minutes": 1})
    await cast.arbiter(game.state, Action(kind="perform", summary="周衡讲述亲历的旧事", speech=speech))
    prompt = sent[0]["prompt"]
    assert prompt.count(speech) == 1 and rules in prompt
    assert "行动里的 speech 原文" in prompt and "不改变难度" in prompt
    await game.close()
