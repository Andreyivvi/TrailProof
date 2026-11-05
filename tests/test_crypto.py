from decimal import Decimal

import pytest

from trailproof import crypto


def test_canonical_json_is_stable_under_key_order_and_whitespace():
    a = {"b": 1, "a": {"y": [1, 2], "x": "é"}}
    b = {"a": {"x": "é", "y": [1, 2]}, "b": 1}
    assert crypto.canonical_json(a) == crypto.canonical_json(b)
    assert crypto.canonical_json(a) == '{"a":{"x":"é","y":[1,2]},"b":1}'
    assert crypto.commit(a) == crypto.commit(b)


def test_canonical_json_rejects_decimal():
    with pytest.raises(TypeError):
        crypto.canonical_json({"total": Decimal("1.00")})


def test_amount_str_two_dp():
    assert crypto.amount_str(Decimal("36.9")) == "36.90"
    assert crypto.amount_str("42.905") == "42.91"
    assert crypto.amount_str(5) == "5.00"
    assert crypto.parse_amount("36.90") == Decimal("36.90")
    with pytest.raises(ValueError):
        crypto.parse_amount("abc")


def test_iso_z_roundtrip():
    now = crypto.utc_now()
    s = crypto.iso_z(now)
    assert s.endswith("Z") and "T" in s
    back = crypto.parse_iso_z(s)
    assert abs((back - now).total_seconds()) < 0.001


def test_sign_verify_roundtrip():
    kp = crypto.Keypair.generate()
    msg = crypto.event_signing_message("ab" * 32, None, ["cd" * 32])
    sig = kp.sign(msg)
    assert crypto.verify(kp.public_hex, msg, sig)
    other = crypto.Keypair.generate()
    assert not crypto.verify(other.public_hex, msg, sig)
    assert not crypto.verify(kp.public_hex, msg + "x", sig)
    assert not crypto.verify(kp.public_hex, msg, "zz")  # garbage sig does not raise


def test_tampered_payload_changes_commit():
    payload = {"sku": "DOG10", "total": "36.90"}
    c = crypto.commit(payload)
    tampered = dict(payload, total="42.90")
    assert crypto.commit(tampered) != c
    assert crypto.sha256_hex(crypto.canonical_json(payload)) == c


def test_signing_messages_are_distinct_per_prev_and_refs():
    c = "ab" * 32
    assert crypto.event_signing_message(c, None, []) == f"{c}||"
    assert crypto.event_signing_message(c, "ef" * 32, ["01" * 32, "02" * 32]) == (
        f"{c}|{'ef' * 32}|{'01' * 32},{'02' * 32}"
    )
    assert crypto.receipt_signing_message("evt_1", c, "2026-01-01T00:00:00.000Z") == (
        f"evt_1|{c}|2026-01-01T00:00:00.000Z"
    )
