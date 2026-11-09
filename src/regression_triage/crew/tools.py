"""The two tools of the crew. Each one is the only door of one agent to the data.

campaign_gaps  for the gap analyst. Takes no argument: it is bound to the
               campaign under review, so the model cannot ask for another
               one. Reads through the scopes of load_campaign and
               compute_gaps. Its result is the task's answer as it is
               (result_as_answer): the model never retypes a number.
evidence       for the evidence matcher. One product at a time, and only a
               product that has a gap. Reads through the scope of
               classify_gaps.

The release manager has no tool. A tool reaches the data only through a view
of the bank that allows its own scopes (gateway.SCOPES), so giving a tool to
the wrong agent would not widen what it can read either.
"""
import time
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ..campaign import fetch_evidence, find_gaps, load_campaign
from ..gateway import AccessDenied
from ..judge import evidence_lines, gap_lines

# Which scopes of gateway.SCOPES each tool may use. Pinned by a test.
TOOL_SCOPES = {
    "campaign_gaps": frozenset({"load_campaign", "compute_gaps"}),
    "evidence": frozenset({"classify_gaps"}),
}


class BankView:
    """The bank, as one tool may see it: only the scopes listed in TOOL_SCOPES for it."""

    def __init__(self, bank, tool: str):
        self._bank, self.tool, self.allowed = bank, tool, TOOL_SCOPES[tool]

    def scoped(self, node: str):
        if node not in self.allowed:
            raise AccessDenied(f"tool {self.tool!r} may not use the scope of {node!r}")
        return self._bank.scoped(node)

    def run(self, make):
        return self._bank.run(make)


def gaps_text(campaign: dict, gaps: list[dict]) -> str:
    lines = [f"Campaign: {campaign['name']} (reference release {campaign['reference_release']}, "
             f"candidate release {campaign['candidate_release']})",
             f"Gaps found by the code: {len(gaps)}"]
    if not gaps:
        lines.append("No gap: every result is within its tolerance.")
    for number, gap in enumerate(gaps, 1):
        lines += ["", f"Gap {number}:", *gap_lines(gap)]
    return "\n".join(lines)


class NoArguments(BaseModel):
    pass


class TracedTool(BaseTool):
    """A tool that writes one trace line per call, like a node of the graph."""
    tracer: Any = Field(default=None, exclude=True)

    def _run(self, **arguments) -> str:
        started = time.monotonic()
        try:
            text = self._do(**arguments)
        except Exception as error:
            if self.tracer:
                self.tracer.write(f"tool:{self.name}", "error", (time.monotonic() - started) * 1000, arguments,
                                  error=f"{type(error).__name__}: {error}")
            raise
        if self.tracer:
            self.tracer.write(f"tool:{self.name}", "ok", (time.monotonic() - started) * 1000, arguments,
                              {"result": text})
        return text


class CampaignGapsTool(TracedTool):
    name: str = "campaign_gaps"
    description: str = ("Finds every gap of the campaign under review, with the numbers computed by code. "
                        "Takes no argument.")
    args_schema: type[BaseModel] = NoArguments
    result_as_answer: bool = True
    bank: Any = Field(exclude=True)
    campaign_id: int
    # What the code found, kept for the code: the model's text is never parsed back. Any, not dict:
    # pydantic would copy a dict, and the run would lose what the tool found.
    case: Any = Field(exclude=True)

    def _do(self, **_) -> str:
        load, gaps_scope = self.bank.scoped("load_campaign"), self.bank.scoped("compute_gaps")
        campaign = self.bank.run(lambda: load_campaign(load, self.campaign_id))
        gaps = self.bank.run(lambda: find_gaps(gaps_scope, self.campaign_id))
        self.case.update(campaign=campaign, gaps=gaps)
        return gaps_text(campaign, gaps)


class ProductArgument(BaseModel):
    product_code: str = Field(description="The product code of a gap, as written in the gaps (for example FXBAR).")


class EvidenceTool(TracedTool):
    name: str = "evidence"
    description: str = ("The release notes of the candidate release and the bug tracker tickets about one product. "
                        "Call it once for each product code that has a gap.")
    args_schema: type[BaseModel] = ProductArgument
    bank: Any = Field(exclude=True)
    case: Any = Field(exclude=True)

    def _do(self, product_code: str = "", **_) -> str:
        gaps = self.case.get("gaps")
        if gaps is None:
            raise AccessDenied("no gap is known yet: the evidence is only for the products of the gaps")
        mine = [g for g in gaps if g["product_code"] == product_code]
        if not mine:
            known = sorted({g["product_code"] for g in gaps})
            raise AccessDenied(f"product {product_code!r} has no gap in this campaign; products with gaps: {known}")
        scope = self.bank.scoped("classify_gaps")
        evidence = self.bank.run(lambda: fetch_evidence(scope, self.case["campaign"], mine))
        self.case.setdefault("evidence_shown", {})[product_code] = evidence
        return "\n".join([f"Product {product_code}:", *evidence_lines(evidence, self.case["campaign"]["candidate_release"])])


def make_tools(bank, campaign_id: int, case: dict, tracer=None) -> dict:
    """The tools of one run, each with its own view of the bank, sharing the run's case file."""
    return {"campaign_gaps": CampaignGapsTool(bank=BankView(bank, "campaign_gaps"), campaign_id=campaign_id,
                                              case=case, tracer=tracer),
            "evidence": EvidenceTool(bank=BankView(bank, "evidence"), case=case, tracer=tracer)}
