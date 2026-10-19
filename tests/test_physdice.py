"""Physical dice: the throw decides the roll (rules/physdice.py). No model calls, no UI."""

from __future__ import annotations

import json

import numpy as np
import pytest

from autokeeper.rules import physdice as pd
from autokeeper.rules.bcdice import Level, Roll
from autokeeper.rules.coc import Rules
from tests.conftest import needs_node

CHI2_9DOF_P001 = 27.877  # chi-square with 9 degrees of freedom at p = 0.001


def chi2(counts: np.ndarray) -> float:
    expected = counts.sum() / len(counts)
    return float(((counts - expected) ** 2 / expected).sum())


@pytest.mark.parametrize(("kind", "seed"), [("units", 101), ("tens", 202)])
def test_the_d10_is_fair(kind, seed):
    up = pd.faces_up(seed, 5000)
    counts = np.bincount([int(pd.label(kind, f)) for f in up], minlength=100 if kind == "tens" else 10)
    counts = counts[::10] if kind == "tens" else counts
    assert counts.sum() == 5000 and (counts > 0).all()
    assert chi2(counts) < CHI2_9DOF_P001, counts  # p > 0.001: no face favoured


def test_the_faces_are_numbered_as_on_a_real_d10():
    normals = pd.d10()[2]
    digits = pd.numbering()
    assert sorted(digits) == list(range(10))
    for face in range(10):
        opposite = int(np.argmin(normals @ normals[face]))
        assert digits[face] + digits[opposite] == 9
    assert sorted(pd.label("tens", f) for f in range(10)) == [f"{d}0" for d in range(10)]
    assert pd.percentile(0, 0) == 100 and pd.percentile(0, 7) == 7 and pd.percentile(90, 0) == 90 and pd.percentile(40, 2) == 42


def test_bonus_dice_keep_the_lowest_and_penalty_dice_the_highest():
    assert pd.kept([70, 100], 1) == 70 and pd.kept([70, 100], -1) == 100 and pd.kept([55], 0) == 55
    assert pd.kept([55, 65, 15], -2) == 65 and pd.kept([55, 65, 15], 2) == 15
    dice = pd.PhysDice(seed=7)
    for bonus in (-2, -1, 0, 1, 2):
        roll = dice.check(60, bonus)
        t = roll.throw
        assert t is not None and t["kinds"] == ["tens"] * (1 + abs(bonus)) + ["units"]  # separate dice, thrown together
        units = int(t["labels"][-1])
        candidates = [pd.percentile(int(x), units) for x in t["labels"][:-1]]
        assert roll.candidates == candidates and roll.value == pd.kept(candidates, bonus)
        assert roll.level == pd.judge(roll.value, 60) and len(roll.rands) == len(t["kinds"])


def test_a_throw_is_the_same_for_the_same_seed_and_ends_lying_flat():
    a, b = pd.throw(42, tens=2), pd.throw(42, tens=2)
    assert a.faces == b.faces and all(np.array_equal(x, y) for x, y in zip(a.frames, b.frames, strict=True))
    assert pd.throw(43, tens=2).frames[-1].tolist() != a.frames[-1].tolist()
    final = a.frames[-1]
    turns = pd.matrices(final[:, 3:])
    assert np.allclose(final[:, 2], pd.lying())  # on the floor
    assert np.allclose((turns @ pd.d10()[2].T)[:, 2].max(axis=1), 1.0)  # a kite exactly level on top
    assert pd.tops(turns).tolist() == a.faces
    half = np.array(a.tray)
    assert (np.abs(final[:, :2]) < half).all()  # inside the tray
    assert 0.3 < a.seconds < pd.GIVE_UP and len(a.frames) == len(b.frames) > 5


def test_the_event_carries_a_few_kilobytes_that_replay_the_throw():
    t = pd.throw(5, tens=2)
    event = json.loads(json.dumps(t.encode()))
    assert len(json.dumps(event, separators=(",", ":"))) < 8 * 1024
    assert all(abs(c * 1000 - round(c * 1000)) < 1e-6 for c in event["poses"][0][:21])  # three decimals
    centres, turns = pd.decode(event)
    assert centres.shape[1] == turns.shape[1] == 3 and centres.shape[0] >= 3
    assert pd.tops(turns[-1]).tolist() == t.faces == event["faces"]  # it ends on the resting pose
    assert event["labels"] == t.labels


def test_a_throw_fits_the_turn():
    times = sorted(pd.throw(seed, tens=2).ms for seed in range(8))
    assert times[len(times) // 2] < 500  # the target is ~50 ms; this only catches a runaway engine


def test_the_checks_level_is_judged_as_coc_7th():
    assert pd.judge(1, 10) == Level.CRITICAL and pd.judge(100, 99) == Level.FUMBLE
    assert pd.judge(96, 49) == Level.FUMBLE and pd.judge(96, 50) == Level.FAILURE and pd.judge(96, 97) == Level.REGULAR
    assert pd.judge(12, 60) == Level.EXTREME and pd.judge(13, 60) == Level.HARD and pd.judge(30, 60) == Level.HARD
    assert pd.judge(31, 60) == Level.REGULAR and pd.judge(61, 60) == Level.FAILURE


@needs_node
def test_the_level_matches_bcdice(bcdice):
    rng = np.random.default_rng(3)
    for _ in range(120):
        target, bonus = int(rng.integers(1, 100)), int(rng.integers(-2, 3))
        roll = bcdice.check(target, bonus)
        assert pd.judge(roll.value, target) == roll.level, roll.text
    ours = pd.PhysDice(inner=bcdice, seed=1).check(45, -1)
    assert ours.level is not None and ours.value is not None and len(ours.candidates) == 2  # parses like BCDice's own


class Plain:
    def check(self, target, bonus=0):
        return Roll(f"CC<={target}", f"(1D100<={target}) 獎勵・懲罰骰[0] ＞ 50 ＞ 50 ＞ 一般成功")


def test_a_broken_throw_falls_back_to_plain_dice(monkeypatch):
    def broken(*a, **k):
        raise FloatingPointError("the tray caught fire")

    monkeypatch.setattr(pd, "throw", broken)
    dice = pd.PhysDice(inner=Plain(), seed=1)  # type: ignore[arg-type]
    roll = dice.check(60)
    assert roll.value == 50 and roll.throw is None and dice.last is None


def test_rules_throw_physical_dice_unless_given_others(fake):
    assert isinstance(Rules().dice, pd.PhysDice)
    assert Rules(dice=fake).dice is fake  # type: ignore[arg-type]  # tests stay on scripted dice
