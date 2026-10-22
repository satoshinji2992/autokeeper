"""Personal observations travel with the keeper's accepted telling, never its public stream."""

from __future__ import annotations

import json

import pytest

from autokeeper.agents.cast import _asides, _words
from autokeeper.engine import audience
from autokeeper.engine.state import GameState

from .test_scene import SCRIPT, SEEN, acted, made, sandbox, told  # noqa: F401


async def test_private_observation_uses_the_same_call_but_never_the_public_stream():
    game, events = made()
    who = game.state.investigator.id
    turn = acted(game, "我听着", 0)
    SCRIPT.append({"scene": {}, "text": "屋里静了下来。",
                   "asides": [{"to": [who], "text": "只有你听见钟里传来自己的名字。"}]})
    assert await game.narrate(turn) == "屋里静了下来。"
    assert len(SEEN) == 1
    assert told(events) == "屋里静了下来。"
    private = [e for e in game.state.log if not audience.public(e)]
    assert len(private) == 1 and private[0].audience == [who]
    assert [e for e in events if e["type"] == "aside"] == [
        {"type": "aside", "audience": [who], "text": private[0].text, "who": "守秘人"}]
    assert private[0].text not in str([e for e in events if e["type"].startswith("keeper_")])
    await game.close()


async def test_unaccepted_tellings_do_not_deliver_their_private_observations():
    game, events = made()
    who = game.state.investigator.id
    turn = acted(game, "我听着", 0)
    SCRIPT.extend([
        {"scene": {"here": ["does_not_exist"]}, "text": "废弃的正文。",
         "asides": [{"to": [who], "text": "废弃的私信。"}]},
        {"scene": {}, "text": "成立的正文。", "asides": [{"to": [who], "text": "成立的私信。"}]},
    ])
    await game.narrate(turn)
    assert len(SEEN) == 2
    assert "废弃" not in str(events) + str(game.state.log)
    assert [e["text"] for e in events if e["type"] == "aside"] == ["成立的私信。"]
    await game.close()


async def test_recovery_after_private_registration_does_not_repeat_the_call_or_log():
    game, _ = made()
    st = game.state
    turn = acted(game, "我听着", 0)
    st.pending_turn = {"input": "我听着"}
    saved = []

    def checkpoint():
        saved.append(st.model_dump_json())
        if st.pending_turn.get("keeper", {}).get("asides_landed"):
            raise RuntimeError("投递前断开")

    game.checkpoint = checkpoint
    SCRIPT.append({"scene": {}, "text": "钟声停了。",
                   "asides": [{"to": [st.investigator.id], "text": "你独自听见最后一声。"}]})
    with pytest.raises(RuntimeError, match="投递前断开"):
        await game.narrate(turn)
    assert len(SEEN) == 1
    resumed, events = made(state=GameState.model_validate_json(saved[-1]))
    await resumed.narrate(turn)
    assert len(SEEN) == 1
    private = [e for e in resumed.state.log if not audience.public(e)]
    assert [e.text for e in private] == ["你独自听见最后一声。"]
    assert len([e for e in events if e["type"] == "aside"]) == 1
    await game.close()
    await resumed.close()


def test_malformed_or_unknown_private_recipients_never_become_a_public_fallback():
    game, _ = made()
    who = game.state.investigator.id
    assert _asides([{"to": [who, "unknown"], "text": "秘密"}, {"to": [], "text": "秘密"}], game.state) == []
    assert _asides([{"to": [who, who], "text": " 秘密 "}], game.state) == [{"to": [who], "text": "秘密"}]
    for prefix in ("", "```json\n"):
        with pytest.raises(ValueError, match="叙述"):
            _words(prefix + json.dumps({"asides": [{"to": [who], "text": "秘密"}]}))


async def test_shared_memory_compaction_retains_private_originals_without_summarizing_them():
    game, _ = made()
    st = game.state
    who = st.investigator.id
    st.add("keeper", "你认出了匿名写信人的字迹。", audience=[who])
    for n in range(48):
        st.add("keeper", f"公开经过{n}。")
    seen = []

    async def chronicle(state, entries):
        seen.append(entries)
        return "公开经过。"

    game.cast.chronicle = chronicle
    await game.remember()
    assert len(seen) == 1 and "字迹" not in seen[0]
    assert "字迹" not in st.memory
    assert [e.text for e in st.private_history] == ["你认出了匿名写信人的字迹。"]
    again = GameState.model_validate_json(st.model_dump_json())
    assert audience.personal(again, who)[0].text == st.private_history[0].text
    assert not any("字迹" in e.text for e in audience.log(again))
    await game.close()
