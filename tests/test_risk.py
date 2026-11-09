"""What the model says is checked by code, then scored by code, then routed by code."""
import pytest

from regression_triage.data import CAMPAIGNS
from regression_triage.risk import (BLOCK_FROM, HUMAN_REVIEW_FROM, NOISE_MAX_RATIO, check_classification,
                                    route, score)


def gap(result_id=1, product_code="IRS", criticality="high", ratio=1.5, status="out_of_tolerance"):
    return {"result_id": result_id, "case_id": f"{product_code}-001", "product_code": product_code,
            "criticality": criticality, "metric": "PV", "ratio": ratio, "status": status}


EVIDENCE = {
    "release_notes": [{"id": "RN-1", "product_code": "IRS", "text": "swaps change"},
                      {"id": "RN-2", "product_code": None, "text": "general"},
                      {"id": "RN-3", "product_code": "BOND", "text": "bonds change"}],
    "tickets": [{"id": "T-OPEN", "product_code": "IRS", "status": "open"},
                {"id": "T-FIXED", "product_code": "IRS", "status": "fixed"},
                {"id": "T-BOND", "product_code": "BOND", "status": "open"}],
}


def answer(category, evidence_id=None):
    return {"reasoning": "because", "category": category, "evidence_id": evidence_id}


# --- check_classification ----------------------------------------------------

@pytest.mark.parametrize("raw, category", [
    (answer("expected_change", "RN-1"), "expected_change"),
    (answer("expected_change", "RN-2"), "expected_change"),     # a general note may justify any product
    (answer("known_defect", "T-OPEN"), "known_defect"),
    (answer("numerical_noise"), "numerical_noise"),
    (answer("new_regression"), "new_regression"),
])
def test_a_justified_answer_is_accepted(raw, category):
    checked = check_classification(raw, gap(), EVIDENCE)
    assert (checked["category"], checked["accepted"], checked["rejected_because"]) == (category, True, None)


@pytest.mark.parametrize("raw, why", [
    (None, "no answer"),
    ("numerical_noise", "no answer"),
    ({"error": "model call failed: ConnectError"}, "no answer"),
    (answer("probably_fine"), "unknown category"),
    (answer("expected_change"), "release note"),
    (answer("expected_change", "RN-404"), "release note"),        # invented id
    (answer("expected_change", "T-OPEN"), "release note"),        # a ticket is not a release note
    (answer("expected_change", "RN-3"), "another product"),       # a note about bonds for a swap gap
    (answer("known_defect"), "open ticket"),
    (answer("known_defect", "T-FIXED"), "open ticket"),           # a fixed defect that comes back is a regression
    (answer("known_defect", "RN-1"), "open ticket"),
    (answer("known_defect", "T-BOND"), "another product"),
])
def test_an_unjustified_answer_becomes_a_new_regression(raw, why):
    checked = check_classification(raw, gap(), EVIDENCE)
    assert checked["category"] == "new_regression" and checked["accepted"] is False
    assert why in checked["rejected_because"]


@pytest.mark.parametrize("ratio, status, accepted", [
    (1.5, "out_of_tolerance", True),
    (NOISE_MAX_RATIO - 0.01, "out_of_tolerance", True),
    (NOISE_MAX_RATIO, "out_of_tolerance", False),
    (250.0, "out_of_tolerance", False),     # a large gap cannot be hidden as noise
    (None, "failed", False),                # nor a value that failed to price
    (None, "no_tolerance", False),
])
def test_noise_is_only_accepted_for_a_small_gap(ratio, status, accepted):
    checked = check_classification(answer("numerical_noise"), gap(ratio=ratio, status=status), EVIDENCE)
    assert checked["accepted"] is accepted
    assert checked["category"] == ("numerical_noise" if accepted else "new_regression")


def test_the_evidence_of_a_regression_is_dropped():
    assert check_classification(answer("new_regression", "RN-1"), gap(), EVIDENCE)["evidence_id"] is None


def test_what_the_model_proposed_is_kept_for_the_record():
    checked = check_classification(answer("known_defect", "T-FIXED"), gap(), EVIDENCE)
    assert checked["proposed"] == {"category": "known_defect", "evidence_id": "T-FIXED"}


# --- score and route --------------------------------------------------------

def classified(result_id, category):
    return {"result_id": result_id, "category": category}


def test_the_points_of_each_gap():
    gaps = [gap(1, criticality="high"), gap(2, criticality="medium"), gap(3, criticality="low"),
            gap(4, criticality="high"), gap(5, criticality="high")]
    found = score(gaps, [classified(1, "new_regression"), classified(2, "known_defect"),
                         classified(3, "new_regression"), classified(4, "expected_change"),
                         classified(5, "numerical_noise")])
    assert [line["points"] for line in found["lines"]] == [15, 2, 5, 0, 0]
    assert found["score"] == 22


def test_a_gap_left_unclassified_counts_as_a_new_regression():
    found = score([gap(1, criticality="low"), gap(2, criticality="low")], [classified(1, "numerical_noise")])
    assert found["lines"][1]["category"] == "new_regression" and found["score"] == 5


@pytest.mark.parametrize("criticality", [None, "unknown", "HIGH"])
def test_an_unknown_criticality_counts_as_the_highest(criticality):
    found = score([gap(1, criticality=criticality)], [classified(1, "new_regression")])
    assert found["lines"][0]["criticality"] == "high" and found["score"] == 15


def test_no_gap_scores_zero():
    assert score([], [])["score"] == 0


@pytest.mark.parametrize("points, expected", [
    (0, "go"), (HUMAN_REVIEW_FROM - 1, "go"), (HUMAN_REVIEW_FROM, "human_review"),
    (BLOCK_FROM - 1, "human_review"), (BLOCK_FROM, "block"), (100, "block"),
])
def test_route_thresholds(points, expected):
    assert route(points) == expected


def test_the_thresholds_are_these():
    # One known defect on a high-criticality product (3 points) needs a human;
    # one new regression on a medium one (10 points) blocks.
    assert (HUMAN_REVIEW_FROM, BLOCK_FROM, NOISE_MAX_RATIO) == (3, 10, 5.0)


@pytest.mark.parametrize("campaign", CAMPAIGNS, ids=lambda c: f"C{c.id}")
def test_the_ground_truth_of_each_campaign_leads_to_its_expected_decision(campaign):
    from regression_triage.data import PRODUCTS
    gaps = [gap(i, criticality=PRODUCTS[p.case.split("-")[0]][1]) for i, p in enumerate(campaign.planted)]
    truth = [classified(i, p.truth) for i, p in enumerate(campaign.planted)]
    assert score(gaps, truth)["route"] == campaign.decision
