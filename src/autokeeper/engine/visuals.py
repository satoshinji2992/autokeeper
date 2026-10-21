"""Illustrate improvised people, places and props without holding up a turn, and redraw the
places and people the story visibly changes (a fire, a flood, a wound, a death).

A change comes from the director's plan (Improv.looks) or from the module itself, when the
flag of one of its Location.looks / NPC.looks is set. The picture it shows now goes to Codex
with the change (imagegen.py); until the new one is drawn the old one stays. A place's change
that a foreshadowing stage brings (a look on its flag, Stage.flag) is drawn ahead instead, once
the sign before it was handed (ahead): kept under the look's own key, shown the moment its flag
is set. GameState.looks remembers what each art key shows, so a loaded game shows it again; the
module never changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any, NamedTuple

from .. import artkit, config, imagegen, imagery, paths, pictures
from ..module.schema import Module
from .state import GameState

FALLBACKS = {
    "portrait": "",  # no generic stand-ins: wait for an illustration of the actual subject
    "scene": "",
    "item": "",
}

class Target(NamedTuple):
    entity: Any  # the Location or NPC
    field: str  # where its art key lives: "art" or "portrait"
    key: str
    kind: str  # imagegen kind: scene or portrait
    subject: str
    ref: str  # "location:<id>" or "npc:<id>", as GameState.looks keeps it


class Visuals:
    def __init__(self, module: Module, state: GameState, cfg: config.Config | None, emit: Callable[[dict], Awaitable[None]]):
        self.module, self.state, self.cfg, self.emit = module, state, cfg, emit
        self.tasks: dict[str, asyncio.Task] = {}
        self.fill_gaps = True  # the module's own gaps looked for in the background (off where nobody sees pictures)
        self.limit = asyncio.Semaphore(2)
        self.gap_limit = asyncio.Semaphore(1)
        self.idle = asyncio.Event()
        self.idle.set()
        digest = hashlib.sha256(f"{module.id}/{state.slot}".encode()).hexdigest()[:24]
        self.folder = paths.game_art(digest)
        for key, record in state.visuals.items():
            self._attach(key, record)
        self._recover()
        for key in state.looks:
            self._show(key)

    def _attach(self, key: str, record: dict[str, str]) -> None:
        self.module.art[key] = record["art"]
        self.module.art_colors[key] = record["mask"]
        if record.get("picture") and Path(record["picture"]).is_file():
            self.module.pictures[key] = record["picture"]
        self._show(key)  # a place or a person the story changed keeps its new picture in front

    def schedule(self, keys: set[str] | None = None) -> None:
        targets = [(x.art, x.read_aloud, "scene") for x in self.module.locations]
        targets += [(n.portrait, f"{n.name}，{n.look or '衣着朴素的当地成年人'}", "portrait") for n in self.module.npcs]
        targets += [(i.art, f"{i.name}，{i.description}", "item") for i in self.module.items]
        for key, subject, kind in targets:
            if keys is not None and key not in keys:
                continue
            if not key or not re.fullmatch(r"improv_(?:scene|portrait|item)_[0-9a-f]{16}", key):
                continue
            if key in self.tasks:
                if not self.tasks[key].done():
                    continue
                del self.tasks[key]
            if key not in self.state.visuals:
                art = FALLBACKS[kind]
                self.state.visuals[key] = {"art": art, "mask": artkit.auto_mask(art), "status": "pending", "brief": subject}
                self._attach(key, self.state.visuals[key])
            # Artwork can finish during a turn. Its own cache survives without saving a
            # half-resolved game state over the last completed turn.
            try:
                cached = json.loads((self.folder / f"{key}.json").read_text(encoding="utf-8"))
                valid = (isinstance(cached, dict) and cached.get("brief") == subject
                         and all(isinstance(value, str) for value in cached.values()))
                photo_only = bool(cached.get("picture")) and not cached.get("art", "").strip() if valid else False
                if (
                    valid and cached.get("status") in ("ready", "failed") and (cached.get("art", "").strip() or photo_only)
                    and int(cached.get("attempts", "0")) >= int(self.state.visuals[key].get("attempts", "0"))
                    and (photo_only or not artkit.check(key, cached["art"], cached["mask"], portrait=kind == "portrait"))
                ):
                    self.state.visuals[key] = cached
                    self._attach(key, cached)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            record = self.state.visuals[key]
            retry = record["status"] == "failed" and int(record.get("attempts", "0")) < 2
            photo = (record["status"] == "ready" and record.get("photo_status") == "pending"
                     and int(record.get("photo_attempts", "0")) < 2)
            if self.cfg is not None and (record["status"] == "pending" or retry or photo):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    continue
                self.tasks[key] = asyncio.create_task(self._make(key, subject, kind), name=f"illustrate:{key}")
        if keys is None:
            self._looks()
            self.gaps()

    async def prepare(self, keys: set[str]) -> list[str]:
        """Finish first illustrations before exposing a new scene. Redraws keep their old art.
        Returns the ones still missing that another attempt could draw: once the game's attempts
        or picture budget are spent, a scene goes on without its picture instead of waiting."""
        if self.cfg is None or not keys or self.cfg.visual_limit == 0 or self.cfg.visual_budget_usd == 0:
            return []
        paused = not self.idle.is_set()
        self.idle.set()
        try:
            self.schedule(keys)
            await asyncio.gather(*(task for key, task in self.tasks.items() if key in keys))
        finally:
            if paused:
                self.idle.clear()
        missing = [key for key in keys if not self.module.art.get(key) and not self.module.pictures.get(key)]
        spent = sum(v.get("cost_usd", 0) for k, v in self.state.usage.items() if k.startswith("visual:"))
        attempts = sum(int(r.get("attempts", "0")) for r in self.state.visuals.values())
        if attempts >= self.cfg.visual_limit or spent >= self.cfg.visual_budget_usd:
            return []
        return missing

    async def _make(self, key: str, subject: str, kind: str) -> None:
        journal = getattr(self, "debug_journal", None)
        context = journal.task(f"illustrate:{key}", turn=self.state.turn,
                               details={"subject": subject, "kind": kind}) if journal else nullcontext()
        with context:
            await self._make_inner(key, subject, kind)

    async def _make_inner(self, key: str, subject: str, kind: str) -> None:
        assert self.cfg is not None
        record = self.state.visuals[key]
        try:
            async with self.limit:
                await self.idle.wait()
                spent = sum(v.get("cost_usd", 0) for k, v in self.state.usage.items() if k.startswith("visual:"))
                attempts = sum(int(r.get("attempts", "0")) for r in self.state.visuals.values())
                if ((record["status"] != "ready" and not record.get("phase") and attempts >= self.cfg.visual_limit)
                        or self.cfg.visual_limit == 0 or spent >= self.cfg.visual_budget_usd):
                    await self._without(key, record)
                    return  # foreground narration owns the remaining budget
                if record["status"] != "ready" and not record.get("phase"):
                    record["attempts"] = str(int(record.get("attempts", "0")) + 1)
                record["phase"] = "picture"
                if not record.get("picture") and self.cfg.search != "none" and int(record.get("photo_attempts", "0")) < 2:
                    record["photo_status"] = "pending"
                    record["photo_attempts"] = str(int(record.get("photo_attempts", "0")) + 1)
                    try:
                        async with asyncio.timeout(90):
                            await self._photo(key, record, subject, kind)
                    except Exception as exc:
                        record["photo_error"] = type(exc).__name__
                await self.idle.wait()
                if not record.get("picture"):
                    spent = sum(v.get("cost_usd", 0) for k, v in self.state.usage.items() if k.startswith("visual:"))
                    if spent >= self.cfg.visual_budget_usd:
                        return
                    await self._drawn(key, record, subject, kind)
                if record.get("picture"):
                    record.update(status="ready", photo_status="done")
                    record.pop("phase", None)
                    record.pop("error", None)
                    await self._cache(key)
                    return
                # Neither a photo nor image generation supplied one: no picture, never one painted by
                # program or hand-drawn ASCII (the user, 2026-10-21).
                await self._without(key, record)
        except asyncio.CancelledError:
            raise  # still pending: resume the illustration when the save is loaded
        except Exception as exc:
            # Keep the illustration already on screen, and make the failure inspectable
            # in the save instead of breaking play or leaking an unobserved task error.
            record.update(status="ready" if record["status"] == "ready" else "failed", error=type(exc).__name__)
            record.pop("phase", None)
            await self._cache(key)

    async def _without(self, key: str, record: dict[str, str]) -> None:
        """Done without a picture: neither a photo nor a drawing could be had (offline, no Codex, the
        budget spent). The place, the person or the thing is told in words."""
        if record.get("picture"):
            return
        record.update(status="ready", photo_status="done")
        record.pop("phase", None)
        record.pop("error", None)
        self._attach(key, record)
        await self._publish(key)

    async def _photo(self, key, record, subject, kind) -> None:
        await imagery.fetch_folder(
            self.cfg, self.folder, [(key, f"{self.module.era}，{subject}", kind)], log=lambda _: None,
            on_reply=lambda role, reply: self.state.record_usage(f"visual:{role}", reply),
        )
        picture = self.folder / f"{key}.jpg"
        if picture.is_file():
            record["picture"] = str(picture)
            self._attach(key, record)
            await self._publish(key)

    def pause(self) -> None:
        self.idle.clear()

    def resume(self) -> None:
        self.idle.set()
        self.schedule()

    async def _publish(self, key: str) -> None:
        if self.cfg is not None:
            self._attach(key, self.state.visuals[key])
        await self._cache(key)
        event = {"type": "art", "key": key, **self.state.visuals[key]}
        if key in self.state.looks and self.module.pictures.get(key):  # changed by the story since: that picture stays
            event["picture"] = self.module.pictures[key]
        await self.emit(event)

    async def _cache(self, key: str) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{key}.json").write_text(json.dumps(self.state.visuals[key], ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # a cache failure must not hide an illustration that is already ready

    async def close(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    # -- the module's own places, people, things and creatures with no picture -------------------
    def gaps(self) -> None:
        """What the module has no picture of its own for (pictures.gaps), looked for in the
        background once for all its games: a photo, else a drawing by Codex (pictures.fill). The
        painted ones show until then, and wherever neither is to be had."""
        cfg = self.cfg
        if (not self.fill_gaps or cfg is None or "gaps" in self.tasks or cfg.visual_limit == 0 or cfg.visual_budget_usd == 0
                or not pictures.gaps(self.module)):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # built off the loop (a screen's thread): the next schedule() starts it
            return
        self.tasks["gaps"] = asyncio.create_task(self._gaps(), name="gaps")

    async def _gaps(self) -> None:
        async def landed(key: str, path: Path) -> None:
            await self.emit({"type": "art", "key": key, "art": self.module.art.get(key, ""), "status": "ready",
                             "mask": self.module.art_colors.get(key, ""), "picture": str(path)})

        cfg = self.cfg
        assert cfg is not None

        def affordable() -> bool:
            spent = sum(v.get("cost_usd", 0) for k, v in self.state.usage.items() if k.startswith("visual:"))
            return spent < cfg.visual_budget_usd

        # One at a time, in a slot of its own: a drawing takes up to five minutes, and the pictures a
        # scene is waiting for (schedule) must not queue behind it.
        await pictures.fill(cfg, self.module, log=lambda _: None, where="game", limit=self.gap_limit, ready=self.idle.wait,
                            budget=affordable, on_search=lambda role, reply: self.state.record_usage(f"visual:{role}", reply),
                            on_draw=self._usage, done=landed)

    # -- pictures from Codex: what no photo was found for, and what the story changed ----------
    def made(self) -> int:
        """Pictures this game has had from Codex (imagegen.PER_GAME is the most it gets)."""
        return (sum(int(r.get("made", "0")) for r in self.state.looks.values())
                + sum(1 for r in self.state.visuals.values() if r.get("generated")))

    def _may_make(self) -> bool:
        return imagegen.enabled(self.cfg) and self.made() < imagegen.PER_GAME and imagegen.left(self.module.id) > 0

    def _usage(self, reply) -> None:
        self.state.record_usage("visual:imagegen", reply)

    async def _drawn(self, key: str, record: dict[str, str], subject: str, kind: str) -> None:
        """No photo fits an improvised place, face or prop: Codex draws one from its words, when
        it is here and the game has pictures left. Outside the photo search's 90 seconds."""
        if not self._may_make():
            return
        world = {"era": self.module.era, "setting": self.module.setting}
        try:
            picture = await imagegen.make(imagegen.prompt(subject, kind, **world), self.folder / f"{key}.jpg", kind=kind,
                                          module=self.module.id, title=subject[:80], on_reply=self._usage)
        except (imagegen.Unavailable, imagegen.OverBudget, imagegen.Failed, OSError, ValueError):
            return  # the drawing stays
        record.update(picture=str(picture), generated="1")
        self._attach(key, record)
        await self._publish(key)

    def _target(self, target: str) -> Target | None:
        """The place or the person "location:<id>", "npc:<id>" or a bare id names (a place
        first), with what a picture of it is made from: what players see, never a role or a
        secret. None if it names nobody."""
        side, _, ident = target.rpartition(":")
        loc = next((x for x in self.module.locations if x.id == ident), None) if side in ("", "location") else None
        if loc is not None:
            key = loc.art or f"look_scene_{hashlib.sha256(ident.encode()).hexdigest()[:16]}"
            return Target(loc, "art", key, "scene", f"{loc.name}：{loc.read_aloud.strip()[:160]}", f"location:{ident}")
        npc = next((n for n in self.module.npcs if n.id == ident), None) if side in ("", "npc") else None
        if npc is not None:
            key = npc.portrait or f"look_portrait_{hashlib.sha256(ident.encode()).hexdigest()[:16]}"
            return Target(npc, "portrait", key, "portrait", f"{npc.name}，{npc.look or '衣着朴素的当地成年人'}", f"npc:{ident}")
        return None

    def look(self, target: str, change: str) -> str | None:
        """A place or a person the story has visibly changed: from now on it shows a picture made
        from the one it shows now, with the change, once Codex has drawn it. Its art key, or
        None for an id that names nobody."""
        found = self._target(target)
        change = change.strip()
        if found is None or not change:
            return None
        record = self.state.looks.get(found.key)
        task = self.tasks.get(f"look:{found.key}")
        if record is None:
            record = self.state.looks[found.key] = {"target": found.ref, "kind": found.kind, "change": change,
                                                    "status": "pending", "picture": ""}
        elif task is not None and not task.done():  # being drawn: this one goes on top of it
            record["later"] = "；".join(x for x in (record.get("later", ""), change) if x)
        elif record.get("status") == "pending":
            record["change"] = "；".join(x for x in (record.get("change", ""), change) if x)
        else:
            record.update(change=change, status="pending", attempts="0")
            record.pop("error", None)
        record["turn"] = str(self.state.turn)
        return found.key

    def _looks(self) -> None:
        """The module's own changes once their flag is set (drawn at import when Codex could, or
        ahead of the stage that brings them, so shown at once; one still being drawn ahead shows
        when it is done), then the changes still to draw, while Codex and the budget allow, and
        the pictures to draw ahead that a closed game left waiting."""
        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False
        changing = [(f"location:{x.id}", x.art, x.looks) for x in self.module.locations]
        changing += [(f"npc:{n.id}", n.portrait, n.looks) for n in self.module.npcs]
        for ref, base, looks in changing:
            for look in looks:
                if not base or not look.when.startswith("flag:") or look.when[5:] not in self.state.flags:
                    continue
                if look.when in self.state.looks.get(base, {}).get("applied", "").split(","):
                    continue
                if self._drawing_ahead(look.key(base)):
                    continue  # the old picture stays: this one shows as soon as it is done (_ahead)
                early = self.state.looks.get(look.key(base), {})
                if early.get("when") and early.get("status") == "pending":
                    del self.state.looks[look.key(base)]  # never drawn ahead (the game closed first): drawn as usual
                drawn = self.module.pictures.get(look.key(base), "")
                task = self.tasks.get(f"look:{base}")
                if drawn and Path(drawn).is_file() and (task is None or task.done()):
                    record = self.state.looks.setdefault(base, {"target": ref, "kind": "scene" if ref.startswith("location:") else "portrait"})
                    record.update(change=look.change, status="ready", picture=drawn)
                    self._show(base)
                    if running:
                        self.tasks[f"shown:{base}"] = asyncio.create_task(self._publish_look(base), name=f"shown:{base}")
                elif self.look(ref, look.change) is None:
                    continue
                record = self.state.looks[base]
                record["applied"] = ",".join(x for x in (record.get("applied", ""), look.when) if x)
        due = [key for key, record in self.state.looks.items() if not record.get("when") and (
               record.get("status") == "pending" or (record.get("status") == "failed" and int(record.get("attempts", "0")) < 2))]
        due = [key for key in due if (task := self.tasks.get(f"look:{key}")) is None or task.done()]
        if not running or not self._may_make():
            return
        for key in due:
            self.tasks[f"look:{key}"] = asyncio.create_task(self._vary(key), name=f"look:{key}")
        for key, record in list(self.state.looks.items()):  # to draw ahead, and the game was closed before it was
            if (record.get("when") and record.get("status") == "pending" and record["when"][5:] not in self.state.flags
                    and not self._drawing_ahead(key)):
                self.tasks[f"ahead:{key}"] = asyncio.create_task(self._ahead(key), name=f"ahead:{key}")

    async def _vary(self, key: str) -> None:
        journal = getattr(self, "debug_journal", None)
        context = journal.task(f"redraw:{key}", turn=self.state.turn) if journal else nullcontext()
        with context:
            await self._vary_inner(key)

    async def _vary_inner(self, key: str) -> None:
        record = self.state.looks[key]
        try:
            async with self.limit:
                await self.idle.wait()
                if not self._may_make():
                    return  # still pending: drawn in a later session that has Codex and budget
                record["attempts"] = str(int(record.get("attempts", "0")) + 1)
                made = int(record.get("made", "0")) + 1
                picture = await self._redraw(record, self.module.pictures.get(key, ""), self.folder / f"{key}.look{made}.jpg")
                record.update(picture=str(picture), made=str(made))
                record.pop("error", None)
                if record.get("later"):  # changed again while this one was drawn: that goes on top next
                    record.update(change=record.pop("later"), status="pending", attempts="0")
                else:
                    record["status"] = "ready"
                self._show(key)
                await self._publish_look(key)
        except asyncio.CancelledError:
            raise  # still pending: drawn again when the game is loaded
        except Exception as exc:  # the old picture stays; the failure is kept in the save
            record.update(status="failed", error=type(exc).__name__)
            if record.get("later"):
                record["change"] = "；".join(x for x in (record.get("change", ""), record.pop("later")) if x)
            self._keep(key)
            return
        if record.get("status") == "pending" and self._may_make():  # the change that came in meanwhile
            self.tasks[f"look:{key}"] = asyncio.create_task(self._vary(key), name=f"look:{key}")

    async def _redraw(self, record: dict[str, str], shown: str, target: Path) -> Path:
        """The picture `shown` (a place's or a person's; none: from words) after the record's change,
        drawn by Codex into `target`."""
        found = self._target(record.get("target", ""))
        subject = found.subject if found else record.get("target", "")
        kind, change = record.get("kind", "scene"), record.get("change", "")
        original = Path(shown) if shown and Path(shown).is_file() else None
        world = {"era": self.module.era, "setting": self.module.setting}
        text = (imagegen.prompt(subject, kind, change=change, **world) if original is not None
                else imagegen.prompt(f"{subject}。现在：{change}", kind, **world))  # nothing to start from: from words
        return await imagegen.make(text, target, kind=kind, module=self.module.id, original=original,
                                   title=f"{subject[:40]}（{change[:40]}）", on_reply=self._usage)

    # -- drawn ahead: the picture foreshadowing's next step changes a place to -------------------
    def ahead(self, flags: list[str]) -> None:
        """The pictures the next steps of foreshadowing change a place to (a look on a stage's flag,
        engine/signs.coming): each drawn now in the background, from the picture the place shows, so
        it is there by the time its step shows; kept until then under the look's own key and never
        shown before its flag is set (_looks). GameState.looks keeps each, so it is drawn once a game,
        and only while Codex and the budget allow (_may_make): a sign the party never meets costs
        nothing, one it never comes back to one picture."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for flag in dict.fromkeys(flags):
            if flag in self.state.flags:
                continue  # come already: drawn the usual way (_looks)
            for loc in self.module.locations:
                for look in loc.looks:
                    key = look.key(loc.art or "")
                    if not loc.art or look.when != f"flag:{flag}" or key == loc.art or key in self.state.looks:
                        continue
                    if (drawn := self.module.pictures.get(key)) and Path(drawn).is_file():
                        continue  # drawn already: the module's own, or one an import drew
                    if not self._may_make():
                        return
                    self.state.looks[key] = {"target": f"location:{loc.id}", "kind": "scene", "change": look.change,
                                             "when": look.when, "status": "pending", "picture": "", "turn": str(self.state.turn)}
                    self.tasks[f"ahead:{key}"] = asyncio.create_task(self._ahead(key), name=f"ahead:{key}")

    def _drawing_ahead(self, key: str) -> bool:
        return (task := self.tasks.get(f"ahead:{key}")) is not None and not task.done()

    async def _ahead(self, key: str) -> None:
        journal = getattr(self, "debug_journal", None)
        record = self.state.looks[key]
        context = journal.task(f"redraw-ahead:{key}", turn=self.state.turn,
                               details={"when": record.get("when", ""), "change": record.get("change", "")}) if journal else nullcontext()
        with context:
            await self._ahead_inner(key)

    async def _ahead_inner(self, key: str) -> None:
        record, name = self.state.looks[key], f"ahead:{key}"
        found = self._target(record.get("target", ""))
        base = found.key if found is not None else ""
        # What the place will show first: a redraw of it under way, and its pictures drawn ahead before this one.
        order = list(self.tasks)
        first = [self.tasks[n] for n in order[: order.index(name) if name in order else len(order)]
                 if n.startswith("ahead:") and self.state.looks.get(n[6:], {}).get("target") == record.get("target")]
        first += [task for task in (self.tasks.get(f"look:{base}"),) if task is not None]
        try:
            if waiting := [task for task in first if not task.done()]:
                await asyncio.wait(waiting)
            async with self.limit:
                await self.idle.wait()
                if self._may_make():
                    record["attempts"] = str(int(record.get("attempts", "0")) + 1)
                    picture = await self._redraw(record, self.module.pictures.get(base, ""), self.folder / f"{key}.look1.jpg")
                    record.update(picture=str(picture), made="1", status="ready")
                    record.pop("error", None)
                    self.module.pictures[key] = str(picture)  # kept under its own key: shown once its flag is set
                    self._keep(key)
                else:  # no picture to spare now: drawn the usual way if its step comes
                    self.state.looks.pop(key, None)
        except asyncio.CancelledError:
            raise  # still pending: drawn ahead when the game is loaded, unless its step has come by then
        except Exception as exc:  # nothing drawn ahead: the step's picture is drawn the usual way when it comes
            record.update(status="failed", error=type(exc).__name__)
            self._keep(key)
        if record.get("when", "")[5:] in self.state.flags:  # its step came while it was drawn: shown, or drawn, now
            self.tasks.pop(name, None)
            self._looks()

    def _show(self, key: str) -> None:
        """Put a changed place's or person's latest picture in front of its first one."""
        record = self.state.looks.get(key)
        picture = record.get("picture", "") if record else ""
        if not picture or not Path(picture).is_file():
            return
        self.module.pictures[key] = picture
        found = self._target(record.get("target", ""))
        if found is not None and not getattr(found.entity, found.field):  # it had no picture at all: it has one now
            setattr(found.entity, found.field, key)

    async def _publish_look(self, key: str) -> None:
        self._keep(key)
        art = self.module.art.get(key, "")
        mask = self.module.art_colors.get(key) or artkit.auto_mask(art)
        await self.emit({"type": "art", "key": key, "art": art, "mask": mask, "status": "ready", "picture": self.module.pictures.get(key, "")})

    def _keep(self, key: str) -> None:
        """A changed look lands between saves: kept beside its picture until the game is saved."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{key}.look.json").write_text(json.dumps(self.state.looks[key], ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _recover(self) -> None:
        """Looks drawn after the last save (the save is from before they were done)."""
        if not self.folder.is_dir():
            return
        for path in self.folder.glob("*.look.json"):
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(cached, dict) or not all(isinstance(v, str) for v in cached.values()):
                continue
            key = path.name.removesuffix(".look.json")
            have = self.state.looks.get(key, {})
            if int(cached.get("made", "0") or 0) > int(have.get("made", "0") or 0):
                self.state.looks[key] = cached
