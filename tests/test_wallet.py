"""The customer wallet: keys on disk, mandates only from the human side, proofs, orders, disputes."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from demo_shop.__main__ import Shop
from mocks.transport import DirectToolClient
from trailproof import crypto
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService
from wallet import Wallet, WalletError
from wallet.client import HttpToolClient

ROOT = Path(__file__).resolve().parent.parent
SKU = "DOGFOOD-10KG"


def run(coro):
    return asyncio.run(coro)


async def world(tmp_path):
    client = DirectToolClient(TrailProofService(Ledger()))
    w = Wallet.create(tmp_path / "wallet.json", "mia", "direct://", client)
    await w.register()
    shop = Shop(client, tmp_path / "shop.json")
    await shop.ensure()
    return client, w, shop


async def buy(w, shop, mandate_id, sku=SKU, proof=None, ship_wrong=False):
    so = await w.start_order(mandate_id)
    offer = await shop.shop.recognise_and_offer(so["order_id"], sku, proof, buyer_party_id=w.agent_id)
    placed = await w.place_order(so["order_id"], offer["payload"], offer["commit"])
    if placed["ordered"]:
        await shop.shop.take_payment(so["order_id"], shop.psp)
        await shop.shop.fulfil(so["order_id"], **({"sku": "DOGFOOD-5KG", "unit": "bag_5kg"} if ship_wrong else {}))
    return so["order_id"], offer, placed


def test_no_mandate_no_order_and_the_agent_tools_cannot_create_one(tmp_path):
    async def go():
        _, w, _ = await world(tmp_path)
        with pytest.raises(WalletError) as e:
            await w.start_order("mnd_missing")
        return e.value.code

    assert run(go()) == "UNKNOWN_MANDATE"
    from wallet.server import build_server

    names = {t.name for t in run(build_server(str(tmp_path / "wallet.json")).list_tools())}
    assert names == {"wallet_status", "list_mandates", "prove_history", "start_order", "place_order", "confirm_delivery",
                     "open_dispute", "case_status", "order_trail"}
    assert not any("mandate" in n and n != "list_mandates" for n in names)


def test_buy_twice_then_be_recognised_and_stay_inside_the_budget(tmp_path):
    async def go():
        _, w, shop = await world(tmp_path)
        m = w.add_mandate(["pet_food"], "80")["mandate_id"]
        _, o1, p1 = await buy(w, shop, m)
        _, o2, p2 = await buy(w, shop, m)
        proof = await w.prove_history(shop.shop.party_id, shop.shop.party_id)
        # the third order is recognised (10% off) but no longer fits the mandate: 80 - 2 x 36.90 = 6.20
        _, o3, p3 = await buy(w, shop, m, proof=proof)
        return w, o1, p1, p2, proof, o3, p3, m

    w, o1, p1, p2, proof, o3, p3, m = run(go())
    assert o1["payload"]["total"] == "36.90" and not o1["recognised"]
    assert p1["ordered"] and p2["ordered"] and p2["remaining_under_mandate"] == "6.20"
    assert proof["stats"]["orders_completed"] == 2 and proof["subject"] == w.agent_id
    assert o3["recognised"] and o3["payload"]["total"] == "33.21"
    assert p3["ordered"] is False and [r["code"] for r in p3["reasons"]] == ["OVER_BUDGET"]
    assert w.mandates_by_id(m)["spent"] == "73.80"


def test_wallet_survives_a_restart_and_keeps_its_identity(tmp_path):
    async def go():
        client, w, shop = await world(tmp_path)
        m = w.add_mandate(["pet_food"], "200")["mandate_id"]
        await buy(w, shop, m)
        await buy(w, shop, m)
        again = Wallet.load(tmp_path / "wallet.json", client)       # a new process would do exactly this
        proof = await again.prove_history(shop.shop.party_id, shop.shop.party_id)
        _, o3, p3 = await buy(again, shop, m, proof=proof)
        return w, again, proof, o3, p3, shop

    w, again, proof, o3, p3, shop = run(go())
    assert again.agent_id == w.agent_id and again.user.public_hex == w.user.public_hex
    assert crypto.verify_attestation(proof, shop.shop.ledger_public_key)
    assert o3["recognised"] and p3["ordered"] and p3["total"] == "33.21"
    saved = json.loads((tmp_path / "wallet.json").read_text(encoding="utf-8"))
    assert len(saved["orders"]) == 3 and all(o["events"] for o in saved["orders"].values())  # plaintext stays in the wallet


def test_wrong_parcel_is_disputed_and_refunded(tmp_path):
    async def go():
        _, w, shop = await world(tmp_path)
        m = w.add_mandate(["pet_food"], "40")["mandate_id"]
        order_id, offer, placed = await buy(w, shop, m, ship_wrong=True)
        delivery = await w.confirm_delivery(order_id, True, "DOGFOOD-5KG", 1, "bag_5kg", "5 kg bag arrived")
        opened = await w.open_dispute(order_id, "not_as_described", "ordered 10 kg, got 5 kg")
        decision = await shop.shop.handle_dispute({"case_id": opened["case_id"], "order_id": order_id}, shop.psp)
        return delivery, opened, decision, await w.case_status(opened["case_id"]), await w.order_trail(order_id)

    delivery, opened, decision, case, trail = run(go())
    assert delivery["as_offered"] is False
    assert opened["revealed"] == {"MANDATE": True, "ORDER": True, "DELIVERY": True}
    assert decision["action"] == "refunded" and decision["amount"] == "36.90"
    assert case["status"] == "closed" and case["verdict"] == "merchant_at_fault"
    assert "DELIVERED_NE_OFFER" in [f["code"] for f in case["findings"]]
    assert [e["type"] for e in trail["events"]][-2:] == ["DISPUTE", "REFUND"]


def test_revoked_mandate_and_doctored_offer_are_refused(tmp_path):
    async def go():
        _, w, shop = await world(tmp_path)
        m = w.add_mandate(["pet_food"], "40")["mandate_id"]
        so = await w.start_order(m)
        offer = await shop.shop.recognise_and_offer(so["order_id"], SKU, None, buyer_party_id=w.agent_id)
        cheaper = dict(offer["payload"], total="1.00", unit_price="1.00")   # not what the shop committed
        with pytest.raises(WalletError) as mismatch:
            await w.place_order(so["order_id"], cheaper, offer["commit"])
        w.revoke_mandate(m)
        with pytest.raises(WalletError) as revoked:
            await w.start_order(m)
        with pytest.raises(WalletError) as claim:
            await w.open_dispute(so["order_id"], "i_changed_my_mind")
        return mismatch.value.code, revoked.value.code, claim.value.code, w.mandates()

    mismatch, revoked, claim, mandates = run(go())
    assert (mismatch, revoked, claim) == ("COMMIT_MISMATCH", "MANDATE_REVOKED", "BAD_CLAIM")
    assert mandates[0]["status"] == "revoked"


# --------------------------------------------------------------------------- over real HTTP, with restarts


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_ledger(db: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ, TRAILPROOF_DB=str(db), PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-m", "trailproof.server", "--http", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            HttpToolClient(url, timeout=2)._call_sync("get_trail", {"order_id": "probe", "party_id": "nobody"})
            return proc
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("ledger exited")
            time.sleep(0.3)
    proc.kill()
    raise RuntimeError("ledger did not start")


def test_ledger_restart_keeps_history_and_signing_key(tmp_path):
    port, db = free_port(), tmp_path / "ledger.db"
    url = f"http://127.0.0.1:{port}/mcp"
    proc = start_ledger(db, port)
    try:
        async def first():
            w = Wallet.create(tmp_path / "wallet.json", "mia", url)
            await w.register()
            shop = Shop(HttpToolClient(url), tmp_path / "shop.json")
            await shop.ensure()
            m = w.add_mandate(["pet_food"], "200")["mandate_id"]
            await buy(w, shop, m)
            await buy(w, shop, m)
            return await w.prove_history(shop.shop.party_id), w.state["ledger_public_key"], shop.shop.party_id

        before, key, shop_id = run(first())
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    proc = start_ledger(db, port)        # same database, a new process
    try:
        async def second():
            w = Wallet.load(tmp_path / "wallet.json")
            shop = Shop(HttpToolClient(url), tmp_path / "shop.json")
            await shop.ensure()
            after = await w.prove_history(shop.shop.party_id)
            _, offer, placed = await buy(w, shop, next(iter(w.state["mandates"])), proof=await w.prove_history(shop.shop.party_id, shop.shop.party_id))
            return after, offer, placed, shop.shop.party_id

        after, offer, placed, shop_id_after = run(second())
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    assert shop_id_after == shop_id                                   # the shop kept its identity too
    assert before["stats"]["orders_completed"] == after["stats"]["orders_completed"] == 2
    assert crypto.verify_attestation(before, key) and crypto.verify_attestation(after, key)   # one ledger key, before and after
    assert offer["recognised"] and placed["ordered"] and placed["total"] == "33.21"
    assert (tmp_path / "ledger.db.key").exists()


def test_the_wallet_runs_as_an_mcp_server_over_stdio(tmp_path):
    """What Claude does: spawn ``python -m wallet serve`` and call its tools."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    w = Wallet.create(tmp_path / "wallet.json", "mia", "http://127.0.0.1:1/mcp")   # no ledger needed for these two tools
    w.add_mandate(["pet_food"], "40")

    async def go():
        env = dict(os.environ, TRAILPROOF_WALLET=str(tmp_path / "wallet.json"), PYTHONUTF8="1")
        params = StdioServerParameters(command=sys.executable, args=["-m", "wallet", "serve"], env=env, cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [t.name for t in (await session.list_tools()).tools]
                status = (await session.call_tool("wallet_status", {})).structured_content
                mandates = (await session.call_tool("list_mandates", {})).structured_content
                unreachable = (await session.call_tool("prove_history", {"audience_party_id": "pty_x"})).structured_content
                return tools, status, mandates, unreachable

    tools, status, mandates, unreachable = run(go())
    assert "place_order" in tools and len(tools) == 9
    assert status["name"] == "mia" and status["registered"] is False
    assert mandates["mandates"][0]["remaining"] == "40.00"
    assert unreachable["error"] == "NOT_REGISTERED"
