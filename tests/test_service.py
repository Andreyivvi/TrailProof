"""The tools as implemented by TrailProofService (transport-free)."""

import pytest

from trailproof import crypto
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService

from .helpers import NOW, Party, mandate_payload, offer_payload


@pytest.fixture
def svc():
    return TrailProofService(Ledger(":memory:", clock=lambda: NOW))


@pytest.fixture
def world(svc):
    return {
        "user": Party(svc, "user", "mia"),
        "buyer": Party(svc, "buyer_agent", "petbot"),
        "shop": Party(svc, "merchant", "pawshop"),
        "psp": Party(svc, "psp", "mockpay"),
    }


def start_order(world, order_id="ord_1", mandate=None, offer=None):
    mandate = mandate or mandate_payload()
    offer = offer or offer_payload()
    assert "event_id" in world["user"].record(order_id, "MANDATE", mandate)
    assert "event_id" in world["shop"].record(order_id, "OFFER", offer)
    return mandate, offer


def test_register_party_errors_are_dicts(svc):
    assert svc.register_party("ledger", "00" * 32, "x")["error"] == "BAD_ROLE"
    assert svc.register_party("merchant", "zz", "x")["error"] == "BAD_KEY"
    ok = svc.register_party("merchant", crypto.Keypair.generate().public_hex, "x")
    assert ok["party_id"].startswith("pty_") and len(ok["ledger_public_key"]) == 64


def test_record_event_returns_receipt_and_rejects_bad_sig(svc, world):
    r = world["user"].record("ord_1", "MANDATE", mandate_payload())
    assert set(r) == {"event_id", "ledger_ts", "ledger_sig"}
    bad = svc.record_event("ord_1", "OFFER", world["shop"].party_id, crypto.commit({"x": 1}), [], "00" * 64)
    assert bad["error"] == "BAD_SIG"
    assert svc.record_event("ord_1", "OFFER", world["buyer"].party_id, crypto.commit({"x": 1}), [], "00")["error"] == "WRONG_ROLE"


def test_get_trail_hashes_only(svc, world):
    start_order(world)
    t = svc.get_trail("ord_1", world["buyer"].party_id)
    assert [e["type"] for e in t["events"]] == ["MANDATE", "OFFER"]
    assert t["head"] == t["events"][-1]["commit"]
    for e in t["events"]:
        assert "payload" not in e and set(e) >= {"commit", "sig", "ledger_sig", "ledger_ts", "prev"}
    assert svc.get_trail("ord_1", "pty_nobody")["error"] == "UNKNOWN_PARTY"


def test_check_order_in_scope_records_order_and_tracks_spend(svc, world):
    mandate, offer = start_order(world)
    res = world["buyer"].check_order("ord_1", mandate, offer, 1, "36.90", "EUR")
    assert res["in_scope"] is True and res["reasons"] == []
    assert res["receipt"]["event_id"].startswith("evt_")
    trail = svc.get_trail("ord_1", world["buyer"].party_id)["events"]
    order = trail[-1]
    assert order["type"] == "ORDER"
    assert order["refs"] == [crypto.commit(mandate), crypto.commit(offer)]
    assert order["public"]["mandate_commit"] == crypto.commit(mandate)
    assert order["public"]["scope_checked"] is True
    assert svc.ledger.spent_under(world["user"].party_id, "nonce-1") == crypto.parse_amount("36.90")


def test_check_order_out_of_scope_returns_all_reasons_and_records_nothing(svc, world):
    offer = offer_payload(sku="CATTOY", title="Cat toys", category="cat_toys", total="55.00", unit_price="55.00")
    mandate, offer = start_order(world, offer=offer)
    res = world["buyer"].check_order("ord_1", mandate, offer, 1, "55.00", "EUR")
    assert res["in_scope"] is False and res["receipt"] is None
    assert [r["code"] for r in res["reasons"]] == ["CATEGORY_NOT_ALLOWED", "OVER_BUDGET"]
    assert [e["type"] for e in svc.get_trail("ord_1", world["buyer"].party_id)["events"]] == ["MANDATE", "OFFER"]


def test_check_order_replay_same_nonce_fails_on_spend(svc, world):
    mandate = mandate_payload()
    o1 = offer_payload(total="30.00", unit_price="30.00", nonce="o1")
    o2 = offer_payload(total="30.00", unit_price="30.00", nonce="o2")
    start_order(world, "ord_1", mandate, o1)
    assert world["buyer"].check_order("ord_1", mandate, o1, 1, "30.00", "EUR")["in_scope"]
    start_order(world, "ord_2", mandate, o2)
    res = world["buyer"].check_order("ord_2", mandate, o2, 1, "30.00", "EUR")
    assert res["in_scope"] is False
    assert [r["code"] for r in res["reasons"]] == ["OVER_BUDGET"]


def test_check_order_rejects_mismatched_plaintext(svc, world):
    mandate, offer = start_order(world)
    tampered = dict(offer, total="1.00")
    mc, oc = crypto.commit(mandate), crypto.commit(offer)
    res = svc.check_order("ord_1", world["buyer"].party_id, mandate, mc, tampered, oc, 1, "1.00", "EUR", "00")
    assert res["error"] == "COMMIT_MISMATCH"
    res = svc.check_order("ord_1", world["buyer"].party_id, mandate, "ab" * 32, offer, oc, 1, "36.90", "EUR", "00")
    assert res["error"] == "UNKNOWN_REF"
    res = svc.check_order("ord_1", world["shop"].party_id, mandate, mc, offer, oc, 1, "36.90", "EUR", "00")
    assert res["error"] == "WRONG_ROLE"


def test_open_dispute_reveal_bundle_flow(svc, world):
    mandate, offer = start_order(world)
    world["buyer"].check_order("ord_1", mandate, offer, 1, "36.90", "EUR")
    world["psp"].record("ord_1", "CHARGE", {"amount": "36.90", "currency": "EUR", "psp_ref": "ch_1"})
    notices = []
    svc.dispute_hooks.append(notices.append)
    d = world["buyer"].open_dispute("ord_1", "not_received", "nothing arrived")
    assert d["case_id"].startswith("case_") and notices[0]["case_id"] == d["case_id"]
    assert world["shop"].party_id in d["notify"] and d["merchant"] == world["shop"].party_id
    # second open dispute on same order is refused while the case is open
    assert world["buyer"].open_dispute("ord_1", "not_received")["error"] == "CASE_ALREADY_OPEN"
    # a stranger cannot open a dispute
    assert svc.open_dispute("ord_1", world["psp"].party_id, "bogus", "", "00")["error"] == "BAD_CLAIM"
    outsider = Party(svc, "buyer_agent", "other")
    assert outsider.open_dispute("ord_1", "not_received")["error"] == "NOT_A_PARTY"
    # reveal: only the writer, only once, verified against the hash
    case_id = d["case_id"]
    offer_event = next(e for e in svc.get_trail("ord_1", world["shop"].party_id)["events"] if e["type"] == "OFFER")
    assert svc.reveal(case_id, offer_event["event_id"], offer, world["buyer"].party_id)["error"] == "NOT_WRITER"
    assert svc.reveal(case_id, offer_event["event_id"], offer, world["shop"].party_id)["verified"] is True
    assert svc.reveal(case_id, offer_event["event_id"], offer, world["shop"].party_id)["error"] == "ALREADY_REVEALED"
    bundle = svc.get_case_bundle(case_id, world["buyer"].party_id)
    assert bundle["case_id"] == case_id and bundle["claim"] == "not_received"
    assert outsider.svc.get_case_bundle(case_id, outsider.party_id)["error"] == "NOT_A_PARTY"


def test_refund_closes_open_case(svc, world):
    mandate, offer = start_order(world)
    world["buyer"].check_order("ord_1", mandate, offer, 1, "36.90", "EUR")
    world["psp"].record("ord_1", "CHARGE", {"amount": "36.90", "currency": "EUR", "psp_ref": "ch_1"})
    case_id = world["buyer"].open_dispute("ord_1", "not_received")["case_id"]
    world["psp"].record("ord_1", "REFUND", {"amount": "36.90", "currency": "EUR", "psp_ref": "re_1", "reason": "goodwill"})
    assert svc.ledger.get_case(case_id)["status"] == "closed"
    assert svc.get_trail("ord_1", world["buyer"].party_id)["cases"][0]["status"] == "closed"


def test_order_must_reference_mandate_and_offer(svc, world):
    """No mandate, no purchase: the ledger refuses an ORDER that does not point at a MANDATE + OFFER."""
    mandate, offer = start_order(world)
    mc, oc = crypto.commit(mandate), crypto.commit(offer)
    payload = {"mandate_commit": mc, "offer_commit": oc, "qty": 1, "total": "36.90", "currency": "EUR"}
    assert world["buyer"].record("ord_1", "ORDER", payload, refs=[])["error"] == "ORDER_NEEDS_MANDATE"
    assert world["buyer"].record("ord_1", "ORDER", payload, refs=[oc])["error"] == "ORDER_NEEDS_MANDATE"
    # a MANDATE committed in a *different* order does not count
    world["user"].record("ord_other", "MANDATE", dict(mandate, nonce="other"))
    other_mc = crypto.commit(dict(mandate, nonce="other"))
    assert world["buyer"].record("ord_1", "ORDER", payload, refs=[other_mc, oc])["error"] == "ORDER_NEEDS_MANDATE"
    # with both refs from this order it is accepted (the scope check is still skipped: that is the LLM-buyer metric)
    assert "event_id" in world["buyer"].record("ord_1", "ORDER", payload, refs=[mc, oc])
    # and an agent whose mandate was never committed cannot order through check_order either
    ghost = Party(svc, "buyer_agent", "ghost")
    res = ghost.check_order("ord_1", dict(mandate, nonce="never-signed"), offer, 1, "36.90", "EUR")
    assert res["error"] == "UNKNOWN_REF"
