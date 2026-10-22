"""Private table speech uses an explicit recipient and waits for the host's delivery."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.text import Text

from autokeeper.engine.game import Game
from autokeeper.engine.state import LogEntry
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.tui.app import AutoKeeperApp
from autokeeper.tui.screens.lobby import LobbyScreen
from autokeeper.tui.screens.net import NetGameScreen, history_text, with_private_history


def screen_stub(*, seat="zhouheng", apart=None, extra=(), ending=None):
    sent, notices, shown = [], [], []
    roster = [SimpleNamespace(id="zhouheng", name="周衡"), SimpleNamespace(id="lin", name="林秋"), *extra]
    screen = SimpleNamespace(
        seat=seat,
        state=SimpleNamespace(investigators=roster, location="tower", apart=apart or {}, ending=ending),
        app=SimpleNamespace(notify=lambda text, **_: notices.append(text)),
        link=SimpleNamespace(send=sent.append),
        story=SimpleNamespace(add=lambda *args, **kwargs: shown.append((args, kwargs))),
        run_worker=lambda worker: worker(),
    )
    return screen, sent, notices, shown


@pytest.mark.parametrize(("line", "body"), [
    ("/私聊 林秋 别让他看到这封信。", "别让他看到这封信。"),
    ("/w lin （压低声音）“等等。”", "（压低声音）“等等。”"),
    ("/W\tlin\tKeep the door shut.", "Keep the door shut."),
])
def test_private_speech_names_one_recipient_and_does_not_echo_before_delivery(line, body):
    screen, sent, notices, shown = screen_stub()
    NetGameScreen.send_line(screen, line)
    assert sent == [{"t": "speak", "text": body, "to": ["lin"], "channel": "whisper"}]
    assert not notices and not shown


def test_a_quoted_recipient_name_can_contain_spaces_without_changing_the_message():
    friend = SimpleNamespace(id="harold", name="Harold Brennan")
    screen, sent, notices, shown = screen_stub(extra=[friend])
    NetGameScreen.send_line(screen, '/私聊 "Harold Brennan" "I found it."')
    assert sent == [{"t": "speak", "text": '"I found it."', "to": ["harold"], "channel": "whisper"}]
    assert not notices and not shown


@pytest.mark.parametrize(("line", "options", "reason"), [
    ("/私聊 林秋 内容", {"seat": None}, "观战"),
    ("/私聊 周衡 内容", {}, "其他调查员"),
    ("/私聊 不存在 内容", {}, "未找到"),
    ("/私聊 林秋 内容", {"extra": [SimpleNamespace(id="another", name="林秋")]}, "多名调查员"),
    ("/私聊 林秋 内容", {"apart": {"lin": "square"}}, "同一地点"),
    ("/私聊 林秋 内容", {"apart": {"zhouheng": "square"}}, "同一地点"),
    ("/私聊 林秋 内容", {"ending": "over"}, "本局已结束"),
    ("/私聊", {}, "用法"),
    ("/私聊 林秋", {}, "用法"),
    ("/私聊 林秋   ", {}, "用法"),
])
def test_invalid_private_speech_is_not_sent(line, options, reason):
    screen, sent, notices, shown = screen_stub(**options)
    NetGameScreen.send_line(screen, line)
    assert not sent and not shown and len(notices) == 1 and reason in notices[0]


def test_two_players_in_the_same_separate_scene_can_whisper():
    screen, sent, notices, _ = screen_stub(apart={"zhouheng": "square", "lin": "square"})
    NetGameScreen.send_line(screen, "/w lin 看街角。")
    assert sent and not notices


def test_recovered_private_history_replaces_the_previous_seat_without_duplicating_public_lines():
    public = {"turn": 1, "text": "共同看见的街道"}
    previous = {"turn": 2, "text": "另一角色的纸条", "audience": ["other"]}
    private = {"turn": 3, "text": "我的纸条", "audience": ["zhouheng"]}
    assert with_private_history([public, previous], [private]) == [public, private]
    assert with_private_history(with_private_history([public], [private]), [private]) == [public, private]
    assert with_private_history([public, previous], []) == [public]


def test_private_history_labels_the_scope_without_printing_internal_ids():
    entry = SimpleNamespace(kind="keeper", who="守秘人", text="只有你听见敲门声。", audience=["internal-id"])
    assert history_text(entry).plain == "【守秘人·私密】只有你听见敲门声。"
    entry.kind, entry.who = "speech", "林秋"
    assert history_text(entry).plain.startswith("【私语】林秋：")
    assert "internal-id" not in history_text(entry).plain


async def test_lobby_keeps_the_chosen_seats_private_history_only():
    public = {"turn": 1, "text": "共同看见的街道"}
    previous = {"turn": 2, "text": "另一角色的纸条", "audience": ["other"]}
    private = {"turn": 3, "text": "我的纸条", "audience": ["zhouheng"]}
    lobby = SimpleNamespace(welcome={"log": [public, previous]}, chosen="zhouheng")
    await LobbyScreen.on_msg(lobby, {"t": "history", "seat": "other", "log": [previous]})
    assert lobby.welcome["log"] == [public, previous]
    await LobbyScreen.on_msg(lobby, {"t": "history", "seat": "zhouheng", "log": [private]})
    assert lobby.welcome["log"] == [public, private]


class Link:
    host = False

    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        pass


def story_lines(screen):
    return [block.content.plain for block in screen.story.query(".entry") if isinstance(block.content, Text)]


async def test_private_delivery_and_personal_keeper_messages_have_clear_labels_and_recover_once():
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, friend], slot="private-ui", human=[lead.id, friend.id])
    state.location, state.prologue, state.turn = "tower", "开场已讲过", 4
    link = Link()
    app = AutoKeeperApp(theme="amber")
    public = LogEntry(turn=1, time="D1 12:00", kind="keeper", text="所有人看见街道。")
    personal = LogEntry(turn=1, time="D1 12:00", kind="keeper", text="只有你看到窗后的人影。", audience=[lead.id])
    async with app.run_test(size=(130, 40)) as pilot:
        screen = NetGameScreen(module, state, link, lead.id, [public, personal])
        app.push_screen(screen)
        for _ in range(30):
            await pilot.pause(0.05)
            if screen.ready:
                break
        assert screen.ready
        assert screen.told.content.plain == "所有人看见街道。"
        assert any("【守秘人·私密】只有你看到窗后的人影。" in line for line in story_lines(screen))
        screen.send_line("/私聊 林秋 别让他看到这封信。")
        await pilot.pause()
        assert link.sent == [{"t": "speak", "text": "别让他看到这封信。", "to": ["lin"], "channel": "whisper"}]
        assert not any("别让他看到" in line for line in story_lines(screen))
        await screen.on_msg({"t": "event", "ev": {"type": "speech", "who": lead.name, "player": "小明",
                                                "to": "林秋", "text": "别让他看到这封信。", "channel": "whisper",
                                                "audience": [lead.id, friend.id]}})
        await screen.on_msg({"t": "event", "ev": {"type": "aside", "who": "守秘人", "text": "只有你听见敲门声。",
                                                "audience": [lead.id]}})
        await pilot.pause()
        assert any(f"【私语】{lead.name}（小明） → 林秋：别让他看到这封信。" in line for line in story_lines(screen))
        assert any("【守秘人·私密】只有你听见敲门声。" in line for line in story_lines(screen))
        assert screen.told.content.plain == "所有人看见街道。"
        entry = LogEntry(turn=2, time="D1 12:00", kind="speech", who="林秋", text="在塔楼私下对你说：这是旧记录。",
                         audience=[lead.id, friend.id])
        history = {"t": "history", "seat": lead.id, "log": [entry.model_dump(mode="json")]}
        await screen.on_msg(history)
        await screen.on_msg(history)
        await screen.on_msg({"t": "history", "seat": friend.id, "log": [
            entry.model_copy(update={"text": "另一角色的旧记录。"}).model_dump(mode="json")
        ]})
        await pilot.pause()
        assert sum("这是旧记录" in line for line in story_lines(screen)) == 1
        assert any("【私语】林秋：" in line and "这是旧记录" in line for line in story_lines(screen))
        assert not any("另一角色的旧记录" in line for line in story_lines(screen))
