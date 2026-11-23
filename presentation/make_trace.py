"""Run the five-visitor story against the real ledger and export what happened.

The presentation page (``presentation/index.html``) is a replay: every hash,
signature check, error code and verdict it shows comes from this run of the
actual TrailProof service, not from hand-written text.

    uv run python presentation/make_trace.py      # re-records and embeds the run in index.html
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mocks.scenarios import World  # noqa: E402
from mocks.shop import CATALOG, MockShop  # noqa: E402
from mocks.transport import DirectToolClient  # noqa: E402
from trailproof import crypto  # noqa: E402
from trailproof.ledger import Ledger  # noqa: E402
from trailproof.service import TrailProofService  # noqa: E402
from verify_pack import tamper, verify_pack  # noqa: E402

SKU = "DOGFOOD-10KG"


def short(value: str | None, n: int = 10) -> str:
    return value[:n] if value else ""


def blocks(trail: dict, roles: dict, types: list[str]) -> list[dict]:
    return [{"type": e["type"], "writer": roles.get(e["party_id"], "?"), "commit": short(e["commit"]),
             "prev": short(e["prev"])} for e in trail["events"] if e["type"] in types]


async def build() -> dict:
    client = DirectToolClient(TrailProofService(Ledger()))
    w = await World.create(client)
    shop, psp = w.shop, w.psp

    # backstory: two clean orders
    leo = await w.new_buyer("leo", "petbot")
    for oid in ("ord_leo_1", "ord_leo_2"):
        m = leo.new_mandate(["pet_food"], "40.00")
        await leo.submit_mandate(oid, m)
        o = (await shop.make_offer(oid, SKU))["payload"]
        assert (await leo.place_order(oid, m, o))["placed"]
        await shop.take_payment(oid, psp)
        await shop.fulfil(oid)
        await leo.confirm_delivery(oid, True, o["sku"], o["qty"], o["unit"], "")
    public_price = shop.quote(SKU)["total"]
    ledger_pub = leo.agent.ledger_public_key

    # 1. the returning customer
    oid = "ord_leo_3"
    mandate = leo.new_mandate(["pet_food"], "40.00")
    await leo.submit_mandate(oid, mandate)
    att = await leo.request_attestation(shop.party_id)
    offer = (await shop.recognise_and_offer(oid, SKU, att, buyer_party_id=leo.agent.party_id))["payload"]
    placed = await leo.place_order(oid, mandate, offer)
    roles = {**w.roles(), **getattr(w, "extra_roles", {})}
    trail1 = await leo.agent.trail(oid)
    offer_ev = next(e for e in trail1["events"] if e["type"] == "OFFER")
    s1 = {
        "orders": att["stats"]["orders_completed"], "lost": att["stats"]["cases_lost"],
        "sig": short(att["ledger_sig"]), "key": short(ledger_pub),
        "sig_ok": crypto.verify_attestation(att, ledger_pub),
        "price": offer["total"], "public_price": public_price,
        "offer_hash": short(crypto.commit(offer)), "offer_committed": short(offer_ev["commit"]),
        "in_scope": bool(placed["placed"]),
        "blocks": blocks(trail1, roles, ["MANDATE", "OFFER", "ORDER"]),
    }

    # 2. a second shop that has never seen Leo: the proof travels, the shopping history does not
    second = MockShop(client, operator="vet-shop")
    await second.register()
    att2 = await client.call("attest_history", party_id=leo.agent.party_id, merchant_party_id="*",
                             audience_party_id=second.party_id)
    oid2 = "ord_leo_vet"
    mandate2 = leo.new_mandate(["pet_food"], "40.00")
    await leo.submit_mandate(oid2, mandate2)
    offer2 = (await second.recognise_and_offer(oid2, "DOGFOOD-5KG", att2, buyer_party_id=leo.agent.party_id))["payload"]
    placed2 = await leo.place_order(oid2, mandate2, offer2)
    replayed, replay_reason = shop.check_recognition(att2, leo.agent.party_id)
    s_second = {
        "orders": att2["stats"]["orders_completed"], "merchants": att2["stats"]["merchants"], "lost": att2["stats"]["cases_lost"],
        "sig": short(att2["ledger_sig"]), "sig_ok": crypto.verify_attestation(att2, ledger_pub),
        "names_a_shop": shop.party_id in crypto.canonical_json(att2),
        "price": offer2["total"], "public_price": second.quote("DOGFOOD-5KG")["total"], "pct": offer2["recognition"]["discount_pct"],
        "in_scope": bool(placed2["placed"]), "replay_accepted": replayed, "replay_reason": replay_reason,
    }

    # 3. the scraper
    scraper = await w.new_buyer("nobody", "scraperbot")
    q = await shop.recognise_and_offer("ord_scraper", SKU, None, buyer_party_id=scraper.agent.party_id)
    s2 = {"price": q["payload"]["total"], "reason": shop.recognitions["ord_scraper"]["reason"]}

    # 3. the impostor
    impostor = await w.new_buyer("mallory", "fakebot")
    forged = dict(att, subject=impostor.agent.party_id)
    q = await shop.recognise_and_offer("ord_impostor", SKU, forged, buyer_party_id=impostor.agent.party_id)
    s3 = {"subject_was": short(att["subject"]), "subject_now": short(forged["subject"]), "sig": short(forged["ledger_sig"]),
          "sig_ok": crypto.verify_attestation(forged, ledger_pub), "price": q["payload"]["total"],
          "reason": shop.recognitions["ord_impostor"]["reason"]}

    # 4. no mandate
    ghost = await w.new_buyer("ghost", "ghostbot")
    o4 = (await shop.make_offer("ord_nomandate", SKU))["payload"]
    never_signed = ghost.new_mandate(["pet_food"], "40.00")
    tried = await ghost.place_order("ord_nomandate", never_signed, o4)
    direct = await ghost.agent.record("ord_nomandate", "ORDER", {"offer_commit": crypto.commit(o4), "qty": 1,
                                                                  "total": o4["total"], "currency": "EUR"},
                                      refs=[crypto.commit(o4)])
    s4 = {"check_order": tried["reasons"][0]["code"], "record_event": direct.get("error"),
          "detail": direct.get("detail"), "placed": bool(tried["placed"])}

    # 5. the wrong product
    charge = await shop.take_payment(oid, psp)
    ful = (await shop.fulfil(oid, sku="DOGFOOD-5KG", unit="bag_5kg"))["payload"]
    await leo.confirm_delivery(oid, True, ful["sku"], ful["qty"], ful["unit"], "a 5 kg bag arrived; the offer was 10 kg")
    dispute = await leo.open_dispute(oid, "not_as_described", f"received {ful['sku']}, offer was {offer['sku']}")
    case_id = dispute["case_id"]
    await leo.reveal_all(case_id, oid)
    await psp.reveal_all(case_id, oid)
    decision = await shop.handle_dispute(dispute, psp)
    bundle = await leo.bundle(case_id)
    trail5 = await leo.agent.trail(oid)
    events = {e["type"]: e for e in trail5["events"]}
    s5 = {
        "charged": charge["payload"]["amount"], "offered_sku": offer["sku"], "shipped_sku": ful["sku"],
        "offer_hash": short(crypto.commit(shop.offers[oid])), "offer_committed": short(events["OFFER"]["commit"]),
        "ful_hash": short(crypto.commit(ful)), "ful_committed": short(events["FULFILMENT"]["commit"]),
        "findings": sorted({f["code"] for f in bundle["findings"]}), "verdict": bundle["verdict"],
        "refund": decision.get("amount"), "action": decision["action"], "status": bundle["status"],
        "blocks": blocks(trail5, roles, ["CHARGE", "FULFILMENT", "DELIVERY", "DISPUTE", "REFUND"]),
    }

    # closing: anyone can check
    pack = await shop.export_evidence(case_id, "product_not_as_described")
    problems, counts = verify_pack(pack)
    bad_problems, _ = verify_pack(tamper(pack))
    closing = {"events": len(pack["trailproof"]["events"]), "counts": counts, "ok": problems == [],
               "tampered_fails": bool(bad_problems), "tampered_problem": (bad_problems or [""])[0].split(": ", 1)[-1]}

    # 7. not shopping at all: a restaurant booking. The same proof, a different kind of trust.
    # A booking is an order whose price is the deposit; a guest with a clean record has it waived.
    CATALOG["TABLE-FOR-2"] = {"title": "Table for two, Friday 19:30", "category": "restaurant_booking", "unit": "table",
                              "unit_price": "20.00", "currency": "EUR"}
    trattoria = MockShop(client, operator="trattoria", recognition_policy={"min_orders": 2, "max_lost_cases": 0, "discount_pct": 100})
    await trattoria.register()
    bookings = 6
    for i in range(bookings):                                    # earlier bookings, each kept
        oid_b = f"booking_{i + 1}"
        mb = leo.new_mandate(["restaurant_booking"], "200.00", nonce="leo-dining")
        await leo.submit_mandate(oid_b, mb)
        ob = (await trattoria.make_offer(oid_b, "TABLE-FOR-2"))["payload"]
        assert (await leo.place_order(oid_b, mb, ob))["placed"]
        await trattoria.take_payment(oid_b, psp)
    att_r = await leo.request_attestation(trattoria.party_id)
    oid_r = "booking_friday"
    mr = leo.new_mandate(["restaurant_booking"], "200.00", nonce="leo-dining")
    await leo.submit_mandate(oid_r, mr)
    offer_r = (await trattoria.recognise_and_offer(oid_r, "TABLE-FOR-2", att_r, buyer_party_id=leo.agent.party_id))["payload"]
    placed_r = await leo.place_order(oid_r, mr, offer_r)
    trail_r = await leo.agent.trail(oid_r)
    offer_ev_r = next(e for e in trail_r["events"] if e["type"] == "OFFER")
    s_table = {
        "bookings": att_r["stats"]["orders_completed"], "lost": att_r["stats"]["cases_lost"],
        "sig": short(att_r["ledger_sig"]), "key": short(ledger_pub), "sig_ok": crypto.verify_attestation(att_r, ledger_pub),
        "deposit": offer_r["total"], "public_deposit": trattoria.quote("TABLE-FOR-2")["total"],
        "offer_hash": short(crypto.commit(offer_r)), "offer_committed": short(offer_ev_r["commit"]),
        "booked": bool(placed_r["placed"]), "what": CATALOG["TABLE-FOR-2"]["title"],
        "blocks": blocks(trail_r, {**roles, trattoria.party_id: "merchant"}, ["MANDATE", "OFFER", "ORDER"]),
    }
    del CATALOG["TABLE-FOR-2"]

    return {"generated_at": crypto.iso_z(crypto.utc_now()), "tools": 9, "calls": len(client.calls),
            "scenes": [s1, s_second, s2, s3, s4, s5, s_table], "closing": closing}


def main() -> None:
    """Embed the trace in index.html, so the presentation is one self-contained file."""
    import re

    trace = asyncio.run(build())
    page = Path(__file__).resolve().parent / "index.html"
    html = page.read_text(encoding="utf-8")
    html, n = re.subn(r"/\*TRACE-BEGIN\*/.*?/\*TRACE-END\*/", lambda _: "/*TRACE-BEGIN*/" + json.dumps(trace) + "/*TRACE-END*/",
                      html, flags=re.S)
    if n != 1:
        raise SystemExit("index.html has no TRACE-BEGIN/TRACE-END marker")
    page.write_text(html, encoding="utf-8", newline="\n")
    s = trace["scenes"]
    print(f"embedded a fresh run in {page}")
    print(f"  1 recognised {s[0]['price']}  2 second shop {s[1]['price']} (was {s[1]['public_price']}, names a shop: {s[1]['names_a_shop']},"
          f" replay: {s[1]['replay_reason']})  3 public {s[2]['price']}  4 forged sig ok {s[3]['sig_ok']}"
          f"  5 {s[4]['record_event']}  6 {s[5]['verdict']} refund {s[5]['refund']}"
          f"  7 table: {s[6]['bookings']} bookings, deposit {s[6]['deposit']} (was {s[6]['public_deposit']}), booked {s[6]['booked']}"
          f"  verify ok {trace['closing']['ok']}, tampered fails {trace['closing']['tampered_fails']}")


if __name__ == "__main__":
    main()
