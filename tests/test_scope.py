from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trailproof import crypto
from trailproof.rules import scope_check

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
MERCHANT = "pty_shop"


def mandate(**over):
    m = {
        "user_id": "mia",
        "categories": ["pet_food"],
        "max_amount": "40.00",
        "currency": "EUR",
        "merchants": "*",
        "valid_until": crypto.iso_z(NOW + timedelta(days=30)),
        "nonce": "n1",
    }
    m.update(over)
    return m


def offer(**over):
    o = {
        "sku": "DOG10",
        "title": "Dog food 10 kg",
        "category": "pet_food",
        "qty": 1,
        "unit": "bag_10kg",
        "unit_price": "36.90",
        "total": "36.90",
        "currency": "EUR",
        "shipping_terms": "2 days",
        "return_terms": "14 days",
        "valid_until": crypto.iso_z(NOW + timedelta(hours=1)),
        "nonce": "o1",
    }
    o.update(over)
    return o


def run(m=None, o=None, qty=1, total="36.90", currency="EUR", spent="0.00", now=NOW):
    return scope_check(m or mandate(), o or offer(), MERCHANT, qty, total, currency, Decimal(spent), now)


def codes(reasons):
    return [r["code"] for r in reasons]


def test_happy_path_in_scope():
    ok, reasons = run()
    assert ok and reasons == []


def test_mandate_expired():
    ok, reasons = run(m=mandate(valid_until=crypto.iso_z(NOW - timedelta(seconds=1))))
    assert not ok and codes(reasons) == ["MANDATE_EXPIRED"]


def test_merchant_not_allowed_and_allowed_by_list():
    ok, reasons = run(m=mandate(merchants=["pty_other"]))
    assert codes(reasons) == ["MERCHANT_NOT_ALLOWED"]
    ok, reasons = run(m=mandate(merchants=["pty_other", MERCHANT]))
    assert ok
    ok, reasons = run(m=mandate(merchants=["*"]))
    assert ok


def test_offer_expired():
    ok, reasons = run(o=offer(valid_until=crypto.iso_z(NOW - timedelta(minutes=1))))
    assert codes(reasons) == ["OFFER_EXPIRED"]


def test_category_not_allowed():
    ok, reasons = run(o=offer(category="cat_toys"))
    assert codes(reasons) == ["CATEGORY_NOT_ALLOWED"]


def test_over_budget_including_prior_spend():
    ok, reasons = run(o=offer(total="30.00"), total="30.00")
    assert ok
    ok, reasons = run(o=offer(total="30.00"), total="30.00", spent="30.00")
    assert codes(reasons) == ["OVER_BUDGET"]
    assert "remaining budget 10.00" in reasons[0]["detail"]
    ok, reasons = run(o=offer(total="10.00"), total="10.00", spent="30.00")  # exactly the remainder
    assert ok


def test_currency_mismatch():
    ok, reasons = run(o=offer(currency="USD"), currency="USD")
    assert codes(reasons) == ["CURRENCY_MISMATCH"]


def test_order_must_equal_offer():
    ok, reasons = run(total="20.00")
    assert codes(reasons) == ["ORDER_NE_OFFER"]
    ok, reasons = run(qty=2)
    assert codes(reasons) == ["ORDER_NE_OFFER"]


def test_all_failing_reasons_are_returned():
    ok, reasons = run(o=offer(category="cat_toys", total="55.00"), total="55.00")
    assert not ok
    assert codes(reasons) == ["CATEGORY_NOT_ALLOWED", "OVER_BUDGET"]


def test_malformed_mandate_does_not_raise():
    ok, reasons = run(m=mandate(valid_until="yesterday", max_amount="lots"))
    assert not ok
    assert {"MANDATE_MALFORMED", "AMOUNT_MALFORMED"} <= set(codes(reasons))
