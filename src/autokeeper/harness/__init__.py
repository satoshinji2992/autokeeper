"""The harness: everything between the agents and whatever model runs them.

    models    stateless channels (Claude Code, any LangChain provider, Codex stub) and the
              structured-output contract: native schema or prompted JSON, validation, repair
    context   sessions whose history the harness owns: windowing, compaction, persistence
    tools     a sandboxed workspace, file tools, a vision tool, web tools
    search    web search providers with an offline setting
    loop      an async agent loop that gives tools to models without tool-calling APIs
    spend     token-cost estimates and a hard budget cap for metered APIs
    trace     one JSON line per model call
"""

from .context import Session, Summarizer
from .loop import Agent, AgentResult
from .models import ContextBudgetExceeded, Model, ModelPool, Msg, OnDelta, Reply, make_model
from .spend import BudgetExceeded

__all__ = [
    "Agent",
    "AgentResult",
    "BudgetExceeded",
    "ContextBudgetExceeded",
    "Model",
    "ModelPool",
    "Msg",
    "OnDelta",
    "Reply",
    "Session",
    "Summarizer",
    "make_model",
]
