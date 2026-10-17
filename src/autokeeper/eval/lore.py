"""Can the keeper answer detail questions about the original module text?

A question set is written once from source/source.md (by the importer model) and
cached. Each question is then answered by the keeper model with one of four
contexts, and a judge model marks the answer against the reference:

    overview   the module Skill's SKILL.md only (what the keeper always carries)
    full       SKILL.md + every reference file (the whole compiled module)
    naive      SKILL.md + LightRAG naive retrieval (vector search over chunks)
    graphrag   SKILL.md + LightRAG mix retrieval (knowledge graph + chunks)

Context sizes are counted with tiktoken o200k as a model-neutral proxy.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field

from .. import config, knowledge, skills
from ..harness import Session
from ..module.compiler import skill_name
from ..module.schema import Module
from .context import tokens


class QA(BaseModel):
    question: str
    answer: str = Field(description="简短的参考答案")
    evidence: str = Field("", description="原文依据（一句）")


class QASet(BaseModel):
    items: list[QA]


class Verdict(BaseModel):
    correct: bool
    reason: str = ""


WRITER = """你在给一个跑团模组出“守秘人细节问答”题，用来测试 AI 守秘人记不记得模组原文。
从下面的原文里出 {n} 道题，覆盖不同章节：人物的具体特征与动机、地点里的具体物件、检定与理智损失的具体数值、
时间与事件顺序、线索之间的联系（需要串起两三处原文才能答的题占三分之一）。
每题的答案必须能在原文里找到依据，答案简短（一句话以内），并附一句原文依据。不要出主观题。

原文：
{source}"""

ANSWER = """你是这个模组的守秘人。根据给你的资料回答问题，一两句话，资料里没有就说“资料里没有”。

{context}

问题：{question}"""

JUDGE = "你是阅卷人。对照参考答案判断回答是否正确：关键信息一致就算对，多说无妨；关键信息缺失或错误算错。"


async def _call(role: str, system: str, prompt: str, schema: type[BaseModel] | None = None):
    cfg = config.load()
    s = Session(role, cfg.role(role), system, schema=schema.model_json_schema() if schema else None)
    try:
        reply = await s.send(prompt)
        return reply.data if schema else reply.text.strip()
    finally:
        await s.close()


async def question_set(m: Module, module_dir: Path, n: int = 24) -> list[QA]:
    cache = config.SAVES / "eval" / f"lore_qa_{m.id}.json"
    if cache.exists():
        return QASet.model_validate_json(cache.read_text(encoding="utf-8")).items
    source = (module_dir / "source" / "source.md").read_text(encoding="utf-8")
    data = await _call("importer", "你是严谨的出题人，用简体中文。", WRITER.format(n=n, source=source), QASet)
    qa = QASet.model_validate(data)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(qa.model_dump_json(indent=1), encoding="utf-8")
    return qa.items


async def run(module_dir: Path, out: Path, *, n: int = 24, parallel: int = 4) -> dict:
    m = Module.load(module_dir)
    if not knowledge.exists(m.id):
        raise ValueError(f"先运行 autokeeper index {module_dir} 建知识库")
    qa = await question_set(m, module_dir, n)
    name = skill_name(m)
    skills.clear_cache()
    overview = skills.body(name)
    root = config.SKILLS / name
    full = overview + "\n\n" + "\n\n".join(p.read_text(encoding="utf-8") for p in sorted((root / "references").rglob("*.md")))
    sem = asyncio.Semaphore(parallel)
    rows: list[dict] = []

    async with knowledge.KnowledgeBase(m) as kb:

        async def context_for(strategy: str, q: QA) -> str:
            if strategy == "overview":
                return overview
            if strategy == "full":
                return full
            mode = "naive" if strategy == "naive" else "mix"
            async with sem:
                found = await kb.context(q.question, mode=mode, max_tokens=4000)
            return overview + "\n\n【检索到的资料】\n" + found

        async def one(strategy: str, i: int, q: QA) -> None:
            ctx = await context_for(strategy, q)
            async with sem:
                answer = await _call("keeper", "你是 CoC 跑团的守秘人，用简体中文回答。", ANSWER.format(context=ctx, question=q.question))
                verdict = await _call(
                    "intent", JUDGE, f"问题：{q.question}\n参考答案：{q.answer}\n回答：{answer}", Verdict
                )
            ok = bool((verdict or {}).get("correct"))
            rows.append({"strategy": strategy, "i": i, "question": q.question, "reference": q.answer, "answer": answer,
                         "correct": ok, "context_tokens": tokens(ctx)})

        strategies = ["overview", "full", "naive", "graphrag"]
        await asyncio.gather(*(one(s, i, q) for s in strategies for i, q in enumerate(qa)))

    summary: dict = {"module": m.id, "questions": len(qa)}
    for s in strategies:
        mine = [r for r in rows if r["strategy"] == s]
        summary[s] = {
            "accuracy": round(sum(r["correct"] for r in mine) / max(1, len(mine)), 3),
            "context_tokens_avg": round(sum(r["context_tokens"] for r in mine) / max(1, len(mine))),
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": sorted(rows, key=lambda r: (r["strategy"], r["i"]))}, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
