"""Command line.

  python -m regression_triage demo                      build demo/bank.db
  python -m regression_triage run 4                     triage campaign 4; asks you if a human review is needed
  python -m regression_triage run 4 --no-input          stop at the human review, print how to resume
  python -m regression_triage resume <thread> --decision block --reviewer "Your Name" --comment "why"

Each run writes runs/<thread>/trace.jsonl and runs/<thread>/memo.md.
Exit code: 0 go, 1 block, 3 waiting for a human, 2 error.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from .data import build
from .judge import DEFAULT_MODEL, OllamaJudge
from .runner import run

RUNS = Path("runs")
CHECKPOINTS = RUNS / "checkpoints.sqlite"


def ask_in_terminal(question: dict) -> dict:
    print("\n--- Human review needed ---")
    print(json.dumps({k: v for k, v in question.items() if k != "answer_with"}, indent=2))
    decision = input("Decision (go/block): ").strip().lower()
    reviewer = input("Your name: ").strip()
    comment = input("Comment: ").strip()
    return {"decision": decision, "reviewer": reviewer, "comment": comment}


def finish(state: dict, thread: str) -> int:
    folder = RUNS / thread
    if "__interrupt__" in state:
        question = state["__interrupt__"][0].value
        print(json.dumps(question, indent=2))
        print(f"\nWaiting for a human. Resume with:\n  python -m regression_triage resume {thread} "
              '--decision go|block --reviewer "Your Name" --comment "why"')
        return 3
    (folder / "memo.md").write_text(state["memo"], encoding="utf-8")
    print(state["memo"])
    print(f"Trace: {folder / 'trace.jsonl'}\nMemo:  {folder / 'memo.md'}")
    return 0 if state["decision"]["final"] == "go" else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="regression_triage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="build the demo database")
    demo.add_argument("--db", type=Path, default=Path("demo/bank.db"))
    start = sub.add_parser("run", help="triage one campaign")
    start.add_argument("campaign", type=int)
    start.add_argument("--no-input", action="store_true", help="stop at the human review instead of asking")
    again = sub.add_parser("resume", help="answer a waiting human review")
    again.add_argument("thread")
    again.add_argument("--decision", required=True, choices=["go", "block"])
    again.add_argument("--reviewer", required=True)
    again.add_argument("--comment", default="")
    for command in (start, again):
        command.add_argument("--db", type=Path, default=Path("demo/bank.db"))
        command.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "demo":
        print(f"built {build(args.db)}")
        return 0
    if not args.db.is_file():
        print(f"no database at {args.db}: run `python -m regression_triage demo` first", file=sys.stderr)
        return 2
    judge = OllamaJudge(model=args.model)
    if args.command == "run":
        thread = f"c{args.campaign}-{datetime.now():%Y%m%d-%H%M%S}"
        human = None if args.no_input or not sys.stdin.isatty() else ask_in_terminal
        state = asyncio.run(run(args.db, judge, thread, campaign_id=args.campaign, checkpoints=CHECKPOINTS,
                                trace_path=RUNS / thread / "trace.jsonl", human=human))
    else:
        thread = args.thread
        answer = {"decision": args.decision, "reviewer": args.reviewer, "comment": args.comment}
        state = asyncio.run(run(args.db, judge, thread, resume=answer, checkpoints=CHECKPOINTS,
                                trace_path=RUNS / thread / "trace.jsonl"))
    return finish(state, thread)


if __name__ == "__main__":
    sys.exit(main())
