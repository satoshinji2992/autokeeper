"""Classify interrupted work without retrying player choices or permanent model limits."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .. import config
from ..harness.models import ContextBudgetExceeded, StructuredOutputError, refused, transient
from ..harness.spend import BudgetExceeded

ATTEMPTS = 1  # one journal resume after the model channel's own transport/format recovery
DELAY = 0.5


def causes(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    while exc is not None and all(exc is not previous for previous in out):
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


def recoverable(exc: BaseException) -> bool:
    failures = causes(exc)
    if any(isinstance(error, (BudgetExceeded, ContextBudgetExceeded)) or refused(error) for error in failures):
        return False
    return any(transient(error) or isinstance(error, (StructuredOutputError, ValidationError, json.JSONDecodeError))
               for error in failures)


def explanation(exc: BaseException) -> str:
    failures = causes(exc)
    if any(isinstance(error, BudgetExceeded) for error in failures):
        return "已达到房主设置的费用上限，请房主调整费用设置后继续；重复重试无法恢复。"
    if any(isinstance(error, ContextBudgetExceeded) for error in failures):
        return "本次资料超过模型的上下文上限，请房主调整模型或上下文设置后继续。"
    return config.plain(failures[-1]) + "。"
