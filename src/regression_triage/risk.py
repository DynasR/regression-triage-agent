"""The model judges; the code checks the judgement, scores it, and routes it.

check_classification  turns what the model said into a category the code can
                      trust: an answer that cites a justification which does
                      not exist, or does not fit, becomes a new regression.
score                 points per gap: category weight x product criticality.
route                 go / human_review / block, from the score alone.

Every rule fails towards caution: a missing, malformed or unjustified answer
counts as the worst category, never as the best.
"""
from .data import CATEGORIES

CATEGORY_WEIGHT = {"numerical_noise": 0, "expected_change": 0, "known_defect": 1, "new_regression": 5}
CRITICALITY_WEIGHT = {"low": 1, "medium": 2, "high": 3}

HUMAN_REVIEW_FROM = 3   # e.g. one known defect on a high-criticality product
BLOCK_FROM = 10         # e.g. one new regression on a medium-criticality product
NOISE_MAX_RATIO = 5.0   # a gap this many times above its tolerance is not noise


def _reject(gap: dict, proposed: dict, why: str) -> dict:
    return {"result_id": gap["result_id"], "category": "new_regression", "evidence_id": None,
            "rationale": proposed.get("reasoning", ""), "accepted": False, "rejected_because": why,
            "proposed": {"category": proposed.get("category"), "evidence_id": proposed.get("evidence_id")}}


def check_classification(raw, gap: dict, evidence: dict) -> dict:
    """Accept the model's answer for one gap only if the code can verify its justification."""
    if not isinstance(raw, dict):
        return _reject(gap, {}, "no answer from the model")
    category, cited = raw.get("category"), raw.get("evidence_id")
    if category not in CATEGORIES:
        return _reject(gap, raw, f"unknown category {category!r}")

    notes = {n["id"]: n for n in evidence.get("release_notes", [])}
    tickets = {t["id"]: t for t in evidence.get("tickets", [])}
    if category == "numerical_noise":
        small = gap["status"] == "out_of_tolerance" and gap["ratio"] is not None and gap["ratio"] < NOISE_MAX_RATIO
        if not small:
            return _reject(gap, raw, f"noise is only accepted for a gap below {NOISE_MAX_RATIO:g} x its tolerance")
        cited = None
    elif category == "expected_change":
        note = notes.get(cited)
        if note is None:
            return _reject(gap, raw, f"expected change without a release note of this release (cited {cited!r})")
        if note["product_code"] not in (None, gap["product_code"]):
            return _reject(gap, raw, f"release note {cited} is about another product ({note['product_code']})")
    elif category == "known_defect":
        ticket = tickets.get(cited)
        if ticket is None or ticket["status"] != "open":
            return _reject(gap, raw, f"known defect without an open ticket (cited {cited!r})")
        if ticket["product_code"] != gap["product_code"]:
            return _reject(gap, raw, f"ticket {cited} is about another product ({ticket['product_code']})")
    else:
        cited = None
    return {"result_id": gap["result_id"], "category": category, "evidence_id": cited,
            "rationale": raw.get("reasoning", ""), "accepted": True, "rejected_because": None,
            "proposed": {"category": category, "evidence_id": raw.get("evidence_id")}}


def route(points: int) -> str:
    if points >= BLOCK_FROM:
        return "block"
    if points >= HUMAN_REVIEW_FROM:
        return "human_review"
    return "go"


def score(gaps: list[dict], classifications: list[dict]) -> dict:
    by_id = {c["result_id"]: c["category"] for c in classifications}
    lines = []
    for gap in gaps:
        category = by_id.get(gap["result_id"], "new_regression")   # unclassified: assume the worst
        criticality = gap["criticality"] if gap["criticality"] in CRITICALITY_WEIGHT else "high"
        points = CATEGORY_WEIGHT[category] * CRITICALITY_WEIGHT[criticality]
        lines.append({"result_id": gap["result_id"], "case_id": gap["case_id"], "metric": gap["metric"],
                      "category": category, "criticality": criticality, "points": points})
    total = sum(line["points"] for line in lines)
    return {"score": total, "lines": lines, "route": route(total)}
