"""The one-minute demo as a single storyline: one shop, five visitors.

A pet shop's merchant agent sits behind TrailProof. Visitors arrive one by
one and we watch what the ledger does with each:

  1. Leo's agent, a returning customer     -> recognised, gets the loyalty price
  2. a scraper bot                          -> public price, nothing more
  3. an impostor with a forged attestation  -> caught, public price
  4. an agent with no mandate (live Claude) -> no mandate, no purchase: the ledger refuses
  5. the shop ships Leo the wrong product   -> dispute settles itself, auto-refund

Then: can anyone check this? The evidence pack is re-verified offline and a
tampered copy fails. One line of limitations closes.

    uv run python demo_video.py              # record this (~60 s)
    uv run python demo_video.py --pace 0     # no pauses (dry run)
    uv run python demo_video.py --no-llm     # visitor 4 played by a scripted agent
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

from demo import hr, print_trail
from mocks.scenarios import World
from mocks.transport import StdioToolClient
from trailproof import crypto
from verify_pack import report, tamper

PACE = 1.0
SKU = "DOGFOOD-10KG"


def pause(seconds: float) -> None:
    if PACE > 0:
        time.sleep(seconds * PACE)


def say(line: str = "", delay: float = 0.45) -> None:
    print(line)
    sys.stdout.flush()
    pause(delay)


def visitor(n: int, who: str) -> None:
    say()
    say(f"VISITOR {n}  {who}", 0.7)


def row(label: str, text: str, delay: float = 0.5) -> None:
    say(f"   {label:<9}{text}", delay)


def h(value: str | None, n: int = 12) -> str:
    """Short form of a hash / signature / id for the screen."""
    return (value[:n] + "…") if value else "null"


def tech(text: str, delay: float = 0.4) -> None:
    """An 'under the hood' line: what is actually hashed, signed and compared."""
    say(f"            · {text}", delay)


def show_commit(label: str, payload: dict, stored: str | None = None) -> str:
    """Print canonical JSON -> sha256 for a payload, and compare with the ledger's stored commit."""
    cj = crypto.canonical_json(payload)
    c = crypto.commit(payload)
    if len(cj) <= 80:
        preview = cj
    else:
        keys = [k for k in ("sku", "qty", "total", "amount", "currency") if k in payload]
        shown = ",".join(f'"{k}":{crypto.canonical_json(payload[k])}' for k in keys)
        preview = f"{{{shown}, … {len(payload) - len(keys)} more fields}}  ({len(cj)} bytes canonical JSON)"
    tech(f"{label} plaintext  {preview}")
    tail = "" if stored is None else (f"  == committed {h(stored)}  MATCH" if stored == c else f"  != committed {h(stored)}  MISMATCH")
    tech(f"sha256(canonical_json) = {h(c)}{tail}")
    return c


def show_event_sigs(ev: dict, writer_role: str) -> None:
    """What the writer signed and what the ledger countersigned for one event."""
    msg = crypto.event_signing_message(ev["commit"], ev.get("prev"), ev.get("refs") or [])
    shown = "|".join(h(part, 10) if part else "" for part in msg.split("|"))
    tech(f"{writer_role} signs  \"{shown}\"  (commit|prev|refs) -> sig {h(ev['sig'])}")
    tech(f"ledger countersigns  \"{h(ev['event_id'], 14)}|{h(ev['commit'], 10)}|{ev['ledger_ts']}\" -> receipt {h(ev['ledger_sig'])}")


async def main(no_llm: bool, out_dir: Path, model: str | None = None) -> int:
    print(hr("="))
    say("TRAILPROOF  --  the proof layer for AI-agent purchases", 1.2)
    say("One pet shop, its merchant agent behind TrailProof, and five visitors at the door.", 1.2)
    print(hr("="))

    async with StdioToolClient() as client:
        w = await World.create(client)
        shop, psp = w.shop, w.psp
        say(f"shop online: TrailProof MCP server connected, {len(client.tools)} tools; merchant agent + payment provider registered", 1.2)

        # ---- backstory: Leo has bought here twice before (quietly)
        leo = await w.new_buyer("leo", "petbot")
        for oid in ("ord_leo_1", "ord_leo_2"):
            m = leo.new_mandate(["pet_food"], "40.00")
            await leo.submit_mandate(oid, m)
            o = (await shop.make_offer(oid, SKU))["payload"]
            assert (await leo.place_order(oid, m, o))["placed"]
            await shop.take_payment(oid, psp)
            await shop.fulfil(oid)
            await leo.confirm_delivery(oid, True, o["sku"], o["qty"], o["unit"], "")
        say("backstory: Leo's agent has bought dog food here twice before, both orders clean.", 1.2)
        public_price = shop.quote(SKU)["total"]
        # Visitor 4's live model call takes 15-30 s; run it while visitors 1-3 are on screen.
        v4_task = asyncio.create_task(prepare_visitor4(w, client, no_llm, model))

        # ---- visitor 1: the returning customer
        visitor(1, "Leo's agent  --  a returning customer")
        oid = "ord_leo_3"
        mandate = leo.new_mandate(["pet_food"], "40.00")
        await leo.submit_mandate(oid, mandate)
        att = await leo.request_attestation(shop.party_id)
        st = att["stats"]
        row("shows:", f"ledger attestation: {st['orders_completed']} completed orders, {st['cases_lost']} lost disputes "
                      f"(counts only, addressed to this shop, ledger-signed)")
        tech(f"attestation = {{subject: {h(att['subject'])}, merchant: {h(att['merchant'])}, audience: {h(att['audience'])}, "
             f"stats: {{orders_completed: {st['orders_completed']}, cases_lost: {st['cases_lost']}}}}}")
        ledger_pub = leo.agent.ledger_public_key
        tech(f"ledger_sig {h(att['ledger_sig'])}  verify(ledger_pubkey {h(ledger_pub)}, canonical_json(attestation)) -> "
             f"{str(crypto.verify_attestation(att, ledger_pub)).upper()}")
        offer = await shop.recognise_and_offer(oid, SKU, att, buyer_party_id=leo.agent.party_id)
        o = offer["payload"]
        row("shop:", f"signature checks out -> recognised customer, OFFER EUR {o['total']} instead of {public_price} (committed)")
        offer_ev = next(e for e in (await leo.agent.trail(oid))["events"] if e["type"] == "OFFER")
        show_commit("OFFER", o, offer_ev["commit"])
        show_event_sigs(offer_ev, "shop")
        tech("the ledger stores only these: hash, signatures, timestamp. Never the plaintext.")
        placed = await leo.place_order(oid, mandate, o)
        row("ledger:", "check_order -> inside Leo's mandate, ORDER recorded")
        row("outcome:", f"buys at the recognised price, EUR {o['total']}", 1.2)

        # ---- visitor 2: a scraper
        visitor(2, "a price-scraper bot  --  no user, no history")
        scraper = await w.new_buyer("nobody", "scraperbot")
        q = await shop.recognise_and_offer("ord_scraper", SKU, None, buyer_party_id=scraper.agent.party_id)
        row("shows:", "nothing: no attestation, no user mandate")
        row("shop:", f"public price EUR {q['payload']['total']}; no loyalty offer")
        row("outcome:", "sees the price everyone sees; cannot place an order without a signed mandate", 1.2)

        # ---- visitor 3: an impostor
        visitor(3, "an impostor  --  presents a copy of Leo's attestation with its own name on it")
        impostor = await w.new_buyer("mallory", "fakebot")
        forged = dict(att, subject=impostor.agent.party_id)
        q = await shop.recognise_and_offer("ord_impostor", SKU, forged, buyer_party_id=impostor.agent.party_id)
        row("shows:", "a forged attestation")
        tech(f"subject changed {h(att['subject'])} -> {h(forged['subject'])}; same ledger_sig {h(forged['ledger_sig'])}")
        tech(f"verify(ledger_pubkey, canonical_json(forged)) -> {str(crypto.verify_attestation(forged, ledger_pub)).upper()}"
             "  (the signature covers every byte; one changed field breaks it)")
        row("shop:", f"ledger signature does not match -> {shop.recognitions['ord_impostor']['reason']}")
        row("outcome:", f"public price EUR {q['payload']['total']}; the forgery is flagged", 1.2)

        # ---- visitor 4: Mia's agent, live Claude if credentials exist
        visitor(4, "an agent with NO mandate  --  nobody signed anything; it wants dog food anyway")
        show_lines(await v4_task)

        # ---- visitor 5: the wrong product
        visitor(5, "later that week  --  the shop ships Leo the WRONG product")
        charge = await shop.take_payment(oid, psp)
        ful = (await shop.fulfil(oid, sku="DOGFOOD-5KG", unit="bag_5kg"))["payload"]
        await leo.confirm_delivery(oid, True, ful["sku"], ful["qty"], ful["unit"], "a 5 kg bag arrived; the offer was 10 kg")
        row("charge:", f"EUR {charge['payload']['amount']}  (matches the committed offer)")
        row("ships:", f"{ful['sku']} / {ful['unit']}, tracked  (the committed offer said {o['sku']} / {o['unit']})")
        dispute = await leo.open_dispute(oid, "not_as_described", f"received {ful['sku']}, offer was {o['sku']}")
        case_id = dispute["case_id"]
        row("buyer:", f"records DELIVERY: received, but a 5 kg bag; opens a dispute -> {case_id}")
        await leo.reveal_all(case_id, oid)
        await psp.reveal_all(case_id, oid)
        decision = await shop.handle_dispute(dispute, psp)
        events = {e["type"]: e for e in (await leo.agent.trail(oid))["events"]}
        row("reveals:", "each party hands over its plaintext; the ledger re-hashes it against the commitment it stored earlier")
        show_commit("OFFER      ", shop.offers[oid], events["OFFER"]["commit"])
        show_commit("FULFILMENT ", ful, events["FULFILMENT"]["commit"])
        tech(f"rules: fulfilment.sku {ful['sku']} != offer.sku {o['sku']}; unit {ful['unit']} != {o['unit']} -> DELIVERED_NE_OFFER")
        bundle = await leo.bundle(case_id)
        codes = ", ".join(sorted({f["code"] for f in bundle["findings"]}))
        row("ledger:", f"all reveals match their commitments; findings: {codes}; verdict {bundle['verdict']}")
        row("shop:", f"policy -> {decision['action']} EUR {decision.get('amount')}; REFUND event closes the case", 1.2)
        trail = await leo.agent.trail(oid)
        say()
        print_trail(trail, {**w.roles(), **getattr(w, "extra_roles", {})})
        pause(1.5)

        # ---- closing: can anyone check this?
        say()
        say("CAN ANYONE CHECK THIS?  the shop exports the case; an independent script re-verifies it offline", 1.0)
        pack = await shop.export_evidence(case_id, "product_not_as_described")
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"{case_id}.json"
        path.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
        ok = report(pack)
        pause(1.0)
        ok_t = report(tamper(pack), label="tampered copy (one amount changed)")
        pause(1.0)

        say()
        say("LIMITATIONS: shop, buyers and payment provider are mocks; attestations are counts from one ledger; "
            "the user key is held by the agent, not a real passkey.", 1.2)
        print(hr("="))
        say(f"result: {'ALL CHECKS PASSED' if ok and not ok_t else 'CHECK FAILURE'}   tool calls over MCP: {len(client.calls)}", 0.5)
        print(hr("="))
        return 0 if ok and not ok_t else 1


async def prepare_visitor4(w: World, client, no_llm: bool, model: str | None) -> dict:
    """Visitor 4's work: an agent with no signed mandate tries to buy. Live Claude if credentials exist."""
    shop = w.shop
    oid = "ord_nomandate"
    live = False
    if not no_llm:
        try:
            from mocks.buyer_llm import DEFAULT_MODEL, LLMBuyer, credentials_available

            live = credentials_available()
        except ImportError:
            live = False
    lines: list[tuple[str, str]] = []

    if live:
        model = model or DEFAULT_MODEL
        ghost = LLMBuyer(client, user_name="nobody", model=model)
        await ghost.register()
        o = (await shop.make_offer(oid, SKU))["payload"]
        ep = await ghost.run_episode(oid, o, "aborted")  # no submit_mandate: nothing authorises this purchase
        lines.append(("agent:", f"Claude ({model}) with the TrailProof tools and an offer in hand, but no mandate from any human"))
        calls = ", ".join(ep.tool_calls) or "nothing"
        if ep.order_recorded:
            why = "AN ORDER GOT THROUGH (this should not happen)"
        elif "check_order" in ep.tool_calls:
            why = "ledger: no MANDATE committed for this order -> refused"
        elif "record_event" in ep.tool_calls:
            why = "ledger: an ORDER must reference a MANDATE -> refused"
        else:
            why = "the agent stopped by itself: nothing authorises the purchase"
        lines.append(("tries:", f"{o['title']} at EUR {o['total']} -> Claude called {calls} -> {ep.outcome}  ({why})"
                      + (f"  [error: {ep.error}]" if ep.error else "")))
        lines.append(("outcome:", "no mandate, no purchase" + (" -- and the ledger would refuse an ORDER anyway" if not ep.tool_calls else "")))
        return {"lines": lines}

    ghost = await w.new_buyer("nobody", "ghostbot")
    o = (await shop.make_offer(oid, SKU))["payload"]
    never_signed = ghost.new_mandate(["pet_food"], "40.00")  # drafted by the agent, never signed by a user
    placed = await ghost.place_order(oid, never_signed, o)
    direct = await ghost.agent.record(oid, "ORDER", {"offer_commit": crypto.commit(o), "qty": 1, "total": o["total"], "currency": "EUR"}, refs=[crypto.commit(o)])
    lines.append(("agent:", "scripted buyer with an offer in hand, but no mandate from any human (Claude needs credentials in this shell)"))
    lines.append(("tries:", f"check_order -> {placed['reasons'][0]['code']}: {placed['reasons'][0]['detail']}"))
    lines.append(("tries:", f"record_event ORDER directly -> {direct.get('error')}: {direct.get('detail')}"))
    lines.append(("outcome:", "no mandate, no purchase"))
    return {"lines": lines}


def show_lines(result: dict) -> None:
    lines = result["lines"]
    for i, (label, text) in enumerate(lines):
        row(label, text, 1.2 if i == len(lines) - 1 else 0.8)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pace", type=float, default=1.0, help="pause multiplier; 0 disables pauses")
    ap.add_argument("--no-llm", action="store_true", help="play visitor 4 with the scripted agent")
    ap.add_argument("--out", default="packs", help="directory for the exported evidence pack")
    ap.add_argument("--model", default=None, help="model for visitor 4's live agent (default: TRAILPROOF_MODEL or claude-opus-5-5)")
    args = ap.parse_args()
    PACE = args.pace
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main(args.no_llm, Path(args.out), args.model)))
