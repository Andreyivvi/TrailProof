"""TrailProof as an MCP server (stdio transport).

Run with ``python -m trailproof.server`` or ``mcp dev trailproof/server.py``.
The SQLite path comes from ``TRAILPROOF_DB`` (default ``:memory:``). The
ledger keypair is generated at start-up; its public key is returned by
``register_party`` so clients can verify receipts.

Transport is stdio by default. ``python -m trailproof.server --http [--port N]``
serves the same tools over MCP streamable HTTP (plain JSON responses,
stateless) so several agent processes can share one ledger, e.g. when the
mock parties run as separate participants in an external sandbox.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from .ledger import Ledger
from .service import TrailProofService

INSTRUCTIONS = """TrailProof is a neutral, append-only commitment ledger for agent purchases.
Lock a sha256 fingerprint of what you saw at every step (register_party, record_event,
check_order); keep the plaintext in your own vault. On a dispute (open_dispute) each
party reveals its plaintexts (reveal); the ledger checks them against the commitments
and get_case_bundle returns deterministic findings and a verdict. Merchants can
export_evidence_pack. A buyer can prove its track record with one merchant to that
merchant with attest_history (counts only, ledger-signed, bound to one audience).
Every error comes back as {"error": CODE, "detail": ...}."""


def build_server(service: TrailProofService | None = None) -> MCPServer:
    svc = service or TrailProofService(Ledger(os.environ.get("TRAILPROOF_DB", ":memory:")))
    server = MCPServer("trailproof", instructions=INSTRUCTIONS, version="0.1.0")

    @server.tool(description="Register a party's Ed25519 public key (hex). Roles: user, buyer_agent, merchant, psp.")
    async def register_party(role: str, public_key: str, operator: str) -> dict[str, Any]:
        return svc.register_party(role, public_key, operator)

    @server.tool(
        description=(
            "Append a signed commitment to an order's chain. commit = sha256(canonical_json(payload)); "
            "sig = ed25519(commit|prev|refs) where prev is the order head from get_trail (empty if none) "
            "and refs are comma-joined commits this event depends on. Returns the ledger receipt."
        )
    )
    async def record_event(
        order_id: str,
        type: str,
        party_id: str,
        commit: str,
        sig: str,
        refs: list[str] | None = None,
        prev: str | None = None,
    ) -> dict[str, Any]:
        return svc.record_event(order_id, type, party_id, commit, refs or [], sig, prev=prev)

    @server.tool(
        description=(
            "Buyer agent: verify the MANDATE and OFFER commitments against their plaintexts, run the "
            "mandate scope check and, if in scope, record the ORDER. Sign the ORDER commit "
            "sha256(canonical_json({mandate_commit, offer_commit, qty, total, currency})) with "
            "refs=[mandate_commit, offer_commit] and prev=current head. Plaintexts are discarded."
        )
    )
    async def check_order(
        order_id: str,
        party_id: str,
        mandate_payload: dict[str, Any],
        mandate_commit: str,
        offer_payload: dict[str, Any],
        offer_commit: str,
        qty: int,
        total: str,
        currency: str,
        sig: str,
    ) -> dict[str, Any]:
        return svc.check_order(
            order_id, party_id, mandate_payload, mandate_commit, offer_payload, offer_commit, qty, total, currency, sig
        )

    @server.tool(description="The order's event chain (hashes and signatures only, never plaintext) plus its head.")
    async def get_trail(order_id: str, party_id: str) -> dict[str, Any]:
        return svc.get_trail(order_id, party_id)

    @server.tool(
        description=(
            "Record a DISPUTE and open a case. claim: not_as_described | not_received | not_authorised. "
            "Sign sha256(canonical_json({claim, note}))|prev| (no refs)."
        )
    )
    async def open_dispute(order_id: str, party_id: str, claim: str, note: str, sig: str) -> dict[str, Any]:
        return svc.open_dispute(order_id, party_id, claim, note, sig)

    @server.tool(description="Reveal the plaintext of one of your own events into a case. Verified against the commitment.")
    async def reveal(case_id: str, event_id: str, payload: dict[str, Any], party_id: str) -> dict[str, Any]:
        return svc.reveal(case_id, event_id, payload, party_id)

    @server.tool(description="Run the rules engine over everything revealed so far: findings, verdict, remedy, cites.")
    async def get_case_bundle(case_id: str, party_id: str) -> dict[str, Any]:
        return svc.get_case_bundle(case_id, party_id)

    @server.tool(description="Export the case as a Stripe-like dispute-evidence dict with a re-verifiable trailproof block.")
    async def export_evidence_pack(case_id: str, reason_code: str, party_id: str) -> dict[str, Any]:
        return svc.export_evidence_pack(case_id, reason_code, party_id)

    @server.tool(
        description=(
            "Buyer agent or user key: get a ledger-signed attestation of your verified history with one "
            "merchant (orders completed = CHARGE without REFUND, cases lost, first/last order time), bound to "
            "one audience party. No order ids or amounts are disclosed. The audience verifies ledger_sig "
            "(ed25519 over canonical_json of the other fields) with the ledger public key from register_party."
        )
    )
    async def attest_history(party_id: str, merchant_party_id: str, audience_party_id: str) -> dict[str, Any]:
        return svc.attest_history(party_id, merchant_party_id, audience_party_id)

    return server


server = build_server()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="TrailProof MCP server")
    ap.add_argument("--http", action="store_true", help="serve MCP streamable HTTP instead of stdio")
    ap.add_argument("--host", default=os.environ.get("TRAILPROOF_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("TRAILPROOF_PORT", "8765")))
    args = ap.parse_args(argv)
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port, json_response=True, stateless_http=True)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
