"""Tests of the CrewAI version. Run them with the CrewAI environment:

    .venv-crew/Scripts/python -m pytest tests_crew      (Windows)
    .venv-crew/bin/python -m pytest tests_crew

No model runs here: a fake one answers. The MCP server is the real one, over
stdio, started with the LangGraph environment's interpreter (see crew/bank.py).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
pytest.importorskip("crewai")

from regression_triage.data import build  # noqa: E402


@pytest.fixture(scope="session")
def bank_db(tmp_path_factory):
    return build(tmp_path_factory.mktemp("demo") / "bank.db")
