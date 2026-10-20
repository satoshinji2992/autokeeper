"""Input channels, bare browser Alt and queued KP questions, without model calls."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from textual._xterm_parser import XTermParser

from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.net.web import PAGE
from autokeeper.rules.coc import Rules

from .test_engine import FakeCast


@pytest.mark.parametrize("sequence, key", [("\x1b[13;3u", "alt+enter"), ("\x1bm", "alt+m")])
def test_mode_switch_sequences_cannot_be_parsed_as_a_plain_enter(sequence, key):
    parser = XTermParser()
    events = [*parser.feed(sequence), *parser.feed("")]
    assert [getattr(event, "key", None) for event in events] == [key]


async def test_kp_questions_wait_separately_from_the_network_action_pool(fake, monkeypatch):
    m = Module.load(paths.EXAMPLE)
    state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    cast = FakeCast({})
    hub = Hub(paths.EXAMPLE, state=state, cast=cast, rules=Rules(dice=fake))
    monkeypatch.setattr(hub, "_seat_of", lambda cid: state.investigator.id)
    hub.open = True
    before = state.turn, state.minutes
    await hub._on("local-player", {"t": "act", "text": "/kp 我刚才说的是书房。"})
    assert not hub.pool and not hub.early and hub.instant.qsize() == 1
    seat, text = hub.instant.get_nowait()
    await hub._afterwards(seat, text)
    assert (state.turn, state.minutes) == before and not cast.heard and not cast.ruled
    assert "我刚才说的是书房" in cast.reports[-1]
    await hub.game.close()


@pytest.mark.skipif(not shutil.which("node"), reason="browser keyboard checks need Node.js")
def test_browser_alt_taps_toggle_but_shortcuts_altgr_and_lost_focus_do_not():
    page = (PAGE / "app_index.html").read_text(encoding="utf-8")
    start = page.index("window.autokeeperAltKey =")
    end = page.index("\n        // The address", start)
    setup = page[start:end]
    script = r'''
const handlers = {};
global.window = {addEventListener: (key, fn) => { handlers['window:' + key] = fn; }};
global.document = {activeElement: {}, addEventListener: (key, fn) => { handlers[key] = fn; }};
const sent = [];
let focused = true;
const session = {element: {contains: () => focused}, socket: {readyState: 1, send: value => sent.push(JSON.parse(value))}};
SETUP
window.autokeeperAltKey(session);
function key(type, name, extra = {}) {
  handlers[type]({key: name, repeat: false, ctrlKey: false, metaKey: false,
    getModifierState: () => false, preventDefault() {}, ...extra});
}
key('keydown', 'Alt'); key('keyup', 'Alt');
key('keydown', 'Alt'); key('keydown', 'Enter'); key('keyup', 'Alt');
key('keydown', 'Alt', {ctrlKey: true}); key('keyup', 'Alt');
key('keydown', 'Alt', {getModifierState: () => true}); key('keyup', 'Alt');
key('keydown', 'Alt'); handlers['window:blur'](); key('keyup', 'Alt');
focused = false; key('keydown', 'Alt'); key('keyup', 'Alt');
console.log(JSON.stringify(sent));
'''.replace("SETUP", setup)
    result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [["stdin", "\x1b[13;3u"]]
