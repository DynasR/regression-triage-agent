"""Run the graph on one campaign: start, pause for a human if needed, resume.

The checkpointer saves the state after every node. When the graph pauses for
a human review, the run can stop there; `resume` picks it up later, from
another process, with the same thread id. The checkpoint file holds the
agent's own working state (gaps, categories, score), not a copy of the
bank's database.
"""
from pathlib import Path
from typing import Callable

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .gateway import BankData
from .graph import build_graph
from .trace import Tracer


def _unwrap(error: BaseException) -> BaseException:
    # Errors raised inside the MCP session come back wrapped in exception groups.
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


async def run(db_path: Path, judge, thread_id: str, *, campaign_id: int | None = None, resume=None,
              checkpoints: Path, trace_path: Path, human: Callable[[dict], dict] | None = None,
              data=None) -> dict:
    """Start (campaign_id) or resume (resume=answer) a run. Returns the state, with
    "__interrupt__" when the graph is waiting for a human and `human` is None."""
    checkpoints.parent.mkdir(parents=True, exist_ok=True)
    tracer = Tracer(trace_path, thread_id, judge)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoints)) as saver:
            async with (data or BankData.stdio(db_path)) as bank:
                graph = build_graph(bank, judge, tracer, saver)
                if resume is not None:
                    state = await graph.ainvoke(Command(resume=resume), config)
                else:
                    state = await graph.ainvoke({"campaign_id": campaign_id}, config)
                while "__interrupt__" in state and human is not None:
                    answer = human(state["__interrupt__"][0].value)
                    state = await graph.ainvoke(Command(resume=answer), config)
                return state
    except BaseExceptionGroup as group:
        raise _unwrap(group) from group
