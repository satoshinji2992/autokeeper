"""Dice thrown for real: rigid d10s tumbling in a tray, and the face that ends on top is the roll.

numpy only, deterministic for a seed; in play the seed comes from the system's randomness. A
throw starts each die at a uniformly random orientation somewhere over the tray, flung and
spinning at random. The dice fall, bounce off the floor, the walls and each other, slide and
roll until each lies still with a face clearly up; a die balanced on an edge or a corner does
not count, and one that will not settle gets a small random nudge and the throw goes on.

The engine is vectorised over dice, so one throw at the table and thousands for the fairness
test run the same code. The recording of a throw (each die's centre and turn, FPS frames a
second, until the last frame is the pose it was read in) goes with the check's event, so every
screen, the LAN clients' too, replays the very throw that decided the roll.

Units are the die's radius (apex to centre) and seconds. The world has x to the right, y away
from the viewer and z up; the tray's floor is z = 0.
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from .bcdice import BCDice, Level, Roll, shared

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())  # recorded if logging is set up, never printed over the game

FPS = 25  # frames a second in the recording
STEP = 1 / 150  # the simulation's step
GRAVITY = 70.0  # radii a second squared: a slow-motion world, so the eye can follow the tumble
BOUNCE = (0.45, 0.6)  # restitution off the floor and off the walls
SLOW = 1.2  # contacts slower than this do not bounce: a die settles instead of chattering
FRICTION = 0.5
SPIN_DAMPING = 0.8  # angular velocity lost a second in the air
ROLL_DAMPING = 2.5  # and on the floor, with sliding slowed too
SLIDE_DAMPING = 1.0
REST_DAMPING = 8.0  # once it lies flat on a kite
INERTIA = 0.24  # a solid this round turns like a ball: one number, the same about every axis (mass 1)
KNOCK = 0.84  # dice knock each other as balls this big; cheap, and their corners hide the difference
FLAT = math.cos(math.radians(4))  # how level the top face must lie to be read
TURN = (110.0, 16.0, 220.0, 9.0)  # a die lying flat turns its top number to the viewer: stiffness, damping, most a second², most a second
SETTLING = math.cos(math.radians(10))  # it starts turning once this level
UPRIGHT = math.radians(7)  # how near upright it must lie before it rests
NUDGE_AFTER, NUDGE_EVERY, GIVE_UP = 2.5, 1.0, 6.0  # seconds
TRAYS = {1: (1.3, 1.1), 2: (2.15, 1.12), 3: (2.95, 1.55), 4: (3.6, 1.9)}  # half width and depth for that many dice


# -- the solid -------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def d10() -> tuple[np.ndarray, tuple[tuple[int, ...], ...], np.ndarray]:
    """A pentagonal trapezohedron of radius 1: twelve vertices (0 the top apex, 1 the bottom,
    2-6 the upper ring, 7-11 the lower), ten kites wound outwards (even ones round the top
    apex), and the kites' outward normals."""
    c = math.cos(math.pi / 5)
    h = (1 - c) / (1 + c)  # the rings' height that keeps every kite flat
    up = [(math.cos(math.radians(a)), math.sin(math.radians(a)), h) for a in range(0, 360, 72)]
    down = [(math.cos(math.radians(a + 36)), math.sin(math.radians(a + 36)), -h) for a in range(0, 360, 72)]
    verts = np.array([(0, 0, 1), (0, 0, -1), *up, *down], dtype=float)
    faces, normals = [], []
    for i in range(5):
        for f in ([0, 2 + i, 7 + i, 2 + (i + 1) % 5], [1, 7 + i, 2 + (i + 1) % 5, 7 + (i + 1) % 5]):
            p = verts[f]
            n = np.cross(p[1] - p[0], p[2] - p[0])
            if np.dot(n, p.mean(axis=0)) < 0:
                f, n = f[::-1], -n
            faces.append(tuple(f))
            normals.append(n / np.linalg.norm(n))
    return verts, tuple(faces), np.array(normals)


@lru_cache(maxsize=1)
def apexes() -> np.ndarray:
    """Each kite's way up, in the die's space: from the middle of its side corners to its apex,
    the way its number reads."""
    verts, faces, _ = d10()
    out = []
    for f in faces:
        i = next(j for j, v in enumerate(f) if v in (0, 1))
        mid = (verts[f[(i + 1) % 4]] + verts[f[(i + 3) % 4]]) / 2
        up = verts[f[i]] - mid
        out.append(up / np.linalg.norm(up))
    return np.array(out)


@lru_cache(maxsize=1)
def numbering() -> tuple[int, ...]:
    """The digit on each kite, as on a real d10: 0 2 4 6 8 round the top apex, each odd digit
    on the far side from the even one it adds up to nine with."""
    normals = d10()[2]
    out = [0] * 10
    for i in range(5):
        out[2 * i] = 2 * i
        out[int(np.argmin(normals @ normals[2 * i]))] = 9 - 2 * i
    return tuple(out)


def label(kind: str, face: int) -> str:
    """What kite `face` reads on a tens die (00-90) or a units die (0-9)."""
    digit = numbering()[face]
    return f"{digit}0" if kind == "tens" else str(digit)


@lru_cache(maxsize=1)
def lying() -> float:
    """How high a die's centre is when it lies on a kite."""
    verts, faces, normals = d10()
    return float(normals[0] @ verts[faces[0][0]])


# -- rotations -------------------------------------------------------------------------------
SKEW = ([0, 0, 1, 1, 2, 2], [1, 2, 0, 2, 0, 1])


def turns(v: np.ndarray) -> np.ndarray:
    """Rotation matrices for rotation vectors (n, 3): axis times angle."""
    angle = np.sqrt((v * v).sum(axis=1))
    a = v / np.maximum(angle, 1e-12)[:, None]
    k = np.zeros((len(v), 3, 3))
    k[:, SKEW[0], SKEW[1]] = np.stack([-a[:, 2], a[:, 1], a[:, 2], -a[:, 0], -a[:, 1], a[:, 0]], axis=1)
    return np.eye(3) + np.sin(angle)[:, None, None] * k + (1 - np.cos(angle))[:, None, None] * (k @ k)


def quaternions(r: np.ndarray) -> np.ndarray:
    """Unit quaternions (w, x, y, z) for rotation matrices (n, 3, 3)."""
    m = r
    t = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    cands = np.stack([
        np.stack([1 + t, m[:, 2, 1] - m[:, 1, 2], m[:, 0, 2] - m[:, 2, 0], m[:, 1, 0] - m[:, 0, 1]], axis=1),
        np.stack([m[:, 2, 1] - m[:, 1, 2], 1 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2], m[:, 0, 1] + m[:, 1, 0], m[:, 0, 2] + m[:, 2, 0]], axis=1),
        np.stack([m[:, 0, 2] - m[:, 2, 0], m[:, 0, 1] + m[:, 1, 0], 1 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2], m[:, 1, 2] + m[:, 2, 1]], axis=1),
        np.stack([m[:, 1, 0] - m[:, 0, 1], m[:, 0, 2] + m[:, 2, 0], m[:, 1, 2] + m[:, 2, 1], 1 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2]], axis=1),
    ], axis=1)  # four ways to the same quaternion; the one with the biggest pivot is exact
    pick = np.argmax(np.stack([t, m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]], axis=1), axis=1)
    q = cands[np.arange(len(m)), pick]
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    return q * np.where(q[:, :1] < 0, -1.0, 1.0)


def matrices(q: np.ndarray) -> np.ndarray:
    """Rotation matrices for unit quaternions (n, 4), (w, x, y, z)."""
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=1),
    ], axis=1)


def cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.empty_like(a)
    out[:, 0] = a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1]
    out[:, 1] = a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2]
    out[:, 2] = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    return out


# -- the world -------------------------------------------------------------------------------
@dataclass
class World:
    """Dice in flight, in any number of trays at once: those in one tray knock each other."""

    x: np.ndarray  # centres (n, 3)
    v: np.ndarray
    r: np.ndarray  # turns, the die's space to the world's (n, 3, 3)
    w: np.ndarray  # angular velocities, world
    tray: np.ndarray  # each die's tray: half width and half depth (n, 2)
    pairs: tuple[np.ndarray, np.ndarray]  # dice that share a tray
    rng: np.random.Generator
    aim: np.ndarray = field(default=None)  # type: ignore[assignment]  # how far from upright each die's number settles, radians
    still: np.ndarray = field(default=None)  # type: ignore[assignment]  # seconds lain still, face up
    asleep: np.ndarray = field(default=None)  # type: ignore[assignment]
    time: float = 0.0
    nudges: int = 0

    def __post_init__(self) -> None:
        n = len(self.x)
        self.aim = np.zeros(n) if self.aim is None else self.aim
        self.still = np.zeros(n) if self.still is None else self.still
        self.asleep = np.zeros(n, dtype=bool) if self.asleep is None else self.asleep


def launch(rng: np.random.Generator, count: int, trays: int = 1) -> World:
    """`trays` throws of `count` dice each: every die at a uniformly random turn (a random unit
    quaternion), somewhere high over its tray, flung across it and spinning."""
    n = count * trays
    half = np.array(TRAYS[min(count, max(TRAYS))])
    q = rng.normal(size=(n, 4))
    slot = np.tile(np.arange(count), trays)
    across = (slot - (count - 1) / 2) * min(1.8, 2 * (half[0] - 0.9) / max(1, count - 1))  # spread out, so they start apart
    x = np.stack([across + rng.uniform(-0.25, 0.25, n), rng.uniform(-0.3, 0.3, n) * half[1], rng.uniform(2.6, 3.4, n)], axis=1)
    heading = rng.uniform(0, 2 * math.pi, n)
    speed = rng.uniform(1.5, 3.5, n)
    v = np.stack([speed * np.cos(heading), speed * np.sin(heading), rng.uniform(-3.0, -0.5, n)], axis=1)
    w = rng.normal(size=(n, 3))
    w *= (rng.uniform(12, 24, n) / np.linalg.norm(w, axis=1))[:, None]
    group = np.repeat(np.arange(trays), count)
    i, j = np.triu_indices(n, 1)
    keep = group[i] == group[j]
    return World(x, v, matrices(q), w, np.tile(half, (n, 1)), (i[keep], j[keep]), rng, rng.uniform(-0.08, 0.08, n))


SURFACES = np.array([(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0)])  # inward
SURFACE_BOUNCE = (BOUNCE[0], *[BOUNCE[1]] * 4)  # the floor, then the walls at -x, +x, -y, +y


def advance(s: World, dt: float = STEP) -> None:
    """One step: gravity and motion for the dice still moving, then the knocks, the floor, the
    walls, and whether each die has come to rest. A throw's few dice are stepped in place, all of
    them (one at rest does not move); a big batch only its moving dice."""
    moving = ~s.asleep
    if not moving.any():
        return
    small = len(s.x) <= 16
    live = np.arange(len(s.x)) if small else np.flatnonzero(moving)
    if small:
        x, v, w, half, aim = s.x, s.v, s.w, s.tray, s.aim
        v[:, 2] -= GRAVITY * dt * moving
    else:
        x, v, w, half, aim = s.x[live], s.v[live], s.w[live], s.tray[live], s.aim[live]
        v[:, 2] -= GRAVITY * dt
    x += v * dt
    w *= math.exp(-SPIN_DAMPING * dt)
    r = turns(w * dt) @ (s.r if small else s.r[live])
    if small:
        s.r = r
    else:
        s.x[live], s.v[live], s.r[live], s.w[live] = x, v, r, w
    if len(s.pairs[0]):
        knock(s)
        if not small:
            x, v, w = s.x[live], s.v[live], s.w[live]
    p = x[:, None, :] + np.einsum("vj,nij->nvi", d10()[0], r)  # the corners in the world
    depth = p @ SURFACES.T  # (dice, corners, surfaces): below zero is inside the floor or a wall
    depth[..., 1:3] += half[:, None, :1]
    depth[..., 3:5] += half[:, None, 1:]
    into = depth < 0
    hit = into.any(axis=1)
    floor = hit[:, 0].copy()
    level = (r[:, 2, :] @ d10()[2].T).max(axis=1) > FLAT  # lying on a kite
    for k in np.flatnonzero(hit.any(axis=0)):
        c = np.flatnonzero(hit[:, k])
        n = SURFACES[k]
        inside = into[c, :, k]
        point = (p[c] * inside[..., None]).sum(axis=1) / inside.sum(axis=1)[:, None]  # where it touches: the middle of the corners in
        if k == 0:  # a die lying on a kite stands on all of it: held up from right under its centre, it does not rock
            point = np.where(level[c][:, None], x[c] * np.array([1.0, 1.0, 0.0]), point)
        arm = point - x[c]
        vc = v[c] + cross(w[c], arm)
        vn = vc @ n
        an = cross(arm, np.broadcast_to(n, arm.shape))
        jn = np.where(vn < 0, -(1 + np.where(vn < -SLOW, SURFACE_BOUNCE[k], 0.0)) * vn / (1 + (an * an).sum(axis=1) / INERTIA), 0.0)
        slide = vc - vn[:, None] * n
        speed = np.sqrt((slide * slide).sum(axis=1))
        along = slide / np.maximum(speed, 1e-9)[:, None]
        at = cross(arm, along)
        jt = np.minimum(FRICTION * jn, speed / (1 + (at * at).sum(axis=1) / INERTIA))
        impulse = jn[:, None] * n - jt[:, None] * along
        v[c] += impulse
        w[c] += cross(arm, impulse) / INERTIA
        x[c] -= depth[c, :, k].min(axis=1)[:, None] * n  # out of the surface
    heights = r[:, 2, :] @ d10()[2].T  # how level each kite lies (its normal's height)
    face = heights.argmax(axis=1)
    top = heights[np.arange(len(face)), face]
    way = np.einsum("nij,nj->ni", r[:, :2, :], apexes()[face])  # the top kite's way up, on the floor
    off = (np.arctan2(way[:, 1], way[:, 0]) - math.pi / 2 - aim + math.pi) % (2 * math.pi) - math.pi  # from upright
    flat = floor & (top > FLAT)
    if floor.any():
        f = np.flatnonzero(floor)
        lying = flat[f][:, None]  # on a kite: the felt holds it, but for the turn about the upright
        w[f, :2] *= np.where(lying, math.exp(-REST_DAMPING * dt), math.exp(-ROLL_DAMPING * dt))
        w[f, 2:] *= np.where(lying, 1.0, math.exp(-ROLL_DAMPING * dt))
        v[f, :2] *= np.where(lying, math.exp(-REST_DAMPING * dt), math.exp(-SLIDE_DAMPING * dt))
        turning = np.flatnonzero(floor & (top > SETTLING) & moving[live])
        if len(turning):  # lying on a kite, a die turns where it lies until its number faces the viewer
            stiff, damp, most, fastest = TURN
            w[turning, 2] = np.clip(w[turning, 2] + np.clip(-stiff * off[turning] - damp * w[turning, 2], -most, most) * dt, -fastest, fastest)
    if small:
        v[~moving] = 0.0  # a die at rest stays put until something knocks it
        w[~moving] = 0.0
    else:
        s.x[live], s.v[live], s.w[live] = x, v, w
    if round(s.time / dt) % 16 == 0:
        s.r[live] = orthonormal(s.r[live])
    # flat on a kite, upright, and barely moving (what is left is the contacts' jitter) for a moment: at rest
    calm = flat & (np.abs(off) < UPRIGHT) & ((v * v).sum(axis=1) < 0.25) & ((w * w).sum(axis=1) < 2.25)
    if small:
        calm &= moving
    s.still[live] = np.where(calm, s.still[live] + dt, 0.0)
    settle(s, live[calm & (s.still[live] >= 0.12)])
    s.time += dt


def orthonormal(r: np.ndarray) -> np.ndarray:
    u, _, vt = np.linalg.svd(r)
    return u @ vt


def knock(s: World) -> None:
    """Dice in one tray meeting, as balls: an impulse along the line between their centres. A
    die at rest that is hit wakes up and moves."""
    i, j = s.pairs
    if not len(i):
        return
    gap = s.x[j] - s.x[i]
    dist = np.linalg.norm(gap, axis=1)
    close = (dist < 2 * KNOCK) & (dist > 1e-9)
    if not close.any():
        return
    i, j, gap, dist = i[close], j[close], gap[close], dist[close]
    n = gap / dist[:, None]
    vn = ((s.v[j] - s.v[i]) * n).sum(axis=1)
    impulse = np.where(vn < 0, -(1 + BOUNCE[1]) * vn / 2, 0.0)[:, None] * n
    push = ((2 * KNOCK - dist) / 2)[:, None] * n
    np.add.at(s.v, i, -impulse)
    np.add.at(s.v, j, impulse)
    np.add.at(s.x, i, -push)
    np.add.at(s.x, j, push)
    both = np.concatenate([i, j])
    s.w[both] *= 0.92  # a knock scuffs some spin off
    s.asleep[both] = False
    s.still[both] = 0.0


def settle(s: World, dice: np.ndarray) -> None:
    """Lay these dice to rest: the top kite exactly level, the die exactly on the floor, still."""
    if not len(dice):
        return
    normals = d10()[2]
    for d in dice:
        n = s.r[d] @ normals[int(np.argmax((s.r[d] @ normals.T)[2]))]
        axis = np.cross(n, (0.0, 0.0, 1.0))
        angle = math.atan2(float(np.linalg.norm(axis)), float(n[2]))
        if angle > 1e-12:
            s.r[d] = turns((axis / np.linalg.norm(axis) * angle)[None])[0] @ s.r[d]
        s.x[d, 2] = lying()
    s.v[dice] = 0.0
    s.w[dice] = 0.0
    s.asleep[dice] = True


def nudge(s: World) -> None:
    """A die that will not settle (on an edge, wedged against a wall) gets a small random push."""
    live = np.flatnonzero(~s.asleep)
    if not len(live):
        return
    s.nudges += len(live)
    s.w[live] += s.rng.normal(size=(len(live), 3)) * 3.0
    s.v[live] += np.array([0.0, 0.0, 2.0]) + s.rng.normal(size=(len(live), 3)) * np.array([0.6, 0.6, 0.0])


def tops(r: np.ndarray) -> np.ndarray:
    """The kite turned up on each die."""
    return np.argmax((r @ d10()[2].T)[:, 2], axis=1)


def run(s: World, fps: float = FPS, record: bool = True) -> list[np.ndarray]:
    """Run the world until every die rests; with `record`, the poses (centre and quaternion,
    (n, 7)) at fps frames a second, the last being the poses they came to rest in."""
    frames = []
    every = max(1, round(1 / (fps * STEP)))
    steps = 0
    nudged = NUDGE_AFTER
    while not s.asleep.all():
        if record and steps % every == 0:
            frames.append(np.concatenate([s.x, quaternions(s.r)], axis=1))
        advance(s)
        steps += 1
        if s.time >= nudged:
            nudge(s)
            nudged += NUDGE_EVERY
        if s.time >= GIVE_UP:  # hardly ever: lay what is left on its most level kite
            settle(s, np.flatnonzero(~s.asleep))
    if record:
        frames.append(np.concatenate([s.x, quaternions(s.r)], axis=1))
    return frames


# -- a throw ---------------------------------------------------------------------------------
@dataclass
class Throw:
    kinds: list[str]  # per die: "tens" or "units"
    faces: list[int]  # the kite each came to rest with up
    tray: tuple[float, float]
    frames: list[np.ndarray]  # (n, 7) poses at FPS frames a second
    seconds: float = 0.0  # simulated
    nudges: int = 0
    ms: float = 0.0  # what it took

    @property
    def labels(self) -> list[str]:
        return [label(k, f) for k, f in zip(self.kinds, self.faces, strict=True)]

    def encode(self, fps: float = 20.0, places: int = 3) -> dict[str, Any]:
        """For the event: frames thinned to `fps`, always ending on the resting poses, numbers
        rounded. Every screen replays this; none simulates it again."""
        step = max(1, round(FPS / fps))
        keep = [*range(0, len(self.frames) - 1, step), len(self.frames) - 1]
        poses = [[round(float(c), places) for k in keep for c in self.frames[k][d]] for d in range(len(self.kinds))]
        return {"fps": FPS / step, "tray": [round(c, 3) for c in self.tray], "kinds": self.kinds, "faces": self.faces,
                "labels": self.labels, "poses": poses}


def throw(seed: int, tens: int = 1, units: bool = True) -> Throw:
    """Throw `tens` tens dice and (if `units`) a units die together in one tray, as CoC's bonus
    and penalty dice are; the units die is the last."""
    started = time.perf_counter()
    kinds = ["tens"] * tens + (["units"] if units else [])
    s = launch(np.random.default_rng(seed), len(kinds))
    frames = run(s)
    out = Throw(kinds, [int(f) for f in tops(s.r)], tuple(float(c) for c in s.tray[0]), frames, s.time, s.nudges)
    out.ms = (time.perf_counter() - started) * 1000
    return out


def faces_up(seed: int, dice: int) -> np.ndarray:
    """`dice` dice thrown one to a tray, all at once, unrecorded: the kites they came to rest
    with up. The fairness test's batch."""
    s = launch(np.random.default_rng(seed), 1, trays=dice)
    run(s, record=False)
    return tops(s.r)


def decode(event: dict) -> tuple[np.ndarray, np.ndarray]:
    """An event's throw back into centres (frames, dice, 3) and turns (frames, dice, 3, 3)."""
    poses = np.array(event["poses"], dtype=float)  # (dice, frames * 7)
    dice = len(poses)
    poses = poses.reshape(dice, -1, 7).transpose(1, 0, 2)
    return poses[..., :3], matrices(poses.reshape(-1, 7)[:, 3:]).reshape(len(poses), dice, 3, 3)


# -- the check -------------------------------------------------------------------------------
LABELS = {Level.CRITICAL: "大成功", Level.EXTREME: "極限成功", Level.HARD: "困難成功", Level.REGULAR: "一般成功",
          Level.FAILURE: "失敗", Level.FUMBLE: "大失敗"}  # as BCDice's Traditional Chinese CoC system writes them


def percentile(tens: int, units: int) -> int:
    """A tens die (0, 10 ... 90) and a units die (0-9) read as a d100: 00 and 0 is 100."""
    return 100 if tens == 0 and units == 0 else tens + units


def kept(candidates: list[int], bonus: int) -> int:
    """Which d100 counts: bonus dice keep the lowest, penalty dice the highest."""
    return min(candidates) if bonus > 0 else max(candidates) if bonus < 0 else candidates[0]


def judge(value: int, target: int) -> Level:
    """CoC 7th's levels, as BCDice judges them: 1 is a critical; 100 a fumble, and 96-100 when
    the target is under 50; then a fifth, a half, the target."""
    if value == 1:
        return Level.CRITICAL
    if value == 100 or (target < 50 and value >= 96):
        return Level.FUMBLE
    if value <= target // 5:
        return Level.EXTREME
    if value <= target // 2:
        return Level.HARD
    return Level.REGULAR if value <= target else Level.FAILURE


class PhysDice:
    """The dice for Rules: percentile checks are thrown for real (the event carries the throw);
    other rolls and the tables go to BCDice. If a throw fails for any reason the check falls back
    to BCDice's own dice, without a recording, and the turn goes on."""

    def __init__(self, inner: BCDice | None = None, seed: int | None = None):
        self._inner = inner
        self.seeds = random.Random(seed) if seed is not None else random.SystemRandom()
        self.last: Throw | None = None

    @property
    def inner(self) -> BCDice:
        if self._inner is None:
            self._inner = shared()
        return self._inner

    def check(self, target: int, bonus: int = 0) -> Roll:
        target, bonus = max(1, int(target)), max(-2, min(2, int(bonus)))
        try:
            t = throw(self.seeds.getrandbits(63), tens=1 + abs(bonus))
        except Exception:  # a broken throw must never break a turn: plain dice, and a line in the log
            log.warning("the physical dice failed; BCDice rolls this check", exc_info=True)
            self.last = None
            return self.inner.check(target, bonus)
        self.last = t
        units = int(t.labels[-1])
        tens = [int(x) for x in t.labels[:-1]]
        candidates = [percentile(x, units) for x in tens]
        value = kept(candidates, bonus)
        level = judge(value, target)
        text = f"(1D100<={target}) 獎勵・懲罰骰[{bonus}] ＞ {', '.join(map(str, candidates))} ＞ {value} ＞ {LABELS[level]}"
        rands = [[x // 10 or 10, 10] for x in tens] + [[units or 10, 10]]
        return Roll(f"CC({bonus:+d})<={target}" if bonus else f"CC<={target}", text, success=level.ok, failure=not level.ok,
                    critical=level == Level.CRITICAL, fumble=level == Level.FUMBLE, rands=rands, throw=t.encode())

    def roll(self, command: str, system: str | None = None) -> Roll:
        return self.inner.roll(command, system)

    def total(self, expr: str) -> int:
        return self.inner.total(expr)

    def table(self, name: str) -> str:
        return self.inner.table(name)

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()


_shared: PhysDice | None = None


def physical() -> PhysDice:
    """The table's dice, shared like BCDice's process."""
    global _shared
    if _shared is None:
        _shared = PhysDice()
    return _shared
