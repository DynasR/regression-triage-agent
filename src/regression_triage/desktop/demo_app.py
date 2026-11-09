"""A fictional delivery desk: the Windows application the desktop agent drives.

It shows a campaign (number, number of gaps), has a "decision" field, a
"comment" field, a "Valider" button and a status line. It accepts three
decisions, in its own words: LIVRER, BLOQUER, REVUE HUMAINE.

Native Win32 controls (EDIT, BUTTON, STATIC), created with ctypes from the
standard library, each with a fixed control id. Not tkinter: Tk 8.6 draws its
own widgets, and neither UI Automation nor the win32 API sees their names,
their text or their values (measured; see docs/agent-desktop.md). A native
control is what pywinauto reads best, and what an application built with
WinDev or the Win32 API looks like from outside.

Run as a file, by the real interpreter, so that the window belongs to the
process the connector started:
    python demo_app.py --campaign 4 --gaps 7 [--fault rewrite|once|no-close] [--offscreen]

The faults stand for a misbehaving application, for the tests:
  rewrite   each validation puts the field back to LIVRER (a form that resets)
  once      only the first validation does
  no-close  the window ignores the close request
"""
import argparse
import ctypes
import sys

WINDOW_CLASS = "RtaDeliveryDesk"
TITLE = "Poste de contrôle de livraison"
CONTROL_IDS = {"campaign": 101, "gaps": 102, "decision": 201, "comment": 202, "validate": 301, "status": 401}
ACCEPTED = ("LIVRER", "BLOQUER", "REVUE HUMAINE")
WAITING = "En attente"
FAULTS = ("rewrite", "once", "no-close")


def status_after(decision: str) -> str:
    """What the status line shows after a validation."""
    return f"Enregistré : {decision}" if decision in ACCEPTED else "Refusé : décision inconnue"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fictional delivery desk")
    parser.add_argument("--campaign", default="4")
    parser.add_argument("--gaps", default="0")
    parser.add_argument("--fault", choices=FAULTS)
    parser.add_argument("--offscreen", action="store_true",
                        help="open far outside every screen, without taking the focus or a taskbar button "
                             "(for the tests: nothing appears on the user's desktop)")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("this application needs Windows", file=sys.stderr)
        return 2
    return _run(args)


def _run(args) -> int:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    LRESULT = wintypes.LPARAM
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.IsDialogMessageW.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    gdi32.GetStockObject.restype = wintypes.HANDLE

    WS_OVERLAPPED_FIXED = 0x00C00000 | 0x00080000 | 0x00020000   # caption, system menu, minimize box
    WS_CHILD, WS_VISIBLE, WS_BORDER, WS_TABSTOP = 0x40000000, 0x10000000, 0x00800000, 0x00010000
    ES_AUTOHSCROLL, BS_DEFPUSHBUTTON, WS_EX_CLIENTEDGE = 0x0080, 0x0001, 0x00000200
    WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_SETFONT = 0x0002, 0x0010, 0x0111, 0x0030
    BN_CLICKED, COLOR_BTNFACE, IDC_ARROW, DEFAULT_GUI_FONT = 0, 15, 32512, 17
    WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, SW_SHOW, SW_SHOWNOACTIVATE = 0x00000080, 0x08000000, 5, 4
    OFFSCREEN = -30000   # left of and above any real arrangement of monitors

    instance = kernel32.GetModuleHandleW(None)
    font = gdi32.GetStockObject(DEFAULT_GUI_FONT)
    controls: dict[str, int] = {}
    validations = [0]

    def text_of(hwnd) -> str:
        size = user32.GetWindowTextLengthW(hwnd) + 1
        buffer = ctypes.create_unicode_buffer(size)
        user32.GetWindowTextW(hwnd, buffer, size)
        return buffer.value

    def on_validate():
        validations[0] += 1
        decision = text_of(controls["decision"]).strip()
        if args.fault == "rewrite" or (args.fault == "once" and validations[0] == 1):
            decision = "LIVRER"
            user32.SetWindowTextW(controls["decision"], decision)
        user32.SetWindowTextW(controls["status"], status_after(decision))

    def window_proc(hwnd, message, wparam, lparam):
        if message == WM_COMMAND and wparam & 0xFFFF == CONTROL_IDS["validate"] and wparam >> 16 == BN_CLICKED:
            on_validate()
            return 0
        if message == WM_CLOSE and args.fault == "no-close":
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    callback = WNDPROC(window_proc)   # kept referenced for the life of the window
    window_class = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=callback, hInstance=instance,
                               hCursor=user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW)),
                               hbrBackground=wintypes.HBRUSH(COLOR_BTNFACE + 1), lpszClassName=WINDOW_CLASS)
    if not user32.RegisterClassExW(ctypes.byref(window_class)):
        raise ctypes.WinError(ctypes.get_last_error())
    # Off screen: far beyond any monitor, a tool window (no taskbar button), shown without
    # activation (the user's focus stays where it is). Still a visible window to Windows.
    x, y, ex_style = (OFFSCREEN, OFFSCREEN, WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) if args.offscreen else (200, 200, 0)
    main_window = user32.CreateWindowExW(ex_style, WINDOW_CLASS, TITLE, WS_OVERLAPPED_FIXED,
                                         x, y, 420, 260, None, None, instance, None)
    if not main_window:
        raise ctypes.WinError(ctypes.get_last_error())
    user32.ShowWindow(wintypes.HWND(main_window), SW_SHOWNOACTIVATE if args.offscreen else SW_SHOW)

    def child(kind, text, x, y, width, height, style=0, ex_style=0, name=None):
        control_id = CONTROL_IDS[name] if name else 0xFFFF   # 0xFFFF: a label of no interest
        hwnd = user32.CreateWindowExW(ex_style, kind, text, WS_CHILD | WS_VISIBLE | style, x, y, width, height,
                                      main_window, wintypes.HMENU(control_id), instance, None)
        user32.SendMessageW(hwnd, WM_SETFONT, font, 1)
        if name:
            controls[name] = hwnd
        return hwnd

    child("STATIC", f"Campagne {args.campaign}", 16, 14, 180, 20, name="campaign")
    child("STATIC", f"Écarts : {args.gaps}", 210, 14, 180, 20, name="gaps")
    child("STATIC", "Décision", 16, 50, 100, 20)   # read by UI Automation as the field's name
    child("EDIT", "", 120, 48, 270, 22, WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL, WS_EX_CLIENTEDGE, "decision")
    child("STATIC", "Commentaire", 16, 84, 100, 20)
    child("EDIT", "", 120, 82, 270, 22, WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL, WS_EX_CLIENTEDGE, "comment")
    child("BUTTON", "Valider", 290, 120, 100, 28, WS_TABSTOP | BS_DEFPUSHBUTTON, name="validate")
    child("STATIC", WAITING, 16, 170, 374, 20, name="status")

    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        if not user32.IsDialogMessageW(main_window, ctypes.byref(message)):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    return 0


if __name__ == "__main__":
    sys.exit(main())
