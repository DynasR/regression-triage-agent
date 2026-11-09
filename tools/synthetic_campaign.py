"""Synthetic campaigns of any size, built to give a known answer.

The demo database (data.py) has 8 campaigns of at most 5 gaps. To measure the
deterministic parts of the agent on hundreds or thousands of gaps, this module
builds its own database, with the schema of the demo (data.SCHEMA) and a
single campaign whose gaps are PLANTED: for each (category, product) a number
of gaps, each of them shaped so that the right answer is known in advance.
Nothing in production code is changed or reused apart from the schema, the
product table and the tolerances.

    plan = profile("block", 1000)           # [(category, product_code, count), ...]
    built = build(path, plan)               # built.truth: result_id -> (category, evidence_id)
    judge = SyntheticJudge(built.truth)     # a perfect model: answers the truth, counts its calls

Shapes (same as the demo): "noise" is 1.3 to 1.9 times the tolerance, every
other category is a large shift (at least 80 tolerances), and some regressions
are values that failed to price. Besides the gaps, three clean results per gap
are inserted (identical, or inside the tolerance), so a query that flags too
much is seen too. Result ids are shuffled: nothing depends on the order.

Usage as a script: python tools/synthetic_campaign.py <path> <gaps> [route]
"""
import random
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:          # run as a script, outside pytest
    sys.path.insert(0, str(ROOT / "src"))

from regression_triage.data import PRODUCTS, SCHEMA, TOLERANCES, tolerance  # noqa: E402

CAMPAIGN_ID = 1
REFERENCE, CANDIDATE = "7.0.0", "7.1.0"
CODES = tuple(PRODUCTS)
CLEAN_PER_GAP = 3
SEED = 20261110

Plan = list[tuple[str, str, int]]   # (category, product_code, how many gaps)


@dataclass(frozen=True)
class Built:
    path: Path
    plan: Plan
    gaps: int
    results: int
    truth: dict[int, tuple[str, str | None]]   # result_id -> (category, evidence id)


def _spread(category: str, codes: tuple[str, ...], total: int) -> Plan:
    """`total` gaps of one category, spread over the products in turn."""
    counts = {code: 0 for code in codes}
    for i in range(total):
        counts[codes[i % len(codes)]] += 1
    return [(category, code, n) for code, n in counts.items() if n]


def profile(route: str, gaps: int) -> Plan:
    """A plan of exactly `gaps` gaps whose expected route is `route`, whatever the size.

    go            2 known defects on a low-criticality product (2 points), the rest noise or announced
    human_review  3 known defects (3 + 1 + 1 = 5 points)
    block         one regression on a medium product (5 x 2 = 10 points, the exact block threshold)
    mixed         30 % noise, 30 % announced, 20 % known defects, 20 % regressions: a big score, block
    """
    if route == "mixed":
        regressions, known = gaps // 5, gaps // 5
        announced = noise = (gaps - regressions - known) // 2
        noise += gaps - regressions - known - 2 * noise
        return (_spread("numerical_noise", CODES, noise) + _spread("expected_change", CODES, announced)
                + _spread("known_defect", CODES, known) + _spread("new_regression", CODES, regressions))
    fixed = {"go": [("known_defect", "EQOPT", 2)],
             "human_review": [("known_defect", "FXBAR", 1), ("known_defect", "EQOPT", 2)],
             "block": [("new_regression", "FXOPT", 1)]}[route]
    rest = gaps - sum(n for _, _, n in fixed)
    if rest < 0:
        raise ValueError(f"{gaps} gaps is too few for the {route} profile")
    return fixed + _spread("numerical_noise", CODES, rest // 2) + _spread("expected_change", CODES, rest - rest // 2)


def _shift(reference: float, metric: str, rng: random.Random) -> float:
    tol = tolerance(metric, reference)
    return round(reference + rng.choice([-1, 1]) * max(abs(reference) * rng.uniform(0.004, 0.03), 80 * tol), 4)


def _noise(reference: float, metric: str, rng: random.Random) -> float:
    return round(reference + rng.choice([-1, 1]) * tolerance(metric, reference) * rng.uniform(1.3, 1.9), 6)


def _clean(reference: float, metric: str, rng: random.Random) -> float:
    if rng.random() < 0.7:
        return reference
    return round(reference + rng.choice([-1, 1]) * tolerance(metric, reference) * rng.uniform(0.05, 0.6), 6)


def build(path: Path, plan: Plan) -> Built:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    rng = random.Random(SEED)
    gaps = sum(n for _, _, n in plan)

    # One row per (case, metric) of a product, in turn: no pair is used twice.
    slot = {code: 0 for code in CODES}

    def next_slot(code: str) -> tuple[str, str]:
        metrics = PRODUCTS[code][2]
        i = slot[code]
        slot[code] += 1
        return f"{code}-{i // len(metrics) + 1:03d}", metrics[i % len(metrics)]

    rows = []   # (case, metric, reference, candidate, category or None)
    for category, code, count in plan:
        for _ in range(count):
            case, metric = next_slot(code)
            low, high = TOLERANCES[metric][2]
            reference = round(rng.uniform(low, high) * rng.choice([-1, 1]), 4)
            if category == "numerical_noise":
                candidate = _noise(reference, metric, rng)
            elif category == "new_regression" and rng.random() < 0.1:
                candidate = None                       # failed to price
            else:
                candidate = _shift(reference, metric, rng)
            rows.append((case, metric, reference, candidate, (category, code)))
    for i in range(gaps * CLEAN_PER_GAP):
        case, metric = next_slot(CODES[i % len(CODES)])
        low, high = TOLERANCES[metric][2]
        reference = round(rng.uniform(low, high) * rng.choice([-1, 1]), 4)
        rows.append((case, metric, reference, _clean(reference, metric, rng), None))
    rng.shuffle(rows)

    truth = {}
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO tolerances VALUES (?, ?, ?)", [(m, a, r) for m, (a, r, _) in TOLERANCES.items()])
        for code in CODES:
            name, criticality, metrics = PRODUCTS[code]
            for n in range(1, (slot[code] - 1) // len(metrics) + 2):
                con.execute("INSERT INTO test_cases VALUES (?, ?, ?, ?, ?)",
                            (f"{code}-{n:03d}", code, name, criticality, f"synthetic {code} {n}"))
            con.execute("INSERT INTO release_notes VALUES (?, ?, ?, ?)",
                        (f"RN-S-{code}", CANDIDATE, code, f"{name}: announced change of the results."))
            con.execute("INSERT INTO tickets VALUES (?, ?, 'open', ?, ?, NULL)",
                        (f"PRC-S-{code}", code, f"{name}: known defect", f"Known defect of {name}."))
        con.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?, '2026-11-10')",
                    (CAMPAIGN_ID, f"Synthetic, {gaps} gaps", REFERENCE, CANDIDATE))
        for result_id, (case, metric, reference, candidate, planted) in enumerate(rows, start=1):
            con.execute("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?)",
                        (result_id, CAMPAIGN_ID, case, metric, reference, candidate))
            if planted:
                category, code = planted
                evidence = {"expected_change": f"RN-S-{code}", "known_defect": f"PRC-S-{code}"}.get(category)
                truth[result_id] = (category, evidence)
        con.commit()
    finally:
        con.close()
    return Built(path, plan, gaps, len(rows), truth)


class SyntheticJudge:
    """A perfect model by default: answers the planted truth, never reads the prompt. Counts its calls."""

    def __init__(self, truth: dict[int, tuple[str, str | None]], answer=None, summary="The release was triaged."):
        self.truth, self._answer, self.summary = truth, answer, summary
        self.classify_calls = 0
        self.summarize_calls = 0
        self.usage = []

    async def classify(self, gap, evidence, campaign):
        self.classify_calls += 1
        self.usage.append({"prompt_tokens": 0, "output_tokens": 0})
        if self._answer:
            return self._answer(gap, evidence)
        category, cited = self.truth[gap["result_id"]]
        return {"reasoning": "synthetic", "category": category, "evidence_id": cited}

    async def summarize(self, facts):
        self.summarize_calls += 1
        return self.summary


if __name__ == "__main__":
    target, size = Path(sys.argv[1]), int(sys.argv[2])
    built = build(target, profile(sys.argv[3] if len(sys.argv) > 3 else "mixed", size))
    print(f"built {built.path}: {built.gaps} gaps planted, {built.results} results")
