"""How the crew calls the local model, and what leaves the machine. No LLM runs here."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

from regression_triage.crew.llm import NUM_CTX, OllamaLLM, to_ollama


class FakeOllama:
    def __init__(self, content="", tool_calls=None):
        self.content, self.tool_calls, self.requests = content, tool_calls, []

    def chat(self, **request):
        self.requests.append(request)
        return SimpleNamespace(message=SimpleNamespace(content=self.content, tool_calls=self.tool_calls),
                               prompt_eval_count=12, eval_count=3)


def llm_with(client) -> OllamaLLM:
    llm = OllamaLLM()
    llm._client = client
    return llm


def test_the_model_is_called_like_the_langgraph_version():
    client = FakeOllama("hello")
    llm = llm_with(client)
    assert llm.call([{"role": "user", "content": "hi"}]) == "hello"
    request = client.requests[0]
    assert request["model"] == "qwen3.5:9b" and request["think"] is False
    assert request["options"] == {"temperature": 0, "seed": 42, "num_ctx": NUM_CTX} and NUM_CTX == 8192
    assert llm.usage == [{"prompt_tokens": 12, "output_tokens": 3, "ms": llm.usage[0]["ms"]}]
    assert llm.calls[0]["text"] == "hello"


def test_a_structured_answer_is_asked_with_its_schema_when_no_tool_is_offered():
    from pydantic import BaseModel

    class Answer(BaseModel):
        reasoning: str

    client = FakeOllama('{"reasoning": "r"}')
    llm_with(client).call("hi", response_model=Answer)
    assert client.requests[0]["format"] == Answer.model_json_schema()


def test_tool_calls_come_back_in_the_format_crewai_runs():
    call = SimpleNamespace(function=SimpleNamespace(name="evidence", arguments={"product_code": "FXBAR"}))
    client = FakeOllama(tool_calls=[call])
    tools = [{"type": "function", "function": {"name": "evidence", "parameters": {}}}]
    answer = llm_with(client).call("hi", tools=tools)
    assert answer[0]["function"] == {"name": "evidence", "arguments": {"product_code": "FXBAR"}}
    assert client.requests[0]["tools"] == tools


def test_crewai_messages_are_translated_for_ollama():
    assistant = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "evidence", "arguments": json.dumps({"product_code": "X"})}}]}
    assert to_ollama(assistant) == {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "evidence", "arguments": {"product_code": "X"}}}]}
    tool = {"role": "tool", "tool_call_id": "c1", "name": "evidence", "content": "notes"}
    assert to_ollama(tool) == {"role": "tool", "content": "notes", "tool_name": "evidence"}


def test_telemetry_is_off_whatever_the_shell_says():
    # CrewAI sends anonymous telemetry, and may upload traces, unless told not to.
    env = {**os.environ, "CREWAI_DISABLE_TELEMETRY": "false", "OTEL_SDK_DISABLED": "false",
           "CREWAI_TRACING_ENABLED": "true"}
    code = ("import os\n"
            "import regression_triage.crew\n"
            "from crewai.telemetry.telemetry import Telemetry\n"
            "print(Telemetry._is_telemetry_disabled(), os.environ['CREWAI_TRACING_ENABLED'])\n")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.split() == ["True", "false"], out.stderr[-2000:]
