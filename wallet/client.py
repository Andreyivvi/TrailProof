"""A small MCP streamable-HTTP client for talking to a TrailProof ledger.

Standard library only. ``await client.call(tool, **args)`` returns the tool's
dict, exactly like the in-process and stdio clients in ``mocks.transport``,
so the wallet, the demo shop and the tests can use any of them.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2025-06-18"


class LedgerUnreachable(Exception):
    """The ledger could not be reached. Callers decide whether to fail open."""


class HttpToolClient:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self.calls: list[str] = []
        self._id = 0
        self._lock = threading.Lock()
        self._ready = False

    def _post(self, message: dict[str, Any]) -> dict[str, Any] | None:
        req = urllib.request.Request(self.url, data=json.dumps(message).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise LedgerUnreachable(f"HTTP {exc.code} from {self.url}") from exc
        except (urllib.error.URLError, OSError, socket.timeout) as exc:
            raise LedgerUnreachable(f"{self.url}: {exc}") from exc
        return json.loads(raw.decode("utf-8")) if raw else None

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._id += 1
            message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
            if params is not None:
                message["params"] = params
            reply = self._post(message)
        if reply is None:
            raise LedgerUnreachable(f"empty reply to {method}")
        if "error" in reply:
            raise LedgerUnreachable(f"{method}: {reply['error']}")
        return reply["result"]

    def _call_sync(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._ready:
            self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                     "clientInfo": {"name": "trailproof-wallet", "version": "0.1"}})
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._ready = True
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        payload = result.get("structuredContent")
        if payload is None:
            texts = [c.get("text") for c in result.get("content", []) if c.get("type") == "text"]
            try:
                payload = json.loads(texts[0]) if texts else {}
            except json.JSONDecodeError:
                payload = {"error": "BAD_RESPONSE", "detail": texts[0]}
        if result.get("isError") and "error" not in payload:
            payload = {"error": "TOOL_ERROR", "detail": json.dumps(payload)[:300]}
        return payload

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        self.calls.append(tool)
        return await asyncio.to_thread(self._call_sync, tool, arguments)

    async def close(self) -> None:
        return None
