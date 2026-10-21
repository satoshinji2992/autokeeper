"""Everyone in an encounter as the engine reads them: a `CombatantView`, rebuilt for every unit and
never stored. An investigator's numbers are their sheet's, an NPC's or a creature's its profile's
(engine/encounter/profiles.py) with the world's life on top; a weapon's rounds are its item's.

Read-only: a view changes nothing. Whatever happens to them goes through engine/effects.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from ...rules.catalog import canonical
from ...rules.dice_expr import DamageExpr
from ...rules.weapons import BRAWL, UNARMED, Weapon, weapon_in
from ..effects import Body, body_of
from . import profiles

if TYPE_CHECKING:
    from ...rules.coc import Investigator
    from ...world.record import Entity
    from ...world.store import World

Audience = Literal["players", "keeper"]


@dataclass(frozen=True)
class Traits:
    armor: int = 0
    guns_halved: bool = False
    regen: int = 0
    knockout: int = 0
    human: bool = True
    swarm: bool = False


@dataclass(frozen=True)
class AttackOption:
    """An attack someone can make now: natural, or a weapon they hold or carry (its item id)."""

    id: str  # the item id, or "natural:<name>"
    name: str
    skill: str
    value: int
    damage: DamageExpr | None  # None when the sheet's damage cannot be rolled (`problem` says why)
    damage_text: str
    firearm: bool = False
    impale: bool = False
    natural: bool = False
    item: str | None = None
    range: int = 0
    loaded: int | None = None  # rounds in it now; None for a weapon that takes none
    capacity: int = 0
    jammed: bool = False
    malfunction: int = 100
    problem: str = ""

    @property
    def usable(self) -> bool:
        return self.damage is not None and not self.jammed and (not self.firearm or self.loaded is None or self.loaded > 0)


class CombatantView(Protocol):
    id: str
    kind: Literal["investigator", "npc", "creature"]
    true_name: str

    def public_name(self, audience: Audience) -> str: ...
    def char(self, key: str) -> int: ...
    def skill(self, key: str) -> int: ...
    def body(self) -> Body: ...
    def can_act(self) -> bool: ...
    def conscious(self) -> bool: ...
    def attacks(self) -> list[AttackOption]: ...
    def db(self) -> str: ...
    def build(self) -> int: ...
    def mov(self) -> int: ...
    def traits(self) -> Traits: ...
    def held(self) -> list[str]: ...
    def attacks_per_round(self) -> int: ...


def _expr(text: str) -> tuple[DamageExpr | None, str]:
    try:
        return DamageExpr.parse(text.replace("+灼烧", "").replace("灼烧", "")), ""
    except ValueError as exc:
        return None, str(exc)


def _option(*, ident: str, name: str, skill: str, value: int, damage: str, firearm: bool = False, impale: bool = False,
            natural: bool = False, item: str | None = None, range_: int = 0, loaded: int | None = None, capacity: int = 0,
            jammed: bool = False, malfunction: int = 100, problem: str = "") -> AttackOption:
    expr, why = _expr(damage)
    return AttackOption(id=ident, name=name, skill=skill, value=value, damage=expr, damage_text=damage, firearm=firearm,
                        impale=impale, natural=natural, item=item, range=range_, loaded=loaded, capacity=capacity,
                        jammed=jammed, malfunction=malfunction, problem=problem or why)


class InvestigatorView:
    kind: Literal["investigator"] = "investigator"

    def __init__(self, inv: Investigator, world: World):
        self.inv, self.world = inv, world
        self.id, self.true_name = inv.id, inv.name

    def public_name(self, audience: Audience) -> str:
        return self.inv.name

    def char(self, key: str) -> int:
        return self.inv.characteristics.get(canonical(key), 0)

    def skill(self, key: str) -> int:
        return self.inv.value(key)

    def body(self) -> Body:
        body = body_of(self.world, self.id)
        assert body is not None
        return body

    def can_act(self) -> bool:
        return self.inv.can_act

    def conscious(self) -> bool:
        return not {"昏迷", "濒死", "死亡"} & set(self.inv.conditions)

    def held(self) -> list[str]:
        """Item ids in hand or carried: the record's item entities they hold, then inventory lines."""
        own = [e.id for e in self.world.rec.entities.values() if e.kind == "item" and e.at == self.id]
        names = {self.world.rec.entities[i].name for i in own}
        return [*own, *(line for line in self.inv.inventory if line not in names and line not in own)]

    def attacks(self) -> list[AttackOption]:
        """The bare hands, and every weapon they carry: an item entity's state when it has one, a
        full load when nobody has counted its rounds yet."""
        out = [_option(ident=f"natural:{UNARMED.name}", name=UNARMED.name, skill=BRAWL, value=self.inv.value(BRAWL),
                       damage=UNARMED.damage, natural=True)]
        seen: set[str] = set()
        for item in self.held():
            ent = self.world.rec.entities.get(item)
            weapon: Weapon | None = weapon_in(ent.name if ent is not None else item)
            if weapon is None or weapon is UNARMED or weapon.name in seen or (ent is not None and ent.quantity <= 0):
                continue
            seen.add(weapon.name)
            state = ent.weapon if ent is not None else None
            loaded = (state.loaded if state is not None and state.loaded is not None else weapon.ammo) if weapon.firearm else None
            out.append(_option(ident=item, name=weapon.name, skill=weapon.skill, value=self.inv.value(weapon.skill),
                               damage=weapon.damage, firearm=weapon.firearm, impale=weapon.impale, item=item, range_=weapon.range,
                               loaded=loaded, capacity=weapon.ammo, jammed=bool(state and state.jammed),
                               malfunction=weapon.malfunction))
        return out

    def db(self) -> str:
        return self.inv.damage_bonus

    def build(self) -> int:
        return self.inv.build

    def mov(self) -> int:
        return self.inv.move

    def traits(self) -> Traits:
        return Traits()

    def attacks_per_round(self) -> int:
        return 1


class EntityView:
    """An NPC or a creature: its profile's numbers, the world's life and items."""

    def __init__(self, entity: Entity | None, profile: profiles.CombatProfile, world: World):
        self.entity, self.profile, self.world = entity, profile, world
        self.id, self.true_name = profile.id, profile.name
        self.kind: Literal["npc", "creature"] = profile.kind

    @classmethod
    def of(cls, world: World, ident: str) -> EntityView | None:
        p = profiles.profile(world, ident)
        return None if p is None else cls(world.rec.entities.get(p.id), p, world)

    def public_name(self, audience: Audience) -> str:
        if audience == "keeper":
            return self.profile.name
        st = self.world.st
        known = self.id in st.identified or (self.kind == "npc" and (n := st.npcs.get(self.id)) is not None and n.met)
        return self.profile.name if known or not self.profile.called else self.profile.called

    def char(self, key: str) -> int:
        return self.profile.chars.get(canonical(key), 0)

    def skill(self, key: str) -> int:
        return self.profile.skill(key)

    def body(self) -> Body:
        body = body_of(self.world, self.id)
        assert body is not None
        return body

    def can_act(self) -> bool:
        return self.body().conscious

    def conscious(self) -> bool:
        return self.body().conscious

    def held(self) -> list[str]:
        return [e.id for e in self.world.rec.entities.values() if e.kind == "item" and e.at == self.id]

    def attacks(self) -> list[AttackOption]:
        out = []
        for a in self.profile.attacks:
            loaded = (a.loaded if a.loaded is not None else a.capacity) if a.firearm else None
            out.append(_option(ident=a.item or f"natural:{a.name}", name=a.name, skill=a.skill, value=a.value, damage=a.damage,
                               firearm=a.firearm, impale=a.impale, natural=a.natural, item=a.item, range_=a.range, loaded=loaded,
                               capacity=a.capacity, jammed=a.jammed, malfunction=a.malfunction, problem=a.problem))
        return out

    def db(self) -> str:
        return self.profile.db

    def build(self) -> int:
        return self.profile.build

    def mov(self) -> int:
        return self.profile.mov

    def traits(self) -> Traits:
        p = self.profile
        return Traits(armor=p.armor, guns_halved=p.guns_halved, regen=p.regen, knockout=p.knockout, human=p.human, swarm=p.swarm)

    def attacks_per_round(self) -> int:
        return self.profile.per_round


def view(world: World, ident: str) -> InvestigatorView | EntityView | None:
    """Whoever `ident` is, as the engine reads them; None for nobody."""
    inv = next((i for i in world.st.investigators if i.id == ident), None)
    return InvestigatorView(inv, world) if inv is not None else EntityView.of(world, ident)


__all__ = ["AttackOption", "CombatantView", "EntityView", "InvestigatorView", "Traits", "view"]
