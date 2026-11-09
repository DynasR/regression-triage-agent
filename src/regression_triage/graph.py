"""The triage graph.

    load_campaign -> compute_gaps -> [gaps?] -> classify_gaps -> score_risk -> [route]
                                       \\-- none ------------------^          go | block | human_review
                                                                                       \\-> report

The code computes, the model judges, no number is invented:
- load_campaign, compute_gaps  deterministic, through the MCP server (scoped)
- classify_gaps                the model, one gap at a time, its answer checked by code
- score_risk                   deterministic
- go / block / human_review    the route; human_review pauses the graph (interrupt)
                               until a person answers, then resumes
- report                       the memo: numbers from the state, summary from the model

Each node receives only what it needs: a scoped data handle for the three
nodes that read data (see gateway.SCOPES), the model for the two that need
it, nothing for the others.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import report as memo_writer
from .campaign import evidence_for, fetch_evidence, find_gaps, load_campaign
from .risk import check_classification, check_human_answer, score


class TriageState(TypedDict, total=False):
    campaign_id: int
    campaign: dict
    gaps: list[dict]
    classifications: list[dict]
    risk: dict
    decision: dict
    summary: dict
    memo: str


def after_gaps(state: TriageState) -> str:
    return "classify_gaps" if state["gaps"] else "score_risk"


def after_score(state: TriageState) -> str:
    return state["risk"]["route"]


def build_graph(data, judge, tracer=None, checkpointer=None):
    """data: an open gateway.BankData. judge: anything with async classify() and summarize()."""

    async def load_campaign_node(state: TriageState) -> dict:
        return {"campaign": await load_campaign(data.scoped("load_campaign"), state["campaign_id"])}

    async def compute_gaps_node(state: TriageState) -> dict:
        return {"gaps": await find_gaps(data.scoped("compute_gaps"), state["campaign_id"])}

    async def classify_gaps_node(state: TriageState) -> dict:
        evidence = await fetch_evidence(data.scoped("classify_gaps"), state["campaign"], state["gaps"])
        classifications = []
        for gap in state["gaps"]:
            own = evidence_for(gap, evidence)
            raw = await judge.classify(gap, own, state["campaign"])
            classifications.append(check_classification(raw, gap, own))
        return {"classifications": classifications}

    async def score_risk_node(state: TriageState) -> dict:
        return {"risk": score(state["gaps"], state.get("classifications", []))}

    async def go_node(state: TriageState) -> dict:
        return {"decision": {"route": "go", "final": "go", "decided_by": "rules"}}

    async def block_node(state: TriageState) -> dict:
        return {"decision": {"route": "block", "final": "block", "decided_by": "rules"}}

    async def human_review_node(state: TriageState) -> dict:
        question = {
            "question": f"Ship release {state['campaign']['candidate_release']}?",
            "score": state["risk"]["score"],
            "gaps": [{k: line[k] for k in ("case_id", "metric", "category", "criticality", "points")}
                     for line in state["risk"]["lines"]],
            "answer_with": {"decision": "go | block", "reviewer": "your name", "comment": "why"},
        }
        # On resume the node runs again from the top, and each interrupt() returns
        # the answers given so far, in order: an invalid answer asks again.
        while True:
            answer = interrupt(question)
            problem = check_human_answer(answer)
            if problem is None:
                break
            question = {**question, "error": problem}
        return {"decision": {"route": "human_review", "final": answer["decision"], "decided_by": "human",
                             "reviewer": answer["reviewer"].strip(), "comment": str(answer.get("comment", ""))}}

    async def report_node(state: TriageState) -> dict:
        given = memo_writer.facts(state)
        summary = memo_writer.check_summary(await judge.summarize(given), given)
        state = {**state, "summary": summary}
        return {"summary": summary, "memo": memo_writer.memo(state)}

    nodes = {
        "load_campaign": load_campaign_node, "compute_gaps": compute_gaps_node,
        "classify_gaps": classify_gaps_node, "score_risk": score_risk_node,
        "go": go_node, "block": block_node, "human_review": human_review_node, "report": report_node,
    }
    graph = StateGraph(TriageState)
    for name, fn in nodes.items():
        graph.add_node(name, tracer.wrap(name, fn) if tracer else fn)
    graph.add_edge(START, "load_campaign")
    graph.add_edge("load_campaign", "compute_gaps")
    graph.add_conditional_edges("compute_gaps", after_gaps, ["classify_gaps", "score_risk"])
    graph.add_edge("classify_gaps", "score_risk")
    graph.add_conditional_edges("score_risk", after_score, ["go", "block", "human_review"])
    for name in ("go", "block", "human_review"):
        graph.add_edge(name, "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)
