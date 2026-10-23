"""Background addresses belong to their investigator, without unlocking unseen scenes."""

from __future__ import annotations

import pytest

from autokeeper import config, paths
from autokeeper.agents.card import Builder, Sheet, Stats
from autokeeper.agents.cast import Action, Cast, Ruling
from autokeeper.engine import fog, graph
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.compiler import _pregens_md
from autokeeper.module.graph import audit
from autokeeper.module.schema import Location, Module
from autokeeper.net.hub import public_card, table
from autokeeper.rules.coc import Rules
from autokeeper.world.store import World

from .test_engine import FakeCast


def home_state() -> tuple[Module, GameState]:
    m = Module.load(paths.EXAMPLE)
    m.events, m.endings, m.foreshadow = [], [], []
    m.locations.append(Location(id="private_home", name="河边私宅", read_aloud="屋内的保险箱敞开着。", exits=["office"]))
    m.location("office").exits.append("private_home")
    lead = m.pregens[0].model_copy(update={"known_locations": ["private_home"]}, deep=True)
    other = lead.model_copy(update={"id": "lin", "name": "林秋", "known_locations": []}, deep=True)
    st = Game.new(m, [lead, other], human=[lead.id, other.id])
    World(m, st)
    return m, st


def test_background_address_survives_save_without_visiting_or_becoming_common_knowledge():
    m, st = home_state()
    st.heard["private_home"] = "河边私宅"  # another seat must not borrow even this global ledger
    restored = GameState.model_validate_json(st.model_dump_json())
    own = fog.personal(m, restored, restored.investigators[0].id)
    assert own.heard["private_home"] == "河边私宅" and "private_home" not in own.visited
    assert "private_home" in fog.chart(m, own).rumours
    for viewer in ("lin", None):
        assert "private_home" not in fog.personal(m, restored, viewer).heard
        assert "private_home" not in {loc["id"] for loc in table(m, restored, viewer=viewer)["locations"]}
    own_book = table(m, restored, viewer=restored.investigators[0].id)
    home = next(loc for loc in own_book["locations"] if loc["id"] == "private_home")
    assert not home["read_aloud"] and not home["art"] and not home["keeper"]
    for viewer in ("lin", None):
        assert public_card(restored.investigators[0].model_dump(), viewer, m, restored)["known_locations"] == []
    assert public_card(restored.investigators[0].model_dump(), restored.investigators[0].id, m, restored)["known_locations"] == ["private_home"]


def test_parser_receives_own_address_and_id_for_single_and_batch_declarations():
    m, st = home_state()
    cast = Cast(config.load(), m)
    for shared in (True, False):
        own = cast._heard(st, "回家", shared=shared)
        assert "河边私宅(private_home)" in own and "destination" in own
        st.active = 1
        other = cast._heard(st, "回家", shared=shared)
        assert "private_home" not in other
        st.active = 0
    assert not st.heard and "private_home" not in st.visited


async def test_only_the_owner_can_take_the_known_route_after_restoring_a_save(fake):
    m, st = home_state()
    st = GameState.model_validate_json(st.model_dump_json())
    move = Action(kind="move", summary="回家", destination="private_home", alone=True)
    cast = FakeCast({"回家": (move, Ruling(needs_roll=False))})
    game = Game(m, st, cast, Rules(dice=fake))
    try:
        assert fog.findable(m, st, "private_home", viewer=st.investigators[0].id)
        assert not fog.findable(m, st, "private_home", viewer="lin")
        await graph.build(game).ainvoke({"input": "回家"})
        assert game.world.place(st.investigators[0].id) == "private_home"
        assert game.world.place("lin") == m.start
        assert "private_home" not in fog.personal(m, st, "lin").visited
    finally:
        await game.close()


def test_invalid_background_ids_fail_module_audit_and_character_initialization():
    m, st = home_state()
    inv = st.investigators[0]
    inv.known_locations = ["missing_home"]
    m.pregens = [inv]
    assert any("已知地点 missing_home 不存在" in issue.text for issue in audit(m))
    with pytest.raises(ValueError, match="missing_home"):
        Game.new(m, [inv])


def test_compiled_keeper_card_lists_the_explicit_address_without_its_scene_contents():
    m, st = home_state()
    m.pregens = [st.investigators[0]]
    compiled = _pregens_md(m)
    assert "河边私宅(private_home)" in compiled and "保险箱" not in compiled


def test_single_player_local_map_uses_the_same_explicit_background_address():
    m, st = home_state()
    st.investigators = st.investigators[:1]
    assert fog.chart(m, st).rumours["private_home"] == "河边私宅"
    assert "private_home" not in st.visited and not st.heard


async def test_character_revision_keeps_background_addresses_and_rejects_unknown_ids(monkeypatch):
    m, st = home_state()
    builder = Builder(config.load(), m)
    builder.card, builder.kind = st.investigators[0], "sheet"

    async def draft(*args, **kwargs):
        return Sheet(characteristics=Stats(**builder.card.characteristics), skills=builder.card.skills)

    monkeypatch.setattr(builder, "_ask_model", draft)
    revised = await builder.build("修改职业说明")
    assert revised.known_locations == ["private_home"]
    revised.known_locations = ["absent"]
    with pytest.raises(ValueError, match="absent"):
        builder._validate_locations(revised)


def test_bundled_background_addresses_follow_the_written_cards_and_family_tie():
    m = Module.load(paths.MODULES / "fallout3-youxierenyouyanbujin")
    cards = {inv.id: inv for inv in m.pregens}
    assert "不仅是你的单位，也是第二个家" in cards["betty"].backstory["重要的地方"]
    assert "储蓄银行投资" in cards["betty"].backstory["重要的东西"]
    assert "他家门口的车" in cards["betty"].backstory["近况"]
    assert "出教堂时" in cards["elois"].backstory["近况"]
    assert "你母亲露希尔刚来电话" in cards["bert"].backstory["近况"]
    assert next(tie.place for tie in m.ties if tie.id == "tie_raymond_father") == "lowry_house"
    assert "在埃尔默牛排馆洗盘子。今天到店" in cards["annabelle"].backstory["近况"]
    assert "教堂里静思" in cards["fred"].backstory["重要的地方"]
    assert "来到商业信托储蓄银行" in cards["fred"].backstory["近况"]
    assert "今晚你上门" in cards["wesley"].backstory["近况"]
    st = Game.new(m, [cards[ident].model_copy(deep=True) for ident in ("bert", "elois")])
    World(m, st)
    assert fog.findable(m, st, "lowry_house", viewer="bert")
    assert not fog.findable(m, st, "lowry_house", viewer="elois")
    assert "劳里" in fog.personal(m, st, "bert").heard["lowry_house"]
    assert "lowry_house" not in fog.personal(m, st, "elois").heard
