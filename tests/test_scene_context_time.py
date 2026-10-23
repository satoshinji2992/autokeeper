"""Continuing scene cursors read shared records without importing later or remote observations."""

from __future__ import annotations

from types import SimpleNamespace

from autokeeper.agents.cast import _book
from autokeeper.world import context
from autokeeper.world.ops import AddFact, Move, Occur

from .test_scene import made, sandbox  # noqa: F401


class SceneCursor:
    """Only the read cursor contract; the real SceneView also routes writable turn state."""

    def __init__(self, root, at: int):
        self.root = root
        self.scene = SimpleNamespace(at=at, members=[root.investigator.id])

    @property
    def minutes(self):
        return self.scene.at

    def __getattr__(self, name):
        return getattr(self.root, name)


async def test_a_continuing_scene_without_operation_clock_does_not_import_later_npc_knowledge(fake):
    game, _ = made("square", fake=fake)
    world, start = game.world, game.state.minutes
    try:
        with world.at(start + 20):
            assert world.apply(Occur(id="future", text="来客自称银鸟。", witnesses=["preacher"],
                                     place="square", visibility="private")).ok
            assert world.apply(AddFact(fact="later-name", text="新住址是松树街。", known_by=["preacher"],
                                      visibility="private")).ok
        original = world.st
        cursor = SceneCursor(original, start + 5)
        world.st = cursor
        assert world.clock is None
        for view in (context.npc_brief(world, "preacher"), context.npc_view(world, "preacher").text):
            assert "银鸟" not in view and "松树街" not in view
        cursor.scene.at = start + 20
        assert "银鸟" in context.npc_brief(world, "preacher")
        assert "松树街" in context.npc_view(world, "preacher").text
    finally:
        world.st = game.state
        await game.close()


async def test_scoped_npc_location_uses_recorded_travel_without_moving_the_authoritative_entity(fake):
    game, _ = made("square", fake=fake)
    world, state, start = game.world, game.state, game.state.minutes
    try:
        with world.at(start + 20, start=start + 10):
            assert world.apply(Move(entity="preacher", to="office", actor="keeper")).ok
        cursor = SceneCursor(state, start + 5)
        world.st = cursor
        assert "【你在】广场（square）" in context.npc_view(world, "preacher").text
        assert world.place("preacher") == "office"  # no retroactive state mutation
        cursor.scene.at = start + 15
        assert "【你在】行程中，尚未抵达" in context.npc_view(world, "preacher").text
        cursor.scene.at = start + 20
        assert "【你在】报社（office）" in context.npc_view(world, "preacher").text
    finally:
        world.st = state
        await game.close()


async def test_scoped_keeper_separates_own_experience_remote_facts_and_future_traces(fake):
    game, _ = made("square", fake=fake)
    world, state, start = game.world, game.state, game.state.minutes
    me = state.investigator.id
    other = state.investigator.model_copy(deep=True, update={"id": "other", "name": "异地同伴"})
    state.investigators.append(other)
    state.apart[other.id] = "office"
    world.sync()
    try:
        with world.at(start + 2):
            assert world.apply(Occur(id="own", text="广场上的钟突然响了。", place="square")).ok
            assert world.apply(Occur(id="remote", text="办公室有人递来密函。", place="office")).ok
            assert world.apply(AddFact(fact="own-fact", text="调查员找到银色纽扣。", known_by=[me], visibility="private")).ok
            assert world.apply(AddFact(fact="remote-fact", text="异地同伴读到红字留言。", known_by=[other.id],
                                      actor=other.id, visibility="private")).ok
        with world.at(start + 20):
            assert world.apply(Occur(id="later", text="钟楼后来着火了。", place="square", trace="广场上积满黑灰。",
                                     visibility="private", witnesses=[me])).ok
        cursor = SceneCursor(state, start + 5)
        world.st = cursor
        text = context.keeper_view(world)
        assert "钟突然响了" in text and "银色纽扣" in text
        assert "密函" not in text and "红字留言" not in text and "后来着火" not in text and "黑灰" not in text
        assert context.happened(world, 0) == ["广场上的钟突然响了。", "调查员找到银色纽扣。"]
        assert not context.traces(world, "square")
        cursor.scene.at = start + 20
        assert "黑灰" in "\n".join(context.traces(world, "square"))
        # Privileged preparation stays available; it must not be confused with what this PL has seen.
        assert world.m.truth.strip() in _book(world.m)
        assert "守秘人须知" in context.keeper_brief(world)
    finally:
        world.st = state
        await game.close()

