"""The Robot Framework suite (robot/triage.robot), run through robot.run with the fake judge.

Robot runs the real agent (the real read-only MCP server over stdio, a database
built in a temporary folder); only the model is replaced. One run serves every
test of this file, except the one that corrupts an expected decision on purpose.
"""
import io
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import robot
from robot.api import ExecutionResult

from regression_triage.data import CAMPAIGNS
from regression_triage.robot_keywords import TriageLibrary

SUITE = Path(__file__).resolve().parent.parent / "robot" / "triage.robot"


def run_suite(folder: Path, **variables) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    return robot.run(str(SUITE), outputdir=str(folder), output="output.xml", xunit="xunit.xml", log="log.html",
                     report="NONE", variable=["JUDGE:fake"] + [f"{k}:{v}" for k, v in variables.items()],
                     stdout=io.StringIO(), stderr=io.StringIO())


@pytest.fixture(scope="module")
def green_run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("robot-green")
    return run_suite(folder), folder


def test_the_suite_file_exists():
    assert SUITE.is_file()


def test_eight_tests_pass(green_run):
    code, folder = green_run
    stats = ExecutionResult(str(folder / "output.xml")).suite.statistics
    assert (stats.total, stats.passed, stats.failed) == (8, 8, 0)
    assert code == 0


def test_xunit_is_valid_with_eight_testcases(green_run):
    _, folder = green_run
    root = ET.parse(folder / "xunit.xml").getroot()
    assert len(root.findall(".//testcase")) == 8
    assert not root.findall(".//failure")


def test_log_html_is_written(green_run):
    _, folder = green_run
    assert (folder / "log.html").stat().st_size > 0


def test_every_test_carries_its_tags(green_run):
    _, folder = green_run
    tests = {t.name: set(t.tags) for t in ExecutionResult(str(folder / "output.xml")).suite.tests}
    assert len(tests) == 8
    by_campaign = {next(tag for tag in tags if tag.startswith("campaign-")): tags for tags in tests.values()}
    for campaign in CAMPAIGNS:
        tags = by_campaign[f"campaign-{campaign.id}"]
        assert f"route-{campaign.decision}" in tags
        assert f"XRAY-DEMO-{campaign.id}" in tags


def test_a_wrong_expected_decision_makes_robot_fail(tmp_path):
    code = run_suite(tmp_path, EXPECTED_3="block")
    result = ExecutionResult(str(tmp_path / "output.xml")).suite
    failed = [t for t in result.tests if t.status == "FAIL"]
    assert code == 1
    assert [t.tags.__contains__("campaign-3") for t in failed] == [True]
    assert result.statistics.passed == 7


# The keywords that check a negative, tried on results made by hand: a green run never
# shows that they would catch a write or a wrong count.
def _result(**changes):
    result = {"campaign_id": 5, "route": "block", "decision": "block", "gap_total": 4,
              "counts": {"numerical_noise": 0, "expected_change": 0, "known_defect": 0, "new_regression": 4},
              "calls": [("load_campaign", "query"), ("compute_gaps", "query")], "database_unchanged": True}
    return {**result, **changes}


def test_the_write_check_accepts_scoped_reads():
    TriageLibrary().no_write_should_have_been_attempted(_result())


@pytest.mark.parametrize("calls", [[("compute_gaps", "execute")], [("score_risk", "query")]],
                         ids=["a tool that is not a read", "a node with no scope"])
def test_the_write_check_refuses_anything_else(calls):
    with pytest.raises(AssertionError, match="not a scoped read"):
        TriageLibrary().no_write_should_have_been_attempted(_result(calls=calls))


def test_the_write_check_refuses_a_changed_database():
    with pytest.raises(AssertionError, match="database file changed"):
        TriageLibrary().no_write_should_have_been_attempted(_result(database_unchanged=False))


def test_the_ground_truth_check_accepts_the_planted_counts_and_refuses_others():
    library = TriageLibrary()
    library.gap_counts_should_match_ground_truth(_result())
    wrong = {"numerical_noise": 1, "expected_change": 0, "known_defect": 0, "new_regression": 3}
    with pytest.raises(AssertionError, match="ground truth"):
        library.gap_counts_should_match_ground_truth(_result(counts=wrong))
    with pytest.raises(AssertionError, match="ground truth"):
        library.gap_counts_should_match_ground_truth(_result(gap_total=5))


def test_gap_count_reads_a_category_and_refuses_an_unknown_one():
    library = TriageLibrary()
    assert library.gap_count(_result(), "new_regression") == 4 and library.gap_count(_result()) == 4
    with pytest.raises(ValueError):
        library.gap_count(_result(), "bug")


def test_an_unknown_judge_is_refused(tmp_path):
    with pytest.raises(ValueError, match="JUDGE"):
        TriageLibrary().prepare_triage_environment(tmp_path / "w", judge="gpt")
