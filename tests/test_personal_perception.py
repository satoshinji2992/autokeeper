"""One real combatant, personal appearances and seat-bound declarations, without model calls."""

from __future__ import annotations

import json

import pytest

from autokeeper.engine import audience, perception
from autokeeper.engine.encounter import choices, views
from autokeeper.engine.encounter.plan import DeclaredAction, EncounterTurnPlan
from autokeeper.engine.game import Game
from autokeeper.engine.state import GameState
from autokeeper.module.schema import Module
from autokeeper.net.hub import Hub
from autokeeper.paths import EXAMPLE
from autokeeper.rules.coc import Rules

from .encounter_kit import Keeper, arrange, begin


@pytest.fixture
def table(fake):
    module = Module.load(EXAMPLE)
    module.events, module.endings = [], []
    lead = module.pregens[0].model_copy(deep=True)
    friend = lead.model_copy(update={"id": "lin", "name": "林秋", "portrait": "lin-portrait"}, deep=True)
    friend.characteristics["敏捷"] = 30
    state = Game.new(module, [lead, friend], human=[lead.id, friend.id])
    state.location = "tower"
    cast = Keeper(arrange=lambda *args: EncounterTurnPlan(npcs=[arrange("warden", DeclaredAction(do="defend", summary="防守"))]))
    game = Game(module, state, cast, Rules(dice=fake))
    module.art.update(monster_face="怪物画像", **{"lin-portrait": "林秋真实画像"})
    return game


def disguise(game, **changes):
    action = {"to": ["zhouheng"], "target": "lin", "name": "渗血的怪物", "portrait": "monster_face",
              "side": "foe", "reason": "一次尚未消退的幻觉", **changes}
    assert not perception.check_changes(game, [action])
    perception.apply(game, [action])
    return action


async def test_personal_fight_names_portraits_sides_and_choices_do_not_change_the_real_party(table):
    game = table
    await begin(game, ["warden"])
    disguise(game)
    mine = views.table(game, viewer="zhouheng")
    theirs = views.table(game, viewer="lin")
    spectator = views.table(game)
    assert spectator["choices"] == spectator["risk"] == spectator["presets"] == spectator["answers"] == {}
    assert "渗血的怪物" not in json.dumps(spectator, ensure_ascii=False)
    imagined = next(row for row in mine["fight"]["fighters"] if row["name"] == "渗血的怪物")
    assert imagined["side"] == "foe" and imagined["kind"] == "creature"
    assert imagined["portrait"] == "monster_face" and imagined["hp"] is None and imagined["hp_max"] is None
    assert imagined["id"].startswith("seen-") and "lin" not in imagined["id"]
    real = next(row for row in theirs["fight"]["fighters"] if row["id"] == "lin")
    assert real["name"] == "林秋" and real["side"] == "party" and real["hp"] == game.state.find("lin").hp
    option = next(row for row in mine["choices"]["zhouheng"] if row[0].startswith("攻击 渗血的怪物"))
    assert option[2].startswith("act-") and "lin" not in option[2]
    declared = choices.match(game, "zhouheng", option[2])
    assert declared.attacks[0].target == "lin"
    assert game.set_react("zhouheng", "fight_back") == ""
    after_tab = choices.match(game, "zhouheng", option[2])
    assert after_tab.attacks[0].target == "lin" and after_tab.react == "fight_back"
    with pytest.raises(ValueError, match="失效"):
        choices.match(game, "lin", option[2])
    game.state.encounter.round += 1
    with pytest.raises(ValueError, match="失效"):
        choices.match(game, "zhouheng", option[2])
    assert game.state.encounter.get("lin").side == "party" and game.state.find("lin").name == "林秋"
    await game.close()


async def test_attacking_an_apparent_monster_hurts_only_the_real_investigator_and_replays_consistently(table, fake):
    game = table
    await begin(game, ["warden"])
    game.asking = False
    disguise(game)
    before = game.state.find("lin").hp
    option = next(o for o in choices.options(game, "zhouheng") if o.label.startswith("攻击 渗血的怪物"))
    text = perception.choice_handle(game, "zhouheng", option.value)
    fake.queue(20, 99).queue_totals(2)
    action = await game.encounter_input(text)
    turn = await game.resolve(action, await game.rule(action))
    game.state.active = 1  # both seated investigators declare before the common round settles
    defending = await game.encounter_input("防守")
    turn.absorb(await game.resolve(defending, await game.rule(defending)))
    assert game.state.find("lin").hp == before - 2
    assert not any(e.id == "monster_face" for e in game.world.rec.entities.values())
    raw = [entry for entry in game.state.log if "攻击林秋" in entry.text]
    assert raw and any("攻击渗血的怪物" in entry.text for entry in audience.log(game.state, "zhouheng"))
    assert any("攻击林秋" in entry.text for entry in audience.log(game.state, "lin"))
    saved = GameState.model_validate_json(game.state.model_dump_json())
    assert saved.perceptions["zhouheng"]["lin"].handle == game.state.perceptions["zhouheng"]["lin"].handle
    game.state = saved
    perception.apply(game, [{"to": ["zhouheng"], "target": "lin", "clear": True, "reason": "恢复正常知觉"}])
    assert any("攻击渗血的怪物" in entry.text for entry in audience.log(game.state, "zhouheng"))
    assert all(not entry.presented for entry in audience.log(game.state, "zhouheng"))
    assert turn.fight  # the keeper's original explanation is retained
    await game.close()


async def test_a_new_appearance_does_not_rewrite_yesterdays_history_or_free_prose(table):
    game = table
    old = game.state.add("npc", "昨日林秋说：信上写着林秋的名字。", who="林秋")
    disguise(game)
    current = game.state.add("npc", "这封信署名林秋。", who="林秋")
    original = next(e for e in audience.log(game.state, "zhouheng") if e.text == old.text)
    personal = next(e for e in audience.log(game.state, "zhouheng") if e.text == current.text)
    assert original.who == "林秋" and personal.who == "渗血的怪物"
    projected = perception.project_payload(game, "zhouheng", {
        "t": "welcome", "log": [e.model_dump(mode="json") for e in audience.log(game.state, "zhouheng")],
        "ev": {"type": "keeper_done", "text": "昨日林秋说：信上写着林秋的名字。"},
    })
    assert projected["log"][0]["who"] == "林秋" and projected["log"][-1]["who"] == "渗血的怪物"
    assert projected["ev"]["text"] == "昨日林秋说：信上写着林秋的名字。"
    await game.close()


async def test_reconnect_has_personal_battle_without_real_party_card_portrait_or_mapping(table):
    game = table
    await begin(game, ["warden"])
    disguise(game)
    hub = Hub(EXAMPLE, state=game.state, cast=game.cast, rules=game.rules)
    hub.m, hub.game = game.m, game
    view = hub._view("zhouheng")
    assert [inv["id"] for inv in view["investigators"]] == ["zhouheng"]
    assert "perceptions" not in view and "perception_key" not in view
    payload = perception.project_payload(game, "zhouheng", {
        "t": "welcome", "state": view,
        "module": {"pregens": [i.model_dump(mode="json") for i in game.state.investigators]},
        "seats": [{"investigator": "lin", "name": "林秋", "occupation": "调查记者"}],
        "art": {"lin-portrait": "真实画像", "monster_face": "假象"},
    })
    rendered = json.dumps(payload, ensure_ascii=False)
    assert '"lin"' not in rendered and "林秋" not in rendered and "调查记者" not in rendered
    assert "lin-portrait" not in payload["art"] and "monster_face" in payload["art"]
    assert len(payload["module"]["pregens"]) == 1
    assert game.state.find("lin").name == "林秋"
    await game.close()


async def test_a_hidden_person_has_no_target_or_private_face_until_the_keeper_clears_it(table):
    game = table
    await begin(game, ["warden"])
    disguise(game, visible=False)
    mine = views.table(game, viewer="zhouheng")
    assert not any(row["name"] == "渗血的怪物" for row in mine["fight"]["fighters"])
    assert not any(any(p["target"] == "lin" for p in o.value.get("attacks", []))
                   for o in choices.options(game, "zhouheng"))
    perception.apply(game, [{"to": ["zhouheng"], "target": "lin", "clear": True, "reason": "幻觉消退"}])
    restored = views.table(game, viewer="zhouheng")
    assert any(row["id"] == "lin" for row in restored["fight"]["fighters"])
    assert not perception.appearances(game.state, "zhouheng")
    await game.close()


async def test_personal_feedback_renders_entity_templates_without_changing_other_observers(table):
    game = table
    await begin(game, ["warden"])
    disguise(game)
    event = {"type": "fight_act", "attacker": "zhouheng", "defender": "lin", "defender_name": "林秋",
             "defender_side": "party", "line": "周衡攻击林秋", "_entity_line": "{zhouheng}攻击{lin}"}
    a = perception.project_event(game, "zhouheng", event)
    b = perception.project_event(game, "lin", event)
    assert a["line"] == "周衡攻击渗血的怪物" and a["defender"].startswith("seen-")
    assert a["defender_name"] == "渗血的怪物" and a["defender_side"] == "foe"
    assert b["line"] == "周衡攻击林秋" and b["defender"] == "lin"
    assert "_entity_line" not in a and "_entity_line" not in b and event["defender"] == "lin"
    await game.close()


async def test_npc_and_reveal_events_replace_structured_identity_and_face_without_editing_dialogue(table):
    game = table
    await begin(game, ["warden"])
    disguise(game, target="warden")
    npc = perception.project_event(game, "zhouheng", {"type": "npc", "npc": "warden", "name": "老守钟人",
                                                      "portrait": "warden", "text": "昨晚林秋来找过我。"})
    assert npc["npc"].startswith("seen-") and npc["name"] == "渗血的怪物" and npc["portrait"] == "monster_face"
    assert npc["text"] == "昨晚林秋来找过我。"
    reveal = perception.project_event(game, "zhouheng", {"type": "reveal", "id": "f2", "name": "老守钟人",
                                                         "portrait": "warden"})
    assert reveal["id"].startswith("seen-") and reveal["portrait"] == "monster_face"
    await game.close()


async def test_the_keepers_actual_plan_party_and_encounter_prompts_include_the_personal_mapping(table, monkeypatch):
    from autokeeper import config
    from autokeeper.agents.cast import Cast, PartyInput
    from autokeeper.harness import Reply

    game = table
    await begin(game, ["warden"])
    disguise(game)
    cast = Cast(config.Config(imagegen=False, showcase=False), game.m)
    cast.world = game.world
    sent = []

    async def send(key, session, state, prompt, **kwargs):
        sent.append(prompt)
        title = (kwargs.get("schema") or session.schema)["title"]
        if title == "Plan":
            data = {"action": {"kind": "attack", "summary": "尝试扑倒怪物"}}
        elif title == "PartyPlan":
            data = {"steps": [{"token": token, "plan": {"action": {"kind": "other", "summary": "保持警惕"}}}
                              for token in ("a", "b")]}
        else:
            data = {"action": {"do": "maneuver", "maneuver": "推倒", "summary": "尝试扑倒怪物",
                               "attacks": [{"target": "lin", "with": "natural:徒手"}]}}
        return Reply(text="", data=data)

    monkeypatch.setattr(cast, "_send", send)
    await cast.plan(game.state, "扑倒这个怪物")
    encounter, game.state.encounter = game.state.encounter, None
    await cast.party_plan(game.state, [PartyInput(token="a", who="zhouheng", text="扑倒这个怪物"),
                                       PartyInput(token="b", who="lin", text="我站到门边")])
    game.state.encounter = encounter
    checked = await cast.plan(game.state, "扑倒这个怪物", encounter=views.keeper_text(game), mode="act", check=lambda _: [])
    assert checked.value.action.attacks[0].target == "lin"
    assert len(sent) == 3 and all("zhouheng把lin看作渗血的怪物" in prompt for prompt in sent)
    await cast.close()
    await game.close()


async def test_the_real_network_send_keeps_personal_target_mapping_off_other_seats(table):
    game = table
    await begin(game, ["warden"])
    disguise(game)
    hub = Hub(EXAMPLE, state=game.state, cast=game.cast, rules=game.rules)
    hub.m, hub.game = game.m, game
    received = {}

    class Socket:
        closed = False

        def __init__(self, key):
            self.key = key

        async def send_str(self, text):
            received.setdefault(self.key, []).append(json.loads(text))

    hub.clients = {key: Socket(key) for key in ("a", "b", "watch")}
    hub.sits = {"zhouheng": "a", "lin": "b"}
    for key in hub.clients:
        await hub._send(key, {"t": "sync", "state": hub._view(hub._seat_of(key))})
    mine = json.dumps(received["a"], ensure_ascii=False)
    theirs = json.dumps(received["b"], ensure_ascii=False)
    spectator = json.dumps(received["watch"], ensure_ascii=False)
    assert "渗血的怪物" in mine and "林秋" not in mine and '"lin"' not in mine
    assert "林秋" in theirs and "渗血的怪物" not in theirs and "渗血的怪物" not in spectator
    assert "perception_key" not in mine and "original_name" not in mine
    assert received["watch"][0]["state"]["encounter_public"]["choices"] == {}
    await game.close()


async def test_cleared_appearances_add_no_internal_identity_templates_to_ordinary_events(table):
    game = table
    game.state.perceptions["zhouheng"] = {}  # a cleared observer remains in the saved map
    events = []

    async def emit(event):
        events.append(event)

    game.emit, game.asking = emit, False
    await begin(game, ["warden"])
    for index in (0, 1):
        game.state.active = index
        action = await game.encounter_input("防守")
        await game.resolve(action, await game.rule(action))
    assert any(e["type"] == "fight_start" for e in events)
    assert any(e["type"] == "fight_state" for e in events)
    assert all("_entity_line" not in e and "_entity_lines" not in e for e in events)
    assert not any(entry.presented for entry in game.state.log)
    await game.close()
