from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .schema import Effect, Output, Plugin, check

TIMEOUT = 3.0
MAX_REQUESTS = 40
MAX_OUTPUT = 64000
Request = Callable[[dict], Awaitable[Any]]


class PluginError(Exception):
    pass


def dice_expression(value: object) -> tuple[int, int]:
    match = re.fullmatch(r"([1-9]\d?)[dD]([1-9]\d{0,2})", str(value))
    if not match or not 1 <= int(match[1]) <= 20:
        raise PluginError("掷骰只支持 1–20 颗、每颗 1–999 面，例如 2D10")
    return int(match[1]), int(match[2])


async def execute(plugin: Plugin, event: dict, context: dict, request: Request, *, timeout: float = TIMEOUT) -> Output:
    if problems := check(plugin):
        raise PluginError("；".join(problems))
    payload = json.dumps({"code": plugin.code, "event": event, "context": context}, ensure_ascii=False, allow_nan=False)
    process = None
    with tempfile.TemporaryDirectory(prefix="autokeeper-plugin-") as directory:
        try:
            async with asyncio.timeout(timeout):
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", "-S", str(Path(__file__).with_name("worker.py")),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    cwd=directory, env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, limit=MAX_OUTPUT,
                )
                process.stdin.write((payload + "\n").encode())
                await process.stdin.drain()
                for _ in range(MAX_REQUESTS + 1):
                    line = await process.stdout.readline()
                    if not line:
                        raise PluginError("插件退出，未返回结果")
                    if len(line) > MAX_OUTPUT:
                        raise PluginError("插件输出过大")
                    message = json.loads(line)
                    if "error" in message:
                        raise PluginError(message["error"])
                    if "output" in message:
                        result = Output.model_validate(message["output"])
                        if len(json.dumps(result.state, allow_nan=False)) > 32000:
                            raise PluginError("插件状态超过 32000 字符")
                        for effect in result.effects:
                            if effect.kind not in plugin.capabilities:
                                raise PluginError(f"未声明能力：{effect.kind}")
                        await process.wait()
                        return result
                    if "dice" not in plugin.capabilities or message.get("request") not in ("roll", "check"):
                        raise PluginError("未声明 dice 能力，或请求不受支持")
                    answer = await request(message)
                    process.stdin.write((json.dumps({"result": answer}, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    await process.stdin.drain()
                raise PluginError("插件掷骰/检定请求次数过多")
        except asyncio.CancelledError:
            raise
        except PluginError:
            raise
        except TimeoutError:
            raise PluginError("插件执行超时") from None
        except (OSError, ValueError, TypeError) as exc:
            raise PluginError(f"插件返回格式错误：{type(exc).__name__}：{str(exc)[:2000]}") from None
        finally:
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()


def contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and contains(actual[key], value) for key, value in expected.items())
    return actual == expected


async def verify(plugin: Plugin) -> list[str]:
    if problems := check(plugin):
        return problems
    problems = []
    for case in plugin.tests:
        dice, checks = deque(case.dice), deque(case.checks)

        async def request(message, dice=dice, checks=checks):
            if message["request"] == "roll":
                count, sides = dice_expression(message.get("expression"))
                if len(dice) < count:
                    raise PluginError("测试缺少 dice 返回值")
                values = [dice.popleft() for _ in range(count)]
                if any(not 1 <= value <= sides for value in values):
                    raise PluginError("测试骰值超出骰子范围")
                return values
            if not checks:
                raise PluginError("测试缺少 checks 返回值")
            passed = checks.popleft()
            return {"passed": passed, "value": 1 if passed else 100, "target": 50, "text": "测试检定"}

        try:
            context = {"turn": 1, "minutes": 0, "location": "", "flags": [], "clues": [], "investigators": [], **case.context}
            context["state"] = json.loads(json.dumps(case.state))
            output = await execute(plugin, case.event, context, request)
            if not contains(output.state, case.expected_state):
                problems.append(f"测试 {case.name}：状态与 expected_state 不符")
            expected = [Effect.model_validate(effect) for effect in case.expected_effects]
            if output.effects != expected:
                problems.append(f"测试 {case.name}：effects 与 expected_effects 不符；实际 {output.model_dump()['effects']}；预期 {case.expected_effects}")
            if dice or checks:
                problems.append(f"测试 {case.name}：预设骰值/检定没有全部使用")
        except (PluginError, ValueError) as exc:
            problems.append(f"测试 {case.name}：{exc}")
    return problems
