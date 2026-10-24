"""Richer scenes: more than one picture of a place, the people who are there set into it, the
pictures the world's changes call for, and what of all that reaches the players' screens."""

from __future__ import annotations

import pytest

from autokeeper.agents.cast import Action, Ruling
from autokeeper.engine import views
from autokeeper.engine.game import Turn
from autokeeper.engine.state import parse_time
from autokeeper.module.schema import Event, Module, View
from autokeeper.net.hub import table
from autokeeper.paths import EXAMPLE
from autokeeper.world import ops as O

from .test_engine import make


@pytest.fixture
def module() -> Module:
    m = Module.load(EXAMPLE)
    tower = m.location("tower")
    tower.art = "tower"
    tower.views = [View(key="tower_gears", label="齿轮间", features=["齿轮间"], prompt="昏暗的齿轮间"),
                   View(key="tower_night", label="夜里的钟楼", time="night")]  # fmt: skip
    for key in ("tower", "tower_gears", "tower_night"):
        m.art[key] = f"[{key}]"
    return m


def look(target: str) -> Action:
    return Action(kind="investigate", summary=f"周衡查看{target}", target=target)


QUIET = Ruling(needs_roll=False, minutes=0, reason="")


def test_the_picture_for_the_hour_and_for_what_they_look_at(module):
    tower = module.location("tower")
    assert views.default(module, tower, parse_time("D1 14:00")) == tower.art
    assert views.default(module, tower, parse_time("D1 21:00")) == "tower_night"
    assert views.focus(module, tower, "齿轮间里的木箱") == "tower_gears"
    assert views.focus(module, tower, "守钟人的柜子") == ""
    assert views.focus(module, tower, "齿轮") == ""  # a part of its name is not the thing
    del module.art["tower_night"]  # nothing to show: the place's own picture
    assert views.default(module, tower, parse_time("D1 21:00")) == tower.art


async def test_the_scene_turns_to_what_they_look_at_and_back(module, fake):
    game, _, events = make(module, fake, {}, location="square")
    await game._arrive("tower", Turn())
    st = game.state
    assert st.view == module.location("tower").art and events[-1]["view"] == st.view
    await game.resolve(look("齿轮间"), QUIET)
    assert (st.view, st.view_kind) == ("tower_gears", "focus")
    assert {"type": "view", "location": "tower", "key": "tower_gears"} in events
    await game.resolve(Action(kind="wait", summary="周衡站着等"), QUIET)
    assert st.view == "tower_gears"  # waiting does not turn away
    await game.resolve(Action(kind="talk", summary="周衡问守钟人", npc="warden"), QUIET)
    assert (st.view, st.view_kind) == (module.location("tower").art, "")


async def test_night_falls_on_the_place_and_an_event_holds_the_scene_for_its_moment(module, fake):
    module.events = [e for e in module.events if not e.when.startswith("D")]  # the module's own evening, with its dice, aside
    module.events.append(Event(id="bell", title="钟声", when="D1 20:30", text="钟多敲了一下", read_aloud="钟声多响了一下", art="bell_moment"))
    module.art["bell_moment"] = "[钟声]"
    game, _, _ = make(module, fake, {}, location="tower")
    st = game.state
    await game.resolve(Action(kind="wait", summary="周衡等到天黑"), Ruling(needs_roll=False, minutes=7 * 60, reason="", rest=True))
    assert (st.view, st.view_kind) == ("bell_moment", "moment")  # the bell, then
    await game.resolve(Action(kind="wait", summary="周衡继续等"), QUIET)
    assert (st.view, st.view_kind) == ("tower_night", "")  # back to the place, by night


async def test_someone_walking_in_tells_the_screens_to_set_the_faces_again(module, fake):
    module.npc("warden").important = True
    game, _, events = make(module, fake, {}, location="square")
    w = game.world
    w.apply(O.SetPlan(npc="warden", steps=[O.Step(id="down", text="下楼", due=w.now + 10,
                                                  ops=[{"kind": "move", "entity": "warden", "to": "square"}])]))  # fmt: skip
    await game.resolve(Action(kind="wait", summary="周衡等着"), Ruling(needs_roll=False, minutes=30, reason=""))
    people = [e for e in events if e["type"] == "people"]
    assert people and set(people[-1]["ids"]) == {"warden", "preacher"}


async def test_what_changed_while_they_were_away_is_drawn_and_so_is_a_wounded_face(module, fake):
    game, _, _ = make(module, fake, {}, location="tower")
    await game._arrive("square", Turn())
    game.world.apply(O.Occur(text="有人撬开了齿轮间的门", place="tower", trace="齿轮间的门被撬开了", actor="preacher", visibility="private"))
    await game._arrive("tower", Turn())
    assert game.state.looks[module.location("tower").art]["change"] == "齿轮间的门被撬开了"
    game.world.apply(O.Condition(entity="warden", status="受伤"))
    await game._people()
    assert "受伤" in game.state.looks[module.npc("warden").portrait]["change"]


async def test_the_players_screens_get_the_pictures_not_what_they_are_for(module, fake):
    module.events.append(Event(id="bell", title="钟声", when="D1 20:30", text="钟多敲了一下", art="bell_moment"))
    game, _, _ = make(module, fake, {}, location="square")
    book = table(module, game.state)
    assert next(x for x in book["locations"] if x["id"] == "tower")["views"] == []  # not been there
    await game._arrive("tower", Turn())  # the changed condition is observed here, not learned remotely
    game.state.npc("warden").met = True
    game.world.apply(O.Condition(entity="warden", status="受伤"))
    book = table(module, game.state)
    tower = next(x for x in book["locations"] if x["id"] == "tower")
    assert [v["key"] for v in tower["views"]] == ["tower_gears", "tower_night"]
    assert all(v["features"] == [] and v["prompt"] == "" for v in tower["views"])
    assert all(e.get("art", "") == "" for e in book["events"])
    assert next(n for n in book["npcs"] if n["id"] == "warden")["status"] == "受伤"
    assert Module.model_validate(book).npc("warden").status == "受伤"


def test_who_they_know_is_here_is_set_into_the_picture(tmp_path):
    from PIL import Image

    from autokeeper import imagery

    scene, face = tmp_path / "scene.png", tmp_path / "face.png"  # lossless: the face keeps its colour
    Image.new("RGB", (480, 240), (40, 90, 140)).save(scene)
    Image.new("RGB", (120, 160), (230, 200, 160)).save(face)
    plain = imagery._framed(str(scene), 72, 22, True)
    with_face = imagery.cameos(plain, (str(face),))
    assert with_face.size == plain.size
    corner = with_face.crop((0, with_face.height // 2, with_face.width // 3, with_face.height))
    def nearer_face(c: tuple[int, ...]) -> bool:  # the photo's preparation shifts colours: warm skin against a blue scene
        return sum((a - b) ** 2 for a, b in zip(c, (230, 200, 160), strict=True)) < sum((a - b) ** 2 for a, b in zip(c, (40, 90, 140), strict=True))

    warm = sum(n for n, c in corner.getcolors(1 << 16) if nearer_face(c))
    assert warm > corner.width * corner.height * 0.05  # the face is in the bottom left
    box = (with_face.width * 2 // 3, 0, with_face.width, with_face.height)
    assert with_face.crop(box).tobytes() == plain.crop(box).tobytes()  # the rest of the scene untouched
    assert imagery.cameos(plain, ()) is not plain and imagery.cameos(plain, ()).tobytes() == plain.tobytes()


def test_only_the_people_they_have_met_show_a_face(module, fake, tmp_path):
    """The user, 2026-10-20: who they know is here, as the oval portraits at the bottom left. Not
    someone they have not met (the figure by the tombstone is no one's yet), not the dead."""
    from types import SimpleNamespace

    from autokeeper.tui.screens.game import GameScreen

    game, _, _ = make(module, fake, {}, location="tower")
    st, warden = game.state, module.npc("warden")
    module.pictures[warden.portrait] = str(tmp_path / "warden.jpg")
    screen = SimpleNamespace(state=st, m=module)
    assert GameScreen._faces(screen, "tower") == ()  # here, never spoken with
    st.npc("warden").met = True
    assert GameScreen._faces(screen, "tower") == (str(tmp_path / "warden.jpg"),)
    warden.status = "死亡"
    assert GameScreen._faces(screen, "tower") == ()
