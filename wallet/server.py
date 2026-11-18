"""The wallet as an MCP server: the tools a person's own AI agent gets.

    python -m wallet serve        # stdio; this is what the agent's MCP config runs

There is deliberately no tool that creates or widens a mandate. The person
does that on the command line; the agent can only see and use what exists.
"""

from __future__ import annotations

import functools
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import LedgerUnreachable
from .core import Wallet, WalletError

INSTRUCTIONS = """This is the user's TrailProof wallet. It lets you shop on their behalf and prove it.

- You can only buy inside a mandate the user signed. list_mandates shows them. You cannot create or
  change a mandate; if none fits, tell the user to run: python -m wallet mandate add --category <c> --max <amount>
- To be recognised by a shop, call prove_history with the shop's party id as audience and hand the
  result to the shop. It discloses counts only, never what was bought, and works at that one shop.
- To buy: start_order (commits the mandate), give the shop the order_id and mandate_commit so it can
  commit its offer, then place_order with the offer the shop returned. The ledger refuses anything
  outside the mandate. After delivery call confirm_delivery with what actually arrived.
- If what arrived is not what was offered, call open_dispute, then ask the shop to respond, then
  case_status. Report amounts and verdicts to the user exactly as returned."""


def build_server(wallet_path: str | None = None, client: Any = None) -> MCPServer:
    server = MCPServer("trailproof-wallet", instructions=INSTRUCTIONS, version="0.1.0")
    shared: dict[str, Any] = {"client": client}

    def wallet() -> Wallet:
        # Reload on every call: the user may have added or revoked a mandate from the command line.
        w = Wallet.load(wallet_path, shared["client"])
        shared["client"] = w._client()
        return w

    async def refresh(w: Wallet) -> None:
        """Budgets shown to the agent reflect refunds the ledger has recorded. Offline: show what is known."""
        try:
            if w.agent_id:
                await w.sync_refunds()
        except (WalletError, LedgerUnreachable):
            pass

    def tool(fn):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return await fn(*args, **kwargs)
            except WalletError as e:
                return e.to_dict()
            except LedgerUnreachable as e:
                return {"error": "LEDGER_UNREACHABLE", "detail": str(e)}
            except (KeyError, TypeError, ValueError) as e:
                return {"error": "BAD_REQUEST", "detail": f"{type(e).__name__}: {e}"}

        return wrapper

    @server.tool(description="Who this wallet belongs to, its party ids, its mandates and how many orders it has placed.")
    @tool
    async def wallet_status() -> dict[str, Any]:
        w = wallet()
        await refresh(w)
        return w.status()

    @server.tool(description="The user's mandates: what you may buy, the budget left, and until when. Read-only.")
    @tool
    async def list_mandates(active_only: bool = True) -> dict[str, Any]:
        w = wallet()
        await refresh(w)
        return {"mandates": w.mandates(active_only=active_only)}

    @server.tool(
        description=(
            "Get a ledger-signed proof of this agent's purchase history, addressed to one shop (audience_party_id). "
            "Leave merchant_party_id as '*' for the record across all shops (names none; use at a shop you have not "
            "bought from). Pass the shop's own party id for the history with that shop (earns returning-customer "
            "prices). A shop-specific proof is the stronger one when the user has bought there before; the '*' proof "
            "is for a shop that is new to them. Hand the returned object to the shop unchanged."
        )
    )
    @tool
    async def prove_history(audience_party_id: str, merchant_party_id: str = "*") -> dict[str, Any]:
        return await wallet().prove_history(audience_party_id, merchant_party_id)

    @server.tool(
        description=(
            "Open a new order under a mandate. Commits the user's mandate to the ledger and returns order_id and "
            "mandate_commit; give both, plus buyer_agent_party_id, to the shop so it can commit its offer."
        )
    )
    @tool
    async def start_order(mandate_id: str) -> dict[str, Any]:
        return await wallet().start_order(mandate_id)

    @server.tool(
        description=(
            "Place the order for the offer a shop returned. The ledger checks the offer against the mandate "
            "(category, budget, expiry, merchant) and against the hash the shop committed; it records the order only "
            "if everything holds, and returns the reasons if not."
        )
    )
    @tool
    async def place_order(order_id: str, offer: dict[str, Any], offer_commit: str | None = None) -> dict[str, Any]:
        return await wallet().place_order(order_id, offer, offer_commit)

    @server.tool(
        description=(
            "Commit what actually arrived for an order: the sku, quantity and unit received, not what was ordered. "
            "Call it only once the goods have arrived and been checked: by the user, by a carrier's delivery scan, "
            "or at a shop that simulates delivery and says so. A shipping notice or tracking number is not a delivery."
        )
    )
    @tool
    async def confirm_delivery(order_id: str, received: bool, sku: str, qty: int, unit: str, note: str = "") -> dict[str, Any]:
        return await wallet().confirm_delivery(order_id, received, sku, qty, unit, note)

    @server.tool(
        description=(
            "Open a dispute on an order and reveal this wallet's records for it. "
            "claim: not_as_described | not_received | not_authorised."
        )
    )
    @tool
    async def open_dispute(order_id: str, claim: str, note: str = "") -> dict[str, Any]:
        return await wallet().open_dispute(order_id, claim, note)

    @server.tool(
        description=(
            "The ledger's findings, verdict and remedy for a dispute case. Once the shop has refunded, "
            "refunded_to_budget is true and remaining_under_mandate shows the budget given back, so a new order is possible."
        )
    )
    @tool
    async def case_status(case_id: str) -> dict[str, Any]:
        return await wallet().case_status(case_id)

    @server.tool(description="The ledger's record of one order: each committed step with its hash and time.")
    @tool
    async def order_trail(order_id: str) -> dict[str, Any]:
        return await wallet().order_trail(order_id)

    return server
