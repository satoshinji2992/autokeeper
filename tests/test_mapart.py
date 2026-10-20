"""The painted map (tui/drawing/mapart.py): ground, ways and mist made by program under the names."""

from __future__ import annotations

from rich.cells import cell_len

from autokeeper import paths
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.tui.drawing import mapart
from autokeeper.tui.modals import mapview as mv


def painted_map(visited: str = "all"):
    m = Module.load(paths.MODULES / "dongxuezhixia")
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)], slot="painted")
    ids = [loc.id for loc in m.locations]
    st.visited = ids if visited == "all" else ids[:2]
    st.heard = {loc.id: loc.name for loc in m.locations}
    c = mv.chart(m, st)
    plan = mv.plan(c, 110, 34, mv.short_names(m, c), mv.default_open(c))
    kinds = {p: plan.kinds.get(p, c.kinds[p]) for p in plan.spots}
    return m, c, plan, kinds


def test_the_map_is_painted_not_drawn_in_lines_and_every_name_is_on_it():
    m, c, plan, kinds = painted_map()
    painted = mapart.paint(plan, kinds, c.names, m.id)
    assert painted.pixels.shape == (plan.height * 2, plan.width, 3)
    text = mapart.compose(plan, painted, kinds, mv.TAG, None, mv.MARK)
    rows = text.plain.split("\n")
    assert len(rows) == plan.height and {cell_len(r) for r in rows} == {plan.width}
    assert not set("─│┌┐└┘├┤┬┴┼┄┆") & set(text.plain)  # no line drawing: the ways are painted
    for place, label in plan.labels.items():
        assert label in text.plain, place


def test_the_same_map_is_painted_the_same_way_and_the_ground_follows_the_name():
    m, c, plan, kinds = painted_map()
    first = mapart.paint(plan, kinds, c.names, m.id).pixels
    assert (first == mapart.paint(plan, kinds, c.names, m.id).pixels).all()  # seeded: no flicker on redraw
    assert mapart.ground("绸缎山").pattern == "trees" and mapart.ground("公墓").pattern == "crosses"
    assert mapart.ground("主街").pattern == "cobbles" and mapart.ground("安特医生的诊所").pattern == "plot"


def test_what_has_only_been_heard_of_lies_in_mist():
    m, c, plan, kinds = painted_map("some")
    clear = mapart.paint(plan, {p: "been" for p in kinds}, c.names, m.id).pixels
    misty = mapart.paint(plan, kinds, c.names, m.id).pixels
    far = [p for p, k in kinds.items() if k == "heard"]
    if far:  # paler where they have not been
        x, y = plan.spots[far[0]]
        assert misty[y * 2, x + 3].mean() >= clear[y * 2, x + 3].mean() - 0.02
