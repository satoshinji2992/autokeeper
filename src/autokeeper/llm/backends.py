"""Model backends behind one small interface.

A *session* is a long-lived conversation with one agent (the keeper, the arbiter,
one particular NPC ...). Sessions keep their own context, so an NPC remembers what
the investigators told it and nobody else's secrets leak in.

    claude     Claude Agent SDK; one persistent Claude Code process per session.
               Uses whatever login the local CLI has (subscription for personal play,
               ANTHROPIC_API_KEY for anything you ship to others).
    langchain  Any chat model LangChain's init_chat_model knows (Gemini, OpenAI ...).
    codex      Interface reserved for the Codex SDK.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import SESSIONS, Role

OnDelta = Callable[[str], Awaitable[None]] | None
OnTool = Callable[[str, dict], Awaitable[None]] | None


@dataclass
class Reply:
    text: str = ""
    data: Any = None
    session_id: str | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    seconds: float = 0.0


class Session:
    role: str
    spec: Role
    session_id: str | None = None

    async def send(self, prompt: str, *, images: list[bytes] | None = None, on_delta: OnDelta = None) -> Reply:
        raise NotImplementedError

    async def warm(self) -> None:
        """Start the backend ahead of the first message (no-op unless it has a process to spawn)."""
        return

    async def close(self) -> None:
        return None


# -- Claude -------------------------------------------------------------------------
class ClaudeSession(Session):
    def __init__(
        self,
        role: str,
        spec: Role,
        system: str,
        *,
        schema: dict | None = None,
        resume: str | None = None,
        tools: list[str] | None = None,
        cwd: Path | None = None,
        extra: dict | None = None,
    ):
        self.role, self.spec, self.system, self.schema = role, spec, system, schema
        self.session_id = resume
        self.tools = tools or []
        self.cwd = cwd or SESSIONS
        self.extra = extra or {}
        self._client = None
        self._lock = asyncio.Lock()

    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions

        self.cwd.mkdir(parents=True, exist_ok=True)
        opts: dict[str, Any] = {
            "model": self.spec.model,
            "effort": self.spec.effort,
            "system_prompt": self.system,
            "tools": self.tools,
            "setting_sources": [],
            "include_partial_messages": True,
            "cwd": str(self.cwd),
            "resume": self.session_id,
        }
        if self.schema:
            opts["output_format"] = {"type": "json_schema", "schema": self.schema}
        if not self.tools:
            opts["max_turns"] = 1
            opts["tools"] = []
        opts.update(self.extra)
        return ClaudeAgentOptions(**opts)

    async def warm(self) -> None:
        async with self._lock:
            await self._connect()

    async def _connect(self):
        if self._client is None:
            from claude_agent_sdk import ClaudeSDKClient

            self._client = ClaudeSDKClient(options=self._options())
            await self._client.connect()
        return self._client

    async def send(
        self, prompt: str, *, images: list[bytes] | None = None, on_delta: OnDelta = None, on_tool: OnTool = None
    ) -> Reply:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
        from claude_agent_sdk.types import StreamEvent

        async with self._lock:
            client = await self._connect()
            started = time.monotonic()
            if images:
                await client.query(_with_images(prompt, images))
            else:
                await client.query(prompt)
            reply = Reply()
            streamed = False
            async for msg in client.receive_response():
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        streamed = True
                        if on_delta and not self.schema:
                            await on_delta(ev["delta"]["text"])
                elif isinstance(msg, AssistantMessage):
                    reply.text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
                    if on_tool:
                        for b in msg.content:
                            if isinstance(b, ToolUseBlock):
                                await on_tool(b.name, b.input)
                elif isinstance(msg, ResultMessage):
                    reply.session_id = msg.session_id
                    reply.usage = msg.usage or {}
                    reply.cost_usd = msg.total_cost_usd or 0.0
                    reply.data = msg.structured_output
                    if msg.is_error and not reply.text:
                        reply.text = msg.result or ""
            if on_delta and not streamed and reply.text and not self.schema:
                await on_delta(reply.text)
            if self.schema and reply.data is None and reply.text:
                reply.data = _parse_json(reply.text)
            reply.seconds = time.monotonic() - started
            self.session_id = reply.session_id or self.session_id
            return reply

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None


async def _with_images(prompt: str, images: list[bytes]):
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()}}
        for img in images
    ]
    content.append({"type": "text", "text": prompt})
    yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


# -- LangChain (Gemini, OpenAI, ...) -------------------------------------------------
class LangChainSession(Session):
    """Keeps the message history itself so it behaves like a session."""

    def __init__(self, role: str, spec: Role, system: str, *, schema: dict | None = None, **_):
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import SystemMessage

        kwargs: dict[str, Any] = {}
        if spec.effort and spec.model.startswith(("openai:", "gpt")):
            kwargs["reasoning_effort"] = spec.effort
        if spec.effort and spec.model.startswith("google_genai:"):
            kwargs["thinking_level"] = "low" if spec.effort in ("low", "minimal") else "high"
        self.role, self.spec, self.schema = role, spec, schema
        self.model = init_chat_model(spec.model, **kwargs)
        self.history: list = [SystemMessage(system)]

    async def send(self, prompt: str, *, images: list[bytes] | None = None, on_delta: OnDelta = None) -> Reply:
        from langchain_core.messages import AIMessage, HumanMessage

        started = time.monotonic()
        content: Any = prompt
        if images:
            content = [
                {"type": "image", "base64": base64.b64encode(img).decode(), "mime_type": "image/png"} for img in images
            ] + [{"type": "text", "text": prompt}]
        self.history.append(HumanMessage(content))
        reply = Reply()
        if self.schema:
            data = await self.model.with_structured_output(self.schema).ainvoke(self.history)
            reply.data = data
            reply.text = json.dumps(data, ensure_ascii=False)
        else:
            async for chunk in self.model.astream(self.history):
                piece = chunk.text
                reply.text += piece
                if on_delta and piece:
                    await on_delta(piece)
        self.history.append(AIMessage(reply.text))
        reply.seconds = time.monotonic() - started
        return reply


# -- Codex (interface only for now) --------------------------------------------------
class CodexSession(Session):
    def __init__(self, role: str, spec: Role, system: str, **_):
        self.role, self.spec, self.system = role, spec, system

    async def send(self, prompt: str, *, images: list[bytes] | None = None, on_delta: OnDelta = None) -> Reply:
        raise NotImplementedError(
            "Codex 后端的接口已预留（openai-codex SDK：Codex().thread_start().run()），v0.1 默认只用 Claude。"
        )


BACKENDS = {"claude": ClaudeSession, "langchain": LangChainSession, "codex": CodexSession}


def open_session(role: str, spec: Role, system: str, **kwargs) -> Session:
    try:
        cls = BACKENDS[spec.backend]
    except KeyError:
        raise ValueError(f"未知后端：{spec.backend}（可选：{', '.join(BACKENDS)}）") from None
    return cls(role, spec, system, **kwargs)
