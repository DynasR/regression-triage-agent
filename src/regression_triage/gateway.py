"""The only way to the bank's data: the read-only MCP server, scoped per node.

The agent never opens the database. It starts readonly-sql-mcp as a
subprocess and talks to it over stdio, like any MCP client. The server
guarantees that nothing can be written; this module adds least privilege on
top: each node of the graph gets a handle that only allows the tools and the
tables its job needs. A node that is not listed in SCOPES gets no handle at
all: scoring, routing, the human review and the report never touch the data,
and the language model is never given a tool.

A refused call is stopped here, before it reaches the server.
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from mcp import Client, StdioServerParameters
from sqlglot import exp
from sqlglot.errors import SqlglotError


class AccessDenied(Exception):
    """The node asked for a tool or a table outside its scope."""


class DataError(Exception):
    """The server refused the call, or returned only part of the data."""


@dataclass(frozen=True)
class Scope:
    tools: frozenset[str]
    tables: frozenset[str]


SCOPES = {
    "load_campaign": Scope(tools=frozenset({"query"}), tables=frozenset({"campaigns", "results"})),
    "compute_gaps": Scope(tools=frozenset({"query"}), tables=frozenset({"results", "test_cases", "tolerances"})),
    "classify_gaps": Scope(tools=frozenset({"query"}), tables=frozenset({"release_notes", "tickets"})),
}


def tables_read(sql: str) -> set[str]:
    """The tables a query reads. Raise AccessDenied when that cannot be known for sure."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except SqlglotError as error:
        raise AccessDenied(f"the query could not be parsed, so its tables cannot be checked: {error}") from error
    if len(statements) != 1:
        raise AccessDenied("one statement at a time")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise AccessDenied("only a SELECT can be checked against a scope")
    tables = set()
    for table in statement.find_all(exp.Table):
        if table.db or table.catalog:
            raise AccessDenied(f"schema-qualified table {table.sql()!r} is not allowed")
        # No special case is needed for the rest: a table-valued function such as
        # pragma_table_info('tickets') has an empty name, and a CTE alias is read as
        # a table name; neither is ever in a scope. The tables a CTE body reads are
        # found by this same walk.
        tables.add(table.name.lower())
    return tables


class BankData:
    """An open MCP session to the bank's data. Use as an async context manager."""

    def __init__(self, server: Any):
        self._client = Client(server)
        self.calls: list[tuple[str, str]] = []   # (node, tool) of every call that reached the server

    @classmethod
    def stdio(cls, db_path: Path) -> "BankData":
        return cls(StdioServerParameters(command=sys.executable,
                                         args=["-m", "readonly_sql_mcp", str(Path(db_path).resolve())]))

    async def __aenter__(self) -> "BankData":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    def scoped(self, node: str) -> "ScopedTools":
        if node not in SCOPES:
            raise AccessDenied(f"node {node!r} has no access to the data")
        return ScopedTools(self, node, SCOPES[node])

    async def _call(self, node: str, tool: str, arguments: dict) -> dict:
        self.calls.append((node, tool))
        result = await self._client.call_tool(tool, arguments)
        if result.is_error:
            text = " ".join(getattr(part, "text", "") for part in result.content)
            raise DataError(f"{tool} refused by the server: {text}")
        return result.structured_content


class ScopedTools:
    """What one node may do with the data, and nothing more."""

    def __init__(self, data: BankData, node: str, scope: Scope):
        self._data, self.node, self.scope = data, node, scope

    async def call(self, tool: str, **arguments) -> dict:
        if tool not in self.scope.tools:
            raise AccessDenied(f"node {self.node!r} may not call {tool!r}")
        if tool == "query":
            outside = tables_read(arguments["sql"]) - self.scope.tables
            if outside:
                raise AccessDenied(f"node {self.node!r} may not read {sorted(outside)}")
        return await self._data._call(self.node, tool, arguments)

    async def query(self, sql: str) -> list[dict]:
        out = await self.call("query", sql=sql)
        if out["truncated"]:
            # Triaging half a campaign and calling it done is worse than stopping.
            raise DataError(f"the server cut the result: {out['note']}")
        return [dict(zip(out["columns"], row)) for row in out["rows"]]
