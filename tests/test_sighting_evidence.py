"""An observed appearance is evidence; a telephone name is not a visual observation."""

from __future__ import annotations

from autokeeper.world import knowledge
from autokeeper.world.ops import Occur
from autokeeper.world.record import KEEPER, PLAYERS, Fact

from .test_world import fresh


def secret(w, text: str, ident: str = "hidden_mark") -> Fact:
    fact = Fact(id=ident, text=text, kind="secret", about=["warden"])
    w.rec.facts[ident] = fact
    return fact


def test_telephone_introduction_does_not_reveal_unseen_appearance():
    w = fresh("square")
    me = w.st.investigator.id
    w.m.npc("warden").look = "衣领内侧纹有黑色六角印记。"
    hidden = secret(w, w.m.npc("warden").look)
    assert w.apply(Occur(text="电话里对方说：叫我陈先生。", actor=KEEPER, witnesses=[me],
                         introduced={"warden": "陈先生"}, visibility="private")).ok
    w.st.npc("warden").met = True  # the summary records a conversation, not seeing a face
    seen = w.rec.sightings[me]["warden"]
    assert seen.name == "陈先生" and not seen.look and not seen.place
    assert not any(entry.source.startswith(("look:", "private-look:"))
                   for entry in knowledge.evidence(w.m, w.st, w.rec, include_private=True))
    w.sync()
    assert hidden.revealed is None and me not in hidden.known_by


def test_an_unintroduced_but_actually_seen_appearance_remains_evidence_after_a_private_redraw():
    w = fresh("tower")
    me = w.st.investigator.id
    w.m.npc("warden").look = "衣领内侧纹有黑色六角印记。"
    observed = secret(w, w.m.npc("warden").look)
    w.observe("tower", [me], "actual_sighting")
    assert not w.rec.sightings[me]["warden"].name
    w.m.npc("warden").look = "衣领上绣着一枚银色徽章。"
    unseen = secret(w, w.m.npc("warden").look, "unseen_redraw")
    w.sync()
    assert observed.revealed is not None and PLAYERS in observed.known_by
    assert unseen.revealed is None and me not in unseen.known_by


def test_personal_appearance_does_not_become_common_evidence():
    w = fresh("tower")
    me = w.st.investigator.id
    friend = w.st.investigator.model_copy(update={"id": "friend", "name": "林秋"}, deep=True)
    w.st.investigators.append(friend)
    w.st.apart[friend.id] = "office"
    w.sync()
    w.m.npc("warden").look = "衣领内侧纹有黑色六角印记。"
    hidden = secret(w, w.m.npc("warden").look)
    w.observe("tower", [me], "private_sighting")
    assert not any(entry.source.startswith("look:") for entry in knowledge.evidence(w.m, w.st, w.rec))
    own = [entry for entry in knowledge.evidence(w.m, w.st, w.rec, include_private=True)
           if entry.source.startswith("private-look:")]
    assert len(own) == 1 and own[0].readers == (me,)
    w.sync()
    assert hidden.revealed is None and me in hidden.known_by
    assert friend.id not in hidden.known_by and PLAYERS not in hidden.known_by


def test_different_observed_appearances_keep_their_own_recipients():
    w = fresh("tower")
    me = w.st.investigator.id
    friend = w.st.investigator.model_copy(update={"id": "friend", "name": "林秋"}, deep=True)
    w.st.investigators.append(friend)
    w.sync()
    w.m.npc("warden").look = "衣领内侧纹有黑色六角印记。"
    first = secret(w, w.m.npc("warden").look)
    w.observe("tower", [me], "first_sighting")
    w.m.npc("warden").look = "衣领上绣着一枚银色徽章。"
    second = secret(w, w.m.npc("warden").look, "second_look")
    w.observe("tower", [friend.id], "second_sighting")
    w.sync()
    assert first.revealed is None and set(first.known_by) == {me}
    assert second.revealed is None and set(second.known_by) == {friend.id}
    assert not any(entry.source.startswith("look:") for entry in knowledge.evidence(w.m, w.st, w.rec))
