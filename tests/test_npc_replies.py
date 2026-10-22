"""An NPC reply settles in one place (engine/game.Game._reply), whatever asked for it: the one spoken
to, one who steps in before the ruling, one spoken to before the journey, one joining in, a performer.
Its receipt (Said) is kept with the turn before anything is shown, so a turn resumed after a crash
goes on from it: the NPC is not asked again, its words are shown, and the clues it told
(Dialogue.told) reach the notebook once (the review of 2026-10-21). Scripted casts, FakeDice."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, BeforeTravel, Initiative, Ruling
from autokeeper.engine.game import TURN_RECORD, Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Event, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import ops as O

from .conftest import FakeDice
from .test_engine import FakeCast

DIARY = "日记里写着，它在钟里很安静，只要钟一直走。"
ASK = Action(kind="talk", summary="周衡问守钟人日记里写了什么", npc="warden", speech="日记里写了什么？")
LOOK = Action(kind="investigate", summary="周衡看看钟楼", target="齿轮间")


class Teller(FakeCast):
    """A FakeCast whose NPCs tell the clues `tells` gives them, handed over as Cast.npc hands them
    (Cast.told), and walk off where `leaves` says, as a reply's leave lands (Dialogue.leave)."""

    def __init__(self, tells: dict[str, list[str]], leaves: dict[str, str] | None = None):
        super().__init__({})
        self.tells, self.leaves = tells, leaves or {}
        self.told: dict[str, list[str]] = {}

    async def npc(self, state, npc_id, message, **kw):
        self.npc_calls.append((npc_id, message))
        self.told[npc_id] = list(self.tells.get(npc_id, []))
        if to := self.leaves.get(npc_id):
            assert self.world is not None
            self.world.apply(O.Move(id=f"leave:{state.turn}:{npc_id}", actor=npc_id, entity=npc_id, to=to))
        return DIARY if self.told[npc_id] else f"{npc_id} 的回答"


def table(fake, *, state: GameState | None = None, module: Module | None = None, leaves: dict[str, str] | None = None):
    """The tower, the warden with his diary unlocked (his to tell, a thing said rather than a paper
    handed over), and a cast that tells it."""
    m = module or Module.load(EXAMPLE)
    m.clue("c_diary").handout = None
    if state is None:
        state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
        state.location, state.turn = "tower", 1
        state.npc("warden").unlocked = ["c_diary"]
    cast = Teller({"warden": ["c_diary"]}, leaves)
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    return Game(m, state, cast, Rules(dice=fake), emit=emit), cast, events  # type: ignore[arg-type]


def kinds(events: list[dict]) -> list[str]:
    return [e["type"] for e in events if e["type"] in ("npc", "performance", "clue", "sanity")]


async def test_what_the_one_spoken_to_told_reaches_the_notebook_when_the_turn_is_resumed(fake):
    """The review of 2026-10-21: the line was kept by the checkpoint, the told clue only in the
    cast's memory; resumed, the warden was rightly not asked again and the clue never came."""
    game, _, _ = table(fake)
    turn = await game.resolve(ASK, Ruling(needs_roll=False))
    saved: dict = {}

    def crash(t: Turn) -> None:  # the first save after the reply, before anything is shown
        saved.update(state=game.state.model_dump_json(), turn=TURN_RECORD.dump_json(t))
        raise RuntimeError("断电")

    game.turn_checkpoint = crash
    with pytest.raises(RuntimeError):
        await game.talk(turn)
    await game.close()
    kept = TURN_RECORD.validate_json(saved["turn"])
    receipt = kept.replies["talk:warden"]
    assert (receipt.line, receipt.told, receipt.shown) == (DIARY, ["c_diary"], False)  # kept with the words
    assert kept.npc_lines == [("warden", DIARY)]
    resumed, again, events = table(fake, state=GameState.model_validate_json(saved["state"]))
    await resumed.talk(kept)
    assert again.npc_calls == []  # never asked again
    assert resumed.state.clues == ["c_diary"] and kept.clues == ["c_diary"]
    assert kinds(events) == ["npc", "clue"]  # shown once, the card after the words
    assert next(e for e in events if e["type"] == "clue")["heard"] == "warden"
    assert [e.text for e in resumed.state.log if e.kind == "npc"] == [DIARY]  # one line in the log
    await resumed.talk(kept)  # settled: nothing more to show or to find
    assert again.npc_calls == [] and kinds(events) == ["npc", "clue"]
    await resumed.close()


async def test_a_told_clue_cut_off_before_its_card_is_not_rolled_for_again(fake):
    """A clue with a sanity cost, its roll made and kept, the connection gone before its card: the
    resumed turn shows the card and the roll as they were, and rolls nothing."""
    m = Module.load(EXAMPLE)
    m.clue("c_diary").sanity = "0/1"
    game, _, events = table(fake, module=m)
    turn = await game.resolve(ASK, Ruling(needs_roll=False))
    saved: dict = {}
    game.turn_checkpoint = lambda t: saved.update(state=game.state.model_dump_json(), turn=TURN_RECORD.dump_json(t))

    async def cut(ev):
        if ev["type"] == "clue":
            raise ConnectionError("断线")
        events.append(ev)

    game.emit = cut
    fake.queue(10)  # 理智 55 → 10, passed: nothing lost
    with pytest.raises(ConnectionError):
        await game.talk(turn)
    await game.close()
    assert kinds(events) == ["npc"] and not fake.values
    state = GameState.model_validate_json(saved["state"])
    assert state.clues == ["c_diary"]  # committed with its roll, the card not yet shown
    resumed, again, shown = table(FakeDice(), state=state, module=m)  # no dice queued: any roll would fail
    kept = TURN_RECORD.validate_json(saved["turn"])
    await resumed.talk(kept)
    assert again.npc_calls == [] and kinds(shown) == ["clue", "sanity"]  # the words were shown before
    assert resumed.state.clues == ["c_diary"] and len(kept.sanity) == 1 and kept.sanity[0][1].check.value == 10
    assert [e.kind for e in resumed.state.log].count("sanity") == 1
    await resumed.close()


async def test_what_one_who_steps_in_tells_is_in_the_notebook_before_the_ruling(fake):
    """The review of 2026-10-21: what the warden told stepping in (Initiative.first) went nowhere;
    the stages after it pass by whoever already spoke, so nothing took it in."""
    game, cast, events = table(fake)
    game.state.pending_turn = {"input": "看看钟楼", "initiative": [
        Initiative(npc="warden", does="主动把日记里写的告诉他", first=True).model_dump()]}
    await game.prepare(LOOK)
    assert game.state.clues == ["c_diary"] and kinds(events) == ["npc", "clue"]
    ruled: list[list[str]] = []

    async def arbiter(state, action, at=None):
        ruled.append(list(state.clues))
        return Ruling(needs_roll=False)

    cast.arbiter = arbiter  # type: ignore[method-assign]
    ruling = await game.rule(LOOK)
    assert ruled == [["c_diary"]]  # the arbiter rules knowing it was told
    turn = await game.resolve(LOOK, ruling)
    await game.talk(turn)
    assert turn.clues == ["c_diary"] and turn.npc_lines == [("warden", DIARY)] and turn.joined == ["warden"]
    assert [n for n, _ in cast.npc_calls] == ["warden"]  # not asked again after the ruling
    assert "获得线索〔守钟人的日记〕" in game._report(turn)
    await game.close()


async def test_one_who_stepped_in_is_not_asked_again_when_the_preparation_is_resumed(fake):
    game, _, _ = table(fake)
    game.state.pending_turn = {"input": "看看钟楼", "initiative": [
        Initiative(npc="warden", does="主动把日记里写的告诉他", first=True).model_dump()]}
    saved: dict = {}

    def crash() -> None:
        saved["state"] = game.state.model_dump_json()
        if "turn" in game.state.pending_turn.get("preparation", {}):  # his reply kept, nothing shown yet
            raise RuntimeError("断电")

    game.checkpoint = crash
    with pytest.raises(RuntimeError):
        await game.prepare(LOOK)
    await game.close()
    assert "c_diary" not in GameState.model_validate_json(saved["state"]).clues
    resumed, again, events = table(fake, state=GameState.model_validate_json(saved["state"]))
    await resumed.prepare(LOOK)
    assert again.npc_calls == [] and resumed.state.clues == ["c_diary"] and kinds(events) == ["npc", "clue"]
    turn = await resumed.resolve(LOOK, Ruling(needs_roll=False))
    await resumed.talk(turn)
    assert again.npc_calls == [] and turn.npc_lines == [("warden", DIARY)] and turn.clues == ["c_diary"]
    await resumed.close()


async def test_what_one_tells_before_the_journey_is_in_the_notebook(fake):
    game, cast, events = table(fake)
    go = Action(kind="move", summary="周衡问过日记就去广场", destination="square",
                before=[BeforeTravel(npc="warden", speech="日记里写了什么？")])
    await game.prepare(go)
    assert game.state.clues == ["c_diary"] and kinds(events) == ["npc", "clue"]
    turn = await game.resolve(go, Ruling(needs_roll=False))
    await game.talk(turn)
    assert game.state.location == "square" and turn.clues == ["c_diary"] and turn.npc_lines == [("warden", DIARY)]
    assert [n for n, _ in cast.npc_calls] == ["warden"]
    await game.close()


async def test_one_who_joins_in_tells_a_clue_and_walks_off_and_the_clue_stays(fake):
    """Told, then gone in the same reply (Dialogue.leave): the clue was said here, whoever is here now."""
    game, _, _ = table(fake, leaves={"warden": "tower"})
    game.world.apply(O.Move(id="down", actor="director", entity="warden", to="square"))
    game.state.location = "square"
    game.initiative = [Initiative(npc="warden", does="说起日记里写的，然后回钟楼去")]
    turn = await game.resolve(Action(kind="investigate", summary="周衡看看告示", target="告示栏"), Ruling(needs_roll=False))
    await game.talk(turn)
    assert game.world.place("warden") == "tower" and turn.joined == ["warden"]
    assert turn.clues == ["c_diary"] and game.state.clues == ["c_diary"]
    assert any(n.startswith("老守钟人说完就走了") for n in turn.notes)
    await game.close()


async def test_a_performance_cut_off_after_it_was_kept_is_shown_and_not_staged_again(fake):
    game, cast, _ = table(fake)
    ev = Event(id="e_warden", title="守钟人赶来", when="flag:warden_woken", text="守钟人赶来。",
               read_aloud="守钟人提着灯冲上楼梯。", performer="warden", performance="喝止调查员")
    turn = Turn(action=Action(kind="wait", summary="周衡等着"), events=[ev])
    saved: dict = {}

    def crash(t: Turn) -> None:
        saved.update(state=game.state.model_dump_json(), turn=TURN_RECORD.dump_json(t))
        raise RuntimeError("断电")

    game.turn_checkpoint = crash
    with pytest.raises(RuntimeError):
        await game.talk(turn)
    await game.close()
    assert len(cast.performances) == 1
    resumed, again, events = table(fake, state=GameState.model_validate_json(saved["state"]))
    kept = TURN_RECORD.validate_json(saved["turn"])
    await resumed.talk(kept)
    assert again.performances == [] and kinds(events) == ["performance"]
    assert kept.performance == ("warden", "诸位，钟声会替我们作证……") and kept.performed == ["e_warden"]
    await resumed.close()


# -- a passed roll on someone nobody asks (the review of 2026-10-22) ---------------------------------------
async def test_a_passed_psychology_roll_gives_its_clue_without_asking_anyone(fake):
    """有些人油盐不进's c_fitz_talk: a 心理学 gate, watched and not spoken to. The roll passed; the clue
    was held back for the priest to tell, and nobody asked him: passed, and nothing gained."""
    from autokeeper.module.schema import Check

    game, cast, events = table(fake)
    game.state.npc("warden").unlocked = []
    game.m.npc("warden").gates["c_diary"] = Check(skill="心理学", difficulty="常规")
    fake.queue(5)
    watch = Action(kind="talk", summary="周衡观察守钟人的神色", npc="warden", approach="心理学")
    turn = await game.resolve(watch, Ruling(needs_roll=True, skill="心理学", clues_on_success=["c_diary"]))
    await game.talk(turn)
    assert turn.checks[0].passed and game.state.clues == ["c_diary"] and turn.clues == ["c_diary"]
    assert cast.npc_calls == [] and kinds(events) == ["clue"]
    await game.close()


async def test_what_ones_manner_gives_away_is_found_even_when_they_are_spoken_to(fake):
    """A 心理学 gate is the investigator's own observation: asked something as well, the one watched
    answers, but the clue is a card at once, never left to the reply to tell (Dialogue.told)."""
    from autokeeper.module.schema import Check

    game, cast, events = table(fake)
    game.state.npc("warden").unlocked = []
    cast.tells = {}
    game.m.npc("warden").gates["c_diary"] = Check(skill="心理学", difficulty="常规")
    fake.queue(5)
    ask = ASK.model_copy(update={"approach": "心理学"})
    turn = await game.resolve(ask, Ruling(needs_roll=True, skill="心理学", clues_on_success=["c_diary"]))
    assert turn.clues == ["c_diary"] and game.state.npc("warden").unlocked == ["c_diary"]
    await game.talk(turn)
    assert [n for n, _ in cast.npc_calls] == ["warden"] and kinds(events) == ["clue", "npc"]
    await game.close()


async def test_what_is_seen_of_someone_is_found_even_when_they_are_spoken_to(fake):
    """A clue that comes from someone (Clue.npc) and is not theirs to tell (not in their NPC.clues: a
    disguise seen through, a spell seen cast) is a card when it is ruled, never left to their reply."""
    game, cast, events = table(fake)
    warden = game.m.npc("warden")
    warden.clues, warden.gates = [], {}
    game.state.npc("warden").unlocked = []
    cast.tells = {}
    turn = await game.resolve(ASK, Ruling(needs_roll=False, auto_clues=["c_diary"]))
    assert turn.clues == ["c_diary"] and game.state.npc("warden").unlocked == ["c_diary"]
    await game.talk(turn)
    assert [n for n, _ in cast.npc_calls] == ["warden"] and kinds(events) == ["clue", "npc"]
    await game.close()


async def test_a_daily_action_that_says_nothing_finds_what_it_finds(fake):
    """A daily action with someone and not a word to them: talk() asks nobody, so the clue the arbiter
    gives is found, not held back for a reply that never comes."""
    game, cast, events = table(fake)
    help_out = Action(kind="daily", summary="周衡默默帮守钟人把木柴搬上楼", npc="warden")
    turn = await game.resolve(help_out, Ruling(needs_roll=False, auto_clues=["c_diary"]))
    await game.talk(turn)
    assert game.state.clues == ["c_diary"] and cast.npc_calls == [] and kinds(events) == ["clue"]
    await game.close()


async def test_a_clue_is_left_to_the_one_asked_when_they_are_asked(fake):
    """The rule the review kept: spoken to and asked, the warden tells what he knows in his own words,
    and the card comes after them."""
    game, cast, events = table(fake)
    turn = await game.resolve(ASK, Ruling(needs_roll=False, auto_clues=["c_diary"]))
    assert turn.clues == [] and game.state.clues == []
    await game.talk(turn)
    assert [n for n, _ in cast.npc_calls] == ["warden"] and kinds(events) == ["npc", "clue"]
    assert game.state.clues == ["c_diary"]
    await game.close()


async def test_what_the_npc_said_is_not_told_again_and_nobody_is_asked_to_narrate_it(fake):
    """No clue card follows the words (the user, 2026-10-22: players hear it in the fiction and keep
    their own notes): the warden's line is how they learn what he told, so the keeper is not asked to
    tell it again, and its report says he said it."""
    game, cast, events = table(fake)
    turn = await game.resolve(ASK, Ruling(needs_roll=False))
    await game.talk(turn)
    assert turn.clues == ["c_diary"]
    report = game._report(turn)
    assert "获得线索〔守钟人的日记〕" in report and "老守钟人刚才已经亲口说出，不要复述" in report
    assert "【讲出发现】" not in report and "线索卡与检定卡" not in report
    assert await game.narrate(turn) == "" and cast.reports == [] and events[-1]["type"] == "dialogue_end"
    await game.close()


async def test_a_find_nobody_said_is_the_keepers_to_tell_even_in_a_conversation(fake):
    """The gears found while the warden talks: his line tells the diary, nothing shows the gears. The
    keeper tells that turn, and the report asks for what was found in the story's own words."""
    m = Module.load(EXAMPLE)
    gears = m.clue("c_gears")
    gears.check, gears.sanity = None, None  # in plain sight here, and no roll for it
    game, cast, _ = table(fake, module=m)
    turn = await game.resolve(ASK, Ruling(needs_roll=False, auto_clues=["c_gears"]))
    await game.talk(turn)
    assert turn.clues == ["c_gears", "c_diary"] and turn.npc_lines == [("warden", DIARY)]
    await game.narrate(turn)
    report = cast.reports[-1]
    assert f"获得线索〔齿轮间〕：{gears.text.strip()}" in report and "亲口说出" in report  # the diary was his line
    assert "【讲出发现】玩家看不到线索卡，〔齿轮间〕的内容只能从你的叙述里得知" in report  # the diary was said
    assert all(words in report for words in ("看到、读到、听到或想到", "不概括成结论", "不点明这是线索", "不添原文没有的内容"))
    await game.close()


async def test_a_paper_handed_over_speaks_for_itself(fake):
    """A clue that comes with a paper: the players hold its words (the handout window, F2's 资料), so the
    keeper tells how they came by it, not the whole of it again."""
    m = Module.load(EXAMPLE)
    game, _, events = table(fake, module=m)
    m.clue("c_diary").handout = "h_diary"  # the warden's diary, handed over this time
    turn = Turn(action=LOOK)
    await game._gain(["c_diary"], turn, None)
    assert "handout" in [e["type"] for e in events]
    report = game._report(turn)
    assert "手稿〔守钟人的日记〕的原文在玩家手里" in report and "不复述全文" in report and "【讲出发现】" not in report
    await game.close()
