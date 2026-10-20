"""How exactly a replayed turn did what the engine and the rules asked, counted without a judge
(eval/session.py). A prompt that reads better but makes the engine send more back loses.

    scene     the keeper's scene: held the first time, sent back once, or failing twice settled
              by the program (Game._tell), and why it was sent back (world/scene.plan's words)
    npc       replies the NPC was told to write again (Cast._dialogue_problems), replies whose acts
              still did not hold (Cast.unsettled), and why
    fields    an NPC that says it now goes along, hands something over or walks off, without the
              field that makes it so (accompany, items, leave); the keeper's scene putting a person
              in company, gone or handing a thing while that person's reply had no such field; what
              it was offered to play on meeting the investigators, and whether it marked it played
    arbiter   repairs of its JSON; clue ids it gave that the engine could not use (Game._valid_clues)
    clues     a turn whose check failed, telling what the failed check would have found; the text of
              a clue behind a check that nobody has found, in what was told (runs of five 字)

Watch is put on a turn's cast and game before it plays (session.replay) and read after it.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from ..agents import guard
from ..module.schema import Module
from ..world import context as world_view

# What an NPC's words say it does now. Narrow on purpose: a cue counted is one a reader would agree
# with, and a reply it misses is no worse off in one variant than in another.
_NOT = r"(?<![不没别甭难])(?<!不能)(?<!不会)(?<!没法)(?<!不想)(?<!不便)"
CUES: dict[str, re.Pattern[str]] = {
    "accompany": re.compile(_NOT + r"(?:(?:我|咱们|咱|我们)(?:陪你|跟你|和你|带你|领你)(?:一起|一块儿?)?(?:去|过去|走)"
                            r"|一起(?:去|走|过去)吧|跟我来|我带你(?:去|过去)|我领你(?:去|过去))"),
    "items": re.compile(_NOT + r"(?:给你[。！，吧]|拿着吧|拿去吧|你拿着|借给你|借你用|这个给你|这给你)"
                        r"|[（(][^）)]*(?:递给你|递过来|递到你|塞到你|塞给你|交到你手|放到你手|扔给你)[^）)]*[）)]"),
    "leave": re.compile(r"[（(][^）)]*(?:转身(?:离开|走了|出门|走开|进了|回了)|起身离开|快步离开|走开了|离开了|出了门|回屋去了|走出门)[^）)]*[）)]"
                        r"|" + _NOT + r"(?:我先走了|我得走了|我先回去了|失陪了|告辞了)"),
}

# Why a scene was sent back, by the words world/scene.plan uses (the first that matches).
SCENE_REASONS = [
    ("格式", ("格式不对",)),
    ("地点写错", ("不是地点", "不能替他们走到", "不在同一处")),
    ("漏写在场的人", ("scene 里没有他",)),
    ("来人赶不到", ("赶不到", "从那里到不了")),
    ("去向不对", ("不是能离开去的地点", "去不了", "到不了")),
    ("死者或失踪者", ("已经死了", "下落不明")),
    ("here 与 left 冲突", ("同时写在 here 和 left",)),
    ("follow 不在 here", ("写在 follow 里",)),
    ("物件没登记", ("不是登记过的物件",)),
    ("物件去处不对", ("不是人也不是地点",)),
    ("递不过去或拿不出", ("递不过去", "拿不出", "不是在场的人")),
    ("痕迹放错", ("痕迹留不在",)),
]

NPC_REASONS = [
    ("欺骗说明", ("有意欺骗", "欺骗说明", "假答应同行", "欺骗依据", "真实意图或动机", "新增原回复没有声明的欺骗")),
    ("不实声称当成事实", ("不实声称",)),
    ("同行", ("同行目的地", "同行结论", "同行")),
    ("changes 不成立", ("不能宣称已经完成",)),
    ("当场动作", ("当场的动作", "递", "拿出", "离开", "走开", "去处", "不在")),
]


def _takes(fn: Any, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def reason(problem: str, table: list[tuple[str, tuple[str, ...]]]) -> str:
    return next((name for name, keys in table if any(k in problem for k in keys)), "其他")


def cues(text: str) -> list[str]:
    """What the words say the speaker does now: accompany, items, leave."""
    return [field for field, pattern in CUES.items() if pattern.search(text)]


def _npc_of(m: Module, text: str) -> str | None:
    """An NPC written in a scene: its id, its name, or a part of its name (托马斯 for 托马斯·金博尔)."""
    text = text.strip()
    for n in m.npcs:
        if text in (n.id, n.name) or (len(text) >= 2 and (text in n.name or n.name.split("·")[0] in text)):
            return n.id
    return None


class Watch:
    """What the engine's own checks said while one turn played: put on before, read after."""

    def __init__(self) -> None:
        self.tellings: list[dict[str, Any]] = []  # each keeper call with a scene: retry?, held?, the scene
        self.settled = 0  # tellings written to a scene the program put together
        self.replies: list[dict[str, Any]] = []  # each NPC reply: its checks (problems per try), fields, offers
        self.unsettled: list[dict[str, Any]] = []
        self.clues: dict[str, str] = {}  # clue id the arbiter gave -> "" when usable, else why not

    def attach(self, cast: Any, game: Any) -> None:
        """Wrap what the cast and the game have of these; a test's stand-in may lack some."""
        keeper, npc = cast.keeper, getattr(cast, "npc", None)
        problems, valid = getattr(cast, "_dialogue_problems", None), getattr(game, "_valid_clues", None)
        watch = self

        async def watched_keeper(state, report, on_delta=None, lore="", *, check=None, fix=""):
            if check is None:
                if "【场面已经定了】" in lore:
                    watch.settled += 1
                return await keeper(state, report, on_delta=on_delta, lore=lore)
            verdicts: list[bool] = []

            def checked(raw: Any) -> bool:
                verdicts.append(ok := check(raw))
                return ok

            told = await keeper(state, report, on_delta=on_delta, lore=lore, check=checked, fix=fix)
            scene = getattr(told, "scene", None)
            watch.tellings.append({"retry": bool(fix), "held": verdicts[-1] if verdicts else None,
                                   "scene": scene, "shown": bool(getattr(told, "shown", False))})
            return told

        async def watched_npc(state, npc_id, message, *, travel=False, private=False):
            offered = []
            if cast.world is not None:
                brief = world_view.npc_brief(cast.world, npc_id, query=message)
                offered = re.findall(r"（([^（）]+)）", brief.split("【见到调查员时，你会】", 1)[1].split("\n", 1)[0]) \
                    if "【见到调查员时，你会】" in brief else []
            watch.replies.append({"npc": npc_id, "checks": [], "offered": offered, "fields": {}, "text": ""})
            return await npc(state, npc_id, message, travel=travel, private=private)

        def watched_problems(state, npc_id, dialogue, text, destinations, *, private):
            found = problems(state, npc_id, dialogue, text, destinations, private=private)
            reply = next((r for r in reversed(watch.replies) if r["npc"] == npc_id), None)
            if reply is not None:
                reply["checks"].append(found)  # the list itself: a problem added after the check still shows
                reply["text"] = text
                reply["fields"] = {"accompany": dialogue.accompany, "items": [i.name for i in dialogue.items],
                                   "leave": dialogue.leave, "played": list(dialogue.played),
                                   "feigned": [d.accompany for d in dialogue.deceptions if d.accompany]}
            return found

        def watched_valid(ids, action):
            got = valid(ids, action)
            st, m = game.state, game.m
            known = {c.id for c in m.clues}
            for cid in ids:
                if cid in got:
                    watch.clues[cid] = ""
                elif watch.clues.get(cid, "x"):
                    watch.clues[cid] = ("不存在" if cid not in known else "早已找到" if cid in st.clues
                                        else "不在这里或条件未到")
            return got

        class Unsettled(dict):
            def __setitem__(self, key, value):
                watch.unsettled.append({"npc": key, "problems": list(value)})
                super().__setitem__(key, value)

        if _takes(keeper, "check"):  # a keeper that writes scenes; one that does not must not be offered one
            cast.keeper = watched_keeper
        if npc is not None and problems is not None:
            cast.npc, cast._dialogue_problems = watched_npc, watched_problems
        if isinstance(getattr(cast, "unsettled", None), dict):
            cast.unsettled = Unsettled(cast.unsettled)
        if valid is not None:
            game._valid_clues = watched_valid

    @staticmethod
    def outcome(telling: dict[str, Any] | None) -> str:
        """held; sent back; shown while it held and then not (kept, partly); no scene to read; "" none."""
        if telling is None:
            return ""
        if telling["scene"] is None or telling["held"] is None:
            return "no_scene"
        if telling["held"]:
            return "held"
        return "kept_partial" if telling["shown"] else "sent_back"

    def result(self, m: Module, journal: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """The turn's counts. `journal`: the game's "scene" journal rows of this turn (why scenes went back)."""
        first = self.outcome(next((t for t in self.tellings if not t["retry"]), None))
        second = self.outcome(next((t for t in self.tellings if t["retry"]), None))
        rejected = [p for row in journal or [] if row.get("data", {}).get("phase") in ("rejected", "kept")
                    for p in row["data"].get("problems", [])]
        out: dict[str, Any] = {
            "scene": {
                "first": first,  # "" when the turn told nothing with a scene (a question to the KP)
                "second": second,
                "settled": self.settled,  # failed twice: the program put the scene together
                "reasons": [reason(p, SCENE_REASONS) for p in rejected],
                "problems": rejected,
            },
        }
        replies = []
        for r in self.replies:
            fields = r["fields"]
            said = cues(r["text"])
            missing = [f for f in said if not fields.get(f) and not (f == "accompany" and fields.get("feigned"))]
            replies.append({"npc": r["npc"], "tries": len(r["checks"]), "reasks": max(0, len(r["checks"]) - 1),
                            "failed_checks": [p for c in r["checks"] for p in c], "said": said, "missing": missing,
                            "offered": r["offered"], "played": fields.get("played", []), "fields": fields})
        out["npc"] = {
            "replies": len(replies),
            "reasks": sum(r["reasks"] for r in replies),
            "unsettled": len(self.unsettled),
            "reasons": [reason(p, NPC_REASONS) for r in replies for p in r["failed_checks"]]
                       + [reason(p, NPC_REASONS) for u in self.unsettled for p in u["problems"]],
            "detail": replies,
        }
        out["fields"] = self._fields(m, replies)
        out["arbiter"] = {"invalid_clues": {c: why for c, why in self.clues.items() if why and why != "早已找到"},
                          "found_again": [c for c, why in self.clues.items() if why == "早已找到"]}
        return out

    def _fields(self, m: Module, replies: list[dict[str, Any]]) -> dict[str, Any]:
        """Said without the field, and the keeper's scene against the replies' fields."""
        said_missing = [f"{r['npc']}:{f}" for r in replies for f in r["missing"]]
        spoke = {r["npc"]: r["fields"] for r in replies}
        scene = next((t["scene"] for t in reversed(self.tellings) if isinstance(t.get("scene"), dict)), None) or {}
        crossed = []
        for who in scene.get("follow", []) or []:
            n = _npc_of(m, str(who))
            if n in spoke and not spoke[n].get("accompany") and not spoke[n].get("feigned"):
                crossed.append(f"{n}:follow")
        for gone in scene.get("left", []) or []:
            n = _npc_of(m, str(gone.get("who", "")) if isinstance(gone, dict) else "")
            if n in spoke and not spoke[n].get("leave"):
                crossed.append(f"{n}:left")
        for thing in scene.get("things", []) or []:
            n = _npc_of(m, str(thing.get("holder", "")) if isinstance(thing, dict) else "")
            if n in spoke and not spoke[n].get("items"):
                crossed.append(f"{n}:things")
        offered = [r for r in replies if r["offered"]]
        return {"said_missing": said_missing, "scene_vs_reply": crossed,
                "meetings_offered": len(offered), "meetings_marked": sum(bool(r["played"]) for r in offered)}


# -- what the clues say, in what was told -------------------------------------------------------
def clue_slips(m: Module, found: list[str], told: str, public: str, *, missed: list[str]) -> dict[str, list[str]]:
    """`missed`: the clues this turn's failed check would have given. Returns the ones whose text the
    telling says again (five-字 runs, two or more, not already public), and every clue behind a check
    or an NPC's gate that nobody has found whose text it says ("early")."""
    gated = {}
    for c in m.clues:
        if c.id in found:
            continue
        gate = m.npc(c.npc).gates.get(c.id) if c.npc and any(n.id == c.npc for n in m.npcs) else None
        if c.check is not None or gate is not None or c.id in missed:
            gated[c.id] = c.text
    slips = guard.leaks(told, gated, public=public) if told.strip() and gated else []
    return {"missed_told": [c for c in slips if c in missed], "early_told": slips}


def failed_check(log: list[Any], turn: int) -> bool:
    """A check this turn ended failed: a dice line that failed, and no later one this turn that turned it."""
    lines = [e.text for e in log if e.turn == turn and e.kind == "dice"]
    return bool(lines) and "失败" in lines[0] and not any(("成功" in x and "✓" in x) or "改为成功" in x for x in lines[1:])


# -- what the narration does, by rule ------------------------------------------------------------
_SECRET = re.compile(r"悄悄|偷偷|暗中|暗地|私下|小声|低声|压低声音|不让[^，。]{0,8}(听|知道|发现|看见)|不被[^，。]{0,8}(听|发现|看见)|"
                     r"(听|看)不(到|见)的地方|假装|瞒着")
_LOUD = re.compile(r"扬声|大声|高声|喊了|喊道|喊一声|喊了一声|喊一句|喊了一句|嚷|吆喝|当着[^，。]{0,6}的面|朝[^，。]{0,10}喊")
_HURT = re.compile(r"你[^。！？\n]{0,12}(受了伤|受伤|流血|出血|划破|割破|擦破|扭伤|扭了|骨折|撞伤|咬伤|抓伤|昏了过去|晕了过去|昏过去|晕过去|疼得)")
_NOBODY = re.compile(r"没人应|没有人应|无人应|没人回应|没有人回应|没有回应|屋里没人|屋里没有人|屋里空无一人|空无一人|没有人在|一个人也没有|没人在家")
_ACT = re.compile(r"说|道|问|答|点头|摇头|开口|走|站|坐|看|望|笑|递|拿|转身|跟|推|拉|招手|回|进|出来|应声|喊")
_AWAY = re.compile(r"不在|没有|没在|没见|没来|不见|未见|想起|记得|说过|提过|提到|曾经|信里|信上|据说|听说|也许|大概|可能|如果|要是|"
                   r"回忆|那天|昨天|之前|以前|当时|去找|找到|等|约")
_SENT = re.compile(r"[^。！？\n]+[。！？]?")


def secret_spoken(said: str, keeper: str) -> list[str]:
    """The player meant it quietly (悄悄, 假装, where he cannot hear), and the telling has it called out."""
    if not _SECRET.search(said):
        return []
    return [s.strip() for s in _SENT.findall(keeper) if _LOUD.search(s)]


def unhurt(keeper: str, log: list[Any], turn: int) -> list[str]:
    """The telling hurts the investigator, and nothing this turn did (no damage, no sanity loss)."""
    if any(e.turn == turn and e.kind in ("damage", "sanity") for e in log):
        return []
    return [m.group(0) for m in _HURT.finditer(keeper)]


def presence_rules(m: Module, st: Any, keeper: str) -> dict[str, list[str]]:
    """Two contradictions a rule can see: nobody answers while someone the record has in this
    building is there ("nobody"), and someone the record has far off acting in a sentence that
    neither remembers nor misses them ("absent")."""
    near = world_view.rooms(m, st.location) | {st.location}
    people = {e.id: e for e in st.record.entities.values() if e.kind == "npc" and e.status not in ("死亡", "失踪")}
    inside = [e.name for e in people.values() if e.at in near]
    nobody = [s.strip() for s in _SENT.findall(keeper) if _NOBODY.search(s)] if inside else []
    absent = []
    for s in _SENT.findall(keeper):
        if _AWAY.search(s) or not _ACT.search(s):
            continue
        for e in people.values():
            short = e.name.split("·")[0]
            if e.at not in near and e.at and any(len(x) >= 2 and x in s for x in (e.name, short)):
                absent.append(f"{e.name}（在{next((loc.name for loc in m.locations if loc.id == e.at), e.at)}）：{s.strip()[:60]}")
    return {"nobody": nobody, "absent": absent}
