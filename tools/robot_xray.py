"""Run the Robot Framework suite and keep what Xray can import.

Robot writes three files, here in runs/robot/ (a folder git ignores):
  output.xml  Robot's own result: the file for Xray's native Robot import
  xunit.xml   the same result as JUnit XML: the file for Xray's JUnit import
  log.html    the readable log, for a human

Nothing is sent anywhere: no Jira, no Xray. See docs/robot-framework-et-xray.md
for how to import the files by hand.

Usage: python tools/robot_xray.py                    fake judge, no model needed
       python tools/robot_xray.py --judge ollama     the real local model (slow)
       python tools/robot_xray.py --test "Campaign 3*"
       python tools/robot_xray.py --variable EXPECTED_3:block      see Robot fail
Exit code: Robot's own (0 all passed, otherwise the number of failed tests, 252 bad arguments).
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))   # the code of this checkout, not an installed copy

import robot  # noqa: E402

SUITE = ROOT / "robot" / "triage.robot"
OUT = ROOT / "runs" / "robot"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--judge", choices=["fake", "ollama"], default="fake")
    parser.add_argument("--model", default=None, help="Ollama model (only with --judge ollama)")
    parser.add_argument("--test", action="append", default=[], help="run only the tests matching this name (repeatable)")
    parser.add_argument("--include", action="append", default=[], help="run only the tests with this tag, e.g. route-block")
    parser.add_argument("--variable", action="append", default=[], help="an extra Robot variable, NAME:value")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    variables = [f"JUDGE:{args.judge}", *args.variable]
    if args.model:
        variables.append(f"MODEL:{args.model}")
    code = robot.run(str(SUITE), outputdir=str(args.out), output="output.xml", xunit="xunit.xml", log="log.html",
                     report="NONE", variable=variables, test=args.test, include=args.include)
    print()
    for name in ("output.xml", "xunit.xml", "log.html"):
        path = args.out / name
        print(f"{name:11} {path}" if path.is_file() else f"{name:11} not written")
    return code


if __name__ == "__main__":
    sys.exit(main())
