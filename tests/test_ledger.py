from decimal import Decimal

import pytest

from trailproof import crypto
from trailproof.ledger import Ledger, LedgerError


@pytest.fixture
def ledger():
    return Ledger(":memory:")


@pytest.fixture
def parties(ledger):
    """Register one party per role; returns {role: (party_id, keypair)}."""
    out = {}
    for role in ("user", "buyer_agent", "merchant", "psp"):
        kp = crypto.Keypair.generate()
        pid = ledger.register_party(role, kp.public_hex, f"{role}-operator")
        out[role] = (pid, kp)
    return out


def write(ledger, parties, order_id, type_, role, payload, refs=(), prev="auto"):
    pid, kp = parties[role]
    c = crypto.commit(payload)
    head = ledger.head(order_id)
    actual_prev = head.commit if head else None
    sign_prev = actual_prev if prev == "auto" else prev
    sig = kp.sign(crypto.event_signing_message(c, sign_prev, list(refs)))
    return ledger.record_event(order_id, type_, pid, c, list(refs), sig)


def test_register_party_validates(ledger):
    kp = crypto.Keypair.generate()
    pid = ledger.register_party("merchant", kp.public_hex, "shop")
    assert pid.startswith("pty_")
    assert ledger.party(pid).role == "merchant"
    with pytest.raises(LedgerError) as e:
        ledger.register_party("ledger", kp.public_hex, "x")
    assert e.value.code == "BAD_ROLE"
    with pytest.raises(LedgerError) as e:
        ledger.register_party("merchant", "nothex", "x")
    assert e.value.code == "BAD_KEY"


def test_chain_prev_links_and_receipts(ledger, parties):
    e1 = write(ledger, parties, "ord_1", "MANDATE", "user", {"n": 1})
    e2 = write(ledger, parties, "ord_1", "OFFER", "merchant", {"n": 2})
    e3 = write(ledger, parties, "ord_1", "ORDER", "buyer_agent", {"n": 3}, refs=[e1.commit, e2.commit])
    assert e1.prev is None
    assert e2.prev == e1.commit
    assert e3.prev == e2.commit
    assert e3.refs == [e1.commit, e2.commit]
    trail = ledger.get_trail("ord_1")
    assert [e.type for e in trail] == ["MANDATE", "OFFER", "ORDER"]
    for e in trail:
        assert ledger.verify_receipt(e)
        assert ledger.verify_event_sig(e)
        assert e.ledger_ts.endswith("Z")
    assert ledger.verify_chain("ord_1") == []
    # a different order starts its own chain
    f1 = write(ledger, parties, "ord_2", "MANDATE", "user", {"n": 1})
    assert f1.prev is None


def test_wrong_prev_rejected(ledger, parties):
    write(ledger, parties, "ord_1", "MANDATE", "user", {"n": 1})
    # signer believes prev is null (stale view) -> signature covers the wrong prev
    with pytest.raises(LedgerError) as e:
        write(ledger, parties, "ord_1", "OFFER", "merchant", {"n": 2}, prev=None)
    assert e.value.code == "BAD_SIG"
    # explicitly stating a wrong prev is caught before the signature check
    pid, kp = parties["merchant"]
    c = crypto.commit({"n": 2})
    sig = kp.sign(crypto.event_signing_message(c, "ff" * 32, []))
    with pytest.raises(LedgerError) as e:
        ledger.record_event("ord_1", "OFFER", pid, c, [], sig, prev="ff" * 32)
    assert e.value.code == "BAD_PREV"
    assert len(ledger.get_trail("ord_1")) == 1


def test_wrong_party_role_rejected(ledger, parties):
    with pytest.raises(LedgerError) as e:
        write(ledger, parties, "ord_1", "OFFER", "buyer_agent", {"n": 1})
    assert e.value.code == "WRONG_ROLE"
    with pytest.raises(LedgerError) as e:
        write(ledger, parties, "ord_1", "CHARGE", "merchant", {"n": 1})
    assert e.value.code == "WRONG_ROLE"


def test_bad_or_missing_signature_rejected(ledger, parties):
    pid, kp = parties["user"]
    c = crypto.commit({"n": 1})
    other = crypto.Keypair.generate()
    with pytest.raises(LedgerError) as e:
        ledger.record_event("ord_1", "MANDATE", pid, c, [], other.sign(crypto.event_signing_message(c, None, [])))
    assert e.value.code == "BAD_SIG"
    with pytest.raises(LedgerError) as e:
        ledger.record_event("ord_1", "MANDATE", pid, c, [], "")
    assert e.value.code == "MISSING_SIG"


def test_revoked_key_rejected(ledger, parties):
    write(ledger, parties, "ord_1", "MANDATE", "user", {"n": 1})
    ledger.revoke_party(parties["merchant"][0])
    with pytest.raises(LedgerError) as e:
        write(ledger, parties, "ord_1", "OFFER", "merchant", {"n": 2})
    assert e.value.code == "REVOKED_KEY"


def test_unknown_ref_and_bad_commit_rejected(ledger, parties):
    with pytest.raises(LedgerError) as e:
        write(ledger, parties, "ord_1", "MANDATE", "user", {"n": 1}, refs=["aa" * 32])
    assert e.value.code == "UNKNOWN_REF"
    pid, kp = parties["user"]
    with pytest.raises(LedgerError) as e:
        ledger.record_event("ord_1", "MANDATE", pid, "nothash", [], kp.sign("x"))
    assert e.value.code == "BAD_COMMIT"
    with pytest.raises(LedgerError) as e:
        ledger.record_event("ord_1", "BOGUS", pid, "ab" * 32, [], kp.sign("x"))
    assert e.value.code == "BAD_TYPE"


def test_mandate_spend_accumulates(ledger):
    assert ledger.spent_under("u1", "n1") == Decimal("0.00")
    ledger.add_spend("u1", "n1", Decimal("30.00"))
    ledger.add_spend("u1", "n1", Decimal("5.50"))
    assert ledger.spent_under("u1", "n1") == Decimal("35.50")
    assert ledger.spent_under("u1", "n2") == Decimal("0.00")


def test_cases_and_reveals(ledger, parties):
    ev = write(ledger, parties, "ord_1", "MANDATE", "user", {"n": 1})
    case_id = ledger.open_case("ord_1", "not_received", "", parties["buyer_agent"][0], ev.event_id)
    assert ledger.get_case(case_id)["status"] == "open"
    ledger.record_reveal(case_id, ev.event_id, parties["user"][0], {"n": 1}, True)
    ledger.record_reveal(case_id, "evt_x", parties["merchant"][0], {"n": 9}, False)
    r = ledger.reveals_for_case(case_id)
    assert r[ev.event_id]["verified"] and r[ev.event_id]["payload"] == {"n": 1}
    assert r["evt_x"]["verified"] is False and r["evt_x"]["payload"] is None
    ledger.close_case(case_id, "refunded")
    assert ledger.get_case(case_id)["status"] == "closed"


def test_persisted_db_reopens(tmp_path):
    path = str(tmp_path / "ledger.db")
    l1 = Ledger(path)
    kp = crypto.Keypair.generate()
    pid = l1.register_party("psp", kp.public_hex, "psp")
    l1.close()
    l2 = Ledger(path)
    assert l2.party(pid).role == "psp"
