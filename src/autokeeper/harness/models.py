"""Model channels: a system prompt and a list of messages in, text or JSON out.

Channels keep no conversation state. The harness sends the whole window on every call
(context.py), so any backend can take over mid-game and nothing lives in a vendor's
session store.

    claude     Claude Code through the Claude Agent SDK, used as a bare transport: no
               tools, one turn, `/clear` after every call. Local CLI login (personal
               play on a subscription) or ANTHROPIC_API_KEY.
    langchain  whatever init_chat_model builds: deepseek:, openai:, anthropic:,
               google_genai:, ollama: (local, offline), or any OpenAI-compatible server
               through base_url.
    codex      the Codex CLI (`codex exec`) used the same way: its instructions replaced by
               ours, read-only, nothing kept. Local Codex login.

The contract belongs to the harness, not the provider:

    structured output  the provider's schema mode where it is reliable ("native"); else
                       the schema and an example go into the system prompt and JSON mode
                       is switched on where the API has one ("json"). Either way the
                       result is parsed, validated against the schema, and repaired once.
                       Given an on_delta, Claude Code and JSON mode stream the JSON as it is
                       written; the caller reads what it wants of it (agents/cast._Telling) and
                       says, through its `shown`, whether any of it reached the player yet.
    thinking           Role.effort is mapped to each provider's own knob. Reasoning text
                       comes back in Reply.thinking for traces and is never resent.
    spend              metered calls pass the budget guard and land in the ledger.
    fallback           a role may name a second model (Role.fallback). When the first one
                       still fails after its retries, or the budget is spent, the call goes
                       to the fallback, and the failed model is skipped for COOLDOWN seconds
                       so every agent switches at once instead of each waiting it out.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import weakref
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .. import paths
from ..config import Role, load_env, route
from . import spend, trace

OnDelta = Callable[[str], Awaitable[None]] | None
Msg = dict[str, str]  # {"role": "user" | "assistant", "content": str}
_format_retry: ContextVar[bool] = ContextVar("format_retry", default=False)


class ContextBudgetExceeded(RuntimeError):
    """The configured text budget refused a request before contacting its provider."""

    def __init__(self, parts: dict):
        self.parts = dict(parts)
        super().__init__(f"本次输入 {parts['total_chars']} 字符，超过配置的 {parts['max_input_chars']} 字符预算；"
                         "超限请求尚未发送，当前行动与已有记录已保留。请调整预算或本次资料范围。")


@dataclass
class Reply:
    text: str = ""
    data: Any = None
    thinking: str = ""
    usage: dict = field(default_factory=dict)  # input_tokens (incl. cached), cache_read, output_tokens
    cost_usd: float = 0.0
    seconds: float = 0.0
    repairs: int = 0
    retries: int = 0

    def add(self, other: Reply) -> None:
        for k, v in other.usage.items():
            self.usage[k] = self.usage.get(k, 0) + v
        self.cost_usd += other.cost_usd
        self.thinking += other.thinking
        self.retries += other.retries


# -- transient failures --------------------------------------------------------------------
ATTEMPTS = 3
RETRY_WAIT = (1.0, 20.0)  # first back-off and cap in seconds; exponential with jitter
CALL_TIMEOUT = float(os.environ.get("AUTOKEEPER_TIMEOUT", 600))
# What the player waits on gives up sooner, then retries or goes to the fallback: at ten minutes a
# try, a stuck keeper's plan could hold a turn for half an hour. Long jobs (imports, the director,
# pictures) keep CALL_TIMEOUT; AUTOKEEPER_TIMEOUT, when set, applies to every role.
WAITED_ON = {"plan": 120.0, "intent": 90.0, "arbiter": 120.0, "npc": 120.0, "keeper": 180.0, "investigator": 120.0}


def call_timeout(label: str) -> float:
    if os.environ.get("AUTOKEEPER_TIMEOUT"):
        return CALL_TIMEOUT
    return WAITED_ON.get(label.split(":")[0], CALL_TIMEOUT)
TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
TRANSIENT_NAMES = ("Timeout", "RateLimit", "Connection", "InternalServer", "ServiceUnavailable", "Overloaded", "RemoteProtocol")


def unavailable(exc: BaseException) -> bool:
    """The model itself is out of reach (still failing after its retries, refusing the key, out of
    credit, its program gone), not just this one request: every role on it switches to its fallback
    for a while. A request the model turned down (a schema it would not take) puts it on hold for no one."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in TRANSIENT_STATUS or status in (401, 402, 403)
    return transient(exc) or type(exc).__name__ in ("ProcessError", "CLIConnectionError", "CLINotFoundError", "CodexError",
                                                    "BudgetExceeded")


def refused(exc: BaseException | None) -> bool:
    """The model turned this request down (a schema or a prompt it will not take: 4xx, not a rate
    limit): the same request fails the same way however often it is sent."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in TRANSIENT_STATUS


def transient(exc: BaseException) -> bool:
    """Worth another try: rate limits, overload, time-outs, dropped connections, 5xx.
    Not: budget, auth, bad requests, missing keys, our own bugs."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in TRANSIENT_STATUS
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ == "ClaudeError":  # no status code: Claude Code failed without saying why, worth another go
        return True
    return any(n in type(exc).__name__ for n in TRANSIENT_NAMES)


COOLDOWN = 300.0  # seconds a failed model is skipped in favour of its fallback
_down: dict[tuple, float] = {}  # (backend, model, base_url) -> when to try it again
listeners: list[Callable[[str], None]] = []  # told when a role switches to its fallback


# -- structured output -----------------------------------------------------------------
def parse_json(text: str) -> Any:
    """One complete JSON object, tolerating a code fence or an introduction before it.

    A second object or trailing prose/fields means the response is incomplete, not that its
    first text field is a valid substitute for the missing actions.
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start == -1:
        return None

    def decode(decoder: json.JSONDecoder) -> Any:
        value, end = decoder.raw_decode(text[start:])
        return None if text[start + end:].strip() else value

    try:
        return decode(json.JSONDecoder())
    except json.JSONDecodeError as exc:
        if exc.msg.startswith("Invalid control character"):
            try:
                # Some providers leave literal line breaks in a JSON string. Preserve the
                # whole object, including NPC acts, instead of treating its envelope as speech.
                return decode(json.JSONDecoder(strict=False))
            except json.JSONDecodeError:
                pass
        return None


def _resolve(root: dict, node: dict) -> dict:
    if ref := node.get("$ref"):
        return root.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
    if "anyOf" in node:  # Optional[...] from pydantic
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        return _resolve(root, options[0]) if options else node
    return node


def example(root: dict, node: dict | None = None, depth: int = 0) -> Any:
    """Show every field without inventing optional actions; definitions remain in the schema."""
    node = root if node is None else node
    if "default" in node:  # before resolving Optional: a default null is meaningful
        return deepcopy(node["default"])
    node = _resolve(root, node)
    if "default" in node:
        return deepcopy(node["default"])
    if "const" in node:
        return deepcopy(node["const"])
    if "enum" in node:
        return node["enum"][0]
    kind = node.get("type")
    if kind == "object" or "properties" in node:
        return {k: example(root, v, depth + 1) for k, v in node.get("properties", {}).items()}
    if kind == "array":
        # Pydantic default_factory=list has no schema default. An unconstrained array may
        # stay empty; filling it would demonstrate a goal rewrite, image, injury, etc.
        return [example(root, node.get("items", {}), depth + 1) for _ in range(node.get("minItems", 0))]
    return {"string": "…", "integer": 0, "number": 0, "boolean": False}.get(kind or "")


def compact_schema(schema: dict) -> dict:
    """Drop generated field labels, preserving the contract and any literal payloads.

    A property called title is a field, not an annotation. Likewise default, const
    and examples can contain arbitrary dictionaries. Only walk schema nodes; retain
    the root title because native tool adapters use it as the function name.
    """
    maps = {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}
    nodes = {"items", "additionalProperties", "contains", "propertyNames", "not", "if", "then", "else",
             "unevaluatedProperties", "unevaluatedItems", "additionalItems", "contentSchema"}
    arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}

    def walk(node: Any, *, root: bool = False) -> Any:
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key == "title" and not root:
                continue
            if key in maps and isinstance(value, dict):
                out[key] = {name: walk(child) for name, child in value.items()}
            elif key in nodes:
                out[key] = [walk(child) for child in value] if isinstance(value, list) else walk(value)
            elif key in arrays and isinstance(value, list):
                out[key] = [walk(child) for child in value]
            else:
                out[key] = value
        return out

    return walk(schema, root=True)


def schema_text(schema: dict) -> str:
    return json.dumps(compact_schema(schema), ensure_ascii=False, separators=(",", ":"))


def json_instruction(schema: dict) -> str:
    return (
        "\n\n## 输出格式\n只输出一个 JSON 对象（json），不要代码块，不要其他文字。字段定义（JSON Schema）：\n"
        f"{schema_text(schema)}\n格式示例（只示意结构）："
        f"{json.dumps(example(schema), ensure_ascii=False, separators=(',', ':'))}"
    )


# A tool call's own markup written into a field's words: the next fields went into the text as
# `</text><parameter name="told">["c_map"]` (an NPC under Claude, 2026-10-22). Shown as said, the
# clue it meant to tell was never told: the answer goes back once, as any malformed one.
CALL_MARKUP = re.compile(r"</?(?:parameter|invoke|function_calls|antml:[a-z_]+)\b|</text>")


def _markup(node: Any, path: str = "") -> list[str]:
    if isinstance(node, str):
        found = CALL_MARKUP.search(node)
        return [f"{path or '(根)'}：文字里混进了工具调用的标记 {found.group(0)}；每个字段单独填写，不要把别的字段写进这段文字"] if found else []
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in _markup(v, f"{path}/{k}".lstrip("/"))]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _markup(v, f"{path}/{i}".lstrip("/"))]
    return []


def problems(data: Any, schema: dict) -> list[str]:
    if not isinstance(data, dict):
        return ["没有输出一个可解析的 JSON 对象"]
    from jsonschema import Draft202012Validator

    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    return ([f"{'/'.join(map(str, e.path)) or '(根)'}：{e.message}" for e in errors[:8]] + _markup(data))[:8]


def spoken(schema: dict) -> bool:
    """Only a text-only contract can lose its envelope without losing operational information."""
    return schema.get("required") == ["text"] and set(schema.get("properties", {})) == {"text"}


REPAIR = "上一条输出不符合格式要求：\n{errors}\n请重新输出完整、正确的 JSON 对象（json），只输出 JSON。"


def _shown(on_delta: OnDelta) -> bool:
    """Whether what streamed so far reached the player. A plain stream shows every piece; a
    structured one's reader may hold its first pieces back and says so (agents/cast._Telling), and
    until then a failed call can still be tried again or handed to the fallback."""
    return bool(getattr(on_delta, "shown", True))


# -- the channel contract -----------------------------------------------------------------
class Model:
    mode = "native"  # how structured output is obtained: native | json
    fallback: Model | None = None
    standby_for = ""  # on a fallback: the model it stands in for (shows in traces)

    def __init__(self, spec: Role, system: str, *, label: str = ""):
        self.spec, self.system, self.label = spec, system, label or spec.model

    async def _call(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        raise NotImplementedError

    def input_size(self, messages: list[Msg], schema: dict | None = None) -> dict:
        """Count known text/schema once, without guessing a provider's token window."""
        contract = schema_text(schema) if schema is not None else ""
        formatting = json_instruction(schema) if schema and self.mode == "json" else ""
        body = "".join(message["content"] for message in messages)
        payload = self.system + (formatting or contract) + body
        return {"system_chars": len(self.system), "messages_chars": len(body), "schema_chars": len(contract),
                "format_chars": len(formatting) - len(contract) if formatting else 0,
                "json_instruction_chars": len(formatting), "total_chars": len(payload),
                "schema_format": "compact-v1" if schema is not None else "",
                "utf8_bytes": len(payload.encode("utf-8")), "max_input_chars": self.spec.max_input_chars,
                "notice": "字符预算，不是模型token窗口；schema仅计一次，不含图片token与提供方隐藏包装。"}

    def _input_parts(self, messages: list[Msg], schema: dict | None = None) -> dict:
        supplied = trace.context_for(self)
        measured = self.input_size(messages, schema)
        if supplied and measured["messages_chars"] != supplied.get("messages_chars"):
            supplied["additional_messages_chars"] = measured["messages_chars"] - supplied.get("messages_chars", 0)
        return {**supplied, **measured}

    def _guard_input(self, messages: list[Msg], schema: dict | None = None) -> dict:
        parts = self._input_parts(messages, schema)
        if self.spec.max_input_chars > 0 and parts["total_chars"] > self.spec.max_input_chars:
            trace.progress("context_blocked", agent=self.label, context=parts)
            raise ContextBudgetExceeded(parts)
        return parts

    async def complete(
        self, messages: list[Msg], *, schema: dict | None = None, images: list[bytes] | None = None, on_delta: OnDelta = None
    ) -> Reply:
        if self.fallback is None:
            return await self._complete(messages, schema=schema, images=images, on_delta=on_delta)
        key = (self.spec.backend, self.spec.model, self.spec.base_url)
        if time.monotonic() < _down.get(key, 0.0):
            self._guard_input(messages, schema)  # a cooldown cannot bypass the role's configured budget
            return await self.fallback.complete(messages, schema=schema, images=images, on_delta=on_delta)
        shown = False

        async def watch(piece: str) -> None:
            nonlocal shown
            try:
                await on_delta(piece)  # type: ignore[misc]
            finally:
                shown = watch.shown = shown or _shown(on_delta)  # type: ignore[attr-defined]

        watch.shown = False  # type: ignore[attr-defined]
        try:
            return await self._complete(messages, schema=schema, images=images, on_delta=watch if on_delta else None)
        except ContextBudgetExceeded:
            raise  # input size is a local request policy, not a provider failure
        except Exception as exc:
            if shown:  # the player already read part of this reply; starting over would repeat it
                raise
            if unavailable(exc):
                _down[key] = time.monotonic() + COOLDOWN
                for tell in listeners:
                    tell(f"{self.spec.model} 出错（{type(exc).__name__}），{self.label} 等角色改用备用模型 {self.fallback.spec.model}")
            return await self.fallback.complete(messages, schema=schema, images=images, on_delta=on_delta)

    async def _complete(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        with trace.input_context(self._input_parts(messages, schema), owner=self):
            return await self._complete_traced(messages, schema=schema, images=images, on_delta=on_delta)

    async def _complete_traced(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        observed = trace.begin(self, messages, schema, images)
        started = time.monotonic()
        reply, error = Reply(), ""
        try:
            self._guard_input(messages, schema)
            spend.guard(self.spec.model)
            reply = await self._attempt(messages, schema=schema, images=images, on_delta=on_delta)
            plain = reply.text.strip()
            envelope = plain.startswith(("{", "[", "```")) or bool(re.search(r'\{\s*"text"\s*:', plain))
            if schema is not None and reply.data is None and plain and spoken(schema) and not envelope:
                reply.data = {"text": reply.text.strip()}  # a line said in plain words is a line: no repair call
            if schema is not None and (errors := problems(reply.data, schema)):
                fix = [
                    *messages,
                    {"role": "assistant", "content": reply.text or "（空）"},
                    {"role": "user", "content": REPAIR.format(errors="\n".join(errors))},
                ]
                trace.progress("repair", input=fix, problems=errors, context=self._input_parts(fix, schema))
                # Keep the output contract, but let the backend avoid repeating a broken
                # transport mode (for example json_object producing only whitespace).
                token = _format_retry.set(True)
                try:
                    second = await self._attempt(fix, schema=schema, images=images, on_delta=None)
                finally:
                    _format_retry.reset(token)
                reply.add(second)
                reply.repairs += 1
                if not problems(second.data, schema) or (reply.data is None and second.data is not None):
                    reply.data, reply.text = second.data, second.text
                if (problems(reply.data, schema) and not isinstance(reply.data, dict)
                        and "text" in schema.get("properties", {}) and (envelope or not spoken(schema))):
                    # A structured envelope can contain private plans and motives. After the
                    # one format retry, fail the call; never turn that envelope into dialogue.
                    # Stateful plain-text replies also lack their operation/disclosure receipt.
                    # Parsed but incomplete drafts go to their caller's domain validator;
                    # encounter plans and missing keeper scenes already have recovery there.
                    raise ValueError("模型回复格式未通过校验，本次回应尚未完成")
            return reply
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            reply.seconds = time.monotonic() - started
            if not reply.cost_usd:
                reply.cost_usd = spend.record(self.spec.model, reply.usage)
            elif spend.billed(self.spec.model):  # Claude Code on an API key reports what it cost: a real bill
                spend.record(self.spec.model, reply.usage, reply.cost_usd)
            try:
                trace.emit(self, messages, reply, schema=schema, error=error)
            finally:
                trace.finish(observed)

    async def _attempt(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        """One call under a time limit, retried with back-off on transient failures. Once
        streamed text has reached the player, a failure is not retried (it would repeat)."""
        from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

        streamed = False

        async def delta(piece: str) -> None:
            nonlocal streamed
            trace.progress("delta", text=piece)
            try:
                await on_delta(piece)  # type: ignore[misc]
            finally:
                streamed = streamed or _shown(on_delta)

        reply = Reply()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(ATTEMPTS),
            wait=wait_exponential_jitter(initial=RETRY_WAIT[0], max=RETRY_WAIT[1]),
            retry=retry_if_exception(lambda e: transient(e) and not streamed),
            before_sleep=lambda attempt: trace.progress(
                "retry", attempt=attempt.attempt_number,
                error=str(attempt.outcome.exception()) if attempt.outcome else "",
                delay=attempt.next_action.sleep if attempt.next_action else 0),
            reraise=True,
        ):
            with attempt:
                parts = self._guard_input(messages, schema)
                trace.progress("attempt", attempt=attempt.retry_state.attempt_number, context=parts)
                async with asyncio.timeout(call_timeout(self.label)):
                    reply = await self._call(messages, schema=schema, images=images, on_delta=delta if on_delta else None)
                reply.retries = attempt.retry_state.attempt_number - 1
        return reply

    async def warm(self) -> None:
        return None

    async def close(self) -> None:
        if self.fallback is not None:
            await self.fallback.close()


# -- Claude Code ----------------------------------------------------------------------------
def _portable(schema: Any) -> Any:
    """A schema as Claude Code takes it: without OpenAPI's `discriminator` (pydantic writes one for
    a tagged union, like the NPC's backstage ops; Claude Code refuses the whole call). The union's
    `kind` constants still tell its members apart, and the reply is checked against the full
    schema (problems) all the same."""
    if isinstance(schema, dict):
        return {k: _portable(v) for k, v in schema.items() if k != "discriminator"}
    if isinstance(schema, list):
        return [_portable(v) for v in schema]
    return schema


HISTORY = "【下面是系统保存的对话记录，每次完整重发。“〔输入〕”是发给你的消息，“〔你的回复〕”是你之前的回答；只回应最后一条输入。】"
ALONE = "【本次输入】"  # leads a request without history: a block that never changes, to mark for the cache


# The prompt cache keeps a prefix only where a request marks it, and Claude Code marks the end
# of the request: the new prompt, with its one-off extra (preferences, a correction) that the
# transcript never keeps. So that mark never matched the next call and only the system prompt
# hit (a keeper turn: 4,822 of ~21,000 tokens cached). A mark of our own after the last block
# of history, which the next call repeats word for word, lets the whole history hit. Claude
# Code's own marks last an hour on a subscription; ours must not outlast a mark after it, so
# it steps down to five minutes, then to none, if the API says so (_cache_refused).
CACHE_TTL: list[str | None] = ["1h"]


def _blocks(messages: list[Msg], images: list[bytes] | None) -> list[dict]:
    """One text block per message, history first; the last block of history carries a
    cache mark, so the next call (which repeats all of it) reads it back from the cache.
    A request without history leads with a fixed block, marked: what comes before it (the
    system prompt, the schema) is then read back too. Without it nothing of the keeper's
    plan, the arbiter's first ruling or the director cached at all (the user's game,
    2026-10-21: 12–37 thousand tokens a call, none of them cached)."""
    if len(messages) == 1:
        rows = [ALONE, messages[0]["content"]]
    else:
        rows = [HISTORY] + [("〔你的回复〕" if m["role"] == "assistant" else "〔输入〕") + m["content"] for m in messages]
    blocks: list[dict] = [{"type": "text", "text": r} for r in rows]
    if CACHE_TTL[0]:  # one mark of ours: Claude Code adds its own, and a request takes four at most
        blocks[-2]["cache_control"] = {"type": "ephemeral", "ttl": CACHE_TTL[0]}
    for img in images or []:
        blocks.insert(-1, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()}})
    return blocks


class ClaudeError(Exception):
    """An API error Claude Code reported as its reply (“API Error: 529 …”). The status code
    lets `transient` retry overload and rate limits; anything else goes to the fallback."""

    def __init__(self, text: str):
        super().__init__(text[:500])
        found = re.search(r"API Error:\s*(\d{3})", text)
        self.status_code = int(found.group(1)) if found else None


def _cache_refused(exc: ClaudeError) -> bool:
    """The API turned our cache mark down: try a shorter one, or none."""
    if exc.status_code != 400 or "cache_control" not in str(exc) or not CACHE_TTL[0]:
        return False
    CACHE_TTL[0] = "5m" if CACHE_TTL[0] == "1h" else None
    return True


async def _stream(content: list[dict]):
    yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}


# Claude Code processes open at once, across every agent. Each is a Node program of a hundred MB
# or two, and every NPC, every important NPC's mind and every role kept its own until the game
# ended. The one idle longest is closed to make room; nothing lives in it (the transcript is the
# game's), so the next call on that agent starts it again.
MAX_PROCESSES = int(os.environ.get("AUTOKEEPER_CLAUDE_PROCESSES", 6))
_open: weakref.WeakSet[ClaudeCodeModel] = weakref.WeakSet()
_asked = itertools.count()  # the order calls came in: Windows' clock can give two the same time


async def _make_room(asking: ClaudeCodeModel) -> None:
    while sum(len(m._clients) for m in list(_open)) >= MAX_PROCESSES:
        idle = [(m._used.get(k, 0), id(m), m, k) for m in list(_open)
                if m is not asking and not m._lock.locked() for k in m._clients]  # never one mid-call
        if not idle:
            return
        _, _, m, k = min(idle)
        await m._release(k)


class ClaudeCodeModel(Model):
    """One Claude Code process per agent (and output schema), at most MAX_PROCESSES of them at
    once. It is wiped with /clear right after each reply, in the background while the player
    reads, so it never carries context of its own."""

    def __init__(self, spec: Role, system: str, **kw: Any):
        super().__init__(spec, system, **kw)
        self._clients: dict[str, Any] = {}  # one per output schema
        self._used: dict[str, int] = {}  # when each was last asked, in the order of all calls
        self._wiping: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def _release(self, key: str) -> None:
        """Close one idle process to make room for another agent's (_make_room). Out of the table
        first, so a call that comes meanwhile starts a fresh one instead of using this."""
        client = self._clients.pop(key, None)
        self._used.pop(key, None)
        if (task := self._wiping.pop(key, None)) is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def _client(self, key: str, schema: dict | None):
        self._used[key] = next(_asked)
        if key not in self._clients:
            await _make_room(self)
            try:
                from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
            except ImportError as exc:  # an extra (pyproject [claude]): the game runs on any backend
                from ..config import CLAUDE_EXTRA

                raise RuntimeError(CLAUDE_EXTRA) from exc

            paths.sessions().mkdir(parents=True, exist_ok=True)
            opts: dict[str, Any] = {
                "model": self.spec.model,
                "system_prompt": self.system,
                "tools": [],
                "max_turns": 1,
                "setting_sources": [],
                "include_partial_messages": True,
                "cwd": str(paths.sessions()),
                "max_buffer_size": 32 * 1024 * 1024,
            }
            if self.spec.effort:
                opts["effort"] = self.spec.effort
            if schema:
                opts["output_format"] = {"type": "json_schema", "schema": _portable(compact_schema(schema))}
            client = ClaudeSDKClient(options=ClaudeAgentOptions(**opts))
            await client.connect()
            self._clients[key] = client
            _open.add(self)
        return self._clients[key]

    async def _wipe(self, key: str) -> None:
        client = self._clients.get(key)
        if client is None:
            return
        try:
            await client.query("/clear")
            async for _ in client.receive_response():
                pass
        except Exception:  # a broken process is replaced on the next call
            self._clients.pop(key, None)

    async def warm(self) -> None:
        async with self._lock:
            await self._client("", None)

    async def _call(self, messages, *, schema, images, on_delta) -> Reply:
        while True:
            try:
                return await self._once(messages, schema=schema, images=images, on_delta=on_delta)
            except ClaudeError as exc:
                if not _cache_refused(exc):
                    raise

    async def _once(self, messages, *, schema, images, on_delta) -> Reply:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ThinkingBlock
        from claude_agent_sdk.types import StreamEvent

        key = json.dumps(schema, sort_keys=True) if schema else ""
        async with self._lock:
            if pending := self._wiping.pop(key, None):
                await pending
            client = await self._client(key, schema)
            reply, streamed, block = Reply(), False, ""
            try:
                await client.query(_stream(_blocks(messages, images)))
                async for msg in client.receive_response():
                    if isinstance(msg, StreamEvent):
                        ev = msg.event
                        delta = ev.get("delta", {})
                        if ev.get("type") == "content_block_start":
                            block = (ev.get("content_block") or {}).get("type", "")
                        elif ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                            streamed = True
                            if on_delta and not schema:
                                await on_delta(delta["text"])
                        elif (ev.get("type") == "content_block_delta" and delta.get("type") == "input_json_delta"
                              and schema and block == "tool_use" and on_delta and delta.get("partial_json")):
                            # Claude Code's structured output is the input of its StructuredOutput tool: the JSON as written.
                            await on_delta(delta["partial_json"])
                    elif isinstance(msg, AssistantMessage):
                        for b in msg.content:
                            if isinstance(b, TextBlock):
                                reply.text += b.text
                            elif isinstance(b, ThinkingBlock):
                                reply.thinking += b.thinking
                    elif isinstance(msg, ResultMessage):
                        u = msg.usage or {}
                        cached = u.get("cache_read_input_tokens", 0)
                        reply.usage = {
                            "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + cached,
                            "cache_read": cached,
                            "output_tokens": u.get("output_tokens", 0),
                        }
                        reply.cost_usd = msg.total_cost_usd or 0.0
                        reply.data = msg.structured_output
                        if msg.is_error:  # never hand an API error on as the reply (it would be narrated)
                            raise ClaudeError(msg.result or reply.text or "Claude Code reported an error")
            except BaseException:
                self._discard(key)  # a half-read stream is unusable; a retry gets a fresh process
                raise
            self._wiping[key] = asyncio.create_task(self._wipe(key))
        if on_delta and not streamed and reply.text and not schema:
            await on_delta(reply.text)
        if schema and reply.data is None and reply.text:
            reply.data = parse_json(reply.text)
        return reply

    def _discard(self, key: str) -> None:
        if (client := self._clients.pop(key, None)) is not None:
            task = asyncio.get_running_loop().create_task(client.disconnect())
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # swallow errors from a dead process

    async def close(self) -> None:
        for task in self._wiping.values():
            task.cancel()
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:  # already gone
                pass
        self._clients.clear()
        self._wiping.clear()
        await super().close()


# -- LangChain ------------------------------------------------------------------------------
THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 12000, "max": 24000}
JSON_SWITCH = {"deepseek", "openai"}  # APIs with response_format json_object


def _usage(meta: dict | None) -> dict:
    meta = meta or {}
    return {
        "input_tokens": meta.get("input_tokens", 0),
        "cache_read": (meta.get("input_token_details") or {}).get("cache_read", 0),
        "output_tokens": meta.get("output_tokens", 0),
    }


def _thinking(msg: Any) -> str:
    text = (msg.additional_kwargs or {}).get("reasoning_content") or ""
    if isinstance(msg.content, list):
        text += "".join(b.get("thinking", "") for b in msg.content if isinstance(b, dict) and b.get("type") == "thinking")
    return text


class LangChainModel(Model):
    def __init__(self, spec: Role, system: str, **kw: Any):
        super().__init__(spec, system, **kw)
        from langchain.chat_models import init_chat_model

        self.provider, _, self.name = spec.model.partition(":") if ":" in spec.model else ("", "", spec.model)
        options = self._options()
        self.chat = init_chat_model(spec.model, **options)
        # Anthropic structured output is a forced tool call, which extended thinking does not allow.
        thinking_free = {k: v for k, v in options.items() if k not in ("thinking", "max_tokens")}
        self.plain = init_chat_model(spec.model, **thinking_free) if "thinking" in options else self.chat
        self.mode = "json" if self.provider == "deepseek" or (self.provider == "openai" and spec.base_url) else "native"

    def _options(self) -> dict[str, Any]:
        p, effort = self.provider, self.spec.effort
        kw: dict[str, Any] = {"base_url": self.spec.base_url} if self.spec.base_url else {}
        if p == "deepseek":
            if base := self.spec.base_url or os.environ.get("DEEPSEEK_BASE_URL"):
                kw["base_url"] = base
            kw["stream_usage"] = True
            kw["extra_body"] = {"thinking": {"type": "enabled" if effort else "disabled"}}
            if effort:  # DeepSeek has low / high / max
                kw["reasoning_effort"] = {"minimal": "low", "low": "low", "max": "max"}.get(effort, "high")
        elif p == "ollama":
            # Ollama's default context silently truncates long prompts; too large a one spills the
            # model out of VRAM. A game turn needs ~6k tokens, so 16k leaves room for the window.
            kw["num_ctx"] = int(os.environ.get("AUTOKEEPER_NUM_CTX", 16384))
            kw["reasoning"] = effort in ("high", "max")  # local thinking is slow: only when asked for
        elif p == "openai":
            kw["stream_usage"] = True
            if effort and re.match(r"(o\d|gpt-5)", self.name):
                kw["reasoning_effort"] = "high" if effort == "max" else effort  # OpenAI tops out at high
        elif p == "google_genai" and effort:
            kw["thinking_level"] = "low" if effort in ("minimal", "low") else "high"
        elif p == "anthropic" and effort:
            budget = THINKING_BUDGET.get(effort, 4096)
            kw["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kw["max_tokens"] = budget + 8000
        return kw

    def _messages(self, messages: list[Msg], images: list[bytes] | None, schema: dict | None) -> list:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        system = self.system + (json_instruction(schema) if schema and self.mode == "json" else "")
        out: list = [SystemMessage(system)]
        for i, m in enumerate(messages):
            if m["role"] == "assistant":
                out.append(AIMessage(m["content"]))
            elif images and i == len(messages) - 1:
                pics = [{"type": "image", "base64": base64.b64encode(img).decode(), "mime_type": "image/png"} for img in images]
                out.append(HumanMessage(content=[*pics, {"type": "text", "text": m["content"]}]))
            else:
                out.append(HumanMessage(m["content"]))
        return out

    async def _call(self, messages, *, schema, images, on_delta) -> Reply:
        history = self._messages(messages, images, schema)
        reply = Reply()
        if schema is None:
            async for chunk in self.chat.astream(history):
                if piece := chunk.text:
                    reply.text += piece
                    if on_delta:
                        await on_delta(piece)
                reply.thinking += _thinking(chunk)
                if chunk.usage_metadata:
                    reply.usage = _usage(chunk.usage_metadata)
            return reply
        if self.mode == "json":
            # Dialogue can carry actions, discoveries and private intentions. Optional fields do
            # not make that contract text-only: keep JSON mode for every stateful reply.
            switch = self.provider in JSON_SWITCH and not spoken(schema) and not _format_retry.get()
            runnable = self.chat.bind(response_format={"type": "json_object"}) if switch else self.chat
            if on_delta:  # the JSON as it is written, for a caller that reads it on the way (the keeper's telling)
                async for chunk in runnable.astream(history):
                    if piece := chunk.text:
                        reply.text += piece
                        await on_delta(piece)
                    reply.thinking += _thinking(chunk)
                    if chunk.usage_metadata:
                        reply.usage = _usage(chunk.usage_metadata)
            else:
                msg = await runnable.ainvoke(history)
                reply.text, reply.thinking, reply.usage = msg.text, _thinking(msg), _usage(msg.usage_metadata)
            reply.data = parse_json(reply.text)
            return reply
        out = await self.plain.with_structured_output(compact_schema(schema), include_raw=True).ainvoke(history)
        raw, parsed = out["raw"], out.get("parsed")
        if hasattr(parsed, "model_dump"):
            parsed = parsed.model_dump()
        reply.text = raw.text or (json.dumps(parsed, ensure_ascii=False) if parsed is not None else "")
        reply.thinking, reply.usage = _thinking(raw), _usage(raw.usage_metadata)
        reply.data = parsed if isinstance(parsed, dict) else parse_json(reply.text)
        return reply


# -- Codex --------------------------------------------------------------------------------
CODEX_EFFORT = {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "max": "high"}


class CodexError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        found = re.search(r'"status":\s*(\d{3})', message)
        self.status_code = int(found.group(1)) if found else None  # lets transient() see 429 / 5xx


async def _communicate(proc: asyncio.subprocess.Process, prompt: bytes) -> tuple[bytes, bytes]:
    """Cancel the CLI process group as well as the request when a turn times out."""
    try:
        return await proc.communicate(prompt)
    except BaseException:
        if proc.returncode is None:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except TimeoutError:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass
        raise


def strict_schema(node: Any) -> Any:
    """OpenAI's strict structured output: every object closed and every field required,
    no defaults. Fields that had defaults simply get filled in by the model; the original
    schema still validates the result. A tagged union (the director's and the NPCs' backstage ops)
    goes as anyOf without its discriminator: strict mode refuses oneOf outright (HTTP 400)."""
    if isinstance(node, list):
        return [strict_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {("anyOf" if k == "oneOf" else k): strict_schema(v) for k, v in node.items() if k not in ("default", "discriminator")}
    if "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out


def _transcript(messages: list[Msg]) -> str:
    """The window as one prompt, in the same tagged form the Claude Code channel uses."""
    if len(messages) == 1:
        return messages[0]["content"]
    rows = [("〔你的回复〕" if m["role"] == "assistant" else "〔输入〕") + m["content"] for m in messages]
    return "\n\n".join([HISTORY, *rows])


class CodexModel(Model):
    """The Codex CLI (`codex exec`) as a bare transport, like Claude Code: the harness's own
    instructions replace Codex's (model_instructions_file), nothing is kept (--ephemeral),
    the sandbox is read-only, and structured output goes through --output-schema. Each call
    is a fresh process of about 9 s, so it suits imports and generation better than a live
    turn. Runs on the local Codex login; spec.model "default" keeps the login's own model."""

    async def _call(self, messages, *, schema, images, on_delta) -> Reply:
        import tempfile

        paths.sessions().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.realpath(tmp)
            instructions = os.path.join(folder, "instructions.md")
            with open(instructions, "w", encoding="utf-8") as f:
                f.write(self.system)
            args = [
                shutil.which("codex") or "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                "-s", "read-only", "-C", str(paths.sessions()),
                "-c", f"model_instructions_file={json.dumps(instructions)}", "-c", "project_doc_max_bytes=0",
            ]  # fmt: skip
            if self.spec.model and self.spec.model != "default":
                args += ["-m", self.spec.model]
            if self.spec.effort:
                args += ["-c", f'model_reasoning_effort="{CODEX_EFFORT.get(self.spec.effort, "medium")}"']
            if schema is not None:
                schema_file = os.path.join(folder, "schema.json")
                with open(schema_file, "w", encoding="utf-8") as f:
                    json.dump(strict_schema(compact_schema(schema)), f, ensure_ascii=False, separators=(",", ":"))
                args += ["--output-schema", schema_file]
            for i, img in enumerate(images or []):
                path = os.path.join(folder, f"image{i}.png")
                with open(path, "wb") as f:
                    f.write(img)
                args += ["-i", path]
            proc = await asyncio.create_subprocess_exec(
                *args, "-", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
                # Windows: codex.cmd found by its full name, and run with no console of its own: sharing the
                # game's, a child took the screen down with it (the user, 2026-10-20, as chafa did).
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
            )
            out, err = await _communicate(proc, _transcript(messages).encode("utf-8"))
        reply, failure = Reply(), ""
        for line in out.decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = ev.get("item") or {}
            if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
                reply.text += item.get("text", "")
            elif ev.get("type") == "item.completed" and item.get("type") == "reasoning":
                reply.thinking += item.get("text", "")
            elif ev.get("type") == "turn.completed":
                u = ev.get("usage") or {}
                reply.usage = {
                    "input_tokens": u.get("input_tokens", 0),
                    "cache_read": u.get("cached_input_tokens", 0),
                    "output_tokens": u.get("output_tokens", 0) + u.get("reasoning_output_tokens", 0),
                }
            elif ev.get("type") == "turn.failed":
                failure = (ev.get("error") or {}).get("message", "")
        if failure or (proc.returncode and not reply.text):
            raise CodexError(failure or err.decode("utf-8", "replace")[-500:] or f"codex exec 退出码 {proc.returncode}")
        if on_delta and reply.text and schema is None:
            await on_delta(reply.text)
        if schema is not None:
            reply.data = parse_json(reply.text)
        return reply


MODELS: dict[str, type[Model]] = {"claude": ClaudeCodeModel, "langchain": LangChainModel, "codex": CodexModel}


def make_model(spec: Role, system: str, *, label: str = "") -> Model:
    load_env()  # keys in ~/.config/autokeeper/.env, also when the harness is used as a library
    if spec.backend == "replay" and "replay" not in MODELS:
        from .replay import ReplayModel

        MODELS["replay"] = ReplayModel
    try:
        cls = MODELS[spec.backend]
    except KeyError:
        raise ValueError(f"未知后端：{spec.backend}（可选：{', '.join(MODELS)}）") from None
    model = cls(spec, system, label=label)
    if spec.fallback:
        backend, name = route(spec.fallback)
        model.fallback = make_model(Role(backend, name, spec.effort, window=spec.window,
                                         max_input_chars=spec.max_input_chars), system, label=label)
        model.fallback.standby_for = spec.model
    return model


class ModelPool:
    """Reusable channels for many short independent calls (LightRAG extraction, evals),
    so a Claude Code process is not started per call."""

    def __init__(self, spec: Role, *, size: int = 4, label: str = ""):
        self.spec, self.label = spec, label
        self._sem = asyncio.Semaphore(size)
        self._idle: dict[str, list[Model]] = {}
        self._all: list[Model] = []

    async def complete(self, system: str, messages: list[Msg], **kw: Any) -> Reply:
        async with self._sem:
            idle = self._idle.setdefault(system, [])
            if idle:
                model = idle.pop()
            else:
                model = make_model(self.spec, system, label=self.label)
                self._all.append(model)
            try:
                return await model.complete(messages, **kw)
            finally:
                idle.append(model)

    async def close(self) -> None:
        await asyncio.gather(*(m.close() for m in self._all), return_exceptions=True)
        self._all.clear()
        self._idle.clear()
