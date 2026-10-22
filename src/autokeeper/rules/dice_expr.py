"""Damage dice as a model, a module or a plugin writes them, and as BCDice can roll them.

Whoever writes a damage expression writes it the way the rulebook does: "1D6+DB", "1D4+2+半DB",
a shotgun's "4D6/2D6/1D6" by range band. BCDice rolls none of these as written. It refuses "DB",
any Chinese, a bare number ("0", "2") and a word after the dice ("1D3点"), and it reads "/" as
division and stops at the first space ("1D6 + 2" rolls 1D6). The review of 2026-10-21 found model
strings like these reaching it and failing mid-fight.

So nothing written is rolled as written. `DamageExpr.parse` reads the text once, with a Chinese
reason for what it cannot read; `text()` gives it back in one canonical spelling; `rollable(db)`
gives the BCDice command for one band with the damage bonus put in, and "" when there is nothing
to roll (a constant, or no damage at all: "0" and "" both mean none). `DiceText` is the pydantic
type for a freshly written expression: its JSON schema carries `PATTERN`, so the harness's one
repair pass sends a bad string back to the model before any of this code sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, WithJsonSchema

MAX_COUNT = 99  # dice in one term
MAX_SIDES = 100
MAX_CONSTANT = 999
MAX_TERMS = 8  # in one band
MAX_BANDS = 3  # a shotgun's ranges: point blank / medium / long

# The schema's pattern: what a model may write. Spaces around the signs are allowed (the canonical
# text drops them); anything else, Chinese words above all, fails here. Plain enough for Python's
# re (jsonschema), ECMA-262 (the providers) and the Rust regex alike.
_TERM = r"(?:\d{0,2}[dD]\d{1,3}|\d{1,3}|半?[dD][bB])"
_BAND = rf" *\+? *{_TERM}(?: *[+-] *{_TERM})* *"
PATTERN = rf"^(?:{_BAND}(?:/{_BAND}){{0,2}})?$"

HOW = "写成 1D6、1D4+2、1D6+DB 这样"  # what every reason ends with


@dataclass(frozen=True)
class Term:
    """One term of a sum: `count`D`sides`, a constant `count` (sides 0), or the damage bonus."""

    sign: int  # +1 or -1
    count: int = 0
    sides: int = 0
    bonus: Literal["", "DB", "半DB"] = ""

    def text(self) -> str:
        if self.bonus:
            return self.bonus
        return f"{self.count}D{self.sides}" if self.sides else str(self.count)


Band = tuple[Term, ...]


@dataclass(frozen=True)
class DamageExpr:
    """A parsed damage expression: one band, or up to three by range (a shotgun's)."""

    bands: tuple[Band, ...] = ()

    # -- reading ----------------------------------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> DamageExpr:
        """The expression `text` writes, or ValueError with a Chinese reason. "" and "0" are no
        damage; full-width signs and digits are read as the half-width ones."""
        raw = str(text)
        plain = _halfwidth(raw).replace(" ", "")
        if not plain:
            return cls()
        if reason := _reason(plain):
            raise ValueError(reason)
        parts = plain.split("/")
        bands = tuple(_band(part, raw) for part in parts)
        if len(bands) > 1 and not all(any(t.sides for t in b) for b in bands):
            raise ValueError(f"“{raw}”里的“/”是按射程分档（如 4D6/2D6/1D6），每档都要有骰子；骰子式不能做除法")
        return cls(bands)

    # -- what it is -------------------------------------------------------------------------
    def text(self) -> str:
        """The canonical spelling: "1d6 + db" is "1D6+DB", "D4" is "1D4"."""
        return "/".join(_render(band) for band in self.bands)

    def __str__(self) -> str:
        return self.text()

    @property
    def none(self) -> bool:
        """No damage at all: "" or "0" (every band only zero constants)."""
        return all(t.count == 0 and not t.bonus for band in self.bands for t in band)

    @property
    def has_db(self) -> bool:
        return any(t.bonus for band in self.bands for t in band)

    def band(self, band: int = 0) -> Band:
        """The terms of one band; a band past the last is the last (long range of a one-band gun)."""
        if not self.bands:
            return ()
        return self.bands[max(0, min(band, len(self.bands) - 1))]

    def weapon(self, band: int = 0) -> DamageExpr:
        """The band without its damage bonus: the weapon's own damage, which an impaling hit rolls
        once more on top of the maximum (Keeper Rulebook p. 87: 1D4+DB at extreme is 1D4+4+4; the
        .45's 1D10+2 impales for 12+1D10+2, p. 99)."""
        kept = tuple(t for t in self.band(band) if not t.bonus)
        return DamageExpr((kept,)) if kept else DamageExpr()

    # -- rolling it -------------------------------------------------------------------------
    def terms(self, db: str = "0", band: int = 0) -> Band:
        """The band's terms with the damage bonus `db` put in for DB (half of it for 半DB)."""
        bonus = bonus_terms(db)
        half = tuple(_halve(t) for t in bonus)
        out: list[Term] = []
        for t in self.band(band):
            if t.bonus:
                out += [Term(t.sign * b.sign, b.count, b.sides) for b in (bonus if t.bonus == "DB" else half)]
            else:
                out.append(t)
        return tuple(t for t in out if t.count)  # "+0" and an empty bonus add nothing

    def rollable(self, db: str = "0", band: int = 0) -> str:
        """The BCDice command for one band, or "" when it has no dice to roll (a constant, or none).
        Never holds "DB", Chinese, a space or "/"."""
        terms = self.terms(db, band)
        return _render(terms) if any(t.sides for t in terms) else ""

    def fixed(self, db: str = "0", band: int = 0) -> int | None:
        """What the band comes to when there is nothing to roll ("2", "0", "1+DB" at DB -1): never
        below 0. None when there are dice."""
        terms = self.terms(db, band)
        if any(t.sides for t in terms):
            return None
        return max(0, sum(t.sign * t.count for t in terms))

    def maximum(self, db: str = "0", band: int = 0) -> int:
        """The most the band can come to with the bonus in: an extreme success's damage. A negative
        term counts at its least (1D6-1D4 is at most 5). Never below 0."""
        return max(0, sum(t.sign * t.count * (t.sides or 1) if t.sign > 0 else -t.count for t in self.terms(db, band)))

    def total(self, dice, db: str = "0", band: int = 0) -> int:
        """Rolled through `dice` (BCDice, or a test's FakeDice) when there are dice, never below 0."""
        command = self.rollable(db, band)
        if not command:
            return self.fixed(db, band) or 0
        return max(0, int(dice.total(command)))


def parse(text: str) -> DamageExpr:
    return DamageExpr.parse(text)


def valid(text: str) -> bool:
    try:
        DamageExpr.parse(text)
    except ValueError:
        return False
    return True


def problem(text: str) -> str:
    """Why `text` is no damage expression, in Chinese; "" when it is one."""
    try:
        DamageExpr.parse(text)
    except ValueError as exc:
        return str(exc)
    return ""


def bonus_terms(db: str) -> Band:
    """A damage bonus as Investigator.damage_bonus and creature sheets write it ("-2", "0", "+1D4",
    "1D6"): its terms. ValueError when it is not one."""
    plain = _halfwidth(str(db)).replace(" ", "").upper()
    if plain in ("", "0", "+0", "-0"):
        return ()
    if not re.fullmatch(r"[+-]?(?:\d{0,2}D\d{1,3}|\d{1,3})(?:[+-](?:\d{0,2}D\d{1,3}|\d{1,3}))*", plain):
        raise ValueError(f"伤害加值“{db}”读不懂：写成 -2、0、+1D4、+1D6 这样")
    if not plain.startswith(("+", "-")):
        plain = "+" + plain
    out = []
    for sign, body in re.findall(r"([+-])([^+-]+)", plain):
        term = _term(body, sign, str(db))
        if term.bonus:  # pragma: no cover - the pattern above admits no DB
            raise ValueError(f"伤害加值“{db}”里不能再有 DB")
        out.append(term)
    return tuple(out)


def canonical(text: str) -> str:
    """The canonical spelling of a valid expression; ValueError (a Chinese reason) otherwise."""
    return DamageExpr.parse(text).text()


def _kept(text: str) -> str:
    """A stored expression in its canonical spelling when it is one, as it was when it is not."""
    try:
        return DamageExpr.parse(text).text()
    except ValueError:
        return text


_SCHEMA = {"type": "string", "pattern": PATTERN}

# A damage expression a model, the keeper's tools or a plugin writes now: checked (with a Chinese
# reason) and kept in its canonical spelling. "" and "0" are no damage.
DiceText = Annotated[str, AfterValidator(canonical), WithJsonSchema(_SCHEMA)]

# One kept in a module. A model writing it meets the same pattern; but a string that was never an
# expression (an imported module's "1D3点") still loads, so the game never locks on it: it is
# refused where it is rolled (DamageExpr.parse), with its reason.
StoredDiceText = Annotated[str, AfterValidator(_kept), WithJsonSchema(_SCHEMA)]


# A SAN roll's losses a model writes now (Failure.sanity): what passing and what failing it costs, each a
# number or dice, "0/1D3" (Rules.sanity rolls them); "" for no roll. No damage bonus, no range bands.
_SAN_TERM = r"(?:\d{0,2}[dD]\d{1,3}|\d{1,3})"
_SAN_SIDE = rf" *{_SAN_TERM}(?: *\+ *{_SAN_TERM})* *"
SANITY_PATTERN = rf"^(?:{_SAN_SIDE}/{_SAN_SIDE})?$"


def _sanity(text: str) -> str:
    plain = _halfwidth(str(text))
    if not plain:
        return ""
    if not re.fullmatch(SANITY_PATTERN, plain):
        raise ValueError(f"理智损失“{text}”读不懂：写成 成功时损失/失败时损失，如 0/1D3、1/1D6")
    return "/".join(canonical(side) for side in plain.split("/"))


SanityText = Annotated[str, AfterValidator(_sanity), WithJsonSchema({"type": "string", "pattern": SANITY_PATTERN})]


# -- the parts ------------------------------------------------------------------------------------
_FULLWIDTH = str.maketrans("０１２３４５６７８９ＤｄＢｂ＋－／　", "0123456789DdBb+-/ ")


def _halfwidth(text: str) -> str:
    return text.translate(_FULLWIDTH).replace("−", "-").replace("–", "-").strip()


# What a model tends to write instead of an expression, and what to tell it.
_WORDS = (
    (re.compile(r"^(?:无|没有|不受伤|不适用|无伤害|-|—|/|N/?A|none)$", re.I), "没有伤害就留空（或写 0），不要写“{w}”"),
    (re.compile(r"伤害加[值成]|伤害|加值|db值"), "伤害加值写作 DB，不要写“{w}”：如 1D6+DB"),
    (re.compile(r"[（(【\[].*?[）)】\]]"), "括号里的说明“{w}”不要写进骰子式：只写骰子，如 1D6"),
    (re.compile(r"[点點]|hp|HP|生命"), "骰子式后面不要带单位“{w}”：如 1D3"),
    (re.compile(r"[x×*÷]"), "骰子式不能乘除（“{w}”）：" + HOW),
)


def _reason(plain: str) -> str:
    """Why the text (half-width, no spaces) is not an expression, or "" when its shape is right."""
    if re.fullmatch(PATTERN, plain):
        return ""
    for pattern, says in _WORDS:
        if found := pattern.search(plain):
            return says.format(w=found.group())
    if plain.count("/") >= MAX_BANDS:
        return f"“{plain}”分了 {plain.count('/') + 1} 档：最多三档，按射程从近到远，如霰弹枪的 4D6/2D6/1D6"
    if plain.endswith(("+", "-")):
        return f"“{plain}”末尾多了一个“{plain[-1]}”：" + HOW
    if plain.startswith("-"):
        return f"“{plain}”是负数：伤害从正数写起，" + HOW
    return f"“{plain}”不是骰子式：" + HOW


def _band(part: str, raw: str) -> Band:
    body = part.lstrip("+")
    terms = [_term(b, s or "+", raw) for s, b in re.findall(r"([+-]?)([^+-]+)", body)]
    if len(terms) > MAX_TERMS:
        raise ValueError(f"“{raw}”太长了：一档最多 {MAX_TERMS} 项")
    if sum(1 for t in terms if t.bonus) > 1:
        raise ValueError(f"“{raw}”里 DB 写了不止一次：伤害加值只加一次")
    if any(t.bonus and t.sign < 0 for t in terms):
        raise ValueError(f"“{raw}”里 DB 前面是减号：伤害加值只能加，负的加值由引擎代入")
    return tuple(terms)


def _term(body: str, sign: str, raw: str) -> Term:
    s = -1 if sign == "-" else 1
    where = f"“{raw}”里的 {body}" if _halfwidth(raw).replace(" ", "") != body else f"“{raw}”"
    upper = body.upper()
    if upper in ("DB", "半DB"):
        return Term(s, bonus=upper)  # type: ignore[arg-type]
    if "D" in upper:
        n, _, sides = upper.partition("D")
        count, faces = int(n or 1), int(sides)
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"{where}掷不出点数：骰子个数从 1 到 {MAX_COUNT}")
        if not 2 <= faces <= MAX_SIDES:
            raise ValueError(f"{where}面数不对：骰子 2 到 {MAX_SIDES} 面")
        return Term(s, count, faces)
    value = int(upper)
    if value > MAX_CONSTANT:
        raise ValueError(f"{where}太大了：常数最多 {MAX_CONSTANT}")
    return Term(s, value)


def _render(terms: Band) -> str:
    out = ""
    for i, t in enumerate(terms):
        out += ("-" if t.sign < 0 else "+" if i else "") + t.text()
    return out


def _halve(t: Term) -> Term:
    """Half a bonus term, as 7th edition throws a thrown weapon's: +1D4 is +1D2, -2 is -1."""
    if t.sides:
        return Term(t.sign, t.count, max(1, t.sides // 2)) if t.sides // 2 >= 2 else Term(t.sign, t.count)
    return Term(t.sign, t.count // 2)
