"""The bank's data for the CrewAI version: the same gateway, another MCP client, one more thread.

Two differences with the LangGraph version, both forced by CrewAI, none in the rules:
- crewai pins mcp<2, so the client is the mcp 1 ClientSession, adapted to the
  interface gateway.BankData expects. The scopes and the checks (SCOPES,
  ScopedTools, tables_read) are gateway.py's, unchanged.
- CrewAI calls tools synchronously, from its own threads, and the MCP session
  is asynchronous. The session lives in one background thread with its own
  event loop; tools hand it their coroutines and wait for the answer.
"""
import asyncio
import concurrent.futures
import os
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace

from ..gateway import BankData

ROOT = Path(__file__).resolve().parents[3]


def server_python() -> str:
    """The interpreter that runs readonly-sql-mcp: one with mcp 2, the LangGraph environment by default."""
    chosen = os.environ.get("TRIAGE_SERVER_PYTHON")
    if chosen:
        return chosen
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("no interpreter with readonly-sql-mcp: create .venv (see README) or set TRIAGE_SERVER_PYTHON")


class Mcp1Client:
    """The mcp 1 ClientSession over stdio, with the three methods gateway.BankData uses."""

    def __init__(self, params):
        self._params = params

    async def __aenter__(self):
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(self._params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._stack.__aexit__(*exc)

    async def call_tool(self, name: str, arguments: dict):
        result = await self._session.call_tool(name, arguments)
        # mcp 1 names these fields in camelCase, mcp 2 in snake_case.
        return SimpleNamespace(is_error=result.isError, content=result.content,
                               structured_content=result.structuredContent)


class CrewBankData(BankData):
    """gateway.BankData with the mcp 1 client: same scopes, same checks, same record of calls."""

    def __init__(self, db_path: Path):
        from mcp import StdioServerParameters
        self._client = Mcp1Client(StdioServerParameters(
            command=server_python(), args=["-m", "readonly_sql_mcp", str(Path(db_path).resolve())]))
        self.calls = []


class BankSession:
    """An open MCP session on its own thread, usable from CrewAI's synchronous tools.

    The session is entered and left inside one coroutine (anyio requires it),
    which then serves the calls handed to it through a queue.
    """

    def __init__(self, data: BankData, timeout: float = 120):
        self.data, self._timeout = data, timeout

    def __enter__(self) -> "BankSession":
        self._loop = asyncio.new_event_loop()
        self._ready = concurrent.futures.Future()
        self._thread = threading.Thread(target=self._loop.run_until_complete, args=(self._serve(),),
                                        name="mcp-session", daemon=True)
        self._thread.start()
        self._ready.result(timeout=self._timeout)   # raises if the server could not start
        return self

    def __exit__(self, *exc) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        self._thread.join(timeout=self._timeout)
        self._loop.close()

    async def _serve(self) -> None:
        self._queue = asyncio.Queue()
        try:
            async with self.data:
                self._ready.set_result(True)
                while (job := await self._queue.get()) is not None:
                    make, future = job
                    try:
                        future.set_result(await make())
                    except Exception as error:   # handed back to the calling thread
                        future.set_exception(error)
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)
            else:
                raise

    def run(self, make):
        """Run make() (a coroutine function) in the session's thread and wait for its result."""
        future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (make, future))
        return future.result(timeout=self._timeout)

    def scoped(self, node: str):
        return self.data.scoped(node)

    @property
    def calls(self) -> list:
        return self.data.calls


def open_bank(db_path: Path) -> BankSession:
    return BankSession(CrewBankData(db_path))

