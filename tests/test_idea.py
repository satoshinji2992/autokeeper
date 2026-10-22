"""The Idea roll (灵感检定, Keeper Rulebook pp. 82, 167–169), the push made a stated way (p. 73) and
the arbiter's difficulty with a basis (p. 72). Scripted agents and FakeDice, no model calls."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from autokeeper.agents.cast import ARBITER_SYSTEM, Action, Failure, Proposal, PushRuling, Ruling, opposing
from autokeeper.agents.director import DIRECTOR_SYSTEM, Improv, NewClue, NewEvent
from autokeeper.engine import autoplay, idea
from autokeeper.engine import graph as turn_graph
from autokeeper.engine.game import STALL_TURNS, Game, Turn, ai_choice, push_choice
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast, Improvising, make, run_turn
from .test_guide import canned  # noqa: F401  (a fixture)


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


def scholar(game: Game, intellect: int = 90) -> str:
    """A second investigator, the party's best mind."""
    st = game.state
    other = st.investigators[0].model_copy(deep=True, update={"id": "li", "name": "李雁"})
    other.characteristics = {**other.characteristics, "智力": intellect}
    st.investigators.append(other)
    return other.id


# -- which clue ---------------------------------------------------------------------------------
def test_the_most_crucial_missed_clue_is_core_first_then_for_the_nearest_conclusion(module, fake):
    game, *_ = make(module, fake, {})
    st = game.state
    assert idea.pick(module, st).id == "c_gears"  # core; supports k_tower, first in the module
    st.clues.append("c_gears")
    assert idea.pick(module, st).id == "c_archive"  # the other core clue
    module.clue("c_archive").requires = ["flag:archive_open"]  # not to be found yet
    assert idea.pick(module, st).id == "c_diary"  # no core left: k_tower is nearest (1 of 3 held)
    st.clues.append("c_diary")
    assert idea.pick(module, st).id == "c_notice"
    st.clues.append("c_notice")
    assert idea.pick(module, st) is None and game.idea_refused() == idea.NOTHING


def test_the_party_with_the_highest_intellect_rolls(module, fake):
    game, *_ = make(module, fake, {})
    assert idea.roller(game.state).id == game.state.investigator.id
    best = scholar(game)
    assert idea.roller(game.state).id == best != game.state.investigator.id


# -- how present it was -------------------------------------------------------------------------
def test_difficulty_follows_how_present_the_clue_already_was(module, fake):
    game, *_ = make(module, fake, {})
    st = game.state
    idea.note(module, st)
    assert idea.difficulty(st, "c_gears") == "常规"  # never mentioned
    st.add("keeper", "钟楼顶上的齿轮间里传来咔哒声。")
    idea.note(module, st)
    assert idea.difficulty(st, "c_gears") == "困难"  # mentioned once
    st.minutes += 2 * 1440
    assert idea.difficulty(st, "c_gears") == "常规"  # mentioned long ago
    for _ in range(idea.STRESSED):
        st.add("keeper", "齿轮间又响了一声。")
    idea.note(module, st)
    assert idea.difficulty(st, "c_gears") == "极难"  # stressed
    idea.note(module, st)
    assert st.mentions["c_gears"].told == idea.STRESSED + 1  # counting again adds nothing
    st.add("player", "我想看看报社里有什么旧报合订本")
    idea.note(module, st)
    assert idea.difficulty(st, "c_archive") == "极难"  # the players discussed it themselves
    st.visited.append("tower")
    idea.note(module, st)
    assert idea.presence(st, "c_diary").mentioned  # they were where the diary's keeper is


# -- the roll -----------------------------------------------------------------------------------
async def test_a_successful_idea_roll_gives_the_clue_as_the_investigators_own_thought(module, fake):
    game, cast, _events = make(module, fake, {})
    roller = scholar(game)
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(30, 10)  # INT 90, regular: success; the gears' sanity check passes
    out, interrupts = await run_turn(app, "/灵感", {"configurable": {"thread_id": "i1"}})
    st = game.state
    assert not interrupts and st.clues == ["c_gears"]
    dice = next(e for e in out if e["type"] == "dice")
    assert dice["skill"] == "灵感" and dice["who"] == "李雁" and dice["target"] == 90 and dice["difficulty"] == "常规"
    assert st.ideas[-1].passed and st.ideas[-1].clue == "c_gears" and st.ideas[-1].day == 0
    report = cast.reports[-1]
    assert "灵感检定成功" in report and "迂回地融入" in report and "获得线索〔齿轮间〕" in report
    assert "此前没有出现过" in report  # never mentioned: the keeper needs a way in for it
    assert st.investigators[1].id == roller and st.minutes == Game.new(module, [module.pregens[0]]).minutes  # no time lost
    assert game.idea_refused() == idea.TODAY  # once a game day
    st.minutes += 1440
    assert game.idea_refused() == ""


async def test_a_player_throws_the_idea_roll_for_the_best_mind(module, fake):
    game, *_ = make(module, fake, {})
    scholar(game)
    app = turn_graph.build(game, InMemorySaver())
    thread = {"configurable": {"thread_id": "i2"}}
    out = []
    async for _mode, chunk in app.astream({"input": "/灵感", "by_hand": True}, thread, stream_mode=["custom", "updates"]):
        if "__interrupt__" in chunk:
            out.append(chunk["__interrupt__"][0].value)
    ask = out[0]
    assert ask["kind"] == "roll" and ask["who"] == "李雁" and ask["skill"] == "灵感" and ask["target"] == 90
    assert ask["why"] == idea.BASIS["常规"] and "齿轮" not in str(ask)  # nothing about the clue itself


async def test_a_failed_idea_roll_still_gives_the_clue_and_cuts_to_the_worst_situation(module, fake):
    cut = Improv(kind="千钧一发", reason="钟楼里正在出事", go="tower",
                 events=[NewEvent(id="e_cut", title="齿轮间的动静", when="now", read_aloud="齿轮间的门在你们面前猛地关上。")],
                 note="守钟人锁上了齿轮间的门")
    game, cast, _ = make(module, fake, {})
    game.cast = cast = Improvising({}, [cut])
    start = game.state.minutes
    fake.queue(99, 10)  # INT 75: a failure; the gears' sanity check passes
    turn = await game.resolve(game.idea_action(), Ruling(needs_roll=True, skill="灵感"))
    st = game.state
    assert cast.asked[0][0] == "千钧一发" and st.location == "tower" and "e_cut" in st.fired
    assert st.minutes - start >= idea.LOST and "c_gears" in st.clues and not st.ideas[-1].passed
    notes = "\n".join(turn.notes)
    assert "千钧一发" in notes and "导演的安排：守钟人锁上了齿轮间的门" in notes and "〔齿轮间〕" in notes


async def test_the_cut_hands_over_the_missed_clue_and_nothing_the_director_gives(module, fake):
    """The director is told to leave give empty in 千钧一发 (the clue is the engine's); what it gives
    anyway is not handed over (the review of 2026-10-22: _idea still processed it)."""
    cut = Improv(kind="千钧一发", reason="钟楼里正在出事", go="tower", give=["c_cut_note"],
                 clues=[NewClue(id="c_cut_note", title="门上的划痕", text="齿轮间门上有新的划痕。", where="tower")],
                 events=[NewEvent(id="e_cut", title="齿轮间的动静", when="now", read_aloud="齿轮间的门在你们面前猛地关上。")])
    game, *_ = make(module, fake, {})
    game.cast = Improvising({}, [cut])
    fake.queue(99, 10)
    turn = await game.resolve(game.idea_action(), Ruling(needs_roll=True, skill="灵感"))
    assert game.state.location == "tower" and "e_cut" in game.state.fired
    assert turn.clues == ["c_gears"] and "c_cut_note" not in game.state.clues


async def test_nothing_overlooked_is_said_plainly_and_spends_no_turn(module, fake):
    game, cast, _ = make(module, fake, {})
    game.state.clues = [c.id for c in module.clues]
    app = turn_graph.build(game, InMemorySaver())
    out, _ = await run_turn(app, "/灵感", {"configurable": {"thread_id": "i3"}})
    assert game.state.turn == 0 and not cast.reports and not game.state.ideas
    assert any(e["type"] == "command" and e["text"] == idea.NOTHING for e in out)


async def test_a_stall_offers_the_idea_roll_instead_of_a_silent_lead(module, fake):
    wait = Action(kind="wait", summary="周衡在广场上发呆")
    game, cast, events = make(module, fake, {"x": (wait, Ruling(needs_roll=False, minutes=5))})
    game.cast = cast = Improvising({"x": (wait, Ruling(needs_roll=False, minutes=5))}, [])
    st = game.state
    st.turn = STALL_TURNS
    await game.resolve(wait, Ruling(needs_roll=False, minutes=5))
    assert cast.asked == [] and st.idea_offer == STALL_TURNS and st.progress_at == STALL_TURNS
    assert any(e["type"] == "idea_offer" for e in events)
    assert "/灵感" in autoplay.situation(module, st)  # an AI player decides for itself
    st.ideas.append(idea.IdeaRoll(turn=1, day=0, clue="c_notice", difficulty="常规", passed=True))
    assert game.idea_refused() == ""  # offered: not held back by today's roll
    action = game.idea_action()
    assert action.idea["offered"]
    fake.queue(10, 10)
    await game.resolve(action, Ruling(needs_roll=True, skill="灵感"))
    assert st.idea_offer == 0 and st.ideas[-1].offered and game.idea_refused() == idea.TODAY


# -- pushing a stated way ------------------------------------------------------------------------
class Arbiter(FakeCast):
    def __init__(self, script, ruling: PushRuling):
        super().__init__(script)
        self.pushes: list[tuple[dict, str]] = []
        self.push_ruling = ruling

    async def repush(self, state, pending, how):
        self.pushes.append((pending, how))
        return self.push_ruling


LOOK = Action(kind="investigate", summary="周衡翻旧报", target="旧报合订本")
ARCHIVE = Ruling(needs_roll=True, skill="图书馆使用", clues_on_success=["c_archive"], minutes=30,
                 on_push_fail=Failure(text="管理员来赶人"))


def test_a_push_always_says_how():
    assert push_choice("luck") == ("luck", "") and push_choice({"choice": "push", "how": " 一页页 翻 "}) == ("push", "一页页 翻")
    assert push_choice({"choice": "drop", "how": "随便"}) == ("drop", "")
    for bad in ("again", "push", {"choice": "push", "how": "  "}):  # a bare push came only from old tapes
        with pytest.raises(ValueError):
            push_choice(bad)


async def test_a_push_says_how_and_its_time_passes_before_the_roll(module, fake):
    risk = Failure(text="翻得太狠，合订本散了架，管理员把你请出了报社", minutes=20)
    game, *_ = make(module, fake, {}, location="office")
    game.cast = cast = Arbiter({}, PushRuling(achievable=True, minutes=90, on_push_fail=risk))
    app = turn_graph.build(game, InMemorySaver())
    game.cast.script = {"look": (LOOK, ARCHIVE)}
    fake.queue(90, 95)  # fails, then the pushed roll fails too
    start = game.state.minutes
    out, interrupts = await run_turn(app, "look", {"configurable": {"thread_id": "p1"}},
                                     resume={"choice": "push", "how": "把三十年前那一年的合订本一页页全部翻完"})
    st = game.state
    assert interrupts[0]["action"] == "周衡翻旧报"
    assert cast.pushes[0][1] == "把三十年前那一年的合订本一页页全部翻完"
    assert st.minutes - start == 30 + 90 + 20  # the search, the push's own time, then its failure
    assert "c_archive" not in st.clues and any("孤注一掷：把三十年前" in e.text for e in st.log if e.kind == "player")
    outcome = [e for e in out if e["type"] == "outcome"][-1]
    assert "合订本散了架" in outcome["lines"][0]  # the failure fitting this way, not the general one


async def test_a_push_that_cannot_reach_the_goal_is_refused_and_nothing_is_rolled(module, fake):
    game, *_ = make(module, fake, {}, location="office")
    game.cast = cast = Arbiter({}, PushRuling(achievable=False, why="合订本已经被人借走了。", minutes=0))
    fake.queue(90)
    await game.resolve(LOOK, ARCHIVE)
    turn = await game.push({"choice": "push", "how": "去隔壁街再找一份"})
    st = game.state
    assert not fake.values and len([e for e in st.log if e.kind == "dice"]) == 1  # no second roll
    assert st.pending_push is None and "c_archive" not in st.clues and len(cast.pushes) == 1
    assert any("孤注一掷不成立（去隔壁街再找一份）：合订本已经被人借走了" in n for n in turn.notes)


def test_an_ai_player_answers_a_push_offer_the_way_it_said_it_would_push():
    offer = {"can_push": True, "luck": 12}
    assert ai_choice(offer, " 把合订本 一页页翻完 ") == {"choice": "push", "how": "把合订本 一页页翻完"}
    assert ai_choice(offer, "") == "luck"  # it said no way: no bare push
    assert ai_choice({"can_push": True, "luck": None}) == "drop"
    assert ai_choice({"can_push": False, "luck": None}, "再翻一遍") == "drop"  # a push not allowed stays so


async def test_the_ai_investigator_says_its_action_and_how_it_would_push_it(module, monkeypatch):
    from autokeeper import config
    from autokeeper.agents.cast import INVESTIGATOR_SYSTEM, Cast
    from autokeeper.harness import Reply

    cast = Cast(config.Config(), module)
    replies = [Reply(text="", data={"text": " 我翻旧报 ", "push": " 把那一年的 合订本全翻完 "})]

    async def send(key, session, state, prompt, **kw):
        assert session.schema == Proposal.model_json_schema()
        return replies.pop(0)

    monkeypatch.setattr(cast, "_send", send)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    inv = state.investigators[0].id
    assert await cast.investigator(state, inv, "") == Proposal(text="我翻旧报", push="把那一年的 合订本全翻完")
    assert "push 写：这件事如果要检定而失败了，你打算怎样孤注一掷" in INVESTIGATOR_SYSTEM


async def test_an_ai_investigators_answer_that_is_no_action_goes_back_once_and_nothing_is_made_up(module, monkeypatch):
    """The review of 2026-10-22: an empty answer became 「我四下看看。」 and a second line was cut off.
    Now an answer that is no action goes back once with what is wrong; still none, it goes to the debug
    journal and the seat makes no move (None)."""
    from autokeeper import config
    from autokeeper.agents.cast import Cast
    from autokeeper.harness import Reply

    cast = Cast(config.Config(), module)
    journal: list[tuple[str, dict]] = []
    cast.debug_journal = type("Journal", (), {"append": lambda self, kind, data, turn=0: journal.append((kind, data))})()
    replies = [Reply(text="", data={"text": "我翻旧报\n然后去钟楼"}), Reply(text="", data={"text": "我去钟楼"}),
               Reply(text="", data={"text": ""}), Reply(text="", data={"text": "  "})]
    fixes: list[str] = []

    async def send(key, session, state, prompt, **kw):
        fixes.append(kw.get("extra", ""))
        return replies.pop(0)

    monkeypatch.setattr(cast, "_send", send)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    inv = state.investigators[0].id
    assert await cast.investigator(state, inv, "") == Proposal(text="我去钟楼")  # the whole answer, sent back once
    assert fixes[0] == "" and "不分行" in fixes[1] and "我翻旧报\\n然后去钟楼" in fixes[1]
    assert await cast.investigator(state, inv, "") is None  # no action after one resend: none made up
    assert not replies and journal[-1][0] == "investigator" and journal[-1][1]["phase"] == "invalid"


async def test_in_a_fight_an_ai_players_answer_sets_how_it_answers_blows(module, fake, monkeypatch):
    """The review: AI players only had the stored answer to blows (a dodge) since it left the option words.
    The same answer may set it in a fight, one a player's Tab could set (views.stances); one it cannot
    goes back once; outside a fight it is dropped. The table lands it as Tab does (set_react)."""
    from autokeeper import config
    from autokeeper.agents.cast import Cast
    from autokeeper.engine.encounter import views
    from autokeeper.harness import Reply

    from .encounter_kit import begin, fight_game

    module.events, module.endings = [], []
    game, _, _ = fight_game(module, fake)
    await begin(game, ["warden"])
    inv = game.state.investigator.id
    stances = views.stances(game, inv)
    assert stances == ["dodge", "fight_back"]  # no foe with a gun: no diving for cover
    cast = Cast(config.Config(), module)
    replies = [Reply(text="", data={"text": "防守", "react": "cover"}), Reply(text="", data={"text": "防守", "react": "fight_back"}),
               Reply(text="", data={"text": "我去钟楼", "react": "fight_back"})]
    fixes: list[str] = []

    async def send(key, session, state, prompt, **kw):
        fixes.append(kw.get("extra", ""))
        return replies.pop(0)

    monkeypatch.setattr(cast, "_send", send)
    move = await cast.investigator(game.state, inv, "", stances=stances)
    assert move == Proposal(text="防守", react="fight_back") and "react 填的 cover" in fixes[1]
    assert game.set_react(inv, move.react) == "" and game.state.encounter.get(inv).react == "fight_back"
    assert await cast.investigator(game.state, inv, "") == Proposal(text="我去钟楼")  # no fight: nothing to set


async def test_the_party_proposes_with_each_ones_answers_to_blows(module):
    """Autoplay's fan-out (engine/autoplay.party_graph) asks each with the answers to blows it may set,
    and hands back what it set with its move."""
    from autokeeper.engine.autoplay import party_graph

    asked: dict = {}

    class Party:
        async def investigator(self, state, inv_id, situation, *, stances=None):
            asked[inv_id] = stances
            return Proposal(text="防守", react="fight_back" if stances else "")

    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    inv = state.investigators[0].id
    out = await party_graph(Party(), state).ainvoke({"situation": "", "ids": [inv], "proposals": [],  # type: ignore[arg-type]
                                                     "stances": {inv: ["dodge", "fight_back"]}})
    assert out["proposals"] == [(inv, "防守", "", "fight_back")] and asked == {inv: ["dodge", "fight_back"]}


async def test_an_ai_players_stated_push_is_ruled_again_and_takes_its_time(module, fake):
    game, *_ = make(module, fake, {}, location="office")
    game.cast = cast = Arbiter({"look": (LOOK, ARCHIVE)}, PushRuling(achievable=True, minutes=60))
    app = turn_graph.build(game, InMemorySaver())
    fake.queue(90, 3)
    start = game.state.minutes
    thread = {"configurable": {"thread_id": "ai-push"}}
    _out, interrupts = await run_turn(app, "look", thread)
    choice = ai_choice(interrupts[0], "把那一年的合订本一页页全部翻完")
    async for _ in app.astream(Command(resume=choice), thread, stream_mode=["custom", "updates"]):
        pass
    st = game.state
    assert cast.pushes[0][1] == "把那一年的合订本一页页全部翻完" and "c_archive" in st.clues
    assert st.minutes - start == 30 + 60


# -- the arbiter's basis --------------------------------------------------------------------------
def test_the_arbiter_is_told_where_difficulty_comes_from_and_what_core_clues_mean():
    assert "低于 50 常规，50 及以上困难，90 及以上极难" in ARBITER_SYSTEM
    assert "不因为调查员自己的技能高就提高难度" in ARBITER_SYSTEM and "准备充分" in ARBITER_SYSTEM
    assert "【关键】" in ARBITER_SYSTEM and "灵感检定" in ARBITER_SYSTEM
    assert "不受伤、不掉理智，on_fail 多数时候留空" in ARBITER_SYSTEM
    assert "千钧一发" in DIRECTOR_SYSTEM


def test_the_opposing_values_come_from_the_sheet_or_an_ordinary_person(module):
    warden = module.npc("warden")
    assert "心理学 10（常规）" in opposing(warden) and "模组没写数值" in opposing(warden)
    warden.stats = {"心理学": 65, "意志": 90}
    shown = opposing(warden)
    assert shown.startswith("老守钟人(warden)：心理学 65（困难）、意志 90（极难）") and "侦查 25（常规）" in shown


async def test_the_arbiters_prompt_marks_core_clues_and_the_opponents_values(module, monkeypatch):
    from autokeeper import config
    from autokeeper.agents.cast import Cast
    from autokeeper.harness import Reply

    cast = Cast(config.Config(), module)
    seen: list[str] = []

    async def send(key, session, state, prompt, **kw):
        seen.append(prompt)
        return Reply(text="", data=Ruling(needs_roll=False).model_dump())

    monkeypatch.setattr(cast, "_send", send)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.location = "tower"
    talk = Action(kind="talk", summary="周衡劝守钟人说实话", npc="warden", approach="说服")
    await cast.arbiter(state, talk)
    assert "c_gears「齿轮间」【关键】" in seen[0] and "【对方的相应数值】老守钟人(warden)：" in seen[0]
    await cast.close()


async def test_old_saves_load_with_the_new_fields(module, fake):
    game, *_ = make(module, fake, {})
    raw = game.state.model_dump(mode="json")
    for key in ("mentions", "mentions_at", "ideas", "idea_offer"):
        raw.pop(key)
    state = type(game.state).model_validate(raw)
    assert state.mentions == {} and state.ideas == [] and state.idea_offer == 0
    Game(module, state, FakeCast({}), Rules(dice=fake))  # type: ignore[arg-type]
    assert isinstance(Turn(), Turn)


# -- at a shared table -------------------------------------------------------------------------
async def test_a_seated_player_pushes_saying_how_and_asks_for_an_idea_roll(monkeypatch, tmp_path, skills_dir, fake):
    import aiohttp

    from autokeeper import paths
    from autokeeper.net.hub import Hub

    from .test_net import NoticeCast, closed, opened, pool_open, until

    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    hub = Hub(EXAMPLE, seats=["玩家"], cast=NoticeCast(), rules=Rules(dice=fake))
    task = await opened(hub)
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"ws://127.0.0.1:{hub.port}/ws")
        await ws.send_json({"t": "hello", "name": "小明"})
        await until(ws, lambda x: x["t"] == "welcome")
        await ws.send_json({"t": "sit", "investigator": "zhouheng"})
        await ws.send_json({"t": "ready"})
        await until(ws, pool_open)
        fake.queue(90, 30)  # the notice is missed, then the push finds it
        await ws.send_json({"t": "act", "text": "我看看告示栏"})
        await ws.send_json({"t": "done"})
        roll = (await until(ws, lambda x: x["t"] == "offer"))["offer"]
        await ws.send_json({"t": "respond", "id": roll["respond_id"], "choice": "roll"})
        push = (await until(ws, lambda x: x["t"] == "offer"))["offer"]
        assert push["kind"] == "push" and push["can_push"]
        before = hub.state.minutes
        await ws.send_json({"t": "respond", "id": push["respond_id"], "choice": {"choice": "push", "how": "搬来椅子站上去，凑近了一行行看"}})
        await until(ws, lambda x: x["t"] == "event" and x["ev"]["type"] == "outcome" and "广场告示" in "".join(x["ev"]["lines"]))
        assert "c_notice" in hub.state.clues and hub.state.minutes - before >= 10  # its time passed first
        assert any("孤注一掷：搬来椅子" in e.text for e in hub.state.log)
        sync = await until(ws, lambda x: x["t"] == "sync")
        assert "mentions" not in sync["state"]  # the clues still to find stay on the host
        await until(ws, pool_open)
        hub.state.clues = [c.id for c in hub.m.clues]
        await ws.send_json({"t": "act", "text": "/灵感"})
        refused = await until(ws, lambda x: x["t"] == "error")
        assert refused["text"] == idea.NOTHING
        await ws.close()
    await closed(task)


async def test_the_push_dialog_asks_how_before_it_pushes():
    from textual.app import App
    from textual.widgets import Button, Input

    from autokeeper.tui.modals.dialogs import PushModal

    offer = {"kind": "push", "skill": "图书馆使用", "target": 60, "need": 60, "value": 63, "can_push": True, "luck": 3, "left": 52,
             "risk": "管理员下班锁门"}
    chosen: list = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(PushModal(offer), chosen.append)

    app = Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        assert "如何行动以孤注一掷" in "".join(str(w.render()) for w in modal.query("Static"))
        modal.query_one("#push", Button).press()
        await pilot.pause()
        assert chosen == [] and app.screen is modal  # no way said: not yet
        modal.query_one("#push-how", Input).value = "  把整排书架 逐本翻完 "
        modal.query_one("#push", Button).press()
        await pilot.pause()
    assert chosen == [{"choice": "push", "how": "把整排书架 逐本翻完"}]


async def test_ctrl_l_asks_for_an_idea_roll_on_the_game_screen(canned, fake):  # noqa: F811
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.dialogs import RollModal

    from .test_guide import open_game, until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot, resume=True)
        screen.game.rules = Rules(dice=fake)
        fake.queue(20, 10)  # INT 75: a success; the gears' sanity check passes
        await pilot.press("ctrl+l")
        assert await until(pilot, lambda: isinstance(app.screen, RollModal))
        assert app.screen.ask["skill"] == "灵感" and app.screen.ask["who"] == "周衡"
        await pilot.pause(0.5)
        await pilot.press("enter")
        assert await until(pilot, lambda: app.screen is screen and not screen.busy)
        assert screen.state.clues == ["c_gears"] and screen.state.ideas
        await pilot.press("ctrl+l")  # once a game day
        await pilot.pause(0.2)
        assert app.screen is screen and len(screen.state.ideas) == 1
