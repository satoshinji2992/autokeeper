"""The keeper's scene: settled first, told after, shown once it holds (world/scene.py, Game._narrate).

No network: a scripted backend writes the keeper's tellings, streaming their JSON in pieces the
way Claude Code (StructuredOutput) and DeepSeek's JSON mode do.
"""

from __future__ import annotations

import json
from collections import deque

import pytest

from autokeeper import config, paths
from autokeeper.agents.cast import Action, Cast, Ruling, _Telling
from autokeeper.config import Role
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.harness import Reply, spend
from autokeeper.harness import models as hm
from autokeeper.module.schema import Item, Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.world import scene

SCRIPT: deque = deque()  # what the keeper answers next: a dict (a telling, streamed as JSON), a str (words), an error
SEEN: list[dict] = []  # every call: its messages and schema


class Writer(hm.Model):
    """Writes a telling the way the real backends stream one: its JSON in small pieces, keys in
    the order given (a dict written text first comes out text first)."""

    mode = "json"

    async def _call(self, messages, *, schema, images, on_delta):
        SEEN.append({"messages": [dict(m) for m in messages], "schema": schema})
        out = SCRIPT.popleft()
        if isinstance(out, Exception):
            raise out
        text = json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out
        if on_delta:
            for i in range(0, len(text), 7):
                await on_delta(text[i:i + 7])
        data = (out if isinstance(out, dict) else hm.parse_json(out)) if schema else None
        return Reply(text=text, data=data, usage={"input_tokens": 100, "output_tokens": 10})


WRITER = Role("writer", "scripted-keeper", None)


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setitem(hm.MODELS, "writer", Writer)
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setenv("AUTOKEEPER_TRACE", "off")
    spend.budget.cache_clear()
    SCRIPT.clear()
    SEEN.clear()
    yield
    spend.budget.cache_clear()


def telling(text: str, **scene_fields) -> dict:
    return {"scene": scene_fields, "text": text}


def made(location: str = "square", *, module: Module | None = None, state: GameState | None = None, fake=None):
    """A game of the format example at `location` (or a saved one, `state`), with the real cast,
    every role on the writer."""
    m = module or Module.load(EXAMPLE)
    if state is None:
        state = Game.new(m, [m.pregens[0].model_copy(deep=True)])
        state.location = location
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    cast = Cast(config.Config(roles={r: WRITER for r in config.DEFAULT_ROLES}), m)
    game = Game(m, state, cast, Rules(dice=fake), emit=emit)  # type: ignore[arg-type]
    return game, events


def acted(game: Game, said: str, minutes: int, **action) -> Turn:
    """A turn the player began with `said`, which took `minutes` so far."""
    st = game.state
    st.turn += 1
    st.add("player", said, who=st.investigator.name)
    st.minutes += minutes
    return Turn(action=Action(kind=action.pop("kind", "investigate"), summary=said, **action), ruling=Ruling(needs_roll=False))


def ctx(game: Game, elapsed: int = 0, **kw) -> scene.Context:
    return scene.Context(travel=game._travel, elapsed=elapsed, **kw)


def told(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "keeper_delta")


# -- the scene against the record ---------------------------------------------------------------
async def test_company_and_a_walk_between_rooms_of_one_place_land(fake):
    game, events = made("square")
    w, st = game.world, game.state
    turn = acted(game, "我和布道者一起进钟楼", 2)
    SCRIPT.append(telling("你推开钟楼的门，布道者跟着你挤了进来。守钟人从楼梯口抬起头。",
                          room="tower", here=["warden", "preacher"], follow=["preacher"],
                          traces=[{"text": "门槛上留下一串湿脚印", "visibility": "public"}], now="守钟人盯着你们，没说话"))
    text = await game.narrate(turn)
    assert text.startswith("你推开钟楼的门") and told(events) == text  # streamed, whole
    assert st.location == "tower" and "tower" in st.visited  # the walk: no minutes added
    assert st.minutes == turn_start(st) + 2
    preacher = w.rec.entities["preacher"]
    assert preacher.at == "tower" and preacher.following == st.investigator.id and preacher.follow_to == ""
    assert any(e.trace == "门槛上留下一串湿脚印" and e.place == "tower" for e in w.rec.events)
    assert [e.id for e in w.rec.journal if e.id.startswith(f"keeper:{st.turn}:")]  # op ids keeper:{turn}:{i}
    assert st.ongoing == "守钟人盯着你们，没说话" and st.log[-1].text == text
    assert {"type": "move", "location": "tower"}.items() <= next(e for e in events if e["type"] == "move").items()
    # Company along wherever they go next: the party's own travel takes the preacher too.
    await game._arrive("square", Turn(), party=True)
    assert preacher.at == "square" and preacher.following == st.investigator.id
    await game.close()
    await game.cast.close()


def turn_start(st: GameState) -> int:
    from autokeeper.engine.state import parse_time

    return parse_time(next(e.time for e in reversed(st.log) if e.kind == "player"))


def test_someone_gone_from_the_scene_must_have_gone_somewhere(fake):
    game, _ = made("square")
    nobody = scene.plan(game.world, {"here": []}, ctx(game, 30))
    assert nobody.problems and "街头布道者原来在广场" in nobody.problems[0] and "也没写去向" in nobody.problems[0]
    gone = scene.plan(game.world, {"here": [], "left": [{"who": "preacher", "to": "office"}]}, ctx(game, 30))
    assert not gone.problems and [u.said for u in gone.held] == [["街头布道者去了报社"]]
    unchanged = scene.plan(game.world, {"now": "雾更浓了"}, ctx(game))  # what the scene leaves out stays as it is
    assert not unchanged.problems and not unchanged.units and unchanged.now == "雾更浓了"
    by_name = scene.plan(game.world, {"here": ["街头布道者"]}, ctx(game))  # a name stands for its id
    assert not by_name.problems and not by_name.units


def test_the_scene_does_not_take_the_investigators_to_another_place(fake):
    game, _ = made("square")
    far = scene.plan(game.world, {"room": "office", "here": []}, ctx(game, 30))
    assert any("报社不在同一处" in p and "玩家下一轮自己走" in p for p in far.problems) and not far.room
    near = scene.plan(game.world, {"room": "钟楼", "here": ["preacher", "warden"]}, ctx(game))  # a room of the same place, by name
    assert not near.problems and near.room == "tower"
    missed = scene.plan(game.world, {"room": "tower", "here": ["preacher"]}, ctx(game))  # the warden is in there
    assert any("老守钟人原来在钟楼" in p for p in missed.problems)
    still = scene.plan(game.world, {"room": "tower", "here": ["preacher"]}, ctx(game, walk=False))  # the opening, an interlude
    assert any("不能替他们走到钟楼" in p for p in still.problems)


def test_an_npc_comes_or_goes_only_as_far_as_the_turn_took_them(fake):
    game, _ = made("office")
    rushed = scene.plan(game.world, {"here": ["preacher"]}, ctx(game, 3))
    assert any("街头布道者在广场，到报社要 10 分钟，这一轮只过了 3 分钟，赶不到" in p and "可以写他动身" in p for p in rushed.problems)
    walked = scene.plan(game.world, {"here": ["preacher"]}, ctx(game, 12))
    assert not walked.problems and walked.held[0].said == ["街头布道者来到报社"]
    game, _ = made("tower")  # the tower stands in the square: a few steps, while they talk (the review of 2026-10-20)
    over = scene.plan(game.world, {"here": ["warden", "preacher"]}, ctx(game, 0))
    assert not over.problems and over.held[0].said == ["街头布道者来到钟楼"]
    leaving = scene.plan(game.world, {"here": [], "left": [{"who": "warden", "to": "office"}]}, ctx(game, 5))
    assert any("老守钟人从钟楼到报社要 20 分钟" in p and "人还写在 here 里" in p for p in leaving.problems)


def test_a_handover_lands_with_the_moves_of_the_people_in_it(fake):
    m = Module.load(EXAMPLE)
    m.items.append(Item(id="lantern", name="提灯", description="一盏铜提灯", where="tower", owner="warden"))
    game, _ = made("office", module=m)
    lead = game.state.investigator.id
    p = scene.plan(game.world, {"here": ["warden"], "holds": [{"item": "提灯", "holder": lead}]}, ctx(game, 25))
    assert not p.problems and [u.key for u in p.held] == ["npc:warden"]  # his coming and his handing over: one unit
    assert p.held[0].said == ["老守钟人来到报社", "提灯到了周衡手里"]
    alone = scene.plan(game.world, {"holds": [{"item": "lantern", "holder": lead}]}, ctx(game, 25))
    assert any("老守钟人和周衡讲完时不在一处，提灯递不过去" in x for x in alone.problems)
    rushed = scene.plan(game.world, {"here": ["warden"], "holds": [{"item": "lantern", "holder": lead}]}, ctx(game, 3))
    assert not rushed.held  # he cannot come, so he cannot hand it over either
    landed = scene.land(game.world, p.held)
    assert [u.key for u in landed] == ["npc:warden"] and game.world.rec.entities["lantern"].at == lead


def test_everyday_things_are_registered_as_given_but_not_what_the_action_lacks(fake):
    game, _ = made("square")
    w, lead = game.world, game.state.investigator.id
    turn = Turn(action=Action(kind="investigate", summary="周衡想挖开地砖"), ruling=Ruling(needs_roll=False, missing=["铲子"]))
    c = game._scene_context(turn)
    assert c.missing == ["铲子"] and "铲子" not in game._scene_brief(c)  # said once, in the report's notes (Game._lacking)
    spade = scene.plan(w, {"here": ["preacher"], "things": [{"name": "铲子", "holder": lead}]}, c)
    assert any("这一轮的行动正缺铲子" in x for x in spade.problems)
    given = scene.plan(w, {"here": ["preacher"], "things": [{"name": "火柴", "holder": "preacher"}],
                           "holds": [{"item": "火柴", "holder": lead}]}, c)
    assert not given.problems and given.held[0].said == ["街头布道者拿出火柴，给了周衡"]  # brought out and handed over: one unit
    assert [u.key for u in scene.land(w, given.held)] == ["thing:0"]
    match = next(e for e in w.rec.entities.values() if e.kind == "item" and e.name == "火柴")
    assert match.at == lead and "火柴" in game.state.investigator.inventory and not match.important
    away = scene.plan(w, {"here": ["preacher"], "things": [{"name": "烟斗", "holder": "warden"}]}, c)
    assert any("老守钟人讲完时不在调查员身边" in x for x in away.problems)  # he is in the tower


# -- told after it is settled -----------------------------------------------------------------
async def test_a_scene_turned_back_once_is_written_again_and_only_that_one_is_shown(fake):
    game, events = made("square")
    turn = acted(game, "我在广场上等等看", 15)
    SCRIPT.extend([
        telling("布道者收起册子走了。", here=[]),  # gone, but where to?
        telling("布道者收起册子，往报社那边去了。", here=[], left=[{"who": "preacher", "to": "office"}]),
    ])
    text = await game.narrate(turn)
    assert text == "布道者收起册子，往报社那边去了。" and told(events) == text  # the first draft never reached the player
    assert game.world.rec.entities["preacher"].at == "office"
    again = SEEN[1]["messages"][-1]["content"]
    assert "【上一稿的 scene 没通过核对】" in again and "也没写去向" in again and "走了就写进 left" in again
    keeper = game.state.transcripts["keeper"]
    assert [m["content"] for m in keeper if m["role"] == "assistant"] == [text]  # it remembers the words, once
    await game.cast.close()


async def test_failing_twice_the_keeper_tells_what_holds_and_the_rest_as_attempts(fake):
    game, events = made("office")
    turn = acted(game, "我喊布道者过来", 2)
    rushed = telling("布道者推门进来。", here=["preacher"], traces=[{"text": "楼梯上落了一层灰", "visibility": "public"}])
    SCRIPT.extend([rushed, rushed, "你朝窗外喊了一声，布道者在广场上应了，说这就过来。楼梯上落着一层灰。"])
    text = await game.narrate(turn)
    assert text.startswith("你朝窗外喊了一声") and told(events) == text
    last = SEEN[-1]
    assert last["schema"] is None  # the words only, to the scene the program put together
    asked = last["messages"][-1]["content"]
    assert "【场面已经定了】" in asked and "留下痕迹：楼梯上落了一层灰" in asked
    assert "没能成立的，只能写成尝试或打算" in asked and "到报社要 10 分钟" in asked
    w = game.world
    assert w.rec.entities["preacher"].at == "square"  # what did not hold did not land
    assert any(e.trace == "楼梯上落了一层灰" for e in w.rec.events) and game.state.ongoing == ""
    await game.cast.close()


async def test_words_written_before_the_scene_wait_for_it(fake):
    game, events = made("square")
    turn = acted(game, "我看看告示栏", 3)
    SCRIPT.append({"text": "告示栏上的纸被风掀起一角。", "scene": {"here": ["preacher"], "now": "风还在刮"}})
    await game.narrate(turn)
    pieces = [e["text"] for e in events if e["type"] == "keeper_delta"]
    assert pieces == ["告示栏上的纸被风掀起一角。"]  # not streamed: shown whole, once the scene held
    assert game.state.ongoing == "风还在刮"
    SCRIPT.append(telling("布道者清了清嗓子，又念起经来。", here=["preacher"]))
    events.clear()
    await game.narrate(acted(game, "我听他念", 1))
    assert len([e for e in events if e["type"] == "keeper_delta"]) > 1  # scene first: the words stream
    await game.cast.close()


def test_the_reader_holds_the_words_until_the_scene_is_checked():
    import asyncio

    seen, shown = [], []

    async def show(piece):
        shown.append(piece)

    def check(raw):
        seen.append(raw)
        return raw.get("here") == ["a"]

    async def feed(text: str, reader: _Telling) -> None:
        for i in range(0, len(text), 3):
            await reader(text[i:i + 3])

    good = _Telling(check, show)
    asyncio.run(feed('{"scene": {"here": ["a"]}, "text": "\\n你好，\\"朋友\\"\\u4e16界"}', good))
    assert seen == [{"here": ["a"]}] and "".join(shown) == '你好，"朋友"世界' and good.shown and good.first
    bad, shown[:] = _Telling(check, show), []
    asyncio.run(feed('{"scene": {"here": ["b"]}, "text": "不该出现"}', bad))
    assert not shown and not bad.shown
    late = _Telling(check, show)
    asyncio.run(feed('{"text": "先写的字", "scene": {"here": ["a"]}}', late))
    assert not shown and not late.first


async def test_a_resumed_turn_lands_its_telling_without_asking_the_keeper_again(fake):
    game, events = made("square")
    st = game.state
    turn = acted(game, "我和布道者去钟楼", 2)
    st.pending_turn.update(input="我和布道者去钟楼")
    saved: list[str] = []

    def checkpoint():
        saved.append(st.model_dump_json())
        receipt = st.pending_turn.get("keeper", {})
        if "text" in receipt and not receipt.get("landed"):
            raise RuntimeError("断电了")  # the telling is in the journal; nothing of it has landed

    game.checkpoint = checkpoint
    SCRIPT.append(telling("布道者跟着你进了钟楼。", room="tower", here=["preacher", "warden"], follow=["preacher"]))
    with pytest.raises(RuntimeError):
        await game.narrate(turn)
    calls = len(SEEN)
    again = GameState.model_validate_json(saved[-1])  # the save as the power went
    assert again.location == "square" and "keeper" in again.pending_turn
    resumed, events = made(state=again)
    text = await resumed.narrate(turn)  # the script is empty: a call would fail
    assert len(SEEN) == calls and text == "布道者跟着你进了钟楼。" and told(events) == text
    assert again.location == "tower" and resumed.world.rec.entities["preacher"].following == again.investigator.id
    assert [e.text for e in again.log].count(text) == 1
    await game.cast.close()
    await resumed.cast.close()


async def test_telling_it_again_writes_the_words_only(fake):
    game, _ = made("square")
    st, w = game.state, game.world
    turn = acted(game, "我请布道者陪我走走", 1)
    SCRIPT.append(telling("布道者合上册子，跟在你身边。", here=["preacher"], follow=["preacher"], now="布道者等你开口"))
    await game.narrate(turn)
    journal = len(w.rec.journal)
    SCRIPT.append("布道者合上册子，一言不发地跟着你。")
    assert await game.retell("短一点") == "布道者合上册子，一言不发地跟着你。"
    assert SEEN[-1]["schema"] is None and "场面已经记下，重讲不能改：街头布道者跟着调查员" in SEEN[-1]["messages"][-1]["content"]
    assert len(w.rec.journal) == journal and st.ongoing == "布道者等你开口"  # nothing settled twice
    assert w.rec.entities["preacher"].following == st.investigator.id
    await game.cast.close()


async def test_what_is_going_on_as_a_telling_ends_reaches_the_next_one(fake):
    game, _ = made("square")
    st = game.state
    SCRIPT.append(telling("布道者的声音越来越高，围观的人往后退。", here=["preacher"], now="布道者正对着钟楼高声布道"))
    await game.narrate(acted(game, "我站着听", 2))
    assert st.ongoing == "布道者正对着钟楼高声布道"
    nxt = acted(game, "我走近一点", 1)
    assert "【上一段叙述停在】布道者正对着钟楼高声布道" in game._report(nxt)
    assert "上一段叙述停在：布道者正对着钟楼高声布道" in game.cast.bearings(st)
    SCRIPT.append(telling("你挤到前排。", here=["preacher"]))
    await game.narrate(nxt)
    assert st.ongoing == ""  # a telling with nothing going on as it ends leaves nothing behind
    await game.cast.close()


async def test_an_old_save_plays_on(fake):
    game, _ = made("square")
    old = json.loads(game.state.model_dump_json())
    old["ongoing"] = "旧存档里的一句：钟声刚停"  # written by the old check, never read since
    old["transcripts"] = {"keeper": [{"role": "user", "content": "【第 1 轮】"}, {"role": "assistant", "content": "旧的叙述。"}]}
    old["narrator_context_version"] = 1
    old["pending_turn"] = {"input": "我四处看看", "talk_done": True}  # cut short mid-narration, before any receipt
    for key in ("pending_resolution", "progress", "plugin_panels"):
        old.pop(key, None)
    st = GameState.model_validate(old)
    loaded, _ = made(state=st)
    SCRIPT.append(telling("广场上的雾散了一些。", here=["preacher"], now="布道者在整理册子"))
    assert await loaded.narrate(acted(loaded, "我四处看看", 3)) == "广场上的雾散了一些。"
    assert st.ongoing == "布道者在整理册子" and st.pending_turn["keeper"]["landed"]
    assert [m["content"] for m in st.transcripts["keeper"] if m["role"] == "assistant"] == ["旧的叙述。", "广场上的雾散了一些。"]
    await loaded.cast.close()
    await game.cast.close()


async def test_a_save_from_the_mcp_days_loads_without_its_journal(fake):
    game, _ = made("square")
    old = json.loads(game.state.model_dump_json())
    old["pending_mcp"] = {"request": {"tool": "skill_check", "args": {"skill": "侦查"}}, "turn": {"checks": []}}
    st = GameState.model_validate_json(json.dumps(old, ensure_ascii=False))
    assert "pending_mcp" not in st.model_dump() and "pending_mcp" not in st.model_dump_json()
    assert st.model_dump() == game.state.model_dump()  # everything else as it was
    await game.cast.close()


async def test_a_keeper_that_answers_in_words_alone_still_tells_the_turn(fake):
    from .test_engine import FakeCast

    game, events = made("square")
    game.cast = FakeCast({})
    game.cast.world = game.world
    assert await game.narrate(acted(game, "我看看", 1)) == "叙事" and told(events) == "叙事"
    assert game.state.ongoing == "" and not [e for e in game.world.rec.journal if e.id.startswith("keeper:")]


async def test_json_mode_streams_the_telling_as_it_is_written():
    """DeepSeek's JSON mode (LangChainModel, mode "json") hands the JSON on piece by piece."""
    from langchain_core.messages import AIMessageChunk

    class Chat:
        def bind(self, **kw):
            return self

        async def astream(self, history):
            for piece in ['{"scene": {"here": []}, ', '"text": "雾', '散了。"}']:
                yield AIMessageChunk(content=piece)

    model = hm.LangChainModel.__new__(hm.LangChainModel)
    model.spec, model.system, model.label, model.provider, model.mode, model.chat = WRITER, "守秘人", "keeper", "deepseek", "json", Chat()
    pieces: list[str] = []

    async def show(piece):
        pieces.append(piece)

    reply = await model._call([{"role": "user", "content": "讲"}], schema={"title": "Telling"}, images=None, on_delta=show)
    assert len(pieces) == 3 and reply.data == {"scene": {"here": []}, "text": "雾散了。"}


async def test_a_telling_that_never_gives_its_scene_is_held_to_the_record(fake):
    """Twice no scene at all: its words would have the warden go with them while the record has
    nobody following (the review of 2026-10-20). Asked again for the scene, then held to the record
    as it is: the keeper writes the words only, and neither draft is shown."""
    game, events = made("tower")
    turn = acted(game, "我下楼，叫守钟人一起", 2)
    bare = {"text": "守钟人跟着你下了楼。"}
    SCRIPT.extend([bare] * 4 + ["你走到楼梯口回头看，守钟人还坐在那里，没有起身。"])  # each call is repaired once (harness)
    text = await game.narrate(turn)
    assert text.startswith("你走到楼梯口回头看") and told(events) == text
    assert "【上一稿没有 scene】" in SEEN[2]["messages"][-1]["content"]
    assert SEEN[-1]["schema"] is None and "【场面已经定了】" in SEEN[-1]["messages"][-1]["content"]
    warden = game.world.rec.entities["warden"]
    assert warden.at == "tower" and not warden.following
    await game.cast.close()
