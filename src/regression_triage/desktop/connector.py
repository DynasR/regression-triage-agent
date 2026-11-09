"""The only way the agent touches the desktop: one application it started itself,
three kinds of action on named controls, every action traced.

- The connector starts the application and keeps its process. It finds the
  window by that process id, never by a title: a second window with the same
  title, the same class and the same program is not seen. `attach` refuses any
  process the connector did not start, and every action first checks that the
  window still belongs to that process.
- The list of actions is closed (ALLOWED): read a named label or field, type
  into a named field, click a named button. Anything else is refused here,
  before the window is even looked up, and the refusal is traced.
- No keystroke and no mouse event goes to the desktop. Typing sets the
  field's value (EM_REPLACESEL with win32, ValuePattern with UI Automation);
  clicking sends BM_CLICK to the button (win32) or invokes it (UI Automation).
  The user's pointer, keyboard and focus are never used.
- The application is closed on the way out, even on an error: a close
  request first, then the process is killed if it does not go.

Every action is one line of the run's trace (trace.py): node "desktop.<action>",
status ok, refused or error.
"""
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..trace import Tracer
from .demo_app import CONTROL_IDS, WINDOW_CLASS

DEMO_APP = Path(__file__).resolve().parent / "demo_app.py"
BACKENDS = ("win32", "uia")


class ActionRefused(Exception):
    """The action, or the control, is not on the list."""


class NotOurWindow(Exception):
    """The window or the process is not the one this connector started."""


@dataclass(frozen=True)
class Control:
    id: int
    kind: str                  # label, field, button
    actions: frozenset[str]


CONTROLS = {
    "campaign": Control(CONTROL_IDS["campaign"], "label", frozenset({"read"})),
    "gaps": Control(CONTROL_IDS["gaps"], "label", frozenset({"read"})),
    "status": Control(CONTROL_IDS["status"], "label", frozenset({"read"})),
    "decision": Control(CONTROL_IDS["decision"], "field", frozenset({"read", "type"})),
    "comment": Control(CONTROL_IDS["comment"], "field", frozenset({"read", "type"})),
    "validate": Control(CONTROL_IDS["validate"], "button", frozenset({"click"})),
}
ALLOWED = frozenset((action, name) for name, control in CONTROLS.items() for action in control.actions)


def check(action: str, control: str) -> Control:
    """The control, if this action on it is on the list. Raise ActionRefused otherwise."""
    if (action, control) not in ALLOWED:
        raise ActionRefused(f"{action!r} on {control!r} is not on the list")
    return CONTROLS[control]


def gui_python() -> str:
    """The real interpreter. A virtual environment's python.exe on Windows is a launcher
    that starts the real one as a child process: the window would belong to a process
    the connector never started, and the connector would rightly refuse it."""
    return getattr(sys, "_base_executable", None) or sys.executable


class DesktopConnector:
    """Use as a context manager: the application is started on entry and closed on exit."""

    def __init__(self, tracer: Tracer, *, backend: str = "win32", app_args=(), timeout: float = 10.0,
                 close_wait: float = 5.0):
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}")
        self.tracer, self.backend, self.app_args = tracer, backend, [str(a) for a in app_args]
        self.timeout, self.close_wait = timeout, close_wait
        self.process: subprocess.Popen | None = None
        self._window = None   # the wrapper of the window: its handle, its process id
        self._spec = None     # pywinauto's description of the same window, to find its controls

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    def __enter__(self) -> "DesktopConnector":
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- start, attach, close -------------------------------------------------------------------

    def start(self) -> None:
        started = time.monotonic()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no console window next to the application
        self.process = subprocess.Popen([gui_python(), str(DEMO_APP), *self.app_args], creationflags=flags)
        self._trace("launch", "ok", started, {"args": self.app_args}, {"pid": self.process.pid})
        self.attach(self.process.pid)

    def attach(self, pid: int) -> None:
        """Find the window of a process this connector started. Any other process is refused."""
        started = time.monotonic()
        if self.process is None or pid != self.process.pid or self.process.poll() is not None:
            error = NotOurWindow(f"process {pid} was not started by this connector")
            self._trace("attach", "refused", started, {"pid": pid}, error=error)
            raise error
        from pywinauto import Application
        try:
            app = Application(backend=self.backend).connect(process=pid, timeout=self.timeout)
            spec = app.window(class_name=WINDOW_CLASS)
            spec.wait("visible", timeout=self.timeout)
            window = spec.wrapper_object()
        except Exception as error:
            self._trace("attach", "error", started, {"pid": pid}, error=error)
            raise NotOurWindow(f"no window of class {WINDOW_CLASS} in process {pid}") from error
        if window.process_id() != pid:
            error = NotOurWindow(f"the window found belongs to process {window.process_id()}, not {pid}")
            self._trace("attach", "refused", started, {"pid": pid}, error=error)
            raise error
        self._window, self._spec = window, spec
        self._trace("attach", "ok", started, {"pid": pid}, {"backend": self.backend, "handle": window.handle})

    def close(self) -> None:
        """Close the application: a close request, then kill if it is still there. Never raises
        while an error is already on its way out."""
        if self.process is None:
            return
        started, how = time.monotonic(), "already closed"
        try:
            if self.process.poll() is None:
                how = "closed"
                try:
                    if self._window is None:
                        raise NotOurWindow("no window to ask")   # never attached: no one to ask
                    self._post_close()
                    self.process.wait(self.close_wait)
                except Exception:
                    how = "killed"
                    self.process.kill()
                    self.process.wait(self.close_wait)
            self._trace("close", "ok", started, {"pid": self.process.pid}, {"how": how})
        finally:
            self._window = self._spec = None

    def _post_close(self) -> None:
        import ctypes
        from ctypes import wintypes
        WM_CLOSE = 0x0010
        if not ctypes.windll.user32.PostMessageW(wintypes.HWND(self._window.handle), WM_CLOSE, 0, 0):
            raise OSError("the close request could not be sent")

    # --- the three actions ----------------------------------------------------------------------

    def read(self, control: str) -> str:
        return self.do("read", control)

    def type(self, control: str, text: str) -> None:
        self.do("type", control, text)

    def click(self, control: str) -> None:
        self.do("click", control)

    def do(self, action: str, control: str, value=None):
        """The single entry point: check the list, check the window, act, trace."""
        started = time.monotonic()
        inputs = {"control": control} if value is None else {"control": control, "value": value}
        try:
            spec = check(action, control)
        except ActionRefused as error:
            self._trace(action, "refused", started, inputs, error=error)
            raise
        try:
            self._ensure_ours()
            target = self._control(spec)
            if action == "read":
                out = self._read(target, spec)
            elif action == "type":
                self._type(target, str(value))
                out = None
            else:
                self._click(target)
                out = None
        except Exception as error:
            self._trace(action, "error", started, inputs, error=error)
            raise
        self._trace(action, "ok", started, inputs, {"value": out} if action == "read" else None)
        return out

    def _ensure_ours(self) -> None:
        if self._window is None or self.process is None or self.process.poll() is not None:
            raise NotOurWindow("no application started by this connector is running")
        if self._window.process_id() != self.process.pid:
            raise NotOurWindow("the window no longer belongs to the process this connector started")

    def _control(self, spec: Control):
        if self.backend == "win32":
            found = self._spec.child_window(control_id=spec.id)
        else:
            found = self._spec.child_window(auto_id=str(spec.id))
        return found.wrapper_object()

    def _read(self, target, spec: Control) -> str:
        if self.backend == "uia" and spec.kind == "field":
            # With UI Automation the name of an Edit is its label ("Décision"); its text is its Value.
            return target.iface_value.CurrentValue
        return target.window_text()

    def _type(self, target, text: str) -> None:
        if self.backend == "win32":
            target.set_edit_text(text)                # WM_SETTEXT-like messages to that field only
        else:
            target.iface_value.SetValue(text)         # ValuePattern: no keystroke
        self._settle(target)

    def _click(self, target) -> None:
        if not target.is_enabled():
            raise ActionRefused("the button is disabled")
        if self.backend == "win32":
            from pywinauto import win32defines
            target.send_message_timeout(win32defines.BM_CLICK, timeout=self.timeout)
        else:
            target.invoke()
        self._settle(target)

    def _settle(self, target) -> None:
        # A message sent and answered: the application's thread is back in its message loop,
        # so what it shows can be read back. Fails if the application hangs.
        import ctypes
        from ctypes import wintypes
        result = wintypes.DWORD()
        WM_NULL, SMTO_ABORTIFHUNG = 0x0000, 0x0002
        if not ctypes.windll.user32.SendMessageTimeoutW(wintypes.HWND(self._window.handle), WM_NULL, 0, 0,
                                                        SMTO_ABORTIFHUNG, int(self.timeout * 1000),
                                                        ctypes.byref(result)):
            raise RuntimeError("the application does not answer")

    def _trace(self, action: str, status: str, started: float, inputs: dict, output: dict | None = None,
               error: BaseException | None = None) -> None:
        self.tracer.write(f"desktop.{action}", status, (time.monotonic() - started) * 1000, inputs, output,
                          error=f"{type(error).__name__}: {error}" if error else None)
