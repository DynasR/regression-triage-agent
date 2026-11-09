"""The CrewAI flow around the crew: the code's steps, the route, the human review.

A crew alone runs its tasks in a fixed order and stops. Choosing a path from
a score, and pausing for days until a person answers, is the job of a CrewAI
Flow, the layer CrewAI provides for control around crews:

    triage (crew: gap analyst, evidence matcher; then code: check, score)
      -> route (code)
           go | block            -> decided by the rules
           human_review          -> @human_feedback: the flow pauses, its state is saved;
                                    resumed later, from another process, with the answer
      -> report (crew: release manager; then code: number check, memo)

The human answer is a string (CrewAI's resume takes nothing else); it is read
as JSON by code and checked like the graph's. CrewAI can also turn a free-text
answer into an outcome with a model (`emit=`); that is not used: the model
never reads the human's decision.
"""
import json
import time

from crewai.flow.async_feedback import HumanFeedbackPending
from crewai.flow.flow import Flow, listen, or_, router, start
from crewai.flow.human_feedback import human_feedback
from pydantic import BaseModel

from .. import report as memo_writer
from ..campaign import evidence_for, fetch_evidence
from ..risk import check_classification, check_human_answer, score
from .crew import build_report_crew, build_triage_crew, match_answers
from .tools import make_tools


class AgentFailure(Exception):
    """The crew did not do its job in a way that leaves the decision unknown. Nothing is shipped."""


class CrewState(BaseModel):
    id: str = ""
    campaign_id: int = 0
    campaign: dict | None = None
    gaps: list | None = None
    analyst_output: str | None = None
    answers: list | None = None          # what the matcher answered, before the code checked it
    matcher_error: str | None = None
    classifications: list | None = None
    risk: dict | None = None
    pending: dict | None = None          # the question, while a human is expected
    review_error: str | None = None
    decision: dict | None = None
    summary: dict | None = None
    memo: str | None = None


def parse_human_answer(text) -> tuple[dict | None, str | None]:
    """The reviewer's answer, read by code: (answer, None) or (None, what is wrong)."""
    try:
        answer = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None, "the answer must be JSON with decision and reviewer"
    problem = check_human_answer(answer)
    return (None, problem) if problem else (answer, None)


class PauseForReviewer:
    """CrewAI's asynchronous feedback provider: pause the flow and let the caller ask a person."""

    def request_feedback(self, context, flow):
        flow.trace_step("human_review", "interrupted", 0, {"question": context.method_output})
        raise HumanFeedbackPending(context=context)


class TriageFlow(Flow[CrewState]):
    def __init__(self, llm, bank=None, tracer=None, tool_tracer=None, **settings):
        super().__init__(suppress_flow_events=True, **settings)
        self._llm, self._bank, self._tracer, self._tool_tracer = llm, bank, tracer, tool_tracer

    def trace_step(self, node: str, status: str, ms: float, inputs: dict, output: dict | None = None):
        if self._tracer:
            self._tracer.write(node, status, ms, inputs, output)

    def _trace_tasks(self, tasks: dict) -> None:
        mark = [time.monotonic()]

        def done(name):
            def callback(output):
                now = time.monotonic()
                self.trace_step(f"task:{name}", "ok", (now - mark[0]) * 1000, {"agent": name}, {"answer": output.raw})
                mark[0] = now
            return callback
        for name, task in tasks.items():
            task.callback = done(name)

    @start()
    def triage(self):
        if self._bank is None:
            raise AgentFailure("no data session: a triage cannot start without the bank")
        case = {}
        tools = make_tools(self._bank, self.state.campaign_id, case, self._tool_tracer)
        crew, tasks = build_triage_crew(self._llm, tools, case)
        self._trace_tasks(tasks)
        try:
            crew.kickoff()
        except Exception as error:
            if "gaps" not in case or any("error" in call for call in getattr(self._llm, "calls", [])):
                raise   # the gaps are unknown, or the model itself is down: no decision
            # The matcher failed after the gaps were found: every gap stays unexplained.
            self.state.matcher_error = f"{type(error).__name__}: {error}"[:300]
        if "gaps" not in case:
            raise AgentFailure("the gap analyst never called its tool: the gaps are unknown, nothing is decided")

        started = time.monotonic()
        gaps = case["gaps"]
        self.state.campaign, self.state.gaps = case["campaign"], gaps
        self.state.analyst_output = tasks["gap_analyst"].output.raw if tasks["gap_analyst"].output else None
        matcher = tasks["evidence_matcher"].output
        parsed = getattr(matcher, "pydantic", None) if matcher else None
        self.state.answers = [a.model_dump() for a in parsed.answers] if parsed else []
        classifications = []
        if gaps:
            # The code fetches the evidence itself to check the answers: what the model
            # chose to read is not what the check relies on.
            scope = self._bank.scoped("classify_gaps")
            evidence = self._bank.run(lambda: fetch_evidence(scope, case["campaign"], gaps))
            answers = match_answers(gaps, self.state.answers)
            classifications = [check_classification(answers[g["result_id"]], g, evidence_for(g, evidence))
                               for g in gaps]
        self.state.classifications = classifications
        self.state.risk = score(gaps, classifications)
        self.trace_step("check_and_score", "ok", (time.monotonic() - started) * 1000,
                        {"gaps": gaps, "answers": self.state.answers},
                        {"classifications": classifications, "risk": self.state.risk})

    @router(triage)
    def route(self):
        return self.state.risk["route"]

    @listen(or_("go", "block"))
    def decided_by_rules(self):
        route = self.state.risk["route"]
        self.state.decision = {"route": route, "final": route, "decided_by": "rules"}

    @listen(or_("human_review", "ask_again"))
    @human_feedback(message='Ship this release? Answer with JSON: {"decision": "go" or "block", '
                            '"reviewer": "your name", "comment": "why"}', provider=PauseForReviewer())
    def ask_reviewer(self):
        question = {
            "question": f"Ship release {self.state.campaign['candidate_release']}?",
            "score": self.state.risk["score"],
            "gaps": [{k: line[k] for k in ("case_id", "metric", "category", "criticality", "points")}
                     for line in self.state.risk["lines"]],
            "answer_with": {"decision": "go | block", "reviewer": "your name", "comment": "why"},
        }
        if self.state.review_error:
            question["error"] = self.state.review_error
        self.state.pending = question
        return question

    @router(ask_reviewer)
    def record_answer(self):
        answer, problem = parse_human_answer(self.last_human_feedback.feedback)
        if problem:
            self.state.review_error = problem
            return "ask_again"
        self.state.pending, self.state.review_error = None, None
        self.state.decision = {"route": "human_review", "final": answer["decision"], "decided_by": "human",
                               "reviewer": answer["reviewer"].strip(), "comment": str(answer.get("comment", ""))}
        self.trace_step("human_review", "ok", 0, {"answer": answer}, {"decision": self.state.decision})
        return "decided_by_human"

    @listen(or_(decided_by_rules, "decided_by_human"))
    def report(self):
        state = self.state.model_dump()
        given = memo_writer.facts(state)
        crew = build_report_crew(self._llm, given)
        self._trace_tasks({"release_manager": crew.tasks[0]})
        try:
            text = crew.kickoff().raw
        except Exception:
            if any("error" in call for call in getattr(self._llm, "calls", [])):
                raise
            text = None
        started = time.monotonic()
        summary = memo_writer.check_summary(text, given)
        self.state.summary = summary
        self.state.memo = memo_writer.memo({**state, "summary": summary})
        self.trace_step("report", "ok", (time.monotonic() - started) * 1000, {"facts": given},
                        {"summary": summary})
        return self.state.memo
