"""The whole CrewAI version, with a fake model: routing, the human pause, the checks, trace, memo.

The same scenarios as tests/test_graph.py, through a crew and a flow instead of a graph."""
import pytest

from fake_llm import FakeLLM
from regression_triage import trace
from regression_triage.crew.flow import AgentFailure
from regression_triage.crew.runner import run
from regression_triage.data import CAMPAIGNS

HUMAN = {"decision": "block", "reviewer": "S. Ratovondrahety", "comment": "wait for the fix"}


def triage(bank_db, tmp_path, campaign_id, llm, human=None, thread="t1", resume=None):
    return run(bank_db, llm, thread, campaign_id=campaign_id, resume=resume,
               persistence=tmp_path / "flows.sqlite", trace_path=tmp_path / "trace.jsonl", human=human)


@pytest.mark.parametrize("campaign", CAMPAIGNS, ids=lambda c: f"C{c.id}")
def test_a_perfect_model_reaches_the_expected_route(bank_db, tmp_path, campaign):
    state = triage(bank_db, tmp_path, campaign.id, FakeLLM(campaign_id=campaign.id), human=lambda q: HUMAN)
    assert state["risk"]["route"] == campaign.decision
    expected_final = HUMAN["decision"] if campaign.decision == "human_review" else campaign.decision
    assert state["decision"]["final"] == expected_final
    assert all(c["accepted"] for c in state["classifications"])
    assert len(state["classifications"]) == len(state["gaps"])


def test_no_gap_means_the_matcher_is_never_asked(bank_db, tmp_path):
    llm = FakeLLM(campaign_id=1)
    state = triage(bank_db, tmp_path, 1, llm)
    assert state["decision"]["final"] == "go"
    assert "Evidence matcher" not in {c["agent"] for c in llm.calls}


def test_human_review_pauses_and_resumes_with_the_answer(bank_db, tmp_path):
    state = triage(bank_db, tmp_path, 4, FakeLLM(campaign_id=4))
    assert "decision" not in state
    assert state["pending"]["question"] == "Ship release 7.1.1?" and state["pending"]["score"] == 6

    state = triage(bank_db, tmp_path, None, FakeLLM(campaign_id=4), resume=HUMAN)
    assert state["decision"] == {"route": "human_review", "final": "block", "decided_by": "human",
                                 "reviewer": "S. Ratovondrahety", "comment": "wait for the fix"}
    assert "S. Ratovondrahety" in state["memo"]


@pytest.mark.parametrize("bad", ["go", "ship it", {"decision": "ship it", "reviewer": "S"},
                                 {"decision": "go", "reviewer": " "}, {"decision": "go"}])
def test_an_invalid_human_answer_is_asked_again(bank_db, tmp_path, bad):
    triage(bank_db, tmp_path, 4, FakeLLM(campaign_id=4))
    state = triage(bank_db, tmp_path, None, FakeLLM(campaign_id=4), resume=bad)
    assert "decision" not in state and "error" in state["pending"]
    state = triage(bank_db, tmp_path, None, FakeLLM(campaign_id=4), resume={"decision": "go", "reviewer": "S"})
    assert state["decision"]["final"] == "go" and state["decision"]["reviewer"] == "S"


def test_the_human_answer_never_goes_through_the_model(bank_db, tmp_path):
    # CrewAI can turn a free-text answer into an outcome with a model (emit=...). Not here.
    triage(bank_db, tmp_path, 4, FakeLLM(campaign_id=4))
    llm = FakeLLM(campaign_id=4)
    triage(bank_db, tmp_path, None, llm, resume=HUMAN)
    assert [c["agent"] for c in llm.calls] == ["Release manager"]


def test_the_pause_survives_the_process_and_resuming_reads_nothing(bank_db, tmp_path):
    triage(bank_db, tmp_path, 6, FakeLLM(campaign_id=6), thread="kept")
    state = triage(bank_db, tmp_path, None, FakeLLM(campaign_id=6), thread="kept", resume=HUMAN)
    assert state["decision"]["decided_by"] == "human"
    assert state["bank_calls"] == []


def test_an_analyst_that_never_calls_its_tool_stops_the_run_it_never_ships(bank_db, tmp_path):
    lazy = FakeLLM(campaign_id=2, analyst_calls_tool=False)
    with pytest.raises(AgentFailure, match="never called"):
        triage(bank_db, tmp_path, 2, lazy)


def test_the_analyst_answer_is_the_tool_result_not_the_model_text(bank_db, tmp_path):
    state = triage(bank_db, tmp_path, 7, FakeLLM(campaign_id=7))
    assert state["analyst_output"].startswith("Campaign: ")
    assert "ship it" not in state["analyst_output"]
    assert "Gap 1:" in state["analyst_output"] and "size: LARGE" in state["analyst_output"]


def test_a_model_that_obeys_the_planted_instruction_cannot_ship_campaign_8(bank_db, tmp_path):
    obedient = FakeLLM(campaign_id=8, answer=lambda gap: {"reasoning": "the ticket says so",
                                                         "category": "numerical_noise", "evidence_id": None})
    state = triage(bank_db, tmp_path, 8, obedient)
    assert state["decision"]["final"] == "block"
    assert all(not c["accepted"] for c in state["classifications"])


def test_a_matcher_that_answers_nothing_usable_blocks(bank_db, tmp_path):
    silent = FakeLLM(campaign_id=2, answers=lambda gaps: [])
    state = triage(bank_db, tmp_path, 2, silent)
    assert state["risk"]["route"] == "block"
    assert {c["category"] for c in state["classifications"]} == {"new_regression"}
    assert all(c["rejected_because"] == "no answer from the model" for c in state["classifications"])


def test_a_gap_left_out_of_the_answer_counts_as_a_regression(bank_db, tmp_path):
    # Campaign 3: answer every gap but the bond noise gap.
    llm = FakeLLM(campaign_id=3)
    full = llm._truth
    llm.answers = lambda gaps: [{"case_id": g["case_id"], "metric": g["metric"], **full(g)}
                                for g in gaps if not g["case_id"].startswith("BOND")]
    state = triage(bank_db, tmp_path, 3, llm)
    missing = [c for c in state["classifications"] if c["rejected_because"] == "no answer from the model"]
    assert len(missing) == 1 and missing[0]["category"] == "new_regression"


def test_an_answer_about_a_gap_that_does_not_exist_is_ignored(bank_db, tmp_path):
    llm = FakeLLM(campaign_id=6)
    full = llm._truth
    llm.answers = lambda gaps: ([{"case_id": "IRS-999", "metric": "PV", "reasoning": "x",
                                  "category": "numerical_noise", "evidence_id": None}]
                                + [{"case_id": g["case_id"], "metric": g["metric"], **full(g)} for g in gaps])
    state = triage(bank_db, tmp_path, 6, llm, human=lambda q: HUMAN)
    assert len(state["classifications"]) == len(state["gaps"])
    assert all(c["accepted"] for c in state["classifications"])


def test_a_summary_with_an_invented_number_is_replaced(bank_db, tmp_path):
    llm = FakeLLM(campaign_id=5, summary="Four bonds moved by 12.5 million euros, block the release.")
    state = triage(bank_db, tmp_path, 5, llm)
    assert state["summary"]["accepted"] is False and "12.5" in state["summary"]["unknown_numbers"]
    assert "12.5" not in state["memo"] and state["summary"]["rejected_text"] == llm.summary


def test_one_trace_line_per_step_with_time_and_tokens(bank_db, tmp_path):
    triage(bank_db, tmp_path, 4, FakeLLM(campaign_id=4))
    triage(bank_db, tmp_path, None, FakeLLM(campaign_id=4), resume=HUMAN)
    lines = trace.read(tmp_path / "trace.jsonl")
    assert [(line["node"], line["status"]) for line in lines] == [
        ("tool:campaign_gaps", "ok"), ("task:gap_analyst", "ok"), ("tool:evidence", "ok"),
        ("task:evidence_matcher", "ok"), ("check_and_score", "ok"),
        ("human_review", "interrupted"), ("human_review", "ok"), ("task:release_manager", "ok"), ("report", "ok")]
    by_node = {line["node"]: line for line in lines}
    assert by_node["task:gap_analyst"]["tokens"]["calls"] == 1
    assert by_node["task:evidence_matcher"]["tokens"]["calls"] == 2   # the tool call, then the answer
    assert by_node["tool:campaign_gaps"]["tokens"] is None
    assert all(line["ms"] >= 0 and line["run_id"] == "t1" for line in lines)


def test_the_route_is_the_code_s_not_a_model_s(bank_db, tmp_path):
    # Campaign 7 blocks on two vega regressions; a summary that says "go" changes nothing.
    state = triage(bank_db, tmp_path, 7, FakeLLM(campaign_id=7, summary="Go: ship it."))
    assert state["decision"] == {"route": "block", "final": "block", "decided_by": "rules"}
    assert state["memo"].startswith("# Release decision: 7.4.0 — BLOCK")
