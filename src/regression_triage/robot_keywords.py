"""Robot Framework keywords that drive the triage agent.

Robot Framework reads a test written as sentences (robot/triage.robot) and calls
the methods of this class, one per keyword. Each keyword is a thin layer over the
code that already exists: nothing here decides anything, it runs the agent and
reports what it did.

    Library    regression_triage.robot_keywords.TriageLibrary

The judge is chosen by a Robot variable, at the command line:
    --variable JUDGE:fake      the deterministic stand-in of the tests (default): no model, no Ollama
    --variable JUDGE:ollama    the real local model (--variable MODEL:qwen3.5:9b to change it)

The agent is the real one: the real read-only MCP server over stdio, on a demo
database built in the run's own folder. Only the model can be replaced.
"""
import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from robot.api import logger
from robot.api.deco import keyword, library

from .data import CAMPAIGNS, CATEGORIES, build
from .gateway import SCOPES, BankData
from .judge import DEFAULT_MODEL, OllamaJudge
from .runner import run

WAITING = "waiting_for_human"   # the route is human_review and nobody answered: the run stops there


def _fake_judge(campaign_id: int):
    # The fake judge lives with the tests (tests/fake_judge.py): one definition, not a copy.
    tests = Path(__file__).resolve().parents[2] / "tests"
    if not (tests / "fake_judge.py").is_file():
        raise RuntimeError(f"JUDGE=fake needs {tests / 'fake_judge.py'}; use JUDGE=ollama or run from a checkout")
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    from fake_judge import FakeJudge
    return FakeJudge(campaign_id)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@library(scope="SUITE", auto_keywords=False)
class TriageLibrary:
    def __init__(self):
        self.work: Path | None = None
        self.db: Path | None = None
        self.judge_kind = "fake"
        self.model = DEFAULT_MODEL
        self._db_digest = ""

    # --- setup and teardown -------------------------------------------------

    @keyword
    def prepare_triage_environment(self, work_folder, judge="fake", model=DEFAULT_MODEL):
        """Build the demo database in `work_folder` and remember the judge to use ('fake' or 'ollama')."""
        judge = str(judge).lower()
        if judge not in ("fake", "ollama"):
            raise ValueError(f"JUDGE must be 'fake' or 'ollama', not {judge!r}")
        self.judge_kind, self.model = judge, str(model)
        self.work = Path(work_folder)
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)
        self.db = build(self.work / "bank.db")
        self._db_digest = _digest(self.db)
        logger.info(f"judge={self.judge_kind}, database={self.db}")

    @keyword
    def cleanup_triage_environment(self):
        """Check the database was not changed by any run, then delete the work folder."""
        try:
            if self.db and self.db.is_file() and _digest(self.db) != self._db_digest:
                raise AssertionError("the demo database changed during the suite: a write got through")
        finally:
            if self.work:
                shutil.rmtree(self.work, ignore_errors=True)

    # --- running the agent --------------------------------------------------

    @keyword
    def run_triage(self, campaign_id):
        """Triage one campaign with no human input. Returns a result to hand to the other keywords."""
        campaign_id = int(campaign_id)
        if self.db is None:
            raise RuntimeError("call Prepare Triage Environment first (Suite Setup)")
        judge = _fake_judge(campaign_id) if self.judge_kind == "fake" else OllamaJudge(model=self.model)
        thread = f"robot-c{campaign_id}"
        folder = self.work / thread
        data = self._open_data()
        before = _digest(self.db)
        state = asyncio.run(run(self.db, judge, thread, campaign_id=campaign_id,
                                checkpoints=folder / "checkpoints.sqlite", trace_path=folder / "trace.jsonl",
                                human=None, data=data))
        waiting = "__interrupt__" in state
        lines = state.get("risk", {}).get("lines", [])
        return {
            "campaign_id": campaign_id,
            "route": state["risk"]["route"] if "risk" in state else None,
            "decision": WAITING if waiting else state["decision"]["final"],
            "gap_total": len(state.get("gaps", [])),
            "counts": {c: sum(1 for line in lines if line["category"] == c) for c in CATEGORIES},
            "calls": list(data.calls),
            "database_unchanged": _digest(self.db) == before,
        }

    def _open_data(self) -> BankData:
        # Same server as BankData.stdio(), with one difference: the MCP client gives sys.stderr
        # to the server subprocess, and under Robot sys.stderr is a stream with no file
        # descriptor. The real one is passed explicitly.
        server = StdioServerParameters(command=sys.executable,
                                       args=["-m", "readonly_sql_mcp", str(self.db.resolve())])
        return BankData(stdio_client(server, errlog=sys.__stderr__))

    # --- reading the result -------------------------------------------------

    @keyword
    def triage_route(self, result):
        """The route the score gave: go, block or human_review."""
        return result["route"]

    @keyword
    def triage_decision(self, result):
        """The final decision: go, block, or 'waiting_for_human' when the route is human_review."""
        return result["decision"]

    @keyword
    def gap_count(self, result, category=None):
        """The number of gaps in a category (numerical_noise, expected_change, known_defect, new_regression); all of them without a category."""
        if category is None:
            return result["gap_total"]
        if category not in CATEGORIES:
            raise ValueError(f"unknown category {category!r}; expected one of {', '.join(CATEGORIES)}")
        return result["counts"][category]

    @keyword
    def gap_counts_should_match_ground_truth(self, result):
        """Compare the gaps per category with what the demo data planted (data.CAMPAIGNS, kept out of the database)."""
        campaign = next(c for c in CAMPAIGNS if c.id == result["campaign_id"])
        expected = {c: sum(1 for p in campaign.planted if p.truth == c) for c in CATEGORIES}
        if result["counts"] != expected or result["gap_total"] != len(campaign.planted):
            raise AssertionError(f"campaign {campaign.id}: gaps per category {result['counts']} "
                                 f"({result['gap_total']} in all), the ground truth says {expected} "
                                 f"({len(campaign.planted)} in all)")

    @keyword
    def no_write_should_have_been_attempted(self, result):
        """Every call that reached the data server was a read through a node with a scope, and the database file is unchanged."""
        bad = [(node, tool) for node, tool in result["calls"] if tool != "query" or node not in SCOPES]
        if bad:
            raise AssertionError(f"calls that are not a scoped read: {bad}")
        if not result["database_unchanged"]:
            raise AssertionError("the database file changed during the run")
