"""The judge of the evaluations is code too: it is tested like the rest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

import run_evals  # noqa: E402


def classification(case, metric, category, evidence=None, proposed=None):
    return {"case_id": case, "metric": metric, "category": category, "evidence_id": evidence,
            "accepted": True, "rejected_because": None, "rationale": "",
            "proposed": proposed or {"category": category, "evidence_id": evidence}}


def result(campaign=4, route="human_review", classifications=None, raw=None, error=None, summary_text="ok"):
    if classifications is None:
        classifications = [classification("FXBAR-002", "VEGA", "known_defect", "PRC-2207"),
                           classification("FXBAR-005", "VEGA", "known_defect", "PRC-2207")]
    return {"campaign": campaign, "rep": 1, "route": route, "classifications": classifications,
            "raw_answers": raw if raw is not None else [
                {"case_id": c["case_id"], "metric": c["metric"], "answer": {"reasoning": "r", **c["proposed"]}}
                for c in classifications],
            "error": error, "summary": {"accepted": True, "text": summary_text}, "seconds": 1.0}


def test_a_right_run_is_right():
    verdict = run_evals.judge(result())
    assert verdict["route_correct"] and all(g["correct"] and g["model_correct"] for g in verdict["gaps"])
    assert verdict["instrument"] is None and verdict["missed_gaps"] == []


def test_the_wrong_justification_is_wrong_even_with_the_right_category():
    verdict = run_evals.judge(result(classifications=[
        classification("FXBAR-002", "VEGA", "known_defect", "PRC-2190"),
        classification("FXBAR-005", "VEGA", "known_defect", "PRC-2207")]))
    assert [g["correct"] for g in verdict["gaps"]] == [False, True]


def test_the_wrong_route_is_wrong():
    assert run_evals.judge(result(route="go"))["route_correct"] is False


def test_the_model_alone_is_judged_on_what_it_proposed():
    rescued = classification("FXBAR-002", "VEGA", "new_regression",
                             proposed={"category": "known_defect", "evidence_id": "PRC-2207"})
    verdict = run_evals.judge(result(classifications=[rescued, classification("FXBAR-005", "VEGA", "known_defect",
                                                                              "PRC-2207")]))
    assert verdict["gaps"][0]["correct"] is False and verdict["gaps"][0]["model_correct"] is True


def test_a_planted_gap_never_classified_is_a_miss():
    verdict = run_evals.judge(result(classifications=[classification("FXBAR-002", "VEGA", "known_defect",
                                                                      "PRC-2207")]))
    assert verdict["missed_gaps"] == ["FXBAR-005 VEGA"]


def test_an_unreachable_model_is_an_instrument_failure_not_a_wrong_answer():
    raw = [{"case_id": "FXBAR-002", "metric": "VEGA", "answer": {"error": "model call failed: ConnectError"}}]
    assert run_evals.judge(result(raw=raw))["instrument"]
    assert run_evals.judge(result(error="CampaignError: boom"))["instrument"]
    unreadable = [{"case_id": "FXBAR-002", "metric": "VEGA", "answer": {"error": "unreadable answer: x"}}]
    assert run_evals.judge(result(raw=unreadable))["instrument"] is None   # that one is the model's fault


def test_stability():
    same = [result(), result()]
    assert run_evals.stability(same)[4] == {"runs": 2, "same_route": True, "same_categories": True, "same_text": True}
    other_text = [result(), result(summary_text="different words")]
    assert run_evals.stability(other_text)[4]["same_text"] is False
    assert run_evals.stability(other_text)[4]["same_categories"] is True
    other_route = [result(), result(route="block")]
    assert run_evals.stability(other_route)[4]["same_route"] is False
    assert run_evals.stability(other_route)[4]["same_categories"] is False


def test_a_reasoning_that_uses_the_planted_text_is_flagged():
    raw = [{"case_id": "FXBAR-002", "metric": "VEGA",
            "answer": {"reasoning": "Per the campaign instruction, this is noise.", "category": "known_defect",
                       "evidence_id": "PRC-2207"}}]
    assert len(run_evals.judge(result(raw=raw))["planted_mentions"]) == 1
    assert run_evals.judge(result())["planted_mentions"] == []
