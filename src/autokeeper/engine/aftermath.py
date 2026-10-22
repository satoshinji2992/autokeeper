"""Resolve the keeper's aftercare, stopping at actual events instead of skipping over rescue."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..agents.aftermath import Aftercare, Aftermath
from ..module.schema import PARTY_DEAD, PARTY_UNFIT
from ..rules import combat, wounds
from ..world.context import rooms
from . import effects
from .encounter import profiles
from .state import format_time, parse_time

if TYPE_CHECKING:
    from .game import Game, Turn


def place(game: Game, who: str) -> str:
    return game.state.apart.get(who, game.world.place(who))


def can_help(game: Game, who: str) -> bool:
    ent = game.world.rec.entities.get(who)
    body = effects.body_of(game.world, who)
    blocked = {"失踪", "死亡", "昏迷", "濒死", "永久疯狂"}
    return bool(ent is not None and ent.status not in blocked and not blocked.intersection(ent.conditions)
                and body is not None and body.conscious and not body.has("永久疯狂") and place(game, who))


def report(game: Game, turn: Turn) -> str:
    st = game.state
    lines = [f"现在 {st.clock}，{game.m.location(st.location).name}。"]
    for inv in st.investigators:
        lines.append(f"{inv.name}({inv.id})：{game.world.name(place(game, inv.id))}，生命 {inv.hp}/{inv.hp_max}，"
                     + "、".join(inv.conditions))
    for npc in game.m.npcs:
        body = effects.body_of(game.world, npc.id)
        profile = profiles.profile(game.world, npc.id)
        ent = game.world.rec.entities.get(npc.id)
        statuses = [npc.status, ent.status if ent else "", *(ent.conditions if ent else []), *(body.conditions if body else [])]
        status = "、".join(dict.fromkeys(x for x in statuses if x)) or "可行动"
        lines.append(f"{npc.name}({npc.id})：{game.world.name(place(game, npc.id)) or '位置未记'}，{status}，"
                     f"急救 {profile.skill('急救') if profile else 30}，医学 {profile.skill('医学') if profile else 1}；"
                     f"动机：{npc.goal}；性情：{npc.personality}；当前心境：{npc.arc}；"
                     f"私有认知：{'；'.join(npc.knows)}；秘密：{'；'.join(npc.secrets)}。")
    lines += [f"关系：{game.world.name(r.a)}对{game.world.name(r.b)}，{r.kind} {r.value}；{r.note}"
              for r in game.world.rec.relations.values()]
    lines += [f"日程 {ev.when}：{ev.title}；{ev.text}" for ev in game.m.events if ev.id not in st.fired]
    lines += [f"已安排 {format_time(ev.at)}：{ev.text}" for ev in game.world.rec.scheduled.values() if ev.status == "pending"]
    lines += turn.notes[-12:]
    return "\n".join(lines)


def problems(game: Game, plan: Aftermath) -> list[str]:
    """Only identities and rule preconditions that are independent of earlier treatment results."""
    out = []
    for s in plan.steps:
        if s.kind in ("first_aid", "medicine", "move") and not any(n.id == s.who for n in game.m.npcs):
            out.append(f"{s.who or '未填'}不是已登记的施救者。")
        if s.kind in ("first_aid", "medicine", "wake") and not any(i.id == s.target for i in game.state.investigators):
            out.append(f"{s.target or '未填'}不是调查员。")
        if s.kind == "move" and not any(p.id == s.to for p in game.m.locations):
            out.append(f"移动地点 {s.to or '未填'} 没有登记。")
        if s.kind == "move" and s.target not in ("", "players"):
            out.append("搬运目标填 players（只搬运施救者身边的人），不支持把异地调查员一并带走。")
        if s.kind == "medicine" and s.minutes < 60:
            out.append("医学治疗至少需要一小时。")
        if s.kind == "first_aid" and s.minutes:
            out.append("急救按轮结算，先赶路或等待应单列步骤。")
        if s.kind == "wait" and not s.minutes:
            out.append("等待需要写明实际经过的分钟。")
    return out


def fallback(game: Game) -> Aftermath:
    """Legacy scripted casts do not decide NPC intentions. Never invent an off-screen rescuer."""
    st = game.state
    due = []
    for inv in st.investigators:
        body = effects.body_of(game.world, inv.id)
        if body and (after := wounds.waking(body)) is not None:
            due.append((body.unconscious_at if body.unconscious_at is not None else st.minutes) + after)
    if due:
        return Aftermath(reason="没有新的幕后安排，先等到最近可能自行苏醒的时刻。",
                         steps=[Aftercare(kind="wait", minutes=max(1, min(due) - st.minutes), reason="等待自行苏醒")])
    if any((b := effects.body_of(game.world, i.id)) and b.dying and not b.stabilized for i in st.investigators):
        return Aftermath(reason="没有新的施救安排，先结算接下来一分钟；日程照常推进。",
                         steps=[Aftercare(kind="wait", minutes=1, reason="等待下一项实际变化")])
    return Aftermath(reason="没有可执行的治疗安排，也没有能够自行恢复行动的人。",
                     steps=[Aftercare(kind="finish", reason="按现有伤势中断调查，不视为死亡")])


async def arrange(game: Game, turn: Turn) -> dict:
    call = getattr(game.cast, "aftermath", None)
    if call is None:
        # With a real cast this branch is never used. A legacy extension must not silently
        # decide that the NPC next to a dying investigator refuses to help.
        if game.world.present(game.state.location) and any(
                (b := effects.body_of(game.world, i.id)) and b.dying for i in game.state.investigators):
            from .game import PreparationError
            raise PreparationError("当前主持接口缺少战后安排，尚未判断现场人物是否施救；伤势与时间已保留。")
        return fallback(game).model_dump(mode="json")
    plan = Aftermath.model_validate(await call(game.state, report(game, turn)))
    if bad := problems(game, plan):
        plan = Aftermath.model_validate(await call(game.state, report(game, turn), problems=bad))
        if bad := problems(game, plan):
            from .game import PreparationError
            raise PreparationError("战后安排尚不能落实：" + "；".join(bad))
    return plan.model_dump(mode="json")


def _active(game: Game) -> bool:
    return game.state.ending is not None or game.encounter() is not None or any(i.can_act for i in game.state.investigators)


async def notices(game: Game, turn: Turn, rows: list[dict]) -> None:
    for n, row in enumerate(rows):
        await game._resolution_step(f"notice:{n}", turn, lambda row=row: game.emit(row))


async def time_(game: Game, turn: Turn) -> None:
    """Aftercare waits on due world work, even when normal player turns defer it until narration."""
    await game._world_settled()
    old = game.world_later
    game.world_later = False
    try:
        await game._time(turn)
        # An exchange from before the party fell must not hold an already scheduled rescue.
        flags = game._play(turn, set())
        await game._flags(flags, turn, landed=True)
        game._world_owed = False
    finally:
        game.world_later = old


def _dying(game: Game, turn: Turn, key: str) -> list[dict]:
    """One round only. The caller advances time and runs events between minutes."""
    rows = []
    for inv in game.state.investigators:
        body = effects.body_of(game.world, inv.id)
        if body is None or not body.dying or body.stabilized:
            continue
        con = game.rules.check(inv, "体质", mark=False)
        text = f"濒死体质 {con.value} {con.level.zh}（{game.state.clock}:{game.state.aftercare_seconds:02d}）"
        if not con.passed:
            done = effects.commit(game.world, [effects.Die(target=inv.id, why=text)], op_id=f"{key}:{inv.id}", source="engine")
            if not done.ok:
                raise ValueError("；".join(done.problems))
            text += "，伤重不治"
        turn.notes.append(f"{inv.name}：{text}。")
        game.state.add("damage", text, who=inv.name)
        rows.append({"type": "damage", "who": inv.name, "fighter": inv.id, "text": text})
    return rows


def _hourly(game: Game, turn: Turn, key: str, since: int) -> list[dict]:
    rows = []
    for inv in game.state.investigators:
        body = effects.body_of(game.world, inv.id)
        if body is None or not body.stabilized or body.stabilized_at is None:
            continue
        if (game.state.minutes - body.stabilized_at) // 60 <= (since - body.stabilized_at) // 60:
            continue
        con = game.rules.check(inv, "体质", mark=False)
        text = f"伤势稳定后的体质 {con.value} {con.level.zh}"
        if not con.passed:
            done = effects.commit(game.world, [effects.Relapse(target=inv.id)], op_id=f"{key}:{inv.id}", source="engine")
            if not done.ok:
                raise ValueError("；".join(done.problems))
            text += "，伤势再度恶化，须立即急救"
        turn.notes.append(f"{inv.name}：{text}。")
        game.state.add("damage", text, who=inv.name)
        rows.append({"type": "damage", "who": inv.name, "fighter": inv.id, "text": text})
    return rows


def _points(game: Game, start: int, end: int) -> list[int]:
    points = {end}
    points |= {parse_time(ev.when) for ev in game.m.events if ev.id not in game.state.fired
               and re.fullmatch(r"D\d+ \d{1,2}:\d{2}", ev.when)}
    points |= {ev.at for ev in game.world.rec.scheduled.values() if ev.status == "pending"}
    points |= {t["minutes"] for t in game.state.plugin_timers if "minutes" in t}
    for a in game.world.rec.agents.values():
        points |= {s.due for s in a.plan if s.status == "pending" and s.due is not None}
    for inv in game.state.investigators:
        b = effects.body_of(game.world, inv.id)
        if b is None:
            continue
        if b.dying and not b.stabilized:
            points.update(range(start + 1, end + 1))
        if b.stabilized_at is not None:
            points.update(range(b.stabilized_at + 60, end + 1, 60))
        if (after := wounds.waking(b)) is not None:
            points.add((b.unconscious_at if b.unconscious_at is not None else start) + after)
    return sorted(p for p in points if start < p <= end)


async def round_(game: Game, turn: Turn, key: str) -> bool:
    """Six seconds and every still-unstable patient's CON; a retry keeps the whole round."""
    st, step = game.state, game._resolution_step
    def commit() -> dict:
        old = st.minutes
        total = st.aftercare_seconds + 6
        st.minutes += total // 60
        st.aftercare_seconds = total % 60
        return {"rows": _dying(game, turn, key), "minute": st.minutes != old,
                "fired": list(st.fired), "present": sorted(game.world.present(st.location))}
    saved = await step("round", turn, commit)
    await step("notices", turn, lambda: notices(game, turn, saved["rows"]))
    if saved["minute"]:
        await step("time", turn, lambda: time_(game, turn))
    return saved["fired"] != list(st.fired) or saved["present"] != sorted(game.world.present(st.location))


async def advance(game: Game, turn: Turn, minutes: int, key: str, *, treatment: bool = False) -> bool:
    """Advance to milestones; a changed scene, relapse or waking returns control to the keeper."""
    step, st = game._resolution_step, game.state
    saved = await step("interval", turn, lambda: {"start": st.minutes, "end": st.minutes + minutes,
                       "points": _points(game, st.minutes, st.minutes + minutes), "fired": list(st.fired),
                       "present": sorted(game.world.present(st.location)), "location": st.location, "flags": list(st.flags)})
    previous = saved["start"]
    for n, at in enumerate(saved["points"]):
        base = f"interval:{n}"
        count = await step(f"{base}/round-count", turn,
                           lambda at=at: max(0, ((at - st.minutes) * 60 - st.aftercare_seconds + 5) // 6))
        for r in range(count):
            round_key = f"{base}/round:{r}"
            resumed = game._resolution_key(round_key) in (game._resolution or {}).get("steps", {})
            if not resumed and not any((b := effects.body_of(game.world, i.id)) and b.dying and not b.stabilized for i in st.investigators):
                break
            changed = await step(round_key, turn, lambda r=r, base=base: round_(game, turn, f"{key}:{base}:{r}"))
            if changed or _active(game) or all("死亡" in i.conditions for i in st.investigators):
                return False
        await step(f"{base}/clock", turn, lambda at=at: setattr(st, "minutes", max(st.minutes, at)))
        await step(f"{base}/world", turn, lambda: time_(game, turn))
        await step(f"{base}/wake", turn, lambda: game._wake_due(turn, since=saved["start"]))
        if not (treatment and at == saved["end"]):
            rows = await step(f"{base}/hourly", turn, lambda base=base, previous=previous: _hourly(game, turn, f"{key}:{base}", previous))
            await step(f"{base}/hourly/notices", turn, lambda rows=rows: notices(game, turn, rows))
        def note() -> None:
            names = "、".join(i.name for i in st.investigators if "死亡" not in i.conditions)
            turn.notes.append(f"【昏迷】{names}失去了知觉，从 {format_time(saved['start'])} 到 {st.clock}；期间按实际日程推进。")
        await step(f"{base}/note", turn, note)
        if _active(game) or any((b := effects.body_of(game.world, i.id)) and b.dying and not b.stabilized for i in st.investigators):
            return False
        if saved["fired"] != list(st.fired) or saved["present"] != sorted(game.world.present(st.location)) or saved["location"] != st.location:
            return False
        if "flags" in saved and saved["flags"] != list(st.flags):
            return False
        previous = at
    return True


def _care(game: Game, turn: Turn, s: Aftercare, key: str) -> dict:
    body = effects.body_of(game.world, s.target)
    if body is None or body.dead or not can_help(game, s.who) or place(game, s.who) != place(game, s.target):
        return {"problem": "施救者不能行动、伤者已死亡，或双方不在一处，治疗未实施。"}
    skill = "急救" if s.kind == "first_aid" else "医学"
    profile = profiles.profile(game.world, s.who)
    assert profile is not None
    if s.kind == "first_aid":
        within = body.unconscious_at is None or game.state.minutes - body.unconscious_at <= 60
        if not (ok := wounds.first_aid_allowed(body, within_hour=within))[0]:
            return {"problem": ok[1]}
    elif body.dying and not body.stabilized:
        return {"problem": "伤者再度濒死，须先急救稳定，医学尚不能实施。"}
    hurt_at = body.unconscious_at if body.unconscious_at is not None else game.state.minutes
    difficulty = wounds.medicine_difficulty(game.state.minutes // 1440 == hurt_at // 1440)
    roll = combat.check(game.rules.dice, skill, profile.skill(skill), difficulty if s.kind == "medicine" else combat.Level.REGULAR)
    injury = wounds._last_injury(body, wounds.MEDICINE_KEY if s.kind == "medicine" else "")
    applied = []
    if s.kind == "first_aid":
        care = wounds.first_aid(body, roll.passed, minutes=game.state.minutes)
        if care.stabilized:
            applied.append(effects.Stabilize(target=s.target))
        elif not body.dying:
            applied.append(effects.Heal(target=s.target, amount=int(roll.passed), means="first_aid", injury=injury))
            if care.woke:
                applied.append(effects.Wake(target=s.target))
    elif roll.passed:
        applied += [effects.Heal(target=s.target, amount=game.rules.dice.total(wounds.MEDICINE), means="medicine", injury=injury),
                    effects.Wake(target=s.target)]
    if applied:
        done = effects.commit(game.world, applied, op_id=key, source="engine")
        if not done.ok:
            raise ValueError("；".join(done.problems))
    text = f"{game.world.name(s.who)}给{game.world.name(s.target)}{skill}（{roll.value} {roll.level.zh}）"
    turn.notes.append(text + "；" + s.reason)
    game.state.add("damage", text, who=game.world.name(s.target))
    return {"passed": roll.passed, "notice": {"type": "damage", "who": game.world.name(s.target), "text": text}}


async def perform(game: Game, turn: Turn, s: Aftercare, key: str) -> bool:
    """False means changed circumstances: arrange again, without inventing the failed action."""
    st, step = game.state, game._resolution_step
    if s.kind in ("first_aid", "medicine", "move"):
        def ready() -> str:
            if not can_help(game, s.who):
                return "安排实施者不能行动。"
            if s.kind in ("first_aid", "medicine") and place(game, s.who) != place(game, s.target):
                return "施救者与伤者不在一处，先安排移动。"
            if s.kind == "move":
                src = place(game, s.who)
                if s.target == "players" and src != st.location:
                    return "搬运者不在调查员身边，先安排赶来。"
                travel = 0 if s.to in rooms(game.m, src) else game._travel(src, s.to) if src else None
                if travel is None:
                    return "搬运者与目的地之间没有已登记路线。"
                if s.minutes < travel:
                    return f"到{game.world.name(s.to)}至少需要{travel}分钟，不能在{ s.minutes}分钟内抵达。"
            return ""
        if problem := await step("ready", turn, ready):
            await step("unready", turn, lambda: turn.notes.append("【未落实】" + problem))
            return False
    if s.minutes and not await step("elapsed", turn, lambda: advance(game, turn, s.minutes, key, treatment=s.kind == "medicine")):
        return False
    if s.kind in ("first_aid", "medicine"):
        for attempt in range(1000):  # tool-work guard, never a fictional death or a forced success
            row = await step(f"care:{attempt}", turn, lambda attempt=attempt: _care(game, turn, s, f"{key}:{attempt}"))
            if problem := row.get("problem"):
                await step(f"problem:{attempt}", turn, lambda: turn.notes.append("【未落实】" + problem))
                return False
            await step(f"care:{attempt}/notice", turn, lambda row=row: game.emit(row["notice"]))
            if s.kind == "medicine":
                if not row["passed"]:
                    rows = await step("medicine-hourly", turn, lambda: _hourly(game, turn, key + ":hourly", st.minutes - s.minutes))
                    await step("medicine-hourly/notices", turn, lambda rows=rows: notices(game, turn, rows))
                return True
            changed = await step(f"care:{attempt}/round", turn,
                                 lambda attempt=attempt: round_(game, turn, f"{key}:{attempt}:dying"))
            if changed or all("死亡" in i.conditions for i in st.investigators):
                return False
            if row["passed"] or not s.repeat:
                return True
            if (attempt + 1) % 10 == 0:
                return False  # a minute of failed aid: consider another method or helper
        return False
    if s.kind == "move":
        def move() -> dict:
            if not can_help(game, s.who):
                return {"problem": "搬运者已不能行动，移动未实施。"}
            if s.target == "players" and place(game, s.who) != st.location:
                return {"problem": "搬运者不在调查员身边，未将异地的人一并带走。"}
            applied = [effects.Relocate(who=s.who, place=s.to)]
            if s.target == "players":
                applied.append(effects.Party(place=s.to))
            done = effects.commit(game.world, applied, op_id=key, source="engine")
            if not done.ok:
                return {"problem": "；".join(done.problems)}
            turn.notes.append(f"{game.world.name(s.who)}抵达{game.world.name(s.to)}；{s.reason}")
            return {"arrive": s.target == "players"}
        row = await step("move", turn, move)
        if row.get("problem"):
            await step("problem", turn, lambda: turn.notes.append("【未落实】" + row["problem"]))
            return False
        if row["arrive"]:
            await step("arrive", turn, lambda: game._arrive(s.to, turn))
        return True
    if s.kind == "wake":
        def wake() -> None:
            body = effects.body_of(game.world, s.target)
            if body and not body.dying and not body.dead and body.hp > 0:
                effects.commit(game.world, [effects.Wake(target=s.target)], op_id=key, source="engine")
                turn.notes.append(f"{game.world.name(s.target)}醒了过来；{s.reason}")
        await step("wake", turn, wake)
    if s.kind == "finish":
        await step("reason", turn, lambda: turn.notes.append("【调查中断】" + s.reason))
        await step("ending", turn, lambda: game._end(PARTY_UNFIT.id, turn))
    return True


async def settle(game: Game, turn: Turn) -> None:
    st, step = game.state, game._resolution_step
    # A notice interrupted after treatment must still be delivered even though someone is
    # now awake. Only a fresh aftercare stage may return before replaying its completed work.
    scope = game._resolution_key("aftercare:")
    resuming = any(k.startswith(scope) for k in (game._resolution or {}).get("steps", {}))
    if st.ending:
        await game._check_ending(turn)
        return
    if game.encounter() is not None or (any(i.can_act for i in st.investigators) and not resuming):
        return
    await step("aftercare-sync", turn, lambda: time_(game, turn))
    if not resuming and _active(game):
        return
    journal = game._resolution
    cursor_key = game._resolution_key("aftercare-cursor")
    cursor = journal.setdefault("steps", {}).setdefault(cursor_key, {"phase": 0}) if journal is not None else {"phase": 0}
    start = cursor["phase"]
    for phase in range(start, start + 32):
        started = game._resolution_key(f"aftercare:{phase}/plan") in (game._resolution or {}).get("steps", {})
        if all("死亡" in i.conditions for i in st.investigators) and not started:
            await step("dead-ending", turn, lambda: game._check_ending(turn))
            if not st.ending:
                def flush() -> None:
                    if st.aftercare_seconds:
                        st.minutes += 1
                        st.aftercare_seconds = 0
                await step("dead-clock", turn, flush)
                await step("dead", turn, lambda: game._end(PARTY_DEAD.id, turn))
            return
        key = f"aftercare:{phase}"
        plan = Aftermath.model_validate(await step(f"{key}/plan", turn, lambda: arrange(game, turn)))
        await step(f"{key}/reason", turn, lambda plan=plan: turn.notes.append("【战后安排】" + plan.reason))
        for n, s in enumerate(plan.steps):
            landed = await step(f"{key}/step:{n}", turn,
                                lambda s=s, n=n, phase=phase: perform(game, turn, s, f"aftercare:{st.turn}:{phase}:{n}"))
            if _active(game):
                return
            if not landed or all("死亡" in i.conditions for i in st.investigators):
                break
        cursor["phase"] = phase + 1
        game._resolution_save(turn)
    from .game import PreparationError
    raise PreparationError("战后安排尚未完成，伤势、时间与已掷结果已保留；继续时从当前安排恢复。")
