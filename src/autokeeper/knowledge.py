"""Module knowledge base on LightRAG (GraphRAG).

What goes in:
    * the module's original text (source/source.md for imported modules) and the
      compiled Skill references, chunked and entity-extracted by LightRAG;
    * the structured module graph (locations, NPCs, clues, conclusions, handouts and
      their edges) inserted as a custom KG, using the same human-readable names, so
      LightRAG's extracted entities and ours merge into one graph.

Models: extraction and answers use the "lore" role (Claude Haiku by default) through
the same session layer as every other agent; embeddings come from a local
BAAI/bge-small-zh-v1.5 via fastembed (ONNX on CPU), so no extra API is needed.
Storage: saves/kb/<module>/ (LightRAG's default JSON, NetworkX and nano-vectordb files).

In play the keeper's retrieval passes keywords taken from the structured action
(target, NPC, location), which skips LightRAG's LLM keyword step: a lookup is
local embedding plus graph traversal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from . import config
from .config import Config
from .harness import ModelPool
from .module import graph as module_graph
from .module.compiler import compile_module, skill_name
from .module.schema import Module

EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512
ENTITY_TYPES = ["人物", "地点", "物品", "组织", "事件", "怪物", "线索"]

_embedder = None


def _model():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(EMBED_MODEL, cache_dir=str(config.SAVES / "models"))
    return _embedder


async def embed(texts: list[str]):
    import numpy as np

    return np.array(await asyncio.to_thread(lambda: list(_model().embed(texts))))


def available() -> bool:
    """LightRAG and fastembed are an optional extra (uv sync --extra graphrag)."""
    try:
        import fastembed  # noqa: F401
        import lightrag  # noqa: F401
    except ImportError:
        return False
    return True


def kb_dir(module_id: str) -> Path:
    return config.SAVES / "kb" / module_id


def exists(module_id: str) -> bool:
    return (kb_dir(module_id) / "graph_chunk_entity_relation.graphml").exists()


def documents(m: Module, module_dir: Path) -> list[tuple[str, str]]:
    """(name, text) pairs to index: original text first, then the compiled references."""
    docs = []
    source = Path(module_dir) / "source" / "source.md"
    if source.exists():
        docs.append(("source/source.md", source.read_text(encoding="utf-8")))
    root = config.SKILLS / skill_name(m)
    if not (root / "SKILL.md").exists():
        compile_module(m, config.SKILLS)
    for p in sorted((root / "references").rglob("*.md")):
        docs.append((str(p.relative_to(root)), p.read_text(encoding="utf-8")))
    return docs


_KIND = {"location": "地点", "npc": "人物", "clue": "线索", "conclusion": "结论", "handout": "手稿", "event": "事件"}
_REL = {
    "connects": "相通",
    "at": "常在",
    "can_tell": "知道",
    "found_at": "可在此发现",
    "handout": "附带手稿",
    "points_to": "支持结论",
    "happens_at": "发生在",
    "performs": "表演",
}


def custom_kg(m: Module) -> dict[str, Any]:
    """The structured module graph in LightRAG's custom-KG format."""
    g = module_graph.build(m)
    source = f"module-{m.id}"
    names: dict[str, str] = {}
    taken: set[str] = set()
    priority = ["location", "npc", "clue", "conclusion", "event", "handout"]
    ordered = sorted(g.nodes(data=True), key=lambda nd: priority.index(nd[1].get("kind")) if nd[1].get("kind") in priority else 99)
    for node, data in ordered:
        name = str(data.get("name", node))
        if name in taken:  # e.g. a clue and its handout share a title
            name = f"{name}（{_KIND.get(data.get('kind', ''), '条目')}）"
        names[node] = name
        taken.add(name)
    entities = []
    for node, data in g.nodes(data=True):
        kind = data.get("kind", "")
        entities.append(
            {
                "entity_name": names[node],
                "entity_type": _KIND.get(kind, kind),
                "description": module_graph._describe(m, node, kind) or names[node],
                "source_id": source,
                "file_path": "module.yaml",
            }
        )
    relationships = []
    for src, tgt, data in g.edges(data=True):
        if src not in names or tgt not in names or names[src] == names[tgt]:
            continue
        rel = _REL.get(data["rel"], data["rel"])
        relationships.append(
            {
                "src_id": names[src],
                "tgt_id": names[tgt],
                "description": f"{names[src]}{rel}{names[tgt]}",
                "keywords": rel,
                "weight": 2.0,
                "source_id": source,
                "file_path": "module.yaml",
            }
        )
    chunk = {"content": f"《{m.title}》结构化模组图谱：{len(entities)} 个实体，{len(relationships)} 条关系。", "source_id": source}
    return {"chunks": [chunk], "entities": entities, "relationships": relationships}


def _llm_func(pool: ModelPool, stats: dict[str, int]):
    """LightRAG's llm_model_func. A plain closure, not a bound method: LightRAG deep-copies
    its config, and a bound method would drag this object (and the LightRAG instance it
    holds) into the copy."""

    async def llm(prompt: str, system_prompt: str | None = None, history_messages=None, **_: Any) -> str:
        history = [{"role": h["role"], "content": h["content"]} for h in history_messages or [] if h.get("role") in ("user", "assistant")]
        reply = await pool.complete(system_prompt or "你是严谨的知识图谱助手，用简体中文回答。", [*history, {"role": "user", "content": prompt}])
        stats["llm_calls"] += 1
        return reply.text

    return llm


class KnowledgeBase:
    def __init__(self, m: Module, cfg: Config | None = None, *, parallel: int = 4):
        self.m = m
        self.cfg = cfg or config.load()
        self.dir = kb_dir(m.id)
        self.pool = ModelPool(self.cfg.role("lore"), size=parallel, label="lore")
        self.rag = None
        self.stats = {"llm_calls": 0}

    async def open(self) -> KnowledgeBase:
        import logging

        from lightrag import LightRAG
        from lightrag.utils import EmbeddingFunc

        for name in ("lightrag", "nano-vectordb", "httpx", "httpx2"):  # their INFO lines would land in the game log
            logging.getLogger(name).setLevel(logging.WARNING)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rag = LightRAG(
            working_dir=str(self.dir),
            llm_model_func=_llm_func(self.pool, self.stats),
            embedding_func=EmbeddingFunc(embedding_dim=EMBED_DIM, max_token_size=512, func=embed, model_name=EMBED_MODEL),
            chunk_token_size=800,
            chunk_overlap_token_size=100,
            llm_model_max_async=4,
            entity_extract_max_gleaning=0,
            addon_params={"language": "Simplified Chinese", "entity_types": ENTITY_TYPES},
        )
        await self.rag.initialize_storages()
        return self

    async def close(self) -> None:
        if self.rag is not None:
            await self.rag.finalize_storages()
            self.rag = None
        await self.pool.close()

    async def __aenter__(self) -> KnowledgeBase:
        return await self.open()

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    async def build(self, module_dir: Path) -> dict[str, int]:
        assert self.rag is not None
        docs = documents(self.m, module_dir)
        await self.rag.ainsert([text for _, text in docs], ids=[f"{self.m.id}:{name}" for name, _ in docs], file_paths=[name for name, _ in docs])
        kg = custom_kg(self.m)
        await self.rag.ainsert_custom_kg(kg)
        return {"documents": len(docs), "llm_calls": self.stats["llm_calls"], "entities": len(kg["entities"]), "relationships": len(kg["relationships"])}

    async def context(self, question: str, *, keywords: list[str] | None = None, mode: str = "mix", max_tokens: int = 2000) -> str:
        """Retrieved context only (no answer). With keywords, no LLM call is made."""
        from lightrag import QueryParam

        assert self.rag is not None
        param = QueryParam(mode=mode, only_need_context=True, top_k=20, chunk_top_k=6, max_total_tokens=max_tokens, enable_rerank=False)
        if keywords:
            param.ll_keywords = [k for k in keywords if k]
            param.hl_keywords = [question]
        return str(await self.rag.aquery(question, param=param) or "")

    async def answer(self, question: str, *, mode: str = "mix") -> str:
        from lightrag import QueryParam

        assert self.rag is not None
        param = QueryParam(mode=mode, response_type="简短的中文回答", enable_rerank=False)
        return str(await self.rag.aquery(question, param=param) or "")
