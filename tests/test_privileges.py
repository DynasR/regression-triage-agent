"""Least privilege: each node reaches the data through the MCP server, and
only the tools and tables its job needs. Checked here on the gateway alone;
test_graph.py checks what each node really touched during a full run."""
import pytest
from readonly_sql_mcp.limits import Limits
from readonly_sql_mcp.server import build_server

from regression_triage.gateway import SCOPES, AccessDenied, BankData, DataError, Scope

pytestmark = pytest.mark.anyio


def test_the_scopes_are_exactly_these():
    # The README table mirrors this one. Widening a scope must be a visible decision.
    assert SCOPES == {
        "load_campaign": Scope(tools=frozenset({"query"}), tables=frozenset({"campaigns", "results"})),
        "compute_gaps": Scope(tools=frozenset({"query"}),
                              tables=frozenset({"results", "test_cases", "tolerances"})),
        "classify_gaps": Scope(tools=frozenset({"query"}), tables=frozenset({"release_notes", "tickets"})),
    }


async def test_a_node_without_a_scope_gets_nothing(bank_db):
    async with BankData(build_server(bank_db)) as data:
        for node in ("score_risk", "human_review", "report", "go", "block"):
            with pytest.raises(AccessDenied):
                data.scoped(node)


async def test_a_tool_outside_the_scope_is_refused_before_reaching_the_server(bank_db):
    async with BankData(build_server(bank_db)) as data:
        tools = data.scoped("compute_gaps")
        for tool, args in (("list_tables", {}), ("describe_table", {"table": "results"})):
            with pytest.raises(AccessDenied):
                await tools.call(tool, **args)
        assert data.calls == []


@pytest.mark.parametrize("sql", [
    "SELECT * FROM tickets",
    "SELECT r.id FROM results r JOIN tickets t ON t.product_code = r.case_id",
    "SELECT id FROM results WHERE case_id IN (SELECT id FROM tickets)",
    "SELECT id FROM results UNION SELECT id FROM release_notes",
    "WITH tolerances AS (SELECT * FROM tickets) SELECT * FROM tolerances",
    "SELECT name FROM sqlite_master",
    "SELECT * FROM pragma_table_info('tickets')",
    "SELECT * FROM main.tickets",
    "SELECT * FROM results; SELECT * FROM tickets",
    "this is not SQL",
])
async def test_a_table_outside_the_scope_is_refused(bank_db, sql):
    async with BankData(build_server(bank_db)) as data:
        with pytest.raises(AccessDenied):
            await data.scoped("compute_gaps").query(sql)
        assert data.calls == []


async def test_a_query_inside_the_scope_goes_through(bank_db):
    async with BankData(build_server(bank_db)) as data:
        rows = await data.scoped("compute_gaps").query(
            "SELECT r.metric, t.abs_tol FROM results r JOIN tolerances t ON t.metric = r.metric "
            "WHERE r.campaign_id = 1 AND r.case_id = 'IRS-001' ORDER BY r.metric")
        assert rows == [{"metric": "DV01", "abs_tol": 0.05}, {"metric": "PV", "abs_tol": 1.0}]
        assert data.calls == [("compute_gaps", "query")]


async def test_a_cut_result_is_an_error_not_half_the_data(bank_db):
    async with BankData(build_server(bank_db, Limits(max_rows=3))) as data:
        with pytest.raises(DataError, match="cut"):
            await data.scoped("compute_gaps").query("SELECT id FROM results")


async def test_a_refusal_of_the_server_is_an_error(bank_db):
    async with BankData(build_server(bank_db)) as data:
        with pytest.raises(DataError):
            await data.scoped("compute_gaps").query("SELECT no_such_column FROM results")


async def test_the_real_transport_is_a_subprocess_over_stdio(bank_db):
    async with BankData.stdio(bank_db) as data:
        rows = await data.scoped("load_campaign").query("SELECT count(*) AS n FROM campaigns")
    assert rows == [{"n": 8}]
