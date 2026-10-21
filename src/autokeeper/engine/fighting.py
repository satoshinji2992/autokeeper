"""Fights and chases in the game: a round at a time, in the turn's journaled stages.

How one comes about: the arbiter rules that an action starts it (Ruling.combat, Ruling.chase), or a
fight ends with the investigators running and something going after them. From then on the game is
in it (GameState.fight, GameState.chase) until it is over, and every player input is that
investigator's order for the round (engine/combat.Order: the screen offers them as words, and any
words work, read by the engine or by Cast.order).

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
from typing import TYPE_CHECKING, Any

from ..rules import chase as chase_rules
from ..rules import combat as rules
from ..rules.bcdice import Level
from ..rules.catalog import canonical
from ..rules.chase import Leg, Obstacle, Track
from ..rules.coc import DIFFICULTY_ZH
from ..rules.combat import STANCES, Attack, Fighter
from ..world.ops import Condition, Occur
from ..world.record import ENGINE
from . import combat as battle
from .combat import Chase, Fight, Order

if TYPE_CHECKING:
    from ..agents.cast import Action, Ruling
    from .game import Turn

ATTACKING = ("attack", "shoot", "maneuver")
FIGHT_TELL = ("下面是这一轮按先后结算出来的经过。照它写一段紧凑、有画面的叙述：谁先动手、打中了谁、伤成什么样，混乱和恐惧可以写，节奏要快。"
              "检定和伤害的数字已经显示给玩家，不要复述数字；只写结算出来的结果，不新添伤口、倒地、死亡或别的动作，"
              "不替调查员决定下一轮做什么。120–220 字。")
CHASE_TELL = ("下面是这一轮追逐按先后结算出来的经过。照它写一段急促的叙述：脚步、喘息、身后的动静、绊倒和翻越，"
              "前面还有什么路。数字已经显示过，不复述；只写结算出来的结果，不替调查员决定下一步。100–200 字。")


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
        if st.chase is not None or (st.fight is None and ruling.chase):
            await self._chase_turn(action, ruling, turn)
        else:
            await self._fight_turn(action, ruling, turn)
        if not turn.declared:
            await step("time", turn, lambda: self._time(turn))
            await step("ending", turn, lambda: self._check_ending(turn))

    def battling(self) -> bool:
        return self.state.fight is not None or self.state.chase is not None

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
        """The attack an order names, else the one in hand."""
        if order.weapon:
            for a in f.attacks:
                if a.name == order.weapon or order.weapon in a.name or a.name in order.weapon:
                    return a
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

    # -- a fight ------------------------------------------------------------------------------
    def _fight_begin(self, ruling: Ruling, action: Action) -> list[dict]:
        """Who is in it: every investigator here who can still act, and those the ruling names
        (or the one attacked). Surprise as ruled."""
        st, m = self.state, self.m
        ids = list(dict.fromkeys(ruling.combat or ([action.npc] if action.npc else [])))
        foes: list[Fighter] = []
        for fid in ids:
            npc = next((n for n in m.npcs if n.id == fid), None)
            creature = next((c for c in m.creatures if c.id == fid), None)
            if npc is not None and npc.status != "死亡":
                foes.append(battle.from_npc(npc, self._carried_by(fid)))
            elif creature is not None:
                foes.append(battle.from_creature(creature, portrait=self._creature_face(creature.id)))
        if not foes:
            return []
        party = [battle.from_investigator(inv, self._carried_by(inv.id)) for inv in st.investigators if inv.can_act]
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

    def _sights(self, turn: Turn) -> list[dict]:
        """A creature seen for the first time in a fight or a chase costs every investigator in it sanity."""
        from .game import _sanity_view

        st = self.state
        out: list[dict] = []
        people = st.fight.fighters if st.fight is not None else st.chase.runners if st.chase is not None else []
        for c in [f for f in people if f.side != "party"]:
            if c.kind != "creature" or not c.sanity or c.id in st.seen or "/" not in c.sanity:
                continue
            st.seen.append(c.id)
            ok, bad = c.sanity.split("/", 1)
            for x in [f for f in people if f.side == "party" and f.standing]:
                inv = st.find(x.id)
                s = self.rules.sanity(inv, ok.strip(), bad.strip())
                turn.sanity.append((inv.name, s))
                st.add("sanity", s.describe(), who=inv.name)
                battle.sync(x, inv)
                out.append({"type": "sanity", "who": inv.name, **_sanity_view(s)})
        return out

    async def _fight_turn(self, action: Action, ruling: Ruling, turn: Turn) -> None:
        st, step = self.state, self._resolution_step
        if st.fight is None:
            begun = await step("fight-begin", turn, lambda: self._fight_begin(ruling, action))
            for i, ev in enumerate(begun):
                await step(f"fight-begin:{i}", turn, lambda ev=ev: self.emit(ev))
            if st.fight is None:  # nobody there to fight after all
                turn.notes.append("没有可以动手的对象")
                return
            seen = await step("fight-sight", turn, lambda: self._sights(turn))
            for i, ev in enumerate(seen):
                await step(f"fight-sight:{i}", turn, lambda ev=ev: self.emit(ev))
            if ruling.set_flags:  # what starting it achieves (attacked someone)
                await step("fight-flags", turn, lambda: self._flags(ruling.set_flags, turn))
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
            turn.fight.append(f"【战斗 · 第 {fight.round} 轮】先后：" + " → ".join(
                fight.get(i).name for i in rules.order([f for f in fight.fighters if f.standing])))  # type: ignore[union-attr]
            return rules.order([f for f in fight.fighters if f.standing])

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
        """One of them acts: what they do and what it does. Synchronous: one stage, its rolls kept."""
        st = self.state
        fight = st.fight
        assert fight is not None
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
            return self._told(turn, out, lines, fight)
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
                return self._told(turn, out, lines, fight)
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
        return self._told(turn, out, lines, fight)

    @staticmethod
    def _stance(f: Fighter, order: Order, fight: Fight) -> str:
        """How they answer a blow: as ordered, but diving for cover only when someone has a gun."""
        if order.stance == "cover" and not any(a.firearm for x in fight.foes_of(f) for a in x.attacks):
            return "dodge"
        return order.stance

    def _told(self, turn: Turn, out: dict, lines: list[str], fight: Fight) -> dict:
        """What an act came to, for the keeper and for the screens."""
        turn.fight += lines
        out["events"].append({"type": "fight_state", "fight": battle.view(fight), "lines": lines})
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
        if target.kind == "investigator":
            inv = st.find(target.id)
            self.rules.heal(inv, 1)
            if "濒死" in inv.conditions:
                inv.conditions.remove("濒死")
                inv.add_condition("昏迷")
            battle.sync(target, inv)
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
            ex = rules.shoot(dice, who, atk, target, into_melee=into)
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
                amount = self.rules.dice.total(ruling.harm)
                notes = self._hurt(target, amount, unarmed=False, out=out)
                line += f"，{target.name}受到 {ruling.harm} = {amount} 点伤害" + (f"，{'、'.join(notes)}" if notes else "")
        lines.append(line)

    @staticmethod
    def _dice(check) -> dict:
        from .game import _check_view

        return _check_view(check)

    def _hurt(self, f: Fighter, amount: int, *, unarmed: bool, out: dict) -> list[str]:
        """A wound: an investigator's through the rules every damage in the game goes through (major
        wounds, unconsciousness, dying), anyone else's through rules/combat.wound."""
        st = self.state
        if f.kind == "investigator":
            inv = st.find(f.id)
            dmg = self.rules.damage(inv, amount)
            battle.sync(f, inv)
            st.add("damage", dmg.describe(), who=inv.name)
            out["events"].append({"type": "damage", "who": inv.name, "text": dmg.describe(), "fighter": f.id})
            return [dmg.describe()]
        return rules.wound(self.rules.dice, f, amount, unarmed=unarmed)

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
                if not held.unarmed:
                    target.attacks = [a for a in target.attacks if a is not held]
                    target.weapon = battle.readiest(target)
                    result = f"打掉了{target.name}手里的{held.name}"
                else:
                    result = f"{target.name}手里本来就没有武器"
            elif ex.effect == "推倒":
                target.prone, target.lost_action = True, True
                result = f"把{target.name}摔倒在地"
            else:
                target.held_by = who.id
                result = f"死死制住了{target.name}"
        else:
            hurt = fight.get(ex.hurt)
            assert hurt is not None
            notes = self._hurt(hurt, ex.damage, unarmed=atk.unarmed and ex.hurt == target.id, out=out)
            by = "反击得手，" if ex.hurt == who.id else ""
            result = f"{by}{hurt.name}受到 {ex.damage_text}" + (f"，{'、'.join(n for n in notes if n)}" if notes else "")
        line = f"{head}：" + "；".join(parts) + (f"。{result}" if parts else result)
        lines.append(line)
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
        """The dying roll CON, what heals itself heals, surprise is over, the round's orders go."""
        st = self.state
        fight = st.fight
        assert fight is not None
        lines: list[str] = []
        events: list[dict] = []
        for f in fight.fighters:
            if f.kind == "investigator":
                inv = st.find(f.id)
                if "濒死" in inv.conditions and "死亡" not in inv.conditions:
                    roll = self.rules.check(inv, "体质", mark=False)
                    if not roll.passed:
                        inv.add_condition("死亡")
                        inv.hp = 0
                        lines.append(f"{inv.name}没能撑过去（体质 {roll.value}）：死了")
                        st.add("damage", "伤重不治", who=inv.name)
                        events.append({"type": "damage", "who": inv.name, "text": "伤重不治", "fighter": f.id})
                    else:
                        lines.append(f"{inv.name}还吊着一口气（体质 {roll.value}）")
                battle.sync(f, inv)
            elif f.status == "濒死":
                roll = rules.dying(self.rules.dice, f)
                lines.append(f"{f.name}" + ("断了气" if f.status == "死亡" else "还在抽搐"))
            if rules.heal_round(f):
                lines.append(f"{f.name}身上的伤口蠕动着合拢，它又站了起来")
            if fight.round >= 1:
                f.surprised = False
        fight.orders = {}
        turn.fight += lines
        events.append({"type": "fight_state", "fight": battle.view(fight), "lines": lines})
        return events

    async def _fight_end(self, turn: Turn, end: str, pursue: list[str]) -> None:
        st, step = self.state, self._resolution_step
        fight = st.fight
        assert fight is not None
        text = battle.summary(fight, end)

        def record() -> None:
            npcs = {n.id for n in self.m.npcs}
            for f in fight.fighters:
                if f.kind != "npc" or f.id not in npcs:
                    continue
                status = {"死亡": "死亡", "濒死": "重伤", "昏迷": "受伤", "重伤": "重伤"}.get(f.status) or (
                    "受伤" if f.hp < f.hp_max else None)
                if status:
                    self.world.apply(Condition(id=f"fight:{fight.started}:{f.id}", actor=ENGINE, entity=f.id, status=status))
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
    def _chase_from_fight(self, fight: Fight, party: list[str], chasers: list[str]) -> None:
        """The investigators ran from a fight and something goes after them: a chase on a way drawn
        for this place (scenery's kind of place), the fighters as they were."""
        from .. import scenery

        st = self.state
        loc = self.m.location(st.location)
        runners = [f.model_copy(update={"status": ""}) for f in fight.fighters if f.id in party]
        runners += [f.model_copy() for f in fight.fighters if f.id in chasers]
        track = battle.default_track(scenery.kind_of(loc.name, loc.read_aloud))
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
        """The way the chase runs along: drawn by the tactics agent for this place and hour, or the
        one for this kind of place."""
        from .. import scenery

        st, m = self.state, self.m
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
        st, m = self.state, self.m
        ids = list(dict.fromkeys(ruling.chase or ([action.npc] if action.npc else [])))
        others: list[Fighter] = []
        for fid in ids:
            npc = next((n for n in m.npcs if n.id == fid), None)
            creature = next((c for c in m.creatures if c.id == fid), None)
            if npc is not None and npc.status != "死亡":
                others.append(battle.from_npc(npc, self._carried_by(fid)))
            elif creature is not None:
                others.append(battle.from_creature(creature, portrait=self._creature_face(creature.id)))
        if not others:
            return []
        party = [battle.from_investigator(inv, self._carried_by(inv.id)) for inv in st.investigators if inv.can_act]
        quarry = "foe" if ruling.chase_party == "追" else "party"
        lines = self._chase_setup([*party, *others], Track.model_validate(track), quarry, ruling.reason, rolled=False)
        ch = st.chase
        assert ch is not None
        head = f"【追逐开始】{'调查员在逃，身后是' if quarry == 'party' else '调查员追赶'}" + "、".join(f.name for f in others)
        st.add("event", head)
        return [{"type": "chase_start", "chase": battle.chase_view(ch), "line": head, "speeds": lines}]

    async def _chase_turn(self, action: Action, ruling: Ruling, turn: Turn) -> None:
        st, step = self.state, self._resolution_step
        if st.chase is None:
            track = await step("chase-track", turn, self._chase_track)
            begun = await step("chase-begin", turn, lambda: self._chase_begin(ruling, action, track))
            for i, ev in enumerate(begun):
                await step(f"chase-begin:{i}", turn, lambda ev=ev: self.emit(ev))
            if st.chase is None:
                turn.notes.append("没有可以追或者躲开的对象")
                return
            ch = st.chase
            turn.fight.append(begun[0]["line"] if begun else "【追逐开始】")
            turn.fight += begun[0].get("speeds", []) if begun else []
            seen = await step("chase-sight", turn, lambda: self._sights(turn))
            for i, ev in enumerate(seen):
                await step(f"chase-sight:{i}", turn, lambda ev=ev: self.emit(ev))
            if ruling.set_flags:
                await step("chase-flags", turn, lambda: self._flags(ruling.set_flags, turn))
            quarry = ch.running(ch.quarry)
            hunters = ch.running("foe" if ch.quarry == "party" else "party")
            if chase_rules.gone_at_once(quarry, hunters):
                await self._chase_end(turn, "escaped", "跑得比追的人快得多，转眼就没了影")
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
            return rules.order(moving)

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
        puts something in the pursuers' way, or turns to fight."""
        st = self.state
        ch = st.chase
        assert ch is not None
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
                return self._chase_told(turn, out, lines, ch)
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
                return self._chase_told(turn, out, lines, ch)
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
                roll, crossed = chase_rules.cross(dice, f, leg, value=value)
                if roll is not None and f.kind == "investigator":
                    out["events"].append({"type": "dice", "who": f.name, **self._dice(roll)})
                if crossed is not None and not crossed.passed:
                    if crossed.kind == "barrier":
                        lines.append(f"{f.name}被{ob.what}挡住了（{ob.skill} {roll.value} {roll.level.zh}）")  # type: ignore[union-attr]
                        break
                    f.actions = max(0, f.actions - crossed.lost)
                    hurt = ""
                    if crossed.harm:
                        notes = self._hurt(f, crossed.harm, unarmed=False, out=out)
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
        return self._chase_told(turn, out, lines, ch)

    def _chase_told(self, turn: Turn, out: dict, lines: list[str], ch: Chase) -> dict:
        turn.fight += lines
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
            out = [{"type": "chase_end", "end": end, "text": text}]
            runners = {f.id: f for f in ch.runners}
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
