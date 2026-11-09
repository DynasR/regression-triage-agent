"""The code checks every case the model proposed, measures coverage, and fills what is missing.

A proposed case is kept only if:
- it has the shape of a case (pydantic);
- it cites a rule the spec has;
- every number in it is in the spec or computed by the code (bounds.allowed_numbers);
- for a rule with a bound, when the case gives the bound's variable a number,
  its expected result is the one the code computes at that number;
- it is not the same test as a case already kept.

A rejected case is kept apart with its reason, for the audit. It never counts
for coverage. Then, for each bound, the two boundary points the model did not
test are written by the code from a fixed template. A rule with no bound and
no accepted case is signalled: only a human can write it.

Every doubt fails towards the human: a case the code cannot verify is not
silently trusted, a rule nobody covered is not silently dropped.
"""
import json
import re
import unicodedata

from pydantic import ValidationError

from .bounds import allowed_numbers, as_number, boundary_points, numbers_in, signed_numbers_in
from .spec import Rounding, Spec, Threshold
from .writer import ProposedCase

REASONS = ("schéma", "règle inconnue", "nombre inventé", "issue absente", "issue ambiguë", "issue contredite",
           "arrondi faux", "doublon")
_RULE_ID = re.compile(r"^R-?0*(\d+)$")


def canonical_rule_id(text: str) -> str | None:
    m = _RULE_ID.match(str(text).strip().upper())
    return f"R-{int(m.group(1)):02d}" if m else None


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9.,-]+", " ", text)


def _stem(label: str) -> str:
    """The part of a label that does not change with gender and number: accepté, acceptée, acceptés -> accept."""
    stem = _plain(label).strip().rstrip("s").rstrip("e")
    return stem[:-1] if stem.endswith("if") else stem          # significatif, significative


def named_outcome(text: str, bound: Threshold) -> str | None | bool:
    """The outcome a text names: one of the two labels, None if neither, False if both (ambiguous).
    A label contained in the other (bloquée in non bloquée) loses to the longer one."""
    plain = _plain(text)
    found = [label for label in (bound.if_true, bound.if_false) if _stem(label) in plain]
    if len(found) == 2:
        longer, shorter = sorted(found, key=lambda label: len(_stem(label)), reverse=True)
        return longer if _stem(shorter) in _stem(longer) else False
    return found[0] if found else None


def _reject(raw, reason: str, why: str) -> dict:
    assert reason in REASONS
    return {"reason": reason, "why": why, "proposed": raw}


def _numbers_of(case: ProposedCase) -> set:
    found = set()
    for text in [*case.preconditions, case.expected, *case.inputs]:
        found |= numbers_in(text)
    for value in case.inputs.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found.add(abs(as_number(value)))        # 1e-05 is one number, not "1" and "05"
        elif isinstance(value, str):
            found |= numbers_in(value)
    return found


def _same_test_key(rule_id: str, inputs: dict) -> str:
    def norm(value):
        number = as_number(value)
        return str(number.normalize()) if number is not None else value
    return json.dumps([rule_id, {k: norm(v) for k, v in inputs.items()}], sort_keys=True, ensure_ascii=False)


def check_case(raw, spec: Spec, allowed: set, seen: set) -> tuple[dict | None, dict | None]:
    """(accepted case, None) or (None, rejection). Never raises."""
    try:
        case = ProposedCase.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        return None, _reject(raw, "schéma", f"{'.'.join(map(str, first['loc'])) or 'cas'} : {first['msg']}"[:200])

    rule_id = canonical_rule_id(case.rule)
    rule = spec.rule(rule_id) if rule_id else None
    if rule is None:
        return None, _reject(raw, "règle inconnue", f"la règle {case.rule!r} n'existe pas dans {spec.id} "
                                                    f"(règles : {', '.join(r.id for r in spec.rules)})")

    invented = sorted(_numbers_of(case) - allowed)
    if invented:
        return None, _reject(raw, "nombre inventé", "nombres absents de la spécification et des valeurs calculées "
                                                    "par le code : " + ", ".join(str(n) for n in invented))

    bound = rule.bound
    value = as_number(case.inputs.get(bound.variable)) if bound else None
    if isinstance(bound, Threshold) and value is not None:
        right, named = bound.outcome(value), named_outcome(case.expected, bound)
        if named is None:
            return None, _reject(raw, "issue absente", f"pour {bound.variable} = {value}, le résultat attendu ne "
                                                       f"nomme ni « {bound.if_true} » ni « {bound.if_false} »")
        if named is False:
            return None, _reject(raw, "issue ambiguë", f"le résultat attendu nomme « {bound.if_true} » et "
                                                       f"« {bound.if_false} »")
        if named != right:
            return None, _reject(raw, "issue contredite", f"pour {bound.variable} = {value}, la règle {rule.id} "
                                                          f"donne « {right} », le cas attend « {named} »")
    if isinstance(bound, Rounding) and value is not None:
        right, written = bound.outcome(value), signed_numbers_in(case.expected)
        if right not in written:
            return None, _reject(raw, "arrondi faux" if written else "issue absente",
                                 f"{bound.variable} = {value} s'arrondit à {right} selon {rule.id} "
                                 f"({bound.decimals} décimales, {bound.mode}), le cas attend « {case.expected} »")

    key = _same_test_key(rule.id, case.inputs)
    if key in seen:
        return None, _reject(raw, "doublon", f"même règle {rule.id} et mêmes entrées qu'un cas déjà gardé")
    seen.add(key)
    return {"proposed_id": case.id, "rule": rule.id, "kind": case.kind, "preconditions": case.preconditions,
            "inputs": case.inputs, "expected": case.expected, "origin": "modèle", "covers": []}, None


def _template(spec: Spec, rule, point) -> dict:
    return {"proposed_id": None, "rule": rule.id, "kind": "limite",
            "preconditions": [f"Spécification {spec.id}, règle {rule.id} : {rule.title}"],
            "inputs": {rule.bound.variable: str(point.value)}, "expected": point.expected,
            "origin": "code", "covers": [point.name]}


def build_book(spec: Spec, proposal: dict, model: dict | None = None) -> dict:
    """The test book: the accepted cases, the cases the code added, the rejected ones, and the coverage."""
    allowed, seen = allowed_numbers(spec), set()
    error = proposal.get("error") if isinstance(proposal, dict) else "no proposal"
    raw_cases = [] if error else proposal.get("cases", [])
    accepted, rejected = [], []
    for raw in raw_cases:
        case, rejection = check_case(raw, spec, allowed, seen)
        (accepted.append(case) if case else rejected.append(rejection))

    cases, uncovered = [], []
    bounds_required = bounds_by_model = 0
    for rule in spec.rules:
        mine = [c for c in accepted if c["rule"] == rule.id]
        added = []
        for point in boundary_points(rule):
            bounds_required += 1
            hits = [c for c in mine if as_number(c["inputs"].get(rule.bound.variable)) == point.value]
            for c in hits:
                c["covers"].append(point.name)
            if hits:
                bounds_by_model += 1
            else:
                added.append(_template(spec, rule, point))
        cases += mine + added
        if not mine and not added:
            uncovered.append({"rule": rule.id, "title": rule.title,
                              "why": "règle sans borne lisible par le code et sans cas accepté : à écrire par un humain"})
    for n, case in enumerate(cases, 1):
        case["id"] = f"{spec.id}-{n:03d}"

    covered_by_model = {c["rule"] for c in accepted}
    by_reason = {}
    for r in rejected:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    return {
        "spec": {"id": spec.id, "title": spec.title, "version": spec.version, "file": spec.source,
                 "sha256": spec.sha256},
        "model": model,
        "model_error": error,
        "rules": [{"id": r.id, "title": r.title, "bound": _describe(r.bound),
                   "boundary_points": [{"name": p.name, "value": str(p.value), "expected": p.expected}
                                       for p in boundary_points(r)]} for r in spec.rules],
        "cases": [{"id": c["id"], **{k: v for k, v in c.items() if k != "id"}} for c in cases],
        "rejected": rejected,
        "coverage": {
            "rules_total": len(spec.rules),
            "rules_covered_by_model": len(covered_by_model),
            "rules_covered": len({c["rule"] for c in cases}),
            "uncovered": uncovered,
            "bounds_required": bounds_required,
            "bounds_by_model": bounds_by_model,
            "bounds_added_by_code": bounds_required - bounds_by_model,
            "proposed": len(raw_cases),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "rejected_by_reason": by_reason,
        },
    }


def _describe(bound) -> dict | None:
    if isinstance(bound, Threshold):
        return {"kind": "borne", "variable": bound.variable, "absolute": bound.absolute, "op": bound.op,
                "value": str(bound.value), "unit": bound.unit, "step": str(bound.step),
                "if_true": bound.if_true, "if_false": bound.if_false}
    if isinstance(bound, Rounding):
        return {"kind": "arrondi", "variable": bound.variable, "decimals": bound.decimals, "mode": bound.mode}
    return None
