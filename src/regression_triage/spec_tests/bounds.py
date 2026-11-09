"""The numbers of a test book: computed by the code from the spec, never asked of the model.

boundary_points  the two values every bound must be tested at: on the bound,
                 and the first value on the other side of it (one step away).
test_values      those two, plus a few values the code also allows in a case:
                 the negative side of an absolute bound, an ordinary value on
                 each side, the next tie of a rounding rule.
allowed_numbers  every number a case may contain: the numbers written in the
                 spec, and the values above with their outcomes.
numbers_in       the numbers of a text, whatever their writing (50,00 = 50.00 = 50).

The same idea as the memo's number guard (report.py): a number nobody wrote
in the spec and nobody computed has no place in a test book.
"""
import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from .spec import Rounding, Rule, Spec, Threshold

_RULE_ID = re.compile(r"\bR-\d+\b")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SIGNED = re.compile(r"-?\d+(?:[.,]\d+)?")
_ALONE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(?:EUR|€|h|heures?|caract[eè]res?)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Point:
    name: str
    value: Decimal
    expected: str           # the outcome the rule gives at this value: a label, or the rounded number
    required: bool          # a boundary point: the book must test it


def numbers_in(text) -> set[Decimal]:
    """Unsigned numbers of a text. Rule ids (R-01) are names, not numbers."""
    return {Decimal(n.replace(",", ".")) for n in _NUMBER.findall(_RULE_ID.sub(" ", str(text or "")))}


def signed_numbers_in(text) -> set[Decimal]:
    return {Decimal(n.replace(",", ".")) for n in _SIGNED.findall(_RULE_ID.sub(" ", str(text or "")))}


def as_number(value) -> Decimal | None:
    """An input value as a number, when it is one: 50, 50.0, "50,00", "50.00 EUR". Otherwise None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str) and (m := _ALONE.match(value)):
        return Decimal(m.group(1).replace(",", "."))
    return None


def _expected(bound, x: Decimal) -> str:
    return bound.outcome(x) if isinstance(bound, Threshold) else str(bound.outcome(x))


def boundary_points(rule: Rule) -> list[Point]:
    bound = rule.bound
    if isinstance(bound, Threshold):
        s, step = bound.value, bound.step
        # On the bound, then the first value on the other side of it.
        beyond = {"<=": s + step, ">=": s - step, "<": s - step, ">": s + step}[bound.op]
        name = "juste au-delà" if beyond > s else "juste en deçà"
        return [Point("à la limite", s, _expected(bound, s), True), Point(name, beyond, _expected(bound, beyond), True)]
    if isinstance(bound, Rounding):
        half = Decimal(5).scaleb(-(bound.decimals + 1))         # the first tie: 0.005 for 2 decimals
        epsilon = Decimal(1).scaleb(-(bound.decimals + 2))
        # The other side of the tie is the side the tie does not go to.
        beyond = half - epsilon if bound.outcome(half) > 0 else half + epsilon
        name = "juste en deçà" if beyond < half else "juste au-delà"
        return [Point("à mi-chemin", half, _expected(bound, half), True), Point(name, beyond, _expected(bound, beyond), True)]
    return []


def test_values(rule: Rule) -> list[Point]:
    points = boundary_points(rule)
    bound = rule.bound
    extra = []
    if isinstance(bound, Threshold):
        s, step = bound.value, bound.step
        if bound.absolute:
            extra += [Point("côté négatif, " + p.name, -p.value, _expected(bound, -p.value), False) for p in points]
        half, double = (s / 2).quantize(step, rounding=ROUND_FLOOR), s * 2
        inside, outside = (half, double) if bound.op in ("<=", "<") else (double, half)
        extra += [Point("valeur courante", inside, _expected(bound, inside), False),
                  Point("valeur hors borne", outside, _expected(bound, outside), False)]
    elif isinstance(bound, Rounding):
        half = points[0].value
        following = half + Decimal(1).scaleb(-bound.decimals)
        extra += [Point("mi-chemin négatif", -half, _expected(bound, -half), False),
                  Point("mi-chemin suivant", following, _expected(bound, following), False)]
    seen = {p.value for p in points}
    return points + [p for p in extra if p.value not in seen and not seen.add(p.value)]


test_values.__test__ = False   # not a pytest test, despite its name


def allowed_numbers(spec: Spec) -> set[Decimal]:
    allowed = set(numbers_in(spec.text))
    for rule in spec.rules:
        for point in test_values(rule):
            allowed.add(abs(point.value))
            allowed |= numbers_in(point.expected)
    return allowed
