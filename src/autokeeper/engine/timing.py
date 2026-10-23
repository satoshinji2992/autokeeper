"""A synchronous time mutation can yield before crossing another scene's live boundary."""

from __future__ import annotations


class SceneTimeBoundary(Exception):
    def __init__(self, end: int):
        super().__init__(f"该行动须等待其他现场确认至第 {end} 分钟")
        self.end = end


class SceneResourceConflict(ValueError):
    """A prepared result needs one joint ruling, with both original attempts retained."""

    def __init__(self, details: dict):
        super().__init__("两处行动涉及同一人物或物品，需先共同裁定；原行动与已结算结果已保留")
        self.details = details
