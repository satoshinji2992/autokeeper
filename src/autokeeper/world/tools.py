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

from ..engine import world as legacy_world
from ..engine.state import format_time
from . import agents, context, knowledge, scene
from .ops import (
    ENGINE_ONLY,
    OPS,
    STAFF,
    AddFact,
    AdvancePlan,
    Bring,
    Condition,
    Enlist,
    Follow,
    Improvise,
    Learn,
    MakePromise,
    Move,
    Occur,
    Relate,
    Remember,
    Schedule,
    SetPlan,
    Transfer,
    execution_order,
    parse,
)
from .record import PLAYERS, Entity, Fact, Request, relation_key
from .store import World, _restore, digest

MAX_OPS = 24
MAX_DEPTH = 3


def _clue_disclosure(op: object) -> bool:
    return ((isinstance(op, Learn) and op.fact.startswith("clue:"))
            or (isinstance(op, Occur) and any(ref.startswith("clue:") for ref in op.facts)))


class Gateway:
    def __init__(self, world: World, actor: str, *, scene_context: scene.Context | None = None):
        if actor not in STAFF and (world.entity(actor) is None or world.entity(actor).kind != "npc"):
            raise ValueError("世界工具的执行身份必须由宿主绑定为有效 NPC 或守秘人角色")
        self.world, self.actor = world, actor
        self.scene_context = scene_context
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
            refs |= {f"flag:{flag}" for flag in w.m.flags}
        refs |= {f"scheduled:{s.id}" for s in w.rec.scheduled.values() if self.staff or s.actor == self.actor}
        return {ref: w.version(ref) for ref in refs}

    def snapshot_reads(self) -> dict[str, int]:
        """Host captures these before a model call; no context text or world mutation."""
        return self._public_reads(self._allowed_reads())

    def required_reads(self, ops: list[dict]) -> set[str]:
        """Host selects these refs from its pre-call snapshot (new staff refs use 0)."""
        refs = self._operation_refs(self.normalize_ops(ops))
        return set(self._public_reads(dict.fromkeys(refs, 0)))

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

    def inspect(self, query: str = "", limit: int = 20, *, compact: bool = False,
                scope: list[str] | None = None) -> dict:
        if compact or scope is not None:
            return self._inspect_scoped(query, limit, scope)
        w = self.world
        limit = max(1, min(100, int(limit)))
        view = context.director_view(w) if self.staff else context.npc_view(w, self.actor, query=query)
        facts = self._select(list(self._facts()), query, limit)
        events = self._select(self._events(), query, limit)
        entities = self._select(self._entities(), query, limit)
        plans = {npc: agent.model_dump(mode="json") if self.staff else agent.model_dump(
            mode="json", include={"npc", "goals", "goal_reason", "goal_source", "plan", "mood", "resources", "wake", "version"})
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

    def _inspect_scoped(self, query: str, limit: int, scope: list[str] | None) -> dict:
        """Return the selected records once, with only the versions actually read.

        A scope contains explicit refs (entity:warden, agent:warden, fact:truth),
        never an expansion to everything an omniscient keeper could have read.
        """
        w = self.world
        limit = max(1, min(100, int(limit)))
        allowed = self._allowed_reads()
        native_scope = set() if scope is not None else None
        for ref in scope or []:
            try:
                native_scope.update(self.native_reads({ref: 0}))
            except ValueError:
                pass  # An unknown scoped reference grants no information or knowledge.
        tables = {
            "facts": [(f"fact:{f.id}", f) for f in self._facts()],
            "events": [(f"event:{e.id}", e) for e in self._events()],
            "entities": [(f"entity:{e.id}", e) for e in self._entities()],
            "plans": [(f"agent:{ident}", a) for ident, a in w.rec.agents.items()],
            "schedules": [(f"scheduled:{s.id}", s) for s in w.rec.scheduled.values()],
            "promises": [(f"promise:{p.id}", p) for p in w.rec.promises.values()],
            "relations": [(f"relation:{r.key}", r) for r in w.rec.relations.values()],
            "threads": [(f"thread:{ident}", th) for ident, th in w.rec.threads.items()],
        }
        result = {"ok": True, "actor": self.actor, "query": query, "clock": format_time(w.now),
                  "world_seq": w.rec.seq}
        reads = {}
        for name, rows in tables.items():
            visible = [(ref, row) for ref, row in rows if ref in allowed
                       and (native_scope is None or ref in native_scope)]
            selected = self._select([row for _, row in visible], query, limit)
            ids = {id(row) for row in selected}
            reads.update({ref: allowed[ref] for ref, row in visible if id(row) in ids})
            if name == "facts":
                result[name] = [self._fact_data(row) for row in selected]
            elif name == "entities":
                result[name] = [self._entity_data(row) for row in selected]
            else:
                data = {row.npc: row.model_dump(mode="json", include=None if self.staff else {
                    "npc", "goals", "goal_reason", "goal_source", "plan", "mood", "resources", "wake", "version"
                }) for row in selected} if name == "plans" else [row.model_dump(mode="json") for row in selected]
                result[name] = self._public_data(data)
        # An NPC's decision always depends on its own location and current plan.
        if not self.staff:
            reads.update({ref: allowed[ref] for ref in (f"entity:{self.actor}", f"agent:{self.actor}") if ref in allowed})
        result["reads"] = self._public_reads(reads)
        if self.staff:
            flags = {flag: flag in w.st.flags for flag in w.m.flags
                     if native_scope is not None and f"flag:{flag}" in native_scope}
            if flags:
                result["flags"] = flags
                result["reads"].update({f"flag:{flag}": w.version(f"flag:{flag}") for flag in flags})
        return result

    def query(self, query: str = "", limit: int = 20, *, compact: bool = False,
              scope: list[str] | None = None) -> dict:
        return self.inspect(query, limit, compact=compact, scope=scope)

    def schema(self, kinds: list[str] | None = None) -> dict:
        schema = OPS.json_schema() if self.staff else TypeAdapter(agents.NpcOp).json_schema()
        if self.staff:
            schema["oneOf"] = [branch for branch in schema["oneOf"] if not branch.get("$ref", "").endswith("/Improvise")]
            schema.get("$defs", {}).pop("Improvise", None)
            schema.get("discriminator", {}).get("mapping", {}).pop("improvise", None)
            for name, kind in (("Effects", "effects"), ("EncounterStep", "encounter")):  # the engine's alone
                schema["oneOf"] = [branch for branch in schema["oneOf"] if not branch.get("$ref", "").endswith(f"/{name}")]
                schema.get("discriminator", {}).get("mapping", {}).pop(kind, None)
                schema.get("$defs", {}).pop(name, None)
        if kinds is not None:
            mapping = schema.get("discriminator", {}).get("mapping", {})
            unknown = set(kinds) - set(mapping)
            if unknown or not kinds:
                raise ValueError("操作种类不可用：" + "、".join(sorted(unknown or {"空列表"})))
            mapping = {kind: ref for kind, ref in mapping.items() if kind in kinds}
            schema["discriminator"]["mapping"] = mapping
            schema["oneOf"] = [branch for branch in schema["oneOf"] if branch.get("$ref") in mapping.values()]
            # Keep nested definitions used by these operations, not the whole op union.
            definitions = schema.get("$defs", {})
            needed = set()

            def collect(value):
                if isinstance(value, dict):
                    ref = value.get("$ref", "").removeprefix("#/$defs/")
                    if ref in definitions and ref not in needed:
                        needed.add(ref)
                        collect(definitions[ref])
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(schema["oneOf"])
            schema["$defs"] = {name: value for name, value in definitions.items() if name in needed}
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
        if isinstance(op, Condition) and any(i.id == op.entity for i in self.world.st.investigators):
            raise ValueError("调查员的状态须由正式规则结算，世界工具不能添加、移除或恢复")
        if self.scene_context is not None:
            investigators = {PLAYERS, *(i.id for i in self.world.st.investigators)}
            recipients = (op.who if isinstance(op, Learn) else [*op.participants, *op.witnesses]
                          if isinstance(op, Occur) else [])
            if ((isinstance(op, AddFact) and op.fact.startswith("clue:"))
                    or (_clue_disclosure(op) and (investigators.intersection(recipients)
                        or (depth and (op.visibility == "public"
                                       or (isinstance(op, Occur) and op.visibility != "private")))))):
                raise ValueError("模组线索须由正式线索结算分发，不能通过通用事实或见闻操作发给调查员；理智、神话与手稿需一并结算")
        if not self.staff:
            TypeAdapter(agents.ReplyOp).validate_python(op.model_dump(mode="json"))
            subject = (op.entity if isinstance(op, Move) else op.npc if isinstance(op, Follow | Remember | SetPlan | AdvancePlan | Enlist)
                       else op.a if isinstance(op, Relate) else op.by if isinstance(op, MakePromise) else self.actor)
            if subject != self.actor:
                raise ValueError("只能为自己安排或执行行动")
        if isinstance(op, Schedule):
            if len(op.ops) > MAX_OPS:
                raise ValueError("定时操作过多")
            op.ops = [self._bind(child, ident=f"{ident}:later:{i}", depth=depth + 1).model_dump(mode="json")
                      for i, child in enumerate(op.ops)]
        if isinstance(op, SetPlan):
            for step in op.steps or []:
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
        # A staff transaction may create a specifically named record. Its captured
        # absence is a version too; another writer creating it makes that read stale.
        absent = {ref for ref, version in reads.items() if self.staff and version == 0
                  and ref.partition(":")[0] in {"entity", "fact", "agent", "promise", "relation", "thread", "scheduled"}
                  and ref.partition(":")[2] and ref not in allowed}
        invalid = [ref for ref, version in reads.items() if (ref not in allowed and ref not in absent)
                   or not isinstance(version, int)]
        stale = [ref for ref, version in reads.items() if (ref in allowed or ref in absent)
                 and self.world.version(ref) != version]
        return (["读取标记不在当前身份的可用范围内"] if invalid else []), list(self._public_reads({ref: 0 for ref in stale}))

    def _operation_refs(self, operations: list) -> set[str]:
        """Direct record dependencies of an operation, including its nested plan.

        Caller-supplied contextual reads remain authoritative too. These direct
        targets prevent a scoped writer from silently omitting what it overwrites.
        """
        refs = set()
        entity_fields = {"entity", "item", "npc", "a", "b", "by", "to", "giver", "holder", "place", "leader",
                         "destination", "participants", "about", "known_by", "who", "entities"}

        def collect(value):
            if isinstance(value, list):
                for child in value:
                    collect(child)
            elif isinstance(value, dict):
                for key, child in value.items():
                    values = child if isinstance(child, list) else [child]
                    if key in entity_fields:
                        refs.update(f"entity:{ident}" for ident in values if isinstance(ident, str))
                    if key == "npc" and isinstance(child, str):
                        refs.add(f"agent:{child}")
                    if key in {"fact", "facts", "promise", "thread"}:
                        prefix = "fact" if key == "facts" else key
                        refs.update(f"{prefix}:{ident}" for ident in values if isinstance(ident, str))
                    if key == "target" and isinstance(child, str):
                        refs.add(child)
                    if key in {"when", "achieved"} and isinstance(child, str):
                        for kind, rest, _ in agents.condition_atoms(child):
                            a, _, b = rest.partition(":")
                            if kind in {"at", "owner", "knows"}:
                                refs.add(f"entity:{a}")
                                refs.add(f"fact:{b}" if kind == "knows" else f"entity:{b}")
                            elif kind in {"flag", "promise"}:
                                refs.add(f"{kind}:{a}")
                            elif kind == "gone":
                                refs.add(f"entity:{rest}")
                    collect(child)
                if value.get("kind") == "schedule" and value.get("event"):
                    refs.add(f"scheduled:{value['event']}")
                if value.get("kind") == "flag" and value.get("flag"):
                    refs.add(f"flag:{value['flag']}")
                if value.get("kind") == "relate" and all(value.get(key) for key in ("a", "b", "rel")):
                    refs.add(f"relation:{relation_key(value['a'], value['b'], value['rel'])}")
                if value.get("kind") == "rewrite":
                    refs.update(f"agent:{npc}" for key in ("goals", "memories") for npc in value.get(key, {}))
                if value.get("kind") in {"transfer", "equip"} and value.get("item"):
                    ident = legacy_world.item_id(self.world.m, self.world.st, value["item"],
                                                holder=value.get("giver") or ("" if self.staff else self.actor))
                    if ident:
                        refs.discard(f"entity:{value['item']}")
                        refs.add(f"entity:{ident}")

        collect([op.model_dump(mode="json") if hasattr(op, "model_dump") else op for op in operations])
        refs = {ref for ref in refs if ref.partition(":")[2]}
        if not self.staff:
            refs &= set(self._allowed_reads())
        if not refs:
            # A plain occurrence can be an append without explicit participants or
            # facts. Anchor that decision to its current scene (NPC: itself), so
            # a valid preview still supplies a usable, nonempty commit receipt.
            anchor = self.world.st.location if self.staff else self.actor
            if anchor:
                refs.add(f"entity:{anchor}")
        return refs

    def _receipt(self, w: World, operations: list, results: list, *, status: str, request_id: str,
                 compact: bool = False, reads: dict[str, int] | None = None) -> dict:
        visible = Gateway(w, self.actor)
        refs = visible._operation_refs(operations) | set(self.native_reads(reads or {}))
        versions = visible._allowed_reads()
        if compact:
            versions = {ref: version for ref, version in versions.items() if ref in refs}
        result = {"ok": True, "status": status, "request_id": request_id, "actor": self.actor,
                "repeat": False, "clock": format_time(w.now), "world_seq": w.rec.seq,
                "ops": [{"id": result.op, "kind": op.kind, "actor": self.actor, "operation": op.model_dump(mode="json"), "event": result.event,
                         "repeat": result.repeat} for op, result in zip(operations, results, strict=True)],
                "positions": {e.id: e.at for e in visible._entities() if e.kind == "npc"
                              and (not compact or f"entity:{e.id}" in refs)},
                "plans": {npc: [{"id": s.id, "status": s.status, "achieved": s.achieved} for s in agent.plan]
                          for npc, agent in w.rec.agents.items() if (self.staff or npc == self.actor)
                          and (not compact or f"agent:{npc}" in refs)},
                "events": [e.model_dump(mode="json") for result in results if result.event
                           and (e := w.rec.event(result.event)) is not None],
                "reads": visible._public_reads(versions)}
        if compact:
            for op in result["ops"]:
                op.pop("operation")
        return visible._public_data(result)

    def _prepare(self, ops: list[dict], reads: dict[str, int], request_id: str, *,
                 compact: bool = False) -> tuple[dict, World | None]:
        bad, stale = self._check_reads(reads)
        if bad or stale:
            return {"ok": False, "status": "stale" if stale else "rejected", "problems": bad,
                    "stale": stale, "actor": self.actor, "request_id": request_id}, None
        if not ops or len(ops) > MAX_OPS:
            return {"ok": False, "status": "rejected", "problems": [f"操作数必须为 1–{MAX_OPS}"], "actor": self.actor}, None
        w = self.world
        trial = World(w.m.model_copy(deep=True), w.st.model_copy(deep=True), checker=w.checker)
        trial.clock = w.clock
        root = getattr(w.st, "root", w.st)
        encounters = [state.encounter for state in root.scenes.values() if not state.closed]
        prepared, results = [], []
        try:
            for i, raw in execution_order(ops):
                raw = self.normalize_ops([raw])[0]
                op = self._bind(raw, ident=f"world-tool:{self.actor}:{digest(request_id)[:20]}:{i}")
                if compact and (missing := self._operation_refs([op]) - set(self.native_reads(reads))):
                    return {"ok": False, "status": "rejected", "actor": self.actor,
                            "problems": ["先查询操作涉及的记录，再使用其 reads 提交"],
                            "missing_reads": list(self._public_reads(dict.fromkeys(sorted(missing), 0)))}, None
                if not self.staff and (problems := agents.vet(trial, self.actor, op)):
                    return {"ok": False, "status": "rejected", "problems": problems, "index": i, "actor": self.actor}, None
                if isinstance(op, SetPlan):
                    op.reads = {ref: trial.version(ref) for ref in self.native_reads(reads)}
                when, departure = trial.now, trial.now
                if self.scene_context is not None and isinstance(op, Move):
                    if problems := scene.movement_problems(trial, op.entity, op.to, self.scene_context,
                                                           encounters=encounters):
                        return {"ok": False, "status": "rejected", "problems": problems, "index": i,
                                "actor": self.actor}, None
                    begin, when = self.scene_context.interval or (trial.now - self.scene_context.elapsed, trial.now)
                    available = trial.available_at(op.entity)
                    departure = max(begin, available if available is not None else begin)
                if self.scene_context is not None and isinstance(op, Transfer | Bring):
                    when = self.scene_context.interval[1] if self.scene_context.interval else trial.now
                    problems = []
                    if isinstance(op, Transfer) and (item := op._item_id(trial)):
                        if before := legacy_world.owner(trial.m, trial.st, item):
                            problems = scene.meeting_problems(
                                trial, before, op.to,
                                f"{trial.name(before)}与{trial.name(op.to)}不在同一地点，{trial.name(item)}无法当面交付。",
                                at=when, items=[item])
                    elif isinstance(op, Bring) and op.holder:
                        problems = scene.meeting_problems(
                            trial, op.holder, trial.st.location,
                            f"{trial.name(op.holder)}不在当前场景，不能在其背包中直接登记{op.name}。",
                            at=when, items=[op.item] if op.item else [])
                    if problems:
                        return {"ok": False, "status": "rejected", "problems": problems, "index": i,
                                "actor": self.actor}, None
                with trial.at(when, start=departure):
                    result = trial.apply(op)
                if not result.ok:
                    return {"ok": False, "status": "rejected", "problems": result.problems, "index": i, "actor": self.actor}, None
                if self.scene_context is not None and _clue_disclosure(op) and result.event:
                    event = trial.rec.event(result.event)
                    if event is not None and {inv.id for inv in trial.st.investigators}.intersection(event.witnesses):
                        return {"ok": False, "status": "rejected", "index": i, "actor": self.actor,
                                "problems": ["调查员实际能获知的模组线索须经正式线索结算，不能只登记事实而漏掉理智、神话或手稿。"]}, None
                prepared.append(op)
                results.append(result)
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "status": "rejected", "problems": [str(exc)[:1000]], "actor": self.actor}, None
        if trial.st.minutes != w.st.minutes or trial.st.turn != w.st.turn:
            return {"ok": False, "status": "rejected", "problems": ["世界操作不能改动时钟或回合"]}, None
        receipt = self._receipt(trial, prepared, results, status="preview", request_id=request_id,
                                compact=compact, reads=reads)
        receipt["post_reads"], receipt["reads"] = receipt["reads"], dict(reads)
        return receipt, trial

    def preview(self, ops: list[dict], reads: dict[str, int] | None = None, request_id: str = "preview", *,
                compact: bool = False) -> dict:
        with self.world._gateway_lock:
            if reads is None:
                allowed = self._allowed_reads()
                try:
                    refs = self._operation_refs(self.normalize_ops(ops)) if compact else set(allowed)
                except (TypeError, ValueError) as exc:
                    return {"ok": False, "status": "rejected", "problems": [str(exc)]}
                reads = self._public_reads({ref: allowed.get(ref, 0) for ref in refs})
            return self._public_data(self._prepare(ops, reads, request_id, compact=compact)[0])

    def commit(self, ops: list[dict], *, reads: dict[str, int], request_id: str, compact: bool = False) -> dict:
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
                result["positions"] = {e.id: e.at for e in self._entities() if e.kind == "npc"
                                       and (not compact or e.id in result["positions_at_commit"])}
                return result
            result, trial = self._prepare(ops, reads, request_id, compact=compact)
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
        # Scoped scene views store routing metadata in __dict__, not game fields.
        # Compare the same materialized projection used to prepare the transaction;
        # publish through attributes so scene cursors stay local and world data shared.
        current = w.st.model_copy()
        state_changes = {name: copy.deepcopy(value) for name, value in trial.st.__dict__.items()
                         if value != getattr(current, name)}
        module_changes = {name: copy.deepcopy(value) for name, value in trial.m.__dict__.items()
                          if value != w.m.__dict__.get(name)}
        # Other scenes may be awaiting a model with investigator/entity references. Merge the
        # validated values into those objects, rather than orphaning their later rule updates.
        for name, value in module_changes.items():
            setattr(w.m, name, _restore(getattr(w.m, name), value))
        for name, value in state_changes.items():
            setattr(w.st, name, _restore(getattr(w.st, name), value))


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
