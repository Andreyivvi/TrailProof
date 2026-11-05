"""Shared helpers for tests: a signing party and canonical demo payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from trailproof import crypto
from trailproof.service import TrailProofService

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


class Party:
    """A test party that signs events against a ``TrailProofService`` directly."""

    def __init__(self, svc: TrailProofService, role: str, operator: str = "test"):
        self.svc = svc
        self.role = role
        self.kp = crypto.Keypair.generate()
        res = svc.register_party(role, self.kp.public_hex, operator)
        assert "party_id" in res, res
        self.party_id = res["party_id"]
        self.vault: dict[str, dict[str, Any]] = {}

    def head(self, order_id: str) -> str | None:
        return self.svc.get_trail(order_id, self.party_id)["head"]

    def sign_for(self, order_id: str, commit: str, refs: list[str] | None = None, prev: str | None = "auto") -> str:
        p = self.head(order_id) if prev == "auto" else prev
        return self.kp.sign(crypto.event_signing_message(commit, p, refs or []))

    def record(self, order_id: str, type_: str, payload: dict[str, Any], refs: list[str] | None = None, **kw):
        c = crypto.commit(payload)
        res = self.svc.record_event(order_id, type_, self.party_id, c, refs or [], self.sign_for(order_id, c, refs), **kw)
        if "event_id" in res:
            self.vault[res["event_id"]] = payload
        return res

    def check_order(self, order_id: str, mandate: dict, offer: dict, qty, total: str, currency: str):
        mc, oc = crypto.commit(mandate), crypto.commit(offer)
        order_payload = {"mandate_commit": mc, "offer_commit": oc, "qty": qty, "total": total, "currency": currency}
        c = crypto.commit(order_payload)
        res = self.svc.check_order(
            order_id, self.party_id, mandate, mc, offer, oc, qty, total, currency, self.sign_for(order_id, c, [mc, oc])
        )
        if res.get("receipt"):
            self.vault[res["receipt"]["event_id"]] = order_payload
        return res

    def open_dispute(self, order_id: str, claim: str, note: str = ""):
        payload = {"claim": claim, "note": note}
        res = self.svc.open_dispute(order_id, self.party_id, claim, note, self.sign_for(order_id, crypto.commit(payload)))
        if "receipt" in res:
            self.vault[res["receipt"]["event_id"]] = payload
        return res

    def reveal_all(self, case_id: str, tamper: dict[str, dict] | None = None):
        out = {}
        for event_id, payload in self.vault.items():
            p = (tamper or {}).get(event_id, payload)
            r = self.svc.reveal(case_id, event_id, p, self.party_id)
            if r.get("error") in ("WRONG_ORDER", "ALREADY_REVEALED"):
                continue  # events of other orders / the auto-revealed DISPUTE
            out[event_id] = r
        return out


def mandate_payload(now: datetime = NOW, **over) -> dict[str, Any]:
    m = {
        "user_id": "mia",
        "categories": ["pet_food"],
        "max_amount": "40.00",
        "currency": "EUR",
        "merchants": "*",
        "valid_until": crypto.iso_z(now + timedelta(days=30)),
        "nonce": "nonce-1",
    }
    m.update(over)
    return m


def offer_payload(now: datetime = NOW, **over) -> dict[str, Any]:
    o = {
        "sku": "DOGFOOD-10KG",
        "title": "Premium dog food 10 kg",
        "category": "pet_food",
        "qty": 1,
        "unit": "bag_10kg",
        "unit_price": "36.90",
        "total": "36.90",
        "currency": "EUR",
        "shipping_terms": "2-3 business days",
        "return_terms": "14 days, unopened",
        "valid_until": crypto.iso_z(now + timedelta(hours=2)),
        "nonce": "offer-1",
    }
    o.update(over)
    return o
