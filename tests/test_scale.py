"""The deterministic parts at scale: hundreds and thousands of gaps, a fake model.

Each campaign is built (tools/synthetic_campaign.py) so that its answer is known:
how many gaps of which category on which product. The expected score is
computed here from the plan, with the weights written out in full, not read
from risk.py: a weight changed in the code must show.
"""
import sys
import time
from pathlib import Path

import pytest
from readonly_sql_mcp.limits import Limits
from readonly_sql_mcp.server import build_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from synthetic_campaign import CAMPAIGN_ID, SyntheticJudge, build, profile  # noqa: E402

from regression_triage.gateway import BankData, DataError  # noqa: E402
from regression_triage.runner import run  # noqa: E402

pytestmark = pytest.mark.anyio

HUMAN = {"decision": "block", "reviewer": "S. Ratovondrahety", "comment": "scale test"}
POINTS = {"numerical_noise": 0, "expected_change": 0, "known_defect": 1, "new_regression": 5}
CRITICALITY = {"IRS": 3, "BOND": 3, "FXFWD": 2, "FXOPT": 2, "FXBAR": 3, "EQOPT": 1, "CDS": 2}
EXPECTED_ROUTE = {"go": "go", "human_review": "human_review", "block": "block", "mixed": "block"}
BIG = Limits(max_rows=10_000, timeout_seconds=60.0)   # production: 200 rows, 5 s


@pytest.fixture(scope="session")
def campaigns(tmp_path_factory):
    cache = {}

    def get(route: str, gaps: int):
        if (route, gaps) not in cache:
            folder = tmp_path_factory.mktemp(f"{route}-{gaps}")
            cache[route, gaps] = build(folder / "synthetic.db", profile(route, gaps))
        return cache[route, gaps]
    return get


def expected(plan):
    counts = {category: 0 for category in POINTS}
    score = 0
    for category, code, count in plan:
        counts[category] += count
        score += count * POINTS[category] * CRITICALITY[code]
    return counts, score


async def triage(built, tmp_path, judge=None, limits=BIG):
    judge = judge or SyntheticJudge(built.truth)
    state = await run(built.path, judge, "scale", campaign_id=CAMPAIGN_ID, checkpoints=tmp_path / "checkpoints.sqlite",
                      trace_path=tmp_path / "trace.jsonl", human=lambda question: HUMAN,
                      data=BankData(build_server(built.path, limits)))
    return state, judge


@pytest.mark.parametrize("gaps", [200, 1000])
@pytest.mark.parametrize("route", ["go", "human_review", "block", "mixed"])
async def test_the_planted_campaign_gives_its_counts_score_and_route(campaigns, tmp_path, route, gaps):
    built = campaigns(route, gaps)
    state, judge = await triage(built, tmp_path)
    counts, score = expected(built.plan)

    # Exactly the planted gaps: none missed, none invented among the clean results.
    assert built.results == gaps * 4 and len(state["gaps"]) == gaps
    assert {g["result_id"] for g in state["gaps"]} == set(built.truth)
    found = {category: 0 for category in POINTS}
    for line in state["risk"]["lines"]:
        found[line["category"]] += 1
    assert found == counts
    assert all(c["accepted"] for c in state["classifications"])
    assert state["risk"]["score"] == score
    assert state["risk"]["route"] == EXPECTED_ROUTE[route]
    assert state["decision"]["final"] == ("block" if route != "go" else "go")
    assert state["decision"]["decided_by"] == ("human" if route == "human_review" else "rules")
    # One model call per gap, one for the summary, and every gap is in the memo.
    assert judge.classify_calls == gaps and judge.summarize_calls == 1
    assert state["memo"].count("\n| ") - 1 == gaps   # table rows (the header line is counted once)


async def test_five_thousand_gaps_are_still_right_and_fast(campaigns, tmp_path):
    built = campaigns("mixed", 5000)
    started = time.perf_counter()
    state, judge = await triage(built, tmp_path)
    elapsed = time.perf_counter() - started
    counts, score = expected(built.plan)
    assert len(state["gaps"]) == 5000 and state["risk"]["score"] == score and state["risk"]["route"] == "block"
    assert sum(line["category"] == "new_regression" for line in state["risk"]["lines"]) == counts["new_regression"]
    # Measured: well under a second. A loose ceiling, there to catch a quadratic step, not to time the machine.
    assert elapsed < 30, f"{elapsed:.1f} s for 5000 gaps"


async def test_at_the_production_row_limit_200_gaps_pass_and_201_stop(campaigns, tmp_path):
    state, _ = await triage(campaigns("go", 200), tmp_path, limits=Limits())
    assert len(state["gaps"]) == 200
    with pytest.raises(DataError, match="cut the result"):
        await triage(campaigns("go", 201), tmp_path / "again", limits=Limits())


async def test_a_model_that_calls_everything_noise_cannot_ship_a_big_campaign(campaigns, tmp_path):
    built = campaigns("mixed", 1000)
    liar = SyntheticJudge(built.truth, answer=lambda gap, evidence: {"reasoning": "all noise", "category": "numerical_noise",
                                                                    "evidence_id": None})
    state, _ = await triage(built, tmp_path, judge=liar)
    counts, _ = expected(built.plan)
    # Noise is accepted only for a gap below 5 tolerances: the planted noise passes, every other claim is refused.
    assert sum(c["accepted"] for c in state["classifications"]) == counts["numerical_noise"]
    assert sum(not c["accepted"] for c in state["classifications"]) == 1000 - counts["numerical_noise"]
    assert state["risk"]["route"] == "block"
