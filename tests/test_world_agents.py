"""NPC disclosure follows its knowledge and intent, not matching fragments of secret text."""

from __future__ import annotations

import pytest

from autokeeper.world import knowledge
from autokeeper.world.store import World
from autokeeper.world.tools import Gateway

from .test_world import staged


def test_npc_can_confess_its_own_secret_and_record_who_heard_it():
    world = staged("tower")
    gateway = Gateway(world, "warden")
    secret = world.rec.facts["npc:warden:secrets:0"]
    text = f"守钟人主动坦白：{secret.text}。"
    result = gateway.commit([
        {"kind": "occur", "text": text, "place": "tower", "facts": [secret.id]},
    ], reads=gateway.query()["reads"], request_id="voluntary-confession")

    assert result["ok"]
    assert knowledge.sees(secret, "warden")
    assert knowledge.sees(world.rec.facts[secret.id], "players")
    assert any(event.text == text for event in world.rec.events)
    restored = World(world.m.model_copy(deep=True), world.st.model_copy(deep=True))
    assert knowledge.sees(restored.rec.facts[secret.id], "players")


def test_npc_own_secret_can_be_explicitly_shared_without_a_character_similarity_gate():
    world = staged("tower")
    gateway = Gateway(world, "warden")
    secret = world.rec.facts["npc:warden:secrets:0"]
    result = gateway.commit([
        {"kind": "learn", "fact": secret.id, "who": ["players"], "how": "主动承认自己的隐瞒"},
    ], reads=gateway.query()["reads"], request_id="voluntary-disclosure")

    assert result["ok"] and knowledge.sees(world.rec.facts[secret.id], "players")


@pytest.mark.parametrize("operation", [
    {"kind": "learn", "fact": "truth", "who": ["players"]},
    {"kind": "learn", "fact": "truth", "who": ["warden"], "visibility": "private"},
    {"kind": "occur", "text": "守钟人透露整个案件的真相。", "place": "tower", "facts": ["truth"]},
])
def test_npc_cannot_disclose_or_acquire_a_fact_it_does_not_know(operation):
    world = staged("tower")
    gateway = Gateway(world, "warden")
    before = world.st.model_dump_json()
    result = gateway.preview([operation])

    assert not result["ok"] and world.st.model_dump_json() == before
    assert not knowledge.sees(world.rec.facts["truth"], "warden")
    assert not knowledge.sees(world.rec.facts["truth"], "players")


def test_confession_does_not_grant_control_over_another_character():
    world = staged("tower")
    gateway = Gateway(world, "warden")
    result = gateway.preview([
        {"kind": "occur", "text": world.rec.facts["npc:warden:secrets:0"].text, "place": "tower"},
        {"kind": "move", "entity": "preacher", "to": "tower"},
    ])

    assert not result["ok"]
    assert world.place("preacher") == "square"
    assert not knowledge.sees(world.rec.facts["npc:warden:secrets:0"], "players")
