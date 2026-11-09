"""Mutation check: break each deterministic protection on purpose, and require a test to fail.

A test suite that stays green when a rule is removed protects nothing. Each
mutation below removes or weakens one rule of the deterministic parts (the
gaps, the checks on the model's answer, the score, the routing, least
privilege, the memo guard, the model settings, the evaluation judge); the
named test file must then turn red. The source file is always restored byte
for byte, even on interruption.

No language model runs here: every test file uses a fake one.

Usage: python tools/mutation_check.py      (a few minutes)
Exit code: 0 if every mutation is caught, 1 otherwise.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LF, CRLF = chr(10), chr(13) + chr(10)
CAMPAIGN, RISK, GRAPH = "src/regression_triage/campaign.py", "src/regression_triage/risk.py", "src/regression_triage/graph.py"
GATEWAY, REPORT, JUDGE = "src/regression_triage/gateway.py", "src/regression_triage/report.py", "src/regression_triage/judge.py"
TRACE, EVALS = "src/regression_triage/trace.py", "evals/run_evals.py"

# (what is broken, file, original text, mutated text, test file that must fail)
MUTATIONS = [
    # Gaps: computed by SQL, through the MCP server.
    ("gaps: a difference equal to the tolerance counts", CAMPAIGN,
     "OR abs(r.candidate_value - r.reference_value) > {_TOLERANCE}",
     "OR abs(r.candidate_value - r.reference_value) >= {_TOLERANCE}", "tests/test_gaps.py"),
    ("gaps: relative tolerance ignored", CAMPAIGN,
     '_TOLERANCE = "max(coalesce(t.abs_tol, 0), coalesce(t.rel_tol, 0) * abs(r.reference_value))"',
     '_TOLERANCE = "coalesce(t.abs_tol, 0)"', "tests/test_gaps.py"),
    ("gaps: a value that failed to price is dropped", CAMPAIGN,
     "AND (r.reference_value IS NULL OR r.candidate_value IS NULL\n       OR abs(", "AND ((\n       abs(",
     "tests/test_gaps.py"),
    ("gaps: a metric without tolerance is dropped", CAMPAIGN,
     "LEFT JOIN tolerances t", "JOIN tolerances t", "tests/test_gaps.py"),
    ("gaps: a result of an unknown case is dropped", CAMPAIGN,
     "LEFT JOIN test_cases c", "JOIN test_cases c", "tests/test_gaps.py"),
    ("gaps: an unknown case is not critical", CAMPAIGN,
     'row["criticality"] or "high"', 'row["criticality"] or "low"', "tests/test_gaps.py"),
    ("gaps: other campaigns mixed in", CAMPAIGN,
     "WHERE r.campaign_id = {{cid}}", "WHERE {{cid}} = {{cid}}", "tests/test_gaps.py"),
    ("campaign: nothing tested is accepted", CAMPAIGN,
     'if count[0]["n"] == 0:', "if False:", "tests/test_gaps.py"),
    ("campaign: the id is not checked", CAMPAIGN,
     "    if isinstance(campaign_id, bool) or not isinstance(campaign_id, int):\n",
     "    if False:\n", "tests/test_gaps.py"),

    # The model's answer, checked by code.
    ("check: a model error is not rejected as such", RISK,
     '    if "error" in raw:\n', "    if False:\n", "tests/test_risk.py"),
    ("check: a large gap can be noise", RISK,
     'small = gap["status"] == "out_of_tolerance" and gap["ratio"] is not None and gap["ratio"] < NOISE_MAX_RATIO',
     "small = True", "tests/test_risk.py"),
    ("check: noise bound off by one", RISK,
     'gap["ratio"] < NOISE_MAX_RATIO', 'gap["ratio"] <= NOISE_MAX_RATIO', "tests/test_risk.py"),
    ("check: an invented release note is accepted", RISK,
     "note = notes.get(cited)", 'note = notes.get(cited) or {"product_code": None}', "tests/test_risk.py"),
    ("check: a release note about another product is accepted", RISK,
     'if note["product_code"] not in (None, gap["product_code"]):', "if False:", "tests/test_risk.py"),
    ("check: a fixed ticket is accepted", RISK,
     'if ticket is None or ticket["status"] != "open":', "if ticket is None:", "tests/test_risk.py"),
    ("check: a ticket about another product is accepted", RISK,
     'if ticket["product_code"] != gap["product_code"]:', "if False:", "tests/test_risk.py"),

    # Score and route.
    ("score: an unclassified gap counts as noise", RISK,
     'by_id.get(gap["result_id"], "new_regression")', 'by_id.get(gap["result_id"], "numerical_noise")',
     "tests/test_risk.py"),
    ("score: a known defect weighs nothing", RISK,
     '"known_defect": 1,', '"known_defect": 0,', "tests/test_risk.py"),
    ("score: an unknown criticality is not the highest", RISK,
     'gap["criticality"] if gap["criticality"] in CRITICALITY_WEIGHT else "high"', 'gap["criticality"]',
     "tests/test_risk.py"),
    ("route: block threshold off by one", RISK,
     "if points >= BLOCK_FROM:", "if points > BLOCK_FROM:", "tests/test_risk.py"),
    ("route: human review threshold off by one", RISK,
     "if points >= HUMAN_REVIEW_FROM:", "if points > HUMAN_REVIEW_FROM:", "tests/test_risk.py"),

    # The graph.
    ("graph: the route of the score is ignored", GRAPH,
     'return state["risk"]["route"]', 'return "go"', "tests/test_graph.py"),
    ("graph: the gaps are never classified", GRAPH,
     'return "classify_gaps" if state["gaps"] else "score_risk"', 'return "score_risk"', "tests/test_graph.py"),
    ("graph: an invalid human answer is accepted", GRAPH,
     "            if problem is None:\n", "            if True:\n", "tests/test_graph.py"),
    ("graph: the model sees the evidence of every product", GRAPH,
     "own = evidence_for(gap, evidence)", "own = evidence", "tests/test_graph.py"),

    # Least privilege.
    ("privilege: a scope is widened", GATEWAY,
     'tables=frozenset({"results", "test_cases", "tolerances"})',
     'tables=frozenset({"results", "test_cases", "tolerances", "tickets"})', "tests/test_privileges.py"),
    ("privilege: tables not checked", GATEWAY,
     'outside = tables_read(arguments["sql"]) - self.scope.tables', "outside = set()", "tests/test_privileges.py"),
    ("privilege: tools not checked", GATEWAY,
     "if tool not in self.scope.tools:", "if False:", "tests/test_privileges.py"),
    ("privilege: a node without a scope gets one", GATEWAY,
     '            raise AccessDenied(f"node {node!r} has no access to the data")\n',
     '            return ScopedTools(self, node, Scope(frozenset({"query"}), frozenset({"results"})))\n',
     "tests/test_privileges.py"),
    ("privilege: a schema-qualified table passes", GATEWAY,
     "if table.db or table.catalog:", "if False:", "tests/test_privileges.py"),
    ("privilege: a cut result is used as if complete", GATEWAY,
     'if out["truncated"]:', "if False:", "tests/test_privileges.py"),

    # The memo and the model settings.
    ("memo: invented numbers not checked", REPORT,
     "unknown = sorted(numbers_in(text) - numbers_in(given))", "unknown = []", "tests/test_graph.py"),
    ("model: temperature above zero", JUDGE,
     '"temperature": 0,', '"temperature": 0.7,', "tests/test_judge.py"),
    ("model: no fixed seed", JUDGE,
     '"seed": self.seed,', "", "tests/test_judge.py"),
    ("model: size compared by the model again", JUDGE,
     'small = gap["ratio"] < NOISE_MAX_RATIO', "small = None", "tests/test_judge.py"),
    ("trace: a pause is not recorded", TRACE,
     '                self.write(node, "interrupted", (time.monotonic() - started) * 1000, state)\n', "",
     "tests/test_graph.py"),

    # The judge of the evaluations.
    ("eval judge: an unreachable model counted as the agent", EVALS,
     "    if failed:\n", "    if False:\n", "tests/test_eval_judge.py"),
    ("eval judge: the justification is not checked", EVALS,
     ' and c["evidence_id"] == expected.evidence,', ",", "tests/test_eval_judge.py"),
    ("eval judge: raw text stability not measured", EVALS,
     '"same_text": len({fingerprint(r, raw=True) for r in runs}) == 1', '"same_text": True',
     "tests/test_eval_judge.py"),
    ("eval judge: a missed gap is not counted", EVALS,
     "for case, metric in set(truth) - found)", "for case, metric in set())", "tests/test_eval_judge.py"),
]


def caught(test_file: str) -> bool:
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:logging", "-p", "no:cacheprovider",
                          test_file], cwd=ROOT, capture_output=True, text=True, timeout=600)
    return run.returncode != 0


def read_exact(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_exact(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def main() -> int:
    missed = []
    for name, file, original, mutated, test_file in MUTATIONS:
        path = ROOT / file
        # Read and written back without newline translation: the file comes
        # back exactly as it was, whatever its line endings.
        source = read_exact(path)
        eol = CRLF if CRLF in source else LF
        original, mutated = original.replace(LF, eol), mutated.replace(LF, eol)
        if source.count(original) != 1:
            print(f"STALE   {name}: the original text is not exactly once in {file}", flush=True)
            missed.append(name)
            continue
        try:
            write_exact(path, source.replace(original, mutated))
            ok = caught(test_file)
        finally:
            write_exact(path, source)
        print(f"{'caught' if ok else 'MISSED'}  {name}", flush=True)
        if not ok:
            missed.append(name)
    print(f"\n{len(MUTATIONS) - len(missed)}/{len(MUTATIONS)} mutations caught.")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
