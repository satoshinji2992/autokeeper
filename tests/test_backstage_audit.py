"""Independent probes for world attention across shared scene cursors."""

from __future__ import annotations

from autokeeper.engine.game import Turn
from autokeeper.world import ops as O
from autokeeper.world import scheduler

from .test_scene_runtime import made


async def test_a_behind_scene_still_collects_new_local_events_after_the_global_calendar_advanced(fake):
    runtime, _, _ = made(fake)
    a, b = runtime.for_member("zhouheng"), runtime.for_member("lin")
    ga, gb = runtime.game(a), runtime.game(b)
    try:
        assert ga.world.apply(O.Enlist(npc="preacher", actor="engine")).ok
        runtime.state.record.simulated = 600
        runtime.state_view(a).minutes = 720
        await ga._live(Turn())
        assert runtime.state.record.simulated == 720 and gb.state.minutes == 600
        message = "布道者在广场收到一封刚送到的求救信。"
        assert gb.world.apply(O.Occur(id="behind-scene-letter", actor="lin", place="square",
                                     participants=["preacher"], text=message, visibility="private")).ok
        await gb._live(Turn())
        assert any(message in note.text for note in scheduler.pending(gb.world, ["preacher"]))
        assert message in gb._keeper_backstage({}, Turn())
        assert runtime.state.record.simulated == 720
    finally:
        await runtime.close()


async def test_attention_does_not_date_a_future_scene_event_before_it_happened(fake):
    runtime, _, _ = made(fake)
    game = runtime.game(runtime.for_member("zhouheng"))
    try:
        assert game.world.apply(O.Enlist(npc="warden", actor="engine")).ok
        game.state.minutes = 720
        future = "守钟人在十二点收到新的恐吓信。"
        assert game.world.apply(O.Occur(id="future-scene-letter", actor="zhouheng", place="tower",
                                       participants=["warden"], text=future, visibility="private")).ok
        await scheduler.advance(game.world, 600, 610, tick="older-local-cursor")
        assert not any(future in note.text for note in scheduler.pending(game.world))
    finally:
        await runtime.close()


async def test_acknowledging_a_later_written_current_event_does_not_consume_an_earlier_written_future_event(fake):
    runtime, _, _ = made(fake)
    game = runtime.game(runtime.for_member("zhouheng"))
    try:
        w = game.world
        assert w.apply(O.Enlist(npc="warden", actor="engine")).ok
        future, current = "十二点的恐吓信已抵达。", "十点的送报人提前来访。"
        w.clock = 720
        assert w.apply(O.Occur(id="future-first", actor="engine", place="tower", participants=["warden"],
                               text=future, visibility="private")).ok
        w.clock = 600
        assert w.apply(O.Occur(id="current-second", actor="engine", place="tower", participants=["warden"],
                               text=current, visibility="private")).ok
        w.clock = None
        future_event = next(event for event in w.rec.events if event.text == future)
        current_event = next(event for event in w.rec.events if event.text == current)
        assert future_event.seq < current_event.seq and future_event.t > current_event.t
        game.state.minutes = 610
        await scheduler.advance(w, 600, 610, tick="current-attention")
        considered = [note for note in scheduler.pending(w) if current in note.text]
        assert considered and not any(future in note.text for note in scheduler.pending(w))
        scheduler.acknowledge(w, {note.id: note.version for note in considered})
        game.state.minutes = 720
        await scheduler.advance(w, 610, 720, tick="future-attention")
        pending = scheduler.pending(w)
        assert any(future in note.text for note in pending)
        assert not any(current in note.text for note in pending)
    finally:
        game.world.clock = None
        await runtime.close()


async def test_attention_does_not_import_a_future_revision_at_an_earlier_calendar_boundary(fake):
    runtime, _, _ = made(fake)
    game = runtime.game(runtime.for_member("zhouheng"))
    try:
        w = game.world
        assert w.apply(O.Enlist(npc="warden", actor="engine")).ok
        assert w.apply(O.AddFact(fact="future_rewrite", text="守钟人尚未决定出门。", fact_kind="secret",
                                 known_by=["warden"], about=["warden"], actor="engine")).ok
        game.state.minutes = 720
        changed = "守钟人改为十二点后带证人出城。"
        assert w.apply(O.Rewrite(target="fact:future_rewrite", text=changed, actor="keeper",
                                 reason="午间才决定的新安排")).ok
        assert w.rec.revisions[-1].t == 720
        await scheduler.advance(w, 600, 610, tick="before-future-revision")
        assert not any(changed in note.text for note in scheduler.pending(w))
    finally:
        await runtime.close()
