"""The local model, plugged into CrewAI with the same settings as the LangGraph version.

CrewAI's own route to Ollama is its OpenAI-compatible endpoint. Measured on
this machine (Ollama 0.40.15): that endpoint ignores `num_ctx` and serves
qwen3.5:9b with a 4,096-token context, silently cutting longer prompts, and
it thinks unless told `reasoning_effort="none"`. A CrewAI prompt (role, goal,
backstory, tools, the previous task's output) is longer than ours.

So this class subclasses CrewAI's BaseLLM (its documented way to bring a
model) and calls Ollama's native API exactly like judge.OllamaJudge does:
temperature 0, seed 42, thinking off, an 8,192-token context. The two
versions differ by orchestration, not by model settings.

It also records each call (tokens, milliseconds, the agent that called, the
raw text), for the trace and for the evaluations' determinism measure.
"""
import json
import time
from typing import Any

from crewai.llms.base_llm import BaseLLM
from pydantic import Field, PrivateAttr

from ..judge import DEFAULT_MODEL, SEED

NUM_CTX = 8192


def _arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}


def to_ollama(message: dict) -> dict:
    """One CrewAI message (OpenAI format) as Ollama's native chat API expects it."""
    content = message.get("content")
    if isinstance(content, list):   # parts: keep the text
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    out = {"role": message["role"], "content": content or ""}
    if message.get("tool_calls"):
        out["tool_calls"] = [{"function": {"name": call["function"]["name"],
                                           "arguments": _arguments(call["function"]["arguments"])}}
                             for call in message["tool_calls"]]
    if message["role"] == "tool" and message.get("name"):
        out["tool_name"] = message["name"]
    return out


class OllamaLLM(BaseLLM):
    model: str = DEFAULT_MODEL
    seed: int = SEED
    host: str | None = None
    context_window_size: int | None = NUM_CTX
    usage: list = Field(default_factory=list)   # drained by the tracer, like OllamaJudge.usage
    calls: list = Field(default_factory=list)   # every call of the run, kept: agent, tokens, raw text
    _client: Any = PrivateAttr(default=None)

    def __init__(self, model: str = DEFAULT_MODEL, **settings):
        # BaseLLM checks that a model name is given; give it the default one.
        super().__init__(model=model, **settings)

    def supports_function_calling(self) -> bool:
        return True

    def supports_stop_words(self) -> bool:
        return False

    def _chat(self, **kwargs):
        if self._client is None:
            import ollama
            self._client = ollama.Client(host=self.host)
        return self._client.chat(**kwargs)

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
             from_agent=None, response_model=None) -> Any:
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        request = {"model": self.model, "messages": [to_ollama(m) for m in messages], "think": False,
                   "options": {"temperature": 0, "seed": self.seed, "num_ctx": NUM_CTX}}
        if tools:
            request["tools"] = tools
        elif response_model is not None:
            request["format"] = response_model.model_json_schema()
        agent = getattr(from_agent, "role", None)
        started = time.monotonic()
        try:
            response = self._chat(**request)
        except Exception as error:
            self.calls.append({"agent": agent, "error": f"model call failed: {type(error).__name__}: {error}"[:300]})
            raise
        message = response.message
        tool_calls = [{"id": f"call_{len(self.calls)}_{i}", "type": "function",
                       "function": {"name": call.function.name, "arguments": dict(call.function.arguments or {})}}
                      for i, call in enumerate(message.tool_calls or [])]
        used = {"prompt_tokens": response.prompt_eval_count, "output_tokens": response.eval_count,
                "ms": round((time.monotonic() - started) * 1000)}
        self.usage.append(used)
        self.calls.append({"agent": agent, **used, "text": message.content or "",
                           "tool_calls": [[c["function"]["name"], c["function"]["arguments"]] for c in tool_calls]})
        if tool_calls:
            return tool_calls
        return message.content or ""
