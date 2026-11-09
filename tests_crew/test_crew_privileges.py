"""Least privilege in the crew: each agent has only its tool, each tool only its scopes."""
import pytest

from fake_llm import FakeLLM
from regression_triage.crew.bank import open_bank
from regression_triage.crew.crew import AGENT_TOOLS, build_report_crew, build_triage_crew
from regression_triage.crew.runner import run
from regression_triage.crew.tools import TOOL_SCOPES, BankView, make_tools
from regression_triage.gateway import AccessDenied


def test_the_agents_and_their_tools_are_exactly_these():
    # The README and docs/langgraph-vs-crewai.md draw this table.
    assert AGENT_TOOLS == {"gap_analyst": ("campaign_gaps",), "evidence_matcher": ("evidence",),
                           "release_manager": ()}
    assert TOOL_SCOPES == {"campaign_gaps": frozenset({"load_campaign", "compute_gaps"}),
                           "evidence": frozenset({"classify_gaps"})}


def test_the_built_crews_give_each_agent_only_its_tools():
    tools = make_tools(bank=None, campaign_id=4, case={})
    crew, _ = build_triage_crew(FakeLLM(), tools, case={})
    report = build_report_crew(FakeLLM(), "facts")
    agents = {a.role: a for a in crew.agents + report.agents}
    assert {role: sorted(t.name for t in a.tools or []) for role, a in agents.items()} == {
        "Gap analyst": ["campaign_gaps"], "Evidence matcher": ["evidence"], "Release manager": []}
    assert not any(a.allow_delegation for a in agents.values())


def test_the_gap_tool_takes_no_argument_and_answers_as_it_is():
    tool = make_tools(bank=None, campaign_id=4, case={})["campaign_gaps"]
    assert tool.args_schema.model_fields == {} and tool.result_as_answer is True


def test_a_tool_cannot_use_a_scope_that_is_not_its_own(bank_db):
    with open_bank(bank_db) as bank:
        with pytest.raises(AccessDenied):
            BankView(bank, "evidence").scoped("compute_gaps")
        with pytest.raises(AccessDenied):
            BankView(bank, "campaign_gaps").scoped("classify_gaps")
        assert bank.calls == []


def test_the_evidence_is_only_for_a_product_with_a_gap(bank_db):
    with open_bank(bank_db) as bank:
        case = {}
        tools = make_tools(bank, 4, case)
        with pytest.raises(AccessDenied):
            tools["evidence"]._run(product_code="FXBAR")   # no gap known yet
        tools["campaign_gaps"]._run()
        assert {g["product_code"] for g in case["gaps"]} == {"FXBAR"}
        with pytest.raises(AccessDenied):
            tools["evidence"]._run(product_code="CDS")
        text = tools["evidence"]._run(product_code="FXBAR")
        assert "PRC-2207" in text
        assert [node for node, _ in bank.calls] == ["load_campaign", "load_campaign", "compute_gaps",
                                                    "classify_gaps", "classify_gaps"]


def test_each_step_only_touched_its_own_scope(bank_db, tmp_path):
    state = run(bank_db, FakeLLM(campaign_id=8), "t", campaign_id=8, persistence=tmp_path / "f.sqlite",
                trace_path=tmp_path / "trace.jsonl")
    assert {node for node, _ in state["bank_calls"]} == {"load_campaign", "compute_gaps", "classify_gaps"}
    assert {tool for _, tool in state["bank_calls"]} == {"query"}
