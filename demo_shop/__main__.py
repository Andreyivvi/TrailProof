"""Pawshop, a practice merchant, as an MCP server (stdio).

    python -m demo_shop

It plays the shop's side against the same ledger as the wallet: it commits
offers, recognises customers from the proof they present, takes payment
through its (mock) payment provider, ships, and answers disputes by its
refund policy. In real life this would be the merchant's own agent; here it
exists so a wallet has someone to buy from.

    TRAILPROOF_URL          the ledger (default http://127.0.0.1:8765/mcp)
    TRAILPROOF_SHOP_STATE   where the shop keeps its keys (default ~/.trailproof/demo_shop.json)
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from mocks.psp import MockPSP
from mocks.shop import CATALOG, MockShop
from trailproof import crypto
from wallet.client import HttpToolClient, LedgerUnreachable

INSTRUCTIONS = """Pawshop is a pet shop's agent. To sell to a buyer agent:
1. shop_info gives this shop's party id and catalogue.
2. get_offer needs the buyer's order_id (from its wallet's start_order), a sku, the buyer agent's party id, and
   optionally the proof the buyer got from prove_history. A valid proof earns a loyalty or welcome price. The offer
   is committed to the ledger; return it to the buyer unchanged for place_order.
3. After the buyer has ordered, pay_and_ship charges and ships; it returns the parcel the buyer receives.
4. If the buyer opens a dispute, respond_to_dispute reveals the shop's records and applies its refund policy."""


def state_path() -> Path:
    return Path(os.environ.get("TRAILPROOF_SHOP_STATE", Path.home() / ".trailproof" / "demo_shop.json"))


class Shop:
    """The mock shop and its payment provider, with keys that survive restarts."""

    def __init__(self, client: Any = None, path: Path | None = None):
        self.client = client or HttpToolClient(os.environ.get("TRAILPROOF_URL", "http://127.0.0.1:8765/mcp"))
        self.path = path or state_path()
        self.shop = MockShop(self.client, operator="pawshop-demo")
        self.psp = MockPSP(self.client, operator="pawshop-demo-psp")
        self.ready = False

    async def ensure(self) -> None:
        if self.ready:
            return
        saved = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        for name, party in (("shop", self.shop), ("psp", self.psp)):
            s = saved.get(name)
            if s:
                party.kp = crypto.Keypair.from_private_hex(s["private_key"])
                party.party_id = s["party_id"]
                party.ledger_public_key = saved.get("ledger_public_key")
            else:
                await party.register()
                saved[name] = {"private_key": party.kp.private_hex, "party_id": party.party_id}
                saved["ledger_public_key"] = party.ledger_public_key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.ready = True


def build_server(shop: Shop | None = None) -> MCPServer:
    server = MCPServer("pawshop-demo", instructions=INSTRUCTIONS, version="0.1.0")
    s = shop or Shop()

    def tool(fn):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                await s.ensure()
                return await fn(*args, **kwargs)
            except LedgerUnreachable as e:
                return {"error": "LEDGER_UNREACHABLE", "detail": str(e)}
            except (KeyError, RuntimeError, TypeError, ValueError) as e:
                return {"error": "BAD_REQUEST", "detail": f"{type(e).__name__}: {e}"}

        return wrapper

    @server.tool(description="This shop's party id, its catalogue with public prices, and its loyalty and refund policies.")
    @tool
    async def shop_info() -> dict[str, Any]:
        return {"shop_party_id": s.shop.party_id, "name": "Pawshop (demo)",
                "products": [{"sku": k, "title": v["title"], "category": v["category"], "unit": v["unit"],
                              "price": v["unit_price"], "currency": v["currency"]} for k, v in CATALOG.items()],
                "loyalty": "10% off for returning customers (2+ completed orders here), 5% welcome price for buyers "
                           "with 2+ completed orders elsewhere; both need a valid proof and no lost disputes",
                "refund_policy": s.shop.policy}

    @server.tool(
        description=(
            "Commit an offer for one sku behind the buyer's order. order_id and buyer_agent_party_id come from the "
            "buyer's wallet (start_order). proof is the object from the buyer's prove_history, if it has one. "
            "Returns the offer plaintext and offer_commit for the buyer's place_order, and whether it was recognised."
        )
    )
    @tool
    async def get_offer(order_id: str, sku: str, buyer_agent_party_id: str, proof: dict[str, Any] | None = None) -> dict[str, Any]:
        if sku not in CATALOG:
            return {"error": "UNKNOWN_SKU", "detail": f"{sku}; see shop_info"}
        res = await s.shop.recognise_and_offer(order_id, sku, proof, buyer_party_id=buyer_agent_party_id)
        return {"offer": res["payload"], "offer_commit": res["commit"], "shop_party_id": s.shop.party_id,
                "recognised": res["recognised"], "why": res["reason"], "public_price": CATALOG[sku]["unit_price"]}

    @server.tool(
        description=(
            "After the buyer has placed the order: charge the committed total and ship. Returns the parcel the buyer "
            "receives. ship_wrong_item=true ships another product, to try out a dispute."
        )
    )
    @tool
    async def pay_and_ship(order_id: str, ship_wrong_item: bool = False) -> dict[str, Any]:
        if order_id not in s.shop.offers:
            return {"error": "UNKNOWN_ORDER", "detail": "no offer was made for this order in this shop session"}
        trail = await s.shop.trail(order_id)
        if not any(e["type"] == "ORDER" for e in trail.get("events", [])):
            return {"error": "NOT_ORDERED", "detail": "the buyer has not placed this order on the ledger"}
        charge = await s.shop.take_payment(order_id, s.psp)
        wrong = {"sku": "DOGFOOD-5KG", "unit": "bag_5kg"} if ship_wrong_item else {}
        ful = (await s.shop.fulfil(order_id, **wrong))["payload"]
        return {"charged": charge["payload"]["amount"], "currency": charge["payload"]["currency"],
                "parcel": {"sku": ful["sku"], "qty": ful["qty"], "unit": ful["unit"], "tracking": ful["tracking"]}}

    @server.tool(description="Answer a dispute: reveal the shop's records into the case, read the ledger's verdict, refund if the policy says so.")
    @tool
    async def respond_to_dispute(case_id: str, order_id: str) -> dict[str, Any]:
        d = await s.shop.handle_dispute({"case_id": case_id, "order_id": order_id}, s.psp)
        return {k: d.get(k) for k in ("action", "amount", "currency", "reason", "verdict", "findings_against_me")}

    return server


if __name__ == "__main__":
    build_server().run("stdio")
