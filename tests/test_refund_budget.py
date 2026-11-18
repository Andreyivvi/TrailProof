"""Regression tests for what the HW9 cold-start runs exposed.

Run ``wrong1`` (usability/runs/wrong1): after a wrong delivery was refunded,
the mandate still counted the money as spent, so the agent could not reorder
and the user was left without the goods. A refund now returns the order's
amount to the mandate, on the ledger and in the wallet.
"""

from __future__ import annotations

import asyncio

import pytest

from demo_shop.__main__ import Shop
from mocks.transport import DirectToolClient
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService
from wallet import Wallet

SKU = "DOGFOOD-10KG"


def run(coro):
    return asyncio.run(coro)


async def world(tmp_path, budget="40"):
    client = DirectToolClient(TrailProofService(Ledger()))
    w = Wallet.create(tmp_path / "wallet.json", "mia", "direct://", client)
    await w.register()
    shop = Shop(client, tmp_path / "shop.json")
    await shop.ensure()
    return client, w, shop, w.add_mandate(["pet_food"], budget)["mandate_id"]


async def buy(w, shop, mandate_id, ship_wrong=False):
    so = await w.start_order(mandate_id)
    offer = await shop.shop.recognise_and_offer(so["order_id"], SKU, None, buyer_party_id=w.agent_id)
    placed = await w.place_order(so["order_id"], offer["payload"], offer["commit"])
    if placed["ordered"]:
        await shop.shop.take_payment(so["order_id"], shop.psp)
        ful = (await shop.shop.fulfil(so["order_id"], **({"sku": "DOGFOOD-5KG", "unit": "bag_5kg"} if ship_wrong else {})))["payload"]
        await w.confirm_delivery(so["order_id"], True, ful["sku"], ful["qty"], ful["unit"], "")
    return so["order_id"], placed


def test_a_refund_gives_the_budget_back_and_the_user_can_reorder(tmp_path):
    """The exact situation of run wrong1: 40.00 budget, a 36.90 order goes wrong, is refunded, is reordered."""

    async def go():
        _, w, shop, m = await world(tmp_path)
        order_id, placed = await buy(w, shop, m, ship_wrong=True)
        blocked = await buy(w, shop, m)                       # before any refund: 3.10 left, a second bag does not fit
        opened = await w.open_dispute(order_id, "not_as_described", "5 kg arrived")
        answer = await shop.respond(opened["case_id"], order_id)
        case = await w.case_status(opened["case_id"])
        reorder_id, reordered = await buy(w, shop, m)         # after the refund: fits again
        return placed, blocked[1], answer, case, reordered, w.mandates_by_id(m), await w.order_trail(reorder_id)

    placed, blocked, answer, case, reordered, mandate, trail = run(go())
    assert placed["ordered"] and placed["remaining_under_mandate"] == "3.10"
    assert blocked["ordered"] is False and blocked["reasons"][0]["code"] == "OVER_BUDGET"
    assert answer["action"] == "refunded" and answer["amount"] == "36.90"
    assert case["status"] == "closed" and case["refunded_to_budget"] is True and case["remaining_under_mandate"] == "40.00"
    assert reordered["ordered"] is True, reordered               # this is what failed for the agent in run wrong1
    assert mandate["spent"] == "36.90" and mandate["remaining"] == "3.10"
    assert [e["type"] for e in trail["events"]] == ["MANDATE", "OFFER", "ORDER", "CHARGE", "FULFILMENT", "DELIVERY"]


def test_the_ledger_credits_a_refund_once_and_only_for_that_order(tmp_path):
    async def go():
        client, w, shop, m = await world(tmp_path, budget="80")
        first, _ = await buy(w, shop, m, ship_wrong=True)
        second, _ = await buy(w, shop, m)                     # 73.80 spent
        ledger = client.service.ledger
        key = (w.user_id, m)
        before = ledger.spent_under(*key)
        opened = await w.open_dispute(first, "not_as_described", "")
        await shop.respond(opened["case_id"], first)
        after = ledger.spent_under(*key)
        again = ledger.credit_refund(first)                    # a second credit for the same order does nothing
        untouched = ledger.credit_refund("ord_unknown")
        return str(before), str(after), again, untouched, str(ledger.spent_under(*key))

    before, after, again, untouched, final = run(go())
    assert (before, after, final) == ("73.80", "36.90", "36.90")
    assert again is None and untouched is None


def test_a_decided_case_has_no_unverifiable_finding(tmp_path):
    """Run wrong1 reported 'the payment provider never shared its record'. The shop's side now reveals the charge."""

    async def go():
        _, w, shop, m = await world(tmp_path)
        order_id, _ = await buy(w, shop, m, ship_wrong=True)
        opened = await w.open_dispute(order_id, "not_as_described", "")
        answer = await shop.respond(opened["case_id"], order_id)
        return answer, await w.case_status(opened["case_id"])

    answer, case = run(go())
    codes = [f["code"] for f in case["findings"]]
    assert "UNVERIFIABLE" not in codes and "DELIVERED_NE_OFFER" in codes
    assert answer["findings_against_me"] == ["DELIVERED_NE_OFFER"]


def test_an_order_that_was_not_refunded_still_counts(tmp_path):
    async def go():
        _, w, shop, m = await world(tmp_path)
        await buy(w, shop, m)
        return await w.sync_refunds(), w.mandates_by_id(m), (await buy(w, shop, m))[1]

    newly, mandate, second = run(go())
    assert newly == [] and mandate["remaining"] == "3.10"
    assert second["ordered"] is False


@pytest.mark.parametrize("env,expect", [({"PAWSHOP_WRONG_ITEM_ONCE": "1"}, ["DOGFOOD-5KG", "DOGFOOD-10KG"]),
                                         ({}, ["DOGFOOD-10KG", "DOGFOOD-10KG"])])
def test_practice_shop_says_delivery_is_simulated_and_can_slip_once(tmp_path, monkeypatch, env, expect):
    from demo_shop.__main__ import build_server

    for k, v in env.items():
        monkeypatch.setenv(k, v)

    async def go():
        _, w, shop, m = await world(tmp_path, budget="100")
        server = build_server(shop)
        out = []
        for _ in range(2):
            so = await w.start_order(m)
            offer = await shop.shop.recognise_and_offer(so["order_id"], SKU, None, buyer_party_id=w.agent_id)
            await w.place_order(so["order_id"], offer["payload"], offer["commit"])
            res = await server.call_tool("pay_and_ship", {"order_id": so["order_id"]})
            out.append(res.structured_content if hasattr(res, "structured_content") else res)
        return out

    shipped = run(go())
    assert [r["parcel"]["sku"] for r in shipped] == expect
    assert all("simulated" in r["arrival"] for r in shipped)
