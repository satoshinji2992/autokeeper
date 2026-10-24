"""A telling's readers do not become its actors; NPC speech keeps its own addressee.

Scripted output verifies the real prompts, delivery and world state. It does not establish
that a live model will consistently follow the narrative reference instructions.
"""

from __future__ import annotations

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine.game import Turn

from .test_keeper_tools_execution import final, role_calls, voice
from .test_personal_check_results import close, run, table, wire
from .test_scene import SCRIPT, SEEN, sandbox  # noqa: F401


def current_prompt(call: dict) -> str:
    return call["messages"][-1]["content"]


def block(text: str, heading: str) -> str:
    """Read the actual current scope, excluding names elsewhere in the full KP context."""
    assert heading in text
    return text.split(heading, 1)[1].split("【", 1)[0]


def acted(hub, summary: str) -> Turn:
    st = hub.state
    return Turn(action=Action(kind="use", summary=summary), ruling=Ruling(needs_roll=False), interval={
        "actor": st.investigator.id, "start": st.minutes, "end": st.minutes,
        "participants": [st.investigator.id], "origin": st.location, "destination": st.location,
    })


def positions(hub) -> dict[str, str]:
    return {person.id: hub.game.world.place(person.id) for person in hub.state.investigators}


async def test_one_persons_public_action_has_one_actor_and_all_its_actual_readers(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    first, second, third = hub.state.investigators
    before = positions(hub)
    text = f"{first.name}推开窗户，冷风吹动桌上的纸。"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": f"{first.name}推开窗户", "target": "窗户"}},
        {"needs_roll": False, "result_scope": "scene", "minutes": 0},
        final(text),
    ])
    try:
        assert not await run(hub, {"input": "我推开窗户。"}) and not SCRIPT
        call, = role_calls("HostedReply")
        prompt = current_prompt(call)
        actor = block(prompt, "【本段行动者】")
        participants = block(prompt, "【本段实际参与者】")
        perspective = block(prompt, "【本次正文视角】")
        assert first.name in actor and first.id in actor
        assert first.id in participants and second.id not in participants and third.id not in participants
        assert all(person.name in perspective and person.id in perspective for person in hub.state.investigators)
        assert all(text in wire(messages, client) for client in ("a", "b", "c", "watch"))
        entry, = (entry for entry in hub.state.log if entry.kind == "keeper")
        assert entry.text == text and entry.audience is None
        assert positions(hub) == before
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == 1 and len(SEEN) == 3
    finally:
        await close(hub)


async def test_a_saved_personal_recipient_is_not_replaced_by_the_current_active_player(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    first, second, _ = hub.state.investigators
    fake.queue(1)
    original = await hub.game.resolve(
        Action(kind="investigate", summary=f"{first.name}辨认窗上的小字", target="窗上的小字"),
        Ruling(needs_roll=True, skill="侦查", result_scope="personal", minutes=0),
    )
    report = hub.game._report(original)
    before = positions(hub)
    hub.state.active = 1
    text = "你终于辨认出玻璃角落的三个字。"
    SCRIPT.append({"scene": {}, "text": text})
    try:
        await hub.game._narrate(report, turn=original, _receipt={"audience": [first.id]}, _tools=False)
        call, = role_calls("Telling")
        perspective = block(current_prompt(call), "【本次正文视角】")
        assert first.name in perspective and first.id in perspective
        assert second.name not in perspective and second.id not in perspective
        assert text in wire(messages, "a")
        assert text not in wire(messages, "b") + wire(messages, "c") + wire(messages, "watch")
        entry, = (entry for entry in hub.state.log if entry.kind == "keeper")
        assert entry.audience == [first.id] and positions(hub) == before
        assert hub.state.investigator.id == second.id and len(SEEN) == 1 and not SCRIPT
    finally:
        await close(hub)


async def test_combined_same_scene_reports_keep_each_actor_after_focus_changes(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    game, st = hub.game, hub.state
    first, second, _ = st.investigators
    before = positions(hub)
    game.holding, st.turn = True, 1
    first_turn = acted(hub, f"{first.name}看窗外")
    try:
        await game.narrate(first_turn)
        assert not SEEN and len(st.held) == 1 and st.held[0]["actor"] == first.id
        st.active, st.turn, game.holding = 1, 2, False
        second_turn = acted(hub, f"{second.name}整理桌面")
        text = f"{first.name}站在窗边，{second.name}将桌上的纸叠好。"
        SCRIPT.append({"scene": {}, "text": text})
        await game.narrate(second_turn)
        call, = role_calls("Telling")
        prompt = current_prompt(call)
        assert prompt.count("【本段行动者】") == 2
        segments = prompt.split("【本段行动者】")[1:]
        assert first.name in segments[0].split("【", 1)[0] and first.id in segments[0].split("【", 1)[0]
        assert second.name in segments[1].split("【", 1)[0] and second.id in segments[1].split("【", 1)[0]
        combined = block(prompt, "【同一现场同时行动、已经结算的经过】")
        assert "逐段" in combined and "行动者" in combined
        perspective = block(prompt, "【本次正文视角】")
        assert all(person.id in perspective for person in st.investigators)
        assert all(text in wire(messages, client) for client in ("a", "b", "c", "watch"))
        assert not st.held and len(SEEN) == 1 and positions(hub) == before and not SCRIPT
    finally:
        await close(hub)


async def test_npcs_second_person_speech_is_unchanged_for_every_public_listener_without_extra_calls(fake, monkeypatch):
    hub, messages = table(fake, monkeypatch)
    first = hub.state.investigators[0]
    before = positions(hub)
    spoken = "你先坐一会儿，您要的热水已经烧好了。"
    text = f"{first.name}把椅子移近桌边，炉上的水壶冒着热气。"
    SCRIPT.extend([
        {"action": {"kind": "use", "summary": f"{first.name}整理桌边的椅子", "target": "椅子"}},
        {"needs_roll": False, "result_scope": "scene", "minutes": 0},
        voice("warden", "对眼前这名调查员说热水已好，其他在场人也听得见。"),
        {"text": spoken},
        final(text),
    ])
    try:
        assert not await run(hub, {"input": "我把椅子移到桌边。"}) and not SCRIPT
        for client in ("a", "b", "c", "watch"):
            assert spoken in wire(messages, client) and text in wire(messages, client)
        entry, = (entry for entry in hub.state.log if entry.kind == "npc")
        assert entry.text == spoken and entry.audience is None
        assert len(role_calls("Plan")) == len(role_calls("Ruling")) == len(role_calls("Dialogue")) == 1
        assert len(role_calls("HostedReply")) == 2 and len(SEEN) == 5
        assert positions(hub) == before
    finally:
        await close(hub)
