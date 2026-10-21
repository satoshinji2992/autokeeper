"""One actor-bound world gateway for the internal agents.

Queries are permission-filtered. Previews settle only into a detached copy; commits
publish a fully validated batch, never an incomplete prefix or a narrative promise.
"""

from __future__ import annotations

import copy
import json
import re
import threading

from pydantic import TypeAdapter

from ..engine.state import format_time
from . import agents, context, knowledge
from .ops import (
    ENGINE_ONLY,
    OPS,
    STAFF,
    AdvancePlan,
    Follow,
    Improvise,
    MakePromise,
    Move,
    Relate,
    Remember,
    Schedule,
    SetPlan,
    parse,
)
from .record import Entity, Fact, Request
from .store import World, digest

MAX_OPS = 24
MAX_DEPTH = 3


class Gateway:
    def __init__(self, world: World, actor: str):
        if actor not in STAFF and (world.entity(actor) is None or world.entity(actor).kind != "npc"):
            raise ValueError("世界工具的执行身份必须由宿主绑定为有效 NPC 或守秘人角色")
        self.world, self.actor = world, actor
        self.staff = actor in STAFF
        if not hasattr(world, "_gateway_lock"):
            world._gateway_lock = threading.RLock()

    def _facts(self):
        return [f for f in self.world.rec.facts.values() if not f.retracted] if self.staff else knowledge.known(self.world.rec, self.actor)

    def _events(self):
        return [e for e in self.world.rec.events if self.staff or knowledge.saw(e, self.actor)]

    def _entities(self):
        w = self.world
        if self.staff:
            return list(w.rec.entities.values())
        visible = agents.places(w, self.actor) | {self.actor, *w.present(w.place(self.actor))}
        people_and_places = [e for e in w.rec.entities.values() if e.kind != "item" and e.id in visible]
        return [*people_and_places, *knowledge.visible_items(w, self.actor)]

    def _allowed_reads(self) -> dict[str, int]:
        w = self.world
        refs = {f"fact:{f.id}" for f in self._facts()} | {f"entity:{e.id}" for e in self._entities()}
        refs |= {f"event:{e.id}" for e in self._events()}
        refs |= {f"agent:{a}" for a in w.rec.agents if self.staff or a == self.actor}
        refs |= {f"promise:{p.id}" for p in w.rec.promises.values() if self.staff or self.actor in (p.by, p.to)}
        refs |= {f"relation:{r.key}" for r in w.rec.relations.values() if self.staff or self.actor in r.known_by}
        if self.staff:
            refs |= {f"thread:{th}" for th in w.rec.threads}
        refs |= {f"scheduled:{s.id}" for s in w.rec.scheduled.values() if self.staff or s.actor == self.actor}
        return {ref: w.version(ref) for ref in refs}

    def _fact_data(self, fact) -> dict:
        if self.staff:
            return fact.model_dump(mode="json")
        sources = {fid: event for event in self._events() for fid in event.facts}
        labelled = context._fact_line(self.actor, fact, sources, self.world)
        return {"id": knowledge.public_ref(fact, self.actor), "text": fact.text, "version": fact.version,
                "source": labelled.rpartition("（")[2].removesuffix("）")}

    def _public_ref(self, ref: str) -> str:
        if not self.staff and (fact := self.world.rec.facts.get(ref)) is not None:
            return knowledge.public_ref(fact, self.actor)
        return ref

    def _public_reads(self, reads: dict[str, int]) -> dict[str, int]:
        return {"fact:" + self._public_ref(ref.removeprefix("fact:")) if ref.startswith("fact:") else ref: version
                for ref, version in reads.items()}

    def _public_data(self, value, key: str = ""):
        if self.staff:
            return value
        if isinstance(value, list):
            return [self._public_data(v, key) for v in value]
        if isinstance(value, dict):
            return {k: self._public_reads(v) if k == "reads" else self._public_data(v, k) for k, v in value.items()}
        if isinstance(value, str):
            if key in {"fact", "facts"}:
                return self._public_ref(value)
            if key in {"when", "achieved"}:
                return re.sub(r"(knows:[^: &|]+:)([^ &|]+)",
                              lambda match: match.group(1) + self._public_ref(match.group(2)), value)
            if key == "problems":
                return knowledge.public_text(self.world, self.actor, value)
            return value
        return value

    def native_reads(self, reads: dict[str, int]) -> dict[str, int]:
        """Host-only conversion; return canonical dependencies to the scheduler, never to an NPC."""
        if self.staff:
            return dict(reads)
        native = {}
        for ref, version in reads.items():
            if ref.startswith("fact:"):
                actual = knowledge.resolve_ref(self.world, self.actor, ref.removeprefix("fact:"))
                if actual is None:
                    raise ValueError("事实引用不在当前身份的可知范围内")
                ref = "fact:" + actual
            native[ref] = version
        return native

    def normalize_ops(self, ops: list[dict]) -> list[dict]:
        """Decode actor-scoped references before validation; this never grants new knowledge."""
        if self.staff:
            return copy.deepcopy(ops)

        def fact_ref(ref: str) -> str:
            actual = knowledge.resolve_ref(self.world, self.actor, ref)
            if actual is None:
                raise ValueError("事实引用不在当前身份的可知范围内")
            return actual

        def decode(item):
            if isinstance(item, list):
                return [decode(v) for v in item]
            if not isinstance(item, dict):
                return item
            out = {}
            for key, value in item.items():
                if key == "fact" and value:
                    out[key] = fact_ref(value)
                elif key == "facts":
                    out[key] = [fact_ref(ref) for ref in value]
                elif key == "reads":
                    out[key] = self.native_reads(value)
                elif key in {"when", "achieved"}:
                    out[key] = re.sub(r"(knows:[^: &|]+:)([^ &|]+)",
                                      lambda match: match.group(1) + fact_ref(match.group(2)), value)
                else:
                    out[key] = decode(value)
            return out

        return [decode(raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw) for raw in ops]

    def _entity_data(self, entity) -> dict:
        if self.staff:
            return entity.model_dump(mode="json")
        keep = {"id", "name", "kind", "at", "status", "conditions", "version"}
        if entity.id == self.actor:
            keep |= {"following", "follow_to"}
        if entity.kind == "item" and entity.at == self.actor:
            keep |= {"equipment", "quantity", "hidden", "important"}
        return entity.model_dump(mode="json", include=keep)

    def _select(self, rows: list, query: str, limit: int) -> list:
        if not query.strip():
            return rows[-limit:]
        terms = context._terms(query)
        hits = []
        for index, row in enumerate(rows):
            raw = (self._fact_data(row) if isinstance(row, Fact) else self._entity_data(row) if isinstance(row, Entity)
                   else self._public_data(row.model_dump(mode="json")))
            text = json.dumps(raw, ensure_ascii=False)
            score = len(context._terms(text) & terms) + (10 if query.casefold() in text.casefold() else 0)
            if score:
                hits.append((score, index, row))
        return [row for _, _, row in sorted(hits, key=lambda hit: (-hit[0], -hit[1]))[:limit]]

    def inspect(self, query: str = "", limit: int = 20) -> dict:
        w = self.world
        limit = max(1, min(100, int(limit)))
        view = context.director_view(w) if self.staff else context.npc_view(w, self.actor, query=query)
        facts = self._select(list(self._facts()), query, limit)
        events = self._select(self._events(), query, limit)
        entities = self._select(self._entities(), query, limit)
        plans = {npc: agent.model_dump(mode="json") if self.staff else agent.model_dump(
            mode="json", include={"npc", "goals", "plan", "mood", "resources", "wake", "version"})
                 for npc, agent in w.rec.agents.items()
                 if self.staff or npc == self.actor}
        schedules = [s for s in w.rec.scheduled.values() if self.staff or s.actor == self.actor]
        schedules = self._select(schedules, query, limit)
        allowed = self._allowed_reads()
        refs = {f"fact:{f.id}" for f in facts} | {f"event:{e.id}" for e in events} | {f"entity:{e.id}" for e in entities}
        refs |= set(view.reads)
        refs |= {f"agent:{npc}" for npc in plans}
        refs |= {f"scheduled:{s.id}" for s in schedules}
        # The actor's own location and plan must be part of every decision's dependency set.
        refs |= {f"entity:{self.actor}", f"agent:{self.actor}"} & set(allowed)
        reads = {ref: allowed[ref] for ref in sorted(refs) if ref in allowed}
        return {"ok": True, "actor": self.actor, "query": query, "context": view.text,
                "clock": format_time(w.now), "world_seq": w.rec.seq, "seen": view.seen,
                "facts": [self._fact_data(f) for f in facts],
                "events": self._public_data([e.model_dump(mode="json") for e in events]),
                "entities": [self._entity_data(e) for e in entities], "plans": self._public_data(plans),
                "schedules": self._public_data([s.model_dump(mode="json") for s in schedules]),
                "reads": self._public_reads(reads),
                "notice": "按当前身份过滤，未返回的内容不表示不存在；预演只返回可能结果。"}

    def query(self, query: str = "", limit: int = 20) -> dict:
        return self.inspect(query, limit)

    def schema(self) -> dict:
        schema = OPS.json_schema() if self.staff else TypeAdapter(agents.NpcOp).json_schema()
        if self.staff:
            schema["oneOf"] = [branch for branch in schema["oneOf"] if not branch.get("$ref", "").endswith("/Improvise")]
            schema.get("$defs", {}).pop("Improvise", None)
            schema.get("discriminator", {}).get("mapping", {}).pop("improvise", None)
            for name, kind in (("Effects", "effects"), ("EncounterStep", "encounter")):  # the engine's alone
                schema["oneOf"] = [branch for branch in schema["oneOf"] if not branch.get("$ref", "").endswith(f"/{name}")]
                schema.get("discriminator", {}).get("mapping", {}).pop(kind, None)
                schema.get("$defs", {}).pop(name, None)
        return {"actor": self.actor, "operations": schema,
                "notice": "actor、操作 id 由宿主绑定。时钟、检定与带图像/插件的场景包由游戏引擎结算。"}

    def _bind(self, raw: dict, *, ident: str, depth: int = 0):
        if depth > MAX_DEPTH:
            raise ValueError("嵌套计划或定时操作过深")
        if isinstance(raw, dict) and raw.get("kind") in ENGINE_ONLY:
            raise ValueError("伤害、效果与遭遇只由游戏引擎按掷骰结算，世界工具不能提交")
        op = parse({**raw, "actor": self.actor, "id": ident})
        if isinstance(op, Improvise):
            raise ValueError("场景即兴必须通过游戏引擎准备并提交，不能绕过插件、图像与场景校验")
        if not self.staff:
            TypeAdapter(agents.ReplyOp).validate_python(op.model_dump(mode="json"))
            subject = (op.entity if isinstance(op, Move) else op.npc if isinstance(op, Follow | Remember | SetPlan | AdvancePlan)
                       else op.a if isinstance(op, Relate) else op.by if isinstance(op, MakePromise) else self.actor)
            if subject != self.actor:
                raise ValueError("只能为自己安排或执行行动")
        if isinstance(op, Schedule):
            if len(op.ops) > MAX_OPS:
                raise ValueError("定时操作过多")
            op.ops = [self._bind(child, ident=f"{ident}:later:{i}", depth=depth + 1).model_dump(mode="json")
                      for i, child in enumerate(op.ops)]
        if isinstance(op, SetPlan):
            for step in op.steps:
                if len(step.ops) > MAX_OPS:
                    raise ValueError("计划步骤内的操作过多")
                step.ops = [self._bind(child, ident=f"{ident}:step:{step.id}:{i}", depth=depth + 1).model_dump(mode="json")
                            for i, child in enumerate(step.ops)]
        return op

    def _check_reads(self, reads: dict[str, int]) -> tuple[list[str], list[str]]:
        allowed = self._allowed_reads()
        try:
            reads = self.native_reads(reads)
        except ValueError as exc:
            return [str(exc)], []
        invalid = [ref for ref, version in reads.items() if ref not in allowed or not isinstance(version, int)]
        stale = [ref for ref, version in reads.items() if ref in allowed and self.world.version(ref) != version]
        return (["读取标记不在当前身份的可用范围内"] if invalid else []), list(self._public_reads({ref: 0 for ref in stale}))

    def _receipt(self, w: World, operations: list, results: list, *, status: str, request_id: str) -> dict:
        visible = Gateway(w, self.actor)
        result = {"ok": True, "status": status, "request_id": request_id, "actor": self.actor,
                "repeat": False, "clock": format_time(w.now), "world_seq": w.rec.seq,
                "ops": [{"id": result.op, "kind": op.kind, "actor": self.actor, "operation": op.model_dump(mode="json"), "event": result.event,
                         "repeat": result.repeat} for op, result in zip(operations, results, strict=True)],
                "positions": {e.id: e.at for e in visible._entities() if e.kind == "npc"},
                "plans": {npc: [{"id": s.id, "status": s.status, "achieved": s.achieved} for s in agent.plan]
                          for npc, agent in w.rec.agents.items() if self.staff or npc == self.actor},
                "events": [e.model_dump(mode="json") for result in results if result.event
                           and (e := w.rec.event(result.event)) is not None],
                "reads": visible._public_reads(visible._allowed_reads())}
        return visible._public_data(result)

    def _prepare(self, ops: list[dict], reads: dict[str, int], request_id: str) -> tuple[dict, World | None]:
        bad, stale = self._check_reads(reads)
        if bad or stale:
            return {"ok": False, "status": "stale" if stale else "rejected", "problems": bad,
                    "stale": stale, "actor": self.actor, "request_id": request_id}, None
        if not ops or len(ops) > MAX_OPS:
            return {"ok": False, "status": "rejected", "problems": [f"操作数必须为 1–{MAX_OPS}"], "actor": self.actor}, None
        w = self.world
        trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True), checker=w.checker)
        trial.clock = w.clock
        prepared, results = [], []
        try:
            for i, raw in enumerate(ops):
                raw = self.normalize_ops([raw])[0]
                op = self._bind(raw, ident=f"world-tool:{self.actor}:{digest(request_id)[:20]}:{i}")
                if not self.staff and (problems := agents.vet(trial, self.actor, op)):
                    return {"ok": False, "status": "rejected", "problems": problems, "index": i, "actor": self.actor}, None
                if isinstance(op, SetPlan):
                    op.reads = {ref: trial.version(ref) for ref in self.native_reads(reads)}
                result = trial.apply(op)
                if not result.ok:
                    return {"ok": False, "status": "rejected", "problems": result.problems, "index": i, "actor": self.actor}, None
                prepared.append(op)
                results.append(result)
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "status": "rejected", "problems": [str(exc)[:1000]], "actor": self.actor}, None
        if trial.st.minutes != w.st.minutes or trial.st.turn != w.st.turn:
            return {"ok": False, "status": "rejected", "problems": ["世界操作不能改动时钟或回合"]}, None
        receipt = self._receipt(trial, prepared, results, status="preview", request_id=request_id)
        receipt["post_reads"], receipt["reads"] = receipt["reads"], dict(reads)
        return receipt, trial

    def preview(self, ops: list[dict], reads: dict[str, int] | None = None, request_id: str = "preview") -> dict:
        with self.world._gateway_lock:
            return self._public_data(self._prepare(ops, self._public_reads(self._allowed_reads()) if reads is None else reads, request_id)[0])

    def commit(self, ops: list[dict], *, reads: dict[str, int], request_id: str) -> dict:
        if not request_id.strip() or len(request_id) > 160 or not reads:
            return {"ok": False, "status": "rejected", "problems": ["提交需要稳定 request_id 与查询或预演返回的非空 reads"]}
        w = self.world
        with w._gateway_lock:
            signature = digest([self.actor, ops, reads])
            rid = f"world-tool:{self.actor}:{digest(request_id)[:20]}"
            prior = w.rec.requests.get(rid)
            if prior is not None:
                if prior.reason != signature:
                    return {"ok": False, "status": "conflict", "problems": ["request_id 已用于另一组操作或读取版本"]}
                result = json.loads(prior.note)
                result["repeat"] = True
                result["positions_at_commit"] = result["positions"]
                result["positions"] = {e.id: e.at for e in self._entities() if e.kind == "npc"}
                return result
            result, trial = self._prepare(ops, reads, request_id)
            if trial is None:
                return self._public_data(result)
            # Both validation and publication are synchronous: the event loop cannot run
            # another turn between them. Preparing copies before publication avoids partial
            # application even if a later operation or a consistency check fails.
            result["status"] = "committed"
            result["input_reads"], result["reads"] = result["reads"], result.pop("post_reads")
            trial.rec.requests[rid] = Request(id=rid, key=rid, kind="world-tool", reason=signature,
                                               ops=copy.deepcopy(ops), reads=dict(reads), turn=w.st.turn,
                                               t=w.now, status="landed", note=json.dumps(result, ensure_ascii=False))
            bad, stale = self._check_reads(reads)
            if bad or stale:
                return {"ok": False, "status": "stale", "stale": stale, "problems": bad}
            self._publish(trial)
            return result

    def settle(self, ops: list[dict], *, request_id: str) -> dict:
        """The engine's own landing of what an actor does in this turn (an NPC's on-the-spot acts in
        its reply, agents/cast.Cast.npc): the same binding, vetting, trial on a copy and whole-group
        publication as commit, on the record as it is now. No client receipt is kept: the op ids
        come from `request_id`, so the same ops landed again are repeats."""
        w = self.world
        with w._gateway_lock:
            result, trial = self._prepare(ops, self._public_reads(self._allowed_reads()), request_id)
            if trial is None:
                return self._public_data(result)
            self._publish(trial)
            result["status"] = "committed"
            return result

    def _publish(self, trial: World) -> None:
        """What a validated copy changed, onto the live save at once."""
        w = self.world
        state_changes = {name: copy.deepcopy(value) for name, value in trial.st.__dict__.items()
                         if value != w.st.__dict__.get(name)}
        module_changes = {name: copy.deepcopy(value) for name, value in trial.m.__dict__.items()
                          if value != w.m.__dict__.get(name)}
        w.m.__dict__.update(module_changes)
        w.st.__dict__.update(state_changes)


def harness_tools(gateway: Gateway, *, writable: bool = False) -> list:
    """The same gateway in the provider-independent harness; identity is never an argument."""
    from ..harness.tools import Tool

    async def query(query: str = "", limit: int = 20) -> str:
        return json.dumps(gateway.inspect(query, limit), ensure_ascii=False)

    async def preview(ops: list[dict], reads: dict[str, int] | None = None) -> str:
        return json.dumps(gateway.preview(ops, reads), ensure_ascii=False)

    async def commit(ops: list[dict], reads: dict[str, int], request_id: str) -> str:
        return json.dumps(gateway.commit(ops, reads=reads, request_id=request_id), ensure_ascii=False)

    arguments = {"ops": {"type": "array", "items": {"type": "object"}},
                 "reads": {"type": "object", "additionalProperties": {"type": "integer"}}}
    result = [Tool("world_query", "查询当前身份可知的世界事实、亲历事件与计划", ("query", "limit"), query),
              Tool("world_preview", "在副本中校验一组操作，返回预计位置和完成状态", ("ops", "reads"), preview,
                   ("ops",), argument_schema=arguments)]
    if writable:
        result.append(Tool("world_commit", "以读取版本和稳定请求编号原子提交操作", ("ops", "reads", "request_id"),
                           commit, ("ops", "reads", "request_id"), argument_schema=arguments))
    return result
