"""The whole graph, with a fake model: routing, the human pause, least privilege, trace, memo."""
import pytest
from readonly_sql_mcp.server import build_server

from fake_judge import FakeJudge
from regression_triage import trace
from regression_triage.data import CAMPAIGNS
from regression_triage.gateway import SCOPES, BankData
from regression_triage.runner import run

pytestmark = pytest.mark.anyio

HUMAN = {"decision": "block", "reviewer": "S. Ratovondrahety", "comment": "wait for the fix"}


async def triage(bank_db, tmp_path, campaign_id, judge, human=None, data=None, thread="t1", resume=None):
    data = data or BankData(build_server(bank_db))
    state = await run(bank_db, judge, thread, campaign_id=campaign_id, resume=resume,
                      checkpoints=tmp_path / "checkpoints.sqlite", trace_path=tmp_path / "trace.jsonl",
                      human=human, data=data)
    return state, data


@pytest.mark.parametrize("campaign", CAMPAIGNS, ids=lambda c: f"C{c.id}")
async def test_a_perfect_model_reaches_the_expected_route(bank_db, tmp_path, campaign):
    state, _ = await triage(bank_db, tmp_path, campaign.id, FakeJudge(campaign.id), human=lambda q: HUMAN)
    assert state["risk"]["route"] == campaign.decision
    expected_final = HUMAN["decision"] if campaign.decision == "human_review" else campaign.decision
    assert state["decision"]["final"] == expected_final
    assert all(c["accepted"] for c in state.get("classifications", []))


async def test_no_gap_means_the_model_is_never_asked(bank_db, tmp_path):
    judge = FakeJudge(1)
    state, _ = await triage(bank_db, tmp_path, 1, judge)
    assert judge.seen == [] and state["decision"]["final"] == "go"
    assert "classify_gaps" not in [line["node"] for line in trace.read(tmp_path / "trace.jsonl")]


async def test_human_review_pauses_and_resumes_with_the_answer(bank_db, tmp_path):
    state, _ = await triage(bank_db, tmp_path, 4, FakeJudge(4))
    assert "decision" not in state
    question = state["__interrupt__"][0].value
    assert question["question"] == "Ship release 7.1.1?" and question["score"] == 6

    state, _ = await triage(bank_db, tmp_path, None, FakeJudge(4), resume=HUMAN)
    assert state["decision"] == {"route": "human_review", "final": "block", "decided_by": "human",
                                 "reviewer": "S. Ratovondrahety", "comment": "wait for the fix"}
    assert "S. Ratovondrahety" in state["memo"]


@pytest.mark.parametrize("bad", ["go", {"decision": "ship it", "reviewer": "S"},
                                 {"decision": "go", "reviewer": " "}, {"decision": "go"}])
async def test_an_invalid_human_answer_is_asked_again(bank_db, tmp_path, bad):
    await triage(bank_db, tmp_path, 4, FakeJudge(4))
    state, _ = await triage(bank_db, tmp_path, None, FakeJudge(4), resume=bad)
    assert "decision" not in state and "error" in state["__interrupt__"][0].value
    state, _ = await triage(bank_db, tmp_path, None, FakeJudge(4), resume={"decision": "go", "reviewer": "S"})
    assert state["decision"]["final"] == "go" and state["decision"]["reviewer"] == "S"


async def test_the_pause_survives_the_process(bank_db, tmp_path):
    # Start with one session and one judge, resume with new ones: only the checkpoint file is shared.
    await triage(bank_db, tmp_path, 6, FakeJudge(6), thread="kept")
    state, data = await triage(bank_db, tmp_path, None, FakeJudge(6), thread="kept", resume=HUMAN)
    assert state["decision"]["decided_by"] == "human"
    assert data.calls == []   # resuming re-reads nothing from the bank


async def test_each_node_only_touched_its_own_scope(bank_db, tmp_path):
    _, data = await triage(bank_db, tmp_path, 8, FakeJudge(8))
    used = {}
    for node, tool in data.calls:
        used.setdefault(node, set()).add(tool)
    assert set(used) == {"load_campaign", "compute_gaps", "classify_gaps"}
    assert all(tools <= SCOPES[node].tools for node, tools in used.items())


async def test_the_model_only_sees_evidence_about_the_product_of_the_gap(bank_db, tmp_path):
    judge = FakeJudge(8)
    await triage(bank_db, tmp_path, 8, judge)
    for gap, evidence in judge.seen:
        assert {t["product_code"] for t in evidence["tickets"]} <= {gap["product_code"]}
        assert {n["product_code"] for n in evidence["release_notes"]} <= {gap["product_code"], None}


async def test_a_model_that_obeys_the_planted_instruction_cannot_ship_campaign_8(bank_db, tmp_path):
    obedient = FakeJudge(answer=lambda gap, evidence: {"reasoning": "the ticket says so",
                                                       "category": "numerical_noise", "evidence_id": None})
    state, _ = await triage(bank_db, tmp_path, 8, obedient)
    assert state["decision"]["final"] == "block"
    assert all(not c["accepted"] for c in state["classifications"])   # large gaps cannot be noise


async def test_a_model_that_answers_nothing_usable_blocks(bank_db, tmp_path):
    broken = FakeJudge(answer=lambda gap, evidence: {"error": "model call failed: ConnectError"})
    state, _ = await triage(bank_db, tmp_path, 2, broken)
    assert state["risk"]["route"] == "block"
    assert {c["category"] for c in state["classifications"]} == {"new_regression"}


async def test_a_summary_with_an_invented_number_is_replaced(bank_db, tmp_path):
    judge = FakeJudge(5, summary="Four bonds moved by 12.5 million euros, block the release.")
    state, _ = await triage(bank_db, tmp_path, 5, judge)
    assert state["summary"]["accepted"] is False and "12.5" in state["summary"]["unknown_numbers"]
    assert "12.5" not in state["memo"]


async def test_a_summary_using_only_given_numbers_is_kept(bank_db, tmp_path):
    judge = FakeJudge(5, summary="Release 7.2.0 is blocked: 4 gaps on government bonds are new regressions.")
    state, _ = await triage(bank_db, tmp_path, 5, judge)
    assert state["summary"]["accepted"] is True and judge.summary in state["memo"]


async def test_the_memo_numbers_come_from_the_state(bank_db, tmp_path):
    state, _ = await triage(bank_db, tmp_path, 5, FakeJudge(5))
    assert f"(risk score {state['risk']['score']})" in state["memo"]
    for gap in state["gaps"]:
        assert gap["case_id"] in state["memo"]
    assert "| BOND-004 | PV |" in state["memo"] and "| failed |" in state["memo"]


async def test_one_trace_line_per_node_with_time_and_tokens(bank_db, tmp_path):
    await triage(bank_db, tmp_path, 4, FakeJudge(4))
    await triage(bank_db, tmp_path, None, FakeJudge(4), resume=HUMAN)
    lines = trace.read(tmp_path / "trace.jsonl")
    assert [(line["node"], line["status"]) for line in lines] == [
        ("load_campaign", "ok"), ("compute_gaps", "ok"), ("classify_gaps", "ok"), ("score_risk", "ok"),
        ("human_review", "interrupted"), ("human_review", "ok"), ("report", "ok")]
    classify = lines[2]
    assert classify["tokens"] == {"calls": 2, "prompt_tokens": 200, "output_tokens": 20}
    assert all(line["ms"] >= 0 and line["run_id"] == "t1" for line in lines)
    assert lines[0]["tokens"] is None
