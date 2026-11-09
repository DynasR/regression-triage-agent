"""The decision memo: numbers from the state, words from the model.

Every number of the memo (score, counts, values, ratios) is written by this
module from the state. The model only writes the summary paragraph, and that
paragraph is checked: if it contains a number that is not in the facts it was
given, it is thrown away and replaced by a plain sentence. A memo that goes to
a release committee must not carry a figure nobody computed.
"""
import re

from .data import CATEGORIES
from .risk import BLOCK_FROM, HUMAN_REVIEW_FROM

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER.findall(text or "")}


def counts(state: dict) -> dict:
    found = {category: 0 for category in CATEGORIES}
    for line in state["risk"]["lines"]:
        found[line["category"]] += 1
    return found


def facts(state: dict) -> str:
    """The only text the model sees when writing the summary."""
    campaign, risk, decision = state["campaign"], state["risk"], state["decision"]
    lines = [
        f"Campaign: {campaign['name']}, candidate release {campaign['candidate_release']} "
        f"against reference release {campaign['reference_release']}.",
        f"Results compared: {campaign['results']}. Gaps out of tolerance: {len(state['gaps'])}.",
        "Gaps by category: " + ", ".join(f"{c}: {n}" for c, n in counts(state).items()) + ".",
        f"Risk score computed by the rules: {risk['score']}. The rules send a release to a human reviewer "
        f"from a score of {HUMAN_REVIEW_FROM} and block it from a score of {BLOCK_FROM}. Route: {risk['route']}.",
        (f"Final decision: {decision['final']}, taken by the human reviewer {decision['reviewer']}"
         f" (comment: {decision['comment'] or 'none'})." if decision["decided_by"] == "human" else
         f"Final decision: {decision['final']}, taken automatically by the rules, without a human."),
    ]
    by_id = {c["result_id"]: c for c in state.get("classifications", [])}
    for line in risk["lines"]:
        c = by_id.get(line["result_id"], {})
        lines.append(f"- {line['case_id']} {line['metric']}: {line['category']}"
                     + (f", justified by {c['evidence_id']}" if c.get("evidence_id") else "")
                     + (f", the model's answer was rejected: {c['rejected_because']}" if c.get("rejected_because") else "")
                     + f", product criticality {line['criticality']}.")
    return "\n".join(lines)


def check_summary(text: str | None, given: str) -> dict:
    if not text:
        return {"text": FALLBACK, "accepted": False, "unknown_numbers": [], "why": "no summary from the model"}
    unknown = sorted(numbers_in(text) - numbers_in(given))
    if unknown:
        return {"text": FALLBACK, "accepted": False, "unknown_numbers": unknown,
                "why": "the summary contained numbers that are not in the facts"}
    return {"text": text, "accepted": True, "unknown_numbers": [], "why": None}


FALLBACK = "The generated summary was not used. The tables below are the decision."


def _num(value, digits=2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def memo(state: dict) -> str:
    campaign, risk, decision, summary = state["campaign"], state["risk"], state["decision"], state["summary"]
    by_id = {c["result_id"]: c for c in state.get("classifications", [])}
    gaps = {g["result_id"]: g for g in state["gaps"]}
    out = [
        f"# Release decision: {campaign['candidate_release']} — {decision['final'].upper()}",
        "",
        f"Campaign {campaign['id']} *{campaign['name']}*: candidate {campaign['candidate_release']} "
        f"against reference {campaign['reference_release']}, {campaign['results']} results compared, "
        f"{len(state['gaps'])} out of tolerance.",
        "",
        f"**Route:** {risk['route']} (risk score {risk['score']}). **Decision:** {decision['final']}, "
        f"by {decision['decided_by']}"
        + (f" — {decision['reviewer']}: “{decision['comment']}”" if decision["decided_by"] == "human" else "") + ".",
        "",
        "## Summary",
        "",
        summary["text"] + ("" if summary["accepted"] else f" *(Reason: {summary['why']}.)*"),
        "",
        "## Gaps",
        "",
    ]
    if not risk["lines"]:
        out.append("No result out of tolerance.")
    else:
        out += ["| Case | Metric | Reference | Candidate | × tolerance | Category | Justification | Points |",
                "|---|---|---:|---:|---:|---|---|---:|"]
        for line in risk["lines"]:
            g, c = gaps[line["result_id"]], by_id.get(line["result_id"], {})
            why = c.get("evidence_id") or ("rejected: " + c["rejected_because"] if c.get("rejected_because") else "")
            ratio = "failed" if g["status"] == "failed" else "no tolerance" if g["ratio"] is None else _num(g["ratio"], 1)
            out.append(f"| {line['case_id']} | {line['metric']} | {_num(g['reference'])} | {_num(g['candidate'])} "
                       f"| {ratio} | {line['category']} | {why} | {line['points']} |")
    out += ["", "*Numbers computed by code from the campaign data; categories proposed by a local model and "
                "checked by code; summary written by the model and checked for numbers it was not given.*", ""]
    return "\n".join(out)
