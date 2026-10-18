"""First-time hints (新手提示) and the first game.

The first time something happens in a game (the opening, a roll, a roll that is not made
again, the second chance after a failed one, a clue, a handout, someone talking, a sanity
loss, HP running low, the keeper improvising, a walk somewhere, a few turns going nowhere),
one dim line in the story says how it works and which key or guide page tells more. Each hint once per player and at
most one a turn, after the narration, where the player looks before typing: never on top
of a choice that is being made (a notification there covered the push dialog's buttons on
an 80x24 terminal), never in the way of the story.

Hints talk about how the game works, never about the module: nothing here names a place,
person or clue the player has not come across. 《追书人》 is the tutorial: a player with no
saves is pointed to it, and its own hints use only what its hook and opening have told.

What has been shown, and the switch in the guide, live in saves/tips.json, next to the
player's saves and cards: a player who clears the saves starts over as a new player, and
tests, which point config.SAVES at a temp folder, start clean. `tips = false` in the config
file turns hints off until the switch in the guide says otherwise.
"""

from __future__ import annotations

import json
import weakref
from dataclasses import dataclass, field

from rich.text import Text
from textual.widgets import OptionList, Static

from .. import config
from ..module.schema import Module
from ..rules.coc import Investigator

FILE = "tips.json"
TUTORIAL = "zhuishuren"  # 《追书人》, in modules/: a new player's first game


@dataclass(frozen=True)
class Tip:
    text: str
    page: str  # the guide page it points to (guide.PAGES)


TIPS: dict[str, Tip] = {
    "start": Tip(
        "轮到你了。想做什么，就在下面打一句中文，回车发出去，比如“我先看看屋里的摆设”“我问她昨晚听见了什么”。"
        "不懂规则，按F1看新手指南。",
        "play",
    ),
    "offer": Tip(
        "检定没过时那三个选择怎么挑：线索要紧、幸运也够，就花幸运；孤注一掷再失败，后果更重；拿不准就放弃，故事不会因此卡住。"
        "详见F1「孤注一掷与幸运」。",
        "push",
    ),
    "hurt": Tip("HP只剩三分之一了。再挨一下可能就倒下，HP到0会昏迷，伤得太重会送命。能躲就躲，F7看人物卡。", "screen"),
    "sanity": Tip(
        "理智（SAN）掉了。看见不该看的东西就会掉，一次掉5点以上可能当场发疯，掉到0就是永久疯狂。详见F1「理智与疯狂」。",
        "sanity",
    ),
    "handout": Tip("手稿能反复看：按F2打开线索本，最下面那排按钮就是你拿到的手稿。", "clues"),
    "clue": Tip("找到的线索都记在线索本里，按F2随时翻看。线索之间怎么串起来，没人会替你说破。", "clues"),
    "check": Tip(
        "结果卡这样看：掷出的数越小越好，常规、困难、极难三条线，打勾的就是过了；↳后面是这一掷带来的东西。"
        "掷出来就算数，不能重掷。详见F1「检定与骰子」。",
        "dice",
    ),
    "repeat": Tip("同一件事刚试过，情况又没变，就不会重掷，结果照旧。换个办法、换个人问，或者找到新的线索再来。", "dice"),
    "waived": Tip("用 .ra 点名的检定是向守秘人申请：结果一眼就明白，或者这个技能在这里用不上，他就直接说结果，不用掷。", "dice"),
    "npc": Tip(
        "跟人说话就像平时聊天，问什么、怎么问，直接说。每个人只知道自己那一份，有人会撒谎，有人藏着话；"
        "想让人松口，可能要过说服、话术、魅惑或恐吓。",
        "play",
    ),
    "improv": Tip(
        "不必照着剧本走：说得通的想法，故事会顺着往下编；太出格的举动也会招来后果。详见F1「守秘人会即兴」。",
        "improv",
    ),
    "move": Tip("换地方要花游戏里的时间，最上面一行有钟。按F5看地图，去过的、听说过的地方都画在上面。", "screen"),
    "stuck": Tip("没头绪的时候按F8，调查员会照眼下的情况想三件能做的事，挑一件填进输入框，改不改、发不发由你。", "play"),
    # 《追书人》: only what the hook and the opening say (Thomas, his missing uncle, the stolen
    # books, the study, and the places Thomas offers to point out). Nothing found later.
    f"{TUTORIAL}:outside": Tip(
        "想出门查，就说去哪，比如“我去街上找邻居聊聊”“我去城里的图书馆看看”。托马斯提过的墓地、警局和报社也一样，说了就会走过去。",
        "play",
    ),
}
OPENINGS = {  # a module's own words for "start"
    TUTORIAL: "轮到你了。想做什么，就在下面打一句中文，回车发出去，比如“我问托马斯，他叔叔最后一次出门是什么时候”"
    "“我去书房看看少了哪些书”。托马斯说的邻居、墓地、图书馆、警局、报社都能去查。不懂规则，按F1看新手指南。",
}
HOUSE = {"jinboer_jia", "shufang"}  # 《追书人》: the Kimballs' house and the uncle's study
OUTSIDE_AFTER = 4  # turns spent in the house before the nudge out of the door
STUCK_AFTER = 4  # turns without a clue, an event or a new place before F8 is mentioned
# When several wait, the more urgent and the rarer go first; the rest keep for a later turn,
# except those about this very turn, which no later one would make sense of.
NOW = {"start", "offer"}
ORDER = ("start", "offer", "hurt", "sanity", "handout", "clue", "check", "repeat", "waived", "npc", f"{TUTORIAL}:outside", "stuck", "improv", "move")


class TipLine(Static):
    """A hint in the story: dim, set off by a rule on its left. Never saved."""

    DEFAULT_CSS = """
    TipLine {
        color: $text-muted;
        border-left: outer $secondary 50%;
        padding: 0 1;
    }
    """


@dataclass
class _Board:
    """One game screen's hints: those waiting for the end of a turn, the turn the last one
    was shown in, and the guide page it pointed to (F1 opens there)."""

    waiting: list[str] = field(default_factory=list)
    shown_at: int = -1
    page: str | None = None


_boards: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


# -- the file -------------------------------------------------------------------------------
def _read() -> dict:
    try:
        data = json.loads((config.SAVES / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    try:
        config.SAVES.mkdir(parents=True, exist_ok=True)
        (config.SAVES / FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:  # a hint that cannot be remembered comes again; it must not cost a turn
        pass


def enabled(data: dict | None = None) -> bool:
    """The switch in the guide once the player has used it, else the config file's `tips`."""
    on = (_read() if data is None else data).get("on")
    if isinstance(on, bool):
        return on
    try:
        return config.load().tips
    except ValueError:  # a broken config is reported where it is used, not by a hint
        return True


def set_enabled(on: bool) -> None:
    data = _read()
    data["on"] = on
    _write(data)


# -- in the game ------------------------------------------------------------------------------
def after(screen, ev: dict) -> None:
    """GameScreen.handle passes every event here, once the event is on screen."""
    player = _player(screen)
    if player is None:
        return
    st = screen.state
    board = _boards.setdefault(screen, _Board())
    key = _called_for(st, player, ev)
    if key and key not in board.waiting:
        board.waiting.append(key)
    if ev.get("type") != "keeper_end" or st.ending:
        return
    board.waiting += [k for k in _due(st, player) if k not in board.waiting]
    if board.waiting and board.shown_at != st.turn:
        _show(screen, board, sorted(board.waiting, key=ORDER.index))
    board.waiting = [k for k in board.waiting if k not in NOW]


def page(screen) -> str | None:
    """The guide page the last hint pointed to, once: F1 opens the guide there."""
    board = _boards.get(screen)
    if board is None:
        return None
    shown, board.page = board.page, None
    return shown


def _player(screen) -> Investigator | None:
    """The investigator played at this screen; None when nobody here plays (watching)."""
    if getattr(screen, "spectate", False):
        return None
    st = screen.state
    seat = getattr(screen, "seat", "")  # a seat at a LAN table (tui/net.py); None: only watching
    if seat is None:
        return None
    mine = [seat] if seat else st.human
    return next((inv for inv in st.investigators if inv.id in mine), None)


def _called_for(st, player: Investigator, ev: dict) -> str | None:
    """The hint this event asks for, if it is the first of its kind."""
    t = ev.get("type")
    if st.turn == 0:  # the opening: only how to begin
        return "start" if t == "keeper_end" else None
    if st.pending_push and st.investigator.id == player.id:  # the push / luck choice is coming
        return "offer"
    if t == "damage":
        return "hurt" if ev.get("who") == player.name and 0 < player.hp * 3 <= player.hp_max else None
    if t == "sanity":
        return "sanity" if ev.get("loss") else None
    if t in ("repeat", "waived"):
        return t if ev.get("who") == player.name else None
    if t == "improv":  # the kinds the story tells; a consequence speaks for itself
        return "improv" if ev.get("kind") in ("开局改编", "展开", "推进") else None
    return {"handout": "handout", "clue": "clue", "dice": "check", "npc": "npc", "move": "move"}.get(t or "")


def _due(st, player: Investigator) -> list[str]:
    """What the game's course asks for at the end of the player's turn: a few turns going
    nowhere, and in 《追书人》 a few turns still in the house."""
    if st.investigator.id != player.id:
        return []
    due = ["stuck"] if st.turn - st.progress_at >= STUCK_AFTER else []
    if st.module == TUTORIAL and st.turn >= OUTSIDE_AFTER and set(st.visited) <= HOUSE:
        due.append(f"{TUTORIAL}:outside")
    return due


def _show(screen, board: _Board, keys: list[str]) -> None:
    """The first of `keys` this player has not seen yet, as a line in the story."""
    data = _read()
    seen = set(data.get("seen", []))
    board.waiting = [k for k in board.waiting if k not in seen]
    key = next((k for k in keys if k not in seen), None)
    if key is None or not enabled(data):
        return
    st, tip = screen.state, TIPS[key]
    text = OPENINGS.get(st.module, tip.text) if key == "start" else tip.text
    story = screen.story
    story.mount(TipLine(Text(text), classes="entry"))
    story.scroll_end(animate=False)
    data["seen"] = sorted(seen | {key})
    _write(data)
    if key in board.waiting:
        board.waiting.remove(key)
    board.shown_at, board.page = st.turn, tip.page


# -- the first game ------------------------------------------------------------------------------
def first_game(setup) -> None:
    """No saves yet: 《追书人》 is marked as the place to start and highlighted, so Enter picks
    it. A suggestion only; every other module stays in the list."""
    if setup.spectate or _played():
        return
    path = next((p for p in setup.paths if p.name == TUTORIAL), None)
    if path is None:
        return
    choices = setup.query_one("#choices", OptionList)
    choices.replace_option_prompt(str(path), Text.assemble(("新手教学", "bold"), f"：《{Module.load(path).title}》"))
    choices.highlighted = setup.paths.index(path)
    hint = "第一次玩，推荐先玩《追书人》：一位调查员就能开局"
    hint += "，第一次掷骰、第一次找到线索时，故事里会提示怎么玩" if enabled() else ""
    setup.query_one("#setup-hint", Static).update(hint + "。 · Enter 或空格选择 · Esc 返回")


def _played() -> bool:
    try:
        return bool(json.loads((config.SAVES / "index.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False
