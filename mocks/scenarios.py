"""The demo scenarios (brief §9, plus brand recognition), shared by ``demo.py`` and the tests.

Each scenario drives the mocks through the TrailProof tools and returns a
result dict with ``checks`` (name -> bool) plus the context needed to print
or assert on it. Scenarios are independent: each uses its own order ids and
its own mandate nonce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .buyer import ScriptedBuyer
from .psp import MockPSP
from .shop import MockShop
from .transport import UnreachableToolClient


@dataclass
class World:
    client: Any
    buyer: ScriptedBuyer
    shop: MockShop
    psp: MockPSP
    log: list[str] = field(default_factory=list)
    extra_roles: dict[str, str] = field(default_factory=dict)  # parties a scenario registers itself

    @classmethod
    async def create(cls, client: Any) -> "World":
        w = cls(client, ScriptedBuyer(client), MockShop(client), MockPSP(client))
        await w.buyer.register()
        await w.shop.register()
        await w.psp.register()
        return w

    def roles(self) -> dict[str, str]:
        return {
            self.buyer.user.party_id: "user",
            self.buyer.agent.party_id: "buyer_agent",
            self.shop.party_id: "merchant",
            self.psp.party_id: "psp",
            **self.extra_roles,
        }

    async def new_buyer(self, user_name: str, operator: str) -> ScriptedBuyer:
        """Register a second buyer (user key + agent key) for scenarios that need one."""
        b = ScriptedBuyer(self.client, user_name=user_name, operator=operator)
        await b.register()
        self.extra_roles[b.user.party_id] = "user"
        self.extra_roles[b.agent.party_id] = "buyer_agent"
        return b

    def say(self, line: str) -> None:
        self.log.append(line)

    async def trail(self, order_id: str) -> dict[str, Any]:
        return await self.buyer.agent.trail(order_id)


async def _order_flow(
    w: World,
    order_id: str,
    sku: str,
    max_amount: str = "40.00",
    categories=("pet_food",),
    nonce: str | None = None,
    buyer: ScriptedBuyer | None = None,
    attestation: Any = None,
):
    buyer = buyer or w.buyer
    mandate = buyer.new_mandate(list(categories), max_amount, nonce=nonce)
    await buyer.submit_mandate(order_id, mandate)
    w.say(f"user signs MANDATE: {','.join(categories)} <= EUR {mandate['max_amount']} until {mandate['valid_until'][:10]}")
    if buyer is w.buyer and attestation is None:
        offer = await w.shop.make_offer(order_id, sku)
        o = offer["payload"]
        w.say(f"merchant commits OFFER: {o['qty']} x {o['title']} = EUR {o['total']}")
    else:
        offer = await w.shop.recognise_and_offer(order_id, sku, attestation, buyer_party_id=buyer.agent.party_id)
        o = offer["payload"]
        tag = "recognised" if offer["recognised"] else "public price"
        w.say(f"merchant commits OFFER: {o['qty']} x {o['title']} = EUR {o['total']}  ({tag}: {offer['reason']})")
    placed = await buyer.place_order(order_id, mandate, o)
    if placed["placed"]:
        w.say("buyer agent check_order -> in scope, ORDER recorded")
    else:
        w.say("buyer agent check_order -> OUT OF SCOPE: " + "; ".join(f"{r['code']}" for r in placed["reasons"]))
    return mandate, o, placed


async def _full_reveal(w: World, case_id: str, order_id: str) -> dict[str, Any]:
    r = await w.buyer.reveal_all(case_id, order_id)
    r.update(await w.psp.reveal_all(case_id, order_id))
    return r


# --------------------------------------------------------------------------
# 1. Happy path
# --------------------------------------------------------------------------
async def scenario_happy(w: World) -> dict[str, Any]:
    order_id = "ord_happy"
    mandate, offer, placed = await _order_flow(w, order_id, "DOGFOOD-10KG")
    charge = await w.shop.take_payment(order_id, w.psp)
    w.say(f"psp CHARGE EUR {charge['payload']['amount']}")
    await w.shop.fulfil(order_id)
    w.say("merchant FULFILMENT 1 x bag_10kg, tracked")
    await w.buyer.confirm_delivery(order_id, True, offer["sku"], offer["qty"], offer["unit"], "all good")
    w.say("buyer DELIVERY received=true")
    trail = await w.trail(order_id)
    types = [e["type"] for e in trail["events"]]
    return {
        "order_id": order_id,
        "trail": trail,
        "checks": {
            "order placed in scope": placed["placed"] and placed["in_scope"] is True,
            "trail is MANDATE,OFFER,ORDER,CHARGE,FULFILMENT,DELIVERY": types == ["MANDATE", "OFFER", "ORDER", "CHARGE", "FULFILMENT", "DELIVERY"],
            "charge equals offer": charge["payload"]["amount"] == offer["total"] == "36.90",
            "no dispute": trail["cases"] == [],
        },
    }


# --------------------------------------------------------------------------
# 2. Merchant fault (the Mia story)
# --------------------------------------------------------------------------
async def scenario_merchant_fault(w: World) -> dict[str, Any]:
    order_id = "ord_mia"
    mandate, offer, placed = await _order_flow(w, order_id, "DOGFOOD-10KG")
    charge = await w.shop.take_payment(order_id, w.psp, amount="42.90")
    w.say(f"psp CHARGE EUR {charge['payload']['amount']}  (offer was 36.90!)")
    await w.shop.fulfil(order_id, sku="DOGFOOD-5KG", unit="bag_5kg")
    w.say("merchant FULFILMENT ships DOGFOOD-5KG / bag_5kg  (offer was 10 kg!)")
    await w.buyer.confirm_delivery(order_id, True, "DOGFOOD-5KG", 1, "bag_5kg", "only 5 kg arrived and I was charged 42.90")
    w.say("buyer DELIVERY received=true but 5 kg")
    dispute = await w.buyer.open_dispute(order_id, "not_as_described", "charged 42.90 for 36.90 offer, got 5 kg not 10 kg")
    case_id = dispute["case_id"]
    w.say(f"buyer open_dispute not_as_described -> {case_id}; merchant notified")
    await _full_reveal(w, case_id, order_id)
    w.say("buyer + psp reveal their plaintexts (all verified)")
    decision = await w.shop.handle_dispute(dispute, w.psp)
    w.say(f"merchant reveals, reads bundle -> {decision['action']} " + (f"EUR {decision.get('amount')}" if decision["action"] == "refunded" else decision.get("reason", "")))
    bundle = await w.buyer.bundle(case_id)
    trail = await w.trail(order_id)
    codes = {f["code"] for f in bundle["findings"]}
    return {
        "order_id": order_id,
        "case_id": case_id,
        "bundle": bundle,
        "decision": decision,
        "trail": trail,
        "checks": {
            "findings CHARGE_NE_OFFER + DELIVERED_NE_OFFER + MANDATE_IN_SCOPE": {"CHARGE_NE_OFFER", "DELIVERED_NE_OFFER", "MANDATE_IN_SCOPE"} <= codes,
            "no TAMPERED/UNVERIFIABLE": not ({"TAMPERED", "UNVERIFIABLE"} & codes),
            "verdict merchant_at_fault": bundle["verdict"] == "merchant_at_fault",
            "remedy refund 42.90": bundle["remedy"] == {"type": "refund", "amount": "42.90", "currency": "EUR"},
            "merchant auto-refunded 42.90": decision["action"] == "refunded" and decision["amount"] == "42.90",
            "REFUND event closes the case": trail["events"][-1]["type"] == "REFUND" and bundle["status"] == "closed",
        },
    }


# --------------------------------------------------------------------------
# 3. Friendly fraud
# --------------------------------------------------------------------------
async def scenario_friendly_fraud(w: World) -> dict[str, Any]:
    order_id = "ord_friendly"
    mandate, offer, placed = await _order_flow(w, order_id, "DOGFOOD-10KG")
    await w.shop.take_payment(order_id, w.psp)
    await w.shop.fulfil(order_id)
    await w.buyer.confirm_delivery(order_id, True, offer["sku"], offer["qty"], offer["unit"], "")
    w.say("charge, fulfilment, delivery all clean")
    dispute = await w.buyer.open_dispute(order_id, "not_authorised", "user says: I never approved this purchase")
    case_id = dispute["case_id"]
    w.say(f"user (via agent) claims not_authorised -> {case_id}")
    await _full_reveal(w, case_id, order_id)
    decision = await w.shop.handle_dispute(dispute, w.psp)
    w.say(f"merchant reads bundle -> {decision['action']}: {decision.get('reason', '')}")
    bundle = await w.shop.bundle(case_id)
    pack = await w.shop.export_evidence(case_id, "fraudulent")
    w.say("merchant export_evidence_pack(reason_code=fraudulent)")
    codes = {f["code"] for f in bundle["findings"]}
    return {
        "order_id": order_id,
        "case_id": case_id,
        "bundle": bundle,
        "decision": decision,
        "pack": pack,
        "trail": await w.trail(order_id),
        "checks": {
            "MANDATE_IN_SCOPE and no merchant findings": "MANDATE_IN_SCOPE" in codes and not (codes & {"CHARGE_NE_OFFER", "DELIVERED_NE_OFFER", "NOT_RECEIVED", "TAMPERED"}),
            "verdict buyer_at_fault": bundle["verdict"] == "buyer_at_fault",
            "merchant rejected the claim": decision["action"] == "rejected",
            "evidence pack carries user signature over mandate": bool(pack.get("customer_signature", {}).get("user_sig")) and _commit_matches(pack, w.buyer.orders[order_id]["mandate"]),
            "evidence pack lists every event with receipts": len(pack["trailproof"]["events"]) == 7 and all(e["ledger_receipt"]["ledger_sig"] for e in pack["trailproof"]["events"]),
        },
    }


def _commit_matches(pack: dict[str, Any], mandate: dict[str, Any]) -> bool:
    from trailproof import crypto

    return pack["customer_signature"]["mandate_commit"] == crypto.commit(mandate)


# --------------------------------------------------------------------------
# 4. Tampering
# --------------------------------------------------------------------------
async def scenario_tampering(w: World) -> dict[str, Any]:
    order_id = "ord_tamper"
    mandate, offer, placed = await _order_flow(w, order_id, "DOGFOOD-10KG")
    await w.shop.take_payment(order_id, w.psp, amount="42.90")
    w.say("psp CHARGE EUR 42.90 (offer committed at 36.90)")
    await w.shop.fulfil(order_id)
    await w.buyer.confirm_delivery(order_id, True, offer["sku"], offer["qty"], offer["unit"], "charged more than offered")
    dispute = await w.buyer.open_dispute(order_id, "not_as_described", "charged 42.90, offer said 36.90")
    case_id = dispute["case_id"]
    await _full_reveal(w, case_id, order_id)
    fake_offer = dict(offer, total="42.90", unit_price="42.90")
    decision = await w.shop.handle_dispute(dispute, w.psp, tamper_offer=fake_offer)
    offer_event_id = w.shop.offer_events[order_id]
    w.say("merchant reveals an OFFER saying 42.90 -> hash mismatch, reveal rejected")
    w.say(f"merchant policy -> {decision['action']}: {decision.get('reason', '')}")
    bundle = await w.buyer.bundle(case_id)
    pack = await w.buyer.export_evidence(case_id, "product_not_as_described")
    w.say("buyer export_evidence_pack for the chargeback")
    codes = {f["code"] for f in bundle["findings"]}
    tampered = [f for f in bundle["findings"] if f["code"] == "TAMPERED"]
    return {
        "order_id": order_id,
        "case_id": case_id,
        "bundle": bundle,
        "decision": decision,
        "pack": pack,
        "trail": await w.trail(order_id),
        "checks": {
            "merchant's reveal failed verification": decision["reveals"].get(offer_event_id) is False,
            "TAMPERED finding names the merchant": bool(tampered) and w.shop.party_id in tampered[0]["detail"],
            "verdict merchant_at_fault": bundle["verdict"] == "merchant_at_fault",
            "remedy refund of the 42.90 charge": bundle["remedy"]["amount"] == "42.90",
            "tampered OFFER is flagged in the evidence pack": any(e["event_id"] == offer_event_id and e["revealed"] is False for e in pack["trailproof"]["events"]),
        },
    }


# --------------------------------------------------------------------------
# 5. Out of scope
# --------------------------------------------------------------------------
async def scenario_out_of_scope(w: World) -> dict[str, Any]:
    order_id = "ord_cattoys"
    mandate, offer, placed = await _order_flow(w, order_id, "CATTOY-SET")
    trail = await w.trail(order_id)
    codes = [r["code"] for r in placed["reasons"]]
    return {
        "order_id": order_id,
        "placed": placed,
        "trail": trail,
        "checks": {
            "check_order says out of scope": placed["placed"] is False and placed["in_scope"] is False,
            "both reasons: CATEGORY_NOT_ALLOWED + OVER_BUDGET": codes == ["CATEGORY_NOT_ALLOWED", "OVER_BUDGET"],
            "buyer aborted: no ORDER on the trail": [e["type"] for e in trail["events"]] == ["MANDATE", "OFFER"],
        },
    }


# --------------------------------------------------------------------------
# 6. Replay
# --------------------------------------------------------------------------
async def scenario_replay(w: World) -> dict[str, Any]:
    nonce = "mandate-replay"
    mandate_a, offer_a, placed_a = await _order_flow(w, "ord_replay_a", "DOGFOOD-30", nonce=nonce)
    await w.shop.take_payment("ord_replay_a", w.psp)
    w.say("first EUR 30 order under nonce 'mandate-replay' -> ok, charged")
    mandate_b, offer_b, placed_b = await _order_flow(w, "ord_replay_b", "DOGFOOD-30", nonce=nonce)
    w.say("second EUR 30 order, same mandate nonce -> rejected on spend")
    codes = [r["code"] for r in placed_b["reasons"]]
    return {
        "order_id": "ord_replay_b",
        "placed_a": placed_a,
        "placed_b": placed_b,
        "trail": await w.trail("ord_replay_b"),
        "trail_a": await w.trail("ord_replay_a"),
        "checks": {
            "same mandate nonce in both orders": mandate_a["nonce"] == mandate_b["nonce"] == nonce,
            "first order placed": placed_a["placed"] is True,
            "second order fails on OVER_BUDGET": placed_b["placed"] is False and codes == ["OVER_BUDGET"],
            "reason cites already-spent 30.00": "already spent 30.00" in placed_b["reasons"][0]["detail"],
        },
    }


# --------------------------------------------------------------------------
# Extra: fail-open when the ledger is unreachable (client-side behaviour)
# --------------------------------------------------------------------------
async def scenario_fail_open() -> dict[str, Any]:
    buyer = ScriptedBuyer(UnreachableToolClient())
    mandate = buyer.new_mandate(["pet_food"], "40.00")
    offer = MockShop(None).quote("DOGFOOD-10KG")
    res = await buyer.place_order("ord_offline", mandate, offer)
    return {
        "result": res,
        "checks": {
            "purchase proceeds with no_receipt=True": res["placed"] is True and res["no_receipt"] is True,
            "order is remembered as un-receipted": "ord_offline" in buyer.no_receipt_orders,
        },
    }


# --------------------------------------------------------------------------
# 7. Recognised buyer (brand-direct customer recognition)
# --------------------------------------------------------------------------
async def scenario_recognised(w: World) -> dict[str, Any]:
    """A returning customer proves its track record to the shop and gets a committed discount.

    Leo's agent completes two clean orders, then asks the ledger for an
    attestation of its history with the shop (counts only, addressed to the
    shop, ledger-signed). The shop verifies it and commits a 10 % discounted
    OFFER. The PSP then charges the public price by mistake; because the
    discounted total was committed, the dispute resolves as a plain
    CHARGE_NE_OFFER and the shop auto-refunds. A stranger with no
    attestation, and one with a forged attestation, both get the public price.
    """
    from trailproof import crypto

    leo = await w.new_buyer("leo", "petbot")
    shop = w.shop
    public_price = shop.quote("DOGFOOD-10KG")["total"]  # 36.90

    # --- two clean orders
    for order_id in ("ord_leo_1", "ord_leo_2"):
        _, offer, placed = await _order_flow(w, order_id, "DOGFOOD-10KG", buyer=leo)
        assert placed["placed"], placed
        await shop.take_payment(order_id, w.psp)
        await shop.fulfil(order_id)
        await leo.confirm_delivery(order_id, True, offer["sku"], offer["qty"], offer["unit"], "")
    w.say("Leo's agent completes two clean orders with the shop (mandate, offer, check_order, charge)")

    # --- the attestation
    att = await leo.request_attestation(shop.party_id)
    stats = att.get("stats", {})
    w.say(
        f"ledger attest_history -> {stats.get('orders_completed')} completed order(s), "
        f"{stats.get('cases_lost')} lost case(s); audience = the shop; no order ids or amounts"
    )

    # --- third order, recognised, then charged the public price by mistake
    order_id = "ord_leo_3"
    mandate, offer, placed = await _order_flow(w, order_id, "DOGFOOD-10KG", buyer=leo, attestation=att)
    recognition = shop.recognitions[order_id]
    charge = await shop.take_payment(order_id, w.psp, amount=public_price)
    w.say(f"psp CHARGE EUR {charge['payload']['amount']}  (recognised offer was {offer['total']}!)")
    await shop.fulfil(order_id)
    await leo.confirm_delivery(order_id, True, offer["sku"], offer["qty"], offer["unit"], f"charged {public_price}, offer said {offer['total']}")
    dispute = await leo.open_dispute(order_id, "not_as_described", f"charged public price {public_price}, recognised offer was {offer['total']}")
    case_id = dispute["case_id"]
    w.say(f"buyer open_dispute not_as_described -> {case_id}")
    await leo.reveal_all(case_id, order_id)
    await w.psp.reveal_all(case_id, order_id)
    decision = await shop.handle_dispute(dispute, w.psp)
    w.say(f"merchant reveals, reads bundle -> {decision['action']} " + (f"EUR {decision.get('amount')}" if decision["action"] == "refunded" else decision.get("reason", "")))
    bundle = await leo.bundle(case_id)
    trail = await w.trail(order_id)
    codes = {f["code"] for f in bundle["findings"]}
    charge_finding = next((f for f in bundle["findings"] if f["code"] == "CHARGE_NE_OFFER"), None)

    # --- strangers: no attestation, then a forged one
    stranger = await w.new_buyer("stranger", "scraperbot")
    _, offer_none, placed_none = await _order_flow(w, "ord_stranger_1", "DOGFOOD-10KG", buyer=stranger)
    w.say(f"unknown agent, no attestation -> public price EUR {offer_none['total']}")
    forged = dict(att, subject=stranger.agent.party_id)  # re-addressed copy: the ledger signature no longer covers it
    _, offer_forged, _ = await _order_flow(w, "ord_stranger_2", "DOGFOOD-10KG", buyer=stranger, attestation=forged)
    w.say(f"unknown agent, forged attestation (bad signature) -> public price EUR {offer_forged['total']}")

    return {
        "order_id": order_id,
        "case_id": case_id,
        "attestation": att,
        "bundle": bundle,
        "decision": decision,
        "trail": trail,
        "trail_a": await w.trail("ord_stranger_2"),
        "recognitions": dict(shop.recognitions),
        "checks": {
            "attestation: 2 completed orders, 0 lost cases, no ids or amounts": (
                stats.get("orders_completed") == 2
                and stats.get("cases_lost") == 0
                and set(stats) == {"orders_completed", "cases_lost", "first_order_ts", "last_order_ts"}
                and set(att) == {"subject", "merchant", "audience", "stats", "issued_at", "ledger_sig"}
            ),
            "attestation is addressed to the shop and verifies with the ledger key": (
                att.get("audience") == shop.party_id and crypto.verify_attestation(att, shop.ledger_public_key)
            ),
            "shop commits a 10% discounted OFFER (33.21) citing the attestation": (
                offer["total"] == "33.21"
                and offer["recognition"] == {"discount_pct": 10, "attestation_commit": crypto.commit(att)}
                and recognition["recognised"] is True
            ),
            "check_order passes on the discounted offer": placed["placed"] is True and placed["in_scope"] is True,
            "CHARGE_NE_OFFER cites the discounted offer total": (
                charge_finding is not None and "33.21" in charge_finding["detail"] and "36.90" in charge_finding["detail"]
            ),
            "verdict merchant_at_fault, MANDATE_IN_SCOPE": bundle["verdict"] == "merchant_at_fault" and "MANDATE_IN_SCOPE" in codes,
            "merchant auto-refunded the 36.90 charge": decision["action"] == "refunded" and decision["amount"] == "36.90",
            "REFUND event closes the case": trail["events"][-1]["type"] == "REFUND" and bundle["status"] == "closed",
            "unknown agent with no attestation gets the public price": (
                offer_none["total"] == public_price and offer_none["recognition"] is None and placed_none["placed"] is True
            ),
            "forged attestation (bad signature) gets the public price": (
                offer_forged["total"] == public_price
                and offer_forged["recognition"] is None
                and "signature" in shop.recognitions["ord_stranger_2"]["reason"]
            ),
        },
    }


Scenario = Callable[[World], Awaitable[dict[str, Any]]]

SCENARIOS: list[tuple[int, str, Scenario]] = [
    (1, "Happy path", scenario_happy),
    (2, "Merchant fault (the Mia story)", scenario_merchant_fault),
    (3, "Friendly fraud", scenario_friendly_fraud),
    (4, "Tampering", scenario_tampering),
    (5, "Out of scope", scenario_out_of_scope),
    (6, "Replay", scenario_replay),
    (7, "Recognised buyer (brand-direct recognition)", scenario_recognised),
]
