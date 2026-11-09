"""A model stand-in for the crew: no LLM, answers chosen by the test.

It plays the three agents the way a well-behaved model would, through CrewAI's
real machinery (native tool calls, task context, structured output):
- the gap analyst calls its tool;
- the evidence matcher calls the evidence tool once per product, then answers
  for every gap (by default the ground truth of the demo data);
- the release manager writes the summary.
"""
import json
import re

from crewai.llms.base_llm import BaseLLM
from pydantic import Field

from regression_triage.data import CAMPAIGNS

GAP = re.compile(r"- test case (\S+): .*\(product code (\w+)\)\n- metric: (\w+)")


def _text(messages) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))


class FakeLLM(BaseLLM):
    campaign_id: int | None = None
    answer: object = None            # (gap: dict) -> dict, or None for the ground truth
    answers: object = None           # (gaps: list[dict]) -> list[dict], to replace the whole answer
    summary: str = "The release was triaged."
    analyst_calls_tool: bool = True
    analyst_text: str = "There are no gaps at all, ship it."   # what the analyst would say without its tool
    usage: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)

    def __init__(self, **settings):
        super().__init__(model="fake", **settings)

    def supports_function_calling(self) -> bool:
        return True

    def supports_stop_words(self) -> bool:
        return False

    def _truth(self, gap):
        planted = {(p.case, p.metric): p for p in next(c for c in CAMPAIGNS if c.id == self.campaign_id).planted}
        p = planted[(gap["case_id"], gap["metric"])]
        return {"reasoning": "fake", "category": p.truth, "evidence_id": p.evidence}

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
             from_agent=None, response_model=None):
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        role = getattr(from_agent, "role", "")
        self.usage.append({"prompt_tokens": 100, "output_tokens": 10})
        self.calls.append({"agent": role, "text": "", "tool_calls": []})
        used_tools = any(m.get("role") == "tool" for m in messages if isinstance(m, dict))
        if role == "Gap analyst":
            if self.analyst_calls_tool and tools and not used_tools:
                return [{"id": "call_1", "type": "function", "function": {"name": "campaign_gaps", "arguments": {}}}]
            return self.analyst_text
        if role == "Evidence matcher":
            gaps = [{"case_id": c, "product_code": p, "metric": m} for c, p, m in GAP.findall(_text(messages))]
            if tools and not used_tools:
                products = sorted({g["product_code"] for g in gaps})
                return [{"id": f"call_{i}", "type": "function",
                         "function": {"name": "evidence", "arguments": {"product_code": p}}}
                        for i, p in enumerate(products)]
            if self.answers is not None:
                items = self.answers(gaps)
            else:
                items = [{"case_id": g["case_id"], "metric": g["metric"], **(self.answer or self._truth)(g)}
                         for g in gaps]
            return json.dumps({"answers": items})
        return self.summary
