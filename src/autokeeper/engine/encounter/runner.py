"""Game's fights (0.0.4): the encounter engine on the turn, a unit at a time (the `Encounters` mixin).

A fight is `GameState.encounter`, changed only by world ops (world/ops.EncounterStep). Its work is
done in units: a unit throws its dice through the turn's journal (Game._resolution_step and
_ResolutionDice), works out what they mean (engine/encounter/resolve.py), and lands everything it
did as one op whose id is the unit's key; only then are its notices sent to the screens, each once
(invariants I2 and I3). The encounter in the save (its cursor, its receipts) is the truth of how
far a round has got; the journal only keeps dice, model answers and which notices went out. A turn
cut short goes on from there: a die thrown before the crash is the same die after it, nothing lands
twice, a fight that closed is a finished result and is not begun again (I7: the turn's kind is
decided once, in its first step).

A round: every investigator who can act declares (the button they pressed, or their words read by
the keeper plan, which also arranges the keeper's people); AI companions pick from the same choices
(Cast.encounter_choice); the round begins in DEX order and each slot runs its units. An attack on a
player stops, before any of its dice, to ask how they answer it (choices.should_ask): the turn
graph's `offer` node shows the question and `respond` goes on from it. At the round's end the
dying roll CON, healing creatures heal, the clock moves, whoever fled is chased or gets away (a foe the
round after it ran, when the investigators let it go; the chase once nobody else is still fighting),
and the stakes that now hold set their flags.

A chase is the same encounter in mode "chase" (engine/encounter/chase.py, site.py): the same units,
the same declarations and arrangements, the round's action points spent running across the site, the
obstacles crossed, attacks where the book allows them. It turns into a fight where they meet and
someone turns to fight, and back into a chase when someone flees and is chased; wounds, guns, speeds
and stakes carry over. A chase that comes to blows in another of the world's places moves the
encounter, the party and the keeper's people in it there in the switch's own op (chase.to_fight), and
the arrival follows. There is no split party: the party is in a chase whole. When it ends the keeper's
people are where they ran to or fought, and the party where its players ended up (GameState.apart for
those left elsewhere).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...rules import combat as rules
from ...rules.melee import opposed_difficulty
from ...world.ops import Bring, EncounterStep, Occur
from ...world.record import ENGINE
from .. import effects
from ..audience import scope
from . import actions, choices, clock, plan_check, profiles, site, stakes, telling, views
from . import chase as chasing
from .actions import ATTACKING, Ctx
from .model import (
    ActionReceipt,
    Archive,
    Begin,
    ChoiceOption,
    Cover,
    EncounterState,
    EncounterSummary,
    EndRecord,
    Participant,
    PendingChoice,
    ReactionReceipt,
    RoundBudget,
    SetParticipant,
    StakeState,
    Unseen,
)
from .plan import DeclaredAction, EncounterStart, EncounterTurnPlan, NpcArrangement
from .resolve import Work, act, round_end, round_order, strike

if TYPE_CHECKING:
    from ...agents.cast import Action, Ruling
    from ...module.schema import Event
    from ..game import Turn

UNSETTLED = "【未落实】"
# The keeper's lines the screens never get: cues, what did not land, the round's order line (in a chase with
# everyone's place and action points), stakes, a Luck pool.
KEEPER_ONLY = ("【可观察】", UNSETTLED, "【战斗 · 第", "【追逐 · 第", "【后果】", "【幸运池】")
OUTCOME_WORDS = {"won": "调查员一方占了上风", "fled": "调查员逃离了", "lost": "调查员一方全部倒下", "escaped": "对手跑了",
                 "parley": "双方停手谈和", "surrender": "有一方投降了", "dispersed": "各自散去", "caught": "被抓住了",
                 "aftermath_done": "战斗结束"}


END_HOW = {"gave_up": "escaped", "surrender": "surrender", "parley": "parley", "dispersed": "dispersed"}  # an end proposal's outcome


def _end_slot(enc: EncounterState) -> int:
    return len(enc.order)


def unattended(ctx: Ctx) -> bool:
    """Nobody at this table can act: of the investigators in it its players play (all of them, at a
    table only watching), none present and able to act. The rounds then go on by themselves."""
    invs = [p for p in ctx.enc.participants if p.kind == "investigator"]
    players = [p for p in invs if p.controller == "human"] or invs
    return bool(players) and not any(ctx.acting(p.id) for p in players)


def _lingering(ctx: Ctx) -> list[str]:
    """Those still in it who are dying with nothing holding it (p. 101), or down and coming back."""
    return [p.id for p in ctx.enc.participants if p.presence == "in" and (v := ctx.view(p.id)) is not None
            and ((v.body().dying and not v.body().stabilized) or v.body().regenerating)]


def _pressed(ctx: Ctx) -> bool:
    """In the round just over, something was still done to the investigators' side: one of them went
    down or was hurt lying there (a "down" change), was disarmed or newly held, or a foe struck or shot
    at one of them. A round of none of it, with them all down, is the end of it (_outcome): whatever the
    foes go on doing changes nothing, so the rounds cannot go on for ever."""
    enc = ctx.enc
    party = {p.id for p in enc.participants if p.side == "party"}
    if any(c.round == enc.round and c.kind in ("down", "disarmed") and c.who in party for c in enc.changes):
        return True
    if any(h.since == enc.round and h.held in party for h in enc.scene.holds):
        return True
    return any(r.round == enc.round and r.declared.do in ("attack", "shoot") and ctx.side(r.actor) != "party"
               and any(k.target in party for k in r.declared.attacks) for r in enc.receipts)


def _proposed(enc: EncounterState) -> str:
    """The end the keeper's people proposed while someone was still dying (_propose_end): how it ends
    now that nobody is; "" when there was none."""
    how = next((h.split(":")[1] for h in reversed(enc.history) if h.startswith("end:")), "")
    return END_HOW.get(how, "")


def _stance_problem(ctx: Ctx, who: str, react: str) -> str:
    """Why investigator `who` cannot set `react` as their answer to blows now ("" when they can): in a
    fight they can act in, one of choices.presets. A player's Tab (set_react) and an AI's own answer
    (_ai_declare) are held to the same."""
    if ctx.enc.mode != "fight":
        return "现在不在战斗中"
    p = ctx.p(who)
    if p is None or p.kind != "investigator" or not ctx.acting(who):
        return "这名调查员现在无法行动"
    if react not in choices.presets(ctx, who):
        return "这场战斗中不能这样应对"
    return ""


def _undecided_flight(ctx: Ctx, x: str) -> bool:
    """A foe that ran this very round, with an investigator who could still go after it (they declared
    before it ran): theirs to choose next round (追赶), it is not away yet."""
    enc = ctx.enc
    if ctx.side(x) == "party" or not any(c.kind == "fled" and c.who == x and c.round == enc.round for c in enc.changes):
        return False
    return any(p.controller != "keeper" and ctx.acting(p.id) and ctx.opposed(p.id, x) and not ctx.hidden_from(p.id, x)
               for p in enc.participants)


def _between(enc: EncounterState) -> bool:
    """No round going on: none begun yet, or the last one is over (its end and its switch done)."""
    return enc.round == 0 or enc.cursor >= len(enc.order) + 2


class Encounters:
    """The fight engine, mixed into Game."""

    asking = True  # stop to ask a player (a reaction, a stale declaration); off where nobody can answer (a scripted test)
    by_hand_seats: frozenset[str] = frozenset()  # LAN: seated players, who throw their own dice in a fight (net/hub.py)
    ask_reactions = "major"  # when a player is asked how to answer a blow: "always" (设置: 每次被攻击都询问应对) or "major"

    if TYPE_CHECKING:  # what the Game it is mixed into has
        from ...module.schema import Module
        from ...rules.coc import Rules
        from ...world.store import World
        from ..state import GameState

        state: GameState
        m: Module
        rules: Rules
        world: World
        cast: Any
        holding: bool
        emit: Any
        _resolution: dict | None
        _resolution_step: Any
        _flags: Any
        _time: Any
        _check_ending: Any
        _party_down: Any
        _debug: Any
        _gain: Any
        _valid_clues: Any
        _takes: Any
        _equipment: Any
        _arrive: Any
        _rejoin: Any

    # -- whether there is one -------------------------------------------------------------------
    def encounter(self) -> EncounterState | None:
        """The active investigator's encounter at their own scene."""
        enc = self.world_encounter()
        if enc is None:
            return None
        actor = self.state.investigator.id
        p = enc.get(actor)
        if enc.mode == "chase" and p is not None and p.presence == "in":
            return enc
        return enc if self.world.place(actor) == enc.place else None

    def world_encounter(self) -> EncounterState | None:
        """The authoritative encounter, including one at another investigator's scene."""
        enc = self.state.encounter
        return enc if enc is not None and enc.phase != "ended" else None

    def battling(self) -> bool:
        return self.encounter() is not None

    def fighting(self) -> bool:
        enc = self.encounter()
        return enc is not None and enc.mode == "fight"

    def chasing(self) -> bool:
        enc = self.encounter()
        return enc is not None and enc.mode == "chase"

    def fought_this_turn(self) -> bool:
        """A fight or a chase is going on, or this turn began one (and it is already over)."""
        st = self.state
        actor = st.investigator.id
        return self.battling() or any(x.started_turn == st.turn and actor in x.participants for x in st.encounter_log[-3:]) or (
            st.encounter is not None and st.encounter.started_turn == st.turn and st.encounter.get(actor) is not None)

    def pending_choice(self) -> PendingChoice | None:
        enc = self.encounter()
        return enc.pending if enc is not None else None

    def set_react(self, who: str, react: str) -> str:
        """How `who` answers blows from now on (Tab beside the round's choices, a LAN seat's "react"):
        between turns, in a fight they can act in, one of choices.presets. No action is spent and no
        model is asked; it lands as its own op. "" once set, else why not."""
        enc = self.encounter()
        if enc is None or enc.mode != "fight":
            return "现在不在战斗中"
        if self.state.pending_turn:
            return "本轮正在结算"
        ctx = Ctx.of(self)
        if why := _stance_problem(ctx, who, react):
            return why
        p = ctx.p(who)
        assert p is not None
        if p.react == react:
            return ""
        step = EncounterStep(id=f"enc/{enc.id}/preset/{who}/{self.world.rec.seq}", encounter=enc.id, unit="preset",
                             changes=[SetParticipant(participant=p.model_copy(update={"react": react}))], actor=ENGINE)
        result = self.world.apply(step)
        return "" if result.ok else "；".join(result.problems)

    # -- the player's words ---------------------------------------------------------------------
    async def encounter_input(self, text: str) -> Action:
        """The player's input in a fight: a button's exact words are its declaration; anything else
        is the keeper plan's to read (Cast.plan, mode "act"), with the brief and the engine's check.
        Nothing is read off the words by the engine. "/encounter" goes on with the round."""
        from ...agents.cast import Action

        st = self.state
        enc = self.encounter()
        assert enc is not None
        me = st.investigator
        if enc.get(me.id) is None:
            from ..game import Turn

            def join(w: Work) -> None:
                w.enc.participants.append(Participant(id=me.id, kind="investigator", side="party",
                                                      controller="human" if me.id in st.human else "ai", node=w.enc.place))
                w.change("joined", me.id)
                w.unit.lines.append(f"{{{me.id}}}来到现场，加入了这场遭遇")

            await self._unit(f"enc/{enc.id}/join-investigator/{me.id}/t{st.turn}", Turn(), join)
            enc = self.encounter()
            assert enc is not None
        if text.strip() == "/encounter":
            return Action(kind="other", summary="（这一轮接着结算）", encounter=enc.id, declared={"beat": True})
        found = choices.match(self, me.id, text)
        planner = getattr(self.cast, "plan", None)
        if found is not None:
            declared: dict = {"plan": EncounterTurnPlan(action=found).model_dump(mode="json", by_alias=True), "source": "button"}
            if planner is not None and any(choices.needs_arrangement(enc, p) and Ctx.of(self).acting(p.id) for p in enc.participants):
                checked = await self._ask_plan(text, "arrange")
                if checked is not None and checked.value is not None:
                    landed = plan_check.land(self, checked.value, me.id, act=False)
                    plan = checked.value.model_copy(update={"action": found})
                    declared["plan"] = plan.model_dump(mode="json", by_alias=True)
                    declared["unsettled"] = landed.unsettled if checked.problems else []
            return self._action(enc, found, declared)
        if planner is None:  # a cast with no keeper plan: the parser's neutral words, ruled as something else
            parsed = await self.cast.intent(st, text)
            d = DeclaredAction(do="other", summary=parsed.summary)
            return self._action(enc, d, {"plan": EncounterTurnPlan(action=d).model_dump(mode="json", by_alias=True),
                                         "source": "words"})
        checked = await self._ask_plan(text, "act")
        if checked is None or checked.value is None:
            raise RuntimeError("守秘人没能读懂这一轮的行动：" + "；".join(checked.problems if checked else []))
        plan: EncounterTurnPlan = checked.value
        landed = plan_check.land(self, plan, me.id)
        d = landed.action
        unsettled = list(landed.unsettled) if checked.problems else []
        if d is None:  # still not something the rules settle: the arbiter rules it, with its neutral summary
            summary = plan.action.summary if plan.action is not None and plan.action.summary.strip() else f"{me.name}做了别的事"
            d = DeclaredAction(do="other", summary=summary, takes=plan.action.takes if plan.action else [])
            unsettled += [f"{me.name}的行动按“别的事”交给裁定：{x}" for x in landed.action_problems]
        plan = plan.model_copy(update={"action": d})
        return self._action(enc, d, {"plan": plan.model_dump(mode="json", by_alias=True), "source": "words",
                                     "unsettled": unsettled})

    def _action(self, enc: EncounterState, d: DeclaredAction, declared: dict) -> Action:
        from ...agents.cast import Action

        summary = d.summary + (f"；目标：{d.goal}" if d.goal else "")
        return Action(kind="attack" if d.do in ATTACKING else "other", summary=summary, encounter=enc.id, declared=declared)

    async def _ask_plan(self, text: str, mode: str, *, ask: str = "", check=None):
        """The keeper plan inside the encounter; its answer and what is still wrong with it (Checked).
        What is still wrong after the one resend goes to the developer's journal whole."""
        me = self.state.investigator.id
        brief = views.keeper_text(self)
        check = check or plan_check.turn(self, me, act=mode == "act")
        checked = await self.cast.plan(self.state, text, encounter=brief, mode=mode, check=check, ask=ask)
        if checked.problems:
            self._debug("encounter-plan", {"mode": mode, "problems": checked.problems, "answer": checked.raw})
        return checked

    def encounter_ruling(self, action: Action) -> Ruling | None:
        """An encounter action's ruling without the arbiter: None when the arbiter must rule it
        (something else than the rules settle)."""
        from ...agents.cast import Ruling

        declared = action.declared or {}
        plan = declared.get("plan") or {}
        d = plan.get("action") or {}
        if (d.get("do") in ("other", "use") or (d.get("do") == "speak" and d.get("goal"))) and not declared.get("ruling"):
            return None
        if declared.get("ruling"):
            return Ruling.model_validate(declared["ruling"])
        return Ruling(needs_roll=False, reason="遭遇中")

    # -- the turn ---------------------------------------------------------------------------------
    def _turn_kind(self, action: Action) -> dict:
        """What this turn is, decided once (I7): a round of the encounter going on, the start of one, or
        an ordinary action."""
        st = self.state
        declared = action.declared or {}
        if (enc := self.encounter()) is not None:
            return {"kind": "encounter", "id": enc.id}
        if declared.get("start"):
            return {"kind": "encounter", "start": True}
        hint = st.pending_turn.get("encounter_hint") if st.pending_turn else None
        if hint and hint.get("trigger", "now") == "now":
            return {"kind": "encounter", "start": True}
        return {"kind": "ordinary"}

    async def _encounter_stages(self, action: Action, ruling: Ruling, turn: Turn, kind: dict) -> None:
        st, step = self.state, self._resolution_step
        declared = action.declared or {}
        started = ""
        if kind.get("start"):
            started = await self._start_from(action, turn)
            if not started:
                return
        enc = st.encounter
        eid = kind.get("id") or started or (enc.id if enc else "")
        if enc is None or enc.id != eid or enc.phase == "ended":
            if self._ending_owed(eid):  # it ended (and perhaps went into the log) before the turn was cut short
                await self._encounter_end("", turn, eid=eid)
            return
        if plan := declared.get("plan"):
            await self._plan_unit(f"enc/{enc.id}/plan/t{st.turn}", plan, declared.get("unsettled") or [], turn)
            d = (plan.get("action") or None) if isinstance(plan, dict) else None
            if d:
                await self._declare(turn, st.investigator.id, DeclaredAction.model_validate(d),
                                    "button" if declared.get("source") == "button" else "words", ruling)
        if self.holding:  # a LAN round: the others declare in the same window, the last settles it
            turn.declared = True
            return
        await self._beat(turn, settle=bool(declared.get("beat")) or st.round_spent is not None)
        await step("ending", turn, lambda: self._check_ending(turn))
        await step("party-down", turn, lambda: self._party_down(turn))  # over, and none of them can act: Game._party_down

    # -- starting one -----------------------------------------------------------------------------
    async def _start_from(self, action: Action, turn: Turn) -> str:
        """The encounter this turn begins: one written out already (EncounterStart in the action), or the one the
        keeper plan announced (Plan.encounter), set up by one more keeper-plan call (plan_encounter)."""
        from ...agents.encounter import EncounterHint

        st = self.state
        active = self.world_encounter()
        if active is not None and (active.started_turn != st.turn or active.place != self.world.place(st.investigator.id)):
            turn.notes.append("【未落实】另一处的遭遇仍在进行，这次新的冲突尚未开始结算")
            return ""
        declared = action.declared or {}
        if declared.get("start"):
            start = EncounterStart.model_validate(declared["start"])
            return await self._encounter_open(start, turn, actor=st.investigator.id)
        hint = EncounterHint.model_validate(st.pending_turn["encounter_hint"])
        start = await self._plan_start(hint, turn)
        return await self._encounter_open(start, turn, actor=st.investigator.id if hint.trigger == "now" else "")

    async def _plan_start(self, hint, turn: Turn) -> EncounterStart:
        """How the encounter the plan announced begins: plan_encounter's answer, as far as it holds;
        the hint itself (who, which mode) when nothing of the answer does."""
        st, step = self.state, self._resolution_step
        text = st.pending_turn.get("input", "") if st.pending_turn else ""
        candidates = {i.id for i in st.investigators} | set(self._present()) | set(hint.with_) | {c.id for c in self.m.creatures}

        async def ask() -> dict:
            asker = getattr(self.cast, "plan_encounter", None)
            if asker is None:
                return {}
            checked = await asker(st, text, hint, plan_check.start(self, candidates))
            if checked.problems:
                self._debug("encounter-start", {"problems": checked.problems, "answer": checked.raw})
            return {"start": checked.value.model_dump(mode="json", by_alias=True) if checked.value is not None else None,
                    "problems": list(checked.problems)}

        answer = await step("enc-start", turn, ask)
        base = EncounterStart(mode=hint.mode, foes=list(hint.with_), reason="", pace=None,
                              quarry="party" if hint.mode == "chase" else "")
        if not answer or answer.get("start") is None:
            if answer.get("problems"):
                turn.notes.append(f"{UNSETTLED}遭遇的安排没有落实：" + "；".join(answer["problems"]))
            return base
        start = EncounterStart.model_validate(answer["start"])
        problems = answer.get("problems") or []
        if problems:  # what holds lands; the rest is the keeper's to know
            turn.fight.append(f"{UNSETTLED}遭遇开始时的安排有没落实的：" + "；".join(problems))
            start = start.model_copy(update={
                "foes": [x for x in start.foes if x in candidates] or list(hint.with_),
                "allies": [x for x in start.allies if x in candidates and x not in start.foes],
                "stakes": [s for s in start.stakes if not plan_check.start(self, candidates)(
                    EncounterStart(mode=start.mode, foes=start.foes or ["x"], stakes=[s], pace=6, reason="",
                                   quarry=start.quarry or "party"))]})
            # A first action that still lacks something (a weapon nobody holds) does not land: the first
            # round is the player's to choose, as when there is none.
            start = start.model_copy(update={"opening": None if plan_check.opening(self, start) else start.opening})
        return start

    async def _encounter_open(self, start: EncounterStart, turn: Turn, *, event: Event | None = None,
                              actor: str = "") -> str:
        """An encounter begins where the investigators are: who is in it, their bodies kept by the world
        from now on, the creatures first seen (sanity, per investigator), its stakes. A fight's
        `opening` is the investigator's first action: the first round runs at once; otherwise the next
        input is the first round. Its id; "" when there is nobody there to fight after all."""
        active = self.world_encounter()
        if active is not None and (active.started_turn != self.state.turn
                                   or active.place != self.world.place(self.state.investigator.id)):
            turn.notes.append("【未落实】已有遭遇仍在进行，这次新的冲突尚未开始结算")
            return ""
        st, step = self.state, self._resolution_step
        eid = await step("enc-id", turn, lambda: f"e{st.turn}n{len(st.encounter_log)}" + (f"x{event.id}" if event else ""))
        # Who comes, decided once: resumed after the opening blow killed its foe, the fight still ends.
        foes = await step("enc-foes", turn, lambda: self._coming(start.foes, fight_ok=True))
        if not foes:
            if not event:
                turn.notes.append("没有可以动手的对象")
            return ""
        base = f"enc/{eid}/begin"
        done = await step("/" + base, turn, lambda: self._begin(eid, start, foes, turn, event))
        await self._notify(base, done["notices"], turn)
        if not done.get("ok"):
            return ""
        await self._sights(turn, paid=bool(event and event.sanity), event=event)
        if start.mode == "chase":
            if not await self._chase_open(start, turn):
                return eid
        opening = plan_check.combatants(self, start.opening)  # a creature form's id named as who fights in it
        if opening is not None and actor and not event:
            await self._declare(turn, actor, opening, "words", None)
        return eid

    async def _chase_open(self, start: EncounterStart, turn: Turn) -> bool:
        """A chase from the start: the side in flight (EncounterStart.quarry) runs from everyone of the
        other side who can, on the site built where it begins; True while it goes on (a quarry faster
        than everyone after them is away at once, and it ends)."""
        enc = self.encounter()
        assert enc is not None
        ctx = Ctx.of(self)
        party = [p.id for p in enc.participants if p.side == "party" and ctx.acting(p.id)]
        foes = [p.id for p in enc.participants if p.side == "foe" and ctx.acting(p.id)]
        quarry, pursuers = (foes, party) if start.quarry == "foe" else (party, foes)
        built = site.build(self.m, enc.place, start.site, self.world.rec.obstacles)
        await self._unit(f"enc/{enc.id}/chase/r{enc.round}", turn,
                         lambda w: chasing.begin(w, quarry, pursuers, built, why=start.reason))
        return await self._chase_settled(turn)

    async def _chase_settled(self, turn: Turn) -> bool:
        """A chase over as soon as it begins (all away at once, nobody left to run after) ends; True
        while it goes on."""
        enc = self.encounter()
        if enc is None or enc.mode != "chase":
            return enc is not None
        how = chasing.verdict(Ctx.of(self))
        if how:
            await self._encounter_end(how, turn)
            return False
        return True

    def _begin(self, eid: str, start: EncounterStart, foes: list[str], turn: Turn, event: Event | None) -> dict:
        st = self.state
        humans = set(st.human)
        allies = [profiles.combatant(self.world, x) for x in start.allies if x not in foes]
        parts: list[Participant] = []
        for inv in st.investigators:
            if self.world.place(inv.id) != st.location:
                continue
            parts.append(Participant(id=inv.id, kind="investigator", side="party", controller="human" if inv.id in humans else "ai",
                                     surprised=start.surprise == "party", node=st.location,
                                     ask="always" if inv.id in humans and self.ask_reactions == "always" else "major"))
        for x, side in [*((f, "foe") for f in foes), *((a, "party") for a in allies)]:
            p = profiles.profile(self.world, x)
            if p is None or any(q.id == p.id for q in parts):
                continue
            parts.append(Participant(id=p.id, kind="creature" if p.kind == "creature" else "npc", side=side, controller="keeper",
                                     surprised=start.surprise == ("foe" if side == "foe" else "party"), node=st.location))
        surprise = start.surprise
        if not surprise and start.surprise_contest is not None:
            surprise = self._surprise_contest(start, parts)
            for p in parts:
                p.surprised = (surprise == "party" and p.side == "party") or (surprise == "foe" and p.side == "foe")
        arranged = {a.who: a for a in start.npcs}
        for p in parts:
            a = arranged.get(p.id)
            if a is not None and p.controller == "keeper":
                p.arrangement, p.arranged_round, p.react = a, 1, a.react
        module = event.stakes if event is not None else []
        enc = EncounterState(id=eid, mode=start.mode, place=st.location, reason=(start.reason or (event.title if event else ""))[:120],
                             started_turn=st.turn, participants=parts, source_event=event.id if event is not None else "",
                             stakes=[*(StakeState(**s.model_dump(), source="module") for s in module),
                                     *(StakeState(**s.model_dump(), source="keeper") for s in start.stakes)])
        if start.pace:
            enc.clock.pace = start.pace
        moved = [effects.Relocate(who=p.id, place=st.location) for p in parts
                 if p.kind != "investigator" and self.world.place(p.id) not in ("", st.location)
                 and self.world.rec.entities.get(st.location) is not None]
        result = self.world.apply(EncounterStep(id=f"enc/{eid}/begin", encounter=eid, unit="begin", effects=moved,
                                                changes=[Begin(state=enc)], actor=ENGINE))
        if not result.ok:
            raise RuntimeError("遭遇没有开始：" + "；".join(result.problems))
        ctx = Ctx.of(self)
        kn, pn = views.names(ctx, "keeper"), views.names(ctx, "players")
        them = [p.id for p in parts if p.side == "foe"]
        word = "（调查员被偷袭）" if surprise == "party" else "（对方毫无防备）" if surprise == "foe" else ""
        if start.mode == "chase":
            return {"ok": True, "notices": []}
        line = f"【战斗开始】{{{st.investigator.id}}}一方对上" + "、".join(f"{{{x}}}" for x in them) + word
        turn.fight.append(telling.render(line, kn))
        for x in them:
            v = ctx.view(x)
            if (prof := getattr(v, "profile", None)) is not None:
                turn.fight += [telling.cue(c) for c in telling.first_sight(prof)]
        if allies:
            turn.fight.append("站在调查员这边的：" + "、".join(kn.get(a, a) for a in allies if ctx.p(a)))
        personal = {viewer: {"text": telling.render(line, views.names(ctx, viewer)), "who": ""}
                    for viewer, masks in st.perceptions.items() if masks}
        audience = scope(st, views.observers(self, enc))
        st.add("event", telling.render(line, pn), presented=personal, audience=audience)
        notice = self._filled({"type": "fight_start", "fight": None, "line": telling.render(line, pn),
                               **({"_entity_line": line} if personal else {}), "surprise": surprise, "audience": audience})
        return {"ok": True, "notices": [notice]}

    def _surprise_contest(self, start: EncounterStart, parts: list[Participant]) -> str:
        """The keeper's surprise contest (pp. 90–91): the first of those sneaking up rolls its skill at the
        difficulty the best watcher's skill sets; a success catches the other side unready."""
        c = start.surprise_contest
        assert c is not None
        by = profiles.combatant(self.world, c.by[0]) if c.by else ""
        ctx = Ctx.of(self, EncounterState(id="x", mode="fight", place="", started_turn=0, participants=parts))
        v = ctx.view(by)
        if v is None:
            return ""
        side = next((p.side for p in parts if p.id == by), "foe")
        watchers = [ctx.view(p.id) for p in parts if p.side != side]
        best = max((w.skill(c.against) for w in watchers if w is not None), default=0)
        roll = rules.check(self.rules.dice, c.skill, v.skill(c.skill), opposed_difficulty(best))
        return ("party" if side == "foe" else "foe") if roll.passed else ""

    async def _sights(self, turn: Turn, *, paid: bool = False, event: Event | None = None) -> None:
        """A horror seen for the first time costs each investigator in it sanity, once each (GameState
        sightings). Not what an event's own sanity roll already paid (`paid`, Event.sights), nor a
        horror any event of the module pays for when it is seen (WP-F's `sights`)."""
        from ..game import _sanity_view

        st, step = self.state, self._resolution_step
        enc = st.encounter
        if enc is None:
            return
        ctx = Ctx.of(self)
        observing = views.observers(self, enc)
        audience = scope(st, observing)
        covered = {x for ev in self.m.events for x in ev.sights}
        for p in enc.participants:
            v = ctx.view(p.id)
            prof = getattr(v, "profile", None)
            if prof is None or p.side == "party" or "/" not in prof.sanity:
                continue
            sheet = prof.sheet.partition(":")[2]
            horror = p.id
            free = paid or horror in covered or sheet in covered or (event is not None and (horror in event.sights or sheet in event.sights))
            present = set(observing)
            watching = [inv for inv in st.investigators if inv.id in present and inv.can_act
                        and (participant := enc.get(inv.id)) is not None and participant.presence == "in"
                        and not ctx.hidden_from(inv.id, horror)]
            if not watching:
                continue
            first = not any(horror in seen for seen in st.sightings.values())
            if first:
                face = f"creature-{sheet}" if f"creature-{sheet}" in self.m.pictures else views.portrait(self, horror)
                revealed = scope(st, [inv.id for inv in watching])
                await step(f"/enc/{enc.id}/reveal/{horror}", turn, lambda v=v, horror=horror, face=face, revealed=revealed: self.emit(self._filled({
                    "type": "reveal", "id": horror, "name": ctx.name(horror, "players"), "portrait": face,
                    "look": views.seen_look(v, 120), "audience": revealed})))
            for inv in watching:
                if horror in st.sightings.get(inv.id, []):
                    continue

                def pay(inv=inv, horror=horror, prof=prof, free=free) -> dict | None:
                    # The loss is rolled on a copy and lands with the sighting, together: a die that fails
                    # leaves neither, and the retry rolls (or replays) it (the review: a sighting kept
                    # before its dice skipped the loss for good).
                    if free:
                        st.sightings.setdefault(inv.id, []).append(horror)
                        return None
                    ok, bad = prof.sanity.split("/", 1)
                    trial = inv.model_copy(deep=True)
                    s = self._sanity(trial, ok.strip(), bad.strip())
                    inv.__dict__.update(trial.__dict__)
                    st.sightings.setdefault(inv.id, []).append(horror)
                    turn.sanity.append((inv.name, s))
                    st.add("sanity", s.describe(), who=inv.name, audience=audience)
                    return {"type": "sanity", "who": inv.name, **_sanity_view(s), **self._madness(inv, s, turn), "audience": audience}

                notice = await step(f"/enc/{enc.id}/sight/{inv.id}/{horror}", turn, pay)
                if notice:
                    await step(f"/enc/{enc.id}/sight/{inv.id}/{horror}/n", turn, lambda notice=notice: self.emit(notice))

    async def _scripted(self, ev: Event, turn: Turn) -> None:
        """An event's attack or pursuit (Event.fight, Event.chase), where the investigators are. An
        attack while a fight goes on joins it; nothing begins during a chase, with no investigator
        left who can act, or by those dead or gone. Begun, the next input is the first round."""
        st = self.state
        # Decided once, in the event's own stages: resumed after its fight began, it does not join it.
        how = await self._resolution_step("enc-how", turn, lambda: (
            "none" if self.chasing() or not any(i.can_act for i in st.investigators)
            else "join" if self.fighting() else "begin"))
        if ev.fight:
            if how == "none":
                return
            if how == "join":
                await self._join(ev.fight, ev, turn)
                return
            start = EncounterStart(mode="fight", foes=list(ev.fight), surprise=ev.surprise, reason=ev.title[:60], pace=None)
            await self._encounter_open(start, turn, event=ev)
            return
        if how != "begin":
            return
        start = EncounterStart(mode="chase", foes=list(ev.chase), reason=ev.title[:60], pace=None,
                               quarry="foe" if ev.chase_party == "追" else "party")
        await self._encounter_open(start, turn, event=ev)

    async def _event_effects(self, ev: Event, turn: Turn, *, at: int) -> None:
        """Land prepared bodily consequences; narration and flags never determine a body's state.

        Participants go through the same atomic unit as a combat action, so holds, assistance,
        pending choices and the encounter's ending follow the actual death. The original encounter
        id is journaled before applying anything: a retry after archive still finishes its notices.
        """
        def prepare() -> dict:
            planned = [e.model_copy(update={"target": profiles.combatant(self.world, e.target)}) for e in ev.effects]
            problems = effects.problems(self.world, planned)
            if problems:
                raise RuntimeError("事件身体后果没有落实：" + "；".join(problems))
            enc = self.world_encounter()
            involved = enc is not None and any(enc.get(e.target) is not None for e in planned)
            readers = self.world.observers(enc.place, at=at) if involved else []
            return {"effects": [e.model_dump(mode="json") for e in planned], "encounter": enc.id if involved else "",
                    "readers": readers, "local": self.state.investigator.id in readers}

        plan = await self._resolution_step("body-plan", turn, prepare)
        planned = effects.EFFECTS.validate_python(plan["effects"])
        eid = plan["encounter"]
        key = f"event/{ev.id}/effects"
        if eid:
            def apply(w: Work) -> None:
                for e in planned:
                    if w.enc.get(e.target) is not None:
                        w.apply(e.target, e)
                        w.unit.lines.append(f"{{{e.target}}}死了")
                    else:
                        w.unit.effects.append(e)

            await self._unit(key, turn, apply, at=at, local=plan["local"], readers=plan["readers"])
            enc = self.world_encounter()
            if enc is not None and enc.id == eid:
                how = self._outcome(Ctx.of(self, enc))
                if how:
                    await self._encounter_end(how, turn, eid=eid, local=plan["local"], readers=plan["readers"])
            elif self._ending_owed(eid):
                await self._encounter_end("", turn, eid=eid, local=plan["local"], readers=plan["readers"])
        else:
            def commit() -> None:
                with self.world.at(at):
                    result = effects.commit(self.world, planned, op_id=key, source=f"event:{ev.id}")
                if not result.ok:
                    raise RuntimeError("事件身体后果没有落实：" + "；".join(result.problems))

            await self._resolution_step("body-commit", turn, commit)

    def _coming(self, ids: list[str], *, fight_ok: bool) -> list[str]:
        """Of those sent at the investigators, who can come: the module's NPCs and creatures (a
        creature form as its NPC), not dead, not gone."""
        npcs = {n.id: n for n in self.m.npcs}
        out = []
        for x in dict.fromkeys(ids):
            who = profiles.combatant(self.world, x)
            if profiles.profile(self.world, x) is None:
                continue
            if who in npcs and npcs[who].status in ("死亡", "失踪"):
                continue
            body = effects.body_of(self.world, who)
            if body is not None and body.dead:
                continue
            if who not in out:
                out.append(who)
        return out

    def _dead(self, ident: str) -> bool:
        body = effects.body_of(self.world, profiles.combatant(self.world, ident))
        return body is not None and body.dead

    async def _join(self, ids: list[str], ev: Event, turn: Turn) -> None:
        """An event's attackers joining the fight going on: from the next round."""
        enc = self.encounter()
        assert enc is not None
        fresh = [x for x in self._coming(ids, fight_ok=True) if enc.get(x) is None]
        if not fresh:
            return

        def join(w: Work) -> None:
            for x in fresh:
                p = profiles.profile(self.world, x)
                w.enc.participants.append(Participant(id=x, kind="creature" if p and p.kind == "creature" else "npc", side="foe",
                                                      controller="keeper", surprised=ev.surprise == "foe", node=w.enc.place))
                w.change("joined", x)
                if self.world.place(x) not in ("", w.enc.place):
                    w.unit.effects.append(effects.Relocate(who=x, place=w.enc.place))
            w.unit.lines.append("【战斗】" + "、".join(f"{{{x}}}" for x in fresh) + "也扑了上来")
            w.unit.notices.append({"type": "fight_state", "fight": None, "lines": []})

        await self._unit(f"enc/{enc.id}/join/{ev.id}", turn, join)
        await self._sights(turn, paid=bool(ev.sanity), event=ev)

    # -- declaring and arranging ------------------------------------------------------------------
    def _declared(self, enc: EncounterState, pid: str, rnd: int) -> ActionReceipt | None:
        return next((r for r in reversed(enc.receipts) if r.actor == pid and r.round == rnd
                     and r.phase in ("declared", "awaiting_reaction")), None)

    async def _declare(self, turn: Turn, actor: str, d: DeclaredAction, source: str, ruling: Ruling | None) -> None:
        """A declaration for the round about to begin (or this round, when theirs is still to come)."""
        st = self.state
        enc = self.encounter()
        if enc is None:
            return

        def declare(w: Work) -> None:
            e = w.enc
            rnd = e.round + 1 if _between(e) else e.round
            if not _between(e) and any(r.actor == actor and r.round == rnd and r.phase in ("committed", "lapsed") for r in e.receipts):
                rnd += 1  # their slot has passed: it is next round's
            old = self._declared(e, actor, rnd)
            n = sum(1 for r in e.receipts if r.actor == actor and r.round == rnd)
            rid = f"{e.id}:r{rnd}:{actor}:{n}"
            if old is not None:
                old.phase = "cancelled"
            ruled = ruling.model_dump(mode="json") if ruling is not None and d.needs_ruling else None
            e.receipts.append(ActionReceipt(id=rid, round=rnd, actor=actor, source=source, declared=d,  # type: ignore[arg-type]
                                            summary=d.summary, ruling=ruled))
            me = e.get(actor)
            if me is not None and d.react:
                me.react = d.react

        await self._unit(f"enc/{enc.id}/decl/t{st.turn}/{actor}", turn, declare)

    async def _plan_unit(self, key: str, plan: dict, unsettled: list[str], turn: Turn) -> None:
        """The keeper plan's arrangements landing: what holds (plan_check.land) for the keeper's people,
        those joining, numbers filled, stakes, the pace; the rest noted for the keeper as 【未落实】."""
        enc = self.encounter()
        if enc is None:
            return
        parsed = EncounterTurnPlan.model_validate(plan)
        landed = plan_check.land(self, parsed, act=False)

        def apply(w: Work) -> None:
            e = w.enc
            upcoming = e.round + 1 if _between(e) else e.round
            for x in landed.join:
                prof = profiles.profile(self.world, x)
                e.participants.append(Participant(id=x, kind="creature" if prof and prof.kind == "creature" else "npc",
                                                  side="party", controller="keeper", node=e.place))
                w.change("joined", x)
            for a in landed.npcs:
                p = e.get(a.who)
                if p is None:
                    continue
                if a.side:
                    p.side = a.side  # type: ignore[assignment]
                p.arrangement, p.arranged_round, p.react = a, upcoming, a.react
                for held in a.release:
                    if w.free(held, by=a.who):
                        w.unit.lines.append(f"{{{a.who}}}主动松开了{{{held}}}")
                if a.speak and (prof := profiles.profile(self.world, a.who)) is not None and (prof.human or prof.kind == "npc"):
                    w.unit.extra.setdefault("voices", []).append(a.who)  # his own words, through the NPC's reply (Game.talk)
                if a.say.strip():
                    w.unit.lines.append(f"{{{a.who}}}喊：「{a.say.strip()}」")
                    w.unit.notices.append({"type": "fight_say", "who": a.who, "name": w.ctx.name(a.who, "players"), "text": a.say.strip()})
            for f in landed.fills:
                if f.key in ("备弹",):
                    v = w.ctx.view(f.who)
                    gun = next((x for x in (v.attacks() if v else []) if x.firearm and x.item), None)
                    if gun is not None:
                        w.unit.effects.append(effects.Ammo(item=gun.item, loaded=gun.loaded if gun.loaded is not None else gun.capacity,
                                                           spare=f.value))
                elif profiles.profile(self.world, f.who) is not None:
                    w.unit.effects.append(effects.Fills(who=f.who, values={f.key: f.value}))
            for s in landed.stakes:
                if not any(x.when == s.when and x.flags == s.flags for x in e.stakes):
                    e.stakes.append(StakeState(**s.model_dump(), source="keeper"))
            if landed.pace:
                e.clock.pace = landed.pace
            for c in landed.cover:  # where the keeper says they are: behind something, half or wholly
                e.scene.cover = [x for x in e.scene.cover if x.who != c.who] + [
                    Cover(who=c.who, what=c.what, partial=not c.full, since=e.round)]
            for x in landed.sight:  # who cannot see whom, as the keeper declares it (the dark, smoke, a wall)
                e.scene.unseen = [u for u in e.scene.unseen if (u.watcher, u.who) != (x.watcher, x.who)]
                if not x.sees:
                    e.scene.unseen.append(Unseen(watcher=x.watcher, who=x.who, why=x.why, since=e.round))
            for d in landed.distances:
                e.scene.distance[f"{d.a}|{d.b}"] = d.yards
            if landed.end is not None:
                e.history.append(f"end:{landed.end.how}:{','.join(landed.end.who)}")
            for x in unsettled:
                w.unit.lines.append(f"{UNSETTLED}{x}")

        value = await self._unit(key, turn, apply)
        turn.voices += [x for x in (value.get("extra") or {}).get("voices", []) if x not in turn.voices]
        if landed.end is not None:
            await self._propose_end(landed.end, turn)

    async def _propose_end(self, end, turn: Turn) -> None:
        """The keeper's own end of it (parley, surrender, someone giving up): those named stop, and the
        encounter ends that way unless someone still has to answer a choice."""
        enc = self.encounter()
        if enc is None or enc.pending is not None:
            return

        def stop(w: Work) -> None:
            for x in end.who:
                p = w.enc.get(x)
                if p is not None and p.presence == "in":
                    p.presence = "surrendered" if end.how == "surrender" else "left"
                    w.change("surrendered" if end.how == "surrender" else "fled", x)
            w.unit.lines.append(f"{'、'.join('{' + x + '}' for x in end.who)}：{end.terms or OUTCOME_WORDS.get(end.how, end.how)}")

        await self._unit(f"enc/{enc.id}/r{enc.round}/proposal", turn, stop)
        ctx = Ctx.of(self)
        if any(ctx.side(x) == "party" for x in _lingering(ctx)):
            # Someone of theirs is dying (p. 101: CON at every round's end until First Aid holds it or they
            # die): the rounds go on, and it ends as proposed once nobody is (_outcome, _proposed).
            return
        await self._encounter_end(END_HOW[end.how], turn)

    # -- the beat ---------------------------------------------------------------------------------
    async def _beat(self, turn: Turn, *, settle: bool = False) -> None:
        """The encounter goes on, unit after unit, until it waits for someone (a player's answer, the
        next declaration), changes into a chase, or ends."""
        await self._replay(turn)
        started = 0
        for _ in range(400):
            enc = self.encounter()
            if enc is None or enc.pending is not None:
                return
            if _between(enc):
                how = self._outcome(Ctx.of(self))
                if how:
                    await self._encounter_end(how, turn)
                    return
                ctx = Ctx.of(self)
                nxt = enc.round + 1
                humans = [p.id for p in enc.participants if p.controller == "human" and ctx.acting(p.id)]
                waiting = [x for x in humans if self._declared(enc, x, nxt) is None]
                # With a human who can act, the round waits for them; with none (autoplay, watching), the
                # declaration of whoever's input this was settles it (the review: it never began).
                declared = any(self._declared(enc, p.id, nxt) is not None for p in enc.participants
                               if p.kind == "investigator" and (p.controller == "human" or not humans))
                # Nobody at this table can act (the investigators its players play are all down): the rounds go
                # on by themselves, each shown as it settles, until it ends or one of them can act again (the
                # user's solo game of 2026-10-22 sat at an empty input box with 托马斯 dying).
                alone = unattended(ctx)
                if not alone and ((waiting and not settle) or (not declared and not settle) or started):
                    return
                started += 1
                await self._round_start(enc, nxt, turn, settle or alone)
                continue
            if enc.cursor < len(enc.order):
                if not await self._slot(enc, turn):
                    return
                continue
            await self._round_end(enc, turn)

    async def _replay(self, turn: Turn) -> None:
        """What units this journal already landed still owe (a broken connection, a crash after the op):
        their notices, the rest of a ruling, their flags (a stake's, at a round's end the retry does not
        come back to)."""
        journal = self._resolution
        if journal is None:
            return
        steps = journal.get("steps", {})
        for key in list(journal.get("enc-units", [])):
            row = steps.get(key)
            if row and row.get("done") and isinstance(row.get("value"), dict):
                await self._owed(key, row["value"], turn)

    async def _unit(self, key: str, turn: Turn, fn, *, at: int | None = None, local: bool = True,
                    readers: list[str] | None = None) -> dict:
        """One unit: `fn` works on a copy (resolve.Work) with the journal's dice, then one op lands it
        all; its lines join the keeper's account, its notices go out after."""
        if self._resolution is not None:
            units = self._resolution.setdefault("enc-units", [])
            if key not in units:
                units.append(key)

        def settle() -> dict:
            ctx = Ctx.of(self)
            audience = scope(self.state, views.observers(self, ctx.enc) if readers is None else readers)
            w = Work(self, ctx, key)
            fn(w)
            changes = w.changes()
            # what the unit did to an obstacle for good stays in this game's world, for the next chase there
            w.unit.effects += [effects.Obstacle(way=k, obstacle=ob) for k, ob in site.kept(
                self.m, ctx.enc.chase.site if ctx.enc.chase else None, w.enc.chase.site if w.enc.chase else None)]
            if changes or w.unit.effects:
                result = self.world.apply(EncounterStep(id=key, encounter=ctx.enc.id, unit=key.rsplit("/", 1)[-1],
                                                        effects=w.unit.effects, changes=changes, actor=ENGINE))
                if not result.ok:
                    raise RuntimeError("遭遇结算没有落地：" + "；".join(result.problems))
            after = Ctx.of(self, self.state.encounter or w.enc)
            kn, pn = views.names(after, "keeper"), views.names(after, "players")
            if local:
                turn.fight += [telling.render(x, kn) for x in w.unit.lines]
                turn.narrating["encounter_snapshot"] = views.settled_text(self.world, include_results=False)
            public = [w.unit.public.get(i, x) for i, x in enumerate(w.unit.lines)]
            shown = [telling.render(x, pn) for x in public if x and not x.startswith(KEEPER_ONLY)]
            notices = []
            for ev in w.unit.notices:
                ev = dict(ev)
                if ev.get("line"):
                    if any(self.state.perceptions.values()):
                        ev["_entity_line"] = ev["line"]
                    ev["line"] = telling.render(ev["line"], pn)
                notices.append({"audience": audience, **ev})
            now = self.state.encounter or w.enc
            if now.phase != "ended" and (shown or any(ev["type"] == "fight_act" for ev in notices)):
                templates = [x for x in public if x and not x.startswith(KEEPER_ONLY)]
                notice = ({"type": "chase_state", "chase": None, "lines": shown} if now.mode == "chase" else
                          {"type": "fight_state", "fight": None, "lines": shown})
                if any(self.state.perceptions.values()):
                    notice["_entity_lines"] = templates
                notices.append({**notice, "audience": audience})
            if w.unit.lines and (st := self.state):
                templates = [x for x in public if x and not x.startswith(KEEPER_ONLY)]
                for template, x in zip(templates, shown, strict=True):
                    if not x.startswith("【"):
                        personal = {viewer: {"text": telling.render(template, views.names(after, viewer)), "who": ""}
                                    for viewer, masks in st.perceptions.items() if masks}
                        st.add("dice", x, presented=personal, audience=audience)
            return {"notices": [self._filled(ev) for ev in notices], "rest": w.unit.rest, "flags": w.unit.flags,
                    "fired": w.unit.fired, "extra": w.unit.extra}

        def run() -> dict:
            with self.world.at(self.world.now if at is None else at):
                return settle()

        value = await self._resolution_step("/" + key, turn, run)
        await self._owed(key, value, turn)
        return value

    async def _owed(self, key: str, value: dict, turn: Turn) -> None:
        """What a unit owes once its op has landed, each once: its notices, the rest of an `other`
        ruling, its flags."""
        await self._notify(key, value.get("notices", []), turn)
        if value.get("rest"):
            await self._settle_rest(key, value["rest"], turn)
        if value.get("flags"):
            await self._resolution_step(f"/{key}/flags", turn, lambda: self._flags(value["flags"], turn))

    async def _notify(self, key: str, notices: list[dict], turn: Turn) -> None:
        for k, ev in enumerate(notices):
            await self._resolution_step(f"/{key}/n{k}", turn, lambda ev=ev: self.emit(ev))

    def _filled(self, ev: dict) -> dict:
        """A notice as the screens get it: with the fight as it now stands, for those that carry it, and
        no one's own id but an investigator's (views.masked: never "douglas" behind 犬脸的佝偻身影)."""
        enc = self.state.encounter
        if "fight" in ev and ev["fight"] is None:
            ev = {**ev, "fight": views.fight_view(self, enc) if enc is not None else {"round": 0, "place": "", "fighters": []}}
        if "chase" in ev and ev["chase"] is None:
            ev = {**ev, "chase": views.chase_view(self, enc) if enc is not None else {"round": 0, "quarry": "party", "legs": [],
                                                                                         "runners": []}}
        return views.masked(ev, enc)

    async def _settle_rest(self, key: str, rest: dict, turn: Turn) -> None:
        """The rest of an `other` ruling for the investigator whose action it was (the review: most of
        a ruling used to be dropped): its flags, what it changes, takes and equips, and its clues."""
        from ...agents.cast import Ruling
        from ...world import adapters

        ruling = Ruling.model_validate({**rest["ruling"], "effects": []})  # its effects landed with the unit
        step = self._resolution_step
        if not rest.get("passed"):
            return
        if ruling.set_flags:
            await step(f"/{key}/rest-flags", turn, lambda: self._flags(ruling.set_flags, turn))

        def changes() -> None:
            adapters.settle_changes(self.world, ruling.changes, source="arbiter")
            self._equipment(ruling, turn)
            self._takes(ruling, turn)

        await step(f"/{key}/rest-changes", turn, changes)
        if ruling.rejoin:
            await step(f"/{key}/rest-rejoin", turn, lambda: self._rejoin(ruling.rejoin, turn))
        clues = await step(f"/{key}/rest-clues", turn, lambda: self._valid_clues(ruling.clues_on_success + ruling.auto_clues, None))
        if clues:
            await step(f"/{key}/rest-gain", turn, lambda: self._gain(clues, turn, None))

    # -- a round ----------------------------------------------------------------------------------
    async def _round_start(self, enc: EncounterState, nxt: int, turn: Turn, settle: bool) -> None:
        """Before a round: AI companions pick, the keeper's people are arranged when they need it, then
        the order by DEX (+50 for a readied gun about to be fired)."""
        ctx = Ctx.of(self)
        for p in enc.participants:
            if p.controller == "ai" and ctx.acting(p.id) and self._declared(enc, p.id, nxt) is None:
                await self._ai_declare(p.id, nxt, turn)
        enc = self.encounter()
        assert enc is not None
        if getattr(self.cast, "plan", None) is not None and any(
                choices.needs_arrangement(enc, p) and ctx.acting(p.id) for p in enc.participants):
            checked = await self._resolution_step(f"/enc/{enc.id}/r{nxt}/arrange", turn, lambda: self._arrange_call())
            if checked:
                await self._plan_unit(f"enc/{enc.id}/r{nxt}/arranged", checked["plan"], checked["unsettled"], turn)

        def start(w: Work) -> None:
            e = w.enc
            chase = e.mode == "chase"
            if settle:  # the window closed: a player who put nothing in only defends this round (in a chase, runs on)
                for p in e.participants:
                    if p.kind == "investigator" and w.ctx.acting(p.id) and self._declared(e, p.id, nxt) is None:
                        d = DeclaredAction(do="defend", summary="只防守")
                        if chase and (offered := choices.options(self, p.id, ctx=w.ctx)):
                            d = DeclaredAction.model_validate(offered[0].value)
                        e.receipts.append(ActionReceipt(id=f"{e.id}:r{nxt}:{p.id}:0", round=nxt, actor=p.id, source="table",
                                                        declared=d, summary=d.summary))
            declared = {r.actor: r.declared for r in e.receipts if r.round == nxt and r.phase == "declared"}
            for p in e.participants:
                if p.controller == "keeper" and p.arrangement is not None and p.arrangement.actions:
                    declared.setdefault(p.id, p.arrangement.actions[0])
            order = chasing.order(w) if chase else round_order(w.ctx, declared)
            lost = dict(e.budget.lost_next)
            owed = dict(e.budget.owed)
            # the round before last folds into the history: its receipts' lines, compact
            old = [r for r in e.receipts if r.round < nxt - 1]
            if old:
                e.history += [f"第 {r.round} 轮：" + "；".join(r.lines) for r in old if r.lines]
                e.receipts = [r for r in e.receipts if r.round >= nxt - 1]
            e.round, e.order, e.cursor, e.budget = nxt, order, 0, RoundBudget(lost_next=lost, owed=owed)
            if chase:
                chasing.round_points(w, order)
                where = "；".join(f"{{{x}}}在{chasing.node_name(e, chasing.at(e, x))}，{e.budget.movement.get(x, 0)} 行动点"
                                 for x in order)
                w.unit.lines.append(f"【追逐 · 第 {nxt} 轮】先后：" + " → ".join(f"{{{x}}}" for x in order) + f"。{where}")
                w.unit.notices.append({"type": "chase_round", "round": nxt, "chase": None})
                return
            w.unit.lines.append(f"【战斗 · 第 {nxt} 轮】先后：" + " → ".join(f"{{{x}}}" for x in order))
            w.unit.notices.append({"type": "fight_round", "round": nxt, "order": order, "fight": None})

        await self._unit(f"enc/{enc.id}/r{nxt}/start", turn, start)

    async def _arrange_call(self) -> dict | None:
        checked = await self._ask_plan("（这一轮开始前，安排守秘人控制的人）", "arrange")
        if checked is None or checked.value is None:
            return {"plan": EncounterTurnPlan().model_dump(mode="json", by_alias=True),
                    "unsettled": [f"这一轮的安排没有落实：{'；'.join(checked.problems if checked else [])}"]}
        return {"plan": checked.value.model_dump(mode="json", by_alias=True), "unsettled": []}

    async def _ai_declare(self, pid: str, rnd: int, turn: Turn) -> None:
        """An AI companion's pick among the round's choices (Cast.encounter_choice), from what the
        players' screens show, with how it answers blows from now on in a fight (as a player's Tab
        sets it, held to the same: _stance_problem); without a valid pick, it only defends."""
        enc = self.encounter()
        assert enc is not None
        ctx = Ctx.of(self)
        offered = choices.options(self, pid, ctx)
        stances = choices.presets(ctx, pid) if enc.mode == "fight" else None

        async def pick() -> dict | None:
            chooser = getattr(self.cast, "encounter_choice", None)
            if chooser is None or not offered:
                return None
            situation = self._public_situation(pid)
            checked = await chooser(self.state, pid, situation, offered, stances)
            answer = checked.value
            chosen = next((o for o in offered if answer is not None and o.id == answer.choice), None)
            if chosen is None:
                return None
            return {**chosen.value, "react": answer.react if answer.react in (stances or []) else chosen.value.get("react", "")}

        value = await self._resolution_step(f"/enc/{enc.id}/r{rnd}/ai/{pid}", turn, pick)
        d = DeclaredAction.model_validate(value) if value else DeclaredAction(do="defend", summary="只防守")

        def declare(w: Work) -> None:
            w.enc.receipts.append(ActionReceipt(id=f"{w.enc.id}:r{rnd}:{pid}:0", round=rnd, actor=pid, source="ai", declared=d,
                                                summary=d.summary))
            me = w.enc.get(pid)
            if me is not None and d.react and not _stance_problem(w.ctx, pid, d.react):
                me.react = d.react

        await self._unit(f"enc/{enc.id}/r{rnd}/ai/{pid}/decl", turn, declare)

    def _public_situation(self, pid: str) -> str:
        view = views.fight_view(self, viewer=pid)
        rows = [f"第 {view['round']} 轮战斗。"]
        for f in view["fighters"]:
            hp = f"生命 {f['hp']}/{f['hp_max']}" if f["hp"] is not None else f["wound"]
            rows.append(f"- {f['name']}（{'我方' if f['side'] == 'party' else '对手'}）：{hp}" + (f"，{f['status']}" if f["status"] else ""))
        enc = self.encounter()
        if enc is not None and enc.mode == "fight":
            rows.append(f"你被攻击时：{actions.REACT_WORDS[choices.preset(Ctx.of(self), pid)]}")
        return "\n".join(rows)

    def _by_hand(self, actor: str) -> bool:
        """The player throws this investigator's dice themselves: the one at the screen (graph: by_hand),
        or at a LAN table every seated player, whoever's input settles the round (`by_hand_seats`)."""
        st = self.state
        if actor in self.by_hand_seats:
            return True
        return bool(st.pending_turn.get("by_hand")) and actor == st.investigator.id and actor in st.human

    async def _slot(self, enc: EncounterState, turn: Turn) -> bool:
        """The next unit of the slot the cursor is at; False when the round waits for an answer."""
        ctx = Ctx.of(self)
        c = enc.cursor
        actor = enc.order[c]
        base = f"enc/{enc.id}/r{enc.round}/s{c}"
        p = ctx.p(actor)

        def advance(w: Work, receipt: ActionReceipt | None = None, phase: str = "committed", why: str = "") -> None:
            if receipt is not None:
                r = next(x for x in w.enc.receipts if x.id == receipt.id)
                r.phase, r.lapsed = phase, why  # type: ignore[assignment]
            w.enc.cursor = c + 1

        if p is None or not ctx.acting(actor):
            await self._unit(f"{base}/skip", turn, lambda w: advance(w, self._declared(enc, actor, enc.round), "lapsed", "倒下了"))
            return True
        r = self._declared(enc, actor, enc.round)
        if (lost := enc.budget.lost_next.get(actor)) and (r is None or not r.settled):
            def spend(w: Work) -> None:
                del w.enc.budget.lost_next[actor]
                if actor in w.enc.scene.prone and lost == "爬起来":
                    w.enc.scene.prone.remove(actor)
                w.unit.lines.append(f"{{{actor}}}挣扎着从地上爬起来" if lost == "爬起来" else f"{{{actor}}}还伏在掩体后面，这一轮没能出手")
                advance(w, r, "lapsed", lost)

            await self._unit(f"{base}/lost", turn, spend)
            return True
        if r is None:
            if p.controller == "keeper":
                return await self._keeper_slot(enc, actor, base, turn, advance)
            await self._unit(f"{base}/idle", turn, lambda w: (w.unit.lines.append(f"{{{actor}}}这一轮没有出手"), advance(w)))
            return True
        d = r.declared
        if d.takes:
            await self._take(r, base, turn)
            enc = self.encounter()
            assert enc is not None
            r = self._declared(enc, actor, enc.round) or r
            d = r.declared
        problems = actions.preconditions(ctx, actor, d) if not r.settled and not r.moved else []
        if problems:
            if (p.controller == "human" and r.source not in ("table",) and self.asking and not self.holding
                    and self.state.round_spent is None):
                await self._unit(f"{base}/redeclare/{r.id}", turn, lambda w: self._ask_again(w, r, problems))
                return False
            line = f"{{{actor}}}没能{actions.VERBS.get(d.do, d.do)}：{actions.said(problems[0])}"  # in the names each one knows, no ids
            await self._unit(f"{base}/lapse", turn, lambda w: (w.unit.lines.append(line), advance(w, r, "lapsed", problems[0])))
            return True
        if d.after and (wait := self._delay(enc, r)) is not None:  # waiting for someone to act first (pp. 86, 113)
            await self._unit(f"{base}/delay/{r.id}", turn, wait)
            return True
        rolls = d.do in ATTACKING or d.do == "aid" or (d.needs_ruling and (r.ruling or {}).get("needs_roll"))
        if self.asking and self._by_hand(actor) and rolls and not r.prompted:
            await self._unit(f"{base}/prompt", turn, lambda w: self._prompt(w, r))
            return False
        if enc.mode == "chase":
            return await self._chase_slot(enc, r, base, turn, advance)
        if d.do not in ATTACKING:
            def settle(w: Work) -> None:
                act(w, r, ruling=r.ruling)
                advance(w, r)

            await self._unit(f"{base}/act", turn, settle)
            return True
        return await self._attacks(enc, r, base, turn, lambda w: advance(w, r))

    def _delay(self, enc: EncounterState, r: ActionReceipt):
        """A declaration that waits for `after` to act first: its slot moves to just after theirs, once
        (the book lets anyone delay; two who wait for each other both end up acting). None when there is
        nothing to wait for (they acted already, or are not coming)."""
        later = enc.order[enc.cursor + 1:]
        if r.declared.after not in later:
            return None

        def wait(w: Work) -> None:
            e = w.enc
            rr = next(x for x in e.receipts if x.id == r.id)
            rr.declared = rr.declared.model_copy(update={"after": ""})
            order = list(e.order)
            me = order.pop(e.cursor)
            order.insert(order.index(r.declared.after, e.cursor) + 1, me)
            e.order = order
            w.unit.lines.append(f"{{{r.actor}}}等着{{{r.declared.after}}}先动")

        return wait

    async def _attacks(self, enc: EncounterState, r: ActionReceipt, base: str, turn: Turn, finish) -> bool:
        """The attacks of a declaration, one unit each, on the actor's slot: each one's answer decided (or
        asked) before any of its dice, then its roll; `finish` lands with the last of them."""
        ctx = Ctx.of(self)
        actor, d = r.actor, r.declared
        av = ctx.view(actor)
        picks = actions.picks(d, av.attacks_per_round())
        k = r.settled
        if k >= len(picks):
            await self._unit(f"{base}/done", turn, finish)
            return True
        pick = picks[k]
        last = k + 1 >= len(picks)
        why = ctx.hidden_from(actor, pick.target) or ("" if ctx.present(pick.target) and ctx.alive(pick.target)
                                                      else "已经死了" if not ctx.alive(pick.target) else "已经不在现场")
        reach = actions.out_of_reach(ctx, actor, d.model_copy(update={"attacks": [pick]})) if not why and k == 0 else []
        if why or reach:
            def miss(w: Work) -> None:
                rr = next(x for x in w.enc.receipts if x.id == r.id)
                rr.settled = len(picks) if reach else k + 1  # out of reach: none of its attacks can be made
                said = "；".join(reach) if reach else f"{{{pick.target}}}{why}"
                line = f"{{{actor}}}的第 {k + 1} 下没有出手：{said}" if len(picks) > 1 and not reach else f"{{{actor}}}没有出手：{said}"
                rr.lines.append(line)
                w.unit.lines.append(line)
                if last or reach:
                    finish(w)

            await self._unit(f"{base}/a{k}/gone", turn, miss)
            return True
        if len(r.reactions) <= k:
            asked = await self._react(r, k, pick, base, turn)
            return not asked
        if r.reactions[k].chosen is None:
            return False

        def roll(w: Work) -> None:
            strike(w, r, k, pick)
            if last:
                finish(w)

        await self._unit(f"{base}/a{k}/roll", turn, roll)
        return True

    async def _chase_slot(self, enc: EncounterState, r: ActionReceipt, base: str, turn: Turn, advance) -> bool:
        """A slot in a chase (pp. 113–121): running first for a running declaration (then its attack, if it
        caught up), an attack first for an attack (then running on, if it named where); hiding, a barrier,
        turning to fight; anything else costs a point and is settled as in a fight."""
        d, actor = r.declared, r.actor
        moving = d.do in chasing.MOVING or (d.to and d.do not in ATTACKING)
        if moving and not r.moved:
            def go(w: Work) -> None:
                if chasing.points(w.enc, actor) < 1:
                    owed = w.enc.budget.owed.get(actor, 0)
                    why = f"还在补上一轮耽搁的行动点（欠 {owed}）" if owed else "这一轮的行动点用完了"
                    w.unit.lines.append(f"{{{actor}}}这一轮没能跑：{why}")
                    advance(w, r, "lapsed", why)
                    return
                chasing.run(w, r)
                if not d.attacks or w.enc.get(actor).presence != "in":  # type: ignore[union-attr]
                    advance(w, r)

            await self._unit(f"{base}/run", turn, go)
            return True
        if d.attacks and (d.do in ATTACKING or d.do == "pursue"):
            av = Ctx.of(self).view(actor)
            if r.settled < len(actions.picks(d, av.attacks_per_round())):
                def finish(w: Work) -> None:
                    rr = next(x for x in w.enc.receipts if x.id == r.id)
                    if not (d.do == "shoot" and d.to) and any(x.purpose in ("attack", "shoot", "maneuver") for x in rr.rolls):
                        chasing.pay(w, actor, chasing.RULES.attack_cost)  # stopping to shoot or strike: a point (pp. 116, 120)
                    if not (d.to and d.do in ATTACKING and not rr.moved):
                        advance(w, r)

                return await self._attacks(enc, r, base, turn, finish)
        if d.to and not r.moved and d.do in ATTACKING:  # attacked, now runs on (shooting on the move)
            def on(w: Work) -> None:
                chasing.run(w, r)
                advance(w, r)

            await self._unit(f"{base}/run", turn, on)
            return True

        def settle(w: Work) -> None:
            rr = next(x for x in w.enc.receipts if x.id == r.id)
            if d.do == "hide":
                chasing.hide(w, rr)
            elif d.do == "barrier":
                chasing.barrier(w, rr)
            elif d.do == "stand":
                chasing.stand(w, rr)
            elif d.do in chasing.FREE:
                act(w, r, ruling=r.ruling)
            elif chasing.points(w.enc, actor) < 1:
                why = "这一轮的行动点用完了"
                w.unit.lines.append(f"{{{actor}}}没能{actions.VERBS.get(d.do, d.do)}：{why}")
                advance(w, r, "lapsed", why)
                return
            else:
                chasing.pay(w, actor, chasing.RULES.act_cost)
                act(w, r, ruling=r.ruling)
            advance(w, r)

        await self._unit(f"{base}/act", turn, settle)
        return True

    async def _keeper_slot(self, enc: EncounterState, actor: str, base: str, turn: Turn, advance) -> bool:
        """A keeper's person on its slot: the first of its arranged actions it can do now; none, one
        scoped keeper-plan call for it; still none, it does nothing this round (noted 【未落实】)."""
        ctx = Ctx.of(self)
        p = ctx.p(actor)
        assert p is not None
        able = [a for a in (p.arrangement.actions if p.arrangement else []) if not actions.preconditions(ctx, actor, a)
                and not actions.out_of_reach(ctx, actor, a)]
        asked = ((self._resolution or {}).get("steps") or {}).get(f"{base}/replan", {}).get("done")
        if not able and getattr(self.cast, "plan", None) is not None and not asked:
            # Asked once a slot: a call that failed (a timeout) is asked again when the turn is retried.
            checked = await self._resolution_step(f"/{base}/replan", turn, lambda: self._arrange_call())
            if checked:
                await self._plan_unit(f"{base}/replanned", checked["plan"], checked["unsettled"], turn)
            return True
        chosen = able[0] if able else None
        if chosen is not None and enc.mode == "chase" and chosen.do == "pursue" and not chosen.attacks:
            # running after someone and striking when it gets there, as the arrangement means (p. 116)
            strike_at = next((a for a in (p.arrangement.actions if p.arrangement else [])
                              if a.do in ("attack", "maneuver") and a.attacks and a.attacks[0].target == chosen.target), None)
            if strike_at is not None:
                chosen = chosen.model_copy(update={"attacks": strike_at.attacks, "maneuver": strike_at.maneuver})

        def declare(w: Work) -> None:
            e = w.enc
            rid = f"{e.id}:r{e.round}:{actor}:0"
            if chosen is None:
                why = "这一轮的安排都做不到" if p.arrangement else "没有安排"
                e.receipts.append(ActionReceipt(id=rid, round=e.round, actor=actor, source="keeper",
                                                declared=DeclaredAction(do="defend", summary="没有行动"), summary="没有行动",
                                                phase="lapsed", lapsed=why))
                w.unit.lines.append(f"{UNSETTLED}{{{actor}}}{why}，这一轮没有行动（挨打时照人物卡应对）")
                w.enc.cursor = e.cursor + 1
                return
            e.receipts.append(ActionReceipt(id=rid, round=e.round, actor=actor, source="keeper", declared=chosen,
                                            summary=chosen.summary))

        await self._unit(f"{base}/decl", turn, declare)
        return True

    async def _take(self, r: ActionReceipt, base: str, turn: Turn) -> None:
        """What the action picks up first (DeclaredAction.takes) registered in hand (world/ops.Bring);
        an attack written with its name then uses that very thing."""
        step = self._resolution_step

        def bring() -> dict:
            made: dict[str, str] = {}
            notes = []
            for i, t in enumerate(r.declared.takes):
                result = self.world.apply(Bring(id=f"{base}/takes/{i}", name=t.name, look=t.look, holder=r.actor, actor=r.actor))
                if result.ok and result.data:
                    made[t.name] = result.data
                else:
                    notes.append(f"{UNSETTLED}{t.name}：{'；'.join(result.problems)}")
            return {"made": made, "notes": notes}

        done = await step(f"/{base}/takes", turn, bring)

        def use(w: Work) -> None:
            rr = next(x for x in w.enc.receipts if x.id == r.id)
            v = w.ctx.view(r.actor)
            attacks = []
            for pick in rr.declared.attacks:
                item = done["made"].get(pick.with_)
                if item:
                    opt = next((a for a in v.attacks() if a.item == item), None)
                    pick = pick.model_copy(update={"with_": opt.id if opt else item})
                attacks.append(pick)
            rr.declared = rr.declared.model_copy(update={"attacks": attacks, "takes": []})
            w.unit.lines += done["notes"]
            if done["made"]:
                w.unit.lines.append(f"{{{r.actor}}}抄起了" + "、".join(done["made"]))

        await self._unit(f"{base}/takes/use", turn, use)

    def _ask_again(self, w: Work, r: ActionReceipt, problems: list[str]) -> None:
        """A player's declaration went stale before their slot (the target fell): they choose again,
        told why in their own words (actions.said: the names they know, the things' own names)."""
        offered = choices.options(self, r.actor, w.ctx)
        rr = next(x for x in w.enc.receipts if x.id == r.id)
        rr.phase = "awaiting_reaction"
        why = telling.render(actions.said(problems[0]), views.names(w.ctx, "players"))
        w.enc.pending = PendingChoice(id=f"{r.id}:redeclare", kind="redeclare", who=r.actor, receipt=r.id,
                                      prompt=f"原来的行动做不成了：{why}。这一下改做什么？",
                                      options=offered, default=next((o.id for o in offered if o.value.get("do") == "defend"), offered[0].id if offered else ""))

    def _prompt(self, w: Work, r: ActionReceipt) -> None:
        """The player throws their own dice: shown what the roll is before its first die."""
        st = self.state
        rr = next(x for x in w.enc.receipts if x.id == r.id)
        rr.prompted = True
        v = w.ctx.view(r.actor)
        d = r.declared
        skill, target = "格斗(斗殴)", v.skill("格斗(斗殴)")
        if d.do in ATTACKING and d.attacks:
            opt = actions.option(v, d.attacks[0].with_, d.do)
            if opt is not None:
                skill, target = actions.skill_of(opt, d.do), opt.value if d.do != "maneuver" else v.skill("格斗(斗殴)")
        elif d.do == "aid":
            skill, target = "急救", v.skill("急救")
        elif r.ruling:
            skill = r.ruling.get("skill") or skill
            target = v.skill(skill)
        difficulty = (r.ruling or {}).get("difficulty", "常规")
        need = {"困难": target // 2, "极难": target // 5}.get(difficulty, target)
        p = w.enc.get(r.actor)
        bonus = int((r.ruling or {}).get("bonus", 0)) + sum(
            e.dice for e in (p.edges if p else []) if e.until == "next_roll" and not e.source.startswith("aim"))
        if d.do in ("attack", "maneuver") and w.ctx.held_by(r.actor):
            bonus -= 1
        bonus = max(-2, min(2, bonus))
        payload = {"who": st.find(r.actor).name, "skill": skill, "difficulty": difficulty, "bonus": bonus, "target": target,
                   "hard": target // 2, "extreme": target // 5, "need": need, "action": r.summary, "why": ""}
        w.enc.pending = PendingChoice(id=f"{r.id}:roll", kind="roll", who=r.actor, receipt=r.id, prompt=r.summary,
                                      options=[ChoiceOption(id="roll", label="掷骰", value=payload)], default="roll",
                                      free_text=False)

    async def _react(self, r: ActionReceipt, k: int, pick, base: str, turn: Turn) -> bool:
        """How the defender answers attack `k`: asked, before any of its dice, when the policy says so
        (choices.should_ask); else the preset, the keeper's arrangement or the rules' default.
        True when it stopped to ask."""
        ctx = Ctx.of(self)
        av = ctx.view(r.actor)
        opt = actions.option(av, pick.with_, r.declared.do)
        kind = choices.attack_kind(opt) if opt is not None else "melee"
        options = choices.reaction_options(ctx, pick.target, kind)
        ask = opt is not None and self.asking and not self.holding and choices.should_ask(ctx, pick.target, r.actor, opt, kind, options)
        from .resolve import react_choice

        def decide(w: Work) -> None:
            rr = next(x for x in w.enc.receipts if x.id == r.id)
            if ask:
                dp = w.enc.get(pick.target)
                assert dp is not None
                dp.asked = True
                dp.risk = max(dp.risk, choices.risk(ctx, pick.target, r.actor, opt, kind))  # asked at this danger: not again for it
                rr.phase = "awaiting_reaction"
                rr.reactions.append(ReactionReceipt(attack=k, attacker=r.actor, defender=pick.target, options=options,  # type: ignore[arg-type]
                                                    asked=True))
                default = dp.react if dp.react in options else options[0]
                weapon = f"抄起{opt.name}" if opt is not None and not opt.natural else ""
                w.enc.pending = PendingChoice(
                    id=f"{r.id}:react:{k}", kind="reaction", who=pick.target, receipt=r.id,
                    prompt=f"{ctx.name(r.actor, pick.target)}{weapon}{'朝你开枪' if kind == 'firearm' else '向你扑来'}，你怎么应对？",
                    options=[ChoiceOption(id=x, label=choices.REACTION_LABELS[x], note=choices.reaction_note(ctx, pick.target, x))
                             for x in options], default=default)
                return
            chosen, by = react_choice(w.ctx, pick.target, kind)
            reason = choices.reaction_reason(w.ctx, pick.target)
            if chosen == "none" and not reason:
                reason = "按预设不应对" if by == "preset" else "守秘人安排不应对"
            rr.reactions.append(ReactionReceipt(attack=k, attacker=r.actor, defender=pick.target, options=options,  # type: ignore[arg-type]
                                                chosen=chosen, by=by, reason=reason))  # type: ignore[arg-type]

        await self._unit(f"{base}/a{k}/react", turn, decide)
        return ask

    async def _round_end(self, enc: EncounterState, turn: Turn) -> None:
        """The end of a round (p. 101; design §3.4): the dying, healing, surprise over; the clock; who
        fled is chased or gets away; the stakes that now hold."""
        step = self._resolution_step
        n = enc.round
        base = f"enc/{enc.id}/r{n}/end"
        value = self._landed(base)  # resumed after it landed: what it owes is in the journal
        if enc.cursor == _end_slot(enc):
            def end(w: Work) -> None:
                round_end(w)
                w.enc.completed_rounds += 1
                w.enc.clock = clock.tick(w.enc.clock)
                if w.enc.mode == "chase":
                    chasing.round_end(w)
                else:
                    foes_up = [p.id for p in w.enc.participants if p.side == "foe" and p.presence == "in" and w.standing(p.id)]
                    lingering = [p.id for p in w.enc.participants if p.presence == "in" and
                                 ((w.body(p.id).dying and not w.body(p.id).stabilized) or w.body(p.id).regenerating)]
                    if not foes_up and lingering and w.enc.phase == "open":
                        w.enc.phase = "aftermath"
                        w.unit.lines.append("对手已经倒下，还有人要救、要收拾：接下来一轮一轮善后")
                w.enc.cursor = _end_slot(w.enc) + 1

            value = await self._unit(base, turn, end)
        await step(f"/{base}/time", turn, lambda: self._clock_minutes())
        await step(f"/{base}/clock", turn, lambda: self._time(turn))

        def snapshot() -> None:
            with self.world.at(self.state.minutes):
                turn.narrating["encounter_snapshot"] = views.settled_text(self.world, include_results=False)

        if there := (value.get("extra") or {}).get("arrive"):
            # Caught up in another place: the op moved the fight and the party there (chase.to_fight); what
            # arriving brings about (the scene, its events) as any arrival.
            await self._arrive(there, turn)
        enc = self.encounter()
        if enc is None:
            await step(f"/{base}/snapshot", turn, snapshot)
            return
        if enc.cursor == _end_slot(enc) + 1:
            await self._switch(enc, turn)
        enc = self.encounter()
        if enc is not None:
            await self._stakes(enc, turn, f"enc/{enc.id}/r{n}/stakes")
        await step(f"/{base}/snapshot", turn, snapshot)

    def _clock_minutes(self) -> None:
        """The whole minutes the encounter owes the game clock, added once with the encounter's own
        note that they are (one synchronous step: a resumed turn never counts them twice)."""
        enc = self.state.encounter
        if enc is None:
            return
        owed = clock.owed(enc.clock)
        if not owed:
            return
        self.state.minutes += owed
        result = self.world.apply(EncounterStep(id=f"enc/{enc.id}/r{enc.round}/paid", encounter=enc.id, unit="paid",
                                                changes=[{"kind": "clock", "clock": clock.paid(enc.clock).model_dump()}],  # type: ignore[list-item]
                                                actor=ENGINE))
        if not result.ok:
            raise RuntimeError("；".join(result.problems))

    async def _switch(self, enc: EncounterState, turn: Turn) -> None:
        """Whoever fled: who goes after them. The keeper's people say so in their arrangements (asked
        once if they did not; an explicit "no" is obeyed; no answer, no chase, noted), an investigator
        by pursuing them (追赶, offered the round after a foe ran); nobody goes after someone they
        cannot perceive. Those nobody chases got away, except a foe who ran this round while an
        investigator could still go after it: the next round is theirs to choose.

        There is no split party (the user's call): a chase begins only when nobody outside it is still
        fighting; until then whoever fled stays out of the fight and the pursuit waits for the fight to
        end. When it begins, the party is in it whole (whoever of it neither runs nor chases runs with
        their side), and the keeper's people who take no part stay behind, out of it. Decided before
        any stake is judged."""
        ctx = Ctx.of(self)
        base = f"enc/{enc.id}/r{enc.round}/switch"
        fled = [p.id for p in enc.participants if p.presence == "fled"]
        undecided = plan_check.undecided(ctx, []) if fled else []
        decisions: dict = {}
        if undecided and getattr(self.cast, "plan", None) is not None:
            async def ask() -> dict:
                ask = "要决定追不追的：" + "、".join(undecided) + "；逃跑的：" + "、".join(fled)
                checked = await self._ask_plan("（有人逃跑了）", "pursuit", ask=ask, check=plan_check.pursuit(self, undecided))
                if checked is None or checked.value is None:
                    return {"decisions": [], "problems": checked.problems if checked else []}
                bad = set()
                for p in checked.problems:
                    bad |= {w for w in undecided if w in p}
                return {"decisions": [d.model_dump() for d in checked.value.decisions if d.who not in bad and d.who in undecided],
                        "problems": checked.problems}

            decisions = await self._resolution_step(f"/{base}/ask", turn, ask)

        def switch(w: Work) -> None:
            e, c = w.enc, w.ctx
            for d in decisions.get("decisions", []):
                p = e.get(d["who"])
                if p is not None:
                    a = p.arrangement or NpcArrangement(who=p.id, goal="", actions=[DeclaredAction(do="defend", summary="-")],
                                                        react="fight_back")
                    p.arrangement = a.model_copy(update={"pursue": d["pursue"], "let_go": d["let_go"]})
            if decisions.get("problems"):
                w.unit.lines.append(f"{UNSETTLED}追不追没有定下来：" + "；".join(decisions["problems"]) + "。没定的不追")
            e.cursor = _end_slot(e) + 2
            chasers: dict[str, list[str]] = {}
            for x in fled:
                for p in e.participants:
                    if p.presence != "in" or not w.standing(p.id) or not c.opposed(p.id, x):
                        continue
                    if p.controller == "keeper":
                        wants = p.arrangement is not None and x in p.arrangement.pursue
                    else:
                        wants = any(r.actor == p.id and r.round == e.round and r.declared.do == "pursue" and r.declared.target == x
                                    for r in e.receipts)
                    if not wants:
                        continue
                    if why := c.hidden_from(p.id, x):  # an arrangement made before it lost sight of them
                        w.unit.lines.append(f"{UNSETTLED}{{{p.id}}}追不了{{{x}}}：{why}")
                        continue
                    chasers.setdefault(x, []).append(p.id)
            for x in fled:
                if x in chasers or _undecided_flight(c, x):
                    continue
                e.get(x).presence = "escaped"  # type: ignore[union-attr]
                w.unit.lines.append(f"{{{x}}}脱身了，没有人追上去")
            if not chasers:
                return
            running = set(chasers) | {h for hs in chasers.values() for h in hs}
            if any(p.id not in running and c.acting(p.id) and any(c.acting(o) for o in c.foes(p.id)) for p in e.participants):
                w.unit.lines.append("、".join(f"{{{x}}}" for x in sorted(chasers)) + "逃离了战斗；这里还在打，暂时没人追上去")
                return
            quarry = sorted(chasers)
            hunters = sorted({h for hs in chasers.values() for h in hs})
            party_flees = c.side(quarry[0]) == "party"
            for p in e.participants:
                if p.id in running:
                    continue
                if p.presence == "fled":  # ran too, and nobody went after them in the confusion
                    p.presence = "escaped"
                    w.unit.lines.append(f"{{{p.id}}}脱身了，没有人追上去")
                elif c.acting(p.id) and p.side == "party":  # the party is not split: it runs, or chases, together
                    (quarry if party_flees else hunters).append(p.id)
                elif c.acting(p.id):  # the keeper's people who take no part stay where they are, out of it
                    p.presence = "left"
                    w.unit.lines.append(f"{{{p.id}}}没有跟上去，留在了原地")
            w.unit.lines.append("【战斗转为追逐】" + "、".join(f"{{{x}}}" for x in quarry) + "在逃，"
                                + "、".join(f"{{{h}}}" for h in hunters) + "追了上去")
            e.mode = "chase"
            e.switches.append(f"r{e.round}:chase")
            e.scene.engaged = [g for g in e.scene.engaged if not ({g.a, g.b} & set(quarry))]
            chasing.begin(w, quarry, hunters, site.build(self.m, e.place, kept=self.world.rec.obstacles))

        await self._unit(base, turn, switch)
        enc = self.encounter()
        if enc is not None and enc.mode == "chase":
            await self._chase_settled(turn)

    async def _stakes(self, enc: EncounterState, turn: Turn, key: str) -> list[str]:
        """The stakes that hold now fire, once: their flags set, what hangs on them happens. The flags
        are the unit's (Unit.flags, set once it lands): a turn cut short after it landed and before they
        were set reads them from the journal and sets them on the retry (_replay), as an end's are."""
        due = stakes.due(self, enc)
        if not due:
            return []

        def fire(w: Work) -> None:
            for i in due:
                w.enc.stakes[i].fired = True
                s = w.enc.stakes[i]
                w.unit.lines.append(f"【后果】{s.when} 成立" + (f"：{s.note}" if s.note else ""))
            w.unit.flags = [f for i in due for f in w.enc.stakes[i].flags]

        value = await self._unit(key, turn, fire)
        return list(value.get("flags") or [])

    # -- how it ends ------------------------------------------------------------------------------
    def _outcome(self, ctx: Ctx) -> str:
        """"" while it goes on; else how it ended: won (no foe can fight on, nobody left dying or coming
        back), escaped (none can, and one of them ran off), fled (the investigators got away), lost
        (nobody of theirs is left standing, or all of them down and their foes done with them), or the
        end the keeper's people proposed while someone was still dying (_propose_end). Not while
        whether someone who ran is chased is still open (_switch)."""
        enc = ctx.enc
        if enc.mode == "chase":
            return chasing.verdict(ctx)
        foes = [p.id for p in enc.participants if p.side == "foe" and p.presence == "in"]
        invs = [p for p in enc.participants if p.kind == "investigator"]
        lingering = _lingering(ctx)
        if not any(p.presence == "in" and ctx.alive(p.id) for p in invs):
            return "fled" if any(p.presence in ("fled", "escaped") for p in invs) else "lost"
        if any(p.presence == "fled" for p in enc.participants):
            return ""
        if not any(ctx.acting(x) for x in foes):
            if lingering:
                return ""
            return _proposed(enc) or ("escaped" if any(p.side == "foe" and p.presence == "escaped" for p in enc.participants)
                                      else "won")
        # The investigators down but alive: it is not over. Their foes do with them what their goals say
        # (finish them, take them, leave, stop: the keeper plan, told so in its brief), the dying roll on
        # (p. 101), round by round (the review: downed investigators were frozen). A round in which nobody
        # of theirs went down and no foe laid a hand on them, with nobody left dying, ends it: lost.
        party = [p.id for p in enc.participants if p.side == "party" and p.presence == "in"]
        if not any(ctx.acting(x) for x in party) and not lingering and enc.round and not _pressed(ctx):
            return "lost"
        return ""

    def _summary(self, ctx: Ctx, how: str) -> str:
        rows = []
        for p in ctx.enc.participants:
            v = ctx.view(p.id)
            if v is None:
                continue
            word = views.status(ctx, p.id) or (views.wound_word(v.body()) if p.kind != "investigator"
                                               else ("受伤" if v.body().hp < v.body().hp_max else "无伤"))
            rows.append(f"{ctx.name(p.id, 'players')}{word}")
        return f"{OUTCOME_WORDS.get(how, how)}：" + "，".join(rows)

    async def _encounter_end(self, how: str, turn: Turn, *, eid: str = "", local: bool = True,
                             readers: list[str] | None = None) -> None:
        """Over: who ended how, the stakes that hold, the record, the minutes; its notices; then it goes
        into the log of finished encounters (archived) before its stakes' flags set off anything, so a
        fight a flag begins (an event's) begins cleanly. A turn resumed after any of it goes on from
        there and replays only what it still owed (I7)."""
        st, step = self.state, self._resolution_step
        enc = st.encounter
        eid = eid or (enc.id if enc is not None else "")
        key = f"enc/{eid}/end"
        if enc is not None and enc.id == eid and enc.phase != "ended":
            ctx = Ctx.of(self)
            witnesses = views.observers(self, enc) if readers is None else readers
            audience = scope(st, witnesses)
            due = stakes.due(self, enc, how)
            text = self._summary(ctx, how)
            ran = enc.mode == "chase" and enc.chase is not None
            word = "追逐" if ran else "战斗"
            where = chasing.places(ctx, enc) if ran else self._fight_places(enc)
            party_at, apart = chasing.party_place(self, enc, where) if ran else (st.location, {})

            def close(w: Work) -> None:
                e = w.enc
                for i in due:
                    e.stakes[i].fired = True
                w.unit.fired = [f for s in e.stakes if s.fired for f in s.flags]
                per = {p.id: (views.status(w.ctx, p.id) or p.presence) for p in e.participants}
                minutes = clock.settle_minutes(e.clock)
                e.ended = EndRecord(round=e.round, how=how, per=per, places=where,  # type: ignore[arg-type]
                                    minutes=minutes, text=text)
                e.phase, e.pending = "ended", None
                for p in e.participants:  # the keeper's people end where they are: where they ran to, where it was fought
                    if p.kind != "investigator" and where.get(p.id) and self.world.place(p.id) not in ("", where[p.id]):
                        w.unit.effects.append(effects.Relocate(who=p.id, place=where[p.id]))
                w.unit.extra = {"arrive": party_at if party_at != st.location else "", "apart": apart,
                                "witnesses": witnesses}
                w.unit.lines.append(f"【{word}结束】{text}")
                w.unit.notices.append({"type": "chase_end" if ran else "fight_end", "end": how, "text": text})
                if minutes:
                    e.clock = e.clock.model_copy(update={"applied": e.clock.applied + minutes})

            def land() -> None:
                if enc.ended is None:
                    self.state.minutes += clock.settle_minutes(enc.clock)
                people = [p.id for p in enc.participants if (p.kind != "creature" and self.world.rec.entities.get(p.id) is not None)
                          or p.kind == "investigator"]
                self.world.apply(Occur(id=f"{key}/occur", actor=ENGINE, participants=[x for x in people if x], place=enc.place,
                                       text=text[:300], visibility="public"))
                st.add("event", f"{word}结束：{text}", audience=audience)

            await step(f"/{key}/record", turn, land)
            value = await self._unit(key, turn, close, local=local, readers=witnesses)
        else:  # resumed after the end landed: its notices as the journal has them
            row = ((self._resolution or {}).get("steps") or {}).get(key) or {}
            value = row.get("value") if row.get("done") else None
            if value:
                await self._notify(key, value.get("notices", []), turn)
            elif enc is not None and enc.id == eid:
                value = {"fired": [f for s in enc.stakes if s.fired for f in s.flags]}
        fired = list((value or {}).get("fired") or [])
        extra = (value or {}).get("extra") or {}
        await step(f"/{key}/archive", turn, lambda: self._archive(eid))
        if fired:
            await step(f"/{key}/flags", turn, lambda: self._flags(fired, turn, readers=extra.get("witnesses")))
        if extra.get("arrive"):
            await self._arrive(extra["arrive"], turn)
        if extra.get("apart"):
            await step(f"/{key}/apart", turn, lambda: self._left_apart(extra["apart"], turn))

    def _left_apart(self, apart: dict[str, str], turn: Turn) -> None:
        """Investigators the chase left somewhere else than the party: kept there, with whoever was apart
        before, until the party comes there or someone brings them back (Game._arrive, Game._rejoin)."""
        st = self.state
        st.apart = {**st.apart, **apart}
        for inv, place in apart.items():
            who = next((i.name for i in st.investigators if i.id == inv), inv)
            turn.notes.append(f"{who}落在了{self.world.name(place)}，和大家分开了；要大家去那里、或有人把他带回来才会合。")

    def _fight_places(self, enc: EncounterState) -> dict[str, str]:
        """Where everyone is in the world as a fight ends: the keeper's people still in it where it is
        (a chase that came to blows in another place moved it there, and them with it), the others
        where the world has them; an investigator with the party, or where they were left
        (GameState.apart)."""
        st = self.state
        out = {}
        for p in enc.participants:
            if p.kind == "investigator":
                out[p.id] = st.apart.get(p.id) or st.location
            else:
                out[p.id] = enc.place if p.presence == "in" else (self.world.place(p.id) or enc.place)
        return out

    def _landed(self, key: str) -> dict:
        """What unit `key` came to, as this turn's journal kept it ({} when it has not landed)."""
        row = ((self._resolution or {}).get("steps") or {}).get(key) or {}
        return (row.get("value") or {}) if row.get("done") else {}

    def _ending_owed(self, eid: str) -> bool:
        """This turn's journal began ending encounter `eid`: what is left of that is still owed."""
        steps = (self._resolution or {}).get("steps") or {}
        return f"enc/{eid}/end/record" in steps or f"enc/{eid}/end" in steps

    def _archive(self, eid: str) -> None:
        enc = self.state.encounter
        if enc is not None and enc.id == eid:
            self._archive_now()

    async def archive_encounter(self) -> None:
        """An ended encounter into GameState.encounter_log (normally done as it ends)."""
        self._archive_now()

    def _archive_now(self) -> None:
        enc = self.state.encounter
        if enc is None or enc.phase != "ended":
            return
        summary = EncounterSummary(id=enc.id, mode=enc.mode, place=enc.place, reason=enc.reason, started_turn=enc.started_turn,
                                   rounds=enc.round, participants=[p.id for p in enc.participants], ended=enc.ended,
                                   source_event=enc.source_event, completed_rounds=enc.completed_rounds,
                                   history=[*enc.history, *(f"第 {r.round} 轮：" + "；".join(r.lines) for r in enc.receipts if r.lines)][-40:])
        result = self.world.apply(EncounterStep(id=f"enc/{enc.id}/archive", encounter=enc.id, unit="archive",
                                                changes=[Archive(summary=summary)], actor=ENGINE))
        if not result.ok:
            self._debug("encounter", {"phase": "archive", "problems": result.problems})

    # -- answering a choice -------------------------------------------------------------------------
    def offer_payload(self, choice: PendingChoice) -> dict:
        """The interrupt a choice shows (graph `offer`): the same payload as a roll prompt for a roll."""
        st = self.state
        name = next((i.name for i in st.investigators if i.id == choice.who), choice.who)
        enc = self.encounter()
        if choice.kind == "roll":
            return {"kind": "roll", "id": choice.id, "encounter": enc.id if enc else "", "seat": choice.who, **choice.options[0].value}
        return {"kind": choice.kind, "id": choice.id, "encounter": enc.id if enc else "", "receipt": choice.receipt,
                "who": choice.who, "seat": choice.who, "name": name, "prompt": choice.prompt,
                "options": [{"id": o.id, "label": o.label, "note": o.note} for o in choice.options],
                "default": choice.default, "deadline": choice.deadline, "free_text": choice.free_text,
                "ask": me.ask if (me := enc.get(choice.who) if enc else None) is not None else "major"}

    async def respond(self, cid: str, value: Any, *, who: str = "") -> Turn:
        """The answer to the choice the encounter waits on (a reaction, a roll, a new declaration), then
        the encounter goes on. Its own journal, as push has: resumed, nothing is asked or rolled twice."""
        from ..game import TURN_RECORD, Turn

        async with self._resolution_lock:  # type: ignore[attr-defined]
            journal = self._stage_journal(f"respond:{cid}", {"kind": "respond", "id": cid},  # type: ignore[attr-defined]
                                          "已有未完成的选择，请先继续原选择")
            self._resolution, self._resolution_scope = journal, []  # type: ignore[attr-defined]
            turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn()
            try:
                if not journal.get("complete"):
                    await self._respond_stages(cid, value, who, turn)
                    journal["complete"] = True
                    self._resolution_save(turn)  # type: ignore[attr-defined]
                return turn
            finally:
                self._resolution, self._resolution_scope = None, []  # type: ignore[attr-defined]

    async def _respond_stages(self, cid: str, value: Any, who: str, turn: Turn) -> None:
        enc = self.state.encounter
        assert enc is not None
        key = f"enc/{enc.id}/answer/{cid.replace(':', '-')}"
        units = (self._resolution or {}).get("steps", {})
        if not units.get(key, {}).get("done"):
            pend = enc.pending
            if pend is None or pend.id != cid:
                raise ValueError("这个选择已经过期")
            if who and who != pend.who:
                raise ValueError("这个选择不是你的")
        pend = enc.pending
        answer = await self._resolution_step(f"/{key}/read", turn, lambda: self._read_answer(pend, value))

        def apply(w: Work) -> None:
            p = w.enc.pending
            if p is None or p.id != cid:
                return
            r = next(x for x in w.enc.receipts if x.id == p.receipt)
            me = w.enc.get(p.who)
            if me is not None and answer.get("ask") in ("always", "major"):  # 每次都询问: the player's own setting
                me.ask = answer["ask"]
            if p.kind == "reaction":
                k = len(r.reactions) - 1
                r.reactions[k].chosen, r.reactions[k].by = answer["choice"], "player"
                if me is not None and answer["choice"] in choices.STANCE_WORDS:  # their answer stands for the next blows
                    me.react = answer["choice"]
                r.phase = "declared"
                if answer.get("summary"):
                    r.lines.append(f"{{{p.who}}}的应对：{answer['summary']}")
            elif p.kind == "redeclare":
                option = next((o for o in p.options if o.id == answer["choice"]), None)
                r.phase = "cancelled"
                if option is not None:
                    d = DeclaredAction.model_validate(option.value)
                    n = sum(1 for x in w.enc.receipts if x.actor == r.actor and x.round == r.round)
                    w.enc.receipts.append(ActionReceipt(id=f"{w.enc.id}:r{r.round}:{r.actor}:{n}", round=r.round, actor=r.actor,
                                                        source="button", declared=d, summary=d.summary))
            w.enc.pending = None

        await self._unit(key, turn, apply)
        await self._beat(turn)
        await self._resolution_step("ending", turn, lambda: self._check_ending(turn))
        await self._resolution_step("party-down", turn, lambda: self._party_down(turn))

    async def _read_answer(self, pend: PendingChoice | None, value: Any) -> dict:
        """The answer as a choice id: a button's id, or the player's own words read by the keeper plan
        (mode "react"); a reaction the attack does not allow is the default."""
        if pend is None:
            return {"choice": ""}
        ids = [o.id for o in pend.options]
        text, setting = "", {}
        if isinstance(value, dict):
            chosen, text = str(value.get("choice", "") or ""), str(value.get("text", "") or "")
            if value.get("ask") in ("always", "major"):
                setting = {"ask": value["ask"]}
        else:
            chosen = str(value)
        if chosen in ids:
            return {"choice": chosen, **setting}
        if pend.kind == "roll":
            return {"choice": "roll"}
        if pend.kind == "reaction" and text.strip() and getattr(self.cast, "plan", None) is not None:
            ask = "【可选的应对】" + "、".join(f"{o.id}（{o.label}）" for o in pend.options)
            checked = await self._ask_plan(text, "react", ask=ask, check=plan_check.react(ids))
            if checked is not None and checked.value is not None and not checked.problems:
                return {"choice": checked.value.reaction, "summary": checked.value.summary, **setting}
        return {"choice": pend.default, **setting}



__all__ = ["Encounters"]
