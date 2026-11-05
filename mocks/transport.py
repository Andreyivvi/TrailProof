"""How the mocks talk to TrailProof.

Two interchangeable clients expose ``await call(tool, **args) -> dict``:

* :class:`StdioToolClient` spawns ``python -m trailproof.server`` and speaks
  real MCP over stdio (what ``demo.py`` uses).
* :class:`DirectToolClient` calls a :class:`TrailProofService` in-process
  (fast unit tests, no subprocess).

Fail-open is the *client's* job (brief §4): transport failures raise
:class:`ServerUnreachable`, and the scripted buyer decides what to do.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from trailproof.service import TrailProofService


class ServerUnreachable(Exception):
    pass


class DirectToolClient:
    def __init__(self, service: TrailProofService):
        self.service = service
        self.calls: list[str] = []

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        self.calls.append(tool)
        fn = getattr(self.service, tool)
        return fn(**args)

    async def close(self) -> None:  # symmetry with the stdio client
        return None


class StdioToolClient:
    """A real MCP client session to a TrailProof server subprocess."""

    def __init__(self, db_path: str = ":memory:", python: str | None = None):
        self.db_path = db_path
        self.python = python or sys.executable
        self.calls: list[str] = []
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tools: list[str] = []

    async def __aenter__(self) -> "StdioToolClient":
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def open(self) -> None:
        env = dict(os.environ)
        env["TRAILPROOF_DB"] = self.db_path
        env.setdefault("PYTHONUTF8", "1")
        params = StdioServerParameters(command=self.python, args=["-m", "trailproof.server"], env=env)
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self.session = await self._stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            listed = await self.session.list_tools()
            self.tools = [t.name for t in listed.tools]
        except Exception as e:  # pragma: no cover - environment dependent
            await self.close()
            raise ServerUnreachable(str(e)) from e

    async def close(self) -> None:
        if self._stack is not None:
            stack, self._stack = self._stack, None
            self.session = None
            try:
                await stack.aclose()
            except Exception:
                pass

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        if self.session is None:
            raise ServerUnreachable("no session")
        self.calls.append(tool)
        try:
            result = await asyncio.wait_for(self.session.call_tool(tool, args), timeout=30)
        except Exception as e:
            raise ServerUnreachable(f"{tool}: {e}") from e
        sc = getattr(result, "structured_content", None)
        if isinstance(sc, dict):
            return sc.get("result", sc) if set(sc) == {"result"} else sc
        texts = [getattr(c, "text", None) for c in getattr(result, "content", []) or []]
        texts = [t for t in texts if t]
        if getattr(result, "is_error", False):
            return {"error": "TOOL_ERROR", "detail": " ".join(texts) or "unknown tool error"}
        if texts:
            try:
                return json.loads(texts[0])
            except json.JSONDecodeError:
                return {"error": "BAD_RESPONSE", "detail": texts[0]}
        return {"error": "EMPTY_RESPONSE", "detail": "server returned no content"}


class UnreachableToolClient:
    """Simulates a down server: every call raises ``ServerUnreachable``."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        self.calls.append(tool)
        raise ServerUnreachable("simulated outage")

    async def close(self) -> None:
        return None
