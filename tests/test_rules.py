"""One test per finding code, plus the verdict table."""

from decimal import Decimal

from trailproof import crypto
from trailproof.rules import CaseInput, Finding, decide, evaluate, findings_prose

from .helpers import NOW, mandate_payload, offer_payload

ROLES = {"pty_user": "user", "pty_agent": "buyer_agent", "pty_shop": "merchant", "pty_psp": "psp"}
WRITER = {"MANDATE": "pty_user", "OFFER": "pty_shop", "ORDER": "pty_agent", "CHARGE": "pty_psp",
          "FULFILMENT": "pty_shop", "DELIVERY": "pty_agent", "DISPUTE": "pty_agent", "REFUND": "pty_psp"}


class Case:
    """Build a CaseInput from (type, payload) pairs; everything revealed & verified by default."""

    def __init__(self, claim="not_as_described"):
        self.claim = claim
        self.events = []
        self.reveals = {}
        self.sig_status = {}
        self.prev = None

    def add(self, type_, payload, revealed=True, verified=True, sig="ok"):
        eid = f"evt_{type_.lower()}_{len(self.events)}"
        c = crypto.commit(payload)
        self.events.append({
            "event_id": eid, "order_id": "ord_1", "type": type_, "party_id": WRITER[type_], "commit": c,
            "refs": [], "prev": self.prev, "sig": "sig", "ledger_ts": crypto.iso_z(NOW), "ledger_sig": "lsig",
        })
        self.prev = c
        if revealed:
            self.reveals[eid] = {"party_id": WRITER[type_], "payload": payload if verified else None, "verified": verified}
        self.sig_status[eid] = sig
        return eid

    def run(self, already_spent="0.00"):
        return evaluate(CaseInput("case_1", "ord_1", self.claim, self.events, self.reveals, ROLES, self.sig_status, Decimal(already_spent)))


def standard(claim="not_as_described", charge="36.90", fulfil=None, delivery=None, offer=None, mandate=None):
    c = Case(claim)
    m, o = mandate or mandate_payload(), offer or offer_payload()
    c.add("MANDATE", m)
    c.add("OFFER", o)
    c.add("ORDER", {"mandate_commit": crypto.commit(m), "offer_commit": crypto.commit(o), "qty": o["qty"], "total": o["total"], "currency": o["currency"]})
    c.add("CHARGE", {"amount": charge, "currency": "EUR", "psp_ref": "ch_1"})
    if fulfil is not None:
        c.add("FULFILMENT", fulfil)
    if delivery is not None:
        c.add("DELIVERY", delivery)
    return c


def codes(bundle):
    return [f["code"] for f in bundle["findings"]]


def test_clean_case_is_in_scope_and_ambiguous():
    b = standard(fulfil={"sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "carrier": "DHL", "tracking": "X"},
                 delivery={"received": True, "sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "note": ""}).run()
    assert codes(b) == ["MANDATE_IN_SCOPE"]
    assert b["verdict"] == "ambiguous" and b["remedy"]["type"] == "none"


def test_charge_ne_offer():
    b = standard(charge="42.90").run()
    assert "CHARGE_NE_OFFER" in codes(b)
    f = next(f for f in b["findings"] if f["code"] == "CHARGE_NE_OFFER")
    assert len(f["evidence"]) == 2 and "42.90" in f["detail"]
    assert b["verdict"] == "merchant_at_fault"
    assert b["remedy"] == {"type": "refund", "amount": "42.90", "currency": "EUR"}


def test_delivered_ne_offer_from_fulfilment_and_delivery():
    b = standard(fulfil={"sku": "DOGFOOD-5KG", "qty": 1, "unit": "bag_5kg", "carrier": "DHL", "tracking": "X"}).run()
    assert codes(b).count("DELIVERED_NE_OFFER") == 1
    b = standard(delivery={"received": True, "sku": "DOGFOOD-10KG", "qty": 2, "unit": "bag_10kg", "note": ""}).run()
    assert codes(b).count("DELIVERED_NE_OFFER") == 1
    assert "qty" in next(f for f in b["findings"] if f["code"] == "DELIVERED_NE_OFFER")["detail"]


def test_not_received_without_carrier_confirmation():
    d = {"received": False, "sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "note": "nothing"}
    b = standard(claim="not_received", delivery=d).run()
    assert "NOT_RECEIVED" in codes(b) and b["verdict"] == "merchant_at_fault"
    # with a tracked fulfilment the rule does not fire (carrier confirmation exists)
    b = standard(claim="not_received", delivery=d, fulfil={"sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "carrier": "DHL", "tracking": "X"}).run()
    assert "NOT_RECEIVED" not in codes(b)
    # untracked fulfilment does not count as confirmation
    b = standard(claim="not_received", delivery=d, fulfil={"sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "carrier": "", "tracking": ""}).run()
    assert "NOT_RECEIVED" in codes(b)


def test_mandate_in_scope_and_out_of_scope():
    b = standard().run()
    assert "MANDATE_IN_SCOPE" in codes(b)
    o = offer_payload(category="cat_toys", total="55.00", unit_price="55.00")
    b = standard(offer=o, charge="55.00").run()
    assert "MANDATE_OUT_OF_SCOPE" in codes(b)
    f = next(f for f in b["findings"] if f["code"] == "MANDATE_OUT_OF_SCOPE")
    assert "CATEGORY_NOT_ALLOWED" in f["detail"] and "OVER_BUDGET" in f["detail"]
    assert b["verdict"] == "buyer_at_fault"


def test_mandate_scope_uses_spend_before_the_order():
    o = offer_payload(total="30.00", unit_price="30.00")
    assert "MANDATE_IN_SCOPE" in codes(standard(offer=o, charge="30.00").run("0.00"))
    assert "MANDATE_OUT_OF_SCOPE" in codes(standard(offer=o, charge="30.00").run("30.00"))


def test_unverifiable_names_the_party_that_owes_it():
    c = Case()
    m, o = mandate_payload(), offer_payload()
    c.add("MANDATE", m)
    c.add("OFFER", o)
    c.add("ORDER", {"mandate_commit": crypto.commit(m), "offer_commit": crypto.commit(o), "qty": 1, "total": "36.90", "currency": "EUR"})
    c.add("CHARGE", {"amount": "36.90", "currency": "EUR", "psp_ref": "x"}, revealed=False)
    b = c.run()
    f = next(f for f in b["findings"] if f["code"] == "UNVERIFIABLE")
    assert "psp pty_psp owes it" in f["detail"]
    assert b["verdict"] == "unverifiable"
    # an event that was never recorded at all is also unverifiable
    c2 = Case()
    c2.add("MANDATE", m)
    b2 = c2.run()
    assert sum(f["code"] == "UNVERIFIABLE" for f in b2["findings"]) == 3  # OFFER, ORDER, CHARGE missing


def test_tampered_names_the_party_and_decides_against_it():
    c = standard()
    c.add("FULFILMENT", {"sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "carrier": "DHL", "tracking": "X"}, verified=False)
    b = c.run()
    f = next(f for f in b["findings"] if f["code"] == "TAMPERED")
    assert f["detail"].startswith("merchant pty_shop") and f["party_id"] == "pty_shop"
    assert b["verdict"] == "merchant_at_fault" and b["remedy"]["type"] == "refund"
    # buyer-side tampering goes against the buyer
    c = standard()
    c.add("DELIVERY", {"received": False, "sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "note": ""}, verified=False)
    b = c.run()
    assert b["verdict"] == "buyer_at_fault"


def test_missing_and_bad_sig():
    c = standard()
    c.add("FULFILMENT", {"sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "carrier": "DHL", "tracking": "X"}, sig="missing")
    assert "MISSING_SIG" in codes(c.run())
    c = standard()
    c.add("FULFILMENT", {"sku": "DOGFOOD-10KG", "qty": 1, "unit": "bag_10kg", "carrier": "DHL", "tracking": "X"}, sig="bad")
    b = c.run()
    assert "BAD_SIG" in codes(b) and b["verdict"] == "merchant_at_fault"


def test_cites_collect_all_evidence():
    b = standard(charge="42.90").run()
    assert set(b["cites"]) == {e for f in b["findings"] for e in f["evidence"]}
    assert "CHARGE_NE_OFFER" in findings_prose(b["findings"])
    assert findings_prose([]) == "No findings."


# ---------------------------------------------------------------- verdict table
def F(code, detail="merchant pty_shop did it", party="pty_shop"):
    return Finding(code, "high", [], detail, party)


CHARGE = {"amount": "42.90", "currency": "EUR"}


def test_verdict_table():
    in_scope, out_scope = F("MANDATE_IN_SCOPE"), F("MANDATE_OUT_OF_SCOPE")
    # 1. tampering beats everything
    assert decide("not_as_described", [F("TAMPERED", "merchant pty_shop x", "pty_shop"), out_scope], ROLES, CHARGE, None)[0] == "merchant_at_fault"
    assert decide("not_as_described", [F("TAMPERED", "buyer_agent pty_agent x", "pty_agent"), F("CHARGE_NE_OFFER")], ROLES, CHARGE, None)[0] == "buyer_at_fault"
    assert decide("not_as_described", [F("TAMPERED", "psp pty_psp x", "pty_psp")], ROLES, CHARGE, None)[0] == "unverifiable"
    # 2. merchant finding + in scope -> merchant, refund of the charge
    v, remedy, _ = decide("not_as_described", [F("CHARGE_NE_OFFER"), in_scope], ROLES, CHARGE, None)
    assert v == "merchant_at_fault" and remedy == {"type": "refund", "amount": "42.90", "currency": "EUR"}
    assert decide("not_received", [F("NOT_RECEIVED"), in_scope], ROLES, CHARGE, None)[0] == "merchant_at_fault"
    # 3. out of scope, merchant clean -> buyer
    assert decide("not_authorised", [out_scope], ROLES, CHARGE, None)[0] == "buyer_at_fault"
    assert decide("not_as_described", [out_scope], ROLES, CHARGE, None)[0] == "buyer_at_fault"
    # 4. friendly fraud: not_authorised but mandate covers it -> buyer
    assert decide("not_authorised", [in_scope], ROLES, CHARGE, None)[0] == "buyer_at_fault"
    # 5. both sides deviated -> ambiguous
    assert decide("not_as_described", [F("CHARGE_NE_OFFER"), out_scope], ROLES, CHARGE, None)[0] == "ambiguous"
    # 6. unverifiable
    assert decide("not_received", [F("UNVERIFIABLE"), in_scope], ROLES, CHARGE, None)[0] == "unverifiable"
    # 7. nothing
    assert decide("not_as_described", [in_scope], ROLES, CHARGE, None)[0] == "ambiguous"
    assert decide("not_as_described", [], ROLES, None, None)[0] == "ambiguous"
    # refund falls back to the offer total when no charge was revealed
    assert decide("not_as_described", [F("DELIVERED_NE_OFFER"), in_scope], ROLES, None, {"total": "36.90", "currency": "EUR"})[1]["amount"] == "36.90"


def test_default_judgment_rule_5b_goes_against_the_side_that_withholds():
    """Rule 5b (opt-in): a side that committed to events and then withholds the plaintext loses."""
    in_scope = F("MANDATE_IN_SCOPE")
    owed_by_merchant = F("UNVERIFIABLE", "OFFER was never revealed; merchant pty_shop owes it", "pty_shop")
    owed_by_user = F("UNVERIFIABLE", "MANDATE was never revealed; user pty_user owes it", "pty_user")
    never_recorded = F("UNVERIFIABLE", "no FULFILMENT event was ever recorded for this order", None)
    # off by default: unchanged behaviour
    assert decide("not_as_described", [owed_by_merchant], ROLES, CHARGE, None)[0] == "unverifiable"
    # on: the withholding side loses, and the merchant's refund is the charge
    v, remedy, why = decide("not_as_described", [owed_by_merchant], ROLES, CHARGE, None, default_judgment=True)
    assert v == "merchant_at_fault" and remedy["amount"] == CHARGE["amount"] and "default judgment" in why
    assert decide("not_received", [owed_by_user, in_scope], ROLES, CHARGE, None, default_judgment=True)[0] == "buyer_at_fault"
    # an event nobody ever recorded names nobody: still unverifiable
    assert decide("not_received", [never_recorded], ROLES, CHARGE, None, default_judgment=True)[0] == "unverifiable"
    # both sides withholding is not one side's fault
    assert decide("not_received", [owed_by_merchant, owed_by_user], ROLES, CHARGE, None, default_judgment=True)[0] == "unverifiable"
    # conclusive findings still win over 5b
    assert decide("not_as_described", [F("TAMPERED", "merchant pty_shop x", "pty_shop"), owed_by_user], ROLES, CHARGE, None, default_judgment=True)[0] == "merchant_at_fault"
