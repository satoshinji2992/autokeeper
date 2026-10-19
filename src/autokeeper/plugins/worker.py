"""Small standalone child process. Only JSON crosses the host boundary.

The parent runs it with ``-I -S`` (isolated interpreter, no site), an empty environment and a
throwaway cwd, and caps its wall-clock time and output size. This process adds the rest: POSIX
resource limits where they exist, an audit hook that refuses file, network, subprocess and
import, and an allow-listed ``__builtins__``. Model-written code is validated (plugins.schema
.check) before it ever gets here; these are the second and third walls, not the only one.
"""

from __future__ import annotations

import builtins
import json
import sys

# Only pure data and arithmetic. No open/eval/exec/compile/getattr, no type/object/super (class
# and introspection tricks), no __import__. A plugin that needs more is out of scope by design.
ALLOWED_BUILTINS = (
    "str int float bool dict list set frozenset tuple len range enumerate zip map filter "
    "min max sum sorted reversed abs round divmod pow any all isinstance repr ord chr "
    "Exception ValueError KeyError IndexError TypeError ZeroDivisionError ArithmeticError StopIteration"
).split()

# Audit events that reach the outside world. The plugin has no module to call them with, so
# refusing them is defence in depth: if some allowed call tried one, it stops here.
BLOCKED_EVENTS = frozenset({"open", "import"})
BLOCKED_PREFIXES = (
    "os.", "socket.", "subprocess.", "ctypes.", "pty.", "shutil.", "winreg.", "msvcrt.",
    "urllib.", "webbrowser.", "ftplib.", "smtplib.", "http.", "glob.", "fcntl.", "mmap.",
)


def limit_resources():
    """Cap CPU, address space and file size where the platform allows (POSIX). On Windows
    these are unavailable and the parent's wall-clock kill is the only time bound."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass


def audit(event, args):
    if event in BLOCKED_EVENTS or event.startswith(BLOCKED_PREFIXES):
        raise PermissionError(f"插件不能访问 {event}")


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
    for stream in (sys.stdin, sys.stdout):  # -I ignores PYTHONIOENCODING; Windows would use its ANSI code page
        stream.reconfigure(encoding="utf-8")
    payload = json.loads(sys.stdin.readline())
    # Compile before the hook (compiling the plugin is not the plugin's own import), then limit
    # and arm the hook so every event the plugin raises while it runs is judged.
    code = compile(payload["code"], "<plugin>", "exec")
    limit_resources()
    sys.addaudithook(audit)
    scope = {"__builtins__": {name: getattr(builtins, name) for name in ALLOWED_BUILTINS}}
    try:
        exec(code, scope)
        result = scope["handle"](payload["event"], payload["context"], API())
        print(json.dumps({"output": result}, ensure_ascii=False, allow_nan=False), flush=True)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
