"""Run the CrewAI version on one campaign: start, pause for a human if needed, resume.

Same contract as runner.py (the LangGraph version): start with a campaign id,
or resume a waiting run with the reviewer's answer. The flow's state is saved
by CrewAI's SQLite persistence when it pauses; `resume` restores it from
another process with the same thread id, and opens no data session at all.
"""
import json
from pathlib import Path
from typing import Callable

from crewai.flow.async_feedback import HumanFeedbackPending
from crewai.flow.persistence import SQLiteFlowPersistence

from ..trace import Tracer
from .bank import open_bank
from .flow import TriageFlow


def _as_text(answer) -> str:
    # CrewAI's resume takes a string. The answer travels as JSON and is read by code.
    return answer if isinstance(answer, str) else json.dumps(answer)


def run(db_path: Path, llm, thread: str, *, campaign_id: int | None = None, resume=None,
        persistence: Path, trace_path: Path, human: Callable[[dict], dict] | None = None) -> dict:
    """Start (campaign_id) or resume (resume=answer) a run. Returns the state, with
    "pending" (the question) when the flow is waiting for a human and `human` is None."""
    persistence.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteFlowPersistence(str(persistence))
    tracers = {"tracer": Tracer(trace_path, thread, llm), "tool_tracer": Tracer(trace_path, thread)}
    calls = []
    if resume is None:
        with open_bank(db_path) as bank:
            flow = TriageFlow(llm=llm, bank=bank, persistence=store, **tracers)
            result = flow.kickoff(inputs={"id": thread, "campaign_id": campaign_id})
            calls = list(bank.calls)
    else:
        flow = TriageFlow.from_pending(thread, store, llm=llm, **tracers)
        result = flow.resume(_as_text(resume))
    while isinstance(result, HumanFeedbackPending) and human is not None:
        answer = human(flow.state.pending)
        flow = TriageFlow.from_pending(thread, store, llm=llm, **tracers)
        result = flow.resume(_as_text(answer))
    state = {k: v for k, v in flow.state.model_dump().items() if v is not None}
    state["bank_calls"] = calls
    return state
