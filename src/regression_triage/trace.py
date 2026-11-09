"""One JSON line per node execution: what went in, what came out, how long, how many tokens.

Home-made observability, no external service: the trace is a local file that
can be read, diffed and kept with the decision. A node that pauses for a human
is recorded as "interrupted", and recorded again when it resumes.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from langgraph.errors import GraphInterrupt
except ImportError:   # the CrewAI version (crew/) runs in an environment without LangGraph
    class GraphInterrupt(Exception):
        """Never raised: stands in for LangGraph's pause when LangGraph is not installed."""

MAX_CHARS = 600


def short(value) -> object:
    """A summary of a value small enough for one trace line."""
    if isinstance(value, list):
        text = json.dumps(value, default=str, ensure_ascii=False)
        return value if len(text) <= 5 * MAX_CHARS else {"items": len(value), "first": short(value[0]) if value else None}
    if isinstance(value, dict):
        text = json.dumps(value, default=str, ensure_ascii=False)
        # Too long as a whole: keep every key, shorten each value.
        return value if len(text) <= MAX_CHARS else {k: short(v) for k, v in value.items()}
    if isinstance(value, str) and len(value) > MAX_CHARS:
        return value[:MAX_CHARS] + f"… ({len(value)} chars)"
    return value


def sizes(state: dict) -> dict:
    """What a node received, in brief: scalars as they are, collections by size."""
    return {k: (f"{len(v)} items" if isinstance(v, (list, dict)) else short(v)) for k, v in state.items()}


class Tracer:
    def __init__(self, path: Path, run_id: str, judge=None):
        self.path, self.run_id, self.judge = Path(path), run_id, judge
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _tokens(self) -> dict | None:
        usage = getattr(self.judge, "usage", None)
        if not usage:
            return None
        drained = list(usage)
        usage.clear()
        return {"calls": len(drained),
                "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in drained),
                "output_tokens": sum(u.get("output_tokens") or 0 for u in drained)}

    def write(self, node: str, status: str, ms: float, inputs: dict, output=None, error: str | None = None):
        line = {"run_id": self.run_id, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "node": node, "status": status, "ms": round(ms, 1),
                "input": sizes(inputs),
                "output": {k: short(v) for k, v in (output or {}).items()},
                "tokens": self._tokens()}
        if error:
            line["error"] = error
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, default=str, ensure_ascii=False) + "\n")

    def wrap(self, node: str, fn):
        async def traced(state):
            started = time.monotonic()
            try:
                output = await fn(state)
            except GraphInterrupt:
                self.write(node, "interrupted", (time.monotonic() - started) * 1000, state)
                raise
            except Exception as error:
                self.write(node, "error", (time.monotonic() - started) * 1000, state,
                           error=f"{type(error).__name__}: {error}")
                raise
            self.write(node, "ok", (time.monotonic() - started) * 1000, state, output)
            return output
        traced.__name__ = node
        return traced


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
