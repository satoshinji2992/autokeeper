from __future__ import annotations

import shutil
from collections import deque

import pytest

from autokeeper.rules.bcdice import BCDice, Roll, _default_bridge
from autokeeper.rules.coc import Investigator, Rules

LABEL = {"critical": "大成功", "extreme": "極限成功", "hard": "困難成功", "regular": "一般成功", "failure": "失敗", "fumble": "大失敗"}


class FakeDice:
    """Scripted stand-in for BCDice: queue check values and plain totals."""

    def __init__(self) -> None:
        self.values: deque[int] = deque()
        self.totals: deque[int] = deque()
        self.tables: list[str] = []

    def queue(self, *values: int) -> FakeDice:
        self.values.extend(values)
        return self

    def queue_totals(self, *totals: int) -> FakeDice:
        self.totals.extend(totals)
        return self

    def check(self, target: int, bonus: int = 0) -> Roll:
        v = self.values.popleft()
        fumble_at = 100 if target >= 50 else 96
        if v == 1:
            label = "critical"
        elif v >= fumble_at:
            label = "fumble"
        elif v <= target // 5:
            label = "extreme"
        elif v <= target // 2:
            label = "hard"
        elif v <= target:
            label = "regular"
        else:
            label = "failure"
        text = f"(1D100<={target}) 獎勵・懲罰骰[{bonus}] ＞ {v} ＞ {v} ＞ {LABEL[label]}"
        return Roll(f"CC<={target}", text, success=v <= target, failure=v > target)

    def roll(self, expr: str) -> Roll:
        return Roll(expr, f"({expr}) ＞ {self.totals.popleft()}")

    def total(self, expr: str) -> int:
        return self.totals.popleft()

    def table(self, name: str) -> str:
        self.tables.append(name)
        return f"{name} 表结果"


@pytest.fixture
def fake() -> FakeDice:
    return FakeDice()


@pytest.fixture
def rules(fake: FakeDice) -> Rules:
    return Rules(dice=fake)  # type: ignore[arg-type]


@pytest.fixture
def inv() -> Investigator:
    return Investigator(
        id="chen",
        name="陈墨",
        occupation="记者",
        age=28,
        characteristics={"力量": 50, "体质": 60, "体型": 55, "敏捷": 65, "外貌": 60, "智力": 75, "意志": 60, "教育": 70},
        luck=55,
        skills={"侦查": 60, "图书馆使用": 70, "心理学": 45, "外语(英语)": 40},
    )


needs_node = pytest.mark.skipif(
    shutil.which("node") is None or not (_default_bridge().parent / "node_modules" / "bcdice").exists(),
    reason="BCDice bridge not installed",
)


@pytest.fixture(scope="session")
def bcdice():
    with BCDice() as dice:
        yield dice
