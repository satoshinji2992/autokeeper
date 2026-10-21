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
dying roll CON, healing creatures heal, the clock moves, whoever fled is chased or gets away, and the
stakes that now hold set their flags. Chases still run on the old engine (engine/fighting.py) in
0.0.4's M2, inside the same encounter (its mode "chase"), until WP-D replaces them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...rules import combat as rules
from ...rules.melee import opposed_difficulty
from ...world.ops import Bring, EncounterStep, Occur
from ...world.record import ENGINE
from .. import effects
from . import actions, choices, clock, plan_check, profiles, stakes, telling, views
from .actions import ATTACKING, Ctx
from .model import (
    ActionReceipt,
    Archive,
    Begin,
    ChoiceOption,
    EncounterState,
    EncounterSummary,
    EndRecord,
    Participant,
    PendingChoice,
    ReactionReceipt,
    RoundBudget,
    StakeState,
)
from .plan import DeclaredAction, EncounterStart, EncounterTurnPlan, NpcArrangement
from .resolve import Work, act, round_end, round_order, strike

if TYPE_CHECKING:
    from ...agents.cast import Action, Ruling
    from ...module.schema import Event
    from ..game import Turn

UNSETTLED = "【未落实】"
OUTCOME_WORDS = {"won": "调查员一方占了上风", "fled": "调查员逃离了", "lost": "调查员一方全部倒下", "escaped": "对手跑了",
                 "parley": "双方停手谈和", "surrender": "有一方投降了", "dispersed": "各自散去", "caught": "被抓住了",
                 "aftermath_done": "战斗结束"}


def _end_slot(enc: EncounterState) -> int:
    return len(enc.order)


def _between(enc: EncounterState) -> bool:
    """No round going on: none begun yet, or the last one is over (its end and its switch done)."""
    return enc.round == 0 or enc.cursor >= len(enc.order) + 2


class Encounters:
    """The fight engine, mixed into Game."""

    asking = True  # stop to ask a player (a reaction, a stale declaration); off where nobody can answer (the MCP tools)

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
        _debug: Any
        _gain: Any
        _valid_clues: Any
        _takes: Any
        _equipment: Any

    # -- whether there is one -------------------------------------------------------------------
    def encounter(self) -> EncounterState | None:
        """The encounter going on (not one that has closed and waits to be archived)."""
        enc = self.state.encounter
        return enc if enc is not None and enc.phase != "ended" else None

    def battling(self) -> bool:
        return self.encounter() is not None or self.state.chase is not None

    def fighting(self) -> bool:
        enc = self.encounter()
        return enc is not None and enc.mode == "fight" and self.state.chase is None

    def pending_choice(self) -> PendingChoice | None:
        enc = self.encounter()
        return enc.pending if enc is not None else None

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
        if text.strip() == "/encounter":
            return Action(kind="other", summary="（这一轮接着结算）", encounter=enc.id, declared={"beat": True})
        if st.chase is not None:
            return await self._chase_input(text)
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

        return Action(kind="attack" if d.do in ATTACKING else "other", summary=d.summary, encounter=enc.id, declared=declared)

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
        do = (plan.get("action") or {}).get("do", "")
        if do in ("other", "use") and not declared.get("ruling"):
            return None
        if declared.get("ruling"):
            return Ruling.model_validate(declared["ruling"])
        return Ruling(needs_roll=False, reason="遭遇中")

    # -- the turn ---------------------------------------------------------------------------------
    def _turn_kind(self, action: Action) -> dict:
        """What this turn is, decided once (I7): a round of the encounter going on, the start of one, a
        round of the old chase, or an ordinary action."""
        st = self.state
        declared = action.declared or {}
        if st.chase is not None:
            return {"kind": "chase", "id": st.encounter.id if st.encounter else ""}
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
        if kind.get("start"):
            opened = await self._start_from(action, turn)
            if not opened:
                return
            if st.chase is not None and not declared.get("start"):  # they ran: the chase's first round, running
                await self._chase_turn(action, ruling, turn)  # type: ignore[attr-defined]
                return
        if kind["kind"] == "chase":
            await self._chase_round_turn(action, ruling, turn)
            return
        enc = st.encounter
        eid = kind.get("id") or (enc.id if enc else "")
        if enc is None or enc.id != eid:
            return  # archived: the turn this was is over
        if enc.phase == "ended":
            await self._encounter_end(enc.ended.how if enc.ended else "won", turn)  # its notices, flags once more
            return
        if st.chase is not None:  # the start opened a chase (the old engine runs its rounds)
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

    # -- starting one -----------------------------------------------------------------------------
    async def _start_from(self, action: Action, turn: Turn) -> bool:
        """The encounter this turn begins: one the MCP keeper or an event wrote out, or the one the
        keeper plan announced (Plan.encounter), set up by one more keeper-plan call (plan_encounter)."""
        from ...agents.encounter import EncounterHint

        st = self.state
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
                                   quarry=start.quarry or "party"))],
                "opening": start.opening})
        return start

    async def _encounter_open(self, start: EncounterStart, turn: Turn, *, event: Event | None = None,
                              actor: str = "") -> bool:
        """An encounter begins where the investigators are: who is in it, their bodies kept by the world
        from now on, the creatures first seen (sanity, per investigator), its stakes. A fight's
        `opening` is the investigator's first action: the first round runs at once; otherwise the next
        input is the first round. False when there is nobody there to fight after all."""
        st, step = self.state, self._resolution_step
        eid = await step("enc-id", turn, lambda: f"e{st.turn}n{len(st.encounter_log)}" + (f"x{event.id}" if event else ""))
        foes = self._coming(start.foes, fight_ok=True)
        if not foes:
            if not event:
                turn.notes.append("没有可以动手的对象")
            return False
        base = f"enc/{eid}/begin"
        done = await step("/" + base, turn, lambda: self._begin(eid, start, foes, turn, event))
        await self._notify(base, done["notices"], turn)
        if not done.get("ok"):
            return False
        await self._sights(turn, paid=bool(event and event.sanity), event=event)
        if start.mode == "chase":
            await self._chase_bridge_open(start, turn, event=event)
            return True
        opening = start.opening
        if opening is not None and actor and not event:
            await self._declare(turn, actor, opening, "words", None)
        return True

    def _begin(self, eid: str, start: EncounterStart, foes: list[str], turn: Turn, event: Event | None) -> dict:
        st = self.state
        humans = set(st.human)
        allies = [profiles.combatant(self.world, x) for x in start.allies if x not in foes]
        parts: list[Participant] = []
        for inv in st.investigators:
            parts.append(Participant(id=inv.id, kind="investigator", side="party", controller="human" if inv.id in humans else "ai",
                                     surprised=start.surprise == "party", node=st.location))
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
                             started_turn=st.turn, participants=parts,
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
        st.add("event", telling.render(line, pn))
        return {"ok": True, "notices": [{"type": "fight_start", "fight": None, "line": telling.render(line, pn), "surprise": surprise}]}

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
        covered = {x for ev in self.m.events for x in ev.sights}
        for p in enc.participants:
            v = ctx.view(p.id)
            prof = getattr(v, "profile", None)
            if prof is None or p.side == "party" or "/" not in prof.sanity:
                continue
            sheet = prof.sheet.partition(":")[2]
            horror = p.id
            free = paid or horror in covered or sheet in covered or (event is not None and (horror in event.sights or sheet in event.sights))
            first = not any(horror in seen for seen in st.sightings.values())
            if first:
                face = f"creature-{sheet}" if f"creature-{sheet}" in self.m.pictures else views.portrait(self, horror)
                await step(f"/enc/{enc.id}/reveal/{horror}", turn, lambda prof=prof, horror=horror, face=face: self.emit({
                    "type": "reveal", "id": horror, "name": ctx.name(horror, "players"), "portrait": face,
                    "look": prof.look[:120]}))
            for inv in st.investigators:
                if horror in st.sightings.get(inv.id, []) or not inv.can_act:
                    continue

                def pay(inv=inv, horror=horror, prof=prof, free=free) -> dict | None:
                    st.sightings.setdefault(inv.id, []).append(horror)
                    if free:
                        return None
                    ok, bad = prof.sanity.split("/", 1)
                    trial = inv.model_copy(deep=True)
                    s = self.rules.sanity(trial, ok.strip(), bad.strip())
                    inv.__dict__.update(trial.__dict__)
                    turn.sanity.append((inv.name, s))
                    st.add("sanity", s.describe(), who=inv.name)
                    return {"type": "sanity", "who": inv.name, **_sanity_view(s), **self._madness(inv, s, turn)}

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
            "none" if st.chase is not None or not any(i.can_act for i in st.investigators)
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
                                                      controller="keeper", joined_round=w.enc.round,
                                                      surprised=ev.surprise == "foe", node=w.enc.place))
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
            ruled = ruling.model_dump(mode="json") if ruling is not None and d.do in ("other", "use") else None
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
                                                  side="party", controller="keeper", joined_round=upcoming, node=e.place))
                w.change("joined", x)
            for a in landed.npcs:
                p = e.get(a.who)
                if p is None:
                    continue
                if a.side:
                    p.side = a.side  # type: ignore[assignment]
                p.arrangement, p.arranged_round, p.react = a, upcoming, a.react
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
            e.fills += list(landed.fills)
            for s in landed.stakes:
                if not any(x.when == s.when and x.flags == s.flags for x in e.stakes):
                    e.stakes.append(StakeState(**s.model_dump(), source="keeper"))
            if landed.pace:
                e.clock.pace = landed.pace
            if landed.end is not None:
                e.history.append(f"end:{landed.end.how}:{','.join(landed.end.who)}")
            for x in unsettled:
                w.unit.lines.append(f"{UNSETTLED}{x}")

        await self._unit(key, turn, apply)
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
        how = {"gave_up": "escaped", "surrender": "surrender", "parley": "parley", "dispersed": "dispersed"}[end.how]
        await self._encounter_end(how, turn)

    # -- the beat ---------------------------------------------------------------------------------
    async def _beat(self, turn: Turn, *, settle: bool = False) -> None:
        """The encounter goes on, unit after unit, until it waits for someone (a player's answer, the
        next declaration), changes into a chase, or ends."""
        await self._replay(turn)
        started = 0
        for _ in range(400):
            enc = self.encounter()
            if enc is None or enc.pending is not None or enc.mode != "fight" or self.state.chase is not None:
                return
            if _between(enc):
                how = self._outcome(Ctx.of(self))
                if how:
                    await self._encounter_end(how, turn)
                    return
                ctx = Ctx.of(self)
                nxt = enc.round + 1
                waiting = [p.id for p in enc.participants if p.controller == "human" and ctx.acting(p.id)
                           and self._declared(enc, p.id, nxt) is None]
                declared = any(self._declared(enc, p.id, nxt) is not None for p in enc.participants if p.controller == "human")
                if (waiting and not settle) or (not declared and not settle) or started:
                    return
                started += 1
                await self._round_start(enc, nxt, turn, settle)
                continue
            if enc.cursor < len(enc.order):
                if not await self._slot(enc, turn):
                    return
                continue
            await self._round_end(enc, turn)

    async def _replay(self, turn: Turn) -> None:
        """Notices of units this journal already landed that did not go out (a broken connection)."""
        journal = self._resolution
        if journal is None:
            return
        steps = journal.get("steps", {})
        for key in list(journal.get("enc-units", [])):
            row = steps.get(key)
            if row and row.get("done") and isinstance(row.get("value"), dict):
                await self._notify(key, row["value"].get("notices", []), turn)

    async def _unit(self, key: str, turn: Turn, fn) -> dict:
        """One unit: `fn` works on a copy (resolve.Work) with the journal's dice, then one op lands it
        all; its lines join the keeper's account, its notices go out after."""
        if self._resolution is not None:
            units = self._resolution.setdefault("enc-units", [])
            if key not in units:
                units.append(key)

        def run() -> dict:
            ctx = Ctx.of(self)
            w = Work(self, ctx, key)
            fn(w)
            changes = w.changes()
            if changes or w.unit.effects:
                result = self.world.apply(EncounterStep(id=key, encounter=ctx.enc.id, unit=key.rsplit("/", 1)[-1],
                                                        effects=w.unit.effects, changes=changes, actor=ENGINE))
                if not result.ok:
                    raise RuntimeError("遭遇结算没有落地：" + "；".join(result.problems))
            for fn_after in w.after:
                fn_after()
            after = Ctx.of(self, self.state.encounter or w.enc)
            kn, pn = views.names(after, "keeper"), views.names(after, "players")
            turn.fight += [telling.render(x, kn) for x in w.unit.lines]
            shown = [telling.render(x, pn) for x in w.unit.lines if not x.startswith(("【可观察】", UNSETTLED, "【战斗 · 第", "【后果】"))]
            notices = []
            for ev in w.unit.notices:
                ev = dict(ev)
                if ev.get("line"):
                    ev["line"] = telling.render(ev["line"], pn)
                notices.append(ev)
            if shown or any(ev["type"] == "fight_act" for ev in notices):
                notices.append({"type": "fight_state", "fight": None, "lines": shown})
            if w.unit.lines and (st := self.state):
                for x in shown:
                    if not x.startswith("【"):
                        st.add("dice", x)
            return {"notices": notices, "rest": w.unit.rest, "flags": w.unit.flags}

        value = await self._resolution_step("/" + key, turn, run)
        await self._notify(key, value.get("notices", []), turn)
        if value.get("rest"):
            await self._settle_rest(key, value["rest"], turn)
        if value.get("flags"):
            await self._resolution_step(f"/{key}/flags", turn, lambda: self._flags(value["flags"], turn))
        return value

    async def _notify(self, key: str, notices: list[dict], turn: Turn) -> None:
        for k, ev in enumerate(notices):
            await self._resolution_step(f"/{key}/n{k}", turn, lambda ev=ev: self.emit(self._filled(ev)))

    def _filled(self, ev: dict) -> dict:
        """A notice with the fight as it now stands, for those that carry it."""
        if "fight" in ev and ev["fight"] is None:
            enc = self.state.encounter
            ev = {**ev, "fight": views.fight_view(self, enc) if enc is not None else {"round": 0, "place": "", "fighters": []}}
        return ev

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
            if settle:  # the window closed: a player who put nothing in only defends this round (net/hub.py)
                for p in e.participants:
                    if p.kind == "investigator" and w.ctx.acting(p.id) and self._declared(e, p.id, nxt) is None:
                        e.receipts.append(ActionReceipt(id=f"{e.id}:r{nxt}:{p.id}:0", round=nxt, actor=p.id, source="table",
                                                        declared=DeclaredAction(do="defend", summary="只防守"), summary="只防守"))
            declared = {r.actor: r.declared for r in e.receipts if r.round == nxt and r.phase == "declared"}
            for p in e.participants:
                if p.controller == "keeper" and p.arrangement is not None and p.arrangement.actions:
                    declared.setdefault(p.id, p.arrangement.actions[0])
            order = round_order(w.ctx, declared)
            lost = dict(e.budget.lost_next)
            # the round before last folds into the history: its receipts' lines, compact
            old = [r for r in e.receipts if r.round < nxt - 1]
            if old:
                e.history += [f"第 {r.round} 轮：" + "；".join(r.lines) for r in old if r.lines]
                e.receipts = [r for r in e.receipts if r.round >= nxt - 1]
            e.round, e.order, e.cursor, e.budget = nxt, order, 0, RoundBudget(lost_next=lost)
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
        players' screens show; without a valid pick, it only defends."""
        enc = self.encounter()
        assert enc is not None
        offered = choices.options(self, pid)

        async def pick() -> dict | None:
            chooser = getattr(self.cast, "encounter_choice", None)
            if chooser is None or not offered:
                return None
            situation = self._public_situation(pid)
            checked = await chooser(self.state, pid, situation, offered)
            chosen = next((o for o in offered if o.id == checked.value), None)
            return chosen.value if chosen is not None else None

        value = await self._resolution_step(f"/enc/{enc.id}/r{rnd}/ai/{pid}", turn, pick)
        d = DeclaredAction.model_validate(value) if value else DeclaredAction(do="defend", summary="只防守")

        def declare(w: Work) -> None:
            w.enc.receipts.append(ActionReceipt(id=f"{w.enc.id}:r{rnd}:{pid}:0", round=rnd, actor=pid, source="ai", declared=d,
                                                summary=d.summary))
            me = w.enc.get(pid)
            if me is not None and d.react:
                me.react = d.react

        await self._unit(f"enc/{enc.id}/r{rnd}/ai/{pid}/decl", turn, declare)

    def _public_situation(self, pid: str) -> str:
        view = views.fight_view(self)
        rows = [f"第 {view['round']} 轮战斗。"]
        for f in view["fighters"]:
            hp = f"生命 {f['hp']}/{f['hp_max']}" if f["hp"] is not None else f["wound"]
            rows.append(f"- {f['name']}（{'我方' if f['side'] == 'party' else '对手'}）：{hp}" + (f"，{f['status']}" if f["status"] else ""))
        return "\n".join(rows)

    def _by_hand(self, actor: str) -> bool:
        """The player at the screen throws this investigator's dice themselves (graph: by_hand)."""
        st = self.state
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
        if (holder := ctx.held_by(actor)) and (r is None or not r.settled):
            await self._unit(f"{base}/break", turn, lambda w: self._break_free(w, actor, holder, r, advance))
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
        problems = actions.preconditions(ctx, actor, d) if not r.settled else []
        if problems:
            if (p.controller == "human" and r.source not in ("table",) and self.asking and not self.holding
                    and self.state.round_spent is None):
                await self._unit(f"{base}/redeclare/{r.id}", turn, lambda w: self._ask_again(w, r, problems))
                return False
            await self._unit(f"{base}/lapse", turn, lambda w: (
                w.unit.lines.append(f"{{{actor}}}没能{actions.VERBS.get(d.do, d.do)}：{problems[0]}"), advance(w, r, "lapsed", problems[0])))
            return True
        rolls = d.do in ATTACKING or d.do == "aid" or (d.do in ("other", "use") and (r.ruling or {}).get("needs_roll"))
        if self.asking and self._by_hand(actor) and rolls and not r.prompted:
            await self._unit(f"{base}/prompt", turn, lambda w: self._prompt(w, r))
            return False
        if d.do not in ATTACKING:
            def settle(w: Work) -> None:
                act(w, r, ruling=r.ruling)
                advance(w, r)

            await self._unit(f"{base}/act", turn, settle)
            return True
        av = ctx.view(actor)
        picks = actions.picks(d, av.attacks_per_round())
        k = r.settled
        if k >= len(picks):
            await self._unit(f"{base}/done", turn, lambda w: advance(w, r))
            return True
        pick = picks[k]
        last = k + 1 >= len(picks)
        if not ctx.present(pick.target) or not ctx.alive(pick.target) or ctx.hidden_from(actor, pick.target):
            why = ctx.hidden_from(actor, pick.target) or ("已经死了" if not ctx.alive(pick.target) else "已经不在现场")

            def miss(w: Work) -> None:
                rr = next(x for x in w.enc.receipts if x.id == r.id)
                rr.settled = k + 1
                line = f"{{{actor}}}的第 {k + 1} 下没有出手：{{{pick.target}}}{why}"
                rr.lines.append(line)
                w.unit.lines.append(line)
                if last:
                    advance(w, r)

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
                advance(w, r)

        await self._unit(f"{base}/a{k}/roll", turn, roll)
        return True

    async def _keeper_slot(self, enc: EncounterState, actor: str, base: str, turn: Turn, advance) -> bool:
        """A keeper's person on its slot: the first of its arranged actions it can do now; none, one
        scoped keeper-plan call for it; still none, it does nothing this round (noted 【未落实】)."""
        ctx = Ctx.of(self)
        p = ctx.p(actor)
        assert p is not None
        able = [a for a in (p.arrangement.actions if p.arrangement else []) if not actions.preconditions(ctx, actor, a)]
        units = (self._resolution or {}).get("enc-units", [])
        if not able and getattr(self.cast, "plan", None) is not None and f"{base}/replan" not in units:
            if self._resolution is not None:
                units.append(f"{base}/replan")
            checked = await self._resolution_step(f"/{base}/replan", turn, lambda: self._arrange_call())
            if checked:
                await self._plan_unit(f"{base}/replanned", checked["plan"], checked["unsettled"], turn)
            return True
        chosen = able[0] if able else None

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

    def _break_free(self, w: Work, actor: str, holder: str, r: ActionReceipt | None, advance) -> None:
        """Held fast: the slot goes on trying to break free, STR against STR (p. 89)."""
        mine, theirs = w.check(actor, "力量", w.ctx.view(actor).char("力量")), w.check(holder, "力量", w.ctx.view(holder).char("力量"))
        if mine.level > theirs.level or (mine.level == theirs.level and mine.level.ok and mine.target > theirs.target):
            w.enc.scene.holds = [h for h in w.enc.scene.holds if h.held != actor]
            w.change("freed", actor)
            w.unit.lines.append(f"{{{actor}}}拼命挣脱了{{{holder}}}（力量 {mine.value} 对 {theirs.value}）")
        else:
            w.unit.lines.append(f"{{{actor}}}被{{{holder}}}死死制住，没能挣脱（力量 {mine.value} 对 {theirs.value}）")
        advance(w, r, "lapsed" if r is not None else "committed", "被制住")

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
        """A player's declaration went stale before their slot (the target fell): they choose again."""
        offered = choices.options(self, r.actor, None, w.ctx)
        rr = next(x for x in w.enc.receipts if x.id == r.id)
        rr.phase = "awaiting_reaction"
        w.enc.pending = PendingChoice(id=f"{r.id}:redeclare", kind="redeclare", who=r.actor, receipt=r.id,
                                      prompt=f"原来的行动做不成了：{problems[0]}。这一下改做什么？",
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
        payload = {"who": st.find(r.actor).name, "skill": skill, "difficulty": difficulty, "bonus": 0, "target": target,
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
                rr.phase = "awaiting_reaction"
                rr.reactions.append(ReactionReceipt(attack=k, attacker=r.actor, defender=pick.target, options=options,  # type: ignore[arg-type]
                                                    asked=True))
                default = dp.react if dp.react in options else options[0]
                weapon = f"抄起{opt.name}" if opt is not None and not opt.natural else ""
                w.enc.pending = PendingChoice(
                    id=f"{r.id}:react:{k}", kind="reaction", who=pick.target, receipt=r.id,
                    prompt=f"{ctx.name(r.actor, 'players')}{weapon}{'朝你开枪' if kind == 'firearm' else '向你扑来'}，你怎么应对？",
                    options=[ChoiceOption(id=x, label=choices.REACTION_LABELS[x]) for x in options], default=default)
                return
            chosen, by = react_choice(w.ctx, pick.target, kind)
            rr.reactions.append(ReactionReceipt(attack=k, attacker=r.actor, defender=pick.target, options=options,  # type: ignore[arg-type]
                                                chosen=chosen, by=by))  # type: ignore[arg-type]

        await self._unit(f"{base}/a{k}/react", turn, decide)
        return ask

    async def _round_end(self, enc: EncounterState, turn: Turn) -> None:
        """The end of a round (p. 101; design §3.4): the dying, healing, surprise over; the clock; who
        fled is chased or gets away; the stakes that now hold."""
        step = self._resolution_step
        n = enc.round
        base = f"enc/{enc.id}/r{n}/end"
        if enc.cursor == _end_slot(enc):
            def end(w: Work) -> None:
                round_end(w)
                w.enc.clock = clock.tick(w.enc.clock)
                ctx = w.ctx
                foes_up = [p.id for p in w.enc.participants if p.side == "foe" and p.presence == "in" and w.standing(p.id)]
                lingering = [p.id for p in w.enc.participants if p.presence == "in" and
                             ((w.body(p.id).dying and not w.body(p.id).stabilized) or w.body(p.id).regenerating)]
                if not foes_up and lingering and w.enc.phase == "open":
                    w.enc.phase = "aftermath"
                    w.unit.lines.append("对手已经倒下，还有人要救、要收拾：接下来一轮一轮善后")
                w.enc.cursor = _end_slot(w.enc) + 1
                del ctx

            await self._unit(base, turn, end)
        await step(f"/{base}/time", turn, lambda: self._clock_minutes())
        await step(f"/{base}/clock", turn, lambda: self._time(turn))
        enc = self.encounter()
        if enc is None:
            return
        if enc.cursor == _end_slot(enc) + 1:
            await self._switch(enc, turn)
        enc = self.encounter()
        if enc is not None:
            await self._stakes(enc, turn, f"enc/{enc.id}/r{n}/stakes")

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
        """Whoever fled this round: the keeper's people say whether they give chase (asked once if
        their arrangement did not say; an explicit "no" is obeyed; no answer, no chase, noted); a
        chase begins, or those who fled got away. Decided before any stake is judged."""
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
            e = w.enc
            for d in decisions.get("decisions", []):
                p = e.get(d["who"])
                if p is not None:
                    a = p.arrangement or NpcArrangement(who=p.id, goal="", actions=[DeclaredAction(do="defend", summary="-")],
                                                        react="fight_back")
                    p.arrangement = a.model_copy(update={"pursue": d["pursue"], "let_go": d["let_go"]})
            if decisions.get("problems"):
                w.unit.lines.append(f"{UNSETTLED}追不追没有定下来：" + "；".join(decisions["problems"]) + "。没定的不追")
            chasers: dict[str, list[str]] = {}
            for x in fled:
                for p in e.participants:
                    if p.presence != "in" or not w.standing(p.id) or not w.ctx.opposed(p.id, x):
                        continue
                    if p.controller == "keeper" and p.arrangement is not None and x in p.arrangement.pursue:
                        chasers.setdefault(x, []).append(p.id)
                    if p.controller != "keeper" and any(r.actor == p.id and r.round == e.round and r.declared.do == "pursue"
                                                        and r.declared.target == x for r in e.receipts):
                        chasers.setdefault(x, []).append(p.id)
            for x in fled:
                if x not in chasers:
                    e.get(x).presence = "escaped"  # type: ignore[union-attr]
                    w.unit.lines.append(f"{{{x}}}脱身了，没有人追上去")
            e.cursor = _end_slot(e) + 2
            if chasers:
                e.mode = "chase"
                e.switches.append(f"r{e.round}:chase")
                w.change("switched", next(iter(chasers)))
                quarry = sorted(chasers)
                hunters = sorted({h for hs in chasers.values() for h in hs})
                e.history.append(f"chase:{','.join(quarry)}:{','.join(hunters)}")
                w.unit.lines.append("【战斗转为追逐】" + "、".join(f"{{{x}}}" for x in quarry) + "在逃，"
                                    + "、".join(f"{{{h}}}" for h in hunters) + "追了上去")

        await self._unit(base, turn, switch)
        enc = self.encounter()
        if enc is not None and enc.mode == "chase":
            await self._chase_bridge_from_fight(enc, turn)

    async def _stakes(self, enc: EncounterState, turn: Turn, key: str, ended: str = "") -> list[str]:
        """The stakes that hold now fire, once: their flags set, what hangs on them happens."""
        due = stakes.due(self, enc, ended)
        if not due:
            return []
        flags = [f for i in due for f in enc.stakes[i].flags]

        def fire(w: Work) -> None:
            for i in due:
                w.enc.stakes[i].fired = True
                s = w.enc.stakes[i]
                w.unit.lines.append(f"【后果】{s.when} 成立" + (f"：{s.note}" if s.note else ""))

        await self._unit(key, turn, fire)
        if flags:
            await self._resolution_step(f"/{key}/flags", turn, lambda: self._flags(flags, turn))
        return flags

    # -- how it ends ------------------------------------------------------------------------------
    def _outcome(self, ctx: Ctx) -> str:
        """"" while it goes on; else how it ended: won (no foe can fight on, nobody left dying or coming
        back), fled (the investigators got away), lost (nobody of theirs is left standing)."""
        enc = ctx.enc
        foes = [p.id for p in enc.participants if p.side == "foe" and p.presence == "in"]
        [p.id for p in enc.participants if p.side == "party" and p.presence == "in"]
        invs = [p for p in enc.participants if p.kind == "investigator"]
        lingering = [p.id for p in enc.participants if p.presence == "in" and (v := ctx.view(p.id)) is not None
                     and ((v.body().dying and not v.body().stabilized) or v.body().regenerating)]
        if not any(p.presence == "in" and ctx.alive(p.id) for p in invs):
            return "fled" if any(p.presence in ("fled", "escaped") for p in invs) else "lost"
        if not any(ctx.acting(x) for x in foes):
            return "" if lingering else "won"
        # The investigators down but alive: it is not over, the foes and anyone still standing go on (继续,
        # /encounter), and the dying roll on (the review: downed investigators were frozen).
        return ""

    def _summary(self, ctx: Ctx, how: str) -> str:
        rows = []
        for p in ctx.enc.participants:
            v = ctx.view(p.id)
            if v is None:
                continue
            word = views.status(ctx, p.id) or (views.wound_word(v.body()) if p.kind != "investigator"
                                               else ("受伤" if v.body().hp < v.body().hp_max else "无伤"))
            rows.append(f"{v.public_name('players')}{word}")
        return f"{OUTCOME_WORDS.get(how, how)}：" + "，".join(rows)

    async def _encounter_end(self, how: str, turn: Turn) -> None:
        """Over: who ended how, the stakes that hold, the record, the minutes; notices, flags. A turn
        resumed after the end op replays only what it still owed (I7)."""
        st, step = self.state, self._resolution_step
        enc = st.encounter
        assert enc is not None
        key = f"enc/{enc.id}/end"
        if enc.phase != "ended":
            ctx = Ctx.of(self)
            due = stakes.due(self, enc, how)
            text = self._summary(ctx, how)

            def close(w: Work) -> None:
                e = w.enc
                for i in due:
                    e.stakes[i].fired = True
                per = {p.id: (views.status(w.ctx, p.id) or p.presence) for p in e.participants}
                minutes = clock.settle_minutes(e.clock)
                e.ended = EndRecord(round=e.round, how=how, per=per, places={p.id: e.place for p in e.participants},  # type: ignore[arg-type]
                                    minutes=minutes, text=text)
                e.phase, e.pending = "ended", None
                w.unit.lines.append(f"【战斗结束】{text}")
                w.unit.notices.append({"type": "fight_end", "end": how, "text": text})
                if minutes:
                    e.clock = e.clock.model_copy(update={"applied": e.clock.applied + minutes})

            def land() -> None:
                if enc.ended is None:
                    self.state.minutes += clock.settle_minutes(enc.clock)
                people = [p.id for p in enc.participants if (p.kind != "creature" and self.world.rec.entities.get(p.id) is not None)
                          or p.kind == "investigator"]
                self.world.apply(Occur(id=f"{key}/occur", actor=ENGINE, participants=[x for x in people if x], place=enc.place,
                                       text=text[:300], visibility="public"))
                st.add("event", f"战斗结束：{text}")

            await step(f"/{key}/record", turn, land)
            await self._unit(key, turn, close)
        else:  # resumed after the end landed: its notices as the journal has them
            await self._unit(key, turn, lambda w: None)
        enc = st.encounter
        fired = [f for s in (enc.stakes if enc is not None else []) if s.fired for f in s.flags]
        if fired:
            await step(f"/{key}/flags", turn, lambda: self._flags(fired, turn))

    async def archive_encounter(self) -> None:
        """An ended encounter into GameState.encounter_log (the turn that ended it is complete)."""
        enc = self.state.encounter
        if enc is None or enc.phase != "ended" or self.state.chase is not None:
            return
        summary = EncounterSummary(id=enc.id, mode=enc.mode, place=enc.place, reason=enc.reason, started_turn=enc.started_turn,
                                   rounds=enc.round, participants=[p.id for p in enc.participants], ended=enc.ended,
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
            return {"kind": "roll", "id": choice.id, "encounter": enc.id if enc else "", **choice.options[0].value}
        return {"kind": choice.kind, "id": choice.id, "encounter": enc.id if enc else "", "receipt": choice.receipt,
                "who": choice.who, "name": name, "prompt": choice.prompt,
                "options": [{"id": o.id, "label": o.label, "note": o.note} for o in choice.options],
                "default": choice.default, "deadline": choice.deadline, "free_text": choice.free_text}

    async def respond(self, cid: str, value: Any, *, who: str = "") -> Turn:
        """The answer to the choice the encounter waits on (a reaction, a roll, a new declaration), then
        the encounter goes on. Its own journal, as push has: resumed, nothing is asked or rolled twice."""
        from ..game import TURN_RECORD, Turn

        async with self._resolution_lock:  # type: ignore[attr-defined]
            st = self.state
            direct = not st.pending_turn
            key = f"respond:{cid}"
            saved = (st.pending_resolution or None) if direct else st.pending_turn.get(key)
            identity = {"kind": "respond", "id": cid}
            if saved is not None and saved.get("identity") != identity:
                raise ValueError("已有未完成的选择，请先继续原选择")
            journal = saved or {"identity": identity, "steps": {}}
            if direct:
                st.pending_resolution = journal
            else:
                st.pending_turn[key] = journal
            self._resolution, self._resolution_scope = journal, []  # type: ignore[attr-defined]
            turn = TURN_RECORD.validate_python(journal["turn"]) if "turn" in journal else Turn()
            try:
                if not journal.get("complete"):
                    await self._respond_stages(cid, value, who, turn)
                    journal["complete"] = True
                    self._resolution_save(turn)  # type: ignore[attr-defined]
                if direct:
                    st.pending_resolution = {}
                    if self.checkpoint:  # type: ignore[attr-defined]
                        self.checkpoint()  # type: ignore[attr-defined]
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
            if p.kind == "reaction":
                k = len(r.reactions) - 1
                r.reactions[k].chosen, r.reactions[k].by = answer["choice"], "player"
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

    async def _read_answer(self, pend: PendingChoice | None, value: Any) -> dict:
        """The answer as a choice id: a button's id, or the player's own words read by the keeper plan
        (mode "react"); a reaction the attack does not allow is the default."""
        if pend is None:
            return {"choice": ""}
        ids = [o.id for o in pend.options]
        text = ""
        if isinstance(value, dict):
            chosen, text = str(value.get("choice", "") or ""), str(value.get("text", "") or "")
        else:
            chosen = str(value)
        if chosen in ids:
            return {"choice": chosen}
        if pend.kind == "roll":
            return {"choice": "roll"}
        if pend.kind == "reaction" and text.strip() and getattr(self.cast, "plan", None) is not None:
            ask = "【可选的应对】" + "、".join(f"{o.id}（{o.label}）" for o in pend.options)
            checked = await self._ask_plan(text, "react", ask=ask, check=plan_check.react(ids))
            if checked is not None and checked.value is not None and not checked.problems:
                return {"choice": checked.value.reaction, "summary": checked.value.summary}
        return {"choice": pend.default}

    # -- the chase (0.0.4 M2: the old engine runs it, inside the encounter) ---------------------------------
    async def _chase_input(self, text: str) -> Action:
        """A player's input in the old chase: one of its choices, exactly; anything else the keeper plan
        reads (as for a fight) and is mapped onto what the old chase can do."""
        from ...agents.cast import Action

        st = self.state
        ch = st.chase
        assert ch is not None
        enc = self.encounter()
        me = ch.get(st.investigator.id)
        order = None
        if me is not None:
            for label, _note, o in chase_options(me, ch):
                if label == text.strip():
                    order = o
        summary = f"{st.investigator.name}：{text.strip()}" if order is not None else ""
        if order is None:
            d: DeclaredAction | None = None
            if getattr(self.cast, "plan", None) is not None and enc is not None:
                checked = await self._ask_plan(text, "act", check=lambda plan: [])
                d = checked.value.action if checked is not None and checked.value is not None else None
            if d is None:
                parsed = await self.cast.intent(st, text)
                d = DeclaredAction(do="other", summary=parsed.summary)
            order = _order_of(d)
            summary = d.summary
        return Action(kind="move", summary=summary, encounter=enc.id if enc else "", declared={"chase": order.model_dump()})

    async def _chase_round_turn(self, action: Action, ruling: Ruling, turn: Turn) -> None:
        await self._chase_turn(action, ruling, turn)  # type: ignore[attr-defined]

    async def _chase_bridge_open(self, start: EncounterStart, turn: Turn, *, event: Event | None = None) -> None:
        """A chase begins (from the plan or an event): the old chase engine runs it, the encounter in
        chase mode keeps its stakes and its people."""
        foes = [p.id for p in self.state.encounter.participants if p.side == "foe"]  # type: ignore[union-attr]
        opened = await self._chase_open(foes, "foe" if start.quarry == "foe" else "party", start.reason, turn,  # type: ignore[attr-defined]
                                        paid=True)
        if opened == "none":
            await self._encounter_end("dispersed", turn)

    async def _chase_bridge_from_fight(self, enc: EncounterState, turn: Turn) -> None:
        """Fight to chase: the old chase's runners made from the encounter's people as the world keeps
        them (hit points, guns, wounds carry over), the fled ahead."""
        from ... import scenery
        from .. import combat as battle

        st = self.state
        record = next((h for h in reversed(enc.history) if h.startswith("chase:")), "chase::")
        _, quarry, hunters = record.split(":")
        quarry_ids, hunter_ids = [x for x in quarry.split(",") if x], [x for x in hunters.split(",") if x]

        def setup() -> dict:
            runners = []
            ctx = Ctx.of(self)
            for x in [*quarry_ids, *hunter_ids]:
                f = self._fighter(x)
                if f is None:
                    continue
                f.side = "party" if ctx.side(x) == "party" else "foe"  # type: ignore[assignment]
                runners.append(f)
            loc = self.m.location(st.location)
            track = self._chase_way() or battle.default_track(scenery.kind_of(loc.name, loc.read_aloud))  # type: ignore[attr-defined]
            quarry_side = "party" if any(Ctx.of(self).side(x) == "party" for x in quarry_ids) else "foe"
            speeds = self._chase_setup(runners, track, quarry_side, f"逃离{loc.name}的战斗", rolled=False)  # type: ignore[attr-defined]
            assert st.chase is not None
            return {"type": "chase_start", "chase": battle.chase_view(st.chase), "line": "【战斗转为追逐】", "speeds": speeds}

        ev = await self._resolution_step(f"/enc/{enc.id}/r{enc.round}/chase", turn, setup)
        await self._resolution_step(f"/enc/{enc.id}/r{enc.round}/chase/n", turn, lambda: self.emit(ev))
        turn.fight += ev.get("speeds", [])

    def _fighter(self, pid: str):
        """An old-engine runner for someone in the encounter, from the world (engine/combat)."""
        from .. import combat as battle

        st = self.state
        inv = next((i for i in st.investigators if i.id == pid), None)
        if inv is not None:
            return battle.from_investigator(inv, self._carried_by(inv.id), world=self.world)  # type: ignore[attr-defined]
        npc = next((n for n in self.m.npcs if n.id == pid), None)
        creature = profiles.form_of(self.world, pid) or next((c for c in self.m.creatures if c.id == pid), None)
        if creature is not None:
            f = battle.from_creature(creature, portrait=views.portrait(self, pid), world=self.world)
            f.id = pid
        elif npc is not None:
            f = battle.from_npc(npc, self._carried_by(pid), world=self.world)  # type: ignore[attr-defined]
        else:
            return None
        from .combatant import view

        v = view(self.world, pid)
        if v is not None:
            f.name = v.public_name("players")
        return f

    async def _chase_closed(self, end: str, text: str, fight_ids: list[str] | None, turn: Turn) -> list[dict]:
        """The old chase is over: caught, it comes to blows (the encounter is a fight again, the two at
        it engaged); escaped, those who ran got away and the encounter ends with its stakes judged."""
        st = self.state
        enc = self.encounter()
        if enc is None:
            return []
        ch = st.chase
        assert ch is not None

        def close(w: Work) -> None:
            e = w.enc
            hidden = set(ch.hidden)
            if end == "caught" and fight_ids:
                hunter, prey = fight_ids
                for p in e.participants:
                    runner = ch.get(p.id)
                    if runner is not None and p.presence in ("fled", "in"):
                        p.presence = "in"
                w.engage(hunter, prey)
                e.mode = "fight"
                e.switches.append(f"r{e.round}:fight")
                w.change("switched", prey)
                w.unit.lines.append("【追逐转为战斗】{" + hunter + "}追上了{" + prey + "}")
            else:
                quarry = [r.id for r in ch.runners if r.side == ch.quarry]
                for x in quarry:
                    p = e.get(x)
                    if p is not None and (x in hidden or p.presence in ("fled", "in")):
                        p.presence = "escaped"
                e.mode = "fight"
            e.round = e.round or 0
            e.cursor = max(e.cursor, len(e.order) + 2)
            w.after.append(lambda: setattr(st, "chase", None))

        await self._unit(f"enc/{enc.id}/chase-end/t{st.turn}", turn, close)
        return []


# -- the old chase's choices, matched exactly ---------------------------------------------------------
def chase_options(me, ch) -> list:
    """The old chase's choices for `me`: (words, note, order)."""
    from ...rules import chase as chase_rules
    from .. import combat as battle

    names = {f.id: f.name for f in ch.runners}
    moving = [f for f in ch.runners if f.standing and f.id not in ch.hidden]
    steps = chase_rules.actions_for(me, min((f.speed for f in moving), default=me.speed))
    out = [(battle.order_text(battle.Order(do="run"), names), f"这一轮能跑 {steps} 段", battle.Order(do="run"))]
    if me.side == ch.quarry:
        lead = chase_rules.gap(ch.running(ch.quarry), ch.running("foe" if ch.quarry == "party" else "party"))
        if lead >= chase_rules.SIGHT:
            out.append((battle.order_text(battle.Order(do="hide"), names), f"潜行 {me.value('潜行')} 对追兵的侦查", battle.Order(do="hide")))
        out.append((battle.order_text(battle.Order(do="barrier"), names), "在身后制造障碍，失去一次移动", battle.Order(do="barrier")))
    for f in ch.runners:
        if f.side != me.side and f.standing and abs(f.pos - me.pos) <= 0:
            o = battle.Order(do="turn", target=f.id)
            out.append((battle.order_text(o, names), "停下来动手，追逐转为战斗", o))
    return out


def _order_of(d: DeclaredAction):
    """A keeper-plan declaration as the old chase engine's order."""
    from .. import combat as battle

    target = d.attacks[0].target if d.attacks else d.target
    if d.do in ("attack", "shoot", "maneuver", "stand"):
        return battle.Order(do="turn", target=target)
    if d.do == "hide":
        return battle.Order(do="hide")
    if d.do == "barrier":
        return battle.Order(do="barrier")
    if d.do in ("move", "flee", "pursue", "cross"):
        return battle.Order(do="run")
    return battle.Order(do="other", text=d.summary)


__all__ = ["Encounters", "chase_options"]
