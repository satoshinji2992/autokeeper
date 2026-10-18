"""The F5 map: fog of war over the module's places, laid out and drawn (no model calls)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Static

from autokeeper.agents.director import Improv, NewLocation
from autokeeper.config import EXAMPLE
from autokeeper.engine import fog
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module import improv
from autokeeper.module.schema import Location, Module
from autokeeper.net.hub import PRIVATE
from autokeeper.tui import mapview
from autokeeper.tui.app import AutoKeeperApp

ZHUI = Path(__file__).resolve().parents[1] / "modules" / "zhuishuren"
SECRETS = {"lingmu", "suidao"}  # 墓地深处的陵墓, 食尸鬼隧道网


@pytest.fixture
def zhui() -> Module:
    return Module.load(ZHUI)


def opened(m: Module) -> GameState:
    """A game just after its opening, as Game.start leaves it."""
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.add("opening", m.opening.strip())
    return st


def known(m: Module, st: GameState) -> set[str]:
    c = mapview.chart(m, st)
    return set(c.names) | set(c.rumours)


def overlaps(p: mapview.Plan) -> list[tuple[str, str]]:
    """Places whose marker and name share a row with another's and run into it, and paths
    drawn through a name."""
    boxes = {place: p.box(place) for place in p.spots}
    clash = [(a, b) for a in boxes for b in boxes if a < b and boxes[a][2] == boxes[b][2] and boxes[a][0] <= boxes[b][1] + 1 and boxes[b][0] <= boxes[a][1] + 1]
    clash += [(place, "path") for place, (x0, x1, y) in boxes.items() if any(cy == y and x0 <= cx <= x1 for cx, cy in p.lines)]
    return clash


def test_secret_places_stay_off_the_map_until_heard_of(zhui):
    st = opened(zhui)
    assert known(zhui, st) == {"jinboer_jia", "shufang", "jiefang", "mudi", "tushuguan", "baoshe"}  # what Thomas offered to show
    st.add("player", "我直接去食尸鬼隧道网，再去墓地深处的陵墓看看", who="哈罗德")  # a guess is not knowledge
    assert not known(zhui, st) & SECRETS
    drawn = mapview.view(zhui, st, 88, 22).plain
    assert "陵墓" not in drawn and "隧道" not in drawn and "食尸鬼" not in drawn


def test_a_place_heard_of_shows_by_the_name_it_was_heard_by(zhui):
    st = opened(zhui)
    st.visited.append("shufang")
    st.location, st.clues, st.handouts = "shufang", ["c_diary"], ["h_diary"]  # 公墓地下有一个隧道网络
    c = mapview.chart(zhui, st)
    assert c.rumours["suidao"] == "隧道网"  # no way there known yet, and no ghouls in its name
    drawn = mapview.view(zhui, st, 88, 22).plain
    assert "○ 隧道网" in drawn and "食尸鬼" not in drawn
    st.visited.append("mudi")
    st.location, st.clues = "mudi", [*st.clues, "c_footprints"]  # the footprints lead to a mausoleum's door
    c = mapview.chart(zhui, st)
    assert ("mudi", "lingmu") in c.trails and c.names["lingmu"] == "陵墓" and c.kinds["lingmu"] == "heard"
    st.visited += ["lingmu", "suidao"]
    c = mapview.chart(zhui, st)
    assert c.names["suidao"] == "食尸鬼隧道网" and c.kinds["suidao"] == "been" and ("lingmu", "suidao") in c.roads


def test_names_match_in_full_or_by_their_head_never_by_a_plain_word(zhui):
    assert "图书馆" in fog.aliases(zhui)["tushuguan"]

    def named(text: str) -> set[str]:
        return set(fog.mentions(zhui, [text]))

    assert named("我想先去图书馆翻翻旧报纸") == {"tushuguan"}
    assert named("《阿诺兹堡广告报》报社的资料室") == {"baoshe"}
    assert named("大街上空无一人，镇中心的钟敲了三下") == {"zhenzhongxin"}
    assert named("城里大街上，市中心人来人往") == set()
    assert named("墓地深处有些什么，他不愿多说") == set()  # the cemetery's depths are not the mausoleum


def test_the_way_walked_shows_the_places_passed_through(zhui):
    st = opened(zhui)
    st.visited.append("tushuguan")  # straight from the house to the library
    st.location = "tushuguan"
    c = mapview.chart(zhui, st)
    assert {"jiefang", "zhenzhongxin"} <= {p for p, k in c.kinds.items() if k == "been"}
    assert {("jinboer_jia", "jiefang"), ("jiefang", "zhenzhongxin"), ("zhenzhongxin", "tushuguan")} <= set(c.roads)
    assert not set(c.names) & SECRETS


def test_improvised_places_follow_the_same_rule(zhui):
    st = opened(zhui)
    cellar = Improv(kind="后果", reason="调查员撬了地板", locations=[NewLocation(id="cellar", name="金博尔家的地窖", read_aloud="潮湿的砖墙。", exits=["jinboer_jia"])])
    improv.apply(zhui, cellar)
    st.improv.append({"turn": 1, "kind": "后果", "plan": cellar.model_dump()})
    assert "cellar" not in known(zhui, st)  # made up off stage: nobody has said so
    st.add("keeper", "托马斯说，地窖的门好些年没开过了。", who="守秘人")
    assert mapview.chart(zhui, st).rumours["cellar"] == "地窖"  # heard of, but nothing showed the way down
    sanatorium = Improv(kind="展开", reason="打听沃德女士", locations=[NewLocation(id="sanatorium", name="阿诺兹堡疗养院", read_aloud="铁门。", exits=["zhenzhongxin"])])
    improv.apply(zhui, sanatorium)
    st.improv.append({"turn": 2, "kind": "展开", "plan": sanatorium.model_dump()})
    assert mapview.chart(zhui, st).rumours["sanatorium"] == "阿诺兹堡疗养院"  # a new line is announced by name


async def test_places_heard_of_outlive_the_log_and_reach_a_shared_table(zhui, rules):
    class Chronicler:
        async def chronicle(self, state, entries):
            return "摘要"

    st = opened(zhui)
    game = Game(zhui, st, Chronicler(), rules)  # type: ignore[arg-type]
    st.add("npc", "那座陵墓？我还有活要干。", who="梅洛迪亚斯·杰弗逊")
    for i in range(50):
        st.add("keeper", f"第 {i} 段", who="守秘人")
    await game.remember()
    assert len(st.log) == 24 and not any("陵墓" in e.text for e in st.log)
    assert st.heard["lingmu"] == "陵墓" and "lingmu" in mapview.chart(zhui, st).rumours
    table = GameState.model_validate(st.model_dump(mode="json", exclude=PRIVATE))  # what players' screens get: no log
    assert not table.log and "lingmu" in mapview.chart(zhui, table).rumours


def test_every_known_place_is_drawn_once_without_overlap_and_here_is_marked(zhui):
    st = opened(zhui)
    st.visited = [loc.id for loc in zhui.locations]
    st.location = "lingmu"
    c = mapview.chart(zhui, st)
    p = mapview.plan(c, 88, 22, mapview.short_names(zhui, c))
    assert p is not None and p.width <= 88 and p.height <= 22
    assert set(p.spots) == {loc.id for loc in zhui.locations}
    assert not overlaps(p)
    content = mapview.draw(c, p)
    drawn = content.plain
    assert all(p.labels[place] in drawn for place in p.spots)
    assert drawn.count("◉") == 1 and f"◉ {p.labels['lingmu']}" in drawn
    assert [drawn[s.start : s.end] for s in content.spans if s.style == mapview.STYLE["you"]] == [p.labels["lingmu"]]  # highlighted
    assert ("你在这里" in drawn) == (p.tagged == "lingmu")


def test_twenty_places_with_long_names_still_fit():
    m = Module.load(EXAMPLE)
    names = ["金博尔家", "道格拉斯的书房", "艾尔斯伯里大街", "公墓", "墓地深处的陵墓", "食尸鬼隧道网", "阿诺兹堡镇中心", "阿诺兹堡公共图书馆", "阿诺兹堡警察局",
             "《阿诺兹堡广告报》报社", "圣玛丽教堂的钟楼", "河边的废弃磨坊", "火车站候车室", "黑橡树旅馆", "旅馆二楼的十三号房", "老码头仓库",
             "密斯卡托尼克大学图书馆", "镇北的疗养院", "沼泽边的渔夫小屋", "灯塔"]  # fmt: skip
    ways = {0: [1, 2, 3], 2: [6, 3], 3: [4], 4: [5], 6: [7, 8, 9, 12, 13], 13: [14], 12: [15, 16], 15: [11, 19], 11: [18], 16: [17], 10: [6, 3]}
    places = [Location(id=f"p{i}", name=n, read_aloud="", exits=[f"p{j}" for j in ways.get(i, [])]) for i, n in enumerate(names)]
    m = m.model_copy(update={"locations": places, "start": "p0", "npcs": [], "clues": [], "events": []})
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.visited, st.location = [x.id for x in places], "p13"
    c = mapview.chart(m, st)
    assert len(c.names) == 20 and len(c.roads) == 21
    for width, height in ((88, 22), (70, 14)):  # a big screen, then an 80 x 24 one: shorter names, closer rows
        p = mapview.plan(c, width, height, mapview.short_names(m, c))
        assert p is not None and p.width <= width and p.height <= height
        assert len(p.spots) == 20 and not overlaps(p)


def test_a_screen_too_small_for_a_map_gets_a_list(zhui):
    st = opened(zhui)
    st.visited += ["shufang", "mudi"]
    st.location = "mudi"
    drawn = mapview.view(zhui, st, 20, 5).plain
    assert "◉ 公墓（你在这里）" in drawn and "金博尔家" in drawn and "陵墓" not in drawn


def test_what_happened_where_is_listed_under_the_map():
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.visited, st.location = ["square", "tower"], "tower"
    st.clues = ["c_notice"]
    st.npc("warden").met = True
    st.sky = "fog"
    legend = mapview.legend(m, st, mapview.chart(m, st)).plain
    assert "◉ 钟楼  老守钟人" in legend  # met here; indoors, so no fog
    assert "● 广场  ≋ 雾 · 线索 1" in legend  # open-air, so the fog shows; one clue found here
    assert "听说过，还不知道怎么去：○ 报社" in legend and "线索 0" not in legend


async def test_f5_opens_a_map_that_fits_a_small_screen(zhui):
    st = opened(zhui)
    st.visited = [loc.id for loc in zhui.locations]
    st.location = "lingmu"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.push_screen(mapview.MapModal(zhui, st))
        await pilot.pause()
        drawn = app.screen.query_one("#map", Static).content.plain
        assert "◉" in drawn and "陵墓" in drawn
        hint = app.screen.query(".system").last()
        assert hint.region.bottom <= app.size.height  # the map scrolls; "Esc 关闭" stays on screen
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, mapview.MapModal)
