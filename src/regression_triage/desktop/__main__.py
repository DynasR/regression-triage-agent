"""Triage a campaign, then enter the decision into the delivery desk.

  python -m regression_triage.desktop 7                 triage campaign 7, enter the decision, check it
  python -m regression_triage.desktop 4 --backend uia   the same through UI Automation
  python -m regression_triage.desktop 4 --hold 5        leave the window open 5 seconds to watch it

The triage runs as `python -m regression_triage run <campaign> --no-input`
does: a campaign that needs a human is entered as REVUE HUMAINE, never decided
here. The desk's actions go to the same trace as the triage:
runs/<thread>/trace.jsonl, nodes "desktop.*".
Exit code: 0 entered and read back, 2 error or read-back gap.
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

RUNS = Path("runs")
CHECKPOINTS = RUNS / "checkpoints.sqlite"


def decision_from_state(state: dict) -> tuple[str, str, int]:
    """(decision, comment, number of gaps) from the triage's final state. Code only."""
    gaps = len(state.get("gaps") or [])
    if "__interrupt__" in state:
        score = state["__interrupt__"][0].value.get("score")
        return "human_review", f"score {score}, {gaps} gaps: waiting for a human review", gaps
    decision = state["decision"]
    score = (state.get("risk") or {}).get("score")
    return decision["final"], f"score {score}, {gaps} gaps, decided by {decision.get('decided_by')}", gaps


def main() -> int:
    parser = argparse.ArgumentParser(prog="regression_triage.desktop", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("campaign", type=int)
    parser.add_argument("--db", type=Path, default=Path("demo/bank.db"))
    parser.add_argument("--model", default=None, help="the triage's model (default: the triage's default)")
    parser.add_argument("--backend", choices=["win32", "uia"], default="win32")
    parser.add_argument("--hold", type=float, default=0.0, help="seconds to leave the window open at the end")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.platform != "win32":
        print("the desktop agent needs Windows", file=sys.stderr)
        return 2
    if not args.db.is_file():
        print(f"no database at {args.db}: run `python -m regression_triage demo` first", file=sys.stderr)
        return 2

    from ..judge import DEFAULT_MODEL, OllamaJudge
    from ..runner import run
    from ..trace import Tracer
    from .agent import ReadbackMismatch, enter_decision
    from .connector import DesktopConnector, NotOurWindow

    thread = f"c{args.campaign}-desk-{datetime.now():%Y%m%d-%H%M%S}"
    trace_path = RUNS / thread / "trace.jsonl"
    state = asyncio.run(run(args.db, OllamaJudge(model=args.model or DEFAULT_MODEL), thread,
                            campaign_id=args.campaign, checkpoints=CHECKPOINTS, trace_path=trace_path))
    decision, comment, gaps = decision_from_state(state)
    print(f"triage: {decision} ({comment})")

    tracer = Tracer(trace_path, thread)
    try:
        with DesktopConnector(tracer, backend=args.backend,
                              app_args=["--campaign", args.campaign, "--gaps", gaps]) as app:
            shown = enter_decision(app, decision, comment)
            print(f"desk:   {shown['status']} | comment: {shown['comment']} | attempts: {shown['attempts']}")
            time.sleep(max(args.hold, 0))
    except (ReadbackMismatch, NotOurWindow) as error:
        print(f"desk:   FAILED, {error}", file=sys.stderr)
        print(f"Trace: {trace_path}")
        return 2
    print(f"Trace: {trace_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
