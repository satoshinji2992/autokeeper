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
    "start": Tip("轮到你了。在底部输入行动或对话，按回车发送。F1 查看玩法说明，Esc 打开菜单。", "play"),
    "offer": Tip(
        "检定失败后，可以接受结果；符合条件时，也可花幸运或孤注一掷。孤注一掷再次失败会有更严重的后果。F1 查看规则。",
        "push",
    ),
    "hurt": Tip("HP 已降至三分之一以下。HP 到 0 可能昏迷或濒死，具体取决于伤势。F4 查看人物状态。", "screen"),
    "sanity": Tip("这是理智（SAN）损失。一次损失达到 5 点时，可能触发临时疯狂。F1 查看理智规则。", "sanity"),
    "handout": Tip("F2 打开线索本，可重看已获得的手稿、完整文字和原图。", "clues"),
    "clue": Tip("这条线索已记入线索本，按 F2 查看。", "clues"),
    "check": Tip("结果卡这样看：骰值不超过目标值即通过；↳ 一行记录本次检定的结果。F1 查看成功等级与奖励骰。", "dice"),
    "repeat": Tip("情况没有变化，同一尝试沿用上次结果。改变做法或获得新信息后，可以再试。", "dice"),
    "waived": Tip(".ra 是检定请求。结果明确，或技能不适用时，守秘人会直接说明，不掷骰。", "dice"),
    "npc": Tip("跟人说话时，直接输入要问的内容或你的原话。需要说服对方时，守秘人才会要求检定。", "play"),
    "improv": Tip("你可以探索模组没写到的事，守秘人会按情境即兴处理。F1「模组之外」有详细说明。", "improv"),
    "move": Tip("赶路会花游戏时间，顶部显示当前时间。F3 查看已知地点。", "screen"),
    "stuck": Tip("需要思路时按 F5。选中的建议只会填入输入框，确认后再发送。", "play"),
    # Only places already mentioned in the opening or hook; no later discoveries.
    f"{TUTORIAL}:outside": Tip("想出门时，直接说要去的地点；F3 可以查看目前已知的地方。", "play"),
}
OPENINGS = {
    TUTORIAL: "轮到你了。可以向站务员问路、先买份早饭，或按信上的地址找托马斯。在底部输入行动，回车发送；F1 查看玩法说明，Esc 打开菜单。",
}
HOUSE = {"jinboer_jia", "shufang"}  # 《追书人》: the Kimballs' house and the uncle's study
OUTSIDE_AFTER = 4  # turns spent in the house before the nudge out of the door
STUCK_AFTER = 4  # turns without a clue, an event or a new place before F5 is mentioned
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
    choices.replace_option_prompt(str(path), Text.assemble((f"《{Module.load(path).title}》", "bold"), " · 初次推荐"))
    choices.highlighted = setup.paths.index(path)
    hint = "《追书人》适合初次游玩，可独自调查。"
    setup.query_one("#setup-hint", Static).update(hint + " Enter 选择 · Esc 返回")


def _played() -> bool:
    try:
        return bool(json.loads((config.SAVES / "index.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False
