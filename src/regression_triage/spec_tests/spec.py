"""Reading a functional spec: its rules, their ids, and the bounds the code can compute from.

A spec is a Markdown file:

    # SPEC-TOL — title
    Version 1.2 · ...
    ## R-01 — title of the rule
    The rule, in prose. The prose is what the model reads.
    Borne : `|ecart_pv| <= 50.00 EUR` · pas 0.01 · si vrai « accepté » · sinon « signalé »
    Arrondi : `arrondi(montant, 2, demi-haut)`

The "Borne" and "Arrondi" lines are the only part the code reads for numbers.
A rule without one is prose only: the code can check that a case cites it,
not what the case says. A line that starts like a bound but cannot be read
stops the run: a bound silently skipped would be a bound never tested.
"""
import hashlib
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path


class SpecError(ValueError):
    """The spec cannot be read as written. Nothing is guessed."""


_NUM = r"-?\d+(?:[.,]\d+)?"
_TITLE = re.compile(r"^#\s+([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\s*[—–-]\s*(.+?)\s*$")
_VERSION = re.compile(r"^Version\s+(\d+(?:\.\d+)*)")
_RULE = re.compile(r"^##\s+(R-\d+)\s*[—–-]\s*(.+?)\s*$")
_THRESHOLD = re.compile(
    r"^Borne\s*:\s*`\s*(\|?)\s*([a-z_][a-z0-9_]*)\s*(\|?)\s*(<=|>=|<|>)\s*(" + _NUM + r")\s*([^`]*?)\s*`"
    r"\s*·\s*pas\s+(\d+(?:[.,]\d+)?)\s*·\s*si vrai\s*«\s*([^»]+?)\s*»\s*·\s*sinon\s*«\s*([^»]+?)\s*»\s*$")
_ROUNDING = re.compile(r"^Arrondi\s*:\s*`\s*arrondi\(\s*([a-z_][a-z0-9_]*)\s*,\s*(\d+)\s*,\s*(demi-haut|demi-pair)\s*\)\s*`\s*$")
_FORMAL = re.compile(r"^(Borne|Arrondi)\s*:")

ROUNDING_MODES = {"demi-haut": ROUND_HALF_UP, "demi-pair": ROUND_HALF_EVEN}


def number(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


@dataclass(frozen=True)
class Threshold:
    """A condition on one variable: if it holds, the rule gives one outcome, otherwise the other."""
    variable: str
    absolute: bool          # the condition is on the absolute value
    op: str                 # <=, <, >=, >
    value: Decimal
    unit: str
    step: Decimal           # the smallest difference the rule cares about: "just beyond" is one step away
    if_true: str
    if_false: str

    def holds(self, x: Decimal) -> bool:
        x = abs(x) if self.absolute else x
        return {"<=": x <= self.value, "<": x < self.value, ">=": x >= self.value, ">": x > self.value}[self.op]

    def outcome(self, x: Decimal) -> str:
        return self.if_true if self.holds(x) else self.if_false


@dataclass(frozen=True)
class Rounding:
    """A rounding rule: a variable rounded to a number of decimals, ties broken by a mode."""
    variable: str
    decimals: int
    mode: str               # demi-haut (away from zero), demi-pair (to even)

    def outcome(self, x: Decimal) -> Decimal:
        rounded = x.quantize(Decimal(1).scaleb(-self.decimals), rounding=ROUNDING_MODES[self.mode])
        return rounded.copy_abs() if rounded == 0 else rounded     # no "-0.00"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    text: str
    bound: Threshold | Rounding | None = None


@dataclass(frozen=True)
class Spec:
    id: str
    title: str
    version: str | None
    text: str
    rules: tuple[Rule, ...]
    source: str = ""
    sha256: str = field(default="")

    def rule(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)


def _bound(line: str, rule_id: str):
    if m := _THRESHOLD.match(line):
        left, variable, right, op, value, unit, step, if_true, if_false = m.groups()
        if left != right:
            raise SpecError(f"{rule_id}: an absolute value needs a bar on both sides: {line}")
        if number(step) <= 0:
            raise SpecError(f"{rule_id}: the step must be positive: {line}")
        if if_true == if_false:
            raise SpecError(f"{rule_id}: both outcomes are the same: {line}")
        return Threshold(variable, bool(left), op, number(value), unit, number(step), if_true, if_false)
    if m := _ROUNDING.match(line):
        variable, decimals, mode = m.groups()
        return Rounding(variable, int(decimals), mode)
    raise SpecError(f"{rule_id}: this bound cannot be read by the code: {line}")


def parse_spec(text: str, source: str = "") -> Spec:
    lines = text.splitlines()
    title = next((m for line in lines if (m := _TITLE.match(line))), None)
    if title is None:
        raise SpecError("the spec has no title line '# SPEC-XXX — title'")
    version = next((m.group(1) for line in lines if (m := _VERSION.match(line))), None)

    rules, current = [], None

    def close():
        if current is not None:
            prose = [line for line in current["body"] if not _FORMAL.match(line.strip())]
            formal = [line.strip() for line in current["body"] if _FORMAL.match(line.strip())]
            if len(formal) > 1:
                raise SpecError(f"{current['id']}: more than one bound")
            rules.append(Rule(current["id"], current["title"], "\n".join(prose).strip(),
                              _bound(formal[0], current["id"]) if formal else None))

    for line in lines:
        if line.startswith("## "):
            close()
            m = _RULE.match(line)
            current = {"id": m.group(1), "title": m.group(2), "body": []} if m else None
        elif line.startswith("# "):
            close()
            current = None
        elif current is not None:
            current["body"].append(line)
    close()

    ids = [r.id for r in rules]
    if not rules:
        raise SpecError("the spec has no rule '## R-01 — title'")
    if len(set(ids)) != len(ids):
        raise SpecError(f"two rules share an id: {sorted({i for i in ids if ids.count(i) > 1})}")
    for rule in rules:
        if not rule.text:
            raise SpecError(f"{rule.id}: the rule has no text")
    return Spec(title.group(1), title.group(2), version, text, tuple(rules), source,
                hashlib.sha256(text.encode("utf-8")).hexdigest())


def load_spec(path) -> Spec:
    path = Path(path)
    return parse_spec(path.read_text(encoding="utf-8"), str(path))
