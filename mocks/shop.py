"""Mock pet shop + merchant agent with an auto-refund policy (brief §7)."""

from __future__ import annotations

import itertools
from datetime import timedelta
from decimal import Decimal
from typing import Any

from trailproof import crypto
from trailproof.rules import MERCHANT_FAULT_CODES

from .base import MockParty
from .psp import MockPSP

CATALOG: dict[str, dict[str, Any]] = {
    "DOGFOOD-10KG": {
        "title": "Premium dog food 10 kg",
        "category": "pet_food",
        "unit": "bag_10kg",
        "unit_price": "36.90",
        "currency": "EUR",
    },
    "DOGFOOD-5KG": {
        "title": "Premium dog food 5 kg",
        "category": "pet_food",
        "unit": "bag_5kg",
        "unit_price": "21.50",
        "currency": "EUR",
    },
    "CATTOY-SET": {
        "title": "Deluxe cat toy set",
        "category": "cat_toys",
        "unit": "set",
        "unit_price": "55.00",
        "currency": "EUR",
    },
    "DOGFOOD-30": {
        "title": "Dog food, 30-euro pack",
        "category": "pet_food",
        "unit": "pack",
        "unit_price": "30.00",
        "currency": "EUR",
    },
}

POLICY = {"auto_refund_if": ["CHARGE_NE_OFFER", "DELIVERED_NE_OFFER", "NOT_RECEIVED"], "max_amount": 50.00}

#: Brand-direct recognition: a returning customer who can prove, with a
#: ledger-signed attestation addressed to this shop, at least ``min_orders``
#: completed orders here and no more than ``max_lost_cases`` lost cases gets
#: ``discount_pct`` off the public price. Anyone else gets the public price.
RECOGNITION_POLICY = {"min_orders": 2, "max_lost_cases": 0, "discount_pct": 10}


class MockShop(MockParty):
    def __init__(
        self,
        client: Any,
        operator: str = "pawshop",
        policy: dict[str, Any] | None = None,
        recognition_policy: dict[str, Any] | None = None,
    ):
        super().__init__(client, "merchant", operator)
        self.policy = policy or POLICY
        self.recognition_policy = recognition_policy or RECOGNITION_POLICY
        self.recognitions: dict[str, dict[str, Any]] = {}  # order_id -> why recognised / not
        self._seq = itertools.count(1)
        self.offers: dict[str, dict[str, Any]] = {}  # order_id -> OFFER payload
        self.offer_events: dict[str, str] = {}  # order_id -> event_id
        self.decisions: dict[str, dict[str, Any]] = {}  # case_id -> decision

    # ------------------------------------------------------------- selling
    def quote(self, sku: str, qty: int = 1, now=None) -> dict[str, Any]:
        item = CATALOG[sku]
        now = now or crypto.utc_now()
        unit_price = Decimal(item["unit_price"])
        return {
            "sku": sku,
            "title": item["title"],
            "category": item["category"],
            "qty": qty,
            "unit": item["unit"],
            "unit_price": crypto.amount_str(unit_price),
            "total": crypto.amount_str(unit_price * qty),
            "currency": item["currency"],
            "shipping_terms": "2-3 business days, tracked",
            "return_terms": "14 days, unopened",
            "valid_until": crypto.iso_z(now + timedelta(hours=2)),
            "nonce": f"offer-{next(self._seq):04d}",
        }

    async def make_offer(self, order_id: str, sku: str, qty: int = 1) -> dict[str, Any]:
        """Commit an OFFER to the ledger and hand the plaintext to the buyer off-ledger."""
        payload = self.quote(sku, qty)
        res = await self.record(order_id, "OFFER", payload)
        if "error" in res:
            raise RuntimeError(f"OFFER rejected: {res}")
        self.offers[order_id] = payload
        self.offer_events[order_id] = res["event_id"]
        return {"payload": payload, "commit": crypto.commit(payload), "receipt": res}

    # -------------------------------------------------------- recognition
    def check_recognition(self, attestation: Any, buyer_party_id: str | None = None) -> tuple[bool, str]:
        """Decide whether an attestation earns the loyalty discount.

        Checks, in order: it is signed by *our* ledger (any edit breaks the
        signature), it is addressed to this shop, it is about history with
        this shop, its subject is the buyer we are dealing with, and the
        counts clear ``recognition_policy``. Returns ``(recognised, reason)``.
        """
        pol = self.recognition_policy
        if attestation is None:
            return False, "no attestation presented"
        if not self.ledger_public_key or not crypto.verify_attestation(attestation, self.ledger_public_key):
            return False, "attestation signature does not verify with the ledger key"
        if attestation.get("audience") != self.party_id:
            return False, "attestation is addressed to another audience"
        if attestation.get("merchant") != self.party_id:
            return False, "attestation is about history with another merchant"
        if buyer_party_id is not None and attestation.get("subject") != buyer_party_id:
            return False, "attestation subject is not this buyer"
        stats = attestation.get("stats") or {}
        orders, lost = int(stats.get("orders_completed", 0)), int(stats.get("cases_lost", 0))
        if orders < pol["min_orders"]:
            return False, f"{orders} completed order(s), policy needs {pol['min_orders']}"
        if lost > pol["max_lost_cases"]:
            return False, f"{lost} lost case(s), policy allows {pol['max_lost_cases']}"
        return True, f"{orders} completed order(s), {lost} lost case(s): {pol['discount_pct']}% off"

    async def recognise_and_offer(
        self, order_id: str, sku: str, attestation: Any, qty: int = 1, buyer_party_id: str | None = None
    ) -> dict[str, Any]:
        """Like :meth:`make_offer`, but with the recognition discount when the attestation passes.

        The OFFER payload carries ``recognition``: ``{"discount_pct", "attestation_commit"}``
        (``attestation_commit = sha256(canonical_json(attestation))``) or ``null``.
        Because the discounted total is *committed* like any other offer, a
        later charge at the public price is a plain ``CHARGE_NE_OFFER``.
        """
        recognised, reason = self.check_recognition(attestation, buyer_party_id)
        payload = self.quote(sku, qty)
        if recognised:
            pct = Decimal(str(self.recognition_policy["discount_pct"]))
            unit_price = (Decimal(payload["unit_price"]) * (Decimal(100) - pct) / Decimal(100)).quantize(Decimal("0.01"))
            payload["unit_price"] = crypto.amount_str(unit_price)
            payload["total"] = crypto.amount_str(unit_price * qty)
            payload["recognition"] = {
                "discount_pct": self.recognition_policy["discount_pct"],
                "attestation_commit": crypto.commit(attestation),
            }
        else:
            payload["recognition"] = None
        self.recognitions[order_id] = {"recognised": recognised, "reason": reason}
        res = await self.record(order_id, "OFFER", payload)
        if "error" in res:
            raise RuntimeError(f"OFFER rejected: {res}")
        self.offers[order_id] = payload
        self.offer_events[order_id] = res["event_id"]
        return {
            "payload": payload,
            "commit": crypto.commit(payload),
            "receipt": res,
            "recognised": recognised,
            "reason": reason,
        }

    async def take_payment(self, order_id: str, psp: MockPSP, amount: str | None = None) -> dict[str, Any]:
        """Ask the PSP to charge. ``amount`` overrides the offer total (the merchant-fault scenario)."""
        offer = self.offers[order_id]
        return await psp.charge(order_id, amount or offer["total"], offer["currency"])

    async def fulfil(self, order_id: str, sku: str | None = None, qty: int | None = None, unit: str | None = None) -> dict[str, Any]:
        offer = self.offers[order_id]
        payload = {
            "sku": sku or offer["sku"],
            "qty": qty if qty is not None else offer["qty"],
            "unit": unit or offer["unit"],
            "carrier": "DHL",
            "tracking": f"JD{next(self._seq):010d}",
        }
        res = await self.record(order_id, "FULFILMENT", payload)
        return {"payload": payload, "receipt": res}

    # ------------------------------------------------------------ disputes
    def findings_against_me(self, bundle: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for f in bundle.get("findings", []):
            if f["code"] in MERCHANT_FAULT_CODES:
                out.append(f)
            elif f["code"] in ("TAMPERED", "BAD_SIG") and f.get("party_id") == self.party_id:
                out.append(f)
        return out

    async def handle_dispute(self, notice: dict[str, Any], psp: MockPSP, tamper_offer: dict[str, Any] | None = None) -> dict[str, Any]:
        """What the merchant agent does when told about a case.

        1. Reveal its own OFFER / FULFILMENT plaintexts (optionally a *tampered*
           offer, for the tampering scenario).
        2. Fetch the bundle.
        3. If every finding against it is in ``policy.auto_refund_if`` and the
           remedy amount is within ``policy.max_amount``, refund via the PSP
           (which records REFUND and closes the case). Otherwise reject.
        """
        case_id, order_id = notice["case_id"], notice["order_id"]
        tamper = {}
        if tamper_offer is not None:
            tamper[self.offer_events[order_id]] = tamper_offer
        reveals = await self.reveal_all(case_id, order_id, tamper=tamper)
        bundle = await self.bundle(case_id)
        against = self.findings_against_me(bundle)
        codes = [f["code"] for f in against]
        remedy = bundle.get("remedy") or {}
        amount = remedy.get("amount")
        decision: dict[str, Any] = {
            "case_id": case_id,
            "order_id": order_id,
            "verdict": bundle.get("verdict"),
            "findings_against_me": codes,
            "reveals": {eid: r.get("verified") for eid, r in reveals.items()},
        }
        policy_ok = (
            bool(against)
            and all(c in self.policy["auto_refund_if"] for c in codes)
            and amount is not None
            and Decimal(amount) <= Decimal(str(self.policy["max_amount"]))
        )
        if policy_ok:
            refund = await psp.refund(order_id, amount, remedy["currency"], f"auto-refund per policy, case {case_id}")
            decision.update(action="refunded", amount=amount, currency=remedy["currency"], refund=refund)
        else:
            why = "no finding against merchant" if not against else "finding(s) outside auto-refund policy: " + ", ".join(codes)
            if against and amount is not None and Decimal(amount) > Decimal(str(self.policy["max_amount"])):
                why = f"amount {amount} exceeds policy max {self.policy['max_amount']}"
            decision.update(action="rejected", reason=why)
        self.decisions[case_id] = decision
        return decision
