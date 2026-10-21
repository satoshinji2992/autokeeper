"""The keeper card decks (cards.py): reading a card face, and the decks in play. Every deck here is a
tiny fake one built in the test; no real PDF is read."""

from __future__ import annotations

import ctypes
import io
import random

import pytest
from PIL import Image

from autokeeper import cards, paths
from autokeeper.agents.cast import Action, Failure, Ruling
from autokeeper.agents.director import Improv, NewNPC
from autokeeper.agents.director import context as director_context
from autokeeper.cards import Decks, MadnessCard, MishapCard, NpcCard, WeaponCard, _Face, _Line
from autokeeper.engine.game import Game, Turn
from autokeeper.module import improv
from autokeeper.module.schema import Event, Module
from autokeeper.rules.coc import Rules

from .test_engine import make
from .test_guide import canned  # noqa: F401  (a fixture)


def lines(*rows: str) -> list[_Line]:
    """Card lines; a leading "> " marks an indented one (a new paragraph)."""
    out = []
    for row in rows:
        line = _Line(row.removeprefix("> "))
        line.indent = row.startswith("> ")
        out.append(line)
    return out


def face(deck: str, *rows: str) -> _Face:
    return _Face(deck, 2, 0, lines(*rows), (56, 420, 299, 786))  # type: ignore[arg-type]


# -- reading a face -----------------------------------------------------------------------------
def test_a_phobia_card_reads_its_names_quote_paragraphs_and_effect():
    card = cards._madness(face("madness", "> 怕钟症", "> Campanophobia", "> “别敲了！”",
                               "> 你害怕钟声。钟一响你就", "心慌。", "> 听见钟声时，所有技能检定受到", "一个惩罚骰。"))
    assert (card.name, card.english, card.quote, card.kind) == ("怕钟症", "Campanophobia", "“别敲了！”", "phobia")
    assert card.text == "你害怕钟声。钟一响你就心慌。\n听见钟声时，所有技能检定受到一个惩罚骰。"
    assert card.effect == "听见钟声时，所有技能检定受到一个惩罚骰。"


def test_a_bout_card_says_whether_it_is_real_time_or_summary():
    live = cards._madness(face("madness", "> 发呆（即时）", "> Staring", "> 疯狂发作——即时症状", "> “……”", "> 你呆立 1D10 轮。"))
    later = cards._madness(face("madness", "> 走失", "> Wandering", "> 疯狂发作——总结症状", "> 你在别处醒来。"))
    assert (live.kind, live.effect) == ("live", "你呆立 1D10 轮。") and later.kind == "summary"


def test_a_mishap_card_reads_what_it_does_at_once():
    card = cards._mishap(face("mishap", "> 噩运", "> Ill Luck", "> “又来了！”", "> 命运和你作对。你的当前幸运减半。",
                              "> 进行一次理智检定 (1/1D4)。"))
    assert card.summary == "命运和你作对。" and card.luck_halved and card.sanity == "1/1D4"
    hurt = cards._mishap(face("mishap", "> 寒夜", "> Cold Night", "> 你冻醒了（损失 1 点耐久值和 2 点理智值）。"))
    assert (hurt.hp, hurt.san) == ("1", "2")


def test_a_weapon_card_reads_its_numbers_special_note_and_story():
    card = cards._weapon(face("weapon", "> 试作手枪", "> （仿制品）", "> 1920 年代 / 现代", "> 技能：射击（手枪）", "> 基础成功率：20%",
                              "> 伤害：1D8", "> 射程：15 码", "> 每轮攻击次数：1(3)", "> 载弹量：6", "> 故障值：99", "> 价格：$20/$200",
                              "特殊：极难成功造成贯穿，", "伤害 8+1D8。", "> 一把仿造的", "手枪。"))
    assert (card.subtitle, card.era, card.skill, card.base, card.damage, card.range) == (
        "仿制品", "1920年代/现代", "射击（手枪）", 20, "1D8", "15 码")
    assert (card.attacks, card.ammo, card.malfunction, card.price) == ("1(3)", "6", 99, "$20/$200")
    assert card.special == "极难成功造成贯穿，伤害 8+1D8。" and card.impale and card.text == "一把仿造的手枪。"


def test_an_npc_card_reads_its_sheet_and_backstory():
    card = cards._npc(face("npc", "“老钟”赫伯特·维恩 , 钟", "表匠", "STR 40 DEX 70 POW 55 CON 50 SIZ 60",
                           "APP 45 INT 75 EDU 70 LUCK 40 SAN 55", "HP 11 移动 7 DB 0 体格 0 年龄 61", "闪避 35(17/7)",
                           "斗殴 25(12/5)，伤害 1D3", ".32 左轮 30(15/6)，伤害 1D8", "技能：机械维修 70(35/14)，估价",
                           "50(25/10)，语言（英语）70(35/14)，（德语）40(20/8)。",
                           "形象描述：瘦高，戴单片眼镜。", "思想信念：时间不会说谎。", "秘密：他听见过钟里有人", "说话。"))
    assert (card.name, card.occupation, card.age, card.hp, card.move, card.dodge) == ("“老钟”赫伯特·维恩", "钟表匠", 61, 11, 7, 35)
    assert card.characteristics == {"力量": 40, "敏捷": 70, "意志": 55, "体质": 50, "体型": 60, "外貌": 45, "智力": 75, "教育": 70}
    assert card.attacks == ["斗殴 25%，伤害 1D3", ".32 左轮 30%，伤害 1D8"]
    assert card.skills == {"机械维修": 70, "估价": 50, "语言(英语)": 70, "语言(德语)": 40}
    assert card.backstory["秘密"] == "他听见过钟里有人说话。" and card.sex == "男"


def _text(doc, page, x: float, y: float, text: str) -> None:
    import pypdfium2.raw as raw

    obj = raw.FPDFPageObj_NewTextObj(doc, b"Helvetica", 10.0)
    buf = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
    raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(raw.FPDF_WCHAR)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page, obj)


def test_a_deck_pdf_is_read_frame_by_frame_with_a_picture_of_each_face(tmp_path):
    """The layout of the real decks, in Latin letters (the PDF's standard font has no Chinese): a
    notes page, then card frames; an indented line starts a paragraph; the watermark is left out."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    doc = pdfium.PdfDocument.new()
    doc.new_page(595, 842)  # the deck's notes
    page = doc.new_page(595, 842)
    for left, bottom in ((56, 420), (297, 420)):
        frame = raw.FPDFPageObj_CreateNewRect(left, bottom, 243, 366)
        raw.FPDFPageObj_SetStrokeColor(frame, 120, 20, 20, 255)
        raw.FPDFPath_SetDrawMode(frame, 0, True)
        raw.FPDFPage_InsertObject(page, frame)
    for x, y, words in ((130, 760, "Doom"), (125, 740, "Bad Luck"), (120, 720, "“No!”"), (95, 690, "Fate is against"),
                        (72, 674, "you today."), (95, 650, "It lasts."), (371, 760, "Gloom"), (366, 740, "Gloomy Day")):
        _text(doc, page, x, y, words)
    raw.FPDFPage_GenerateContent(page)
    raw.FPDFPage_GenerateContent(doc[0])
    data = io.BytesIO()
    doc.save(data)
    source = tmp_path / "守秘人卡组：惨事卡.pdf"
    source.write_bytes(data.getvalue())

    decks = cards.read(source, tmp_path / "out")
    assert [(c.id, c.name, c.english) for c in decks.mishap] == [("bad_luck", "Doom", "Bad Luck"), ("gloomy_day", "Gloom", "Gloomy Day")]
    doom = decks.mishap[0]
    assert (doom.quote, doom.text, doom.page, doom.slot) == ("“No!”", "Fate is against you today.\nIt lasts.", 2, 0)
    with Image.open(tmp_path / "out" / doom.face) as im:
        assert abs(im.width - 243 * cards.SCALE) < 10 and abs(im.height - 366 * cards.SCALE) < 10  # the frame, its stroke too
    assert cards.coverage(decks)["mishap"]["cards"] == 2


# -- a fake deck in play --------------------------------------------------------------------------
@pytest.fixture
def deck() -> Decks:
    decks = Decks(
        madness=[
            MadnessCard(id="campanophobia", deck="madness", name="怕钟症", english="Campanophobia", kind="phobia",
                        text="你害怕钟声。", effect="听见钟声时所有技能检定受到一个惩罚骰。", face="faces/madness/campanophobia.png"),
            MadnessCard(id="horologomania", deck="madness", name="钟表狂", english="Horologomania", kind="mania",
                        text="你离不开钟表。", effect="看不到钟表时所有技能检定受到一个惩罚骰。"),
            MadnessCard(id="staring", deck="madness", name="发呆", kind="live", text="你呆立 1D10 轮。", effect="你呆立 1D10 轮。"),
            MadnessCard(id="wandering", deck="madness", name="走失", kind="summary", text="你在别处醒来。", effect="你在别处醒来。"),
        ],
        mishap=[
            MishapCard(id="ill_luck", deck="mishap", name="噩运", english="Ill Luck", summary="命运和你作对。",
                       text="命运和你作对。你的当前幸运减半。\n此状态将一直持续。", luck_halved=True),
            MishapCard(id="red_rain", deck="mishap", name="红雨", english="Red Rain", summary="天上下起红雨。",
                       text="天上下起红雨。进行一次理智检定 (1/1D4)。", sanity="1/1D4"),
        ],
        weapon=[
            WeaponCard(id="weapon01", deck="weapon", name=".32 左轮手枪", skill="射击（手枪）", base=20, damage="1D8"),
            WeaponCard(id="weapon02", deck="weapon", name="左轮", skill="射击（手枪）", base=20, damage="1D6"),
            WeaponCard(id="weapon03", deck="weapon", name="米-戈网状装甲", used_by="米-戈"),
        ],
        npc=[
            NpcCard(id="npc01", deck="npc", name="赫伯特·维恩", occupation="钟表匠", age=61, sex="男", hp=11, move=7, dodge=35,
                    characteristics={"力量": 40, "体质": 50, "体型": 60, "敏捷": 70}, skills={"机械维修": 70},
                    attacks=["斗殴 25%，伤害 1D3", ".32 左轮 30%，伤害 1D8"], backstory={"形象描述": "瘦高，戴单片眼镜。"}),
            NpcCard(id="npc02", deck="npc", name="基兰·奥哈拉", occupation="警官", age=46, sex="男", hp=13),
        ],
    )
    cards.save(decks)
    face = paths.keeper_decks() / "faces" / "madness" / "campanophobia.png"
    face.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 12), "white").save(face)
    return decks


class Rolls(random.Random):
    """A random source whose D10 is given."""

    def __init__(self, d10: int):
        super().__init__(0)
        self.d10 = d10

    def randint(self, a: int, b: int) -> int:
        return self.d10


def madness(rules: Rules, inv, fake, *, indefinite: bool = False):
    s = rules.sanity(inv, "0", "0")
    s.temporary, s.indefinite = not indefinite, indefinite
    return s


def test_without_imported_decks_nothing_changes(inv, rules, fake):
    from autokeeper.engine.state import GameState

    state = GameState(module="m", investigators=[inv], location="x")
    fake.queue(99)
    s = madness(rules, inv, fake, indefinite=True)
    assert cards.load() is None and cards.for_sanity(state, inv, s) is None
    assert cards.arbiter_context(state, inv) == "" and cards.npc_menu("警察") == "" and cards.weapon_in(".32 左轮手枪") is None


def test_an_indefinite_insanity_puts_a_phobia_or_mania_on_the_sheet(deck, inv, rules, fake):
    from autokeeper.engine.state import GameState

    state = GameState(module="m", investigators=[inv], location="x")
    fake.queue(99)
    card = cards.for_sanity(state, inv, madness(rules, inv, fake, indefinite=True), random.Random(1))
    assert card is not None and card.kind in ("phobia", "mania")
    assert inv.backstory[cards.STORY_KEY] == card.name and state.drawn[0].card == card.id
    view = cards.view(card, inv.name)
    assert view["kind"] in ("恐惧症", "躁狂症") and view["who"] == inv.name
    assert card.name in cards.arbiter_context(state, inv) and card.effect in cards.arbiter_context(state, inv)


def test_a_bout_draws_a_bout_card_or_on_9_and_10_a_phobia_or_mania(deck, inv, rules, fake):
    from autokeeper.engine.state import GameState

    friend = inv.model_copy(update={"id": "li", "name": "李"})
    alone = GameState(module="m", investigators=[inv], location="x")
    fake.queue(99, 99, 99)
    assert cards.for_sanity(alone, inv, madness(rules, inv, fake), Rolls(3)).id == "wandering"  # nobody to see it: summary
    together = GameState(module="m", investigators=[inv, friend], location="x")
    assert cards.for_sanity(together, inv, madness(rules, inv, fake), Rolls(3)).id == "staring"
    assert cards.for_sanity(together, inv, madness(rules, inv, fake), Rolls(9)).id == "campanophobia"
    assert cards.held(inv) == ["怕钟症"] and "钟表狂" not in inv.backstory.get(cards.STORY_KEY, "")


def test_a_mishap_settles_what_it_does_at_once(deck, inv, rules, fake):
    from autokeeper.engine.state import GameState

    state = GameState(module="m", investigators=[inv], location="x")
    dealt = cards.deal_mishap(state, inv, "ill_luck", rules)
    assert inv.luck == 27 and dealt.lines == ["陈墨幸运 55 → 27"]
    assert "噩运" in cards.mishap_note(inv, dealt) and "当前幸运减半" in cards.mishap_note(inv, dealt)
    assert "惨事〔噩运〕" in cards.standing(state, inv)  # it lasts: the arbiter keeps seeing it
    fake.queue(99).queue_totals(3)
    rain = cards.deal_mishap(state, inv, "red_rain", rules)
    assert rain.sanity is not None and rain.sanity.loss == 3
    assert cards.deal_mishap(state, inv, "no_such_card", rules) is None
    menu = cards.mishap_menu()
    assert "- ill_luck〔噩运〕命运和你作对。" in menu and "red_rain" in menu


def test_a_weapon_card_is_found_by_the_name_a_line_gives(deck):
    assert cards.weapon_in("一把 .32 左轮手枪，上了膛").id == "weapon01"  # the longest name first
    assert cards.weapon_in("一把旧左轮").id == "weapon02"
    assert cards.weapon_in("米-戈网状装甲") is None  # an artifact is not a weapon
    assert cards.weapon(".32 左轮手枪").damage == "1D8"


def test_the_director_may_base_a_new_person_on_a_ready_made_card(deck):
    menu = cards.npc_menu("调查员去警察局找警官")
    assert menu.index("npc02 基兰·奥哈拉，警官") < menu.index("npc01")  # the one the action names comes first
    m = Module.load(paths.EXAMPLE)
    plan = Improv(kind="展开", reason="钟表铺", npcs=[NewNPC(id="vane", name="维恩老先生", role="钟表匠", location=m.start, card="npc01")])
    improv.apply(m, plan)
    vane = m.npc("vane")
    assert vane.stats["敏捷"] == 70 and vane.stats["生命"] == 11 and vane.stats["机械维修"] == 70 and vane.stats["格斗(斗殴)"] == 25
    assert vane.attacks == [".32 左轮 30%，伤害 1D8"] and vane.look == "瘦高，戴单片眼镜。"
    assert "npc01 赫伯特·维恩，钟表匠，61 岁，男" in director_context(m, Game.new(m, [m.pregens[0]]), "展开")


# -- in a game -------------------------------------------------------------------------------------
async def test_a_bout_in_play_draws_a_card_the_screen_and_the_keeper_get(deck, fake, monkeypatch):
    m = Module.load(paths.EXAMPLE)
    m.events.append(Event(id="horror", title="钟里的东西", when="flag:seen", text="", read_aloud="钟里有东西在动。", sanity="1/1D10"))
    m.flags["seen"] = "看见了"
    game, _, events = make(m, fake, {})
    monkeypatch.setattr(cards, "for_sanity", lambda state, inv, s, rng=None: deck.madness[3])
    fake.queue(99, 50).queue_totals(6)  # the SAN roll fails, 6 lost; INT 50 passes: temporary insanity
    turn = Turn()
    await game._flags(["seen"], turn)
    sanity = next(e for e in events if e["type"] == "sanity")
    assert sanity["temporary"] and sanity["card"]["name"] == "走失" and sanity["card"]["kind"] == "疯狂发作·总结症状"
    assert any("抽到疯狂发作·总结症状卡〔走失〕" in n for n in turn.notes)


async def test_a_failed_push_can_deal_a_mishap(deck, fake):
    look = Action(kind="investigate", summary="周衡查看齿轮间", target="齿轮")
    ruling = Ruling(needs_roll=True, skill="侦查", difficulty="困难", clues_on_success=["c_gears"], minutes=10,
                    on_push_fail=Failure(text="齿轮卡死了", mishap="ill_luck"))
    game, _, events = make(Module.load(paths.EXAMPLE), fake, {"x": (look, ruling)}, location="tower")
    luck = game.state.investigator.luck
    fake.queue(99)
    await game.resolve(look, ruling)
    fake.queue(99)
    pushed = await game.push("push")
    assert game.state.investigator.luck == luck // 2
    card = next(e for e in events if e["type"] == "card")
    assert card["name"] == "噩运" and card["who"] == game.state.investigator.name
    assert any("惨事卡〔噩运〕" in n for n in pushed.notes) and game.state.drawn[-1].card == "ill_luck"


async def test_a_bad_night_can_deal_a_mishap(deck, fake):
    rest = Action(kind="wait", summary="周衡在旅馆睡到天亮")
    ruling = Ruling(needs_roll=False, minutes=0, rest=True, mishap="ill_luck")  # no hours pass: nothing timed comes due
    game, _, events = make(Module.load(paths.EXAMPLE), fake, {"x": (rest, ruling)})
    luck = game.state.investigator.luck
    await game.resolve(rest, ruling)
    assert game.state.investigator.luck == luck // 2 and any(e["type"] == "card" for e in events)


async def test_the_dealt_card_comes_up_on_the_screen(canned, deck):  # noqa: F811
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.modals.deck import DeckCardModal

    from .test_guide import open_game, until

    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        screen = await open_game(app, pilot)
        card = cards.view(deck.madness[0], "周衡")
        await screen.handle({"type": "sanity", "who": "周衡", "before": 50, "after": 44, "loss": 6, "indefinite": True,
                             "card": card})
        assert await until(pilot, lambda: isinstance(app.screen, DeckCardModal))
        await pilot.pause(0.2)
        assert "怕钟症" in str(app.screen.query_one(".modal-title").render())
        await pilot.press("escape")
        assert await until(pilot, lambda: app.screen is screen)
        assert any("恐惧症〔怕钟症〕" in str(w.render()) for w in screen.story.children)

