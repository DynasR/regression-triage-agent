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
