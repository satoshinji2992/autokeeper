"""What the encounter tests share: a scripted keeper (the plan's answers by mode, no model), arrangement
helpers, a fight begun in the clock tower, and the turn graph's interrupts answered."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command

from autokeeper.agents.cast import Plan
from autokeeper.agents.encounter import Checked
from autokeeper.engine.encounter.plan import (
    AttackPick,
    DeclaredAction,
    EncounterStart,
    EncounterTurnPlan,
    NpcArrangement,
)

from .test_engine import FakeCast, make

THREAD = {"configurable": {"thread_id": "enc"}, "recursion_limit": 100}


def attack(target: str, with_: str = "", *, n: int = 1, do: str = "attack", summary: str = "") -> DeclaredAction:
    return DeclaredAction(do=do, attacks=[AttackPick(target=target, with_=with_) for _ in range(n)], summary=summary or f"攻击 {target}")


def arrange(who: str, *actions: DeclaredAction, react: str = "fight_back", **kw: Any) -> NpcArrangement:
    return NpcArrangement(who=who, goal=kw.pop("goal", "赶走闯入者"), actions=list(actions), react=react, **kw)  # type: ignore[arg-type]


class Keeper(FakeCast):
    """The scripted cast with the keeper plan. Everyday turns: the script's action, with `hints`
    (text -> EncounterHint) announcing a fight. Inside one: per mode, a value, a list (one per call,
    the last repeated) or a callable(state, text, brief) -> value; `check` is the engine's and its
    problems come back with the answer, as Cast.plan's do after its resend."""

    def __init__(self, script: dict | None = None, **modes: Any):
        super().__init__(script or {})
        self.hints: dict = modes.pop("hints", {})
        self.starts: list = list(modes.pop("starts", []))
        self.choose: Any = modes.pop("choose", None)
        self.modes = modes
        self.calls: list[tuple[str, str, str]] = []  # (mode, text, the brief it read)
        self.checked: list[list[str]] = []
        self.ruling_for: dict = {}

    async def plan(self, state, text, *, encounter=None, mode="act", check=None, ask=""):
        if encounter is None:
            action = self.script[text][0]
            return Plan(action=action, encounter=self.hints.get(text))
        self.calls.append((mode, text, encounter + ("\n" + ask if ask else "")))
        source = self.modes.get(mode)
        if isinstance(source, list):
            value = source.pop(0) if len(source) > 1 else (source[0] if source else None)
        elif callable(source):
            value = source(state, text, encounter)
        else:
            value = source
        if value is None and mode == "arrange":
            value = EncounterTurnPlan()
        problems = [str(p) for p in check(value)] if check is not None and value is not None else []
        self.checked.append(problems)
        return Checked(value=value, problems=problems, raw=value.model_dump(mode="json") if value is not None else None)

    async def plan_encounter(self, state, text, hint, check=None, *, brief=""):
        start = self.starts.pop(0) if len(self.starts) > 1 else self.starts[0]
        problems = check(start) if check is not None else []
        return Checked(value=start, problems=problems, raw=start.model_dump(mode="json"))

    async def encounter_choice(self, state, inv_id, situation, options):
        pick = self.choose(options) if callable(self.choose) else None
        return Checked(value=pick, problems=[] if pick else ["没选"])

    async def arbiter(self, state, action, at=None, *, encounter="", problems=None):
        self.ruled.append(action)
        if action.summary in self.ruling_for:
            return self.ruling_for[action.summary]
        return next(r for a, r in self.script.values() if a.summary == action.summary)


def fight_game(module, fake, *, location: str = "tower", **modes):
    game, _, events = make(module, fake, {}, location=location)
    cast = Keeper(**modes)
    game.cast, cast.world = cast, game.world
    return game, cast, events


async def begin(game, foes: list[str], *, npcs=(), surprise: str = "", pace: int | None = 6, stakes=()) -> None:
    """A fight begun as an event begins one: nobody has acted yet."""
    from autokeeper.engine.game import Turn

    start = EncounterStart(mode="fight", foes=foes, npcs=list(npcs), surprise=surprise, pace=pace, stakes=list(stakes), reason="动起手来")  # type: ignore[arg-type]
    turn = Turn()
    assert await game._encounter_open(start, turn)
    return turn


async def play(app, text, *, answers: list | None = None, thread=THREAD) -> tuple[list[dict], list[dict]]:
    """A turn through the graph; each interrupt answered from `answers` in order (a reaction id, "roll",
    a push); what was shown and what was asked."""
    out, asked = [], []
    answers = list(answers or [])
    inp: Any = {"input": text, "by_hand": False}
    while True:
        offer = None
        async for mode, chunk in app.astream(inp, thread, stream_mode=["custom", "updates"]):
            if mode == "custom":
                out.append(chunk)
            elif "__interrupt__" in chunk:
                offer = chunk["__interrupt__"][0].value
        if offer is None or not answers:
            if offer is not None:
                asked.append(offer)
            return out, asked
        asked.append(offer)
        inp = Command(resume=answers.pop(0))
