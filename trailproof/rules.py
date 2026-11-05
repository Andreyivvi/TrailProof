"""Deterministic rules: the mandate scope check and the dispute findings engine.

No LLM anywhere in here. Every function is pure: given the same revealed
payloads it returns the same findings, so any party can re-run it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from . import crypto

CLAIMS = ("not_as_described", "not_received", "not_authorised")

MERCHANT_FAULT_CODES = ("CHARGE_NE_OFFER", "DELIVERED_NE_OFFER", "NOT_RECEIVED")

VERDICTS = ("merchant_at_fault", "buyer_at_fault", "unverifiable", "ambiguous")


# ---------------------------------------------------------------------------
# Scope check (brief §5)
# ---------------------------------------------------------------------------


def scope_check(
    mandate: dict[str, Any],
    offer: dict[str, Any],
    offer_merchant_id: str,
    qty: Any,
    total: Any,
    currency: str,
    already_spent: Decimal,
    now: datetime,
) -> tuple[bool, list[dict[str, str]]]:
    """Return ``(in_scope, reasons)``. Every failing rule is reported, not just the first.

    Rules:
      * mandate not expired
      * merchant in ``mandate.merchants`` or ``"*"``
      * offer not expired
      * offer category in ``mandate.categories``
      * ``total <= max_amount - already_spent`` (spend is tracked per user+nonce)
      * order currency == mandate currency == offer currency
      * order qty/total equal the offer's qty/total (the agent orders what was offered)
    """
    reasons: list[dict[str, str]] = []

    def fail(code: str, detail: str) -> None:
        reasons.append({"code": code, "detail": detail})

    # --- mandate validity
    try:
        if crypto.parse_iso_z(str(mandate["valid_until"])) < now:
            fail("MANDATE_EXPIRED", f"mandate expired at {mandate['valid_until']}")
    except (KeyError, ValueError):
        fail("MANDATE_MALFORMED", "mandate.valid_until missing or not ISO-8601")

    merchants = mandate.get("merchants", [])
    if merchants != "*" and not (isinstance(merchants, list) and ("*" in merchants or offer_merchant_id in merchants)):
        fail("MERCHANT_NOT_ALLOWED", f"merchant {offer_merchant_id} is not in mandate.merchants")

    # --- offer validity
    try:
        if crypto.parse_iso_z(str(offer["valid_until"])) < now:
            fail("OFFER_EXPIRED", f"offer expired at {offer['valid_until']}")
    except (KeyError, ValueError):
        fail("OFFER_MALFORMED", "offer.valid_until missing or not ISO-8601")

    categories = mandate.get("categories", [])
    if offer.get("category") not in categories:
        fail(
            "CATEGORY_NOT_ALLOWED",
            f"offer category {offer.get('category')!r} not in mandate categories {categories}",
        )

    # --- money
    try:
        total_d = crypto.parse_amount(total)
        max_amount = crypto.parse_amount(mandate["max_amount"])
        remaining = max_amount - already_spent
        if total_d > remaining:
            fail(
                "OVER_BUDGET",
                f"total {crypto.amount_str(total_d)} exceeds remaining budget "
                f"{crypto.amount_str(remaining)} (max {crypto.amount_str(max_amount)}, "
                f"already spent {crypto.amount_str(already_spent)})",
            )
    except (KeyError, ValueError):
        fail("AMOUNT_MALFORMED", "total or mandate.max_amount is not a valid amount")
        total_d = None

    if not (currency == mandate.get("currency") == offer.get("currency")):
        fail(
            "CURRENCY_MISMATCH",
            f"order {currency!r}, mandate {mandate.get('currency')!r}, offer {offer.get('currency')!r}",
        )

    # --- the order must be the offer
    try:
        if total_d is not None and crypto.parse_amount(offer["total"]) != total_d:
            fail("ORDER_NE_OFFER", f"order total {total} != offer total {offer['total']}")
    except (KeyError, ValueError):
        fail("OFFER_MALFORMED", "offer.total missing or not a valid amount")
    if str(qty) != str(offer.get("qty")):
        fail("ORDER_NE_OFFER", f"order qty {qty} != offer qty {offer.get('qty')}")

    return (not reasons), reasons


# ---------------------------------------------------------------------------
# Findings engine (brief §6)
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    code: str
    severity: str  # "high" | "medium" | "info"
    evidence: list[str]
    detail: str
    party_id: str | None = None  # the party a finding is *against* / who owes something

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "evidence": list(self.evidence),
            "detail": self.detail,
            "party_id": self.party_id,
        }


@dataclass
class CaseInput:
    """Everything the rules engine needs, assembled by the server.

    ``events`` are the order's ledger events (dicts, hashes only), in chain
    order. ``reveals`` maps event_id to ``{"party_id", "payload", "verified"}``.
    ``roles`` maps party_id to its role. ``sig_status`` maps event_id to
    ``"ok" | "missing" | "bad"`` as re-verified by the ledger.
    ``already_spent`` is the spend recorded under the mandate *before* this
    order, so the scope check can be recomputed as of the ORDER event.
    """

    case_id: str
    order_id: str
    claim: str
    events: list[dict[str, Any]]
    reveals: dict[str, dict[str, Any]]
    roles: dict[str, str]
    sig_status: dict[str, str] = field(default_factory=dict)
    already_spent: Decimal = Decimal("0.00")


def _events_of(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == type_]


def _payload(inp: CaseInput, event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    r = inp.reveals.get(event["event_id"])
    if r and r.get("verified"):
        return r["payload"]
    return None


def evaluate(inp: CaseInput) -> dict[str, Any]:
    """Run every rule over a case and return the bundle (brief §6)."""
    findings: list[Finding] = []
    roles = inp.roles

    def who(party_id: str) -> str:
        return f"{roles.get(party_id, 'unknown')} {party_id}"

    # 1. Signature status as re-verified by the ledger.
    for ev in inp.events:
        st = inp.sig_status.get(ev["event_id"], "ok")
        if st == "missing":
            findings.append(Finding("MISSING_SIG", "high", [ev["event_id"]], f"{ev['type']} has no writer signature", ev["party_id"]))
        elif st == "bad":
            findings.append(Finding("BAD_SIG", "high", [ev["event_id"]], f"{ev['type']} signature by {who(ev['party_id'])} does not verify", ev["party_id"]))

    # 2. Tampering: a reveal that did not match the committed hash.
    for event_id, r in inp.reveals.items():
        if not r.get("verified"):
            ev = next((e for e in inp.events if e["event_id"] == event_id), None)
            typ = ev["type"] if ev else "event"
            findings.append(
                Finding(
                    "TAMPERED",
                    "high",
                    [event_id],
                    f"{who(r['party_id'])} revealed {typ} plaintext whose hash does not match the commitment it made earlier",
                    r["party_id"],
                )
            )

    # 3. Which events we need, and which were never revealed.
    mandate_ev = _last(_events_of(inp.events, "MANDATE"))
    offer_ev = _last(_events_of(inp.events, "OFFER"))
    order_ev = _last(_events_of(inp.events, "ORDER"))
    charge_ev = _last(_events_of(inp.events, "CHARGE"))
    fulfil_ev = _last(_events_of(inp.events, "FULFILMENT"))
    delivery_ev = _last(_events_of(inp.events, "DELIVERY"))

    needed = [("MANDATE", mandate_ev), ("OFFER", offer_ev), ("ORDER", order_ev), ("CHARGE", charge_ev)]
    if fulfil_ev:
        needed.append(("FULFILMENT", fulfil_ev))
    if delivery_ev:
        needed.append(("DELIVERY", delivery_ev))
    for name, ev in needed:
        if ev is None:
            findings.append(Finding("UNVERIFIABLE", "medium", [], f"no {name} event was ever recorded for this order"))
        elif ev["event_id"] not in inp.reveals:
            findings.append(
                Finding("UNVERIFIABLE", "medium", [ev["event_id"]], f"{name} was never revealed; {who(ev['party_id'])} owes it", ev["party_id"])
            )

    mandate = _payload(inp, mandate_ev)
    offer = _payload(inp, offer_ev)
    order = _payload(inp, order_ev)
    charge = _payload(inp, charge_ev)
    fulfil = _payload(inp, fulfil_ev)
    delivery = _payload(inp, delivery_ev)

    # 4. Money: charge vs offer.
    if charge and offer:
        try:
            if crypto.parse_amount(charge["amount"]) != crypto.parse_amount(offer["total"]) or charge.get("currency") != offer.get("currency"):
                findings.append(
                    Finding(
                        "CHARGE_NE_OFFER",
                        "high",
                        [charge_ev["event_id"], offer_ev["event_id"]],
                        f"charged {charge['amount']} {charge.get('currency')} but the offer committed to {offer['total']} {offer.get('currency')}",
                    )
                )
        except (KeyError, ValueError):
            findings.append(Finding("CHARGE_NE_OFFER", "high", [charge_ev["event_id"]], "charge or offer amount malformed"))

    # 5. Goods: fulfilment / delivery vs offer.
    if offer:
        for label, ev, p in (("fulfilment", fulfil_ev, fulfil), ("delivery", delivery_ev, delivery)):
            if not p:
                continue
            diffs = [
                f"{k}: {label} {p.get(k)!r} vs offer {offer.get(k)!r}"
                for k in ("sku", "qty", "unit")
                if str(p.get(k)) != str(offer.get(k))
            ]
            if diffs:
                findings.append(
                    Finding("DELIVERED_NE_OFFER", "high", [ev["event_id"], offer_ev["event_id"]], "; ".join(diffs))
                )

    # 6. Not received: buyer says no, and there is no carrier confirmation.
    if delivery and delivery.get("received") is False:
        carrier_confirmed = bool(fulfil and fulfil.get("carrier") and fulfil.get("tracking"))
        if not carrier_confirmed:
            findings.append(
                Finding(
                    "NOT_RECEIVED",
                    "high",
                    [delivery_ev["event_id"]] + ([fulfil_ev["event_id"]] if fulfil_ev else []),
                    "buyer recorded received=false and no fulfilment with carrier+tracking was revealed",
                )
            )

    # 7. Mandate scope, recomputed as of the ORDER event's ledger timestamp.
    if mandate and offer and order and order_ev:
        as_of = crypto.parse_iso_z(order_ev["ledger_ts"])
        ok, reasons = scope_check(
            mandate,
            offer,
            offer_ev["party_id"],
            order.get("qty"),
            order.get("total"),
            order.get("currency", ""),
            inp.already_spent,
            as_of,
        )
        ev_ids = [mandate_ev["event_id"], offer_ev["event_id"], order_ev["event_id"]]
        if ok:
            findings.append(Finding("MANDATE_IN_SCOPE", "info", ev_ids, "order was within the user's signed mandate at the time of ordering"))
        else:
            findings.append(
                Finding("MANDATE_OUT_OF_SCOPE", "high", ev_ids, "; ".join(f"{r['code']}: {r['detail']}" for r in reasons))
            )

    verdict, remedy, rationale = decide(inp.claim, findings, roles, charge, offer)
    cites = sorted({e for f in findings for e in f.evidence})
    return {
        "case_id": inp.case_id,
        "order_id": inp.order_id,
        "claim": inp.claim,
        "findings": [f.to_dict() for f in findings],
        "verdict": verdict,
        "rationale": rationale,
        "remedy": remedy,
        "cites": cites,
    }


def decide(
    claim: str,
    findings: list[Finding],
    roles: dict[str, str],
    charge: dict[str, Any] | None,
    offer: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], str]:
    """The verdict table (brief §6), in priority order:

    1. any ``TAMPERED`` / ``BAD_SIG`` -> against the party that tampered
       (merchant -> merchant_at_fault, user or buyer agent -> buyer_at_fault,
       psp -> unverifiable).
    2. a merchant finding (``CHARGE_NE_OFFER``, ``DELIVERED_NE_OFFER``,
       ``NOT_RECEIVED``) with ``MANDATE_IN_SCOPE`` -> merchant_at_fault,
       remedy: refund the charge.
    3. ``MANDATE_OUT_OF_SCOPE`` with no merchant finding -> buyer_at_fault
       (the agent overstepped; a "not_authorised" claim is upheld against the
       agent, not the merchant).
    4. claim ``not_authorised`` with ``MANDATE_IN_SCOPE`` and no merchant
       finding -> buyer_at_fault (the user did authorise it: the mandate proves it).
    5. a merchant finding together with ``MANDATE_OUT_OF_SCOPE`` -> ambiguous
       (both sides at fault; needs a human).
    6. any ``UNVERIFIABLE`` and nothing conclusive -> unverifiable.
    7. nothing found -> ambiguous.
    """
    codes = {f.code for f in findings}
    none: dict[str, Any] = {"type": "none", "amount": None, "currency": None}

    tampered = [f for f in findings if f.code in ("TAMPERED", "BAD_SIG")]
    if tampered:
        detail = tampered[0].detail
        role = roles.get(tampered[0].party_id or "", "unknown")
        if role == "merchant":
            return "merchant_at_fault", _refund(charge, offer), f"merchant tampered with evidence: {detail}"
        if role in ("user", "buyer_agent"):
            return "buyer_at_fault", none, f"buyer side tampered with evidence: {detail}"
        return "unverifiable", none, f"a third party tampered with evidence: {detail}"

    merchant_fault = [f for f in findings if f.code in MERCHANT_FAULT_CODES]
    in_scope = "MANDATE_IN_SCOPE" in codes
    out_of_scope = "MANDATE_OUT_OF_SCOPE" in codes

    if merchant_fault and in_scope:
        return (
            "merchant_at_fault",
            _refund(charge, offer),
            "order was authorised and the merchant did not deliver what it offered: "
            + "; ".join(f.code for f in merchant_fault),
        )
    if out_of_scope and not merchant_fault:
        return (
            "buyer_at_fault",
            none,
            "the buyer agent ordered outside the user's mandate; the merchant delivered what it offered",
        )
    if claim == "not_authorised" and in_scope and not merchant_fault:
        return (
            "buyer_at_fault",
            none,
            "the user's signed mandate covers this order and the merchant delivered what it offered",
        )
    if merchant_fault and out_of_scope:
        return "ambiguous", none, "both the merchant and the buyer agent deviated; needs human review"
    if "UNVERIFIABLE" in codes:
        return "unverifiable", none, "a needed event was never revealed"
    return "ambiguous", none, "no rule produced a conclusive finding"


def _refund(charge: dict[str, Any] | None, offer: dict[str, Any] | None) -> dict[str, Any]:
    src = charge or offer
    if not src:
        return {"type": "refund", "amount": None, "currency": None}
    amount = src.get("amount", src.get("total"))
    try:
        amount = crypto.amount_str(crypto.parse_amount(amount))
    except ValueError:
        pass
    return {"type": "refund", "amount": amount, "currency": src.get("currency")}


def _last(items: list[Any]) -> Any | None:
    return items[-1] if items else None


def findings_prose(findings: list[dict[str, Any]]) -> str:
    """Findings as plain sentences, for the evidence pack's ``uncategorized_text``."""
    if not findings:
        return "No findings."
    lines = []
    for f in findings:
        ev = ", ".join(f["evidence"]) if f["evidence"] else "no specific event"
        lines.append(f"[{f['severity'].upper()}] {f['code']}: {f['detail']} (evidence: {ev})")
    return "\n".join(lines)
