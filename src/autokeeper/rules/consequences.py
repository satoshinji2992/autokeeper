"""Deterministic consequences shared by prepared modules and the effect executor."""

from typing import Literal

from pydantic import BaseModel, Field


class Die(BaseModel):
    """An explicit death, already determined by a rule or by the original scenario."""

    kind: Literal["die"] = "die"
    target: str = Field(min_length=1)
    why: str = Field(min_length=1, description="确定死亡的规则或原文依据；可能致死的攻击应走战斗掷骰，不写在这里")
