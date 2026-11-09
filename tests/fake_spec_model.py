"""A stand-in for the model that writes test cases: no LLM, the cases are chosen by the test."""


def case(rule, inputs, expected, kind="limite", preconditions=None, id="CT-01"):
    """One proposed case, as the model would write it."""
    return {"id": id, "rule": rule, "kind": kind, "preconditions": preconditions or ["Campagne de non-régression"],
            "inputs": inputs, "expected": expected}


class FakeSpecModel:
    """Answers the cases given to it, or an error, and records what it was asked."""

    def __init__(self, cases=None, error=None):
        self._cases, self._error = list(cases or []), error
        self.calls = []          # (spec id, give_values) of every call
        self.usage = []

    def propose(self, spec, give_values=True):
        self.calls.append((spec.id, give_values))
        self.usage.append({"prompt_tokens": 100, "output_tokens": 10, "ms": 1})
        if self._error:
            return {"error": self._error}
        return {"cases": [dict(c) for c in self._cases]}
