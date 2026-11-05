"""Scripted (deterministic) buyer agent.

The buyer agent holds two keys: the *user key* (simulating the human's passkey;
it signs MANDATEs) and its own agent key (ORDER, DELIVERY, DISPUTE).

Fail-open (brief §4): if the ledger is unreachable the agent carries on with
``no_receipt=True`` instead of blocking the purchase; it never silently skips
``check_order`` when the server *is* reachable.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from typing import Any

from trailproof import crypto

from .base import MockParty
from .transport import ServerUnreachable


class ScriptedBuyer:
    def __init__(self, client: Any, user_name: str = "mia", operator: str = "petbot"):
        self.client = client
        self.user = MockParty(client, "user", f"user:{user_name}")
        self.agent = MockParty(client, "buyer_agent", operator)
        self.user_name = user_name
        self._seq = itertools.count(1)
        self.no_receipt_orders: list[str] = []
        self.aborted: dict[str, list[dict[str, str]]] = {}
        self.orders: dict[str, dict[str, Any]] = {}  # order_id -> {mandate, offer, order_payload}
        self.cases: dict[str, dict[str, Any]] = {}
        self.attestations: dict[str, dict[str, Any]] = {}  # merchant_party_id -> attestation

    @property
    def party_id(self) -> str | None:
        return self.agent.party_id

    async def register(self) -> None:
        await self.user.register()
        await self.agent.register()

    # -------------------------------------------------------------- mandate
    def new_mandate(self, categories: list[str], max_amount: str, currency: str = "EUR", merchants: Any = "*", days: int = 30, nonce: str | None = None, now=None) -> dict[str, Any]:
        now = now or crypto.utc_now()
        return {
            "user_id": self.user_name,
            "categories": list(categories),
            "max_amount": crypto.amount_str(max_amount),
            "currency": currency,
            "merchants": merchants,
            "valid_until": crypto.iso_z(now + timedelta(days=days)),
            "nonce": nonce or f"mandate-{next(self._seq):04d}",
        }

    async def submit_mandate(self, order_id: str, mandate: dict[str, Any]) -> dict[str, Any]:
        """The user key signs the MANDATE; the agent submits it as the order's first event."""
        res = await self.user.record(order_id, "MANDATE", mandate)
        if "error" in res:
            raise RuntimeError(f"MANDATE rejected: {res}")
        self.orders.setdefault(order_id, {})["mandate"] = mandate
        return res

    # ---------------------------------------------------------- recognition
    async def request_attestation(self, merchant_party_id: str, audience_party_id: str | None = None) -> dict[str, Any]:
        """Ask the ledger to attest the agent's history with one merchant, for that merchant's eyes only.

        The result is kept in ``self.attestations`` and handed to the shop
        off-ledger with the next order. Errors come back as ``{"error", ...}``.
        """
        res = await self.client.call(
            "attest_history",
            party_id=self.agent.party_id,
            merchant_party_id=merchant_party_id,
            audience_party_id=audience_party_id or merchant_party_id,
        )
        if "error" not in res:
            self.attestations[merchant_party_id] = res
        return res

    # ---------------------------------------------------------------- order
    async def place_order(self, order_id: str, mandate: dict[str, Any], offer: dict[str, Any]) -> dict[str, Any]:
        """Run ``check_order``; record ORDER only if the ledger says in scope.

        Returns ``{"placed": bool, "in_scope": bool|None, "reasons": [...], "no_receipt": bool}``.
        """
        mc, oc = crypto.commit(mandate), crypto.commit(offer)
        qty, total, currency = offer["qty"], offer["total"], offer["currency"]
        order_payload = {"mandate_commit": mc, "offer_commit": oc, "qty": qty, "total": total, "currency": currency}
        self.orders.setdefault(order_id, {}).update(mandate=mandate, offer=offer, order_payload=order_payload)
        try:
            sig = await self.agent.sign_for(order_id, crypto.commit(order_payload), [mc, oc])
            res = await self.client.call(
                "check_order",
                order_id=order_id,
                party_id=self.agent.party_id,
                mandate_payload=mandate,
                mandate_commit=mc,
                offer_payload=offer,
                offer_commit=oc,
                qty=qty,
                total=total,
                currency=currency,
                sig=sig,
            )
        except ServerUnreachable as e:
            # Fail-open: the purchase goes ahead, flagged as un-receipted.
            self.no_receipt_orders.append(order_id)
            return {"placed": True, "in_scope": None, "reasons": [], "no_receipt": True, "detail": str(e)}
        if "error" in res:
            self.aborted[order_id] = [{"code": res["error"], "detail": res.get("detail", "")}]
            return {"placed": False, "in_scope": None, "reasons": self.aborted[order_id], "no_receipt": False}
        if not res["in_scope"]:
            self.aborted[order_id] = res["reasons"]
            return {"placed": False, "in_scope": False, "reasons": res["reasons"], "no_receipt": False}
        self.agent.vault[res["receipt"]["event_id"]] = order_payload
        self.agent.vault_orders[res["receipt"]["event_id"]] = order_id
        return {"placed": True, "in_scope": True, "reasons": [], "no_receipt": False, "receipt": res["receipt"]}

    # ------------------------------------------------------------- delivery
    async def confirm_delivery(self, order_id: str, received: bool, sku: str, qty: int, unit: str, note: str = "") -> dict[str, Any]:
        payload = {"received": received, "sku": sku, "qty": qty, "unit": unit, "note": note}
        res = await self.agent.record(order_id, "DELIVERY", payload)
        return {"payload": payload, "receipt": res}

    # -------------------------------------------------------------- dispute
    async def open_dispute(self, order_id: str, claim: str, note: str = "") -> dict[str, Any]:
        payload = {"claim": claim, "note": note}
        sig = await self.agent.sign_for(order_id, crypto.commit(payload))
        res = await self.client.call(
            "open_dispute", order_id=order_id, party_id=self.agent.party_id, claim=claim, note=note, sig=sig
        )
        if "error" in res:
            raise RuntimeError(f"open_dispute failed: {res}")
        self.agent.vault[res["receipt"]["event_id"]] = payload
        self.agent.vault_orders[res["receipt"]["event_id"]] = order_id
        self.cases[res["case_id"]] = res
        return res

    async def reveal_all(self, case_id: str, order_id: str) -> dict[str, dict[str, Any]]:
        out = await self.user.reveal_all(case_id, order_id)
        out.update(await self.agent.reveal_all(case_id, order_id))
        return out

    async def bundle(self, case_id: str) -> dict[str, Any]:
        return await self.agent.bundle(case_id)

    async def export_evidence(self, case_id: str, reason_code: str) -> dict[str, Any]:
        return await self.agent.export_evidence(case_id, reason_code)
