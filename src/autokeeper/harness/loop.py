"""A tool-using agent loop that runs on any model, built on LangGraph.

    START ─→ think ─┬─ finish ──────────→ END
               ↑    └─→ act ─┬─ step cap → END
               └─────────────┘

Each step the model returns one JSON object: a short thought, the tool to call and
that tool's arguments as flat fields. Any provider can produce it, through its own
schema mode or plain JSON mode (models.py). The harness checks the arguments, runs the
tool inside its sandbox and sends the observation back. Tool errors are observations
too, so the model corrects itself instead of the job crashing.

The conversation is a harness Session with a work summarizer: older steps are folded
into a progress summary, so a long job (reading a 60-page scenario, then writing a
module) fits whatever context window the model has.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import config
from .context import Session, Summarizer
from .tools import ARGS, Tool, ToolError

INTEGER_ARGS = {"start", "limit"}
OBSERVATION_LIMIT = 12000

OnEvent = Callable[[str, dict], Any] | None


def step_schema(tools: list[Tool]) -> dict:
    props: dict[str, dict] = {
        "thought": {"type": "string", "description": "一两句：现在知道了什么，下一步做什么、为什么"},
        "tool": {"type": "string", "enum": [t.name for t in tools] + ["finish"], "description": "要调用的工具"},
    }
    for t in tools:
        for arg in t.args:
            props.setdefault(arg, {"type": "integer" if arg in INTEGER_ARGS else "string", "description": ARGS.get(arg, arg)})
    props["answer"] = {"type": "string", "description": "调用 finish 时的最终说明"}
    return {"title": "Step", "description": "下一步调用哪个工具、用什么参数", "type": "object", "properties": props, "required": ["thought", "tool"]}


def manual(tools: list[Tool]) -> str:
    rows = [
        "\n\n## 工具",
        "你通过工具完成任务。每一步只输出一个 JSON 对象：thought（一两句：已知什么、下一步做什么）、tool（工具名），"
        "再加上这个工具要用的参数字段，用不到的字段不写。工具的结果会在下一条消息里给你。",
        "任务完成后调用 finish，在 answer 里写最终说明。不要编造工具结果。",
    ]
    for t in tools:
        args = "、".join(f"{a}{'（必填）' if a in t.required else ''}" for a in t.args) or "无参数"
        rows.append(f"- {t.name}：{t.description}。参数：{args}")
    rows.append("- finish：结束任务。参数：answer")
    return "\n".join(rows)


class LoopState(TypedDict, total=False):
    observation: str
    step: dict
    steps: int
    answer: str
    done: bool


@dataclass
class AgentResult:
    answer: str
    steps: int
    finished: bool


class Agent:
    def __init__(
        self,
        role: str,
        system: str,
        tools: list[Tool],
        *,
        cfg: config.Config | None = None,
        max_steps: int = 80,
        memory: int = 120_000,
        on_event: OnEvent = None,
    ):
        cfg = cfg or config.load()
        self.tools = {t.name: t for t in tools}
        self.max_steps = max_steps
        self.on_event = on_event
        self.closers: list[Callable[[], Awaitable[None]]] = []  # tool resources (e.g. a vision model)
        self.summarizer = Summarizer(cfg.role("summarizer"), style=Summarizer.WORK, limit=1500)
        self.session = Session(
            role, cfg.role(role), system + manual(tools), schema=step_schema(tools), memory=memory, summarizer=self.summarizer
        )
        g = StateGraph(LoopState)
        g.add_node("think", self._think)
        g.add_node("act", self._act)
        g.add_edge(START, "think")
        g.add_conditional_edges("think", lambda s: END if s.get("done") else "act", ["act", END])
        g.add_conditional_edges("act", lambda s: END if s.get("steps", 0) >= self.max_steps else "think", ["think", END])
        self.graph = g.compile()

    def _emit(self, kind: str, data: dict) -> None:
        if self.on_event:
            self.on_event(kind, data)

    async def _think(self, state: LoopState) -> dict:
        reply = await self.session.send(state["observation"])
        step = reply.data if isinstance(reply.data, dict) else {}
        self._emit("think", {**step, "repairs": reply.repairs})
        if step.get("tool") == "finish":
            return {"step": step, "answer": step.get("answer") or step.get("thought", ""), "done": True}
        return {"step": step}

    async def _act(self, state: LoopState) -> dict:
        step = state.get("step") or {}
        name = step.get("tool", "")
        tool = self.tools.get(name)
        args = {k: step[k] for k in (tool.args if tool else ()) if step.get(k) not in (None, "")}
        if tool is None:
            obs = f"出错：没有工具“{name}”。可用：{'、'.join(self.tools)}、finish"
        elif missing := [a for a in tool.required if a not in args]:
            obs = f"出错：{name} 缺少参数 {'、'.join(missing)}"
        else:
            try:
                obs = await tool.run(**args)
            except ToolError as exc:
                obs = f"出错：{exc}"
            except OSError as exc:
                obs = f"出错：{type(exc).__name__}: {exc}"
        if len(obs) > OBSERVATION_LIMIT:
            obs = obs[:OBSERVATION_LIMIT] + f"\n（结果太长，只给前 {OBSERVATION_LIMIT} 字）"
        steps = state.get("steps", 0) + 1
        if (left := self.max_steps - steps) <= 5:
            obs += f"\n\n（系统：只剩 {left} 步了，请收尾，最后调用 finish。）"
        self._emit("act", {"tool": name, "args": args, "observation": obs})
        return {"observation": f"〔{name} 的结果〕\n{obs}", "steps": steps}

    async def run(self, task: str) -> AgentResult:
        final = await self.graph.ainvoke({"observation": task, "steps": 0}, {"recursion_limit": self.max_steps * 2 + 10})
        return AgentResult(final.get("answer", ""), final.get("steps", 0), bool(final.get("done")))

    async def close(self) -> None:
        await self.session.close()
        await self.summarizer.close()
        for close in self.closers:
            await close()
