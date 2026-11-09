"""Time the deterministic parts of the agent on synthetic campaigns, with a fake model.

No language model runs. For each size, a campaign is built (synthetic_campaign.py),
then the whole graph runs on it, the data going through a real MCP server (in
process, the same way the tests do it, so the process start-up is not counted).
Per size: a few timed runs (the fastest is kept), then one under tracemalloc for the memory
(tracemalloc slows Python down, so the two are never mixed).
The per-node times come from the trace file, whose clock ticks every 16 ms on Windows: read them to +/- 16 ms.

  SQL alone     the gap query run straight on SQLite, no MCP
  compute_gaps  the same query through MCP, plus the arithmetic of each gap
  classify      read the evidence, one (fake) model call per gap, check each answer
  score, report the points and the memo
  total         the whole run, checkpoints and trace file included
  judge calls   classifications + 1 summary; a real model would be paid for each

The server's row limit is raised for the measure (see --max-rows): with the
default 200 a campaign of more than 200 gaps is refused, and that is a finding.

Usage: python tools/bench.py [--sizes 200 1000 5000] [--profile mixed] [--max-rows 10000] [--repeat 3]
"""
import argparse
import asyncio
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synthetic_campaign import CAMPAIGN_ID, SyntheticJudge, build, profile  # noqa: E402

from readonly_sql_mcp.limits import Limits  # noqa: E402
from readonly_sql_mcp.server import build_server  # noqa: E402

from regression_triage import trace  # noqa: E402
from regression_triage.campaign import GAPS_SQL  # noqa: E402
from regression_triage.gateway import BankData  # noqa: E402
from regression_triage.runner import run  # noqa: E402

HUMAN = {"decision": "block", "reviewer": "bench", "comment": "bench"}


def sql_alone_ms(db: Path) -> float:
    con = sqlite3.connect(db)
    try:
        started = time.perf_counter()
        con.execute(GAPS_SQL.format(cid=CAMPAIGN_ID)).fetchall()
        return (time.perf_counter() - started) * 1000
    finally:
        con.close()


async def one_run(built, work: Path, max_rows: int) -> dict:
    judge = SyntheticJudge(built.truth)
    limits = Limits(max_rows=max_rows, timeout_seconds=60.0)
    for name in ("checkpoints.sqlite", "trace.jsonl"):
        (work / name).unlink(missing_ok=True)
    started = time.perf_counter()
    state = await run(built.path, judge, "bench", campaign_id=CAMPAIGN_ID, checkpoints=work / "checkpoints.sqlite",
                      trace_path=work / "trace.jsonl", human=lambda q: HUMAN,
                      data=BankData(build_server(built.path, limits)))
    total = time.perf_counter() - started
    nodes = {line["node"]: line["ms"] for line in trace.read(work / "trace.jsonl") if line["status"] == "ok"}
    return {"state": state, "judge": judge, "total_s": total, "nodes": nodes,
            "checkpoint_mb": (work / "checkpoints.sqlite").stat().st_size / 1e6,
            "trace_kb": (work / "trace.jsonl").stat().st_size / 1e3}


def measure(gaps: int, route: str, max_rows: int, repeat: int = 3) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        built = build(work / "synthetic.db", profile(route, gaps))
        # The fastest of `repeat` runs: the first one pays for imports and cold caches.
        timed = min((asyncio.run(one_run(built, work, max_rows)) for _ in range(repeat)),
                    key=lambda r: r["total_s"])
        tracemalloc.start()
        try:
            asyncio.run(one_run(built, work, max_rows))
            peak_mb = tracemalloc.get_traced_memory()[1] / 1e6
        finally:
            tracemalloc.stop()
        nodes = timed["nodes"]
        return {"gaps": built.gaps, "results": built.results, "route": timed["state"]["risk"]["route"],
                "sql_ms": sql_alone_ms(built.path), "compute_ms": nodes["compute_gaps"],
                "classify_ms": nodes.get("classify_gaps", 0.0), "score_ms": nodes["score_risk"],
                "report_ms": nodes["report"], "total_s": timed["total_s"], "peak_mb": peak_mb,
                "calls": timed["judge"].classify_calls + timed["judge"].summarize_calls,
                "checkpoint_mb": timed["checkpoint_mb"], "trace_kb": timed["trace_kb"],
                "memo_kb": len(timed["state"]["memo"]) / 1e3}


COLUMNS = [("gaps", "{:,}"), ("results", "{:,}"), ("route", "{}"), ("sql_ms", "{:.1f}"), ("compute_ms", "{:.0f}"),
           ("classify_ms", "{:.0f}"), ("score_ms", "{:.1f}"), ("report_ms", "{:.0f}"), ("total_s", "{:.2f}"),
           ("peak_mb", "{:.0f}"), ("calls", "{:,}"), ("checkpoint_mb", "{:.1f}"), ("trace_kb", "{:.1f}"),
           ("memo_kb", "{:.0f}")]
HEADERS = ["gaps", "results", "route", "SQL alone ms", "compute_gaps ms", "classify ms", "score ms", "report ms",
           "total s", "peak MB", "judge calls", "checkpoint MB", "trace KB", "memo KB"]


def table(rows: list[dict]) -> str:
    out = ["| " + " | ".join(HEADERS) + " |", "|" + "---:|" * len(HEADERS)]
    for row in rows:
        out.append("| " + " | ".join(fmt.format(row[key]) for key, fmt in COLUMNS) + " |")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[200, 1000, 5000])
    parser.add_argument("--profile", default="mixed", choices=["go", "human_review", "block", "mixed"])
    parser.add_argument("--max-rows", type=int, default=10_000, help="row limit of the MCP server (default 200 in production)")
    parser.add_argument("--repeat", type=int, default=3, help="timed runs per size, the fastest is kept")
    args = parser.parse_args()
    rows = []
    for size in args.sizes:
        row = measure(size, args.profile, args.max_rows, args.repeat)
        rows.append(row)
        print(f"# {size} gaps done: {row['total_s']:.2f} s", file=sys.stderr, flush=True)
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
