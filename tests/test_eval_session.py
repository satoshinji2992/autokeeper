"""Replaying a real session (eval/session.py): the turns come back from the checkpoints, each is
played again from the game before it, and the measures read what was told against the record."""

from __future__ import annotations

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from autokeeper import paths
from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import graph
from autokeeper.engine.game import Game
from autokeeper.eval import session
from autokeeper.module.schema import Location, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast

LOOK = Action(kind="investigate", summary="周衡看告示栏", target="告示栏")
ASK = Action(kind="talk", summary="周衡问守钟人钟的事", npc="warden", speech="钟怎么了？")
SCRIPT = {"看看告示": (LOOK, Ruling(needs_roll=True, skill="侦查", minutes=5, reason="告示栏上贴得很乱")),
          "问守钟人": (ASK, Ruling(needs_roll=False, minutes=1, reason=""))}


async def played(fake) -> str:
    """Two turns of the example on the turn graph, kept in the save folder's checkpoints."""
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="replay-me")
    st.location = "tower"  # where the warden is
    fake.queue(30)
    game = Game(m, st, FakeCast(SCRIPT), Rules(dice=fake))  # type: ignore[arg-type]
    paths.games().mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(paths.checkpoints()) as db:
        saver = AsyncSqliteSaver(db)
        await saver.setup()
        app = graph.build(game, saver)
        for text in SCRIPT:
            async for _ in app.astream({"input": text}, {"configurable": {"thread_id": st.slot}}, stream_mode="updates"):
                pass
    await game.close()
    return st.slot


async def test_the_recorded_turns_come_back_from_the_checkpoints(fake):
    slot = await played(fake)
    rec = session.recorded(slot)
    assert sorted(rec) == [1, 2]
    assert session.said(rec[1]["state"], 1) == "看看告示" and session.said(rec[2]["state"], 2) == "问守钟人"
    assert session.rolled(rec[1]["state"], 1) == [30]  # the d100 as it fell
    assert rec[2]["action"]["npc"] == "warden" and rec[1]["ruling"]["skill"] == "侦查"
    assert session.parse_turns("2,5-9", sorted(rec)) == [2]


async def test_a_turn_is_played_again_from_the_game_before_it(fake, monkeypatch):
    slot = await played(fake)
    rec = session.recorded(slot)

    class Scripted(FakeCast):
        def __init__(self, cfg, m):
            super().__init__(SCRIPT)

        async def close(self):
            return None

    monkeypatch.setattr("autokeeper.agents.cast.Cast", Scripted)
    st, shown = await session.replay(EXAMPLE, rec[1]["state"], "问守钟人", [], turn=2)
    assert st.turn == 2 and any(e.get("type") == "action" and e.get("npc") == "warden" for e in shown)
    keeper, lines = session.told(st, 2)
    assert lines == ["老守钟人：warden 的回答"] and keeper in ("", "叙事")  # a plain exchange needs no retelling
    assert not paths.save(st.slot).exists()  # nothing is saved


def test_the_measures(fake):
    m = Module.load(EXAMPLE)
    m.locations += [Location(id="house", name="守钟人家", read_aloud="", exits=["square"]),
                    Location(id="study", name="书房", read_aloud="", exits=["house"], within="house")]  # fmt: skip
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    from autokeeper.world.store import World

    w = World(m, st)
    st.location = "house"
    st.record.entities["warden"].at = "study"
    assert session.echo("他说钟有钟的规矩，谁也别上去。", ["老守钟人：钟有钟的规矩，谁也别上去。"]) > 0.8
    assert session.echo("雾很大。", []) == 0.0
    st.record.entities["preacher"].at = "house"
    swap = session.target(m, st, "我去问老守钟人", {"npc": "preacher"})
    assert "守钟人" in swap  # he is in the next room: the action put the preacher in his place
    assert session.target(m, st, "钟楼是怎么回事", {"npc": "preacher"}) == ""
    record = session.world_after(m, st, 0, {"minutes": st.minutes, "location": "square"}, {"reason": "成功也只是看清楚"})
    assert "同一处别的房间里、不在眼前的人：老守钟人在书房" in record and "这一轮从广场到了守钟人家" in record
    assert "此刻就在调查员身边的人：街头布道者" in record and "裁定员对这一轮的说明" in record
    assert w is not None


@pytest.mark.parametrize("record, kept", [("阿萨塔在钟表铺，不在火车站", True), ("托马斯就在身边，所以一致。", False),
                                          ("这句与记录不矛盾", False)])  # fmt: skip
def test_a_judge_note_that_admits_agreement_is_dropped(record, kept):
    assert (session.AGREED.search(record) is None) == kept


def test_what_the_players_do_not_know_yet():
    from autokeeper.world.store import World

    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    World(m, st)
    hidden = session.secrets(st)
    assert "truth" in hidden and "npc:warden:secrets:0" in hidden and "clue:c_gears" in hidden
    assert not any(k.startswith("npc:warden:lies") for k in hidden)  # what he says is no secret
    st.clues.append("c_gears")
    assert "clue:c_gears" not in session.secrets(st)  # found: no longer the keeper's to keep
    st.record.facts["truth"].revealed = 0  # an old marker with no source does not count as told
    assert "truth" in session.secrets(st)


async def test_a_slip_stands_when_both_readings_name_it():
    from autokeeper.harness import Reply

    class Pool:
        def __init__(self, *answers):
            self.answers = list(answers)

        async def complete(self, system, messages, schema=None):
            return Reply(data=self.answers.pop(0))

    hidden = {"truth": "守钟人在钟里藏了一样东西", "npc:warden:secrets:0": "他在钟里藏了东西"}
    pool = Pool({"leaks": [{"secret": "truth", "claim": "钟里藏着东西"}, {"secret": "npc:warden:secrets:0", "claim": "藏了东西"}]},
                {"leaks": [{"secret": "truth", "claim": "钟里有东西"}, {"secret": "别的", "claim": "没有这一条"}]})
    got = await session.ask_leaks(pool, hidden, "你听见钟里藏着东西在敲。")
    assert [x["secret"] for x in got] == ["truth"]
    assert await session.ask_leaks(pool, hidden, "") == []  # no narration, nothing to read
    rows = [{"turn": 1, "recorded": {"problems": [], "npc": [], "target": "", "echo": 0.0, "chars": 9,
                                     "leaks": got, "verbatim": ["truth"]}}]  # fmt: skip
    told = session.summarize(rows)["recorded"]
    assert told["turns_with_leaks"] == 1 and told["leaks"] == 1 and told["verbatim_leaks"] == 1
