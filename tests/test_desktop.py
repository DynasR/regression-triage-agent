"""The desktop agent: it drives only the application it started, only through
a short list of actions, and checks what the application shows.

Two kinds of tests:
- the list of actions, the refusals, the agent's plan, attach and close on a
  process without a window: they run everywhere, the CI on Linux included;
- real interface tests on a real window: only when asked (RUN_GUI_TESTS=1),
  on Windows with an interactive desktop. They open the application off
  screen, without the focus or a taskbar button, so that nothing appears on
  the user's desktop; still, a plain `pytest` never opens a window.
"""
import ctypes
import os
import shutil
import subprocess
import sys

import pytest

from regression_triage.desktop.agent import LABELS, ReadbackMismatch, enter_decision
from regression_triage.desktop.connector import (ALLOWED, DEMO_APP, ActionRefused, DesktopConnector,
                                                 NotOurWindow, check, gui_python)
from regression_triage.desktop.__main__ import decision_from_state
from regression_triage.trace import Tracer, read


def _interactive_desktop() -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.WinDLL("user32")
    desktop = user32.OpenInputDesktop(0, False, 0x0001)   # DESKTOP_READOBJECTS
    if not desktop:
        return False   # a service session, or a locked screen
    user32.CloseDesktop(desktop)
    return True


def _why_no_gui() -> str | None:
    if os.environ.get("RUN_GUI_TESTS") != "1":
        return "opens a window: set RUN_GUI_TESTS=1 to run the real interface tests"
    if not _interactive_desktop():
        return "real interface test: needs Windows with an interactive, unlocked desktop"
    return None


needs_desktop = pytest.mark.skipif(_why_no_gui() is not None, reason=_why_no_gui() or "")

OFFSCREEN = ["--offscreen"]


@pytest.fixture
def tracer(tmp_path):
    return Tracer(tmp_path / "trace.jsonl", "desk-test")


@pytest.fixture
def sleeper():
    """A process with no window at all, started without the connector."""
    process = subprocess.Popen([gui_python(), "-c", "import time; time.sleep(60)"],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    yield process
    process.kill()
    process.wait(10)


# --- The list of actions, without any window ------------------------------------------------------

def test_the_whitelist_is_exactly_this():
    # Widening it must be a visible decision, like the data scopes.
    assert ALLOWED == {
        ("read", "campaign"), ("read", "gaps"), ("read", "status"),
        ("read", "decision"), ("read", "comment"),
        ("type", "decision"), ("type", "comment"),
        ("click", "validate"),
    }


@pytest.mark.parametrize("action, control", [
    ("send_keys", "decision"),     # global keystrokes: never
    ("type", "status"),            # a label is not a field
    ("type", "campaign"),
    ("click", "decision"),         # only a button is clicked
    ("read", "validate"),
    ("type", "password"),          # a control that is not on the list
    ("close", "window"),
    ("click_input", "validate"),   # a real mouse click moves the user's pointer
])
def test_an_action_outside_the_list_is_refused(action, control):
    with pytest.raises(ActionRefused):
        check(action, control)


def test_a_refused_action_is_traced_before_anything_is_touched(tracer):
    connector = DesktopConnector(tracer)          # never started: no process, no window
    with pytest.raises(ActionRefused):
        connector.do("send_keys", "decision", "{ENTER}")
    assert connector.process is None
    [line] = read(tracer.path)
    assert line["node"] == "desktop.send_keys"
    assert line["status"] == "refused"
    assert line["input"] == {"control": "decision", "value": "{ENTER}"}
    assert "not on the list" in line["error"]


def test_attach_refuses_a_process_it_did_not_start(tracer):
    connector = DesktopConnector(tracer)
    with pytest.raises(NotOurWindow):
        connector.attach(12345)
    [line] = read(tracer.path)
    assert (line["node"], line["status"]) == ("desktop.attach", "refused")


def test_attach_refuses_any_process_but_its_own(tracer, sleeper):
    connector = DesktopConnector(tracer)
    connector.process = sleeper                    # as if the connector had started it
    for pid in (sleeper.pid + 4, os.getpid()):
        with pytest.raises(NotOurWindow, match="not started by this connector"):
            connector.attach(pid)
    assert [line["status"] for line in read(tracer.path)] == ["refused", "refused"]


def test_the_process_is_stopped_even_when_the_work_fails(tracer, sleeper):
    connector = DesktopConnector(tracer, close_wait=2.0)
    connector.start = lambda: setattr(connector, "process", sleeper)   # no window: nothing to ask, so killed
    with pytest.raises(RuntimeError, match="boom"):
        with connector:
            raise RuntimeError("boom")
    assert sleeper.poll() is not None
    [close] = read(tracer.path)
    assert (close["node"], close["output"]) == ("desktop.close", {"how": "killed"})


def test_the_application_runs_on_the_real_interpreter_not_a_launcher():
    # A virtual environment's python.exe on Windows is a launcher that starts the real
    # interpreter as a child: the window would belong to a PID the connector never saw.
    assert gui_python() == getattr(sys, "_base_executable", sys.executable)


# --- The agent's plan, with a fake application ----------------------------------------------------

class FakeApp:
    """Stands in for the connector: an application with the same fields and the same rules."""

    def __init__(self, rewrite: int = 0):
        self.fields = {"decision": "", "comment": "", "status": "En attente", "campaign": "Campagne 4",
                       "gaps": "Écarts : 3"}
        self.rewrite, self.actions = rewrite, []   # rewrite: how many validations go wrong

    def do(self, action, control, value=None):
        check(action, control)
        self.actions.append((action, control) if value is None else (action, control, value))
        if action == "read":
            return self.fields[control]
        if action == "type":
            self.fields[control] = value
            return None
        if self.rewrite:
            self.rewrite -= 1
            self.fields["decision"] = "LIVRER"
        decision = self.fields["decision"]
        self.fields["status"] = f"Enregistré : {decision}" if decision in LABELS.values() else "Refusé"
        return None


def test_the_labels_are_exactly_these():
    assert LABELS == {"go": "LIVRER", "block": "BLOQUER", "human_review": "REVUE HUMAINE"}


def test_the_plan_is_fixed_and_read_back():
    app = FakeApp()
    result = enter_decision(app, "block", "two vega regressions")
    assert app.actions == [
        ("type", "decision", "BLOQUER"), ("type", "comment", "two vega regressions"), ("click", "validate"),
        ("read", "decision"), ("read", "comment"), ("read", "status"),
    ]
    assert result == {"decision": "BLOQUER", "comment": "two vega regressions",
                      "status": "Enregistré : BLOQUER", "attempts": 1}


def test_an_unknown_decision_is_refused_before_any_action():
    app = FakeApp()
    for decision in ("ship", "BLOQUER", "", None):
        with pytest.raises(ValueError):
            enter_decision(app, decision, "x")
    assert app.actions == []


def test_a_gap_at_the_read_back_is_retried_once():
    app = FakeApp(rewrite=1)
    result = enter_decision(app, "block", "x")
    assert result["attempts"] == 2
    assert app.actions.count(("click", "validate")) == 2


def test_a_gap_that_stays_fails_after_one_retry():
    app = FakeApp(rewrite=99)
    with pytest.raises(ReadbackMismatch, match="decision"):
        enter_decision(app, "block", "x")
    assert app.actions.count(("click", "validate")) == 2


def test_a_refused_status_is_a_gap_even_when_the_fields_match():
    app = FakeApp()
    original = app.do

    def refusing(action, control, value=None):
        out = original(action, control, value)
        if action == "click":
            app.fields["status"] = "Refusé : décision inconnue"
        return out

    app.do = refusing
    with pytest.raises(ReadbackMismatch, match="status"):
        enter_decision(app, "go", "x")


def test_the_comment_is_typed_on_one_line():
    app = FakeApp()
    result = enter_decision(app, "go", "  score 0\nno gap  ")
    assert ("type", "comment", "score 0 no gap") in app.actions
    assert result["comment"] == "score 0 no gap"


def test_the_decision_comes_from_the_triage_never_from_the_model():
    waiting = {"__interrupt__": [type("I", (), {"value": {"score": 6}})()], "gaps": [1, 2], "risk": {"score": 6}}
    assert decision_from_state(waiting)[0] == "human_review"
    decided = {"decision": {"final": "block", "decided_by": "rules"}, "gaps": [1], "risk": {"score": 15}}
    assert decision_from_state(decided)[0] == "block"


# --- The real application, on a real desktop ------------------------------------------------------

def _alive(process) -> bool:
    return process is not None and process.poll() is None


@needs_desktop
@pytest.mark.parametrize("backend", ["win32", "uia"])
def test_a_decision_is_entered_and_read_back(tracer, backend):
    with DesktopConnector(tracer, backend=backend, app_args=[*OFFSCREEN, "--campaign", "7", "--gaps", "2"]) as app:
        assert app.read("campaign") == "Campagne 7"
        assert app.read("gaps") == "Écarts : 2"
        result = enter_decision(app, "block", "two vega regressions")
        assert result["attempts"] == 1
        assert app.read("decision") == "BLOQUER"
        assert app.read("status") == "Enregistré : BLOQUER"
    lines = read(tracer.path)
    assert [line["node"] for line in lines[:2]] == ["desktop.launch", "desktop.attach"]
    assert {(line["node"], line["status"]) for line in lines} >= {("desktop.click", "ok"), ("desktop.close", "ok")}


@needs_desktop
def test_an_action_outside_the_list_is_refused_on_the_real_app(tracer):
    with DesktopConnector(tracer, app_args=OFFSCREEN) as app:
        for action, control in (("type", "status"), ("send_keys", "decision"), ("click_input", "validate")):
            with pytest.raises(ActionRefused):
                app.do(action, control, "BLOQUER")
        assert app.read("status") == "En attente"
        assert app.read("decision") == ""
    refused = [line for line in read(tracer.path) if line["status"] == "refused"]
    assert [line["node"] for line in refused] == ["desktop.type", "desktop.send_keys", "desktop.click_input"]


@needs_desktop
def test_a_second_instance_with_the_same_title_is_never_driven(tracer, tmp_path):
    from pywinauto import Application
    # The same application, started by another path and before the agent's own.
    copy = tmp_path / "other" / "demo_app.py"
    copy.parent.mkdir()
    shutil.copy(DEMO_APP, copy)
    other = subprocess.Popen([gui_python(), str(copy), *OFFSCREEN, "--campaign", "99"])
    try:
        other_window = Application(backend="win32").connect(process=other.pid, timeout=10).top_window()
        other_window.wait("visible", timeout=10)
        assert other_window.rectangle().left < -10000   # off screen, yet driven below
        with DesktopConnector(tracer, app_args=[*OFFSCREEN, "--campaign", "4"]) as app:
            assert app.pid != other.pid
            with pytest.raises(NotOurWindow):
                app.attach(other.pid)
            assert app.read("campaign") == "Campagne 4"
            enter_decision(app, "go", "no gap")
        assert other_window.child_window(control_id=201).window_text() == ""
        assert other_window.child_window(control_id=401).window_text() == "En attente"
        assert _alive(other)
    finally:
        other.kill()
        other.wait(10)
    assert ("desktop.attach", "refused") in {(line["node"], line["status"]) for line in read(tracer.path)}


@needs_desktop
def test_the_app_is_closed_even_when_the_work_fails(tracer):
    with pytest.raises(RuntimeError, match="boom"):
        with DesktopConnector(tracer, app_args=OFFSCREEN) as app:
            process = app.process
            raise RuntimeError("boom")
    assert not _alive(process)


@needs_desktop
def test_an_app_that_ignores_the_close_request_is_killed(tracer):
    with DesktopConnector(tracer, app_args=[*OFFSCREEN, "--fault", "no-close"], close_wait=1.0) as app:
        process = app.process
    assert not _alive(process)
    [close] = [line for line in read(tracer.path) if line["node"] == "desktop.close"]
    assert close["output"] == {"how": "killed"}


@needs_desktop
def test_a_gap_at_the_read_back_fails_after_one_retry(tracer):
    with DesktopConnector(tracer, app_args=[*OFFSCREEN, "--fault", "rewrite"]) as app:
        process = app.process
        with pytest.raises(ReadbackMismatch, match="decision"):
            enter_decision(app, "block", "two vega regressions")
    assert not _alive(process)
    clicks = [line for line in read(tracer.path) if line["node"] == "desktop.click"]
    assert len(clicks) == 2
    assert read(tracer.path)[-2]["status"] == "error"   # the agent's line, then the close


@needs_desktop
def test_a_gap_at_the_read_back_once_is_fixed_by_the_retry(tracer):
    with DesktopConnector(tracer, app_args=[*OFFSCREEN, "--fault", "once"]) as app:
        result = enter_decision(app, "block", "x")
    assert result["attempts"] == 2
