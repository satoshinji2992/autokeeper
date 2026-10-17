"""A whole game recorded with DeepSeek, replayed with no model, no key and no network.

The cassette holds every reply and every die of an 8-turn game of the bundled sample
module. Replaying it runs the real turn pipeline (intent → arbiter → rules → NPCs → keeper
→ memory); if the engine still reaches the same state, nothing that matters has changed.
Re-record after a deliberate change to the game's flow:

    uv run autokeeper --preset deepseek autoplay src/autokeeper/module/example \\
        --party 1 --turns 8 --record tests/cassettes/clocktower.jsonl
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autokeeper import config
from autokeeper.config import EXAMPLE
from autokeeper.engine import autoplay
from autokeeper.harness import replay

TAPE = Path(__file__).parent / "cassettes" / "clocktower.jsonl"


@pytest.fixture
def tape(monkeypatch, tmp_path, skills_dir):
    monkeypatch.setattr(config, "SAVES", tmp_path / "saves")
    replay.forget(TAPE)
    yield TAPE
    replay.forget(TAPE)


async def test_a_recorded_game_replays_to_the_same_end(tape):
    summary = await autoplay.run(EXAMPLE, party=1, max_turns=8, slot="replay-test", replay=tape)
    recorded = replay.cassette(tape)
    assert summary["fingerprint"] == recorded.summary
    assert not recorded.dice  # every die was rolled again, in order
    # Background folds may be cut short at the end, so the summarizer may leave one unused.
    assert not [a for a, left in recorded.replies.items() if left and a not in ("summarizer", "lore")]


async def test_a_drifting_engine_is_caught(tape, monkeypatch):
    """An engine that rolls one die more than it did on tape fails loudly, not silently."""
    from autokeeper.rules import coc

    real = coc.Rules.check

    def twice(self, *args, **kw):
        self.dice.total("1D6")  # an extra roll the recording never made
        return real(self, *args, **kw)

    monkeypatch.setattr(coc.Rules, "check", twice)
    with pytest.raises(replay.CassetteError):
        await autoplay.run(EXAMPLE, party=1, max_turns=8, slot="replay-drift", replay=tape)
