"""BCDice client.

All dice in AutoKeeper are rolled by BCDice (https://bcdice.org), the dice engine most
Japanese TRPG tools use. We run its npm package behind a tiny JSON-lines bridge
(bridge/bcdice_bridge.mjs) and keep one Node process per client.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Self

DEFAULT_SYSTEM = "Cthulhu7th:ChineseTraditional"
TABLE_SYSTEM = "Cthulhu7th"  # madness / phobia tables only exist in the Japanese system


def _default_bridge() -> Path:
    env = os.environ.get("AUTOKEEPER_BCDICE_BRIDGE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "bridge" / "bcdice_bridge.mjs"


class Level(IntEnum):
    """Success levels of a CoC 7th percentile roll, ordered so that comparisons work."""

    FUMBLE = 0
    FAILURE = 1
    REGULAR = 2
    HARD = 3
    EXTREME = 4
    CRITICAL = 5

    @property
    def zh(self) -> str:
        return LEVEL_ZH[self]

    @property
    def ok(self) -> bool:
        return self >= Level.REGULAR


LEVEL_ZH = {
    Level.FUMBLE: "大失败",
    Level.FAILURE: "失败",
    Level.REGULAR: "常规成功",
    Level.HARD: "困难成功",
    Level.EXTREME: "极难成功",
    Level.CRITICAL: "大成功",
}

# BCDice labels (Traditional Chinese and Japanese systems) -> Level
_LABELS = {
    "大成功": Level.CRITICAL,
    "極限成功": Level.EXTREME,
    "困難成功": Level.HARD,
    "一般成功": Level.REGULAR,
    "大失敗": Level.FUMBLE,
    "失敗": Level.FAILURE,
    "クリティカル": Level.CRITICAL,
    "イクストリーム成功": Level.EXTREME,
    "ハード成功": Level.HARD,
    "レギュラー成功": Level.REGULAR,
    "ファンブル": Level.FUMBLE,
}


@dataclass
class Roll:
    command: str
    text: str
    success: bool = False
    failure: bool = False
    critical: bool = False
    fumble: bool = False
    rands: list[list[int]] = field(default_factory=list)

    @property
    def parts(self) -> list[str]:
        return [p.strip() for p in self.text.split("＞")]

    @property
    def total(self) -> int | None:
        """Final number of a plain roll such as 1D6 or 3D6*5."""
        m = re.fullmatch(r"-?\d+", self.parts[-1])
        return int(m.group()) if m else None

    @property
    def level(self) -> Level | None:
        """Success level of a CC check, as judged by BCDice."""
        return _LABELS.get(self.parts[-1])

    @property
    def value(self) -> int | None:
        """The d100 value a CC check settled on (after bonus / penalty dice)."""
        if self.level is None or len(self.parts) < 2:
            return None
        m = re.fullmatch(r"\d+", self.parts[-2])
        return int(m.group()) if m else None

    @property
    def candidates(self) -> list[int]:
        """All d100 results rolled for a check with bonus / penalty dice."""
        if self.level is None or len(self.parts) < 3:
            return []
        return [int(x) for x in re.findall(r"\d+", self.parts[-3])]


class BCDiceError(RuntimeError):
    pass


class BCDice:
    """Synchronous client; safe to share between threads."""

    def __init__(self, system: str = DEFAULT_SYSTEM, bridge: Path | None = None, node: str = "node"):
        self.system = system
        self.bridge = bridge or _default_bridge()
        self.node = shutil.which(node) or node
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self.version: str | None = None

    # -- process management -------------------------------------------------
    def _start(self) -> None:
        if not self.bridge.exists():
            raise BCDiceError(f"找不到 BCDice 桥接脚本：{self.bridge}（先运行 autokeeper setup）")
        proc = subprocess.Popen(
            [self.node, str(self.bridge)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert proc.stdout is not None
        hello = json.loads(proc.stdout.readline() or "{}")
        if not hello.get("ready"):
            raise BCDiceError("BCDice 桥接进程没有正常启动（检查 bridge/node_modules 是否已安装）")
        self.version = hello.get("version")
        self._proc = proc

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- rolling --------------------------------------------------------------
    def roll(self, command: str, system: str | None = None) -> Roll:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            req_id = self._next_id
            self._next_id += 1
            req = {"id": req_id, "system": system or self.system, "command": command}
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        if not line:
            raise BCDiceError("BCDice 桥接进程意外退出")
        res = json.loads(line)
        if not res.get("ok"):
            raise BCDiceError(f"BCDice 无法执行“{command}”：{res.get('reason')}")
        return Roll(
            command=command,
            text=res["text"],
            success=res["success"],
            failure=res["failure"],
            critical=res["critical"],
            fumble=res["fumble"],
            rands=res.get("rands") or [],
        )

    def total(self, expr: str) -> int:
        """Roll a plain expression such as 1D6+1 and return the number."""
        r = self.roll(expr)
        if r.total is None:
            raise BCDiceError(f"“{expr}”不是一个能算出数值的掷骰表达式")
        return r.total

    def check(self, target: int, bonus: int = 0) -> Roll:
        """CoC percentile check against ``target``; bonus > 0 bonus dice, < 0 penalty dice."""
        target = max(1, int(target))
        cmd = f"CC({bonus:+d})<={target}" if bonus else f"CC<={target}"
        return self.roll(cmd)

    def table(self, name: str) -> str:
        """Roll one of BCDice's CoC 7th tables (BMR, BMS, PH, MA ...); text is Japanese."""
        return self.roll(name, system=TABLE_SYSTEM).text


_shared: BCDice | None = None


def shared() -> BCDice:
    global _shared
    if _shared is None:
        _shared = BCDice()
    return _shared
