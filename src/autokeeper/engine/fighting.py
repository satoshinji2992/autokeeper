"""Fights and chases in the game: a round at a time, in the turn's journaled stages.

How one comes about: the arbiter rules that an action starts it (Ruling.combat, Ruling.chase); a
module's event scripts it (Event.fight, Event.chase: an ambush, a pack on their heels), begun where
the investigators are with the first round left to their next input (Fighting._scripted); or a
fight ends with the investigators running and something going after them. From then on the game is
in it (GameState.fight, GameState.chase) until it is over, and every player input is that
investigator's order for the round (engine/combat.Order: the screen offers them as words, and any
words work, read by the engine or by Cast.order). A chase runs along the module's way for the place
(Location.chase, or that of the place it is in), else one the tactics agent draws (Cast.track), else
the one for its kind of place (engine/combat.default_track).

A round is settled whole, in DEX order. The NPCs', the creatures' and the AI investigators' orders
come from one tactics call (Cast.tactics; engine/combat.rule_of_thumb when it fails or is slow). Every
roll and every wound lands in the turn's journaled stages (Game._resolution_step), so a round cut
short goes on from where it was and nothing is rolled or suffered twice. The screens see each
exchange as it happens (fight_act), with the dice of the investigators' own rolls; the keeper tells
the round once it is settled (Turn.fight). At a LAN table everyone at the table declares in the
round's window and the last declaration settles it (Game.holding, net/hub.py).

When it is over the record learns who was hurt or killed and what happened, the module's flags the
outcome brings about are set (Cast.outcome) with what hangs on them, and the clock moves on."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..rules import chase as chase_rules
from ..rules import combat as rules
from ..rules import wounds
from ..rules.bcdice import Level
from ..rules.catalog import canonical
from ..rules.chase import Leg, Obstacle, Track
from ..rules.coc import DIFFICULTY_ZH
from ..rules.combat import STANCES, Attack, Fighter
from ..rules.dice_expr import DamageExpr
from ..world.ops import Move, Occur
from ..world.record import ENGINE
from . import combat as battle
from . import effects
from .combat import Chase, Fight, Order

if TYPE_CHECKING:
    from ..agents.cast import Action, Ruling
    from ..module.schema import Event
    from .game import Turn

ATTACKING = ("attack", "shoot", "maneuver")
FIGHT_TELL = ("下面是这一轮按先后结算出来的经过。照它写一段紧凑、有画面的叙述：谁先动手、打中了谁、伤成什么样，混乱和恐惧可以写，节奏要快。"
              "检定和伤害的数字已经显示给玩家，不要复述数字；只写结算出来的结果，不新添伤口、倒地、死亡或别的动作，"
              "不替调查员决定下一轮做什么。120–220 字。")
CHASE_TELL = ("下面是这一轮追逐按先后结算出来的经过。照它写一段急促的叙述：脚步、喘息、身后的动静、绊倒和翻越，"
              "前面还有什么路。数字已经显示过，不复述；只写结算出来的结果，不替调查员决定下一步。100–200 字。")
BEGIN_TELL = ("打斗或追逐刚刚开始，这一轮还没有人出手。写对方怎样扑上来、追上来，此刻的局面、距离和声响，紧张，有画面；"
              "不写谁打中了谁、谁受了伤，不替调查员决定怎么应对，显示过的数字不复述。80–160 字。")


def tell(lines: list[str]) -> str:
    """What the keeper is asked to write for a turn with a fight or a chase in it (Turn.fight): the
    round as settled, or how it begins when nothing is settled yet (an event's scripted attack)."""
    if not any(line.startswith(("【战斗 · 第", "【追逐 · 第", "【战斗结束", "【追逐结束")) for line in lines):
        return BEGIN_TELL
    chase = any(line.startswith("【追逐") for line in lines) and not any(line.startswith("【战斗") for line in lines)
    return CHASE_TELL if chase else FIGHT_TELL


def _merge(dst: BaseModel, src: BaseModel) -> None:
    """Make `dst` what `src` is, keeping the objects that still stand for the same thing (the fight,
    each fighter, each attack), so whoever holds them sees an act's copy land on them."""
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
    """Game's fights and chases (engine/combat.py has the pieces, rules/ the numbers)."""

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
        _flags: Any
        _time: Any
        _check_ending: Any
        _debug: Any

    # -- the turn ------------------------------------------------------------------------------
    async def _battle(self, action: Action, ruling: Ruling, turn: Turn) -> None:
        """This turn is a round of a fight or a chase, or starts one."""
        st, step = self.state, self._resolution_step
        # Decided once, with what was open when the turn began: resumed after the fight or the chase
        # closed (a notice that did not go out), the turn finishes that one, never opens a new one.
        mode, was_open = await step("battle-mode", turn, lambda: (
            "chase" if st.chase is not None or (st.fight is None and ruling.chase) else "fight",
            st.fight is not None or st.chase is not None))
        if was_open and not self.battling() and await self._battle_closed(turn, mode):
            pass
        elif mode == "chase":
            await self._chase_turn(action, ruling, turn)
        else:
            await self._fight_turn(action, ruling, turn)
        if not turn.declared:
            await step("time", turn, lambda: self._time(turn))
            await step("ending", turn, lambda: self._check_ending(turn))

    def battling(self) -> bool:
        return self.state.fight is not None or self.state.chase is not None

    async def _battle_closed(self, turn: Turn, mode: str) -> bool:
        """The fight or the chase this turn was a round of closed before the turn was cut short: only
        what it still owed the screens goes out (the closing step's saved notices). True if that was so."""
        step, steps = self._resolution_step, (self._resolution or {}).get("steps", {})
        key = "fight-close" if mode == "fight" else "chase-close"
        if not steps.get(self._resolution_key(key), {}).get("done"):
            return False
        if mode == "fight":
            ev = await step("fight-close", turn, lambda: None)
            await step("fight-close-notice", turn, lambda: self.emit(ev))
            if self.state.chase is not None:
                await step("chase-from-fight-notice", turn, lambda: self.emit({"type": "chase_start", "chase": battle.chase_view(self.state.chase)}))  # type: ignore[arg-type]
        else:
            for i, ev in enumerate(await step("chase-close", turn, lambda: [])):
                await step(f"chase-close:{i}", turn, lambda ev=ev: self.emit(ev))
        return True

    async def _battle_action(self, text: str) -> Action:
        """The player's words in a fight or a chase, as the action that carries their order."""
        from ..agents.cast import Action

        st = self.state
        everyone = st.fight.fighters if st.fight is not None else st.chase.runners  # type: ignore[union-attr]
        me = next((f for f in everyone if f.id == st.investigator.id), None)
        if me is None:  # not in it (they had left it): words, as any other turn
            return Action(kind="other", summary=f"{st.investigator.name}：{text}")
        order = battle.parse_order(text, me, everyone, chase=st.chase is not None)
        asker = getattr(self.cast, "order", None)
        if order is None and asker is not None:
            try:
                order = Order.model_validate(await asker(st, text, self._battle_brief()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._debug("order", {"phase": "fallback", "error": f"{type(exc).__name__}: {exc}"[:300]})
        if order is None:
            order = Order(do="other", text=text)
        names = {f.id: f.name for f in everyone}
        target = names.get(order.target, "")
        npc = order.target if any(n.id == order.target for n in self.m.npcs) else ""
        kind = "attack" if order.do in ATTACKING or order.do == "turn" else "other"
        summary = f"{st.investigator.name}：{battle.order_text(order, names)}" if order.do != "other" else f"{st.investigator.name}：{text}"
        return Action(kind=kind, summary=summary, target=target, npc=npc, order=order.model_dump())

    def _battle_ruling(self, action: Action) -> Ruling | None:
        """The roll an order makes, for the player's own dice (graph.ask_roll); None for an order the
        arbiter rules ("other": breaking a lamp over its head, slamming a door)."""
        from ..agents.cast import Ruling

        st = self.state
        order = Order.model_validate(action.order or {})
        everyone = st.fight.fighters if st.fight is not None else st.chase.runners if st.chase is not None else []
        me = next((f for f in everyone if f.id == st.investigator.id), None)
        if order.do == "other" or me is None:
            return None
        if order.do in ATTACKING:
            atk = self._weapon(me, order)
            skill = rules.BRAWL if atk.unarmed or order.do == "maneuver" else atk.skill
            return Ruling(needs_roll=True, skill=skill, reason="战斗中的攻击")
        if order.do == "aid":
            return Ruling(needs_roll=True, skill="急救", reason="战斗中的急救")
        if order.do == "hide":
            return Ruling(needs_roll=True, skill="潜行", reason="追逐中躲藏")
        if order.do == "barrier":
            return Ruling(needs_roll=True, skill="力量", reason="追逐中制造障碍")
        return Ruling(needs_roll=False, reason="战斗中")

    @staticmethod
    def _weapon(f: Fighter, order: Order) -> Attack:
        """The attack an order names, else the one in hand. Something picked up on the spot (a
        candlestick, a chair) is the weapon it is most like, or a club made of whatever it is."""
        from ..rules.weapons import weapon_in

        if order.weapon:
            for a in f.attacks:
                if a.name == order.weapon or order.weapon in a.name or a.name in order.weapon:
                    return a
            if order.do in ("attack", "shoot"):
                like = weapon_in(order.weapon)
                if like is not None and not like.firearm:
                    return rules.attack_of(like, f.value(like.skill)).model_copy(update={"name": order.weapon})
                if like is None:  # 7th edition's improvised club
                    return Attack(name=order.weapon, skill=rules.BRAWL, value=f.fighting, damage="1D6+DB")
        if order.do == "shoot":
            guns = [a for a in f.attacks if a.firearm and a.usable()]
            if guns:
                return max(guns, key=lambda a: a.value)
        if order.do == "maneuver":
            return next((a for a in f.attacks if a.unarmed), rules.attack_of(rules.UNARMED, f.fighting))
        return f.wielding

    def _battle_brief(self) -> str:
        """The fight or the chase as the tactics agent and the order reader see it: everyone in it,
        how they stand, what they fight with, what the module says of them."""
        st, m = self.state, self.m
        loc = m.location(st.location)
        rows = [f"地点：{loc.name}；{st.clock}"]
        if st.fight is not None:
            f = st.fight
            rows.append(f"【战斗】第 {f.round + 1} 轮")
            people = f.fighters
        else:
            ch = st.chase
            assert ch is not None
            rows.append(f"【追逐】第 {ch.round + 1} 轮；{'调查员在逃' if ch.quarry == 'party' else '调查员在追'}；路线："
                        + " → ".join(f"{i}.{leg.name}" + (f"（{leg.obstacle.what}）" if leg.obstacle else "")
                                     for i, leg in enumerate(ch.track.legs)))
            people = ch.runners
        for x in people:
            side = "调查员一方" if x.side == "party" else "对手"
            who = "调查员" if x.kind == "investigator" else ("怪物" if x.kind == "creature" else "NPC")
            ai = "，AI 控制" if x.kind == "investigator" and x.id not in st.human else ""
            attacks = "、".join(f"{a.name}{a.value}%" + (f"（余弹 {a.ammo}）" if a.ammo is not None else "") + ("（卡壳）" if a.jammed else "")
                               for a in x.attacks)
            where = f"，在第 {x.pos} 段" if st.chase is not None else ""
            hp = f"HP {x.hp}/{x.hp_max}" if x.side == "party" else x.wound_word()
            state = "、".join(s for s in (x.status, "倒地" if x.prone else "", "被制住" if x.held_by else "") if s) or "正常"
            rows.append(f"- {x.id} {x.name}（{side}·{who}{ai}）{hp}，{state}{where}；攻击：{attacks or '徒手'}；闪避 {x.dodge}，格斗 {x.fighting}，敏捷 {x.dex}")
            note = " ".join(t for t in (x.notes, next((n.goal for n in m.npcs if n.id == x.id), "")) if t).strip()
            if note:
                rows.append(f"  资料：{note[:160]}")
        declared = (st.fight or st.chase).orders  # type: ignore[union-attr]
        if declared:
            names = {x.id: x.name for x in people}
            rows.append("【调查员这一轮要做的】" + "；".join(f"{names.get(k, k)}：{battle.order_text(o, names)}" for k, o in declared.items()))
        return "\n".join(rows)

    async def _tactics_round(self, ids: list[str]) -> dict[str, Any]:
        """Orders for those nobody declared one for this round: the tactics agent's, the rules of
        thumb's where it gave none or failed. A human at the table who did not act just defends."""
        st = self.state
        fight, ch = st.fight, st.chase
        out: dict[str, Any] = {"orders": {}, "say": {}, "pursue": []}
        people = fight.fighters if fight is not None else ch.runners if ch is not None else []
        humans = [i for i in ids if i in st.human]
        asked = [i for i in ids if i not in humans]
        for i in humans:
            me = next(f for f in people if f.id == i)
            out["orders"][i] = Order(do="hold" if fight is not None else "run", stance=me.stance).model_dump()
        ask = getattr(self.cast, "tactics", None)
        if asked and ask is not None:
            try:
                plan = await asyncio.wait_for(ask(st, self._battle_brief(), asked), 75)
                for mv in plan.moves:
                    if mv.who in asked and mv.who not in out["orders"]:
                        do = "flee" if mv.do == "surrender" and fight is None else mv.do
                        out["orders"][mv.who] = Order(do=do if do != "surrender" else "hold", target=mv.target, weapon=mv.weapon,
                                                      maneuver=mv.maneuver, stance=mv.stance).model_dump()
                        if mv.do == "surrender":
                            out["orders"][mv.who]["text"] = "投降"
                        if mv.say.strip():
                            out["say"][mv.who] = mv.say.strip()
                out["pursue"] = [p for p in plan.pursue if any(f.id == p for f in people)]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._debug("tactics", {"phase": "fallback", "error": f"{type(exc).__name__}: {exc}"[:300]})
                out["pursue"] = None
        for i in asked:
            if i not in out["orders"]:
                me = next(f for f in people if f.id == i)
                if fight is not None:
                    out["orders"][i] = battle.rule_of_thumb(fight, me).model_dump()
                else:
                    out["orders"][i] = Order(do="run").model_dump()
        if fight is not None and not out["pursue"]:
            out["pursue"] = battle.pursuers(fight)
        return out

    # -- what an event scripts ------------------------------------------------------------------
    async def _scripted(self, ev: Event, turn: Turn) -> None:
        """An event's attack or pursuit (Event.fight, Event.chase), where the investigators are: it
        begins as a ruling begins one, with the same events for the screens, and the next input is
        the first round's order. An attack while a fight goes on joins it. Nothing begins during a
        chase, with no investigator left who can act, or by those dead or gone. Its stages run inside
        the event's own (Game._fire), who comes decided in the first of them: a turn resumed after it
        does not begin it again."""
        from ..agents.cast import Action, Ruling

        step = self._resolution_step
        if ev.fight:
            begun = await step("fight-begin", turn, lambda: self._ambush(ev))
            for i, out in enumerate(begun):
                await step(f"fight-begin:{i}", turn, lambda out=out: self.emit(out))
            if begun:
                await step("fight-told", turn, lambda: turn.fight.append(begun[0]["line"]))
                seen = await step("fight-sight", turn, lambda: self._sights(turn, paid=bool(ev.sanity)))
                for i, out in enumerate(seen):
                    await step(f"fight-sight:{i}", turn, lambda out=out: self.emit(out))
            return
        who = await step("chase-who", turn, lambda: self._coming(ev.chase, fight_ok=False))
        if who:
            ruling = Ruling(needs_roll=False, chase=who, chase_party=ev.chase_party or "逃", reason=ev.title)
            await self._chase_open(ruling, Action(kind="other", summary=ev.title), turn, paid=bool(ev.sanity))

    def _coming(self, ids: list[str], *, fight_ok: bool) -> list[str]:
        """Of those an event sends at the investigators, who can come: not dead, not gone, and only
        while some investigator can still act and no chase (nor, for a chase, a fight) goes on. The
        NPCs among them come to where the investigators are, in the record too."""
        st = self.state
        if st.chase is not None or (st.fight is not None and not fight_ok) or not any(i.can_act for i in st.investigators):
            return []
        npcs = {n.id: n for n in self.m.npcs}
        who = [x for x in dict.fromkeys(ids) if (x in npcs and npcs[x].status not in ("死亡", "失踪"))
               or (any(c.id == x for c in self.m.creatures) and not self._dead(x))]
        for fid in who:
            ent = self.world.rec.entities.get(fid) if self.world is not None and fid in npcs else None
            if ent is not None and ent.at != st.location:
                self.world.apply(Move(id=f"scripted:{st.turn}:{fid}:{st.location}", actor=ENGINE, entity=fid, to=st.location))
        return who

    def _ambush(self, ev: Event) -> list[dict]:
        """The fight an event begins, or its attackers joining the one going on (Event.fight)."""
        from ..agents.cast import Action, Ruling

        st = self.state
        who = self._coming(ev.fight, fight_ok=True)
        if not who:
            return []
        if st.fight is None:
            ruling = Ruling(needs_roll=False, combat=who, surprise=ev.surprise, reason=ev.title)
            return self._fight_begin(ruling, Action(kind="other", summary=ev.title))
        fresh = [f for f in self._others(who) if st.fight.get(f.id) is None]
        if not fresh:
            return []
        for f in fresh:
            f.surprised = ev.surprise == "foe"
        st.fight.fighters += fresh
        line = f"【战斗】{'、'.join(f.name for f in fresh)}也扑了上来"
        st.add("event", line)
        return [{"type": "fight_state", "fight": battle.view(st.fight), "lines": [line], "line": line}]

    # -- a fight ------------------------------------------------------------------------------
    def _others(self, ids: list[str]) -> list[Fighter]:
        """The module's NPCs and creatures named, as they fight (the dead left out)."""
        m = self.m
        out: list[Fighter] = []
        for fid in dict.fromkeys(ids):
            npc = next((n for n in m.npcs if n.id == fid), None)
            creature = next((c for c in m.creatures if c.id == fid), None)
            if npc is not None and npc.status != "死亡":
                out.append(battle.from_npc(npc, self._carried_by(fid), world=self.world))
            elif creature is not None and not self._dead(fid):
                out.append(battle.from_creature(creature, portrait=self._creature_face(creature.id), world=self.world))
        self._seed([f.id for f in out])
        return out

    def _dead(self, ident: str) -> bool:
        """Killed in an earlier fight: the world keeps the body (Entity.life), creatures' too."""
        from .encounter.profiles import combatant

        ent = self.world.rec.entities.get(combatant(self.world, ident)) if self.world is not None else None
        return ent is not None and ((ent.life is not None and "死亡" in ent.life.conditions) or ent.status == "死亡")

    def _seed(self, ids: list[str]) -> None:
        """NPCs and creatures entering a fight: the world keeps their bodies from now on (a creature
        becomes a world entity, where the investigators are)."""
        if self.world is None or not ids:
            return
        done = effects.commit(self.world, [], op_id=f"seed:{self.state.turn}:{'+'.join(sorted(ids))}", source="fight", seed=ids)
        if not done.ok:
            raise RuntimeError("；".join(done.problems))

    async def _fight_open(self, ruling: Ruling, action: Action, turn: Turn) -> bool:
        """A fight begins: who is in it, a creature's first sight, what starting it achieves. False
        when there is nobody there to fight after all."""
        st, step = self.state, self._resolution_step
        begun = await step("fight-begin", turn, lambda: self._fight_begin(ruling, action))
        for i, ev in enumerate(begun):
            await step(f"fight-begin:{i}", turn, lambda ev=ev: self.emit(ev))
        if st.fight is None:
            return False
        seen = await step("fight-sight", turn, lambda: self._sights(turn))
        for i, ev in enumerate(seen):
            await step(f"fight-sight:{i}", turn, lambda ev=ev: self.emit(ev))
        if ruling.set_flags:  # what starting it achieves (attacked someone)
            await step("fight-flags", turn, lambda: self._flags(ruling.set_flags, turn))
        return True

    def _fight_begin(self, ruling: Ruling, action: Action) -> list[dict]:
        """Who is in it: every investigator here who can still act, and those the ruling names
        (or the one attacked). Surprise as ruled."""
        st = self.state
        foes = self._others(ruling.combat or ([action.npc] if action.npc else []))
        if not foes:
            return []
        party = [battle.from_investigator(inv, self._carried_by(inv.id), world=self.world) for inv in st.investigators if inv.can_act]
        for f in party if ruling.surprise == "party" else foes if ruling.surprise == "foe" else []:
            f.surprised = True
        st.fight = Fight(fighters=[*party, *foes], place=st.location, reason=ruling.reason[:120], started=st.turn)
        names = "、".join(f.name for f in foes)
        line = f"【战斗开始】{st.investigator.name}一方对上{names}" + (
            "（调查员被偷袭）" if ruling.surprise == "party" else "（对方毫无防备）" if ruling.surprise == "foe" else "")
        st.add("event", line)
        return [{"type": "fight_start", "fight": battle.view(st.fight), "line": line, "surprise": ruling.surprise}]

    def _carried_by(self, who: str) -> list[str]:
        """What the record has someone hold: their weapons among it."""
        if self.world is None:
            return []
        return [e.name for e in self.world.rec.entities.values() if e.kind == "item" and e.at == who]

    def _creature_face(self, cid: str) -> str:
        """The picture key of a creature's face: the module's, else one painted by program (scenery)."""
        key = f"creature-{cid}"
        return key if key in self.m.pictures else ""

    def _sights(self, turn: Turn, *, paid: bool = False) -> list[dict]:
        """A creature seen for the first time in a fight or a chase costs every investigator in it
        sanity. `paid`: the event that brought it on had its own sanity roll for that sight (the horde
        coming out of the dark): it is shown, not paid for twice."""
        from .game import _sanity_view

        st = self.state
        out: list[dict] = []
        people = st.fight.fighters if st.fight is not None else st.chase.runners if st.chase is not None else []
        for c in [f for f in people if f.side != "party"]:
            if c.kind != "creature" or not c.sanity or c.id in st.seen or "/" not in c.sanity:
                continue
            st.seen.append(c.id)
            out.append({"type": "reveal", "id": c.id, "name": c.name, "portrait": c.portrait, "look": c.look[:120]})  # shown, then the cost
            if paid:
                continue
            ok, bad = c.sanity.split("/", 1)
            for x in [f for f in people if f.side == "party" and f.standing]:
                inv = st.find(x.id)
                s = self.rules.sanity(inv, ok.strip(), bad.strip())
                turn.sanity.append((inv.name, s))
                st.add("sanity", s.describe(), who=inv.name)
                battle.sync(x, inv)
                out.append({"type": "sanity", "who": inv.name, **_sanity_view(s), **self._madness(inv, s, turn)})
        return out

    async def _fight_turn(self, action: Action, ruling: Ruling, turn: Turn) -> None:
        st, step = self.state, self._resolution_step
        if st.fight is None and not await self._fight_open(ruling, action, turn):
            turn.notes.append("没有可以动手的对象")
            return
        fight = st.fight
        if fight is None:
            return
        me = st.investigator.id
        order = Order.model_validate(action.order) if action.order else (
            Order(do="attack", target=action.npc or next((f.id for f in fight.standing("foe")), "")) if action.kind == "attack"
            else Order(do="hold"))

        def declare() -> None:
            if fight.get(me) is not None:
                fight.orders[me] = order

        await step("fight-declare", turn, declare)
        if self.holding:  # a LAN round: the others in the window declare too, and the last settles it
            turn.declared = True
            return
        await self._fight_round(turn, ruling)

    async def _fight_round(self, turn: Turn, ruling: Ruling | None) -> None:
        st, step = self.state, self._resolution_step
        fight = st.fight
        assert fight is not None
        pending = [f.id for f in fight.fighters if f.standing and f.id not in fight.orders]
        plan = await step("fight-tactics", turn, lambda: self._tactics_round(pending))

        def begin() -> list[str]:
            fight.round += 1
            fight.engaged = []
            for f in fight.fighters:
                f.responded = 0
            for fid, o in plan["orders"].items():
                if fid not in fight.orders:
                    fight.orders[fid] = Order.model_validate(o)
            for f in fight.fighters:  # how each answers a blow this round, before anyone strikes
                if (o := fight.orders.get(f.id)) is not None:
                    f.stance = self._stance(f, o, fight)
            firing = {fid for fid, o in fight.orders.items() if o.do == "shoot"}  # only they get a readied gun's +50 (p. 95)
            sequence = rules.order([f for f in fight.fighters if f.standing], firing)
            turn.fight.append(f"【战斗 · 第 {fight.round} 轮】先后：" + " → ".join(fight.get(i).name for i in sequence))  # type: ignore[union-attr]
            return sequence

        sequence = await step("fight-round", turn, begin)
        await step("fight-round-notice", turn, lambda: self.emit({"type": "fight_round", "round": fight.round, "order": sequence,
                                                                   "fight": battle.view(fight)}))
        for fid, said in plan["say"].items():
            if (who := fight.get(fid)) is not None and who.standing:
                def shout(who=who, said=said) -> dict:
                    st.add("npc", said, who=who.name)
                    turn.fight.append(f"{who.name}喊：「{said}」")
                    return {"type": "fight_say", "who": who.id, "name": who.name, "text": said}

                ev = await step(f"fight-say:{fid}", turn, shout)
                await step(f"fight-say:{fid}:notice", turn, lambda ev=ev: self.emit(ev))
        for i, fid in enumerate(sequence):
            out = await step(f"fight-act:{i}", turn, lambda fid=fid: self._fight_act(fid, turn, ruling))
            for k, ev in enumerate(out["events"]):
                await step(f"fight-act:{i}:{k}", turn, lambda ev=ev: self.emit(ev))
            if out.get("flags"):
                await step(f"fight-act:{i}:flags", turn, lambda flags=out["flags"]: self._flags(flags, turn))
            if battle.outcome(fight):
                break
        tail = await step("fight-round-end", turn, lambda: self._fight_round_end(turn))
        for k, ev in enumerate(tail):
            await step(f"fight-round-end:{k}", turn, lambda ev=ev: self.emit(ev))
        if end := battle.outcome(fight):
            await self._fight_end(turn, end, plan.get("pursue") or [])

    def _fight_act(self, fid: str, turn: Turn, ruling: Ruling | None) -> dict:
        """One of them acts: what they do and what it does. Synchronous: one stage, its rolls kept. It
        works on a copy of the fight and lands what it did to bodies and things in one world op
        (_landed); only then is the copy the fight, so an act cut short keeps none of itself."""
        st = self.state
        assert st.fight is not None
        work = st.fight.model_copy(deep=True)
        out = self._landed(self._act_key(f"fight:{work.started}:r{work.round}:{fid}"), work.fighters,
                           lambda: self._fight_act_on(work, fid, ruling))
        _merge(st.fight, work)
        turn.fight += out.pop("told", [])
        return out

    def _fight_act_on(self, fight: Fight, fid: str, ruling: Ruling | None) -> dict:
        who = fight.get(fid)
        out: dict = {"events": [], "flags": []}
        if who is None or not who.standing:
            return out
        dice = self.rules.dice
        lines: list[str] = []
        if who.lost_action:
            who.lost_action = False
            if who.prone:
                who.prone = False
                lines.append(f"{who.name}挣扎着从地上爬起来")
            else:
                lines.append(f"{who.name}还伏在掩体后面，这一轮没能出手")
            return self._told(out, lines, fight)
        order = fight.orders.get(fid) or battle.rule_of_thumb(fight, who)
        who.stance = self._stance(who, order, fight)
        if who.held_by:
            holder = fight.get(who.held_by)
            if holder is None or not holder.standing:
                who.held_by = ""
            else:
                mine, theirs = rules.check(dice, "力量", who.str_), rules.check(dice, "力量", holder.str_)
                if mine.level > theirs.level or (mine.level == theirs.level and mine.level.ok and who.str_ > holder.str_):
                    who.held_by = ""
                    lines.append(f"{who.name}拼命挣脱了{holder.name}（力量 {mine.value} 对 {theirs.value}）")
                else:
                    lines.append(f"{who.name}被{holder.name}死死制住，没能挣脱（力量 {mine.value} 对 {theirs.value}）")
                return self._told(out, lines, fight)
        if order.do == "flee":
            who.status = "逃走"
            lines.append(f"{who.name}转身就逃")
        elif order.text == "投降":
            who.status = "投降"
            lines.append(f"{who.name}丢下手里的东西，举手求饶")
        elif order.do == "hold":
            lines.append(f"{who.name}没有出手，盯着对方，被攻击时{STANCES[who.stance]}")
        elif order.do == "reload":
            gun = next((a for a in who.attacks if a.firearm and a.ammo is not None and a.ammo < a.load), None)
            if gun is not None:
                gun.ammo = gun.load
                lines.append(f"{who.name}给{gun.name}重新装满了子弹")
            else:
                lines.append(f"{who.name}摸了摸枪，子弹是满的")
        elif order.do == "unjam":
            gun = next((a for a in who.attacks if a.jammed), None)
            if gun is not None:
                gun.jammed = False
                lines.append(f"{who.name}拍开卡住的{gun.name}，枪又能打了")
        elif order.do == "aid":
            self._aid(who, fight.get(order.target), lines, out)
        elif order.do in ATTACKING:
            self._attack(who, order, fight, lines, out)
        elif order.do == "other":
            self._other(who, order, ruling, fight, lines, out)
        return self._told(out, lines, fight)

    @staticmethod
    def _stance(f: Fighter, order: Order, fight: Fight) -> str:
        """How they answer a blow: as ordered, but diving for cover only when someone has a gun."""
        if order.stance == "cover" and not any(a.firearm for x in fight.foes_of(f) for a in x.attacks):
            return "dodge"
        return order.stance

    def _told(self, out: dict, lines: list[str], fight: Fight) -> dict:
        """What an act came to, for the keeper (out["told"], added to the turn once it has landed)
        and for the screens."""
        out.setdefault("told", []).extend(lines)
        out["events"].append({"type": "fight_state", "fight": battle.view(fight), "lines": lines})
        return out

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

    def _aid(self, who: Fighter, target: Fighter | None, lines: list[str], out: dict) -> None:
        st = self.state
        if target is None or target.status in ("死亡", "逃走"):
            lines.append(f"{who.name}想去救人，可已经来不及了")
            return
        if who.kind == "investigator":
            roll = self.rules.check(st.find(who.id), "急救")
            out["events"].append({"type": "dice", "who": who.name, **self._dice(roll)})
        else:
            roll = rules.check(self.rules.dice, "急救", who.value("急救"))
        if not roll.passed:
            lines.append(f"{who.name}给{target.name}急救，手忙脚乱没能止住血（急救 {roll.value} {roll.level.zh}）")
            return
        if self._fx is not None and self._known(target):
            body = self._body(target)  # First Aid as rules/wounds.first_aid has it: the dying stabilized, else +1 and awake
            last = body.injuries[-1] if body.injuries else ""
            if body.dying:
                self._effect(target, effects.Stabilize(target=target.id))
                if body.hp < 1:
                    self._effect(target, effects.Heal(target=target.id, amount=1, means="first_aid", injury=last))
            else:
                self._effect(target, effects.Heal(target=target.id, amount=1, means="first_aid", injury=last))
                if wounds.UNCONSCIOUS in body.conditions:
                    self._effect(target, effects.Wake(target=target.id))
        else:
            target.hp = min(target.hp_max, target.hp + 1)
            if target.status == "濒死":
                target.status = "昏迷"
        lines.append(f"{who.name}给{target.name}急救，暂时稳住了伤势（急救 {roll.value} {roll.level.zh}）")

    def _attack(self, who: Fighter, order: Order, fight: Fight, lines: list[str], out: dict) -> None:
        foes = fight.foes_of(who)
        target = fight.get(order.target)
        if target is None or not target.standing or target.side == who.side:
            target = min(foes, key=lambda f: (f.hp / max(1, f.hp_max), -f.dex)) if foes else None
        if target is None:
            lines.append(f"{who.name}环顾四周，已经没有能动手的对象")
            return
        atk = self._weapon(who, order)
        dice = self.rules.dice
        if atk.firearm and order.do != "maneuver":
            into = any(target.id in pair and not any(x.side != target.side and x.id in pair and x.id != who.id
                                                    for x in [who]) and any(f.id in pair for f in fight.side(who.side) if f.id != who.id)
                       for pair in fight.engaged)
            # Guns halved is the body's (engine/effects.wound), not the shot's: the rolled damage lands whole.
            halved, target.guns_halved = target.guns_halved, target.guns_halved and not self._known(target)
            try:
                ex = rules.shoot(dice, who, atk, target, into_melee=into)
            finally:
                target.guns_halved = halved
        else:
            if atk.firearm:  # a gun used as a club in a manoeuvre: the bare hands' chance
                atk = rules.attack_of(rules.UNARMED, who.fighting)
            ex = rules.melee(dice, who, atk, target, maneuver=order.maneuver if order.do == "maneuver" else "")
            fight.engaged.append([who.id, target.id])
        self._blow(ex, who, target, atk, fight, lines, out)

    def _other(self, who: Fighter, order: Order, ruling: Ruling | None, fight: Fight, lines: list[str], out: dict) -> None:
        """Something else than fighting, as the arbiter ruled it: only the turn's own investigator has
        a ruling; anyone else's other is a moment spent."""
        st = self.state
        if who.id != st.investigator.id or ruling is None:
            lines.append(f"{who.name}{order.text or '忙着别的事'}")
            return
        words = order.text or "做了别的事"
        if not (ruling.needs_roll and ruling.skill):
            lines.append(f"{who.name}：{words}")
            out["flags"] += ruling.set_flags
            return
        inv = st.find(who.id)
        roll = self.rules.check(inv, ruling.skill, DIFFICULTY_ZH.get(ruling.difficulty, Level.REGULAR), ruling.bonus)
        out["events"].append({"type": "dice", "who": inv.name, **self._dice(roll)})
        line = f"{who.name}：{words}（{roll.describe()}）"
        if roll.passed:
            out["flags"] += ruling.set_flags
            target = fight.get(ruling.harm_to)
            if ruling.harm and target is not None and target.standing:
                try:  # a model's words never reach BCDice as written (rules/dice_expr)
                    expr = DamageExpr.parse(ruling.harm)
                except ValueError as exc:
                    line += f"；伤害式无法使用：{exc}"
                else:
                    amount = expr.total(self.rules.dice, who.db)
                    notes = self._hurt(target, amount, unarmed=False, out=out, means="other")
                    line += f"，{target.name}受到 {expr.text()} = {amount} 点伤害" + (f"，{'、'.join(notes)}" if notes else "")
        lines.append(line)

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

    def _blow(self, ex: rules.Exchange, who: Fighter, target: Fighter, atk: Attack, fight: Fight, lines: list[str],
                   out: dict) -> None:
        """An exchange landed: its words, its wounds and effects, its event for the screens."""
        st = self.state
        verb = "射击" if ex.kind == "shot" else ex.effect or ("" if ex.kind == "melee" else ex.attack)
        head = f"{who.name}用{atk.name}{'射击' if ex.kind == 'shot' else ('施展' + ex.attack) if ex.kind == 'maneuver' else '攻击'}{target.name}"
        parts: list[str] = []
        if ex.roll is not None:
            bonus = f"（{'、'.join(ex.reasons)}）" if ex.reasons else ""
            parts.append(f"{ex.roll.name} {ex.roll.target} → {ex.roll.value} {ex.roll.level.zh}{bonus}")
        if ex.answer is not None:
            how = {"dodge": "闪避", "fight": "反击", "cover": "扑向掩体"}[ex.reaction]
            parts.append(f"{target.name}{how}：{ex.answer.value} {ex.answer.level.zh}")
        notes: list[str] = []
        if ex.jammed:
            result = f"{atk.name}卡壳了"
        elif ex.empty:
            result = f"{atk.name}没有子弹了"
        elif ex.roll is None:
            result = "；".join(ex.reasons) or "做不到"
        elif ex.winner == "none":
            result = "被躲开了" if ex.reaction == "dodge" and ex.answer and ex.answer.passed else "没有得手"
        elif ex.effect:
            if ex.effect == "缴械":
                held = target.wielding
                item = battle.item_for(self.world, target, held) if self.world is not None and self._known(target) else None
                if held.unarmed or (self.world is not None and self._known(target) and item is None):
                    result = f"{target.name}手里本来就没有武器" if held.unarmed else f"{target.name}的{held.name}长在身上，夺不走"
                else:
                    target.attacks = [a for a in target.attacks if a is not held]
                    target.weapon = battle.readiest(target)
                    if item is not None and self._fx is not None:  # the item itself falls where they fight
                        self._fx.append(effects.Drop(item=item, to=st.location))
                    result = f"打掉了{target.name}手里的{held.name}"
            elif ex.effect == "推倒":
                target.prone, target.lost_action = True, True
                result = f"把{target.name}摔倒在地"
            else:
                target.held_by = who.id
                result = f"死死制住了{target.name}"
        else:
            hurt = fight.get(ex.hurt)
            assert hurt is not None
            notes = self._hurt(hurt, ex.damage, unarmed=atk.unarmed and ex.hurt == target.id, out=out,
                               means="firearm" if ex.kind == "shot" else "blow")
            by = "反击得手，" if ex.hurt == who.id else ""
            result = f"{by}{hurt.name}受到 {ex.damage_text}" + (f"，{'、'.join(n for n in notes if n)}" if notes else "")
        line = f"{head}：" + "；".join(parts) + (f"。{result}" if parts else result)
        lines.append(line)
        if self._fx is not None:
            self._after.append(lambda: st.add("dice", line, who=who.name))
        else:
            st.add("dice", line, who=who.name)
        mine = ex.roll if who.kind == "investigator" else ex.answer if target.kind == "investigator" else None
        out["events"].append({
            "type": "fight_act", "round": fight.round, "attacker": who.id, "defender": target.id, "attacker_name": who.name,
            "defender_name": target.name, "attacker_side": who.side, "defender_side": target.side,
            "hurt_side": (fight.get(ex.hurt).side if ex.hurt and fight.get(ex.hurt) else ""),  # type: ignore[union-attr]
            "kind": ex.kind, "attack": atk.name, "verb": verb,
            "roll": self._dice(ex.roll) if ex.roll is not None else None,
            "answer": self._dice(ex.answer) if ex.answer is not None else None, "reaction": ex.reaction, "winner": ex.winner,
            "hurt": ex.hurt, "damage": ex.damage, "damage_text": ex.damage_text, "effect": ex.effect, "extreme": ex.extreme,
            "jammed": ex.jammed, "empty": ex.empty, "notes": notes, "line": line,
            "dice": self._dice(mine) if mine is not None else None,
            "dice_who": who.name if mine is ex.roll else target.name})

    def _fight_round_end(self, turn: Turn) -> list[dict]:
        """The dying roll CON, what heals itself heals, surprise is over, the round's orders go. On a
        copy, landed in one world op, as an act is (_landed)."""
        st = self.state
        assert st.fight is not None
        work = st.fight.model_copy(deep=True)
        lines: list[str] = []
        events: list[dict] = []

        def end() -> dict:
            for f in work.fighters:
                if f.kind == "investigator":
                    inv = st.find(f.id)
                    body = wounds.Body.of(inv)
                    if body.dying and not body.stabilized:  # First Aid stabilized them: no CON every round
                        roll = self.rules.check(inv, "体质", mark=False)
                        if not roll.passed:
                            self._die(f, f"濒死时体质检定失败（{roll.value}）")
                            lines.append(f"{inv.name}没能撑过去（体质 {roll.value}）：死了")
                            self._after.append(lambda inv=inv: st.add("damage", "伤重不治", who=inv.name))
                            events.append({"type": "damage", "who": inv.name, "text": "伤重不治", "fighter": f.id})
                        else:
                            lines.append(f"{inv.name}还吊着一口气（体质 {roll.value}）")
                elif f.status == "濒死" and not (self._known(f) and self._body(f).stabilized):
                    roll = rules.check(self.rules.dice, "体质", f.con)
                    if not roll.passed:
                        self._die(f, f"濒死时体质检定失败（{roll.value}）")
                    lines.append(f"{f.name}" + ("断了气" if f.status == "死亡" else "还在抽搐"))
                if f.regen and f.status != "死亡" and f.status not in rules.OUT and f.hp < f.hp_max:
                    was_down = f.status == "倒下"
                    if self._effect(f, effects.Heal(target=f.id, amount=f.regen, means="regen")) is None:
                        rules.heal_round(f)
                    if was_down and f.hp > 0:
                        lines.append(f"{f.name}身上的伤口蠕动着合拢，它又站了起来")
                if work.round >= 1:
                    f.surprised = False
            work.orders = {}
            return {}

        self._landed(self._act_key(f"fight:{work.started}:r{work.round}:end"), work.fighters, end)
        _merge(st.fight, work)
        turn.fight += lines
        events.append({"type": "fight_state", "fight": battle.view(work), "lines": lines})
        return events

    def _die(self, f: Fighter, why: str) -> None:
        if self._effect(f, effects.Die(target=f.id, why=why)) is None:
            f.status, f.hp = "死亡", 0

    async def _fight_end(self, turn: Turn, end: str, pursue: list[str]) -> None:
        st, step = self.state, self._resolution_step
        fight = st.fight
        assert fight is not None
        text = battle.summary(fight, end)

        def record() -> None:
            # Who was hurt or killed is already the world's: every wound, death and dropped weapon
            # landed with its act (engine/effects), creatures' too. What remains is the account.
            npcs = {n.id for n in self.m.npcs}
            people = [f.id for f in fight.fighters if f.kind != "creature" and f.id in (npcs | {i.id for i in st.investigators})]
            self.world.apply(Occur(id=f"fight:{fight.started}:end", actor=ENGINE, participants=people, place=st.location,
                                   text=text[:300], visibility="public"))
            st.minutes += max(1, math.ceil(fight.round / 6))
            turn.fight.append(f"【战斗结束】{text}")
            st.add("event", f"战斗结束：{text}")

        await step("fight-record", turn, record)
        flags = await step("fight-outcome", turn, lambda: self._outcome(text))
        if flags:
            await step("fight-outcome-flags", turn, lambda: self._flags(flags, turn))
        chasers = [p for p in pursue if (f := fight.get(p)) is not None and f.standing] if end == "fled" else []

        def close() -> dict:
            party = [f.id for f in fight.side("party") if f.status == "逃走"]
            st.fight = None
            if chasers:
                self._chase_from_fight(fight, party, chasers)
            return {"type": "fight_end", "end": end, "text": text}

        ev = await step("fight-close", turn, close)
        await step("fight-close-notice", turn, lambda: self.emit(ev))
        if st.chase is not None:
            await step("chase-from-fight-notice", turn, lambda: self.emit({"type": "chase_start", "chase": battle.chase_view(st.chase)}))  # type: ignore[arg-type]

    async def _outcome(self, result: str) -> list[str]:
        """The module's flags the end of it brings about (Cast.outcome); none when it cannot be asked."""
        ask = getattr(self.cast, "outcome", None)
        if ask is None:
            return []
        try:
            found = await asyncio.wait_for(ask(self.state, result), 90)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._debug("outcome", {"phase": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]})
            return []
        return list(found.flags)

    # -- a chase --------------------------------------------------------------------------------
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

    def _chase_from_fight(self, fight: Fight, party: list[str], chasers: list[str]) -> None:
        """The investigators ran from a fight and something goes after them: a chase on the module's
        way from here, else one for this kind of place (scenery's), the fighters as they were."""
        from .. import scenery

        st = self.state
        loc = self.m.location(st.location)
        runners = [f.model_copy(update={"status": ""}) for f in fight.fighters if f.id in party]
        runners += [f.model_copy() for f in fight.fighters if f.id in chasers]
        track = self._chase_way() or battle.default_track(scenery.kind_of(loc.name, loc.read_aloud))
        self._chase_setup(runners, track, "party", f"逃离{loc.name}的战斗", rolled=False)

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
        """The way the chase runs along: the module's for this place (Location.chase), else drawn by
        the tactics agent for this place and hour, else the one for this kind of place."""
        from .. import scenery

        st, m = self.state, self.m
        if (route := self._chase_way()) is not None:
            return route.model_dump()
        loc = m.location(st.location)
        ask = getattr(self.cast, "track", None)
        if ask is not None:
            brief = (f"出发的地方：{loc.name}。{loc.read_aloud.strip()[:200]}\n时间：{st.clock}；天气：{st.sky or '如常'}\n"
                     f"相邻的地方：{'、'.join(m.location(e).name for e in loc.exits)}")
            try:
                track = await asyncio.wait_for(ask(st, brief), 90)
                if 4 <= len(track.legs) <= 12:
                    return track.model_dump()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._debug("track", {"phase": "fallback", "error": f"{type(exc).__name__}: {exc}"[:300]})
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
        seen = await step("chase-sight", turn, lambda: self._sights(turn, paid=paid))
        for i, ev in enumerate(seen):
            await step(f"chase-sight:{i}", turn, lambda ev=ev: self.emit(ev))
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
        if st.chase is None:
            opened = await self._chase_open(ruling, action, turn)
            if opened == "none":
                turn.notes.append("没有可以追或者躲开的对象")
            if opened != "on":
                return
        ch = st.chase
        assert ch is not None
        me = st.investigator.id
        order = Order.model_validate(action.order) if action.order else Order(do="run")

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
        plan = await step("chase-tactics", turn, lambda: self._tactics_round(pending))

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
        for i, fid in enumerate(sequence):
            out = await step(f"chase-act:{i}", turn, lambda fid=fid: self._chase_act(fid, turn))
            for k, ev in enumerate(out["events"]):
                await step(f"chase-act:{i}:{k}", turn, lambda ev=ev: self.emit(ev))
            if out.get("caught") or out.get("turned"):
                break
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
        """Over: the quarry got away, or was caught and it comes to blows."""
        st, step = self.state, self._resolution_step
        ch = st.chase
        assert ch is not None
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
            self.world.apply(Occur(id=f"chase:{ch.started}:end", actor=ENGINE, participants=people, place=st.location,
                                   text=text[:300], visibility="public"))

        await step("chase-record", turn, record)
        flags = await step("chase-outcome", turn, lambda: self._outcome(text))
        if flags:
            await step("chase-outcome-flags", turn, lambda: self._flags(flags, turn))

        def close() -> list[dict]:
            runners = {f.id: f for f in ch.runners}
            hunter = runners.get(fight_ids[0]) if end == "caught" and fight_ids else None
            out = [{"type": "chase_end", "end": end, "text": text, "hunter": hunter.id if hunter else "",
                    "hunter_name": hunter.name if hunter else "", "portrait": hunter.portrait if hunter else ""}]
            st.chase = None
            if end == "caught" and fight_ids:
                hunter, prey = runners[fight_ids[0]], runners[fight_ids[1]]
                sides = [f for f in runners.values() if f.standing and f.id not in ch.hidden
                         and ((f.side == hunter.side and abs(f.pos - hunter.pos) <= 1) or (f.side == prey.side and abs(f.pos - prey.pos) <= 1))]
                for f in sides:
                    f.responded, f.actions, f.pos = 0, 0, 0
                st.fight = Fight(fighters=sides, place=st.location, reason="追逐中被追上", started=st.turn)
                line = "【战斗开始】" + "、".join(f.name for f in sides if f.side == "party") + "与" + "、".join(
                    f.name for f in sides if f.side != "party") + "扭打在一起"
                st.add("event", line)
                turn.fight.append(line)
                out.append({"type": "fight_start", "fight": battle.view(st.fight), "line": line, "surprise": ""})
            return out

        for i, ev in enumerate(await step("chase-close", turn, close)):
            await step(f"chase-close:{i}", turn, lambda ev=ev: self.emit(ev))


__all__ = ["CHASE_TELL", "FIGHT_TELL", "Fighting", "Leg"]
