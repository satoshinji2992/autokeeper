"""Conversation state, owned by the harness.

A Session is one agent's conversation. Its transcript is a plain list the caller keeps
(the game keeps them in GameState.transcripts, so they are saved with the game). Each
call resends a window of it, so the model behind a session can be anything and can be
swapped between turns.

    memory=0   a stateless helper: only the new message is sent
    memory=N   the recent turns that fit in N characters are resent; when the transcript
               outgrows N, the oldest turns are folded into a rolling summary
               (notes[key]) by the summarizer, and the summary leads the window

The window is append-only between compactions, which keeps provider prompt caches hot.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Role
from .models import Msg, OnDelta, Reply, make_model


class Summarizer:
    """Folds old conversation into a summary with a cheap model."""

    GAME = (
        "你负责给 AI 角色压缩记忆。把已有摘要和较早的对话合并成新的摘要：条目式，第三人称，"
        "保留人物与态度、承诺与谎言、已经透露的信息、做过的决定、没解决的事。不超过 {limit} 字，只输出摘要。"
    )
    WORK = (
        "你负责给执行长任务的 AI 压缩工作记录。把已有摘要和较早的步骤合并成新的进度摘要：条目式，"
        "保留任务目标、已完成的步骤、读到的关键事实（带出处：文件名和行号或网址）、写过哪些文件、"
        "遇到的问题和还没做的事。不超过 {limit} 字，只输出摘要。"
    )

    def __init__(self, spec: Role, *, style: str = GAME, limit: int = 600, on_reply: Any = None):
        self.model = make_model(spec, style.format(limit=limit), label="summarizer")
        self.on_reply = on_reply

    async def __call__(self, text: str, who: str) -> str:
        reply = await self.model.complete([{"role": "user", "content": f"【{who}】\n{text}"}])
        if self.on_reply:
            self.on_reply(reply)
        return reply.text.strip()

    async def close(self) -> None:
        await self.model.close()


class Session:
    def __init__(
        self,
        role: str,
        spec: Role,
        system: str,
        *,
        schema: dict | None = None,
        memory: int = 0,
        transcript: list[Msg] | None = None,
        notes: dict[str, str] | None = None,
        key: str = "",
        summarizer: Summarizer | None = None,
    ):
        self.role, self.spec, self.system, self.schema = role, spec, system, schema
        self.memory = (spec.window or memory) if memory else 0
        self.key = key or role
        self.summarizer = summarizer
        self.transcript: list[Msg] = [] if transcript is None else transcript
        self.notes: dict[str, str] = {} if notes is None else notes
        self.model = make_model(spec, system, label=role)

    def bind(self, transcript: list[Msg], notes: dict[str, str]) -> Session:
        """Point the session at the caller's current state objects (after a load or a copy)."""
        self.transcript, self.notes = transcript, notes
        return self

    @property
    def summary(self) -> str:
        return self.notes.get(self.key, "")

    def window(self, prompt: str) -> list[Msg]:
        msgs: list[Msg] = []
        if self.memory:
            if self.summary:
                msgs += [
                    {"role": "user", "content": f"【更早的经过（系统摘要）】\n{self.summary}"},
                    {"role": "assistant", "content": "好的，我记得这些。"},
                ]
            msgs += self.transcript
        msgs.append({"role": "user", "content": prompt})
        return msgs

    async def send(self, prompt: str, *, extra: str = "", images: list[bytes] | None = None, on_delta: OnDelta = None) -> Reply:
        """`extra` is appended for this call only and never stored (corrections, reminders)."""
        window = self.window(prompt + extra)
        reply = await self.model.complete(window, schema=self.schema, images=images, on_delta=on_delta)
        if self.memory:
            answer = reply.text if self.schema is None else json.dumps(reply.data, ensure_ascii=False)
            self.transcript += [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}]
            await self.compact()
        return reply

    def forget(self, turns: int = 1) -> None:
        del self.transcript[-2 * turns :]

    async def compact(self) -> None:
        size = sum(len(m["content"]) for m in self.transcript)
        if size <= self.memory or len(self.transcript) < 4:
            return
        cut, folded = 0, 0
        while cut < len(self.transcript) - 2 and size - folded > self.memory // 2:
            folded += len(self.transcript[cut]["content"]) + len(self.transcript[cut + 1]["content"])
            cut += 2
        old = self.transcript[:cut]
        del self.transcript[:cut]
        text = "\n".join(f"{'回复' if m['role'] == 'assistant' else '输入'}：{m['content']}" for m in old)
        if self.summary:
            text = f"已有摘要：\n{self.summary}\n\n较早的记录：\n{text}"
        if self.summarizer:
            self.notes[self.key] = await self.summarizer(text, self.key)
        else:  # no summarizer: keep the most recent part verbatim
            self.notes[self.key] = text[-max(2000, self.memory // 4) :]

    async def warm(self) -> None:
        await self.model.warm()

    async def close(self) -> None:
        await self.model.close()
