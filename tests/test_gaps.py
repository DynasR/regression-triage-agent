"""Gaps are computed by SQL through the MCP server, never by the model.

A tiny hand-made database puts values exactly on the edges: at the
tolerance, just above it, on the relative branch, without a tolerance,
without a value."""
import sqlite3

import pytest
from readonly_sql_mcp.server import build_server

from regression_triage.campaign import CampaignError, find_gaps, load_campaign
from regression_triage.data import SCHEMA
from regression_triage.gateway import BankData

pytestmark = pytest.mark.anyio

RESULTS = [
    # id, campaign, case, metric, reference, candidate
    (1, 1, "A-1", "PV", 100.0, 101.0),            # exactly at the tolerance (1.0): not a gap
    (2, 1, "A-1", "PV", 100.0, 101.5),            # 1.5 x the tolerance
    (3, 1, "A-1", "PV", 100.0, 98.5),             # same, downwards
    (4, 1, "B-1", "PV", 3_000_000.0, 3_000_002.5),  # relative tolerance is 3.0: not a gap
    (5, 1, "B-1", "PV", 3_000_000.0, 3_000_003.5),  # above the relative tolerance
    (6, 1, "A-1", "VEGA", 10.0, 10.0001),         # no tolerance defined for VEGA: any difference counts
    (7, 1, "A-1", "DV01", 50.0, None),            # the candidate failed to price
    (8, 1, "A-1", "DV01", None, 50.0),            # the reference failed to price
    (9, 2, "A-1", "PV", 100.0, 200.0),            # another campaign
    (10, 1, "A-1", "DV01", 50.0, 50.0),           # identical
    (11, 1, "Z-9", "PV", 100.0, 150.0),           # a case missing from test_cases
]


@pytest.fixture
def tiny_db(tmp_path):
    path = tmp_path / "tiny.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?)",
                    [(1, "one", "1.0", "1.1", "2026-01-01"), (2, "two", "1.1", "1.2", "2026-02-01"),
                     (3, "empty", "1.2", "1.3", "2026-03-01")])
    con.executemany("INSERT INTO test_cases VALUES (?, ?, ?, ?, ?)",
                    [("A-1", "A", "Product A", "low", "a"), ("B-1", "B", "Product B", "medium", "b")])
    con.executemany("INSERT INTO tolerances VALUES (?, ?, ?)", [("PV", 1.0, 1e-6), ("DV01", 0.05, 1e-6)])
    con.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?)", RESULTS)
    con.commit()
    con.close()
    return path


async def gaps_of(db, campaign_id=1):
    async with BankData(build_server(db)) as data:
        return await find_gaps(data.scoped("compute_gaps"), campaign_id)


async def test_only_differences_strictly_above_the_tolerance(tiny_db):
    assert [g["result_id"] for g in await gaps_of(tiny_db)] == [2, 3, 5, 6, 7, 8, 11]


async def test_the_numbers_are_computed_by_the_code(tiny_db):
    gaps = {g["result_id"]: g for g in await gaps_of(tiny_db)}
    assert gaps[2]["diff"] == pytest.approx(1.5) and gaps[2]["tolerance"] == 1.0
    assert gaps[2]["ratio"] == pytest.approx(1.5) and gaps[2]["relative"] == pytest.approx(0.015)
    assert gaps[3]["diff"] == pytest.approx(-1.5) and gaps[3]["ratio"] == pytest.approx(1.5)
    assert gaps[5]["tolerance"] == pytest.approx(3.0) and gaps[5]["ratio"] == pytest.approx(3.5 / 3)
    assert gaps[2]["status"] == gaps[5]["status"] == "out_of_tolerance"


async def test_a_metric_without_tolerance_is_not_silently_ignored(tiny_db):
    gap = next(g for g in await gaps_of(tiny_db) if g["result_id"] == 6)
    assert gap["status"] == "no_tolerance" and gap["ratio"] is None


async def test_a_value_that_failed_to_price_is_a_gap(tiny_db):
    gaps = {g["result_id"]: g for g in await gaps_of(tiny_db)}
    assert gaps[7]["status"] == gaps[8]["status"] == "failed"
    assert gaps[7]["diff"] is None and gaps[7]["ratio"] is None


async def test_an_unknown_case_is_treated_as_highly_critical(tiny_db):
    gap = next(g for g in await gaps_of(tiny_db) if g["result_id"] == 11)
    assert gap["criticality"] == "high" and gap["product"] == "unknown product"


async def test_the_product_and_its_criticality_come_with_each_gap(tiny_db):
    gap = next(g for g in await gaps_of(tiny_db) if g["result_id"] == 5)
    assert (gap["case_id"], gap["product_code"], gap["criticality"], gap["metric"]) == ("B-1", "B", "medium", "PV")


@pytest.mark.parametrize("bad", ["1 OR 1=1", 1.0, True, None])
async def test_the_campaign_id_cannot_carry_sql(bad):
    # Refused before any call: no session is even needed.
    with pytest.raises(TypeError):
        await find_gaps(object(), bad)
    with pytest.raises(TypeError):
        await load_campaign(object(), bad)


async def test_load_campaign(tiny_db):
    async with BankData(build_server(tiny_db)) as data:
        campaign = await load_campaign(data.scoped("load_campaign"), 1)
    assert campaign == {"id": 1, "name": "one", "reference_release": "1.0", "candidate_release": "1.1",
                        "results": 10}


@pytest.mark.parametrize("campaign_id", [3, 42])
async def test_a_campaign_with_nothing_tested_is_an_error_not_a_go(tiny_db, campaign_id):
    async with BankData(build_server(tiny_db)) as data:
        with pytest.raises(CampaignError):
            await load_campaign(data.scoped("load_campaign"), campaign_id)


async def test_on_the_demo_data_the_planted_gaps_are_found(bank_db):
    from regression_triage.data import CAMPAIGNS
    async with BankData(build_server(bank_db)) as data:
        for campaign in CAMPAIGNS:
            found = {(g["case_id"], g["metric"]) for g in await find_gaps(data.scoped("compute_gaps"), campaign.id)}
            assert found == {(p.case, p.metric) for p in campaign.planted}
