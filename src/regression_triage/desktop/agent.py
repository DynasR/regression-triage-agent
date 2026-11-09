"""Enter the triage's decision into the delivery desk, and check that the desk shows it.

The plan is fixed, written here, and the same every time:
    type decision, type comment, click Valider, read decision, read comment, read status.
Nothing chooses the next action at run time. The decision itself was taken
before, by the triage graph (its rules, or a human); this agent only carries
it to the application and proves it arrived.

The model has no role here. The one place it could have helped is turning the
triage's word (go, block, human_review) into the desk's word (LIVRER, BLOQUER,
REVUE HUMAINE); with three closed values, a table does it exactly, every time,
and a test pins it. A model would only add a way to be wrong.

A read-back that differs from what was typed, or a status that does not
confirm it, is retried once from the start of the plan; a second gap is a
failure (ReadbackMismatch), traced, never a silent success.
"""
import time

from .connector import DesktopConnector

LABELS = {"go": "LIVRER", "block": "BLOQUER", "human_review": "REVUE HUMAINE"}
ATTEMPTS = 2   # the first, and one retry


class ReadbackMismatch(Exception):
    """The application does not show what was entered."""


def one_line(text: str) -> str:
    """The comment field holds one line."""
    return " ".join(str(text or "").split())


def expected_status(label: str) -> str:
    return f"Enregistré : {label}"


def enter_decision(app: DesktopConnector, decision: str, comment: str) -> dict:
    """Type the decision and the comment, validate, read back. Returns what the application shows."""
    if decision not in LABELS:
        raise ValueError(f"decision must be one of {sorted(LABELS)}, not {decision!r}")
    label, comment = LABELS[decision], one_line(comment)
    expected = {"decision": label, "comment": comment, "status": expected_status(label)}
    gaps = {}
    for attempt in range(1, ATTEMPTS + 1):
        started = time.monotonic()
        app.do("type", "decision", label)
        app.do("type", "comment", comment)
        app.do("click", "validate")
        shown = {name: app.do("read", name) for name in ("decision", "comment", "status")}
        gaps = {name: {"expected": expected[name], "shown": shown[name]}
                for name in expected if shown[name] != expected[name]}
        _trace(app, "ok" if not gaps else "retry" if attempt < ATTEMPTS else "error", started,
               {"decision": decision, "attempt": attempt}, {"shown": shown},
               f"read-back differs: {gaps}" if gaps else None)
        if not gaps:
            return {**shown, "attempts": attempt}
    raise ReadbackMismatch(f"after {ATTEMPTS} attempts the application shows {gaps} "
                           f"({', '.join(sorted(gaps))})")


def _trace(app, status: str, started: float, inputs: dict, output: dict, error: str | None) -> None:
    tracer = getattr(app, "tracer", None)   # a fake application in the tests may have none
    if tracer is not None:
        tracer.write("desktop.agent", status, (time.monotonic() - started) * 1000, inputs, output, error=error)
