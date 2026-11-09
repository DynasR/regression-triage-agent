"""Command line of the CrewAI version (run it with the .venv-crew interpreter).

  python -m regression_triage.crew run 4                 triage campaign 4; asks you if a human review is needed
  python -m regression_triage.crew run 4 --no-input      stop at the human review, print how to resume
  python -m regression_triage.crew resume <thread> --decision block --reviewer "Your Name" --comment "why"

The demo database is built by the LangGraph version: python -m regression_triage demo.
Each run writes runs/crew/<thread>/trace.jsonl and runs/crew/<thread>/memo.md.
Exit code: 0 go, 1 block, 3 waiting for a human, 2 error.
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from ..judge import DEFAULT_MODEL
from .llm import OllamaLLM
from .runner import run

RUNS = Path("runs") / "crew"
FLOWS = RUNS / "flows.sqlite"


def ask_in_terminal(question: dict) -> dict:
    print("\n--- Human review needed ---")
    print(json.dumps({k: v for k, v in question.items() if k != "answer_with"}, indent=2))
    return {"decision": input("Decision (go/block): ").strip().lower(), "reviewer": input("Your name: ").strip(),
            "comment": input("Comment: ").strip()}


def finish(state: dict, thread: str) -> int:
    folder = RUNS / thread
    if "pending" in state:
        print(json.dumps(state["pending"], indent=2))
        print(f"\nWaiting for a human. Resume with:\n  python -m regression_triage.crew resume {thread} "
              '--decision go|block --reviewer "Your Name" --comment "why"')
        return 3
    (folder / "memo.md").write_text(state["memo"], encoding="utf-8")
    print(state["memo"])
    print(f"Trace: {folder / 'trace.jsonl'}\nMemo:  {folder / 'memo.md'}")
    return 0 if state["decision"]["final"] == "go" else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="regression_triage.crew", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
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
    logging.getLogger("crewai").setLevel(logging.ERROR)   # CrewAI's notices; errors still show
    if not args.db.is_file():
        print(f"no database at {args.db}: run `python -m regression_triage demo` first", file=sys.stderr)
        return 2
    llm = OllamaLLM(model=args.model)
    if args.command == "run":
        thread = f"crew-c{args.campaign}-{datetime.now():%Y%m%d-%H%M%S}"
        human = None if args.no_input or not sys.stdin.isatty() else ask_in_terminal
        state = run(args.db, llm, thread, campaign_id=args.campaign, persistence=FLOWS,
                    trace_path=RUNS / thread / "trace.jsonl", human=human)
    else:
        thread = args.thread
        answer = {"decision": args.decision, "reviewer": args.reviewer, "comment": args.comment}
        state = run(args.db, llm, thread, resume=answer, persistence=FLOWS, trace_path=RUNS / thread / "trace.jsonl")
    return finish(state, thread)


if __name__ == "__main__":
    sys.exit(main())
