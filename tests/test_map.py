"""The F3 map: fog of war over the module's places, what lies in what, laid out and drawn (no model calls)."""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from autokeeper.agents.director import Improv, NewLocation
from autokeeper.config import EXAMPLE
from autokeeper.engine import fog
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module import graph, improv
from autokeeper.module.schema import Location, Module
from autokeeper.net.hub import PRIVATE, table
from autokeeper.tui import mapview
from autokeeper.tui.app import AutoKeeperApp, GameScreen

from .test_tui import canned  # noqa: F401  (a fixture: every agent answers from a can)

ROOT = Path(__file__).resolve().parents[1]
ZHUI = ROOT / "modules" / "zhuishuren"
BUNDLED = [Path(EXAMPLE), *sorted(p.parent for p in (ROOT / "modules").glob("*/module.yaml"))]
SECRETS = {"lingmu", "suidao"}  # 墓地深处的陵墓, 食尸鬼隧道网
SIZES = [(80, 24), (100, 30), (120, 40), (160, 50)]


@pytest.fixture
def zhui() -> Module:
    return Module.load(ZHUI)


def opened(m: Module) -> GameState:
    """A game just after its opening, as Game.start leaves it."""
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.add("opening", m.opening.strip())
    return st


def everywhere(m: Module, here: str) -> GameState:
    """A game that has been to every place, now at `here`."""
    st = opened(m)
    st.visited = [loc.id for loc in m.locations]
    st.location = here
    return st


def known(m: Module, st: GameState) -> set[str]:
    c = mapview.chart(m, st)
    return set(c.names) | set(c.rumours)


def overlaps(p: mapview.Plan) -> list[tuple[str, str]]:
    """Places whose marker and name share a row with another's and run into it, names on a
    frame's wall, and paths drawn through a name."""
    boxes = {place: p.box(place) for place in p.spots}
    clash = [(a, b) for a in boxes for b in boxes if a < b and boxes[a][2] == boxes[b][2] and boxes[a][0] <= boxes[b][1] + 1 and boxes[b][0] <= boxes[a][1] + 1]
    clash += [(place, "path") for place, (x0, x1, y) in boxes.items() if any(cy == y and x0 <= cx <= x1 for cx, cy in p.lines)]
    clash += [(place, "wall") for place, (x0, x1, y) in boxes.items() if any(cy == y and x0 - 1 <= cx <= x1 + 1 for cx, cy in p.walls)]
    return clash


def plays(m: Module, rng: random.Random, games: int, turns: int) -> list[GameState]:
    """Games played at random, as the rules let them: setting out only for a place they could
    find, finding clues and meeting people where they stopped, the keeper naming places in
    passing; the engine keeps what was heard at each turn's end (Game.remember)."""
    out = []
    for _ in range(games):
        st = opened(m)
        st.visited = [m.start]
        st.heard = fog.heard(m, st)
        for _ in range(turns):
            choices = [loc.id for loc in m.locations if loc.id != st.location and fog.findable(m, st, loc.id)]
            if choices:
                st.location = rng.choice(choices)
                st.visited += [st.location] if st.location not in st.visited else []
            for c in m.clues:
                if c.where == st.location and c.id not in st.clues and rng.random() < 0.5:
                    st.clues.append(c.id)
                    st.handouts += [c.handout] if c.handout and c.handout not in st.handouts else []
            for n in m.npcs:
                if n.location == st.location and rng.random() < 0.7:
                    st.npc(n.id).met = True
            if rng.random() < 0.4:
                loc = rng.choice(m.locations)
                st.add("keeper", f"有人提起了{rng.choice([loc.name, *loc.aliases])}，说那边最近不太平。", who="守秘人")
            st.sky = rng.choice(["", "rain", "fog", st.sky])
            st.heard = fog.heard(m, st)
            out.append(st.model_copy(deep=True))
    return out


# -- what they know ------------------------------------------------------------------------
def test_secret_places_stay_off_the_map_until_heard_of(zhui):
    st = opened(zhui)
    assert known(zhui, st) == {"huochezhan", "jinboer_jia", "jiefang", "mudi", "zhenzhongxin"}  # the platform and the address, before meeting Thomas
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
    st.location, st.visited = "jinboer_jia", ["jinboer_jia"]
    st.visited.append("tushuguan")  # straight from the house to the library
    st.location = "tushuguan"
    c = mapview.chart(zhui, st)
    assert {"jiefang", "zhenzhongxin"} <= {p for p, k in c.kinds.items() if k == "been"}
    assert {("jinboer_jia", "jiefang"), ("jiefang", "zhenzhongxin"), ("zhenzhongxin", "tushuguan")} <= set(c.roads)
    assert not set(c.names) & SECRETS


def test_improvised_places_follow_the_same_rule(zhui):
    st = opened(zhui)
    cellar = Improv(kind="后果", reason="调查员撬了地板", locations=[
        NewLocation(id="cellar", name="金博尔家的地窖", read_aloud="潮湿的砖墙。", exits=["jinboer_jia"], within="jinboer_jia")])
    assert not improv.check(zhui, cellar, at=st.location)
    improv.apply(zhui, cellar)
    st.improv.append({"turn": 1, "kind": "后果", "plan": cellar.model_dump()})
    assert zhui.location("cellar").within == "jinboer_jia"
    assert "cellar" not in known(zhui, st)  # made up off stage: nobody has said so
    st.add("keeper", "托马斯说，地窖的门好些年没开过了。", who="守秘人")
    c = mapview.chart(zhui, st)
    assert c.rumours["cellar"] == "地窖" and "cellar" not in c.within  # heard of, but nothing showed the way down
    st.visited += ["jinboer_jia", "cellar"]
    st.location = "cellar"
    c = mapview.chart(zhui, st)
    assert c.within["cellar"] == "jinboer_jia" and "jinboer_jia" in mapview.default_open(c)  # down there, the house is open around it
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
    view = GameState.model_validate(st.model_dump(mode="json", exclude=PRIVATE))  # what players' screens get: no log
    assert not view.log and "lingmu" in mapview.chart(Module.model_validate(table(zhui, st)), view).rumours


# -- laid out and drawn ---------------------------------------------------------------------
def test_every_known_place_is_drawn_once_without_overlap_and_here_is_marked(zhui):
    st = everywhere(zhui, "lingmu")
    c = mapview.chart(zhui, st)
    p = mapview.plan(c, 88, 22, mapview.short_names(zhui, c), opened=set(c.within.values()))  # every place open
    assert set(p.spots) == {loc.id for loc in zhui.locations} and set(p.frames) == {"jinboer_jia", "mudi", "zhenzhongxin"}
    assert not p.missing and not overlaps(p)
    content = mapview.draw(c, p, picked="lingmu")
    drawn = content.plain
    assert all(p.labels[place] in drawn for place in p.spots)
    assert drawn.count("◉") == 1 and f"◉ {p.labels['lingmu']}" in drawn
    you = [drawn[s.start : s.end] for s in content.spans if str(s.style).startswith(mapview.STYLE["you"])]
    assert you == [p.labels["lingmu"]]  # highlighted, and underlined as the place picked
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
        mapview._PLANS.clear()
        start = time.perf_counter()
        p = mapview.plan(c, width, height, mapview.short_names(m, c))
        assert time.perf_counter() - start < 3  # about 0.1 s here; a slow CI machine is not a broken layout
        assert p.width <= width and p.height <= height
        assert len(p.spots) == 20 and not p.missing and not overlaps(p)
        assert mapview.plan(c, width, height, mapview.short_names(m, c)) is p  # laid out once per thing known and window


@pytest.mark.parametrize("folder", BUNDLED, ids=lambda f: f.name)
def test_every_bundled_module_is_a_graph_at_every_screen_size(folder):
    """With every place known, from the start and from inside a place, on four screens: always
    the drawn map, never a list, scrolled when it is bigger than its window."""
    m = Module.load(folder)
    for here in dict.fromkeys([m.start, *(loc.id for loc in m.locations if loc.within)]):
        st = everywhere(m, here)
        c = mapview.chart(m, st)
        short, opened_ = mapview.short_names(m, c), mapview.default_open(c)
        t = mapview.tree(c, opened_)
        for width, height in SIZES:
            (room_w, room_h), *_ = mapview.window(width, height, mapview.legend(c, t))
            mapview._PLANS.clear()
            start = time.perf_counter()
            p = mapview.plan(c, room_w - 1, room_h - 1, short, opened_)
            assert time.perf_counter() - start < 3, (here, width, height)
            assert not p.missing and not overlaps(p), (here, width, height)
            assert set(p.spots) == set(t.shown)
            drawn = mapview.draw(c, p).plain
            assert "通往" not in drawn and all(p.labels[place] in drawn for place in p.spots)
            assert drawn.count("◉") == 1


def test_a_small_screen_still_gets_the_map_to_scroll_over(zhui):
    st = everywhere(zhui, "mudi")
    c = mapview.chart(zhui, st)
    p = mapview.plan(c, 20, 5, mapview.short_names(zhui, c))
    assert p.width > 20 or p.height > 5  # bigger than its window: the window scrolls
    assert not p.missing and not overlaps(p) and "mudi" in p.frames
    drawn = mapview.view(zhui, st, 20, 5).plain
    assert "◉ 公墓" in drawn and "─" in drawn and "通往" not in drawn


def test_a_place_with_places_in_it_folds_and_opens(zhui):
    st = everywhere(zhui, "huochezhan")
    c = mapview.chart(zhui, st)
    assert c.within == {"shufang": "jinboer_jia", "lingmu": "mudi", "suidao": "mudi",
                        "tushuguan": "zhenzhongxin", "jingju": "zhenzhongxin", "baoshe": "zhenzhongxin"}
    assert mapview.default_open(c) == {"huochezhan"}  # at the station: everything folded
    p = mapview.plan(c, 88, 22)
    assert not p.frames and not {"shufang", "lingmu", "suidao", "tushuguan"} & set(p.spots)
    assert (p.labels["jinboer_jia"], p.labels["mudi"], p.labels["zhenzhongxin"]) == ("金博尔家 ▸ 1", "公墓 ▸ 2", "阿诺兹堡镇中心 ▸ 3")
    t = mapview.tree(c, {"mudi"})
    ways = {frozenset(w) for _, w in mapview._ways(c, t)}
    assert {"lingmu", "suidao"} in ways and {"mudi", "lingmu"} not in ways  # inside says the cemetery leads there
    assert {"mudi", "jinboer_jia"} in ways and not any("shufang" in w for w in ways)  # the study's road is the folded house's
    p = mapview.plan(c, 88, 22, opened={"mudi"})
    assert set(p.frames) == {"mudi"} and p.labels["mudi"] == "公墓 ▾" and not p.missing
    left, top, right, bottom = p.frames["mudi"]
    for place in ("lingmu", "suidao"):
        x0, x1, y = p.box(place)
        assert left < x0 and x1 < right and top < y < bottom
    assert any(left < x < right and top < y < bottom for x, y in p.lines)  # the mausoleum's way down, inside
    st.location = "suidao"  # down in the tunnels: the cemetery is open around them
    assert mapview.default_open(mapview.chart(zhui, st)) == {"suidao", "mudi"}


def test_only_the_places_they_know_count_as_inside(zhui):
    st = opened(zhui)
    st.visited += ["jinboer_jia"]
    st.location = "jinboer_jia"  # the study's door is shut and nobody has said what is behind it
    c = mapview.chart(zhui, st)
    assert "shufang" not in c.names and not c.within
    assert mapview.plan(c, 88, 22).labels["jinboer_jia"] == "金博尔家"
    st.visited += ["mudi"]
    st.location, st.clues = "mudi", ["c_footprints", "c_diary"]  # the way to the mausoleum; the tunnels only told of
    c = mapview.chart(zhui, st)
    assert c.within == {"lingmu": "mudi"} and "suidao" in c.rumours
    assert mapview.plan(c, 88, 22, opened=set()).labels["mudi"] == "公墓 ▸ 1"


def test_what_they_know_inside_a_place(zhui):
    st = opened(zhui)
    st.visited, st.location = ["huochezhan", "jinboer_jia", "mudi"], "mudi"  # walked through the town centre and the street
    st.clues, st.sky = ["c_footprints"], "rain"
    st.npc("jefferson").met = True
    c = mapview.chart(zhui, st)
    cemetery = mapview.about(zhui, st, c, "mudi").plain
    lines = cemetery.split("\n")
    assert lines[0] == "◉ 公墓  你在这里"
    assert lines[1] == "".join(x.strip() for x in zhui.location("mudi").read_aloud.split("\n"))  # what they saw, unwrapped
    assert "里面 陵墓" in cemetery and "见过 梅洛迪亚斯·杰弗逊" in cemetery and "线索 墓碑旁的怪足迹" in cemetery
    assert "通往 金博尔家 10 分钟、艾尔斯伯里大街 10 分钟" in cemetery and "陵墓（没走过）" in cemetery and "天气 ☂ 雨" in cemetery
    street = mapview.about(zhui, st, c, "jiefang").plain
    assert street.startswith("● 艾尔斯伯里大街  路过") and zhui.location("jiefang").read_aloud.strip()[:8] not in street
    assert "金博尔家 15 分钟" in street and "阿诺兹堡镇中心 15 分钟" in street  # the module's minutes for the walk
    tomb = mapview.about(zhui, st, c, "lingmu").plain
    assert tomb == "○ 陵墓  听说过，还没去过 · 在公墓里\n通往 公墓（没走过）"
    everything = "\n".join(mapview.about(zhui, st, c, place).plain for place in c.names)
    shown = "\n".join(fog.shown(zhui, st)) + "".join(n.name for n in zhui.npcs if st.npc(n.id).met)
    unseen = [f for loc in zhui.locations for f in loc.features if f not in shown]  # what could be searched, not what was seen
    secret = [loc.keeper.strip()[:12] for loc in zhui.locations] + unseen + [x.title for x in zhui.clues if x.id not in st.clues]
    secret += [s for n in zhui.npcs for s in n.secrets] + ["食尸鬼", "隧道"]
    assert "杰弗逊的外套口袋" in unseen and not [s for s in secret if s and s in everything]


def test_what_lies_in_a_place_is_what_they_saw_there():
    m = Module.load(ROOT / "modules" / "dongxuezhixia")
    st = opened(m)
    st.visited, st.location = ["ratger_house", "darvill_house"], "darvill_house"
    c = mapview.chart(m, st)
    assert "\n物件 " not in mapview.about(m, st, c, "darvill_house").plain  # the grimoire is in the house, unseen
    st.clues = ["c_grimoire"]
    assert "\n物件 卡德威尔家的典籍" in mapview.about(m, st, mapview.chart(m, st), "darvill_house").plain
    assert fog.things(m, st) == {"i_grimoire": "darvill_house"}  # not the letters, nor the doctor's whisky
    st.item_owners["i_letters"] = "ratger_house"  # brought back and left in their room
    assert fog.things(m, st)["i_letters"] == "ratger_house"


def test_the_example_says_who_was_met_where_and_the_weather_over_it():
    m = Module.load(EXAMPLE)
    st = Game.new(m, [m.pregens[0].model_copy(deep=True)])
    st.visited, st.location = ["square", "tower"], "tower"
    st.clues, st.sky = ["c_notice"], "fog"
    st.npc("warden").met = True
    c = mapview.chart(m, st)
    tower = mapview.about(m, st, c, "tower").plain
    assert tower.startswith("◉ 钟楼  你在这里 · 在广场里") and "见过 老守钟人" in tower and "天气" not in tower  # indoors
    square = mapview.about(m, st, c, "square").plain
    assert "里面 钟楼" in square and "线索 广场告示" in square and "天气 ≋ 雾" in square
    legend = mapview.legend(c, mapview.tree(c, mapview.default_open(c))).plain
    assert "听说过，还不知道怎么去：○ 报社" in legend and "地点内部" in legend


# -- a shared table --------------------------------------------------------------------------
@pytest.mark.parametrize("folder", BUNDLED, ids=lambda f: f.name)
def test_a_shared_table_draws_the_same_map_from_its_copy_and_holds_no_more(folder):
    m = Module.load(folder)
    for st in plays(m, random.Random(7), games=5, turns=8):
        book = table(m, st)
        host = fog.chart(m, st)
        copy, view = Module.model_validate(book), GameState.model_validate(st.model_dump(mode="json", exclude=PRIVATE))
        screen = fog.chart(copy, view)
        assert screen == host
        assert all(mapview.about(copy, view, screen, p).plain == mapview.about(m, st, host, p).plain for p in host.names)
        ways, roads = {frozenset(w) for w in host.roads + host.trails}, {frozenset(w) for w in host.roads}
        stopped = set(st.visited) | {st.location}
        read = [fog.plain(text) for text in fog.kept(m, st)]
        for loc in book["locations"]:
            assert not loc["keeper"] and not loc["features"]
            assert all(frozenset((loc["id"], e)) in ways for e in loc["exits"])  # no way they have not found
            assert all(frozenset((loc["id"], e)) in roads for e in loc["travel_minutes"])  # minutes only of roads walked
            assert loc["within"] is None or (loc["id"] in host.names and loc["within"] in host.names)
            if loc["id"] not in stopped:
                assert not loc["read_aloud"] and all(any(fog.plain(a) in text for text in read) for a in loc["aliases"])
        assert {c["id"] for c in book["clues"]} <= set(st.clues)
        assert not any(n["secrets"] or n["knows"] for n in book["npcs"])


def test_a_way_not_found_is_not_in_a_shared_table_s_copy(zhui):
    st = opened(zhui)
    st.visited, st.location = ["huochezhan", "mudi"], "mudi"
    st.add("npc", "那座陵墓？我还有活要干。", who="梅洛迪亚斯·杰弗逊")  # heard of, but not where the way in is
    st.heard = fog.heard(zhui, st)
    cemetery = next(loc for loc in table(zhui, st)["locations"] if loc["id"] == "mudi")
    assert "lingmu" not in cemetery["exits"] and "lingmu" in mapview.chart(zhui, st).rumours
    st.clues = ["c_footprints"]  # the footprints lead to its door
    cemetery = next(loc for loc in table(zhui, st)["locations"] if loc["id"] == "mudi")
    assert "lingmu" in cemetery["exits"]


# -- the format -------------------------------------------------------------------------------
def test_within_is_optional_and_checked(zhui):
    assert Location(id="x", name="某处", read_aloud="").within is None  # old modules and saves load as they were
    assert "within" in Location.model_json_schema()["properties"] and "里面" in Location.model_fields["within"].description
    assert "within" in NewLocation.model_json_schema()["properties"]  # the director can say so for a place it adds
    old = Improv.model_validate({"kind": "后果", "reason": "旧存档", "locations": [{"id": "shed", "name": "棚屋", "read_aloud": "", "exits": ["mudi"]}]})
    assert old.locations[0].within is None
    m = Module.load(EXAMPLE)
    assert m.location("tower").within == "square" and not [i for i in graph.audit(m) if i.level == "error"]
    broken = m.model_copy(deep=True)
    broken.location("office").within, broken.location("square").within = "nowhere", "tower"  # a loop: tower in square in tower
    errors = [i.text for i in graph.audit(broken) if i.level == "error"]
    assert any("office" in e and "nowhere" in e for e in errors) and any("square" in e and "绕回" in e for e in errors)
    c = mapview.chart(broken, everywhere(broken, "tower"))
    assert len(c.within) == 1 and mapview.tree(c, set(c.names)).shown  # the loop is cut, the map still drawn
    bad = Improv(kind="后果", reason="…", locations=[NewLocation(id="loft", name="阁楼", read_aloud="", exits=["jinboer_jia"], within="nowhere")])
    assert improv.check(zhui, bad, at="jinboer_jia")


# -- on screen --------------------------------------------------------------------------------
async def test_f3_opens_a_map_to_pick_from_open_and_fold(zhui):
    st = everywhere(zhui, "huochezhan")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.push_screen(mapview.MapModal(zhui, st))
        await pilot.pause()
        screen = app.screen
        drawn = screen.query_one("#map", Static).content.plain
        assert "◉" in drawn and "金博尔家 ▸ 1" in drawn and screen.picked == "huochezhan"
        assert "你在这里" in screen.query_one("#place", Static).content.plain
        hint = screen.query_one("#map-keys", Static)
        assert hint.region.bottom <= app.size.height and "Esc" in str(hint.content)  # the map scrolls; the keys stay on screen
        await pilot.press("tab")
        assert screen.picked != "huochezhan"
        screen.picked = "mudi"
        await pilot.press("enter")
        await pilot.pause()
        assert "mudi" in screen.laid.frames and "公墓 ▾" in screen.query_one("#map", Static).content.plain
        await pilot.press("enter")
        await pilot.pause()
        assert "mudi" not in screen.laid.frames
        for key in ("right", "left", "down", "up"):  # the place picked stays in view as it moves
            await pilot.press(key)
            await pilot.pause()
            view = screen.query_one(mapview.MapView)
            y = screen.laid.box(screen.picked)[2]
            assert view.scroll_y <= y < view.scroll_y + view.scrollable_content_region.height
        x0, _, y = screen.laid.box("jiefang")
        await pilot.click(mapview.MapCanvas, offset=(x0 + 2, y))
        await pilot.pause()
        assert screen.picked == "jiefang" and "艾尔斯伯里大街" in screen.query_one("#place", Static).content.plain
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, mapview.MapModal)


async def test_a_map_bigger_than_its_window_opens_on_where_they_are_and_drags():
    m = Module.load(ROOT / "modules" / "fallout3-youxierenyouyanbujin")
    st = everywhere(m, "jensen_farm")
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(64, 18)) as pilot:
        await app.push_screen(mapview.MapModal(m, st))
        await pilot.pause(0.2)
        screen = app.screen
        view = screen.query_one(mapview.MapView)
        assert screen.laid.height > view.scrollable_content_region.height  # it scrolls
        y = screen.laid.box("jensen_farm")[2]
        assert view.scroll_y <= y < view.scroll_y + view.scrollable_content_region.height
        was = view.scroll_offset
        up = view.scroll_y + view.scrollable_content_region.height < screen.laid.height  # more of it below: pull it up
        rows = range(int(view.scroll_y) + 2, int(view.scroll_y) + 4) if up else range(int(view.scroll_y), int(view.scroll_y) + 2)
        blank = next((x, y) for y in rows for x in range(screen.laid.width) if screen.laid.at(x, y) is None)
        await pilot.mouse_down(mapview.MapCanvas, offset=blank)
        await pilot.hover(mapview.MapCanvas, offset=(blank[0], blank[1] + (-2 if up else 2)))
        await pilot.mouse_up(mapview.MapCanvas)
        await pilot.pause()
        assert view.scroll_offset != was and screen.picked == "jensen_farm"  # a drag moves the map, picks nothing


async def test_g_on_the_map_puts_going_there_in_the_command_box(canned):  # noqa: F811
    m = Module.load(EXAMPLE)
    lead = m.pregens[0].model_copy(deep=True)
    state = Game.new(m, [lead], slot="map-go", human=[lead.id])
    state.visited, state.location = ["square", "tower"], "square"
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(GameScreen(m, state, resume=True))
        await pilot.pause(0.5)
        said = len(state.log)
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, mapview.MapModal)
        app.screen.picked = "tower"
        await pilot.press("g")
        await pilot.pause()
        box = app.screen.query_one("#command", Input)
        assert box.value == "我去钟楼" and box.has_focus and len(state.log) == said  # in the box, not sent
