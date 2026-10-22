"""The player's own notes (F2, 「/记 内容」) in place of the 线索本 (the user, 2026-10-22: 「让玩家自己总结记录
线索，系统直接给总结完的线索不太好」): the game writes nothing into them and shows no clue's title or words
anywhere while it is on; the notes are kept with the save in a local game. No model calls."""

from __future__ import annotations

import json

import pytest
from textual.widgets import Input, OptionList, TabbedContent, TextArea

from autokeeper import config, paths
from autokeeper.config import Role
from autokeeper.engine.game import Game
from autokeeper.engine.state import read_save
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui import notes
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.modals.dialogs import HandoutModal
from autokeeper.tui.modals.notebook import NotebookModal
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.widgets.panels import NotesPanel

from .test_tui import story_text, until


class Canned(hm.Model):
    async def _call(self, messages, *, schema, images, on_delta):
        text = "前情回顾：周衡还在广场上。"
        if on_delta:
            await on_delta(text)
        return Reply(text=text, data={} if schema else None)


@pytest.fixture
def canned(monkeypatch, tmp_path, skills_dir):
    """Every agent answers from a can; saves go to a temp folder."""
    for var in ("AUTOKEEPER_PRESET", "AUTOKEEPER_MODEL", "AUTOKEEPER_FALLBACK", "AUTOKEEPER_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    monkeypatch.setitem(hm.MODELS, "canned", Canned)
    cfg = config.Config(roles={r: Role("canned", "canned", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


def game_state(slot: str, *, human: bool = True):
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot=slot, human=[lead.id] if human else [])
    return m, state


async def opened(app, pilot, m, state, *, spectate: bool = False) -> GameScreen:
    screen = GameScreen(m, state, resume=True, spectate=spectate)
    app.push_screen(screen)
    assert await until(pilot, lambda: screen.game is not None and not screen.busy)
    return screen


def panel(screen) -> str:
    return str(screen.query_one(NotesPanel).render())


# -- the lines -------------------------------------------------------------------------------------
def test_a_jotted_line_goes_at_the_end_and_the_column_shows_the_start():
    assert notes.line("：  守钟人  说钟只是老了 ") == "守钟人 说钟只是老了"
    assert notes.append("", "第一行") == "第一行"
    assert notes.append("第一行\n\n", "第二行") == "第一行\n第二行"
    assert notes.head("\n第一行\n  \n第二行  \n") == ["第一行", "第二行"]


# -- a local game ------------------------------------------------------------------------------------
async def test_the_notebook_is_the_players_own_page_kept_with_the_save(canned):
    m, state = game_state("notes-local")
    state.clues = ["c_notice", "c_diary"]  # found: still nothing of them on the screen
    state.handouts = ["h_diary"]
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await opened(app, pilot, m, state)
        assert panel(screen) == "笔记 · F2 记录"  # nothing written yet: where to write
        await pilot.press("f2")
        assert await until(pilot, lambda: isinstance(app.screen, NotebookModal))
        await pilot.pause()
        book = app.screen
        page = book.query_one("#notes-text", TextArea)
        assert page.has_focus and page.text == ""
        page.insert("守钟人不让人上楼\n日记里提到钟一直走")
        await pilot.pause()
        await pilot.press("escape")  # kept as it closes
        assert await until(pilot, lambda: app.screen is screen)
        assert state.notebook == {state.investigators[0].id: "守钟人不让人上楼\n日记里提到钟一直走"}
        saved = read_save(paths.save("notes-local").read_text(encoding="utf-8"))
        assert saved.notebook == state.notebook  # with the save
        assert panel(screen) == "笔记 · F2 展开\n守钟人不让人上楼\n日记里提到钟一直走"
        everything = story_text(screen) + panel(screen)
        for cid in state.clues:  # no clue's title or words anywhere on the screen
            clue = m.clue(cid)
            assert clue.title not in everything and clue.text.strip()[:8] not in everything

        await pilot.press("f2")  # again: the page as it was left, and the papers on the next one
        assert await until(pilot, lambda: isinstance(app.screen, NotebookModal))
        await pilot.pause()
        book = app.screen
        assert book.query_one("#notes-text", TextArea).text == state.notebook[state.investigators[0].id]
        await pilot.press("tab")
        assert book.query_one(TabbedContent).active == "papers-tab"
        papers = book.query_one("#papers", OptionList)
        assert papers.has_focus and papers.option_count == 1 and "守钟人的日记" in str(papers.get_option_at_index(0).prompt)
        await pilot.press("enter")  # a paper opens over the notebook, as it did when it was handed over
        assert await until(pilot, lambda: isinstance(app.screen, HandoutModal))
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is book)
        await pilot.press("tab")
        assert book.query_one(TabbedContent).active == "notes-tab" and book.query_one("#notes-text", TextArea).has_focus
        book.query_one("#notes-text", TextArea).clear()  # all of it gone: so is the page in the save
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen)
        assert state.notebook == {} and panel(screen) == "笔记 · F2 记录"


async def test_what_is_typed_is_kept_a_moment_after_the_last_key(canned, monkeypatch):
    from autokeeper.tui.modals import notebook

    monkeypatch.setattr(notebook, "KEEP_AFTER", 0.1)
    m, state = game_state("notes-typed")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await opened(app, pilot, m, state)
        await pilot.press("f2")
        assert await until(pilot, lambda: isinstance(app.screen, NotebookModal))
        await pilot.pause()
        app.screen.query_one("#notes-text", TextArea).insert("门锁是新换的")
        assert await until(pilot, lambda: state.notebook.get(state.investigators[0].id) == "门锁是新换的")  # still open
        assert "门锁是新换的" in paths.save("notes-typed").read_text(encoding="utf-8")
        assert app.screen is not screen


async def test_jotting_a_line_takes_no_turn_even_while_the_keeper_tells(canned):
    m, state = game_state("notes-jot")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await opened(app, pilot, m, state)
        me, turn, log = state.investigators[0].id, state.turn, len(state.log)
        screen.send_line("/记 守钟人说钟只是老了")
        screen.send_line("/记：告示是守钟人贴的")  # with a colon, no space: the same
        await pilot.pause()
        assert state.notebook[me] == "守钟人说钟只是老了\n告示是守钟人贴的"
        assert state.turn == turn and len(state.log) == log and not screen.busy  # no action, no keeper, no time
        assert "守钟人说钟只是老了" not in story_text(screen)  # the story is the game's; the line is in the notes
        assert panel(screen).startswith("笔记 · F2 展开\n守钟人说钟只是老了")
        assert "守钟人说钟只是老了" in paths.save("notes-jot").read_text(encoding="utf-8")
        screen.send_line("/记")  # nothing to write
        assert state.notebook[me] == "守钟人说钟只是老了\n告示是守钟人贴的"

        screen.busy = True  # the keeper is telling: a note goes in at once, nothing waits for the turn
        box = screen.query_one("#command", Input)
        box.focus()
        box.value = "/记 钟楼的门是从里面闩上的"
        await pilot.press("enter")
        await pilot.pause()
        assert state.notebook[me].endswith("\n钟楼的门是从里面闩上的") and screen.queued is None and box.value == ""
        screen.busy = False


async def test_someone_only_watching_keeps_no_notes(canned):
    m, state = game_state("notes-watch", human=False)
    state.handouts = ["h_diary"]
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = GameScreen(m, state, resume=True, spectate=True)
        screen.autoplay = lambda: None  # type: ignore[method-assign]  # nobody plays: nothing to watch here
        app.push_screen(screen)
        assert await until(pilot, lambda: screen.game is not None and not screen.busy)
        assert screen.notes() is None and panel(screen) == "资料 · F2 查看"
        screen.send_line("/记 看到了什么")
        await pilot.pause()
        assert state.notebook == {}
        screen.action_notes()
        assert await until(pilot, lambda: isinstance(app.screen, NotebookModal))
        await pilot.pause()
        assert not app.screen.query(TextArea) and app.screen.query_one("#papers", OptionList).option_count == 1


def test_no_clue_reaches_the_column_or_the_preview_of_a_save():
    """The save list's preview shows the start of the player's own notes, never a clue's title."""
    from rich.console import Console

    from autokeeper.tui.screens.saves import Save, details

    m, state = game_state("notes-preview")
    state.clues = ["c_notice"]
    state.notebook = {state.investigators[0].id: "告示栏上的字很小\n守钟人很紧张\n第三行\n第四行\n第五行"}
    console = Console(width=100, record=True, file=None)
    console.print(details(Save(slot="notes-preview", meta={}, state=state), m))
    shown = console.export_text()
    assert "笔记" in shown and "告示栏上的字很小" in shown and "第四行" in shown and "第五行" not in shown
    assert m.clue("c_notice").title not in shown and "线索" not in shown
    assert json.dumps(state.model_dump(mode="json"), ensure_ascii=False).count("告示栏上的字很小") == 1  # in the save, once


# -- a LAN table: each player's notes on their own machine ----------------------------------------------
class Link:
    """The connection to the host, as a seat's screen holds it: what it sends is kept here."""

    host = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    async def close(self) -> None:
        return None


async def seated(app, pilot, m, state, seat: str | None, link: Link):
    from autokeeper.tui.screens.net import NetGameScreen

    screen = NetGameScreen(m, state.model_copy(deep=True), link, seat, [])  # type: ignore[arg-type]
    app.push_screen(screen)
    assert await until(pilot, lambda: screen.ready)
    return screen


async def test_a_players_notes_at_a_lan_table_stay_on_their_machine_and_come_back_at_their_seat(canned):
    m, state = game_state("lan-example-0101-120000")
    friend = state.investigators[0].model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state.investigators.append(friend)
    me = state.investigators[0].id
    kept = paths.table_notes("example", "lan-example-0101-120000", me)
    link = Link()
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await seated(app, pilot, m, state, me, link)
        assert screen.notes() == "" and panel(screen) == "笔记 · F2 记录"
        screen.send_line("/记 告示是守钟人贴的")
        await pilot.pause()
        assert kept.read_text(encoding="utf-8") == "告示是守钟人贴的" and link.sent == []  # on this machine, never sent
        await pilot.press("f2")
        assert await until(pilot, lambda: isinstance(app.screen, NotebookModal))
        await pilot.pause()
        book = app.screen
        assert "只保存在这台电脑上" in "".join(str(w.render()) for w in book.query(".system"))
        book.query_one("#notes-text", TextArea).insert("\n守钟人很紧张")
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen)
        assert kept.read_text(encoding="utf-8") == "告示是守钟人贴的\n守钟人很紧张"
        view = state.model_copy(update={"turn": 2}).model_dump(mode="json", exclude={"notebook"})  # the host's next word
        await screen.on_msg({"t": "sync", "state": view})
        assert screen.notes() == "告示是守钟人贴的\n守钟人很紧张" and panel(screen).startswith("笔记 · F2 展开\n告示是守钟人贴的")
        assert not [msg for msg in link.sent if "守钟人" in json.dumps(msg, ensure_ascii=False)]  # nothing of them went out
        app.pop_screen()
        await pilot.pause()

        again = await seated(app, pilot, m, state, me, Link())  # back at the same seat, another day
        assert again.notes() == "告示是守钟人贴的\n守钟人很紧张" and "告示是守钟人贴的" in panel(again)
        app.pop_screen()
        await pilot.pause()
        hers = await seated(app, pilot, m, state, "lin", Link())  # another seat: notes of its own
        assert hers.notes() == "" and panel(hers) == "笔记 · F2 记录"
        app.pop_screen()
        await pilot.pause()
        watching = await seated(app, pilot, m, state, None, Link())  # watching: none
        assert watching.notes() is None and panel(watching) == "资料 · F2 查看"
    assert sorted(p.name for p in kept.parent.iterdir()) == [f"{me}.txt"]


async def test_a_browser_seat_keeps_its_notes_only_while_its_page_is_open(canned, monkeypatch):
    """A browser's seat runs on the host's machine (net/web.py): nothing of its notes is written there."""
    monkeypatch.setenv("AUTOKEEPER_GUEST", "1")
    m, state = game_state("lan-example-0101-130000")
    me = state.investigators[0].id
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        link = Link()
        screen = await seated(app, pilot, m, state, me, link)
        screen.send_line("/记 钟楼的门从里面闩上了")
        await pilot.pause()
        assert screen.notes() == "钟楼的门从里面闩上了" and link.sent == []
        assert not (paths.games() / "notes").exists()
        await pilot.press("f2")
        assert await until(pilot, lambda: isinstance(app.screen, NotebookModal))
        await pilot.pause()
        assert "网页版不保存笔记" in "".join(str(w.render()) for w in app.screen.query(".system"))


async def test_the_hosts_notes_in_a_save_never_reach_another_screen(fake):
    """A save played on the host's machine carries its player's notes (GameState.notebook): opened as a LAN
    room, they stay on the host; the view and the welcome every screen gets hold none of them."""
    from autokeeper.net.hub import Hub
    from autokeeper.rules.coc import Rules

    from .test_net import TableCast

    _, state = game_state("lan-hosted")
    state.notebook = {state.investigators[0].id: "房主自己的推测：守钟人在撒谎"}
    hub = Hub(EXAMPLE, state=state, cast=TableCast(), rules=Rules(dice=fake))
    hub.clients["c1"] = None  # type: ignore[assignment]
    for payload in (hub._view(), hub._welcome("c1")):
        text = json.dumps(payload, ensure_ascii=False, default=str)
        assert "房主自己的推测" not in text and "notebook" not in text
    assert hub.state.notebook  # still the host's, in its save
    await hub.game.close()
