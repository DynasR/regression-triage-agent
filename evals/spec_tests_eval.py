"""Evaluate spec -> test cases with the real local model, against the truth written by hand.

For each spec of specs/, each mode and each repetition:
- the local model (Ollama, temperature 0, seed 42) proposes the cases;
- the code checks them and completes the coverage (spec_tests.check);
- the book is judged against specs/verite/<SPEC>.json, which neither the
  model nor the spec_tests package ever reads.

Two modes: "values" (the model is given the test values computed by the code,
the default of the command line) and "no-values" (the spec alone: what the
model finds by itself).

Measured:
- extraction: the rules and bounds the code read equal the hand-written ones;
- coverage of the rules by the model alone (cited, then accepted), then after the code;
- boundary points tested by the model, added by the code;
- the cases every good book must have (truth "must_cover"), model alone and final;
- rejected cases, by reason;
- stability: the same accepted cases, the same rejections, the same raw text, across repetitions.

A run where the model could not be reached is NOT JUDGED (instrument), never a wrong answer.

Usage:
  python evals/spec_tests_eval.py                      # every spec, both modes, 3 repetitions
  python evals/spec_tests_eval.py --modes values --reps 1
  python evals/spec_tests_eval.py --rejudge evals/runs/spec-tests-<timestamp>
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from regression_triage.judge import DEFAULT_MODEL  # noqa: E402
from regression_triage.spec_tests.bounds import as_number, boundary_points, signed_numbers_in  # noqa: E402
from regression_triage.spec_tests.check import build_book, canonical_rule_id, named_outcome  # noqa: E402
from regression_triage.spec_tests.spec import Rounding, Threshold, load_spec  # noqa: E402

SPECS = ROOT / "specs"
MODES = {"values": True, "no-values": False}


def truths() -> dict:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((SPECS / "verite").glob("*.json"))}


def code_version() -> str:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + ("+dirty" if git("status", "--porcelain", "src", "specs") else "")


# --- Running ---------------------------------------------------------------

def run_one(truth: dict, mode: str, rep: int, out: Path, model: str) -> Path:
    from regression_triage.spec_tests.writer import OllamaCaseWriter

    spec = load_spec(SPECS / truth["file"])
    run_dir = out / mode / f"{spec.id}__{rep}"
    run_dir.mkdir(parents=True, exist_ok=True)
    writer = OllamaCaseWriter(model=model)
    started = time.monotonic()
    proposal = writer.propose(spec, give_values=MODES[mode])
    seconds = round(time.monotonic() - started, 1)
    book = build_book(spec, proposal, {"name": model, "values_given": MODES[mode], "usage": writer.usage})
    result = {"spec": spec.id, "mode": mode, "rep": rep, "model": model, "code": code_version(), "seconds": seconds,
              "proposal": proposal, "raw_text": writer.last_text, "usage": writer.usage}
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "cahier.json").write_text(json.dumps(book, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_dir


# --- Judging ---------------------------------------------------------------

def matches(case: dict, item: dict, spec) -> bool:
    """Does an accepted case test this must-cover item of the truth, with the right outcome?"""
    if case["rule"] != item["rule"]:
        return False
    if "variable" not in item:          # a rule in prose: any accepted case that cites it (the content is not judged)
        return True
    if as_number(case["inputs"].get(item["variable"])) != Decimal(item["value"]):
        return False
    bound = spec.rule(item["rule"]).bound
    if isinstance(bound, Threshold):
        return named_outcome(case["expected"], bound) == item["expected"]
    if isinstance(bound, Rounding):
        return Decimal(item["expected"]) in signed_numbers_in(case["expected"])
    return False


def extraction_errors(spec, truth: dict) -> list[str]:
    errors = []
    if [r.id for r in spec.rules] != truth["rules"]:
        errors.append(f"rules {[r.id for r in spec.rules]} != {truth['rules']}")
    computed = {r.id: boundary_points(r) for r in spec.rules if r.bound}
    if set(computed) != set(truth["bounds"]):
        errors.append(f"bounds on {sorted(computed)} != {sorted(truth['bounds'])}")
    for rule_id, b in truth["bounds"].items():
        points = computed.get(rule_id)
        if not points or [(p.value, p.expected) for p in points] != [
                (Decimal(b["limit"]), b["limit_expected"]), (Decimal(b["beyond"]), b["beyond_expected"])]:
            errors.append(f"{rule_id}: computed {[(str(p.value), p.expected) for p in points or []]}")
    return errors


def judge(result: dict, book: dict, truth: dict) -> dict:
    spec = load_spec(SPECS / truth["file"])
    instrument = book["model_error"] if "model call failed" in (book["model_error"] or "") else None
    raw = result["proposal"].get("cases", []) if isinstance(result["proposal"], dict) else []
    cited = {canonical_rule_id(c.get("rule", "")) for c in raw if isinstance(c, dict)}
    model_cases = [c for c in book["cases"] if c["origin"] == "modèle"]
    must = truth["must_cover"]
    return {
        "spec": result["spec"], "mode": result["mode"], "rep": result["rep"], "instrument": instrument,
        "model_error": book["model_error"],
        "extraction_errors": extraction_errors(spec, truth),
        "rules_total": len(truth["rules"]),
        "rules_cited_by_model": len(cited & set(truth["rules"])),
        "rules_accepted_from_model": book["coverage"]["rules_covered_by_model"],
        "rules_final": book["coverage"]["rules_covered"],
        "uncovered": [u["rule"] for u in book["coverage"]["uncovered"]],
        "bounds_required": book["coverage"]["bounds_required"],
        "bounds_by_model": book["coverage"]["bounds_by_model"],
        "bounds_added_by_code": book["coverage"]["bounds_added_by_code"],
        "must_total": len(must),
        "must_by_model": sum(any(matches(c, m, spec) for c in model_cases) for m in must),
        "must_final": sum(any(matches(c, m, spec) for c in book["cases"]) for m in must),
        "must_missed_final": [f"{m['rule']} {m.get('value', '')} ({m['why']})".replace(" ()", "")
                              for m in must if not any(matches(c, m, spec) for c in book["cases"])],
        "proposed": book["coverage"]["proposed"], "accepted": book["coverage"]["accepted"],
        "rejected": book["coverage"]["rejected"], "rejected_by_reason": book["coverage"]["rejected_by_reason"],
        "rejections": [{"rule": (r["proposed"] or {}).get("rule") if isinstance(r["proposed"], dict) else None,
                        "reason": r["reason"], "why": r["why"]} for r in book["rejected"]],
        "fingerprint": json.dumps(sorted([c["rule"], json.dumps(c["inputs"], sort_keys=True, ensure_ascii=False),
                                          c["expected"]] for c in model_cases)
                                  + sorted([r["reason"], r["why"]] for r in book["rejected"]), ensure_ascii=False),
        "raw_text": result["raw_text"],
        "seconds": result["seconds"],
        "tokens": [(u["prompt_tokens"], u["output_tokens"]) for u in result["usage"]],
    }


def stability(verdicts: list[dict]) -> dict:
    groups = {}
    for v in verdicts:
        groups.setdefault((v["mode"], v["spec"]), []).append(v)
    return {key: {"runs": len(vs), "same_cases": len({v["fingerprint"] for v in vs}) == 1,
                  "same_text": len({v["raw_text"] for v in vs}) == 1} for key, vs in groups.items()}


# --- Reporting -------------------------------------------------------------

def summarize(verdicts: list[dict]) -> str:
    judged = [v for v in verdicts if not v["instrument"]]
    stable = stability(judged)
    lines = ["| Mode | Spec | Runs | Rules cited (model) | Rules accepted (model) | Rules final | Bounds by model "
             "| Bounds added by code | Must-cover (model) | Must-cover (final) | Proposed | Rejected | Same cases "
             "| Same text | Seconds (mean) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (mode, spec), s in sorted(stable.items()):
        vs = [v for v in judged if (v["mode"], v["spec"]) == (mode, spec)]
        tot = lambda key: sum(v[key] for v in vs)  # noqa: E731
        yes = lambda flag: "yes" if flag else "**no**"  # noqa: E731
        lines.append(f"| {mode} | {spec} | {len(vs)} | {tot('rules_cited_by_model')}/{tot('rules_total')} "
                     f"| {tot('rules_accepted_from_model')}/{tot('rules_total')} | {tot('rules_final')}/{tot('rules_total')} "
                     f"| {tot('bounds_by_model')}/{tot('bounds_required')} | {tot('bounds_added_by_code')} "
                     f"| {tot('must_by_model')}/{tot('must_total')} | {tot('must_final')}/{tot('must_total')} "
                     f"| {tot('proposed')} | {tot('rejected')} | {yes(s['same_cases'])} | {yes(s['same_text'])} "
                     f"| {sum(v['seconds'] for v in vs) / len(vs):.1f} |")
    reasons = {}
    for v in judged:
        for reason, n in v["rejected_by_reason"].items():
            reasons[f"{v['mode']} {reason}"] = reasons.get(f"{v['mode']} {reason}", 0) + n
    lines += ["", "Rejections by mode and reason: "
              + (", ".join(f"{k} {n}" for k, n in sorted(reasons.items())) or "none") + ".",
              f"Extraction errors: {sum(len(v['extraction_errors']) for v in verdicts)}. "
              f"Runs not judged (instrument): {len(verdicts) - len(judged)}. "
              f"Model errors (unreadable answer): {sum(1 for v in judged if v['model_error'])}. "
              f"Tokens in/out: {sum(t[0] for v in judged for t in v['tokens'])}/"
              f"{sum(t[1] for v in judged for t in v['tokens'])}. "
              f"Wall time: {sum(v['seconds'] for v in verdicts):.0f} s."]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--modes", nargs="*", choices=list(MODES), default=list(MODES))
    parser.add_argument("--specs", nargs="*", help="spec ids (default: every spec with a truth file)")
    parser.add_argument("--rejudge", type=Path, help="judge an existing folder again, without running")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    truth = {k: t for k, t in truths().items() if not args.specs or k in args.specs}

    if args.rejudge:
        out = args.rejudge
    else:
        out = HERE / "runs" / ("spec-tests-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        jobs = [(mode, rep, sid) for mode in args.modes for rep in range(1, args.reps + 1) for sid in truth]
        print(f"{len(jobs)} run(s), model {args.model} -> {out}", flush=True)
        for mode, rep, sid in jobs:     # one at a time: one local GPU
            print(f"  done {run_one(truth[sid], mode, rep, out, args.model).relative_to(out)}", flush=True)

    verdicts = []
    for run_dir in sorted(p for p in out.glob("*/*") if (p / "result.json").exists()):
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        book = json.loads((run_dir / "cahier.json").read_text(encoding="utf-8"))
        if result["spec"] in truth:
            verdicts.append(judge(result, book, truth[result["spec"]]))
    (out / "verdicts.json").write_text(json.dumps([{k: v for k, v in x.items() if k != "raw_text"} for x in verdicts],
                                                  indent=2, ensure_ascii=False), encoding="utf-8")
    for v in verdicts:
        tag = f"{v['mode']} {v['spec']}#{v['rep']}"
        if v["instrument"]:
            print(f"NOT JUDGED {tag}: {v['instrument']}")
            continue
        for e in v["extraction_errors"]:
            print(f"EXTRACTION {tag}: {e}")
        if v["model_error"]:
            print(f"MODEL ERROR {tag}: {v['model_error']}")
        for r in v["rejections"]:
            print(f"REJECTED {tag} [{r['rule']}] {r['reason']}: {r['why']}")
        for m in v["must_missed_final"]:
            print(f"MISSED {tag}: {m}")
    report = summarize(verdicts)
    (out / "summary.md").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
