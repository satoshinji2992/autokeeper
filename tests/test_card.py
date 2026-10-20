"""Character creation: the rules, cards players bring, and the card screen (no model calls)."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin
from textual.widgets import Input, OptionList

from autokeeper import config, imagery, paths, roster
from autokeeper.agents import card as cardmod
from autokeeper.agents.card import Builder, Figure, Photo, local_file, parse_st
from autokeeper.config import Role
from autokeeper.harness import Reply
from autokeeper.harness import models as hm
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE
from autokeeper.rules.creation import ARRAY, accept, make

ST = (
    ".st 力量60str60敏捷50dex50意志40pow40体质55con55外貌65app65教育70edu70体型60siz60智力70灵感70int70"
    "san35理智35幸运55运气55mp8魔法8hp9体力9会计5信用评级30克苏鲁0闪避25斗殴25手枪20图书馆60聆听50心理学45侦查60"
)
STORY = {"个人描述": "瘦削，眼神锐利", "思想与信念": "救救孩子", "重要之人": "母亲", "意义非凡之地": "百草园", "宝贵之物": "一支金不换毛笔", "特质": "烟不离手"}
ANSWERS = {
    "Proposal": {
        "who": "鲁迅，中国现代文学的奠基人",
        "photo": "鲁迅 作家 肖像",
        "questions": [
            {"ask": "在这个年代他是谁？", "options": ["教书的作家", "报社的编辑"]},
            {"ask": "侧重什么本事？", "options": ["学识", "调查"]},
            {"ask": "他什么年纪？", "options": ["四十出头", "二十多岁"]},
        ],
    },
    "Draft": {
        "name": "周樟寿",
        "occupation": "作家（报馆撰稿人）",
        "age": 41,
        "sex": "男",
        "characteristics": {"力量": 40, "体质": 50, "体型": 50, "敏捷": 50, "外貌": 60, "智力": 80, "意志": 70, "教育": 60},
        "formula": "教育×4",
        "credit": 30,
        "occupation_skills": {"图书馆使用": 50, "历史": 50, "心理学": 40, "外语(日语)": 40},
        "interest_skills": {"侦查": 40, "医学": 40},
        "backstory": STORY,
        "inventory": ["毛笔", "烟盒"],
        "note": "以笔为刀的作家",
    },
}


class CardModel(hm.Model):
    """Answers each schema with a fixed reply; counts the calls."""

    calls: list[str] = []

    async def _call(self, messages, *, schema, images, on_delta):
        title = (schema or {}).get("title", "")
        CardModel.calls.append(title)
        data = ANSWERS[title]
        return Reply(text=json.dumps(data, ensure_ascii=False), data=data)


@pytest.fixture
def cards(monkeypatch, tmp_path):
    """Canned models for every role, and saves (the roster) in a temp folder."""
    monkeypatch.setattr(paths, "DATA", tmp_path / "saves")
    monkeypatch.setitem(hm.MODELS, "cardtest", CardModel)
    CardModel.calls = []
    cfg = config.Config(roles={r: Role("cardtest", "cardtest", None) for r in config.DEFAULT_ROLES})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    return cfg


def test_a_built_card_follows_the_creation_rules():
    stats = {"力量": 90, "体质": 10, "体型": 50, "敏捷": 50, "外貌": 60, "智力": 99, "意志": 70, "教育": 60}
    inv, fixes = make(
        "c1", name="鲁迅", occupation="作家", age=45, characteristics=stats, formula="教育×4", credit=40,
        occupation_skills={"图书馆使用": 400, "历史": 100}, interest_skills={"侦查": 500}, luck=50,
    )  # fmt: skip
    assert sorted(inv.characteristics.values(), reverse=True) == list(ARRAY)
    assert inv.characteristics["智力"] == 80 and inv.characteristics["体质"] == 40  # the strongest stays strongest
    assert max(v for k, v in inv.skills.items()) <= 90 and inv.skills["信用评级"] == 40
    assert inv.hp_max == (inv.characteristics["体质"] + inv.characteristics["体型"]) // 10
    assert len(fixes) >= 3  # the array, the occupation points, the interest points


def test_a_brought_card_is_taken_as_written():
    stats = {"力量": 60, "体质": 55, "体型": 60, "敏捷": 50, "外貌": 65, "智力": 70, "意志": 40, "教育": 0}
    inv, fixes = accept("c2", name="沈芸", characteristics=stats, skills={"侦查": 120, "克苏鲁神话": 6, "会计": 5}, san=30, hp=5)
    assert inv.characteristics["教育"] == 50 and inv.skills["侦查"] == 99 and inv.skills["克苏鲁神话"] == 6
    assert "会计" not in inv.skills  # base chances are not worth listing
    assert inv.san == 30 and inv.hp == 5 and inv.luck == 50 and len(fixes) == 3


def test_a_dice_bot_line_is_read_by_code():
    found = parse_st(ST)
    assert found and found["characteristics"]["教育"] == 70 and found["characteristics"]["意志"] == 40
    assert found["skills"]["射击(手枪)"] == 20 and found["skills"]["图书馆使用"] == 60
    assert (found["san"], found["luck"], found["hp"]) == (35, 55, 9)
    assert parse_st("我叫张三，28 岁，力量 60") is None


def test_paths_come_typed_quoted_or_dragged(tmp_path):
    f = tmp_path / "我的 卡.json"
    f.write_text("{}", encoding="utf-8")
    assert local_file(f'"{f}"') == f and local_file(f.as_uri()) == f
    if os.name != "nt":  # on Windows a backslash separates folders and escapes nothing
        assert local_file(str(f).replace(" ", "\\ ")) == f
    assert local_file("力量60") is None


async def test_a_bare_st_line_needs_no_model(cards):
    b = Builder(cards, Module.load(EXAMPLE))
    assert await b.read(ST) == "sheet"
    assert b.card and b.card.characteristics["智力"] == 70 and b.card.san == 35 and b.card.skills["侦查"] == 60
    assert CardModel.calls == [] and "名字" in b.note  # nothing to ask a model; the missing name is pointed out


async def test_a_tavern_card_becomes_a_character_to_build(cards, tmp_path):
    card = {"spec": "chara_card_v2", "data": {"name": "艾琳", "description": "{{char}}是港口酒馆的女招待", "personality": "嘴硬心软", "first_mes": "要喝点什么？"}}
    info = PngImagePlugin.PngInfo()
    info.add_text("chara", base64.b64encode(json.dumps(card, ensure_ascii=False).encode()).decode())
    path = tmp_path / "艾琳.png"
    Image.new("RGB", (64, 64), "gray").save(path, pnginfo=info)
    b = Builder(cards, Module.load(EXAMPLE))
    assert await b.read(str(path)) == "persona"
    assert b.figure and b.figure.source == "角色卡" and "艾琳是港口酒馆的女招待" in b.figure.brief()
    assert b.figure.photos[0].url == str(path)  # the card's picture is the photo


async def _until(pilot, test, rounds: int = 80) -> None:
    for _ in range(rounds):
        if test():
            return
        await pilot.pause(0.05)
    raise AssertionError("the screen never got there")


async def test_a_public_figure_becomes_an_investigator(cards, monkeypatch, tmp_path, skills_dir):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.card import CardScreen
    from autokeeper.tui.screens.setup import SetupScreen

    picture = tmp_path / "luxun.png"
    Image.new("RGB", (120, 160), "white").save(picture)

    async def research(query, provider="auto", **_):
        return Figure(query, title="鲁迅", extract="中国作家。", photos=[Photo(str(picture), title="照片")])

    searched: list[str] = []

    async def find_photos(fig, query=""):  # by the proposal's words, once it is known who this is
        searched.append(query)

    monkeypatch.setattr(cardmod, "research", research)
    monkeypatch.setattr(cardmod, "find_photos", find_photos)
    monkeypatch.setattr(imagery, "preprocess", lambda src, kind, *a, **k: src)  # no background-removal model download in tests
    monkeypatch.setattr(paths, "module_folders", lambda: [EXAMPLE])
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        app.push_screen(SetupScreen(spectate=False))
        await pilot.pause()
        await pilot.press("enter")  # the module
        await pilot.pause()
        setup = app.screen
        choices = setup.query_one("#choices", OptionList)
        choices.highlighted = next(i for i in range(choices.option_count) if choices.get_option_at_index(i).id == "card:new")
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(app.screen, CardScreen))
        screen = app.screen
        screen.query_one(Input).value = "鲁迅"
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "photo")
        await pilot.press("enter")  # the first photo
        await _until(pilot, lambda: screen.step == "ask")
        for _ in range(3):  # the first option of each question
            await pilot.press("space")
            await pilot.pause()
        await _until(pilot, lambda: screen.step == "extra")
        await pilot.press("enter")  # nothing more to ask for
        await _until(pilot, lambda: screen.step == "card")
        assert screen.builder.answers == [("在这个年代他是谁？", "教书的作家"), ("侧重什么本事？", "学识"), ("他什么年纪？", "四十出头")]
        await pilot.press("enter")  # 就用这张卡
        await _until(pilot, lambda: app.screen is setup and setup.step == "companions")
        assert setup.lead and setup.lead.name == "周樟寿" and setup.lead.skills["图书馆使用"] == 70
    kept = roster.saved()
    assert [c.name for c in kept] == ["周樟寿"] and (roster.folder() / f"{kept[0].portrait}.jpg").exists()
    assert CardModel.calls == ["Proposal", "Draft"] and len(searched) == 1


async def test_a_dice_bot_card_is_brought_in(cards, skills_dir):
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.card import CardScreen

    app = AutoKeeperApp(theme="amber")
    kept = []
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        app.push_screen(CardScreen(Module.load(EXAMPLE), start="import"), kept.append)
        await pilot.pause()
        screen = app.screen
        screen.query_one(Input).value = ST
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "card")
        await pilot.press("enter")  # 就用这张卡
        await _until(pilot, lambda: kept)
    assert kept[0].characteristics["外貌"] == 65 and kept[0].luck == 55 and CardModel.calls == []


def test_a_description_is_not_searched_for():
    from autokeeper.agents.card import is_description

    assert is_description("在码头长大、胆子很大的女记者") and is_description("酗酒的退役海军军医")
    assert not is_description("鲁迅") and not is_description("立石凛") and not is_description("Marie Curie")


async def test_a_described_investigator_skips_the_web(cards, monkeypatch):
    async def research(*a, **k):
        raise AssertionError("a description must not be looked up")

    monkeypatch.setattr(cardmod, "research", research)
    b = Builder(cards, Module.load(EXAMPLE))
    fig = await b.look_up("在码头长大、胆子很大的女记者")
    assert fig.source == "描述" and not fig.photos and "女记者" in fig.brief()


async def test_photos_are_searched_for_by_who_it_is_new_names_start_afresh_and_a_local_photo_can_be_used(cards, monkeypatch, tmp_path):
    """The user, 2026-10-20: 「哈姆」 found the right person but Hamtaro's pictures (the photos were
    searched by the bare name, before it was known who this is); 「重新输入姓名」 showed the last
    name's pictures; and a photo from this machine could not be used while making a card."""
    from autokeeper.tui.app import AutoKeeperApp
    from autokeeper.tui.screens.card import CardScreen

    queries: list[str] = []
    web = tmp_path / "web.png"
    Image.new("RGB", (120, 160), "gray").save(web)

    async def research(query, provider="auto", photos=True):
        return Figure(query, title=query, extract="一个人。")

    async def image_search(fig, query=""):
        queries.append(query)
        fig.photos.append(Photo(str(web), title=f"{fig.query} 的照片"))

    monkeypatch.setattr(cardmod, "research", research)
    monkeypatch.setattr(cardmod, "_image_search", image_search)
    monkeypatch.setattr(imagery, "preprocess", lambda src, kind, *a, **k: src)
    mine = tmp_path / "mine.png"
    Image.new("RGB", (120, 160), "white").save(mine)
    app = AutoKeeperApp(theme="amber")
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        app.push_screen(CardScreen(Module.load(EXAMPLE), start="figure"))
        await pilot.pause()
        screen = app.screen
        screen.query_one(Input).value = "鲁迅"
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "photo")
        assert queries == ["鲁迅 作家 肖像"]  # the proposal's words, not the bare name
        screen.shots[0] = tmp_path / "stale.png"
        screen._restart()  # 重新输入姓名
        screen.query_one(Input).value = "许广平"
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "photo")
        assert 0 not in screen.shots or screen.shots[0] != tmp_path / "stale.png"  # this name's own
        choices = screen.query_one(OptionList)
        choices.highlighted = next(i for i in range(choices.option_count) if choices.get_option_at_index(i).id == "photo-local")
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "local")
        screen.query_one(Input).value = str(mine)
        await pilot.press("enter")
        await _until(pilot, lambda: screen.step == "ask")
        assert screen.chosen is not None and Path(screen.builder.photo.url) == mine
