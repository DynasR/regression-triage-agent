"""The same triage, orchestrated with CrewAI instead of LangGraph.

Only the orchestration changes. The data gateway and its scopes, the gaps, the
checks on the model's answers, the score, the route and the memo are the same
code as the LangGraph version (gateway.py, campaign.py, risk.py, report.py).

CrewAI lives in its own environment (.venv-crew): crewai 1.15 pins mcp<2,
while this project and readonly-sql-mcp use mcp 2. So the CrewAI process
speaks to the MCP server with the mcp 1 client (bank.py), and the server
itself runs with the interpreter of the LangGraph environment.

Nothing leaves the machine: CrewAI's telemetry and its trace upload are on by
default, and are switched off here, before crewai is imported.
"""
import os

# Forced, not defaulted: a run of this agent must not send anything out, whatever the shell says.
TELEMETRY_OFF = {"CREWAI_DISABLE_TELEMETRY": "true", "OTEL_SDK_DISABLED": "true", "CREWAI_TRACING_ENABLED": "false"}
os.environ.update(TELEMETRY_OFF)
