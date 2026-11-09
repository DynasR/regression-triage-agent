"""The desktop agent: carries the triage's decision into a Windows application.

connector.py  starts the application, finds it by its process id, allows three actions
agent.py      a fixed plan: type, validate, read back, retry once
demo_app.py   the application: a fictional delivery desk, native Win32 controls
__main__.py   python -m regression_triage.desktop <campaign>

Nothing here is imported by the triage itself, and pywinauto is imported only
when a window is really driven: the list of actions and its tests load anywhere.
"""
