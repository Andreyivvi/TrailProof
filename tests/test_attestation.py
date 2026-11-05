"""Brand-direct customer recognition: ``attest_history``, ``verify_attestation`` and scenario 7."""

import asyncio

import pytest

from mocks import scenarios as S
from mocks.shop import MockShop
from mocks.transport import DirectToolClient
from trailproof import crypto
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService

from .helpers import NOW, Party, mandate_payload, offer_payload

ATTESTATION_KEYS = {"subject", "merchant", "audience", "stats", "issued_at", "ledger_sig"}
STATS_KEYS = {"orders_completed", "cases_lost", "first_order_ts", "last_order_ts"}


@pytest.fixture
def svc():
    return TrailProofService(Ledger(":memory:", clock=lambda: NOW))


@pytest.fixture
def world(svc):
    return {
        "user": Party(svc, "user", "mia"),
        "buyer": Party(svc, "buyer_agent", "petbot"),
        "shop": Party(svc, "merchant", "pawshop"),
        "other_shop": Party(svc, "merchant", "otherstore"),
        "psp": Party(svc, "psp", "mockpay"),
    }


def complete_order(world, order_id, nonce, shop="shop", charge=True, refund=False):
    """MANDATE, OFFER, ORDER (via check_order), then optionally CHARGE and REFUND."""
    mandate = mandate_payload(nonce=nonce)
    offer = offer_payload(nonce=f"offer-{order_id}")
    assert "event_id" in world["user"].record(order_id, "MANDATE", mandate)
    assert "event_id" in world[shop].record(order_id, "OFFER", offer)
    res = world["buyer"].check_order(order_id, mandate, offer, 1, "36.90", "EUR")
    assert res["in_scope"] is True, res
    if charge:
        assert "event_id" in world["psp"].record(order_id, "CHARGE", {"amount": "36.90", "currency": "EUR", "psp_ref": f"ch_{order_id}"})
    if refund:
        assert "event_id" in world["psp"].record(order_id, "REFUND", {"amount": "36.90", "currency": "EUR", "psp_ref": f"re_{order_id}", "reason": "x"})
    return mandate, offer


def attest(svc, world, subject="buyer", merchant="shop", audience="shop"):
    return svc.attest_history(world[subject].party_id, world[merchant].party_id, world[audience].party_id)


# ------------------------------------------------------------------ stats


def test_two_completed_orders_count_and_nothing_else_is_disclosed(svc, world):
    complete_order(world, "ord_1", "n1")
    complete_order(world, "ord_2", "n2")
    complete_order(world, "ord_3", "n3", charge=False)  # never charged: not completed
    complete_order(world, "ord_4", "n4", shop="other_shop")  # with another merchant

    att = attest(svc, world)
    assert set(att) == ATTESTATION_KEYS
    assert set(att["stats"]) == STATS_KEYS
    assert att["subject"] == world["buyer"].party_id
    assert att["merchant"] == world["shop"].party_id
    assert att["audience"] == world["shop"].party_id
    assert att["stats"]["orders_completed"] == 2
    assert att["stats"]["cases_lost"] == 0
    assert att["stats"]["first_order_ts"] == att["stats"]["last_order_ts"] == crypto.iso_z(NOW)
    assert att["issued_at"] == crypto.iso_z(NOW)
    flat = crypto.canonical_json(att)
    assert "ord_" not in flat and "36.90" not in flat and "DOGFOOD" not in flat

    # the user key sees the same history (it signed every mandate)
    assert attest(svc, world, subject="user")["stats"]["orders_completed"] == 2
    # the other merchant's view of the same buyer
    assert attest(svc, world, merchant="other_shop", audience="other_shop")["stats"]["orders_completed"] == 1


def test_refunded_only_history_counts_zero(svc, world):
    complete_order(world, "ord_1", "n1", refund=True)
    complete_order(world, "ord_2", "n2", refund=True)
    att = attest(svc, world)
    assert att["stats"] == {"orders_completed": 0, "cases_lost": 0, "first_order_ts": None, "last_order_ts": None}
    assert crypto.verify_attestation(att, svc.ledger.keypair.public_hex)


def test_no_history_at_all_is_a_signed_zero(svc, world):
    att = attest(svc, world)
    assert att["stats"]["orders_completed"] == 0 and att["stats"]["first_order_ts"] is None
    assert crypto.verify_attestation(att, svc.ledger.keypair.public_hex)


def test_lost_case_is_counted(svc, world):
    complete_order(world, "ord_1", "n1")
    # friendly fraud: everything clean, buyer claims not_authorised -> buyer_at_fault
    assert "case_id" in world["buyer"].open_dispute("ord_1", "not_authorised", "never approved")
    case_id = svc.ledger.cases_for_order("ord_1")[0]["case_id"]
    for p in ("user", "buyer", "shop", "psp"):
        world[p].reveal_all(case_id)
    assert svc.get_case_bundle(case_id, world["buyer"].party_id)["verdict"] == "buyer_at_fault"

    att = attest(svc, world)
    assert att["stats"] == {
        "orders_completed": 1,  # charged, never refunded
        "cases_lost": 1,
        "first_order_ts": crypto.iso_z(NOW),
        "last_order_ts": crypto.iso_z(NOW),
    }


def test_case_won_by_buyer_is_not_lost(svc, world):
    mandate, offer = complete_order(world, "ord_1", "n1", charge=False)
    assert "event_id" in world["psp"].record("ord_1", "CHARGE", {"amount": "42.90", "currency": "EUR", "psp_ref": "ch_x"})
    assert "case_id" in world["buyer"].open_dispute("ord_1", "not_as_described", "overcharged")
    case_id = svc.ledger.cases_for_order("ord_1")[0]["case_id"]
    for p in ("user", "buyer", "shop", "psp"):
        world[p].reveal_all(case_id)
    assert svc.get_case_bundle(case_id, world["buyer"].party_id)["verdict"] == "merchant_at_fault"
    assert attest(svc, world)["stats"]["cases_lost"] == 0


# -------------------------------------------------------------- rejections


def test_rejections(svc, world):
    assert attest(svc, world, subject="shop")["error"] == "WRONG_ROLE"
    assert attest(svc, world, subject="psp")["error"] == "WRONG_ROLE"
    assert svc.attest_history(world["buyer"].party_id, "pty_nobody", world["shop"].party_id)["error"] == "UNKNOWN_PARTY"
    assert svc.attest_history("pty_nobody", world["shop"].party_id, world["shop"].party_id)["error"] == "UNKNOWN_PARTY"
    assert svc.attest_history(world["buyer"].party_id, world["shop"].party_id, "pty_nobody")["error"] == "UNKNOWN_PARTY"
    # the "merchant" must actually be a merchant
    assert attest(svc, world, merchant="psp")["error"] == "WRONG_ROLE"
    svc.ledger.revoke_party(world["buyer"].party_id)
    assert attest(svc, world)["error"] == "REVOKED_KEY"


# -------------------------------------------------------------- signature


def test_signature_verifies_and_tampering_fails(svc, world):
    complete_order(world, "ord_1", "n1")
    complete_order(world, "ord_2", "n2")
    att = attest(svc, world)
    pk = svc.ledger.keypair.public_hex

    assert crypto.verify_attestation(att, pk)
    # the signature is exactly ed25519 over canonical_json(attestation without ledger_sig)
    body = {k: v for k, v in att.items() if k != "ledger_sig"}
    assert crypto.verify(pk, crypto.canonical_json(body), att["ledger_sig"])

    assert not crypto.verify_attestation(dict(att, stats=dict(att["stats"], orders_completed=20)), pk)
    assert not crypto.verify_attestation(dict(att, stats=dict(att["stats"], cases_lost=0, orders_completed=2), issued_at="2030-01-01T00:00:00.000Z"), pk)
    assert not crypto.verify_attestation(dict(att, audience=world["other_shop"].party_id), pk)
    assert not crypto.verify_attestation(dict(att, subject=world["user"].party_id), pk)
    assert not crypto.verify_attestation(dict(att, ledger_sig="00" * 64), pk)
    assert not crypto.verify_attestation(dict(att, ledger_sig=""), pk)
    assert not crypto.verify_attestation({k: v for k, v in att.items() if k != "ledger_sig"}, pk)
    assert not crypto.verify_attestation(None, pk)
    assert not crypto.verify_attestation("not a dict", pk)
    # a different ledger's key does not verify it
    assert not crypto.verify_attestation(att, crypto.Keypair.generate().public_hex)
    # self-signed by an impostor "ledger": fails with the real key
    forged = crypto.sign_attestation(crypto.Keypair.generate().private_key, dict(att, stats=dict(att["stats"], orders_completed=99)))
    assert not crypto.verify_attestation(forged, pk)


# ---------------------------------------------------------- shop policy


def test_shop_recognition_policy_is_checked_in_order(svc, world):
    """The mock shop's checks, exercised directly (no transport)."""
    complete_order(world, "ord_1", "n1")
    complete_order(world, "ord_2", "n2")
    pk = svc.ledger.keypair.public_hex
    shop = MockShop(None)
    shop.party_id = world["shop"].party_id
    shop.ledger_public_key = pk
    buyer = world["buyer"].party_id

    good = attest(svc, world)
    assert shop.check_recognition(good, buyer) == (True, "2 completed order(s), 0 lost case(s): 10% off")
    assert shop.check_recognition(None, buyer)[0] is False
    assert "signature" in shop.check_recognition(dict(good, subject=buyer, issued_at="x"), buyer)[1]
    assert "audience" in shop.check_recognition(attest(svc, world, audience="other_shop"), buyer)[1]
    assert "another merchant" in shop.check_recognition(attest(svc, world, merchant="other_shop", audience="shop"), buyer)[1]
    assert "not this buyer" in shop.check_recognition(good, world["user"].party_id)[1]
    # too few orders: sign a genuine attestation for a buyer with one order
    complete_order(world, "ord_x", "nx", shop="other_shop")
    one = attest(svc, world, merchant="other_shop", audience="other_shop")
    shop.party_id = world["other_shop"].party_id
    assert one["stats"]["orders_completed"] == 1
    assert "policy needs 2" in shop.check_recognition(one, buyer)[1]


# ------------------------------------------------------------- scenario 7


def test_scenario_7_over_direct_client():
    async def go():
        client = DirectToolClient(TrailProofService(Ledger(":memory:")))
        world = await S.World.create(client)
        return await S.scenario_recognised(world), client

    result, client = asyncio.run(go())
    failed = [name for name, ok in result["checks"].items() if not ok]
    assert not failed, failed
    assert client.calls.count("attest_history") == 1
    att = result["attestation"]
    assert crypto.verify_attestation(att, client.service.ledger.keypair.public_hex)
    # the discounted offer is what the ORDER refs, so the charge mismatch is provable
    offer_commit = result["trail"]["events"][1]["commit"]
    assert result["trail"]["events"][2]["refs"][1] == offer_commit
    assert result["bundle"]["remedy"] == {"type": "refund", "amount": "36.90", "currency": "EUR"}
    # the recognised OFFER payload cites the attestation by hash, and the shop kept the reason
    assert result["recognitions"]["ord_leo_3"] == {"recognised": True, "reason": "2 completed order(s), 0 lost case(s): 10% off"}
    assert result["recognitions"]["ord_stranger_1"]["reason"] == "no attestation presented"
