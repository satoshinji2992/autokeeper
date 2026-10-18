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
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import SESSIONS, Role, load_env, route
from . import spend, trace

OnDelta = Callable[[str], Awaitable[None]] | None
Msg = dict[str, str]  # {"role": "user" | "assistant", "content": str}


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
TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
TRANSIENT_NAMES = ("Timeout", "RateLimit", "Connection", "InternalServer", "ServiceUnavailable", "Overloaded", "RemoteProtocol")


def transient(exc: BaseException) -> bool:
    """Worth another try: rate limits, overload, time-outs, dropped connections, 5xx.
    Not: budget, auth, bad requests, missing keys, our own bugs."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in TRANSIENT_STATUS
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return any(n in type(exc).__name__ for n in TRANSIENT_NAMES)


COOLDOWN = 300.0  # seconds a failed model is skipped in favour of its fallback
_down: dict[tuple, float] = {}  # (backend, model, base_url) -> when to try it again
listeners: list[Callable[[str], None]] = []  # told when a role switches to its fallback


# -- structured output -----------------------------------------------------------------
def parse_json(text: str) -> Any:
    """The first JSON object in a reply, tolerating code fences and chatter around it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start == -1:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except json.JSONDecodeError:
        return None


def _resolve(root: dict, node: dict) -> dict:
    if ref := node.get("$ref"):
        return root.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
    if "anyOf" in node:  # Optional[...] from pydantic
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        return _resolve(root, options[0]) if options else node
    return node


def example(root: dict, node: dict | None = None, depth: int = 0) -> Any:
    """A skeleton object for the prompt, so models without schema decoding see the shape."""
    node = _resolve(root, root if node is None else node)
    if "enum" in node:
        return node["enum"][0]
    kind = node.get("type")
    if kind == "object" or "properties" in node:
        return {k: example(root, v, depth + 1) for k, v in node.get("properties", {}).items()}
    if kind == "array":
        return [example(root, node.get("items", {}), depth + 1)] if depth < 3 else []
    return {"string": "…", "integer": 0, "number": 0, "boolean": False}.get(kind or "")


def json_instruction(schema: dict) -> str:
    return (
        "\n\n## 输出格式\n只输出一个 JSON 对象（json），不要代码块，不要其他文字。字段定义（JSON Schema）：\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n格式示例（只示意结构）：{json.dumps(example(schema), ensure_ascii=False)}"
    )


def problems(data: Any, schema: dict) -> list[str]:
    if not isinstance(data, dict):
        return ["没有输出一个可解析的 JSON 对象"]
    from jsonschema import Draft202012Validator

    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '(根)'}：{e.message}" for e in errors[:8]]


REPAIR = "上一条输出不符合格式要求：\n{errors}\n请重新输出完整、正确的 JSON 对象（json），只输出 JSON。"


# -- the channel contract -----------------------------------------------------------------
class Model:
    mode = "native"  # how structured output is obtained: native | json
    fallback: Model | None = None
    standby_for = ""  # on a fallback: the model it stands in for (shows in traces)

    def __init__(self, spec: Role, system: str, *, label: str = ""):
        self.spec, self.system, self.label = spec, system, label or spec.model

    async def _call(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        raise NotImplementedError

    async def complete(
        self, messages: list[Msg], *, schema: dict | None = None, images: list[bytes] | None = None, on_delta: OnDelta = None
    ) -> Reply:
        if self.fallback is None:
            return await self._complete(messages, schema=schema, images=images, on_delta=on_delta)
        key = (self.spec.backend, self.spec.model, self.spec.base_url)
        if time.monotonic() < _down.get(key, 0.0):
            return await self.fallback.complete(messages, schema=schema, images=images, on_delta=on_delta)
        shown = False

        async def watch(piece: str) -> None:
            nonlocal shown
            shown = True
            await on_delta(piece)  # type: ignore[misc]

        try:
            return await self._complete(messages, schema=schema, images=images, on_delta=watch if on_delta else None)
        except Exception as exc:
            if shown:  # the player already read part of this reply; starting over would repeat it
                raise
            _down[key] = time.monotonic() + COOLDOWN
            for tell in listeners:
                tell(f"{self.spec.model} 出错（{type(exc).__name__}），{self.label} 等角色改用备用模型 {self.fallback.spec.model}")
            return await self.fallback.complete(messages, schema=schema, images=images, on_delta=on_delta)

    async def _complete(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        spend.guard(self.spec.model)
        started = time.monotonic()
        reply, error = Reply(), ""
        try:
            reply = await self._attempt(messages, schema=schema, images=images, on_delta=on_delta)
            if schema is not None and (errors := problems(reply.data, schema)):
                fix = [
                    *messages,
                    {"role": "assistant", "content": reply.text or "（空）"},
                    {"role": "user", "content": REPAIR.format(errors="\n".join(errors))},
                ]
                second = await self._attempt(fix, schema=schema, images=None, on_delta=None)
                reply.add(second)
                reply.repairs += 1
                if not problems(second.data, schema) or (reply.data is None and second.data is not None):
                    reply.data, reply.text = second.data, second.text
            return reply
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            reply.seconds = time.monotonic() - started
            if not reply.cost_usd:
                reply.cost_usd = spend.record(self.spec.model, reply.usage)
            trace.emit(self, messages, reply, schema=schema, error=error)

    async def _attempt(self, messages: list[Msg], *, schema: dict | None, images: list[bytes] | None, on_delta: OnDelta) -> Reply:
        """One call under a time limit, retried with back-off on transient failures. Once
        streamed text has reached the player, a failure is not retried (it would repeat)."""
        from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

        streamed = False

        async def delta(piece: str) -> None:
            nonlocal streamed
            streamed = True
            await on_delta(piece)  # type: ignore[misc]

        reply = Reply()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(ATTEMPTS),
            wait=wait_exponential_jitter(initial=RETRY_WAIT[0], max=RETRY_WAIT[1]),
            retry=retry_if_exception(lambda e: transient(e) and not streamed),
            reraise=True,
        ):
            with attempt:
                async with asyncio.timeout(CALL_TIMEOUT):
                    reply = await self._call(messages, schema=schema, images=images, on_delta=delta if on_delta else None)
                reply.retries = attempt.retry_state.attempt_number - 1
        return reply

    async def warm(self) -> None:
        return None

    async def close(self) -> None:
        if self.fallback is not None:
            await self.fallback.close()


# -- Claude Code ----------------------------------------------------------------------------
HISTORY = "【下面是系统保存的对话记录，每次完整重发。“〔输入〕”是发给你的消息，“〔你的回复〕”是你之前的回答；只回应最后一条输入。】"


def _blocks(messages: list[Msg], images: list[bytes] | None) -> list[dict]:
    """One text block per message, append-only, so each call's prompt is a prefix of the
    next and Claude's prompt cache keeps hitting."""
    rows = [messages[0]["content"]] if len(messages) == 1 else [HISTORY] + [
        ("〔你的回复〕" if m["role"] == "assistant" else "〔输入〕") + m["content"] for m in messages
    ]
    blocks: list[dict] = [{"type": "text", "text": r} for r in rows]
    for img in images or []:
        blocks.insert(-1, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()}})
    return blocks


async def _stream(content: list[dict]):
    yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}


class ClaudeCodeModel(Model):
    """One Claude Code process per agent. It is wiped with /clear right after each reply,
    in the background while the player reads, so it never carries context of its own."""

    def __init__(self, spec: Role, system: str, **kw: Any):
        super().__init__(spec, system, **kw)
        self._clients: dict[str, Any] = {}  # one per output schema
        self._wiping: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def _client(self, key: str, schema: dict | None):
        if key not in self._clients:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

            SESSIONS.mkdir(parents=True, exist_ok=True)
            opts: dict[str, Any] = {
                "model": self.spec.model,
                "system_prompt": self.system,
                "tools": [],
                "max_turns": 1,
                "setting_sources": [],
                "include_partial_messages": True,
                "cwd": str(SESSIONS),
                "max_buffer_size": 32 * 1024 * 1024,
            }
            if self.spec.effort:
                opts["effort"] = self.spec.effort
            if schema:
                opts["output_format"] = {"type": "json_schema", "schema": schema}
            client = ClaudeSDKClient(options=ClaudeAgentOptions(**opts))
            await client.connect()
            self._clients[key] = client
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
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ThinkingBlock
        from claude_agent_sdk.types import StreamEvent

        key = json.dumps(schema, sort_keys=True) if schema else ""
        async with self._lock:
            if pending := self._wiping.pop(key, None):
                await pending
            client = await self._client(key, schema)
            reply, streamed = Reply(), False
            try:
                await client.query(_stream(_blocks(messages, images)))
                async for msg in client.receive_response():
                    if isinstance(msg, StreamEvent):
                        ev = msg.event
                        if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                            streamed = True
                            if on_delta and not schema:
                                await on_delta(ev["delta"]["text"])
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
                        if msg.is_error and not reply.text:
                            reply.text = msg.result or ""
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
            runnable = self.chat.bind(response_format={"type": "json_object"}) if self.provider in JSON_SWITCH else self.chat
            msg = await runnable.ainvoke(history)
            reply.text, reply.thinking, reply.usage = msg.text, _thinking(msg), _usage(msg.usage_metadata)
            reply.data = parse_json(reply.text)
            return reply
        out = await self.plain.with_structured_output(schema, include_raw=True).ainvoke(history)
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


def strict_schema(node: Any) -> Any:
    """OpenAI's strict structured output: every object closed and every field required,
    no defaults. Fields that had defaults simply get filled in by the model; the original
    schema still validates the result."""
    if isinstance(node, list):
        return [strict_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: strict_schema(v) for k, v in node.items() if k != "default"}
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

        SESSIONS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.realpath(tmp)
            instructions = os.path.join(folder, "instructions.md")
            with open(instructions, "w", encoding="utf-8") as f:
                f.write(self.system)
            args = [
                "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                "-s", "read-only", "-C", str(SESSIONS),
                "-c", f"model_instructions_file={json.dumps(instructions)}", "-c", "project_doc_max_bytes=0",
            ]  # fmt: skip
            if self.spec.model and self.spec.model != "default":
                args += ["-m", self.spec.model]
            if self.spec.effort:
                args += ["-c", f'model_reasoning_effort="{CODEX_EFFORT.get(self.spec.effort, "medium")}"']
            if schema is not None:
                schema_file = os.path.join(folder, "schema.json")
                with open(schema_file, "w", encoding="utf-8") as f:
                    json.dump(strict_schema(schema), f, ensure_ascii=False)
                args += ["--output-schema", schema_file]
            for i, img in enumerate(images or []):
                path = os.path.join(folder, f"image{i}.png")
                with open(path, "wb") as f:
                    f.write(img)
                args += ["-i", path]
            proc = await asyncio.create_subprocess_exec(
                *args, "-", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await proc.communicate(_transcript(messages).encode("utf-8"))
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
        model.fallback = make_model(Role(backend, name, spec.effort, window=spec.window), system, label=label)
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
