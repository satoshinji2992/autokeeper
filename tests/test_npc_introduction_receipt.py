"""Only an accepted, explicit NPC introduction becomes this reply's name receipt."""

from .test_harness import SCRIPT, SEEN, harness_sandbox  # noqa: F401
from .test_npc_acts import setup


async def test_npc_receipt_keeps_the_spoken_alias_instead_of_the_module_name(fake):
    game, cast = setup(fake)
    try:
        SCRIPT.append({"text": "叫我哈里就好，我替这里看钟。", "introduced_as": "  哈里  "})
        said = await cast.npc(game.state, "warden", "我该怎么称呼您？")
        assert said == "叫我哈里就好，我替这里看钟。"
        assert cast.introduced_as["warden"] == "哈里"
        assert cast.introduced_as["warden"] != game.m.npc("warden").name
        assert len(SEEN) == 1  # name reporting does not require another model pass
    finally:
        await game.close()


async def test_an_ordinary_reply_clears_the_previous_introduction_receipt(fake):
    game, cast = setup(fake)
    try:
        SCRIPT.extend([
            {"text": "叫我哈里。", "introduced_as": "哈里"},
            {"text": "楼梯在右边，上去时当心脚下。"},
        ])
        await cast.npc(game.state, "warden", "怎么称呼您？")
        assert cast.introduced_as["warden"] == "哈里"
        said = await cast.npc(game.state, "warden", "楼上怎么走？")
        assert said == "楼梯在右边，上去时当心脚下。"
        assert not cast.introduced_as.get("warden")
        assert len(SEEN) == 2
    finally:
        await game.close()


async def test_a_rejected_draft_cannot_publish_its_introduced_name(fake):
    game, cast = setup(fake)
    try:
        SCRIPT.extend([
            {"text": "我是钟楼管理员，我现在去报社。", "introduced_as": "钟楼管理员", "leave": "office"},
            {"text": "叫我哈里吧，我先留在这里。", "introduced_as": "哈里"},
        ])
        said = await cast.npc(game.state, "warden", "请问您是？")
        assert len(SEEN) == 2 and not cast.unsettled
        assert "先留在这里" in said and game.world.place("warden") == "tower"
        assert cast.introduced_as["warden"] == "哈里"
        assert "钟楼管理员" not in cast.introduced_as.values()
    finally:
        await game.close()


async def test_a_spoken_introduction_stands_when_an_unrelated_action_cannot_land(fake):
    game, cast = setup(fake)
    try:
        reply = {"text": "叫我哈里，我现在去报社。", "introduced_as": "哈里", "leave": "office"}
        SCRIPT.extend([reply, reply, reply])
        said = await cast.npc(game.state, "warden", "您怎么称呼，要去哪里？")
        assert len(SEEN) == 3 and cast.unsettled["warden"]
        assert said == reply["text"] and game.world.place("warden") == "tower"
        assert cast.introduced_as["warden"] == "哈里"
    finally:
        await game.close()
