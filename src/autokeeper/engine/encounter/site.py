"""Where a chase runs: its site, a small graph of places (engine/encounter/model.ChaseSite).

Built once when a chase begins, from what the module and the world give, in this order:

1. The module's routes (Location.chase): the route of the place the chase starts at, or of the
   place it is in (out of the study and through the house), merged with the routes of the places
   within two exits of it. A leg is a node; legs of different routes with the same id (or name) are
   one node; a leg's `links` are its branches, an `exit` ends its branch; `place` is the world
   location the node lies in, so running into it moves people there (module/graph.route_edges).
2. The world's map: the place, its exits and theirs, real exits as edges; the farthest are ways out.
3. The keeper's own draft (EncounterStart.site), checked by plan_check.
4. Nothing at all: the place and a short way out of it.

Each chase works on its own copy; what changes an obstacle for good (a door forced or unlocked, a
way barricaded, a barricade cleared, a door mended) lands with its unit in this game's world record
(WorldRecord.obstacles, effects.Obstacle) under the stable id of the way it is on (`way_key`: a
module route's leg, a place of the map), and the next chase there is built with it (`build`'s
`kept`), until something changes it again. The module's files are never written. Paths and distances
are the graph's (`path`, `distance`); a node's obstacle is crossed on entering it.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from ...module import graph as module_graph
from ...module.schema import Leg, Method
from .model import ChaseSite
from .plan import ObstacleState, SiteDraft, SiteEdge, SiteNode

if TYPE_CHECKING:
    from ...module.schema import Module

DEPTH = 2  # exits away from the start whose routes, or whose places, the site takes in
OPENING = ("力量", "锁匠", "机械维修")  # a barrier's one skill that breaks or unlocks it: open for those behind
BREAK = "break"  # the method id of breaking a barrier by damage (Keeper Rulebook p. 115), offered where it has hit points


# -- building ---------------------------------------------------------------------------------------
def build(m: Module, place: str, draft: SiteDraft | None = None, kept: dict[str, ObstacleState] | None = None) -> ChaseSite:
    """The site of a chase that starts at world location `place` (see the module docstring), its ways'
    obstacles as this game left them (`kept`: WorldRecord.obstacles)."""
    site = from_routes(m, place) or from_map(m, place) or (from_draft(draft, place) if draft else None) or bare(m, place)
    for n in site.nodes:
        ob = (kept or {}).get(way_key(site, n.id))
        if ob is not None:
            n.obstacle = ob.id
            site.obstacles[ob.id] = ob.model_copy(deep=True)
    return site


# -- what a game keeps of them --------------------------------------------------------------------------
def way_key(site: ChaseSite, nid: str) -> str:
    """The stable id of node `nid` as a way of this game's world, under which what is done to its
    obstacle is kept (WorldRecord.obstacles): "leg:<id>" for a leg of the module's routes, "place:<id>"
    for a place of the map; "" for a node drawn for one chase alone (the keeper's, the last resort's)."""
    n = node(site, nid)
    if n is None:
        return ""
    if site.source == "module":
        return f"leg:{n.id}"
    if site.source == "location_graph" and n.place == n.id:
        return f"place:{n.id}"
    return ""


def pristine(m: Module, key: str) -> ObstacleState | None:
    """The obstacle the module puts on way `key` (as a chase builds it), None where it puts none."""
    kind, _, ident = key.partition(":")
    leg = _leg(m, ident) if kind == "leg" else None
    return obstacle_of(ident, leg) if leg is not None else None


def _leg(m: Module, ident: str) -> Leg | None:
    return next((x for loc in m.locations for x in loc.chase if module_graph.leg_key(x) == ident), None)


def way_place(m: Module, key: str) -> str:
    """The world place way `key` lies in ("" when the module does not say)."""
    kind, _, ident = key.partition(":")
    if kind == "place":
        return ident
    leg = _leg(m, ident)
    return leg.place if leg is not None else ""


def ways_at(m: Module, kept: dict[str, ObstacleState], place: str) -> list[str]:
    """The ways in world place `place` whose obstacle this game changed, a line each with its id, as
    the keeper and the arbiter read them (outside a chase a ruling may mend, shut or clear them)."""
    out = []
    for key, ob in kept.items():
        if way_place(m, key) != place:
            continue
        kind, _, ident = key.partition(":")
        leg = _leg(m, ident) if kind == "leg" else None
        out.append(f"{leg.name if leg else ident}（{key}）：{ob.what}，{STATE_WORDS.get(ob.state, ob.state)}")
    return out


STATE_WORDS = {"intact": "完好", "open": "开着", "broken": "毁了", "blocked": "堵上了"}
MEND = r"修|补|复原|装回"


def ruled(ob: ObstacleState, what: str, by: str) -> str:
    """An obstacle a ruling changed, by its words (`what`): broken, opened or cleared, or shut and
    blocked; the line it comes to, "" when the words change nothing of it. Mending is the caller's
    (`pristine`)."""
    import re

    if re.search(r"破|毁|砸|塌", what):
        ob.state, ob.by = "broken", by
        return f"{ob.what}被毁掉了，后面的人可以直接过去：{what}"
    if re.search(r"开|移|推倒|搬走|清", what):
        ob.state, ob.by = "open", by
        return f"{ob.what}打开了，后面的人可以直接过去：{what}"
    if re.search(r"堵|挡|锁|闩|封|关", what):
        ob.state, ob.by = "blocked", by
        return f"{ob.what}被堵上了：{what}"
    return ""


def _solid(ob: ObstacleState | None) -> dict | None:
    """What an obstacle is, without whether the players have seen it."""
    return ob.model_dump(mode="json", exclude={"revealed"}) if ob is not None else None


def kept(m: Module, before: ChaseSite | None, after: ChaseSite | None) -> list[tuple[str, ObstacleState | None]]:
    """The obstacles a unit changed on the ways a game keeps (`way_key`), as they now are: each (way,
    obstacle), None when it is as the module has it again (mended, cleared away)."""
    if before is None or after is None:
        return []
    out = []
    for n in after.nodes:
        key = way_key(after, n.id)
        was = node(before, n.id)
        now = after.obstacles.get(n.obstacle)
        if not key or was is None or _solid(now) == _solid(before.obstacles.get(was.obstacle)):
            continue
        out.append((key, None if _solid(now) == _solid(pristine(m, key)) else now))
    return out


def _near(m: Module, place: str, depth: int = DEPTH) -> list[str]:
    """The places within `depth` exits of `place` (it first), and those it is in or that are in it."""
    locs = {x.id: x for x in m.locations}
    seen, queue = [place], deque([(place, 0)])
    while queue:
        here, d = queue.popleft()
        loc = locs.get(here)
        if loc is None or d >= depth:
            continue
        for nxt in [*loc.exits, *([loc.within] if loc.within else []), *(x.id for x in m.locations if x.within == here)]:
            if nxt in locs and nxt not in seen:
                seen.append(nxt)
                queue.append((nxt, d + 1))
    return seen


def obstacle_of(node: str, leg: Leg) -> ObstacleState | None:
    """A leg's obstacle as the chase keeps it: its ways past (the module's methods, else its one skill),
    and for a barrier with hit points, breaking it."""
    ob = leg.obstacle
    if ob is None:
        return None
    methods = [x.model_copy() for x in ob.methods] or [
        Method(id="pass", label="冲过去" if ob.kind == "hazard" else "越过去", skill=ob.skill, difficulty=ob.difficulty,
               opens=ob.kind == "barrier" and ob.skill in OPENING)]
    if ob.kind == "barrier" and ob.hp and not any(x.id == BREAK for x in methods):
        methods = [*methods, Method(id=BREAK, label="砸开", skill="伤害", opens=True)][:4]
    return ObstacleState(id=f"ob_{node}", kind=ob.kind, what=ob.what, methods=methods, harm=ob.harm, hp=ob.hp)


def from_routes(m: Module, place: str) -> ChaseSite | None:
    """The module's routes around `place`, merged into one graph; None when none runs from here."""
    locs = {x.id: x for x in m.locations}
    here, seen = place, set()
    while here and here not in seen and not (locs.get(here) and locs[here].chase):
        seen.add(here)
        here = locs[here].within if here in locs else ""
    if not here or here not in locs:
        return None
    routes = [locs[x].chase for x in [here, *_near(m, place)] if x in locs and locs[x].chase]
    names = {leg.name: module_graph.leg_key(leg) for legs in routes for leg in legs}
    nodes: dict[str, SiteNode] = {}
    obstacles: dict[str, ObstacleState] = {}
    edges: set[tuple[str, str]] = set()
    for legs in routes:
        for leg in legs:
            key = module_graph.leg_key(leg)
            if key not in nodes:
                ob = obstacle_of(key, leg)
                nodes[key] = SiteNode(id=key, name=leg.name, look=leg.look, place=leg.place, exit=leg.exit,
                                      obstacle=ob.id if ob else "")
                if ob is not None:
                    obstacles[ob.id] = ob
            else:  # the same leg in another route: what one of them says about it holds
                node = nodes[key]
                if not node.obstacle and (ob := obstacle_of(key, leg)) is not None:
                    node.obstacle, obstacles[ob.id] = ob.id, ob
                node.exit = node.exit or leg.exit
                node.place = node.place or leg.place
        for a, b in module_graph.route_edges(legs, names):
            if a != b:
                edges.add((a, b) if a < b else (b, a))
    edges = {(a, b) for a, b in edges if a in nodes and b in nodes}
    if len(nodes) < 2 or not edges:
        return None
    first = locs[here].chase
    origin = next((module_graph.leg_key(x) for x in first if x.place == place), None) or next(
        (k for k, n in nodes.items() if n.place == place), module_graph.leg_key(first[0]))
    site = ChaseSite(nodes=list(nodes.values()), edges=[SiteEdge(a=a, b=b) for a, b in sorted(edges)], obstacles=obstacles,
                     origin=origin, source="module")
    return _reachable(site)


def from_map(m: Module, place: str) -> ChaseSite | None:
    """The world's map around `place`: it, its exits and theirs; the farthest are ways out of the scene."""
    locs = {x.id: x for x in m.locations}
    if place not in locs:
        return None
    depth: dict[str, int] = {place: 0}
    queue = deque([place])
    while queue:
        here = queue.popleft()
        if depth[here] >= DEPTH:
            continue
        for nxt in locs[here].exits:
            if nxt in locs and nxt not in depth:
                depth[nxt] = depth[here] + 1
                queue.append(nxt)
    if len(depth) < 2:
        return None
    far = max(depth.values())
    nodes = [SiteNode(id=x, name=locs[x].name, place=x, exit=d == far) for x, d in depth.items()]
    edges = {tuple(sorted((a, b))) for a in depth for b in locs[a].exits if b in depth and a != b}
    return ChaseSite(nodes=nodes, edges=[SiteEdge(a=a, b=b) for a, b in sorted(edges)], origin=place, source="location_graph")


def from_draft(draft: SiteDraft, place: str) -> ChaseSite | None:
    """The keeper's drawn site (already checked by plan_check.site_problems); its first node the start."""
    if site_problems(draft):
        return None
    nodes = [n.model_copy(update={"place": n.place or (place if i == 0 else "")}) for i, n in enumerate(draft.nodes)]
    return ChaseSite(nodes=nodes, edges=[e.model_copy() for e in draft.edges],
                     obstacles={o.id: o.model_copy() for o in draft.obstacles}, origin=nodes[0].id, source="keeper")


def bare(m: Module, place: str) -> ChaseSite:
    """The last resort: the place, the way out of it, somewhere beyond."""
    name = next((x.name for x in m.locations if x.id == place), "原地")
    nodes = [SiteNode(id="here", name=name, place=place), SiteNode(id="near", name=f"{name}附近"),
             SiteNode(id="away", name="更远的地方", exit=True)]
    return ChaseSite(nodes=nodes, edges=[SiteEdge(a="here", b="near"), SiteEdge(a="near", b="away")], origin="here",
                     source="location_graph")


def _reachable(site: ChaseSite) -> ChaseSite:
    """The part of the site one can run to from its origin."""
    keep = set(distances(site, site.origin))
    nodes = [n for n in site.nodes if n.id in keep]
    edges = [e for e in site.edges if e.a in keep and e.b in keep]
    obstacles = {k: v for k, v in site.obstacles.items() if any(n.obstacle == k for n in nodes)}
    return site.model_copy(update={"nodes": nodes, "edges": edges, "obstacles": obstacles})


def site_problems(draft: SiteDraft) -> list[str]:
    """What is wrong with a keeper's drawn site, in words naming ids: [] when it can be run."""
    out = []
    ids = [n.id for n in draft.nodes]
    if len(set(ids)) != len(ids):
        out.append("site 里有重复的节点 id")
    known = set(ids)
    for e in draft.edges:
        for x in (e.a, e.b):
            if x not in known:
                out.append(f"site 的连线里 {x} 不是节点")
    obs = {o.id for o in draft.obstacles}
    for n in draft.nodes:
        if n.obstacle and n.obstacle not in obs:
            out.append(f"节点 {n.id} 的障碍 {n.obstacle} 没有写在 obstacles 里")
    if not out:
        site = ChaseSite(nodes=draft.nodes, edges=draft.edges, origin=ids[0], source="keeper")
        if len(distances(site, ids[0])) < len(ids):
            out.append("site 不连通")
        if not any(n.exit for n in draft.nodes):
            out.append("site 没有出口（exit）")
    return out


# -- reading it --------------------------------------------------------------------------------------
def node(site: ChaseSite, nid: str) -> SiteNode | None:
    return next((n for n in site.nodes if n.id == nid), None)


def neighbours(site: ChaseSite, nid: str) -> list[tuple[str, int]]:
    """The nodes next to `nid`, with what running there costs (action points), in the site's order."""
    out = []
    for e in site.edges:
        if e.a == nid:
            out.append((e.b, e.cost))
        elif e.b == nid:
            out.append((e.a, e.cost))
    order = {n.id: i for i, n in enumerate(site.nodes)}
    return sorted(out, key=lambda x: order.get(x[0], 0))


def distances(site: ChaseSite, start: str) -> dict[str, int]:
    """Locations (hops) from `start` to every node it reaches."""
    out = {start: 0}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for nxt, _ in neighbours(site, here):
            if nxt not in out:
                out[nxt] = out[here] + 1
                queue.append(nxt)
    return out


def distance(site: ChaseSite, a: str, b: str) -> int | None:
    return distances(site, a).get(b)


def path(site: ChaseSite, start: str, goal: str, *, avoid: set[str] | None = None) -> list[str]:
    """The shortest way from `start` to `goal`, both included ([] when there is none)."""
    if start == goal:
        return [start]
    back: dict[str, str] = {start: ""}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for nxt, _ in neighbours(site, here):
            if nxt in back or (avoid and nxt in avoid and nxt != goal):
                continue
            back[nxt] = here
            if nxt == goal:
                out = [goal]
                while out[-1] != start:
                    out.append(back[out[-1]])
                return out[::-1]
            queue.append(nxt)
    return []


def nearest_exit(site: ChaseSite, start: str, *, avoid: set[str] | None = None) -> str:
    """The way out nearest to `start` ("" when there is none it can reach)."""
    far = distances(site, start)
    exits = [n.id for n in site.nodes if n.exit and n.id in far and n.id != start and not (avoid and n.id in avoid)]
    return min(exits, key=lambda x: far[x]) if exits else ""


def sight_blocked(site: ChaseSite, a: str, b: str) -> bool:
    """Something between `a` and `b` on the way from one to the other hides one from the other: a node
    that blocks sight, or a barrier still shut."""
    way = path(site, a, b)
    for nid in way[1:-1] or []:
        n = node(site, nid)
        if n is None:
            continue
        if n.blocks_sight:
            return True
        ob = site.obstacles.get(n.obstacle)
        if ob is not None and ob.kind == "barrier" and ob.state in ("intact", "blocked"):
            return True
    return False


def line(site: ChaseSite, origin: str, ahead_to: str) -> tuple[list[str], int]:
    """A straight run through the site: the way from `origin` on to `ahead_to`, and before `origin` the
    longest way back that avoids it; with where `origin` is on it (the start of a chase, p. 124)."""
    ahead = path(site, origin, ahead_to) if ahead_to else [origin]
    rest = _without(site, set(ahead[1:]))
    far = distances(rest, origin)
    back = max(far, key=lambda k: (far[k], k)) if far else origin
    behind = path(rest, origin, back)[1:] if back != origin else []
    return [*behind[::-1], *ahead], len(behind)


def _without(site: ChaseSite, gone: set[str]) -> ChaseSite:
    return site.model_copy(update={"edges": [e for e in site.edges if e.a not in gone and e.b not in gone]})


def draft_of(site: ChaseSite) -> dict[str, Any]:
    """The site as the keeper's brief lists it."""
    return {"nodes": site.nodes, "edges": site.edges, "obstacles": list(site.obstacles.values())}


__all__ = ["build", "distance", "distances", "line", "nearest_exit", "neighbours", "node", "path", "sight_blocked", "site_problems"]
