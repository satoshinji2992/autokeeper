"""Personal observations stay scoped through shared situations and compressed history."""

from __future__ import annotations

import asyncio

import pytest

from autokeeper import config
from autokeeper.agents.cast import Action, Cast, _recent
from autokeeper.engine.autoplay import situation
from autokeeper.engine.game import Game
from autokeeper.engine.state import LogEntry
from autokeeper.harness import Reply
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

PUBLIC = "全员看见街角警员正在修车。"
FIRST = "只有周衡看见窗后亮起一盏青灯。"
SECOND = "只有林秋听见有人在墙内数数。"
SHARED = "林秋私下告诉周衡：信封内有一把铜钥匙。"
ARCHIVED = "周衡昨夜独自在梦里读到灰塔的名字。"
BACKSTAGE = "幕后人物此刻正在烧毁账本。"


@pytest.fixture
def private_game():
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(update={"id": "zhouheng", "name": "周衡"}, deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋"}, deep=True)
    state = Game.new(module, [lead, friend], slot="private-context", human=[lead.id, friend.id])
    state.location, state.turn = "square", 10
    state.memory = "大家昨夜在旅店会合。"
    state.add("keeper", PUBLIC)
    state.add("keeper", FIRST, audience=[lead.id])
    state.add("keeper", SECOND, audience=[friend.id])
    state.add("speech", SHARED, who=friend.name, audience=[lead.id, friend.id])
    state.add("event", BACKSTAGE, audience=[])
    state.private_history.append(LogEntry(turn=1, time="D1 20:00", kind="keeper", text=ARCHIVED, audience=[lead.id]))
    return module, state


def test_situations_filter_private_observations_before_truncating_and_restore_archived_personal_details(private_game):
    module, state = private_game
    common = situation(module, state)
    first = situation(module, state, "zhouheng")
    second = situation(module, state, "lin")
    assert all(secret not in common for secret in (FIRST, SECOND, SHARED, ARCHIVED, BACKSTAGE))
    assert all(known in first for known in (PUBLIC, FIRST, SHARED, ARCHIVED))
    assert "私密，收件人：周衡" in first and "私密，收件人：周衡、林秋" in first
    assert SECOND not in first and BACKSTAGE not in first
    assert all(known in second for known in (PUBLIC, SECOND, SHARED))
    assert FIRST not in second and ARCHIVED not in second and BACKSTAGE not in second


def test_personal_details_survive_a_busy_public_log_without_becoming_shared(private_game):
    module, state = private_game
    for turn in range(20):
        state.add("keeper", f"大家共同看到第{turn}辆车经过。")
    first = situation(module, state, "zhouheng")
    assert FIRST in first and ARCHIVED in first and SHARED in first
    assert SECOND not in first and BACKSTAGE not in first
    assert all(secret not in situation(module, state) for secret in (FIRST, SECOND, SHARED, ARCHIVED))


def test_a_separated_investigator_reads_their_actual_location(private_game):
    module, state = private_game
    state.apart["lin"] = "tower"
    view = situation(module, state, "lin")
    assert "你在钟楼" in view.split("\n", 1)[0]
    assert "你在钟楼，大家在广场" in view


def test_the_keeper_sees_all_recent_scopes_but_other_viewers_do_not(private_game):
    _, state = private_game
    full = _recent(state)
    assert all(known in full for known in (PUBLIC, FIRST, SECOND, SHARED, ARCHIVED, BACKSTAGE))
    assert "私密收件人：周衡" in full and "私密收件人：林秋" in full and "仅KP" in full
    first = _recent(state, viewer="zhouheng", keeper=False)
    assert all(known in first for known in (FIRST, SHARED, ARCHIVED))
    assert SECOND not in first and BACKSTAGE not in first
    assert all(secret not in _recent(state, keeper=False) for secret in (FIRST, SECOND, SHARED, ARCHIVED, BACKSTAGE))


async def test_parallel_ai_proposals_add_their_own_private_history_to_the_shared_situation(private_game, monkeypatch):
    module, state = private_game
    cast = Cast(config.Config(), module)
    asked = {}

    async def send(key, session, state, prompt, **kwargs):
        await asyncio.sleep(0)
        asked[key] = prompt
        return Reply(text="", data={"text": "我查看街角的汽车"})

    monkeypatch.setattr(cast, "_send", send)
    common = situation(module, state)
    await asyncio.gather(*(cast.investigator(state, inv.id, common) for inv in state.investigators))
    first, second = asked["investigator:zhouheng"], asked["investigator:lin"]
    assert all(known in first for known in (PUBLIC, FIRST, SHARED, ARCHIVED))
    assert SECOND not in first and BACKSTAGE not in first
    assert SECOND in second and SHARED in second and FIRST not in second and ARCHIVED not in second
    assert all(secret not in common for secret in (FIRST, SECOND, SHARED, ARCHIVED, BACKSTAGE))
    await cast.close()


async def test_personal_suggestions_include_only_the_requesters_observations(private_game, monkeypatch):
    module, state = private_game
    cast = Cast(config.Config(), module)
    asked = []

    async def send(key, session, state, prompt, **kwargs):
        asked.append(prompt)
        return Reply(text="", data={"look": "查看修车工具", "ask": "询问警员", "dare": "进入钟楼"})

    monkeypatch.setattr(cast, "_send", send)
    await cast.suggest(state, "zhouheng", situation(module, state))
    assert all(known in asked[0] for known in (PUBLIC, FIRST, SHARED, ARCHIVED))
    assert SECOND not in asked[0] and BACKSTAGE not in asked[0]
    own = situation(module, state, "zhouheng")
    await cast.suggest(state, "zhouheng", own)
    assert asked[1] == own  # already scoped: do not repeat the same private block
    await cast.close()


async def test_public_recap_excludes_current_and_archived_private_records(private_game, monkeypatch):
    module, state = private_game
    cast = Cast(config.Config(), module)
    asked = []

    async def send(key, session, state, prompt, **kwargs):
        asked.append(prompt)
        return Reply(text="大家在街角看见警员。")

    monkeypatch.setattr(cast, "_send", send)
    assert await cast.recap(state) == "大家在街角看见警员。"
    assert PUBLIC in asked[0] and state.memory in asked[0]
    assert all(secret not in asked[0] for secret in (FIRST, SECOND, SHARED, ARCHIVED, BACKSTAGE))
    await cast.close()


async def test_intent_and_arbiter_recent_observations_use_the_acting_investigator(private_game, monkeypatch, skills_dir):
    module, state = private_game
    cast = Cast(config.Config(), module)
    state.active = 1
    heard = cast._heard(state, "我查看汽车")
    assert SECOND in heard and FIRST not in heard and ARCHIVED not in heard and BACKSTAGE not in heard
    asked = []

    async def send(key, session, state, prompt, **kwargs):
        asked.append(prompt)
        return Reply(text="", data={"needs_roll": False, "minutes": 0})

    monkeypatch.setattr(cast, "_send", send)
    await cast.arbiter(state, Action(kind="investigate", summary="林秋查看街角的汽车"))
    assert SECOND in asked[0] and FIRST not in asked[0] and ARCHIVED not in asked[0] and BACKSTAGE not in asked[0]
    await cast.close()
