"""Private keeper correspondence and media cross the real delivery boundary, with no paid calls."""

from __future__ import annotations

import copy
import json

import pytest
from PIL import Image

from autokeeper import config
from autokeeper.agents.cast import Action, BeforeTravel, Cast, _personal_situation
from autokeeper.engine import audience, fog, perception, presentation
from autokeeper.engine.game import Game, Turn
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Handout, Module
from autokeeper.net import media
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules
from autokeeper.tui.screens.game import GameScreen
from autokeeper.tui.screens.net import NetGameScreen
from autokeeper.world import knowledge

from .test_engine import FakeCast
from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401
from .test_net_private import private_hub as _private_hub

private_hub = _private_hub


def private_paper(hub, tmp_path):
    picture = tmp_path / "undisclosed-original.jpg"
    Image.new("RGB", (32, 32), "purple").save(picture)
    hub.m.handouts.append(Handout(id="personal_letter", title="单独收到的信", text="零点，独自到东门。", images=["secret_art"]))
    hub.m.pictures["secret_art"] = str(picture)
    return {"ref": "handout:personal_letter", "to": ["lin"]}


async def test_a_private_material_and_its_image_never_enter_the_shared_book_or_spectator_payload(private_hub, tmp_path):
    hub, messages = private_hub
    raw = private_paper(hub, tmp_path)
    await hub.game.present([raw], key="one-letter")
    assert [cid for cid, _ in messages] == ["b"]
    sent = messages[0][1]["ev"]
    assert sent["type"] == "presentation" and sent["payload"]["picture_data"]
    encoded = json.dumps(sent, ensure_ascii=False)
    assert "undisclosed-original" not in encoded and "secret_art" not in encoded and "ref" not in sent
    assert not hub.state.handouts and not hub.state.clues
    assert [(d.who, d.audience) for d in hub.state.deeds] == [("lin", ["lin"])]
    assert "零点，独自到东门" not in json.dumps(hub._welcome("a"), ensure_ascii=False)
    assert "零点，独自到东门" not in json.dumps(hub._welcome("watch"), ensure_ascii=False)
    assert "personal_letter" not in json.dumps(hub._view("zhouheng"), ensure_ascii=False)
    assert "零点，独自到东门" in _personal_situation(hub.state, "lin", "你在广场。")
    assert "零点，独自到东门" not in _personal_situation(hub.state, "zhouheng", "你在广场。")


async def test_private_materials_survive_serialized_reload_and_restore_only_to_their_seat(private_hub, tmp_path):
    hub, messages = private_hub
    await hub.game.present([private_paper(hub, tmp_path)], key="one-letter")
    saved = GameState.model_validate_json(hub.state.model_dump_json())
    hub.state = hub.game.state = saved
    messages.clear()
    await hub._sit("b", "lin")
    restored = next(m for cid, m in messages if cid == "b" and m["t"] == "media_sync")
    assert len(restored["papers"]) == 1 and restored["slots"]["overlay"]["id"] == presentation.opaque("one-letter:0", "delivery")
    unpacked = NetGameScreen._media_unpacked(restored["papers"][0])
    assert unpacked["payload"]["pictures"] and not unpacked["payload"].get("picture_data")
    assert not any("零点，独自到东门" in json.dumps(m, ensure_ascii=False) for cid, m in messages if cid != "b")
    assert not hub._media_view("zhouheng")["papers"] and not hub._media_view(None)["papers"]


async def test_media_keeps_clears_and_does_not_remove_the_received_document(private_hub, tmp_path):
    hub, _ = private_hub
    await hub.game.present([private_paper(hub, tmp_path)], key="show")
    await hub.game.present([], key="keep")
    assert presentation.current(hub.state, "lin")["overlay"]["id"] == "show:0"
    await hub.game.present([{"action": "clear", "to": ["lin"]}], key="clear")
    assert not presentation.current(hub.state, "lin") and len(presentation.papers(hub.state, "lin")) == 1
    assert not hub._media_view("lin")["slots"] and len(hub._media_view("lin")["papers"]) == 1
    assert not hub._media_view("zhouheng")["papers"]


async def test_an_empty_pending_image_is_not_reported_as_displayed_and_can_finish_later(private_hub, tmp_path):
    hub, messages = private_hub
    hub.m.art["pending_art"] = ""
    hub.m.pictures["pending_art"] = str(tmp_path / "not-drawn.jpg")
    action = {"ref": "image:pending_art", "to": ["lin"], "slot": "scene"}
    assert await hub.game.present([action], key="waiting")
    assert not messages and not hub.state.presentations
    assert "尚未准备好" in presentation.index(hub.m, hub.state)
    hub.m.art["pending_art"] = "[ finished ]"
    assert not await hub.game.present([action], key="finished")
    assert not hub.state.presentation_issues and [cid for cid, _ in messages] == ["b"]


async def test_a_replacement_for_the_same_audience_slot_clears_the_old_unshown_request(private_hub):
    hub, _ = private_hub
    assert await hub.game.present([{"ref": "image:missing", "to": ["lin"], "slot": "scene"}], key="missing")
    hub.m.art["ready_picture"] = "[ a lantern ]"
    assert not await hub.game.present([{"ref": "image:ready_picture", "to": ["lin"], "slot": "scene"}], key="replacement")
    assert not hub.state.presentation_issues


async def test_rejected_perception_reaches_the_keeper_and_is_cleared_by_a_corrected_update(private_hub):
    hub, _ = private_hub
    change = {"to": ["lin"], "target": "unknown_person", "name": "门口的影子", "reason": "未消退的幻觉"}
    hub.state.pending_turn = {"input": "等待", "keeper": {"text": "脚步声停了。", "perceptions": [change]}}
    await hub.game._narrate("")
    assert hub.state.pending_turn["keeper"]["perception_problems"]
    assert "unknown_person" in presentation.index(hub.m, hub.state)
    assert "unknown_person" not in hub.state.perceptions.get("lin", {})
    change = {**change, "target": "warden"}
    hub.state.pending_turn = {"input": "抬头看", "keeper": {"text": "影子站在门口。", "perceptions": [change]}}
    await hub.game._narrate("")
    assert hub.state.perceptions["lin"]["warden"].name == "门口的影子"
    assert "perception" not in hub.state.presentation_issues


@pytest.mark.parametrize("raw", [
    {"ref": "/etc/passwd"}, {"ref": "image:/etc/passwd"}, {"ref": "https://example.com/a.jpg"},
    {"ref": "handout:missing"}, {"ref": "scene:missing"}, {"ref": "portrait:missing"},
    {"ref": "scene:square", "to": ["not_a_player"]}, {"action": "clear", "to": []},
])
async def test_unregistered_resources_or_recipients_are_not_delivered(private_hub, raw):
    hub, messages = private_hub
    assert await hub.game.present([raw], key="invalid")
    assert not messages and not hub.state.presentations
    assert hub.state.presentation_issues
    assert "尚未展示" in presentation.index(hub.m, hub.state)


async def test_private_keeper_input_uses_the_connection_seat_and_retries_idempotently(private_hub, tmp_path, monkeypatch):
    hub, messages = private_hub
    paper = private_paper(hub, tmp_path)
    calls = []

    async def reply(state, who, text, *, context=""):
        calls.append((who, text))
        return {"text": "你可以私下提出这个问题，但移动尚未结算。", "media": [paper | {"to": ["zhouheng"]}]}

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    before = (hub.state.turn, hub.state.minutes, copy.deepcopy(hub.state.record))
    msg = {"t": "kp_private", "text": "我想独自到东门。", "request": "letter-1", "from": "zhouheng"}
    await hub._on("b", msg)
    assert [msg["ev"]["type"] for _, msg in messages] == ["private_input"]
    assert not hub.pool and not hub.early and hub.private_requests.qsize() == 1
    await hub._privates()
    assert calls == [("lin", msg["text"])] and {cid for cid, _ in messages} == {"b"}
    assert all(e.audience == ["lin"] for e in hub.state.log)
    assert (hub.state.turn, hub.state.minutes, hub.state.record) == before
    assert "东门" not in json.dumps(hub._view("zhouheng"), ensure_ascii=False)
    assert all(p["audience"] == ["lin"] for p in hub.state.presentations)  # model cannot broaden a private reply
    count = len(hub.state.log)
    messages.clear()
    await hub._on("b", msg)
    await hub._privates()
    assert len(calls) == 1 and len(hub.state.log) == count and len(hub.state.presentations) == 1
    assert all(m["ev"]["type"] == "presentation" for _, m in messages)
    assert not [e for e in audience.log(hub.state) if e.text]


async def test_a_spectator_cannot_send_private_keeper_requests(private_hub):
    hub, messages = private_hub
    await hub._on("watch", {"t": "kp_private", "text": "请告诉我秘密。", "from": "lin"})
    assert hub.private_requests.empty() and len(messages) == 1 and messages[0][0] == "watch"
    assert not hub.state.log


async def test_private_keeper_failures_only_reach_the_requesting_seat(private_hub, monkeypatch):
    hub, messages = private_hub

    async def fail(*args, **kwargs):
        raise RuntimeError("do not broadcast private question or provider details")

    monkeypatch.setattr(hub.cast, "private_keeper", fail, raising=False)
    await hub._on("b", {"t": "kp_private", "text": "密码藏在我衣袋里。", "request": "failure"})
    await hub._privates()
    assert {cid for cid, _ in messages} == {"b"}
    assert not any("provider" in json.dumps(m) for _, m in messages)


@pytest.mark.parametrize("stopped_at", ["reply_checkpoint", "media_emit"])
async def test_a_private_reply_resumes_undelivered_attachments_after_reload(
    private_hub, tmp_path, monkeypatch, fake, stopped_at,
):
    hub, _ = private_hub
    paper = private_paper(hub, tmp_path)
    calls, snapshots = [], []

    async def reply(state, who, text, *, context=""):
        calls.append((who, text))
        return {"text": "这是单独交给你的信。", "media": [paper]}

    def checkpoint():
        receipt = hub.state.private_messages.get("lin:resume-letter", {})
        if stopped_at == "reply_checkpoint" and receipt.get("reply_shown") and not hub.state.presentations:
            snapshots.append(hub.state.model_dump_json())
            raise OSError("process stopped after text delivery")

    async def emit(ev):
        if stopped_at == "media_emit" and ev["type"] == "presentation":
            snapshots.append(hub.state.model_dump_json())
            raise OSError("display disconnected")
        await hub._event(ev)

    monkeypatch.setattr(hub.cast, "private_keeper", reply, raising=False)
    hub.game.checkpoint = checkpoint
    with pytest.raises(OSError):
        await hub.game.private_message("lin", "请单独把信给我。", request="resume-letter", emit=emit)

    restored = GameState.model_validate_json(snapshots[-1])
    assert restored.private_messages["lin:resume-letter"]["reply_shown"]
    assert not restored.private_messages["lin:resume-letter"].get("delivered")
    resumed = Hub(EXAMPLE, state=restored, cast=hub.cast, rules=Rules(dice=fake))
    resumed.m = resumed.game.m = hub.m
    resumed.clients, resumed.sits = hub.clients.copy(), hub.sits.copy()
    resumed.game.checkpoint = lambda: None
    messages = []

    async def send(cid, msg):
        messages.append((cid, copy.deepcopy(msg)))

    monkeypatch.setattr(resumed, "_send", send)
    monkeypatch.setattr(resumed, "_save", lambda: None)
    assert resumed.private_requests.qsize() == 1
    await resumed._privates()
    assert calls == [("lin", "请单独把信给我。")]
    assert len(restored.presentations) == 1
    assert restored.private_messages["lin:resume-letter"]["delivered"]
    assert len(restored.log) == 3  # accepted question, keeper reply, received document
    assert [cid for cid, _ in messages] == ["b"]
    assert messages[0][1]["ev"]["type"] == "presentation"
    assert messages[0][1]["ev"]["payload"]["handout"]["text"] == "零点，独自到东门。"

    complete = GameState.model_validate_json(restored.model_dump_json())
    reloaded = Hub(EXAMPLE, state=complete, cast=hub.cast, rules=Rules(dice=fake))
    assert reloaded.private_requests.empty()


async def test_keeper_media_use_separate_receipts_for_two_responses_in_the_same_turn(fake):
    module = Module.load(EXAMPLE)
    module.art["presentation_test"] = "[ a dark room ]"
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, minds=0,
                             visual_limit=0, imagegen=False, search="none"), module)
    events = []

    async def emit(ev):
        events.append(ev)

    game = Game(module, state, cast, Rules(dice=fake), emit=emit)
    turn = Turn(action=Action(kind="daily", summary="查看眼前的房间"))
    here = cast.present(state.location)
    try:
        SCRIPT.append({"scene": {"here": here}, "text": "你看到了阴暗的房间。",
                       "media": [{"ref": "image:presentation_test", "slot": "scene"}]})
        await game._narrate("环顾房间", turn=turn)
        SCRIPT.append({"scene": {"here": here}, "text": "画面回到广场。", "media": [{"action": "clear", "slot": "scene"}]})
        await game._narrate("继续说明", turn=turn)
        assert len(state.presentations) == 2 and state.presentations[0]["id"] != state.presentations[1]["id"]
        assert not presentation.current(state, state.investigator.id)
        assert any("image:presentation_test" in m["content"] for row in SEEN for m in row["messages"])
    finally:
        await game.close()


async def test_a_private_keeper_call_uses_isolated_transcripts_and_the_keeper_role(fake):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, minds=0,
                             visual_limit=0, imagegen=False, search="none"), module)
    game = Game(module, state, cast, Rules(dice=fake))
    before = copy.deepcopy(state.transcripts)
    SCRIPT.append({"text": "这次沟通不推进时间，也没有替你离开现场。"})
    try:
        await game.private_message(state.investigator.id, "我是否还站在原地？", request="one")
        assert len(SEEN) == 1 and state.transcripts == before
        assert "不执行秘密物理行动" in SEEN[0]["system"]
        assert f"private_keeper:{state.investigator.id}" in cast.sessions
        assert not any(e.audience is None for e in state.log)
    finally:
        await game.close()


async def test_a_split_player_private_reply_reads_their_own_place_and_current_question_once(fake):
    module = Module.load(EXAMPLE)
    module.location("square").keeper = "主队地点独有的幕后机关。"
    module.location("office").keeper = "报社独有的幕后须知：档案柜钥匙由主编保管。"
    first = module.pregens[0].model_copy(deep=True)
    second = first.model_copy(update={"id": "split_reader", "name": "留在报社的记者"}, deep=True)
    state = Game.new(module, [first, second])
    state.location = "square"
    state.apart[second.id] = "office"
    question = "这里的柜子锁着吗？"
    state.add("system", f"对守秘人（私信）：{question}", who=second.name, audience=[second.id])
    state.add("keeper", "你还未检查过柜子。", who="守秘人", audience=[second.id])
    state.add("keeper", "另一人的私密约定，不应出现在这封信里。", who="守秘人", audience=[first.id])
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, minds=0,
                             visual_limit=0, imagegen=False, search="none"), module)
    game = Game(module, state, cast, Rules(dice=fake))
    SCRIPT.append({"text": "你仍在报社，柜子的状态需要正式检查。"})
    try:
        await game.private_message(second.id, question, request="split-scene")
        prompt = SEEN[0]["messages"][-1]["content"]
        assert "报社独有的幕后须知" in prompt and "主队地点独有的幕后机关" not in prompt
        assert "此地（报社）的守秘人须知" in prompt
        assert prompt.count(question) == 2  # one old exchange and this new message, not the new log twice
        assert "你还未检查过柜子" in prompt and "另一人的私密约定" not in prompt
        assert state.location == "square" and state.apart[second.id] == "office"
    finally:
        await game.close()


async def test_a_real_keeper_response_can_introduce_a_person_and_set_their_personal_appearance(fake):
    module = Module.load(EXAMPLE)
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, minds=0,
                             visual_limit=0, imagegen=False, search="none"), module)
    game = Game(module, state, cast, Rules(dice=fake))
    SCRIPT.append({"scene": {"people": [{"id": "visitor", "name": "访客", "look": "灰色外套"}],
                             "here": [*cast.present(state.location), "visitor"]},
                   "text": "门边传来脚步声。", "asides": [{"to": [state.investigator.id], "text": "你看见一个扭曲的人影。"}],
                   "perceptions": [{"to": [state.investigator.id], "target": "visitor", "name": "扭曲的人影",
                                    "side": "foe", "reason": "短暂的幻觉"}]})
    try:
        await game._narrate("有人走来", turn=Turn(action=Action(kind="daily", summary="站在广场上")))
        assert len(SEEN) == 1 and "visitor" in game.world.rec.entities
        assert state.perceptions[state.investigator.id]["visitor"].name == "扭曲的人影"
        assert state.log[-1].audience == [state.investigator.id]
    finally:
        await game.close()


async def test_a_failed_media_delivery_resumes_the_same_keeper_receipt_without_asking_again(fake):
    module = Module.load(EXAMPLE)
    module.art["resume_image"] = "[]"
    state = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    state.pending_turn = {"input": "我看向房间"}
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}, minds=0,
                             visual_limit=0, imagegen=False, search="none"), module)

    async def interrupted(ev):
        if ev["type"] == "presentation":
            raise OSError("display disconnected")

    game = Game(module, state, cast, Rules(dice=fake), emit=interrupted)
    SCRIPT.append({"kind": "tell", "telling": {
        "scene": {"here": cast.present(state.location)}, "text": "灯光映出一面墙。",
        "media": [{"ref": "image:resume_image", "slot": "scene"}]}})
    turn = Turn(action=Action(kind="daily", summary="我看向房间"))
    try:
        with pytest.raises(OSError):
            await game._narrate("看房间", turn=turn)
        saved_id = state.presentations[0]["id"]
        restored = GameState.model_validate_json(state.model_dump_json())

        async def resumed_emit(ev):
            if ev["type"] == "presentation":
                assert ev["id"] == saved_id

        resumed = Game(module, restored, cast, Rules(dice=fake), emit=resumed_emit)
        try:
            await resumed._narrate("看房间", turn=turn)
            assert len(SEEN) == 1 and len(restored.presentations) == 1
            assert sum(e.kind == "keeper" for e in restored.log) == 1
        finally:
            await resumed.close()
    finally:
        await game.close()


async def test_a_cleared_appearance_receives_the_previously_hidden_picture_without_resending_to_everyone(private_hub, tmp_path):
    hub, messages = private_hub
    image = tmp_path / "real.jpg"
    Image.new("RGB", (32, 32), "green").save(image)
    hub.state.find("zhouheng").portrait = "real_face"
    hub.state.find("lin").portrait = "lin_face"
    hub.state.find("chen").portrait = "chen_face"
    hub.m.pictures["real_face"] = str(image)
    perception.apply(hub.game, [{"to": ["lin"], "target": "zhouheng", "name": "黑影", "reason": "幻觉"}])
    await hub._picture("real_face")
    assert {cid for cid, _ in messages} == {"a", "c", "watch"}
    messages.clear()
    perception.apply(hub.game, [{"to": ["lin"], "target": "zhouheng", "clear": True, "reason": "恢复清醒"}])
    await hub._picture("real_face")
    assert [cid for cid, _ in messages] == ["b"]


async def test_a_personal_appearance_picture_is_sent_only_to_its_viewer_and_restored_on_reconnect(private_hub, tmp_path):
    hub, messages = private_hub
    image = tmp_path / "monster.jpg"
    Image.new("RGB", (32, 32), "red").save(image)
    hub.m.pictures["monster_face"] = str(image)
    perception.apply(hub.game, [{"to": ["lin"], "target": "zhouheng", "name": "影子", "portrait": "monster_face", "reason": "幻觉"}])
    await hub._personal_faces("b")
    assert [cid for cid, _ in messages] == ["b"]
    assert messages[0][1]["ev"]["picture_data"]
    assert "monster_face" in hub._welcome("b")["picture_data"]
    assert "monster_face" not in hub._welcome("a")["picture_data"]


@pytest.mark.parametrize("costs", [False, True])
async def test_a_private_before_travel_reply_keeps_its_recipients_across_interruption_and_reload(fake, costs):
    module = Module.load(EXAMPLE)
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "friend", "name": "同伴"}, deep=True)
    state = Game.new(module, [lead, friend])
    state.location = "tower"
    state.pending_turn = {"input": "临行前私下询问"}
    clue = module.clue("c_diary")
    clue.npc = "warden"
    if costs:
        clue.sanity, clue.mythos = "1/1", 2
        fake.queue(20)
    module.npc("warden").clues.append(clue.id)
    module.npc("warden").gates.pop(clue.id, None)
    calls, events = [], []
    before_san = {inv.id: inv.san for inv in state.investigators}
    before_mythos = {inv.id: inv.value("克苏鲁神话") for inv in state.investigators}

    class Privately(FakeCast):
        async def npc(self, st, npc_id, message, *, travel=False, private=False):
            assert private
            calls.append(npc_id)
            self.told[npc_id] = [clue.id]
            return "密码是十七，别让别人听见。"

    cast = Privately({})

    async def interrupted(ev):
        events.append(ev)
        if ev["type"] == "npc":
            raise OSError("display disconnected")

    game = Game(module, state, cast, Rules(dice=fake), emit=interrupted)
    action = Action(kind="move", summary="向广场出发", destination="square",
                    before=[BeforeTravel(npc="warden", speech="私下说说密码", private=True)])
    resumed = None
    try:
        with pytest.raises(OSError):
            await game.prepare(action)
        assert events[-1]["audience"] == [lead.id]
        assert next(e for e in state.log if e.kind == "npc").audience == [lead.id]
        assert clue.id not in state.clues and not state.clue_settled  # Delivery interrupted before formal settlement.
        saved = GameState.model_validate_json(state.model_dump_json())

        async def emit(ev):
            events.append(ev)

        resumed = Game(module, saved, cast, Rules(dice=fake), emit=emit)
        await resumed.prepare(action)
        await resumed.prepare(action)  # Reusing the completed preparation cannot award its information twice.
        assert calls == ["warden"]
        assert all(ev["audience"] == [lead.id] for ev in events if ev["type"] in ("npc", "presentation"))
        assert sum(e.kind == "npc" for e in saved.log) == 1
        # Internal progress records a real private discovery; personal knowledge controls publication.
        assert clue.id in saved.clues and saved.clue_settled[clue.id] == [lead.id]
        assert not saved.handouts and not presentation.papers(saved, lead.id)
        assert not presentation.papers(saved, friend.id)
        assert lead.id in saved.record.facts[f"clue:{clue.id}"].known_by
        assert "players" not in saved.record.facts[f"clue:{clue.id}"].known_by
        assert knowledge.clue_known(saved, clue.id, lead.id)
        assert not knowledge.clue_known(saved, clue.id, friend.id)
        assert clue.id in fog.personal(module, saved, lead.id).clues
        assert clue.id not in fog.personal(module, saved, friend.id).clues
        assert saved.find(lead.id).san == before_san[lead.id] - int(costs)
        assert saved.find(lead.id).value("克苏鲁神话") == before_mythos[lead.id] + 2 * int(costs)
        assert saved.find(friend.id).san == before_san[friend.id]
        assert saved.find(friend.id).value("克苏鲁神话") == before_mythos[friend.id]
        assert sum(e.kind == "sanity" for e in saved.log) == int(costs) and not fake.values
        assert "密码是十七" not in "\n".join(e.text for e in audience.log(saved, "friend"))
        projected = Hub(EXAMPLE, state=saved, cast=cast, rules=Rules(dice=fake))
        projected.sits = {lead.id: "lead-client", friend.id: "friend-client"}
        try:
            for data in (projected._view(friend.id), projected._welcome("friend-client"), projected._welcome("watch")):
                wire = json.dumps(data, ensure_ascii=False)
                assert "密码是十七" not in wire and clue.id not in wire
                assert clue.text not in wire and clue.handout not in wire
            assert "密码是十七" in json.dumps(projected._welcome("lead-client")["log"], ensure_ascii=False)
        finally:
            await projected.game.close()
    finally:
        if resumed is not None:
            await resumed.close()
        await game.close()


def test_ui_media_replays_do_not_reopen_an_overlay_and_clear_keeps_the_paper():
    from types import SimpleNamespace

    opened, scene = [], []
    app = SimpleNamespace(screen=None, push_screen=lambda modal: opened.append(modal), pop_screen=lambda: None)
    screen = SimpleNamespace(_media_slots={}, _media_papers={}, _media_seen=set(), _media_overlay=None,
                             app=app, story=SimpleNamespace(add=lambda *args, **kwargs: None),
                             _flash_off=lambda: None, _scene=lambda loc: scene.append(loc), state=SimpleNamespace(location="square"))
    ev = {"id": "paper", "slot": "overlay", "action": "show", "payload": {"kind": "handout",
          "handout": {"id": "letter", "title": "信", "text": "正文", "images": [], "kind": "信件"}}}
    GameScreen._present(screen, ev)
    GameScreen._present(screen, ev)  # repeated receipt or reconnect restoration
    assert len(opened) == 1
    GameScreen._present(screen, {"slot": "overlay", "action": "clear"})
    assert not screen._media_slots and screen._media_papers["letter"]["handout"]["text"] == "正文"
    GameScreen._present(screen, {"id": "view", "slot": "scene", "action": "show", "payload": {"kind": "image", "art": "[]"}})
    GameScreen._present(screen, {"slot": "scene", "action": "clear"})
    assert scene == ["square", "square"] and not screen._media_slots


@pytest.mark.parametrize("shape", ["face", "batch", "art"])
async def test_current_single_picture_and_batch_picture_envelopes_are_sent_and_cached(private_hub, tmp_path, shape):
    hub, _ = private_hub
    path = tmp_path / "face.jpg"
    Image.new("RGB", (32, 32), "blue").save(path)
    hub.m.pictures["current_picture"] = str(path)
    data = media.pack(str(path))
    if shape == "face":
        packet = {"t": "face", "key": "current_picture", "picture_data": data}
    elif shape == "batch":
        packet = {"t": "welcome", "picture_data": {"current_picture": data}}
    else:
        packet = {"t": "event", "ev": {"type": "art", "key": "current_picture", "picture_data": data}}
    received = []

    class Receiver:
        closed = False

        async def send_str(self, message):
            received.append(json.loads(message))

    hub.clients["a"] = Receiver()
    await Hub._send(hub, "a", packet)
    assert received == [packet] and hub.sent_to["a"]["current_picture"] == str(path)
