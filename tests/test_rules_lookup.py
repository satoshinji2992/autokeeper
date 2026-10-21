"""The rulebooks looked up on demand (rules_lookup.py): reading, splitting by page and heading,
indexing, lookup, and the two places that ask (the arbiter's Ruling.lookup, a /kp question). A tiny
fake corpus, no real books, no model calls, no embedding model."""

from __future__ import annotations

import numpy as np
import pytest

from autokeeper import config, paths, rules_lookup
from autokeeper.agents.cast import KP_QUESTION, Action, Cast
from autokeeper.engine.game import Game
from autokeeper.module.schema import Module
from autokeeper.paths import EXAMPLE

from .test_harness import SCRIPT, SCRIPTED, SEEN, harness_sandbox  # noqa: F401

DROWNING = """# 第六章 战斗

## 其他类型的伤害

守秘人需要裁定随机事件对角色造成的伤害量时，可以在表III中寻找类似的伤害。

## 窒息和溺水

窒息和溺水：每轮都要进行一次体质检定；一旦体质检定失败，每轮都要受到伤害，直到受害者死亡或者能够重新呼吸为止。如果角色处在强体力活动之中，体质检定是困难难度的。
"""

SANITY = """# 第八章 理智

## 恐惧症

恐惧症是对某样东西持续的、不合理的恐惧。患者面对恐惧对象时，所有检定承受一颗惩罚骰，或者干脆逃离。
"""


@pytest.fixture
def books(tmp_path):
    folder = tmp_path / "rule"
    folder.mkdir()
    (folder / "COC7th守秘人规则书.md").write_text(DROWNING, encoding="utf-8")
    (folder / "调查员手册.txt").write_text(SANITY, encoding="utf-8")
    (folder / "卡组.zip").write_bytes(b"PK")  # not a book
    return folder


@pytest.fixture
def module() -> Module:
    return Module.load(EXAMPLE)


def test_a_paged_book_cites_its_printed_page_chapter_and_heading():
    bodies = {3: "急救\n急救可以让濒死的角色稳定下来。" + "伤口处理需要时间。" * 10,
              4: "其他类型的伤害\n很多时候，守秘人需要裁定一些随机事件对角色造成的伤害量。" + "比如被电视砸到脚。" * 10,
              5: "表Ⅲ\n血肉横飞：几乎肯定当场死亡。8D10 遭遇高速直接撞击。" * 2,
              6: "窒息和溺水\n每轮都要进行一次体质检定。" * 3}
    head = {3: "第六章 战斗", 4: "克苏鲁的呼唤 第七版", 5: "第六章 战斗", 6: "克苏鲁的呼唤 第七版"}
    pages = ["封面", "目录", *(f"{head[i]}\n{i - 1}\n{bodies[i]}" for i in range(3, 7)), "克苏鲁的呼唤 第七版\n6\n附录"]
    found = rules_lookup.split_pages("守秘人规则书", pages)
    assert {p.page for p in found} == {2, 3, 4, 5} and all(p.chapter == "第六章 战斗" for p in found)
    first = next(p for p in found if p.page == 3)
    assert first.pdf_page == 4 and first.section == "其他类型的伤害"
    assert "克苏鲁的呼唤 第七版" not in first.text and not first.text.startswith("3")  # running header and page number
    assert first.cite() == "《守秘人规则书》第 3 页，第六章 战斗，其他类型的伤害"
    long = rules_lookup.split_pages("书", ["正文。" * 600])
    assert len(long) > 2 and all(len(p.text) <= rules_lookup.LONGEST for p in long)


def test_the_index_is_built_from_the_books_and_answers_with_book_and_page(books, tmp_path):
    out = tmp_path / "index"
    meta = rules_lookup.build(books, out, embedder=None)
    assert set(meta["books"]) == {"COC7th守秘人规则书.md", "调查员手册.txt"} and meta["embed"] is None
    assert meta["books"]["COC7th守秘人规则书.md"]["title"] == "守秘人规则书" and meta["passages"] >= 2
    assert rules_lookup.ready(out) and not rules_lookup.ready(tmp_path / "none")
    hits = rules_lookup.lookup("溺水了怎么办", folder=out)
    assert hits and hits[0].book == "守秘人规则书" and "体质检定" in hits[0].text and hits[0].section == "窒息和溺水"
    assert rules_lookup.lookup("恐惧症", folder=out)[0].book == "调查员手册"
    assert rules_lookup.lookup("火车时刻表", folder=out) == []  # nothing about it: nothing, not the nearest noise
    quoted = rules_lookup.quote(hits[:1])
    assert quoted.startswith("- 《守秘人规则书》，第六章 战斗，窒息和溺水：窒息和溺水：每轮")
    # Unchanged books are taken from the last index; a changed one is read again.
    (books / "调查员手册.txt").write_text(SANITY + "\n## 躁狂症\n\n" + "躁狂症是对某种行为的强迫，患者无法克制地重复它。" * 3, encoding="utf-8")
    said: list[str] = []
    again = rules_lookup.build(books, out, embedder=None, say=said.append)
    assert any("未变" in line for line in said) and again["books"]["调查员手册.txt"]["passages"] >= 2


def test_with_embeddings_the_vectors_rank_and_are_kept_beside_the_passages(books, tmp_path):
    def embed(texts):  # a stand-in for the local model: one dimension a topic
        return np.array([[float("溺" in t or "窒息" in t), float("恐惧" in t), 0.1] for t in texts])

    out = tmp_path / "index"
    meta = rules_lookup.build(books, out, embedder=embed)
    assert meta["embed"] == "custom" and (out / "vectors.npy").exists()
    index = rules_lookup.load(out, embedder=embed)
    assert index is not None and index.vectors.shape == (meta["passages"], 3)
    assert index.search("窒息")[0].section == "窒息和溺水"


def test_the_books_folder_is_the_checkouts_rule_unless_told(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOKEEPER_RULEBOOKS", raising=False)
    assert paths.rulebooks().name == "rule"
    monkeypatch.setenv("AUTOKEEPER_RULEBOOKS", str(tmp_path / "books"))
    assert paths.rulebooks() == tmp_path / "books"
    assert paths.rules_index() == paths.cache() / "kb" / "rules"
    (paths.USER_DIR).mkdir(parents=True, exist_ok=True)
    (paths.USER_DIR / "config.toml").write_text('rulebooks = "/srv/books"\n', encoding="utf-8")
    assert config.load().rulebooks == "/srv/books"


# -- who asks ---------------------------------------------------------------------------------------
def state_of(module):
    st = Game.new(module, [module.pregens[0].model_copy(deep=True)])
    st.location = "tower"
    return st


async def test_without_an_index_the_arbiter_is_asked_as_before(module):
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    SCRIPT.append({"needs_roll": False})
    await cast.arbiter(state_of(module), Action(kind="other", summary="周衡跳进冰冷的河里"))
    assert "lookup" not in SEEN[-1]["schema"]["properties"] and "lookup：" not in SEEN[-1]["system"]
    assert len(SEEN) == 1


async def test_the_arbiter_asks_for_a_rule_and_rules_again_with_the_books_words(module, books):
    rules_lookup.build(books, paths.rules_index(), embedder=None)
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    rows: list[tuple[str, dict]] = []
    cast.debug_journal = type("J", (), {"append": lambda self, k, d, turn=None: rows.append((k, d)),
                                         "observe": lambda self, *a, **k: None})()
    SCRIPT.extend([{"needs_roll": False, "lookup": "溺水"},
                   {"needs_roll": True, "skill": "体质", "difficulty": "困难", "lookup": "溺水"}])
    st = state_of(module)
    ruling = await cast.arbiter(st, Action(kind="other", summary="周衡跳进冰冷的河里去捞日记"))
    assert SEEN[0]["schema"]["properties"]["lookup"]["type"] == "string" and "lookup：裁定要用到" in SEEN[0]["system"]
    again = SEEN[1]["messages"][-1]["content"]
    assert "【规则书查询】（溺水）" in again and "《守秘人规则书》" in again and "体质检定" in again
    assert ruling.skill == "体质" and ruling.lookup == "" and len(SEEN) == 2  # once: a second ask is not answered
    assert len(st.transcripts["arbiter"]) == 2  # remembered once, as the ruling that knew the rule
    assert ("rules-lookup", {"query": "溺水", "found": ["《守秘人规则书》，第六章 战斗，窒息和溺水"]}) in rows


async def test_a_kp_question_brings_the_books_words_to_the_keeper(module, books):
    rules_lookup.build(books, paths.rules_index(), embedder=None)
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    SCRIPT.append("溺水时每轮过一次体质检定（守秘人规则书，窒息和溺水）。")
    await cast.keeper(state_of(module), f"【第 1 轮】\n{KP_QUESTION}溺水怎么算？\n请以守秘人身份简短回答。")
    sent = SEEN[-1]["messages"][-1]["content"]
    assert "【规则书原文】" in sent and "窒息和溺水：每轮都要进行一次体质检定" in sent
    SCRIPT.append("好的。")
    await cast.keeper(state_of(module), "【第 2 轮】\n行动意图：周衡跳进河里")  # not a question: nothing looked up
    assert "【规则书原文】" not in SEEN[-1]["messages"][-1]["content"]


async def test_the_keepers_lookup_brings_rules_too(module, books):
    rules_lookup.build(books, paths.rules_index(), embedder=None)
    cast = Cast(config.Config(roles={r: SCRIPTED for r in config.DEFAULT_ROLES}), module)
    found = await cast.look_up(state_of(module), "恐惧症发作时检定怎么算")
    assert "【规则书】" in found and "调查员手册" in found
