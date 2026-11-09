"""The deterministic reads: the campaign, and its gaps.

Both go through a scoped MCP handle (see gateway.py). The gaps are found by
SQL on the server side, and every number attached to them (difference,
tolerance, ratio) is computed here, in code. The model never computes a
number; it only receives them.

A gap is a result where |candidate - reference| > max(abs_tol, rel_tol * |reference|).
Three cases that a naive query would silently drop are gaps too:
- a value that failed to price (NULL),
- a metric without a tolerance (any difference counts),
- a result whose test case is unknown (treated as highly critical).
"""


class CampaignError(Exception):
    """The campaign cannot be triaged: unknown, or nothing was tested."""


def _id(campaign_id) -> int:
    # Inlined in SQL (the query tool takes no parameters): it must be an int.
    if isinstance(campaign_id, bool) or not isinstance(campaign_id, int):
        raise TypeError(f"campaign id must be an int, got {campaign_id!r}")
    return campaign_id


async def load_campaign(tools, campaign_id: int) -> dict:
    cid = _id(campaign_id)
    rows = await tools.query("SELECT id, name, reference_release, candidate_release "
                             f"FROM campaigns WHERE id = {cid}")
    if not rows:
        raise CampaignError(f"no campaign {cid}")
    count = await tools.query(f"SELECT count(*) AS n FROM results WHERE campaign_id = {cid}")
    if count[0]["n"] == 0:
        # Zero results would mean zero gaps, and zero gaps would mean "go".
        raise CampaignError(f"campaign {cid} has no results: nothing was tested")
    return {**rows[0], "results": count[0]["n"]}


_TOLERANCE = "max(coalesce(t.abs_tol, 0), coalesce(t.rel_tol, 0) * abs(r.reference_value))"

GAPS_SQL = f"""
SELECT r.id AS result_id, r.case_id, c.product_code, c.product, c.criticality, r.metric,
       r.reference_value AS reference, r.candidate_value AS candidate,
       {_TOLERANCE} AS tolerance, t.metric IS NULL AS no_tolerance
FROM results r
LEFT JOIN test_cases c ON c.id = r.case_id
LEFT JOIN tolerances t ON t.metric = r.metric
WHERE r.campaign_id = {{cid}}
  AND (r.reference_value IS NULL OR r.candidate_value IS NULL
       OR abs(r.candidate_value - r.reference_value) > {_TOLERANCE})
ORDER BY r.id
"""


def describe_gap(row: dict) -> dict:
    reference, candidate = row["reference"], row["candidate"]
    failed = reference is None or candidate is None
    diff = None if failed else candidate - reference
    tolerance = row["tolerance"]
    ratio = abs(diff) / tolerance if diff is not None and tolerance else None
    relative = abs(diff) / abs(reference) if diff is not None and reference else None
    status = "failed" if failed else "no_tolerance" if row["no_tolerance"] else "out_of_tolerance"
    return {
        "result_id": row["result_id"], "case_id": row["case_id"],
        "product_code": row["product_code"] or "unknown",
        "product": row["product"] or "unknown product",
        "criticality": row["criticality"] or "high",
        "metric": row["metric"], "reference": reference, "candidate": candidate,
        "diff": diff, "tolerance": tolerance, "ratio": ratio, "relative": relative, "status": status,
    }


async def find_gaps(tools, campaign_id: int) -> list[dict]:
    cid = _id(campaign_id)
    rows = await tools.query(GAPS_SQL.format(cid=cid))
    return [describe_gap(row) for row in rows]
