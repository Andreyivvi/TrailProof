"""The TrailProof tool implementations, independent of transport.

``TrailProofService`` has one method per MCP tool (brief §4, plus
``attest_history`` for brand-direct customer recognition). Every method
returns a JSON-serialisable dict and never raises: rejected operations come
back as ``{"error": CODE, "detail": "..."}``. ``server.py`` wraps these in an
MCP server; tests and in-process clients can call them directly.
"""

from __future__ import annotations

import functools
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from . import crypto, rules
from .ledger import CASE_OPEN, Ledger, LedgerError

DisputeHook = Callable[[dict[str, Any]], None]


def _tool(fn):
    """Turn ``LedgerError`` (and stray exceptions) into error dicts."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except LedgerError as e:
            return e.to_dict()
        except (KeyError, TypeError, ValueError) as e:  # malformed input
            return {"error": "BAD_REQUEST", "detail": f"{type(e).__name__}: {e}"}

    return wrapper


class TrailProofService:
    def __init__(self, ledger: Ledger | None = None, clock: Callable[[], datetime] | None = None):
        self.ledger = ledger or Ledger(":memory:", clock=clock)
        self.dispute_hooks: list[DisputeHook] = []

    # ------------------------------------------------------------ helpers
    def _now(self) -> datetime:
        return self.ledger.clock()

    def _require_party_to_order(self, order_id: str, party_id: str) -> None:
        party = self.ledger.party(party_id)
        if party.role == "ledger":
            return
        if party_id not in self.ledger.order_parties(order_id):
            raise LedgerError("NOT_A_PARTY", f"{party_id} has written no event for {order_id}")

    def _merchant_of(self, order_id: str) -> str | None:
        for ev in self.ledger.get_trail(order_id):
            if ev.type == "OFFER":
                return ev.party_id
        return None

    # ---------------------------------------------------------------- tools
    @_tool
    def register_party(self, role: str, public_key: str, operator: str) -> dict[str, Any]:
        party_id = self.ledger.register_party(role, public_key, operator)
        return {"party_id": party_id, "ledger_public_key": self.ledger.keypair.public_hex}

    @_tool
    def record_event(
        self,
        order_id: str,
        type: str,
        party_id: str,
        commit: str,
        refs: list[str] | None,
        sig: str,
        prev: str | None = None,
    ) -> dict[str, Any]:
        if type == "ORDER":
            # No mandate, no purchase: an ORDER must point at a MANDATE and an OFFER committed in
            # this very order, whichever path it arrives by (check_order sets these refs itself).
            kinds = {e.type for r in (refs or []) for e in self.ledger.find_by_commit(r, order_id)}
            if not {"MANDATE", "OFFER"} <= kinds:
                raise LedgerError(
                    "ORDER_NEEDS_MANDATE",
                    "an ORDER must reference a MANDATE and an OFFER committed in this order; use check_order",
                )
        ev = self.ledger.record_event(order_id, type, party_id, commit, refs, sig, prev=prev)
        if type == "REFUND":
            for case in self.ledger.cases_for_order(order_id):
                if case["status"] == CASE_OPEN:
                    self.ledger.close_case(case["case_id"], f"refunded:{ev.event_id}")
        return ev.receipt()

    @_tool
    def check_order(
        self,
        order_id: str,
        party_id: str,
        mandate_payload: dict[str, Any],
        mandate_commit: str,
        offer_payload: dict[str, Any],
        offer_commit: str,
        qty: Any,
        total: str,
        currency: str,
        sig: str,
    ) -> dict[str, Any]:
        """Verify both commits, run the scope check and (if in scope) record ORDER.

        The buyer agent signs the ORDER commit it expects the server to compute:
        ``commit({mandate_commit, offer_commit, qty, total, currency})`` with
        ``refs=[mandate_commit, offer_commit]`` and ``prev`` = current head.
        Plaintexts are used for the check and then discarded.
        """
        buyer = self.ledger.party(party_id)
        if buyer.role != "buyer_agent":
            raise LedgerError("WRONG_ROLE", "check_order is for buyer agents")

        # Both commitments must exist in this order and match the plaintext given.
        mandate_evs = [e for e in self.ledger.find_by_commit(mandate_commit, order_id) if e.type == "MANDATE"]
        offer_evs = [e for e in self.ledger.find_by_commit(offer_commit, order_id) if e.type == "OFFER"]
        if not mandate_evs:
            raise LedgerError("UNKNOWN_REF", "mandate_commit is not a MANDATE committed in this order")
        if not offer_evs:
            raise LedgerError("UNKNOWN_REF", "offer_commit is not an OFFER committed in this order")
        if crypto.commit(mandate_payload) != mandate_commit:
            raise LedgerError("COMMIT_MISMATCH", "sha256(mandate_payload) != mandate_commit")
        if crypto.commit(offer_payload) != offer_commit:
            raise LedgerError("COMMIT_MISMATCH", "sha256(offer_payload) != offer_commit")
        mandate_ev, offer_ev = mandate_evs[-1], offer_evs[-1]

        total_s = crypto.amount_str(crypto.parse_amount(total))
        spend_key = (mandate_ev.party_id, str(mandate_payload.get("nonce")))
        already = self.ledger.spent_under(*spend_key)
        in_scope, reasons = rules.scope_check(
            mandate_payload, offer_payload, offer_ev.party_id, qty, total_s, currency, already, self._now()
        )
        if not in_scope:
            return {"in_scope": False, "reasons": reasons, "receipt": None, "order_commit": None}

        order_payload = {
            "mandate_commit": mandate_commit,
            "offer_commit": offer_commit,
            "qty": qty,
            "total": total_s,
            "currency": currency,
        }
        order_commit = crypto.commit(order_payload)
        ev = self.ledger.record_event(
            order_id,
            "ORDER",
            party_id,
            order_commit,
            [mandate_commit, offer_commit],
            sig,
            public={
                "mandate_commit": mandate_commit,
                "offer_commit": offer_commit,
                "scope_checked": True,
                "already_spent": crypto.amount_str(already),
            },
        )
        self.ledger.add_spend(*spend_key, crypto.parse_amount(total_s))
        return {
            "in_scope": True,
            "reasons": [],
            "receipt": ev.receipt(),
            "order_commit": order_commit,
            "order_payload": order_payload,
        }

    @_tool
    def get_trail(self, order_id: str, party_id: str) -> dict[str, Any]:
        """The order's events, hashes only.

        Any registered, non-revoked party may read a trail: writers need the
        current head to sign ``prev`` before they are themselves parties to the
        order (e.g. the merchant writing the first OFFER).
        """
        party = self.ledger.party(party_id)
        if party.revoked_at:
            raise LedgerError("REVOKED_KEY", f"party {party_id} is revoked")
        events = [e.to_dict() for e in self.ledger.get_trail(order_id)]
        return {
            "order_id": order_id,
            "head": events[-1]["commit"] if events else None,
            "events": events,
            "cases": [
                {"case_id": c["case_id"], "claim": c["claim"], "status": c["status"]}
                for c in self.ledger.cases_for_order(order_id)
            ],
        }

    @_tool
    def open_dispute(self, order_id: str, party_id: str, claim: str, note: str, sig: str) -> dict[str, Any]:
        """Record a DISPUTE event and open a case.

        The DISPUTE payload is ``{"claim", "note"}``; the opener signs
        ``commit(payload)|prev|`` (no refs). Because the server is given the
        plaintext, the dispute is auto-revealed into the case.
        """
        if claim not in rules.CLAIMS:
            raise LedgerError("BAD_CLAIM", f"claim must be one of {rules.CLAIMS}")
        self._require_party_to_order(order_id, party_id)
        for c in self.ledger.cases_for_order(order_id):
            if c["status"] == CASE_OPEN:
                raise LedgerError("CASE_ALREADY_OPEN", f"case {c['case_id']} is still open")
        payload = {"claim": claim, "note": note or ""}
        ev = self.ledger.record_event(order_id, "DISPUTE", party_id, crypto.commit(payload), [], sig)
        case_id = self.ledger.open_case(order_id, claim, note or "", party_id, ev.event_id)
        self.ledger.record_reveal(case_id, ev.event_id, party_id, payload, True)
        merchant = self._merchant_of(order_id)
        notice = {
            "case_id": case_id,
            "order_id": order_id,
            "claim": claim,
            "opened_by": party_id,
            "notify": [p for p in self.ledger.order_parties(order_id) if p != party_id],
            "merchant": merchant,
        }
        for hook in list(self.dispute_hooks):
            hook(notice)
        return {"case_id": case_id, "receipt": ev.receipt(), "dispute_payload": payload, **notice}

    @_tool
    def reveal(self, case_id: str, event_id: str, payload: dict[str, Any], party_id: str) -> dict[str, Any]:
        """Reveal the plaintext of one event into a case. Only its writer may do so.

        The result is recorded either way: a failed reveal is evidence of
        tampering and cannot be overwritten with a second attempt.
        """
        case = self.ledger.get_case(case_id)
        ev = self.ledger.get_event(event_id)
        if ev.order_id != case["order_id"]:
            raise LedgerError("WRONG_ORDER", f"{event_id} is not part of order {case['order_id']}")
        if ev.party_id != party_id:
            raise LedgerError("NOT_WRITER", f"{event_id} was written by {ev.party_id}, not {party_id}")
        if event_id in self.ledger.reveals_for_case(case_id):
            raise LedgerError("ALREADY_REVEALED", f"{event_id} already has a reveal on record for this case")
        verified = crypto.commit(payload) == ev.commit
        self.ledger.record_reveal(case_id, event_id, party_id, payload, verified)
        return {
            "verified": verified,
            "event_id": event_id,
            "commit": ev.commit,
            "computed": crypto.commit(payload),
        }

    # ------------------------------------------------- brand recognition
    @_tool
    def attest_history(self, party_id: str, merchant_party_id: str, audience_party_id: str) -> dict[str, Any]:
        """A ledger-signed summary of ``party_id``'s verified history with one merchant.

        Computed from the chain only, never from plaintext: an order counts as
        completed when it carries a CHARGE and no REFUND and its OFFER was
        written by ``merchant_party_id``; a case counts as lost when the rules
        engine's verdict on it is ``buyer_at_fault``. No order ids, amounts or
        skus are disclosed. The attestation is bound to one ``audience`` so it
        cannot be shown to anyone else, and signed with the ledger key over
        ``canonical_json(attestation without ledger_sig)``.
        """
        subject = self.ledger.party(party_id)
        if subject.role not in ("buyer_agent", "user"):
            raise LedgerError("WRONG_ROLE", "attest_history is for buyer agents and user keys")
        if subject.revoked_at:
            raise LedgerError("REVOKED_KEY", f"party {party_id} is revoked")
        merchant = self.ledger.party(merchant_party_id)  # UNKNOWN_PARTY if unknown
        if merchant.role != "merchant":
            raise LedgerError("WRONG_ROLE", f"{merchant_party_id} is a {merchant.role}, not a merchant")
        self.ledger.party(audience_party_id)  # UNKNOWN_PARTY if unknown

        completed = 0
        lost = 0
        first_ts: str | None = None
        last_ts: str | None = None
        for order_id in self.ledger.orders_of_party(party_id):
            events = self.ledger.get_trail(order_id)
            offers = [e for e in events if e.type == "OFFER"]
            if not offers or offers[-1].party_id != merchant_party_id:
                continue
            for case in self.ledger.cases_for_order(order_id):
                if rules.evaluate(self._case_input(case["case_id"]))["verdict"] == "buyer_at_fault":
                    lost += 1
            charge = next((e for e in events if e.type == "CHARGE"), None)
            if charge is None or any(e.type == "REFUND" for e in events):
                continue
            completed += 1
            order_ev = next((e for e in events if e.type == "ORDER"), None)
            ts = (order_ev or charge).ledger_ts
            first_ts = ts if first_ts is None or ts < first_ts else first_ts
            last_ts = ts if last_ts is None or ts > last_ts else last_ts

        attestation = {
            "subject": party_id,
            "merchant": merchant_party_id,
            "audience": audience_party_id,
            "stats": {
                "orders_completed": completed,
                "cases_lost": lost,
                "first_order_ts": first_ts,
                "last_order_ts": last_ts,
            },
            "issued_at": self.ledger.now_iso(),
        }
        return crypto.sign_attestation(self.ledger.keypair.private_key, attestation)

    def _case_input(self, case_id: str) -> rules.CaseInput:
        case = self.ledger.get_case(case_id)
        events = self.ledger.get_trail(case["order_id"])
        reveals = self.ledger.reveals_for_case(case_id)
        roles = {pid: self.ledger.party(pid).role for pid in self.ledger.order_parties(case["order_id"])}
        sig_status = {
            e.event_id: ("missing" if not e.sig else ("ok" if self.ledger.verify_event_sig(e) else "bad"))
            for e in events
        }
        already = Decimal("0.00")
        order_ev = next((e for e in events if e.type == "ORDER"), None)
        if order_ev and "already_spent" in order_ev.public:
            already = crypto.parse_amount(order_ev.public["already_spent"])
        return rules.CaseInput(
            case_id=case_id,
            order_id=case["order_id"],
            claim=case["claim"],
            events=[e.to_dict() for e in events],
            reveals=reveals,
            roles=roles,
            sig_status=sig_status,
            already_spent=already,
        )

    @_tool
    def get_case_bundle(self, case_id: str, party_id: str) -> dict[str, Any]:
        case = self.ledger.get_case(case_id)
        self._require_party_to_order(case["order_id"], party_id)
        bundle = rules.evaluate(self._case_input(case_id))
        bundle["status"] = case["status"]
        bundle["resolution"] = case["resolution"]
        bundle["opened_by"] = case["opened_by"]
        bundle["note"] = case["note"]
        bundle["revealed"] = {
            eid: {"party_id": r["party_id"], "verified": r["verified"]} for eid, r in self._case_input(case_id).reveals.items()
        }
        return bundle

    @_tool
    def export_evidence_pack(self, case_id: str, reason_code: str, party_id: str) -> dict[str, Any]:
        """Map a case to a Stripe-like dispute-evidence dict (brief §8).

        Any party to the order may export it: the merchant to answer a
        chargeback, the buyer to raise one when the merchant rejects a claim.
        """
        case = self.ledger.get_case(case_id)
        order_id = case["order_id"]
        self._require_party_to_order(order_id, party_id)
        inp = self._case_input(case_id)
        bundle = rules.evaluate(inp)
        events = {e["event_id"]: e for e in inp.events}
        by_type: dict[str, dict[str, Any]] = {}
        for e in inp.events:
            by_type[e["type"]] = e  # last of each type wins

        def revealed(type_: str) -> dict[str, Any] | None:
            e = by_type.get(type_)
            if not e:
                return None
            r = inp.reveals.get(e["event_id"])
            return r["payload"] if r and r["verified"] else None

        mandate_ev, offer_ev = by_type.get("MANDATE"), by_type.get("OFFER")
        offer, fulfil = revealed("OFFER"), revealed("FULFILMENT")
        product_description = None
        if offer:
            product_description = (
                f"{offer.get('qty')} {offer.get('unit')} x {offer.get('title')} (sku {offer.get('sku')}), "
                f"{offer.get('total')} {offer.get('currency')}; shipping: {offer.get('shipping_terms')}; "
                f"returns: {offer.get('return_terms')}"
            )
        pack = {
            "reason_code": reason_code,
            "product_description": product_description,
            "customer_signature": (
                {
                    "mandate_commit": mandate_ev["commit"],
                    "user_party_id": mandate_ev["party_id"],
                    "user_sig": mandate_ev["sig"],
                    "ledger_receipt": _receipt_of(mandate_ev),
                }
                if mandate_ev
                else None
            ),
            "receipt": (
                {"offer_commit": offer_ev["commit"], "ledger_receipt": _receipt_of(offer_ev)} if offer_ev else None
            ),
            "shipping_documentation": fulfil,
            "uncategorized_text": (
                f"TrailProof case {case_id} for order {order_id}. Claim: {case['claim']}. "
                f"Verdict: {bundle['verdict']} ({bundle['rationale']}).\n" + rules.findings_prose(bundle["findings"])
            ),
            "trailproof": {
                "case_id": case_id,
                "order_id": order_id,
                "ledger_public_key": self.ledger.keypair.public_hex,
                "verdict": bundle["verdict"],
                "findings": bundle["findings"],
                # public keys of every party to the order, so a reviewer can re-verify writer signatures
                "parties": {
                    pid: {"role": self.ledger.party(pid).role, "public_key": self.ledger.party(pid).public_key}
                    for pid in sorted(self.ledger.order_parties(order_id))
                },
                # plaintexts that were revealed into the case and verified against their commitments
                "revealed_payloads": {
                    eid: r["payload"] for eid, r in inp.reveals.items() if r["verified"] and r["payload"] is not None
                },
                "events": [
                    {
                        "event_id": e["event_id"],
                        "type": e["type"],
                        "party_id": e["party_id"],
                        "commit": e["commit"],
                        "prev": e["prev"],
                        "refs": e["refs"],
                        "sig": e["sig"],
                        "ledger_receipt": _receipt_of(e),
                        "revealed": inp.reveals.get(e["event_id"], {}).get("verified"),
                    }
                    for e in inp.events
                ],
                "cites": bundle["cites"],
            },
        }
        return pack


def _receipt_of(e: dict[str, Any]) -> dict[str, str]:
    return {"event_id": e["event_id"], "ledger_ts": e["ledger_ts"], "ledger_sig": e["ledger_sig"]}
