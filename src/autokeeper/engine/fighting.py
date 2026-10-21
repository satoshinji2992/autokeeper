"""The old chase engine (0.0.3), kept for 0.0.4's M2 while fights run on engine/encounter.

A chase runs along the module's way for the place (Location.chase, or that of the place it is in),
else the one for its kind of place (engine/combat.default_track), a round at a time, its rolls and
wounds in the turn's journaled stages and landed in the world through effects (the same bodies and
guns as the fights'). It runs inside an encounter in chase mode (engine/encounter/runner.py): the
encounter keeps the stakes and the people, and when the chase ends it either ends the encounter or
turns back into a fight. WP-D replaces all of this with the encounter's own chase units (M3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..rules import chase as chase_rules
from ..rules import combat as rules
from ..rules import wounds
from ..rules.catalog import canonical
from ..rules.chase import Leg, Obstacle, Track
from ..rules.combat import Fighter
from ..rules.dice_expr import DamageExpr
from ..world.ops import Occur
from ..world.record import ENGINE
from . import combat as battle
from . import effects
from .combat import Chase, Order
from .encounter.telling import BEGIN_TELL, CHASE_TELL, FIGHT_TELL, tell

if TYPE_CHECKING:
    from ..agents.cast import Action, Ruling
    from .game import Turn


def _merge(dst, src) -> None:
    """Make `dst` what `src` is, keeping the objects that still stand for the same thing (the chase,
    each runner), so whoever holds them sees an act's copy land on them."""
    from pydantic import BaseModel

    for name in type(src).model_fields:
        a, b = getattr(dst, name), getattr(src, name)
        if isinstance(a, BaseModel) and type(a) is type(b):
            _merge(a, b)
        elif (isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and a
              and all(isinstance(x, BaseModel) and type(x) is type(y) for x, y in zip(a, b, strict=True))):
            for x, y in zip(a, b, strict=True):
                _merge(x, y)
        else:
            setattr(dst, name, b)


class Fighting:
    """Game's old chase engine (engine/combat.py has its pieces, rules/ the numbers)."""

    if TYPE_CHECKING:  # what the Game it is mixed into has
        from ..module.schema import Module
        from ..rules.coc import Rules
        from ..world.store import World
        from .state import GameState

        state: GameState
        m: Module
        rules: Rules
        world: World
        cast: Any
        holding: bool
        emit: Any
        _resolution_step: Any
        _resolution_key: Any
        _flags: Any
        _time: Any
        _check_ending: Any
        _debug: Any
        _sights: Any
        _chase_closed: Any
        _encounter_end: Any

    def _runners_orders(self, ids: list[str]) -> dict[str, Any]:
        """Orders for those nobody declared one for this round: they run (the old chase has no keeper
        arrangement; WP-D gives the encounter's chase its own)."""
        return {"orders": {i: Order(do="run").model_dump() for i in ids}, "say": {}, "pursue": []}

    def _others(self, ids: list[str]) -> list[Fighter]:
        """The module's NPCs and creatures named, as they run (the dead left out), by the names the
        players know (engine/encounter/runner._fighter)."""
        from .encounter import profiles

        out: list[Fighter] = []
        for fid in dict.fromkeys(profiles.combatant(self.world, x) for x in ids):
            if self._dead(fid):  # type: ignore[attr-defined]
                continue
            f = self._fighter(fid)  # type: ignore[attr-defined]
            if f is not None:
                out.append(f)
        self._seed([f.id for f in out])
        return out

    def _seed(self, ids: list[str]) -> None:
        """NPCs and creatures entering a fight: the world keeps their bodies from now on (a creature
        becomes a world entity, where the investigators are)."""
        if self.world is None or not ids:
            return
        done = effects.commit(self.world, [], op_id=f"seed:{self.state.turn}:{'+'.join(sorted(ids))}", source="fight", seed=ids)
        if not done.ok:
            raise RuntimeError("；".join(done.problems))

    def _carried_by(self, who: str) -> list[str]:
        """What the record has someone hold: their weapons among it."""
        if self.world is None:
            return []
        return [e.name for e in self.world.rec.entities.values() if e.kind == "item" and e.at == who]

    def _creature_face(self, cid: str) -> str:
        """The picture key of a creature's face: the module's, else one painted by program (scenery)."""
        key = f"creature-{cid}"
        return key if key in self.m.pictures else ""

    # -- what a fight does to bodies and things lands in the world (engine/effects) -----------------
    _fx: list | None = None  # the act's effects, while one runs
    _bodies: dict[str, effects.Body]
    _adopt: dict[str, Any]
    _after: list[Callable[[], Any]]
    _act: str = ""
    _wounds: int = 0

    def _act_key(self, what: str) -> str:
        """The op id of an act: the turn, the fight's round and who, and the stage it runs in."""
        return f"{what}:t{self.state.turn}:{self._resolution_key('fx')}"

    def _landed(self, key: str, people: list[Fighter], work: Callable[[], dict]) -> dict:
        """Run one act on copies (`people` are the copy's fighters). Its wounds, aid, deaths, rounds
        fired, jams, reloads and dropped weapons are collected as effects and land in one world op
        with id `key`: all or none, and a retried act lands nothing twice. Then the fighters are
        what the world says, and the act's log lines go out."""
        self._fx, self._bodies, self._adopt, self._after, self._act, self._wounds = [], {}, {}, [], key, 0
        arms = self._arms(people)
        try:
            out = work()
            fx = [*self._fx, *self._arms_changed(arms, people)]
            if self.world is not None and (fx or self._adopt):
                done = effects.commit(self.world, fx, op_id=key, source="fight", adopt=self._adopt)
                if not done.ok and any("已经用于另一个操作" in p for p in done.problems):
                    # This act landed once already and its dice were lost since (a journal cleared
                    # after repeated failures): what landed stands; the fighters follow the world.
                    self._debug("fight", {"phase": "landed-before", "op": key})
                elif not done.ok:
                    raise RuntimeError("战斗结算没有落地：" + "；".join(done.problems))
            after = self._after
        finally:
            self._fx = None
        for f in people:
            self._sync(f)
        for fn in after:
            fn()
        return out

    def _known(self, f: Fighter) -> bool:
        """Whether the world keeps this fighter's body: an investigator, a module NPC or creature."""
        from .encounter.profiles import profile

        if self.world is None:
            return False
        return f.kind == "investigator" or profile(self.world, f.id) is not None

    def _body(self, f: Fighter) -> effects.Body:
        """The act's working copy of someone's body: the world's, or (the first time the world sees
        them) the fighter's own numbers, which the world then adopts."""
        holder = battle.holder_of(self.world, f)
        if holder in self._bodies:
            return self._bodies[holder]
        ent = self.world.rec.entities.get(holder)
        if f.kind == "investigator" or (ent is not None and ent.life is not None):
            body = effects.body_of(self.world, f.id)
            assert body is not None
        else:
            from ..world.record import Life

            conditions = {"死亡": ["死亡"], "濒死": ["重伤", "濒死", "昏迷"], "昏迷": ["昏迷"], "重伤": ["重伤"]}.get(f.status, [])
            life = Life(hp=f.hp, hp_max=f.hp_max, conditions=conditions, regenerating=f.status == "倒下")
            body = effects.Body.of(life, human=f.human, armor=f.armor, guns_halved=f.guns_halved, knockout=f.knockout, regen=f.regen)
            self._adopt[holder] = life
        self._bodies[holder] = body
        return body

    def _sync(self, f: Fighter) -> None:
        """A fighter as the world now keeps them (after an act landed)."""
        if self.world is None:
            return
        if f.kind == "investigator":
            inv = next((i for i in self.state.investigators if i.id == f.id), None)
            if inv is not None:
                battle.sync(f, inv)
            return
        ent = self.world.rec.entities.get(battle.holder_of(self.world, f))
        if ent is not None and ent.life is not None:
            f.hp, f.hp_max = ent.life.hp, ent.life.hp_max
            f.status = battle.life_status(ent.life.conditions, ent.life.regenerating, f.status)

    def _shown(self, f: Fighter, body: effects.Body) -> None:
        """The fighter as the act's working body leaves them, before it has landed."""
        f.hp, f.hp_max = body.hp, body.hp_max
        f.status = battle.life_status(body.conditions, body.regenerating, f.status)

    def _effect(self, f: Fighter, e) -> effects.Body | None:
        """One effect on a fighter's body in this act: applied to the working body now, landed with the act."""
        if self._fx is None or not self._known(f):
            return None
        body = self._body(f)
        if isinstance(e, effects.Heal):
            body = effects.heal(body, e)
        elif isinstance(e, effects.Stabilize):
            body = effects.stabilize(body, self.state.minutes)
        elif isinstance(e, effects.Wake):
            body = wounds.wake(body)
        elif isinstance(e, effects.Die):
            body = effects.die(body)
        self._bodies[battle.holder_of(self.world, f)] = body
        self._fx.append(e)
        self._shown(f, body)
        return body

    def _arms(self, people: list[Fighter]) -> dict[tuple[str, str], tuple[int | None, bool, str | None]]:
        """Every firearm's rounds and jam before the act, with the item it is."""
        if self.world is None:
            return {}
        return {(f.id, a.name): (a.ammo, a.jammed, battle.item_for(self.world, f, a))
                for f in people if self._known(f) for a in f.attacks if a.firearm}

    def _arms_changed(self, before: dict, people: list[Fighter]) -> list:
        """What the act did to guns, as absolute effects on their items: rounds left, a jam, cleared."""
        out: list = []
        for f in people:
            for a in f.attacks:
                was = before.get((f.id, a.name))
                if was is None or was[2] is None:
                    continue
                if a.ammo is not None and a.ammo != was[0]:
                    out.append(effects.Ammo(item=was[2], loaded=a.ammo))
                if a.jammed != was[1]:
                    out.append(effects.Jam(item=was[2], jammed=a.jammed))
        return out

    @staticmethod
    def _dice(check) -> dict:
        from .game import _check_view

        return _check_view(check)

    def _hurt(self, f: Fighter, amount: int, *, unarmed: bool, out: dict, means: str = "blow") -> list[str]:
        """A wound, through engine/effects like every damage in the game: rolled here (the CON roll a
        major wound calls for), applied to the act's working body, landed in the world with the act.
        Armour, guns halved and a knockout are the body's (its sheet's profile)."""
        st = self.state
        if self._fx is None or not self._known(f):  # nobody the world keeps (a bare test fighter)
            return rules.wound(self.rules.dice, f, amount, unarmed=unarmed)
        body = self._body(f)
        self._wounds += 1
        injury = f"{self._act}:w{self._wounds}"
        wound = effects.harm(self, f.id, amount, means="unarmed" if unarmed else means, injury=injury, body=body)  # type: ignore[arg-type]
        hurt = effects.wound(body, wound, minutes=st.minutes)
        body = self._bodies[battle.holder_of(self.world, f)] = hurt.body
        self._fx.append(wound)
        self._shown(f, body)
        if f.kind == "investigator":
            name, said = st.find(f.id).name, hurt.describe()
            self._after.append(lambda: st.add("damage", said, who=name))
            out["events"].append({"type": "damage", "who": name, "text": said, "fighter": f.id, "injury": injury})
            return [said]
        return list(hurt.notes)

    def _chase_way(self) -> Track | None:
        """The way the module has a chase from here run along (Location.chase): this place's, else
        that of the place it is in (out of the study and through the house); None when there is none
        the rules can run (4 to 12 legs). A fresh copy: what a chase does to its way (a barrier
        broken open, crates pushed over) stays in that chase."""
        m, here, seen = self.m, self.state.location, set()
        while here and here not in seen:
            seen.add(here)
            loc = next((x for x in m.locations if x.id == here), None)
            if loc is None:
                return None
            if 4 <= len(loc.chase) <= 12:
                track = Track(legs=[leg.model_copy(deep=True) for leg in loc.chase])
                for leg in track.legs:
                    if leg.obstacle is not None:
                        leg.obstacle.open = False
                return track
            here = loc.within or ""
        return None

    def _chase_setup(self, runners: list[Fighter], track: Track, quarry: str, reason: str, *, rolled: bool) -> list[str]:
        """Places and speeds: the quarry a head start ahead, everyone's CON for speed (rolled here unless
        `rolled`). Returns what the speed rolls were."""
        st = self.state
        lines: list[str] = []
        for f in runners:
            f.pos = chase_rules.HEAD_START if f.side == quarry else 0
            f.responded, f.lost_action, f.prone, f.held_by = 0, False, False, ""
            if not rolled:
                roll = chase_rules.speed_roll(self.rules.dice, f)
                lines.append(f"{f.name}的体质 {roll.value} {roll.level.zh}：速度 {f.speed}")
        st.chase = Chase(runners=runners, track=track, quarry=quarry, place=st.location, reason=reason[:120], started=st.turn)  # type: ignore[arg-type]
        return lines

    async def _chase_track(self) -> dict:
        """The way the chase runs along: the module's for this place (Location.chase), else the one for
        this kind of place."""
        from .. import scenery

        st, m = self.state, self.m
        if (route := self._chase_way()) is not None:
            return route.model_dump()
        loc = m.location(st.location)
        return battle.default_track(scenery.kind_of(loc.name, loc.read_aloud)).model_dump()

    def _chase_begin(self, ruling: Ruling, action: Action, track: dict) -> list[dict]:
        st = self.state
        others = self._others(ruling.chase or ([action.npc] if action.npc else []))
        if not others:
            return []
        party = [battle.from_investigator(inv, self._carried_by(inv.id), world=self.world) for inv in st.investigators if inv.can_act]
        quarry = "foe" if ruling.chase_party == "追" else "party"
        lines = self._chase_setup([*party, *others], Track.model_validate(track), quarry, ruling.reason, rolled=False)
        ch = st.chase
        assert ch is not None
        head = f"【追逐开始】{'调查员在逃，身后是' if quarry == 'party' else '调查员追赶'}" + "、".join(f.name for f in others)
        st.add("event", head)
        return [{"type": "chase_start", "chase": battle.chase_view(ch), "line": head, "speeds": lines}]

    async def _chase_open(self, ruling: Ruling, action: Action, turn: Turn, *, paid: bool = False) -> str:
        """A chase begins: its way, who runs, their speeds, a creature's first sight (`paid` as in
        _sights), what starting it achieves. "on" while it goes on; "none" with nobody in it; "over"
        when it is over at once (every quarry far faster than everyone after them)."""
        st, step = self.state, self._resolution_step
        track = await step("chase-track", turn, self._chase_track)
        begun = await step("chase-begin", turn, lambda: self._chase_begin(ruling, action, track))
        for i, ev in enumerate(begun):
            await step(f"chase-begin:{i}", turn, lambda ev=ev: self.emit(ev))
        if not begun:
            return "none"
        await step("chase-told", turn, lambda: turn.fight.extend([begun[0]["line"], *begun[0].get("speeds", [])]))
        await self._sights(turn, paid=paid)
        if ruling.set_flags:
            await step("chase-flags", turn, lambda: self._flags(ruling.set_flags, turn))
        ch = st.chase
        if ch is None:  # over at once, and this is that turn resumed
            return "over"
        if chase_rules.gone_at_once(ch.running(ch.quarry), ch.running("foe" if ch.quarry == "party" else "party")):
            await self._chase_end(turn, "escaped", "跑得比追的人快得多，转眼就没了影")
            return "over"
        return "on"

    async def _chase_turn(self, action: Action, ruling: Ruling, turn: Turn) -> None:
        st, step = self.state, self._resolution_step
        ch = st.chase
        if ch is None:  # over before the turn was cut short: nothing is owed
            return
        me = st.investigator.id
        chased = (action.declared or {}).get("chase")
        order = Order.model_validate(chased) if chased else Order(do="run")

        def declare() -> None:
            if ch.get(me) is not None:
                ch.orders[me] = order

        await step("chase-declare", turn, declare)
        if self.holding:
            turn.declared = True
            return
        await self._chase_round(turn, ruling)

    async def _chase_round(self, turn: Turn, ruling: Ruling | None) -> None:
        st, step = self.state, self._resolution_step
        ch = st.chase
        assert ch is not None
        pending = [f.id for f in ch.runners if f.standing and f.id not in ch.orders and f.id not in ch.hidden]
        plan = await step("chase-tactics", turn, lambda: self._runners_orders(pending))

        def begin() -> list[str]:
            ch.round += 1
            moving = [f for f in ch.runners if f.standing and f.id not in ch.hidden]
            slowest = min((f.speed for f in moving), default=8)
            for f in moving:
                f.actions = chase_rules.actions_for(f, slowest)
            for fid, o in plan["orders"].items():
                if fid not in ch.orders:
                    ch.orders[fid] = Order.model_validate(o)
            turn.fight.append(f"【追逐 · 第 {ch.round} 轮】" + "；".join(f"{f.name}能移动 {f.actions} 次" for f in moving))
            return rules.order(moving, set())  # the old chase has no shooting

        sequence = await step("chase-round", turn, begin)
        await step("chase-round-notice", turn, lambda: self.emit({"type": "chase_round", "round": ch.round,
                                                                   "chase": battle.chase_view(ch)}))
        turned = None
        for i, fid in enumerate(sequence):
            out = await step(f"chase-act:{i}", turn, lambda fid=fid: self._chase_act(fid, turn))
            for k, ev in enumerate(out["events"]):
                await step(f"chase-act:{i}:{k}", turn, lambda ev=ev: self.emit(ev))
            if out.get("turned"):
                turned = out["turned"]
                break
            if out.get("caught"):
                break
        if turned:  # someone stopped and turned on the other: it comes to blows (the review: it used to be dropped)
            a, b = (ch.get(x) for x in turned)
            await self._chase_end(turn, "caught", f"{a.name}回身迎向{b.name}", fight_ids=[turned[1], turned[0]] if a.side == ch.quarry
                                  else list(turned))
            return
        await self._chase_settle(turn)

    def _chase_act(self, fid: str, turn: Turn) -> dict:
        """One runner spends their movement: runs leg by leg, crossing what is in the way, hides or
        puts something in the pursuers' way, or turns to fight. On a copy of the chase, its wounds
        landed in one world op (_landed), as a fight's act is."""
        st = self.state
        assert st.chase is not None
        work = st.chase.model_copy(deep=True)
        out = self._landed(self._act_key(f"chase:{work.started}:r{work.round}:{fid}"), work.runners,
                           lambda: self._chase_act_on(work, fid))
        _merge(st.chase, work)
        turn.fight += out.pop("told", [])
        return out

    def _chase_act_on(self, ch: Chase, fid: str) -> dict:
        st = self.state
        out: dict = {"events": [], "caught": None, "turned": None}
        f = ch.get(fid)
        if f is None or not f.standing or f.id in ch.hidden:
            return out
        order = ch.orders.get(fid) or Order(do="run")
        dice = self.rules.dice
        lines: list[str] = []
        quarry = f.side == ch.quarry
        others = ch.running("foe" if f.side == "party" else "party")
        if order.do == "other":  # something else than running: the keeper rules it; it costs the round's running
            lines.append(f"{f.name}停下脚步：{order.text}" if order.text else f"{f.name}停下来做别的事")
            return self._chase_told(out, lines, ch)
        if order.do == "turn":
            target = ch.get(order.target) if order.target else min(others, key=lambda o: abs(o.pos - f.pos), default=None)
            if target is not None and abs(target.pos - f.pos) <= 1:
                out["turned"] = [f.id, target.id]
                lines.append(f"{f.name}停下脚步，转身迎向{target.name}")
                return self._chase_told(out, lines, ch)
            order = Order(do="run")
        if order.do == "hide" and quarry:
            hunters = ch.running("foe" if ch.quarry == "party" else "party")
            if chase_rules.gap([f], hunters) >= chase_rules.SIGHT:
                stealth = st.find(f.id).value("潜行") if f.kind == "investigator" else f.value("潜行")
                roll, _seen, hidden = chase_rules.hide(dice, stealth, hunters)
                if f.kind == "investigator":
                    out["events"].append({"type": "dice", "who": f.name, **self._dice(roll)})
                if hidden:
                    ch.hidden.append(f.id)
                    lines.append(f"{f.name}闪身躲了起来（潜行 {roll.value} {roll.level.zh}），追兵没有发现")
                else:
                    lines.append(f"{f.name}想躲起来，没能藏住（潜行 {roll.value} {roll.level.zh}）")
                    f.actions = 0
                return self._chase_told(out, lines, ch)
            lines.append(f"{f.name}想躲，可追兵就在眼前")
        if order.do == "barrier" and quarry and f.actions > 0:
            value = st.find(f.id).value("力量") if f.kind == "investigator" else f.str_
            roll = rules.check(dice, "力量", value) if f.kind != "investigator" else self.rules.check(st.find(f.id), "力量", mark=False)
            if f.kind == "investigator":
                out["events"].append({"type": "dice", "who": f.name, **self._dice(roll)})
            f.actions -= 1
            leg = ch.track.at(f.pos)
            if roll.passed and leg is not None and leg.obstacle is None:
                leg.obstacle = Obstacle(kind="barrier", what="推倒在路上的杂物", skill="敏捷")
                lines.append(f"{f.name}把身边的东西推倒在{leg.name}，挡住了身后的路（力量 {roll.value} {roll.level.zh}）")
            else:
                lines.append(f"{f.name}想挡住身后的路，没能弄成（力量 {roll.value} {roll.level.zh}）")
        moved = 0
        while f.actions > 0:
            nxt = f.pos + 1
            f.actions -= 1
            if quarry and nxt >= len(ch.track.legs):  # off the end of the way: gone
                f.pos = nxt
                ch.hidden.append(f.id)
                lines.append(f"{f.name}冲出了最后一段路，消失在黑暗里")
                break
            leg = ch.track.at(nxt)
            if leg is None:
                break
            if leg.obstacle is not None and not (leg.obstacle.kind == "barrier" and leg.obstacle.open):
                ob = leg.obstacle
                value = st.find(f.id).value(canonical(ob.skill)) if f.kind == "investigator" else f.value(ob.skill)
                # The obstacle's harm is rolled here, through rules/dice_expr: what a module or a model
                # wrote never reaches BCDice as written, and one that is no expression is a note.
                harm, bad = None, ""
                if ob.harm:
                    try:
                        harm = DamageExpr.parse(ob.harm)
                    except ValueError as exc:
                        bad = f"伤害式无法使用：{exc}"
                plain = leg.model_copy(update={"obstacle": ob.model_copy(update={"harm": ""})})
                roll, crossed = chase_rules.cross(dice, f, plain, value=value)
                ob.open = plain.obstacle.open  # type: ignore[union-attr]
                if crossed is not None and not crossed.passed and crossed.kind == "hazard" and harm is not None and not harm.none:
                    crossed.harm = harm.total(dice, f.db)
                    crossed.harm_text = f"{harm.text()} = {crossed.harm}"
                if roll is not None and f.kind == "investigator":
                    out["events"].append({"type": "dice", "who": f.name, **self._dice(roll)})
                if crossed is not None and not crossed.passed:
                    if crossed.kind == "barrier":
                        lines.append(f"{f.name}被{ob.what}挡住了（{ob.skill} {roll.value} {roll.level.zh}）")  # type: ignore[union-attr]
                        break
                    f.actions = max(0, f.actions - crossed.lost)
                    hurt = f"（{bad}）" if bad else ""
                    if crossed.harm:
                        notes = self._hurt(f, crossed.harm, unarmed=False, out=out, means="fall")
                        hurt = f"，受了 {crossed.harm_text} 点伤" + (f"（{'、'.join(notes)}）" if notes else "")
                    lines.append(f"{f.name}在{ob.what}上栽了跟头（{ob.skill} {roll.value} {roll.level.zh}），耽搁了{crossed.lost}步{hurt}")  # type: ignore[union-attr]
                    if not f.standing:
                        break
                elif crossed is not None:
                    lines.append(f"{f.name}越过了{ob.what}（{ob.skill} {roll.value} {roll.level.zh}）")  # type: ignore[union-attr]
            f.pos = nxt
            moved += 1
            if not quarry:
                prey = [q for q in ch.running(ch.quarry) if q.pos <= f.pos]
                if prey:
                    out["caught"] = [f.id, prey[0].id]
                    lines.append(f"{f.name}追上了{prey[0].name}")
                    break
        if moved and not out["caught"]:
            lines.append(f"{f.name}跑到了{ch.track.legs[min(f.pos, len(ch.track.legs) - 1)].name}")
        return self._chase_told(out, lines, ch)

    def _chase_told(self, out: dict, lines: list[str], ch: Chase) -> dict:
        out.setdefault("told", []).extend(lines)
        out["events"].append({"type": "chase_state", "chase": battle.chase_view(ch), "lines": lines})
        return out

    async def _chase_settle(self, turn: Turn) -> None:
        """The end of a chase's round: caught (it comes to blows), lost (the quarry got away), or on."""
        st = self.state
        ch = st.chase
        assert ch is not None
        quarry = ch.running(ch.quarry)
        hunters = ch.running("foe" if ch.quarry == "party" else "party")
        close = [(h, q) for h in hunters for q in quarry if h.pos >= q.pos]
        if close:
            h, q = close[0]
            await self._chase_end(turn, "caught", f"{h.name}追上了{q.name}", fight_ids=[h.id, q.id])
        elif not quarry:
            await self._chase_end(turn, "escaped", "全都脱了身")
        elif not hunters or chase_rules.gap(quarry, hunters) >= chase_rules.LOST:
            await self._chase_end(turn, "escaped", "把追兵远远甩在了后面")
        else:
            ch.orders = {}

    async def _chase_end(self, turn: Turn, end: str, how: str, *, fight_ids: list[str] | None = None) -> None:
        """Over: the quarry got away (the encounter ends, its stakes judged), or was caught and it comes
        to blows (the encounter is a fight again: engine/encounter/runner._chase_closed)."""
        st, step = self.state, self._resolution_step
        ch = st.chase
        if ch is None:  # closed before the turn was cut short: what it owed the screens goes out
            for i, ev in enumerate(await step("chase-close", turn, lambda: [])):
                await step(f"chase-close:{i}", turn, lambda ev=ev: self.emit(ev))
            return
        names = "、".join(f.name for f in ch.runners if f.side != "party")
        if end == "caught":
            text = f"追逐结束：{how}"
        elif ch.quarry == "party":
            text = f"追逐结束：调查员{how}，{names}没能追上"
        else:
            text = f"追逐结束：{names}{how}，调查员没能追上"

        def record() -> None:
            turn.fight.append(f"【{text}】")
            st.add("event", text)
            st.minutes += max(1, ch.round)
            npcs = {n.id for n in self.m.npcs}
            people = [f.id for f in ch.runners if f.kind == "investigator" or f.id in npcs]
            self.world.apply(Occur(id=f"chase:{ch.started}:{st.turn}:end", actor=ENGINE, participants=people, place=st.location,
                                   text=text[:300], visibility="public"))

        await step("chase-record", turn, record)
        runners = {f.id: f for f in ch.runners}
        hunter = runners.get(fight_ids[0]) if end == "caught" and fight_ids else None
        events = await step("chase-close", turn, lambda: [{"type": "chase_end", "end": end, "text": text,
                                                           "hunter": hunter.id if hunter else "",
                                                           "hunter_name": hunter.name if hunter else "",
                                                           "portrait": hunter.portrait if hunter else ""}])
        quarry = ch.quarry
        await self._chase_closed(end, text, fight_ids, turn)
        for i, ev in enumerate(events):
            await step(f"chase-close:{i}", turn, lambda ev=ev: self.emit(ev))
        enc = st.encounter
        if enc is None or enc.phase == "ended":
            return
        if end == "caught":
            from .encounter import views

            line = "【追逐转为战斗】扭打在一起"
            await step("chase-fight", turn, lambda: self.emit({"type": "fight_start", "fight": views.fight_view(self),
                                                               "line": line, "surprise": ""}))
            return
        await self._encounter_end("fled" if quarry == "party" else "escaped", turn)

__all__ = ["BEGIN_TELL", "CHASE_TELL", "FIGHT_TELL", "Fighting", "Leg", "tell"]
