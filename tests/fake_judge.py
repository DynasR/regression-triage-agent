"""A model stand-in for the tests: no LLM, answers chosen by the test."""
from regression_triage.data import CAMPAIGNS


class FakeJudge:
    """By default a perfect model: it answers the ground truth of the demo data."""

    def __init__(self, campaign_id=None, answer=None, summary="The release was triaged."):
        truth = {} if campaign_id is None else {
            (p.case, p.metric): p for p in next(c for c in CAMPAIGNS if c.id == campaign_id).planted}
        self._answer = answer or (lambda gap, evidence: {
            "reasoning": "fake", "category": truth[(gap["case_id"], gap["metric"])].truth,
            "evidence_id": truth[(gap["case_id"], gap["metric"])].evidence})
        self.summary = summary
        self.seen = []          # (gap, evidence) of every classify call
        self.facts = []         # facts given to every summarize call
        self.usage = []

    async def classify(self, gap, evidence, campaign):
        self.seen.append((gap, evidence))
        self.usage.append({"prompt_tokens": 100, "output_tokens": 10})
        return self._answer(gap, evidence)

    async def summarize(self, facts):
        self.facts.append(facts)
        return self.summary
