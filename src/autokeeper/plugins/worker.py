"""Small standalone child process. Only JSON crosses the host boundary."""

from __future__ import annotations

import builtins
import json
import sys


def limit_resources():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass


def audit(event, args):
    if event in ("open", "os.system", "os.exec", "os.posix_spawn", "subprocess.Popen") or event.startswith(("socket.", "os.spawn", "ctypes.")):
        raise PermissionError("Plugin IO is unavailable")


class API:
    def _request(self, kind, **args):
        print(json.dumps({"request": kind, **args}, ensure_ascii=False), flush=True)
        reply = json.loads(sys.stdin.readline())
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply["result"]

    def roll(self, expression):
        """Return the individual dice, e.g. roll('2D10') -> [3, 8]."""
        return self._request("roll", expression=expression)

    def check(self, who, skill, difficulty="常规"):
        """Return passed/value/target/text, adjudicated by the host's CoC rules."""
        return self._request("check", who=who, skill=skill, difficulty=difficulty)


def main():
    payload = json.loads(sys.stdin.readline())
    limit_resources()
    sys.addaudithook(audit)
    allowed = (
        "str int float bool dict list set tuple len range enumerate zip min max sum sorted reversed abs round "
        "any all isinstance Exception ValueError KeyError IndexError TypeError ZeroDivisionError"
    ).split()
    scope = {"__builtins__": {name: getattr(builtins, name) for name in allowed}}
    try:
        exec(compile(payload["code"], "<plugin>", "exec"), scope)
        result = scope["handle"](payload["event"], payload["context"], API())
        print(json.dumps({"output": result}, ensure_ascii=False, allow_nan=False), flush=True)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
