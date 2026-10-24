"""Ordinary NPCs retain prepared schedules; importance does not allocate another model."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.module.schema import Agenda, Module, Move
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O
from autokeeper.world import scheduler

from .test_engine import make

WAIT = Action(kind="wait", summary="周衡在报社等着")


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    m.agendas = [Agenda(npc="preacher", goals=["把信众带到钟楼下"], steps=[
        Move(id="tower", text="去钟楼底下布道", when="D1 14:30", does="at:preacher:tower"),
        Move(id="think", text="考虑要不要去报社找记者", when="D1 14:30"),
        Move(id="never", text="等钟停了就收摊", when="D1 14:30 & flag:bell_stopped", does="at:preacher:square"),
    ])]
    m.flags.setdefault("bell_stopped", "钟停了")
    return m


@pytest.mark.parametrize("important", [False, True])
async def test_schedules_execute_and_unresolved_work_reaches_kp_for_either_npc_tier(module, fake, important):
    module.npc("preacher").important = important
    game, cast, _ = make(module, fake, {}, location="office")
    try:
        w = game.world
        assert w.agent("preacher").passive is not important
        await game.resolve(WAIT, Ruling(needs_roll=False, minutes=240, rest=True))
        states = {step.id: step.status for step in w.agent("preacher").plan}
        assert w.place("preacher") == "tower" and states == {"tower": "done", "think": "pending", "never": "blocked"}
        notes = scheduler.pending(w, ["preacher"])
        assert any(n.kind == "due" and "报社" in n.text for n in notes)
        assert any(n.kind == "blocked" and "收摊" in n.text for n in notes)
        assert not hasattr(game, "brain") and not cast.reports
    finally:
        await game.close()


async def test_ordinary_npc_can_become_important_without_replacing_identity_or_memories(module, fake):
    game, _, _ = make(module, fake, {}, location="office")
    try:
        w = game.world
        assert w.agent("preacher").passive
        assert w.apply(O.Occur(id="shout", actor="warden", text="守钟人冲他吼了一声", place="square",
                              participants=["preacher"])).ok
        await game.resolve(WAIT, Ruling(needs_roll=False, minutes=5))
        assert any("吼了一声" in n.text for n in scheduler.pending(w, ["preacher"]))
        memories = [memory.text for memory in w.agent("preacher").memory]
        assert w.apply(O.Enlist(id="enlist:preacher:later", actor="keeper", npc="preacher", goals=["阻止钟声"])).ok
        assert not w.agent("preacher").passive
        assert all(text in [memory.text for memory in w.agent("preacher").memory] for text in memories)
        assert w.agent("preacher").goals == ["阻止钟声"]
    finally:
        await game.close()
