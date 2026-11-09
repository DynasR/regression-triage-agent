"""The demo data says what the evaluations believe it says.

These checks read the file with sqlite3 directly: they verify the generator,
not the agent (the agent never opens the file)."""
import sqlite3

import pytest

from regression_triage.data import CAMPAIGNS, PRODUCTS, TICKETS, build, tolerance


def rows(db, sql, params=()):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_the_same_file_every_time(tmp_path):
    a, b = build(tmp_path / "a.db"), build(tmp_path / "b.db")
    dump = lambda p: list(sqlite3.connect(p).iterdump())  # noqa: E731
    assert dump(a) == dump(b)


def test_the_ground_truth_is_not_in_the_database(bank_db):
    tables = {name for (name,) in rows(bank_db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"campaigns", "test_cases", "results", "tolerances", "tickets", "release_notes"}
    schema = " ".join(sql for (sql,) in rows(bank_db, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
    for word in ("truth", "expected_change", "known_defect", "new_regression", "numerical_noise"):
        assert word not in schema


@pytest.mark.parametrize("campaign", CAMPAIGNS, ids=lambda c: f"C{c.id}")
def test_planted_gaps_and_only_them_are_out_of_tolerance(bank_db, campaign):
    planted = {(p.case, p.metric): p for p in campaign.planted}
    found = {}
    for case, metric, ref, cand in rows(bank_db, "SELECT case_id, metric, reference_value, candidate_value "
                                                 "FROM results WHERE campaign_id = ?", (campaign.id,)):
        if cand is None:
            found[(case, metric)] = None
        elif abs(cand - ref) > tolerance(metric, ref):
            found[(case, metric)] = abs(cand - ref) / tolerance(metric, ref)
    assert set(found) == set(planted)
    for key, ratio in found.items():
        shape = planted[key].shape
        if shape == "noise":
            assert 1.2 < ratio < 2.0
        elif shape == "shift":
            assert ratio >= 80
        else:
            assert ratio is None


@pytest.mark.parametrize("campaign", CAMPAIGNS, ids=lambda c: f"C{c.id}")
def test_every_justification_exists_and_fits(bank_db, campaign):
    notes = {nid: code for nid, code in rows(
        bank_db, "SELECT id, product_code FROM release_notes WHERE release = ?", (campaign.candidate,))}
    tickets = {tid: (code, status) for tid, code, status, *_ in TICKETS}
    for p in campaign.planted:
        product = p.case.split("-")[0]
        if p.truth == "expected_change":
            assert notes[p.evidence] == product
        elif p.truth == "known_defect":
            assert tickets[p.evidence] == (product, "open")
        else:
            assert p.evidence is None


def test_every_product_has_cases_and_metrics(bank_db):
    for code, (_, _, metrics) in PRODUCTS.items():
        assert rows(bank_db, "SELECT count(*) FROM test_cases WHERE product_code = ?", (code,))[0][0] == 5
        for metric in metrics:
            assert rows(bank_db, "SELECT count(*) FROM tolerances WHERE metric = ?", (metric,))[0][0] == 1
