"""HW7 benchmark: dispute verdicts with TrailProof commitments vs. the same rules on self-reported records.

Claim under test
----------------
For a dispute over an agent purchase, an arbiter that judges from TrailProof's
pre-committed, signed evidence reaches the correct verdict even when one party
is dishonest about what was agreed, with no LLM and no human. The baseline is
the *same* rules engine (``trailproof.rules``) applied to the records each
party hands over after the fact, with nothing committed in advance: TrailProof
with its key feature disabled.

Cases
-----
Seeded, randomly generated orders over the mock catalogue, each with a ground
truth decided by the generator (not by the rules): who is at fault, and which
party, if any, lies at dispute time or withholds its plaintext. Fault types:
overcharge, wrong item shipped, nothing shipped, order outside the user's
mandate, friendly fraud (clean order, user claims it was not authorised) and a
baseless complaint on a clean order. Every case runs through the real TrailProof
flow (mandate, offer, order, charge, fulfilment, delivery, dispute, reveals,
bundle) via the in-process client, then the baseline evaluation on the same
self-reported records.

Metric: the verdict blames the right side (merchant / buyer side / nobody).
Also counted: honest parties blamed, at-fault parties cleared, wall time and
tool calls per case. Deterministic: rerunning a seed reproduces every verdict.

    uv run python benchmark/bench_disputes.py --seed 1 --n 120 --out benchmark/results/seed1.json
    uv run python benchmark/bench_disputes.py --seed 2 --n 120 --default-judgment --out benchmark/results/seed2_dj.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from decimal import Decimal
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mocks.scenarios import World  # noqa: E402
from mocks.shop import CATALOG  # noqa: E402
from mocks.transport import DirectToolClient  # noqa: E402
from trailproof import crypto, rules  # noqa: E402
from trailproof.ledger import Ledger  # noqa: E402
from trailproof.service import TrailProofService  # noqa: E402

FAULTS = ("overcharge", "wrong_item", "not_shipped", "out_of_scope", "friendly_fraud", "baseless")
#: who is truly to blame, by fault type
TRUTH = {
    "overcharge": "merchant",
    "wrong_item": "merchant",
    "not_shipped": "merchant",
    "out_of_scope": "buyer",
    "friendly_fraud": "buyer",
    "baseless": "none",
}
CLAIM = {
    "overcharge": "not_as_described",
    "wrong_item": "not_as_described",
    "not_shipped": "not_received",
    "out_of_scope": "not_authorised",
    "friendly_fraud": "not_authorised",
    "baseless": "not_as_described",
}
#: dispute-time behaviour of the party that would gain from it
STRATEGIES = ("honest", "lie", "withhold")
BLAME = {"merchant_at_fault": "merchant", "buyer_at_fault": "buyer"}


def generate_cases(seed: int, n: int) -> list[dict[str, Any]]:
    """n cases, faults cycled evenly, strategy chosen per case where it can help."""
    rng = random.Random(seed)
    skus = sorted(CATALOG)
    cases = []
    for i in range(n):
        fault = FAULTS[i % len(FAULTS)]
        sku = rng.choice(skus)
        qty = rng.randint(1, 3)
        total = Decimal(CATALOG[sku]["unit_price"]) * qty
        if fault == "out_of_scope":
            max_amount = total - Decimal(rng.randint(1, 15))
        else:
            max_amount = total + Decimal(rng.randint(0, 25))
        truth = TRUTH[fault]
        # a liar / withholder is the side that is truly at fault; on a baseless
        # complaint the buyer may still try to fabricate a merchant fault
        actor = "merchant" if truth == "merchant" else "buyer"
        strategy = rng.choice(STRATEGIES) if truth != "none" else rng.choice(("honest", "lie"))
        # Labelling rule (fixed after the first seed-1 run, see the report): a party that
        # lies in the dispute is at fault even when the order itself was clean, so a buyer
        # fabricating a merchant fault on a baseless complaint is the one to blame.
        if strategy == "lie":
            truth = actor
        cases.append({
            "case": i,
            "fault": fault,
            "claim": CLAIM[fault],
            "truth": truth,
            "sku": sku,
            "qty": qty,
            "total": crypto.amount_str(total),
            "max_amount": crypto.amount_str(max_amount),
            "overcharge_by": crypto.amount_str(Decimal(rng.randint(1, 12))),
            "actor": actor,
            "strategy": strategy,
        })
    return cases


def wrong_sku(sku: str, rng: random.Random) -> str:
    others = [s for s in sorted(CATALOG) if s != sku]
    return rng.choice(others)


async def run_case(w: World, c: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Drive one case through TrailProof; return the trail, true records and self-reports."""
    buyer, shop, psp = w.buyer, w.shop, w.psp
    order_id = f"bench_{c['case']:04d}"
    fault, strategy, actor = c["fault"], c["strategy"], c["actor"]
    mandate = buyer.new_mandate(["pet_food", "cat_toys"], c["max_amount"], nonce=f"n{c['case']}")
    await buyer.submit_mandate(order_id, mandate)
    offer = (await shop.make_offer(order_id, c["sku"], c["qty"]))["payload"]
    mc, oc = crypto.commit(mandate), crypto.commit(offer)
    if fault == "out_of_scope":
        # the agent overstepped: it wrote ORDER without the scope check (allowed by the ledger)
        order_payload = {"mandate_commit": mc, "offer_commit": oc, "qty": offer["qty"], "total": offer["total"], "currency": offer["currency"]}
        res = await buyer.agent.record(order_id, "ORDER", order_payload, refs=[mc, oc])
        assert "event_id" in res, res
    else:
        placed = await buyer.place_order(order_id, mandate, offer)
        assert placed["placed"] and placed["in_scope"], placed
        order_payload = buyer.orders[order_id]["order_payload"]

    charge_amount = offer["total"]
    if fault == "overcharge":
        charge_amount = crypto.amount_str(Decimal(offer["total"]) + Decimal(c["overcharge_by"]))
    charge = (await shop.take_payment(order_id, psp, amount=charge_amount))["payload"]

    fulfil = None
    if fault == "wrong_item":
        shipped = wrong_sku(c["sku"], rng)
        fulfil = (await shop.fulfil(order_id, sku=shipped, unit=CATALOG[shipped]["unit"]))["payload"]
    elif fault != "not_shipped":
        fulfil = (await shop.fulfil(order_id))["payload"]

    if fault == "not_shipped":
        delivery = (await buyer.confirm_delivery(order_id, False, offer["sku"], offer["qty"], offer["unit"], "nothing arrived"))["payload"]
    elif fault == "wrong_item":
        delivery = (await buyer.confirm_delivery(order_id, True, fulfil["sku"], fulfil["qty"], fulfil["unit"], "wrong item"))["payload"]
    else:
        delivery = (await buyer.confirm_delivery(order_id, True, offer["sku"], offer["qty"], offer["unit"], ""))["payload"]

    dispute = await buyer.open_dispute(order_id, c["claim"], f"benchmark case {c['case']}")
    case_id = dispute["case_id"]

    # --- what each side says at dispute time -------------------------------
    # true records, by event type
    true_records = {"MANDATE": mandate, "OFFER": offer, "ORDER": order_payload, "CHARGE": charge,
                    "FULFILMENT": fulfil, "DELIVERY": delivery, "DISPUTE": {"claim": c["claim"], "note": f"benchmark case {c['case']}"}}
    said = dict(true_records)  # what the arbiter is handed (baseline) / revealed (TrailProof)
    fabricated_fulfilment = None
    if strategy == "lie":
        if actor == "merchant":
            if fault == "overcharge":
                said["OFFER"] = dict(offer, total=charge["amount"], unit_price=crypto.amount_str(Decimal(charge["amount"]) / offer["qty"]))
            elif fault == "wrong_item":
                said["OFFER"] = dict(offer, sku=fulfil["sku"], unit=fulfil["unit"], title=CATALOG[fulfil["sku"]]["title"])
            elif fault == "not_shipped":
                fabricated_fulfilment = {"sku": offer["sku"], "qty": offer["qty"], "unit": offer["unit"], "carrier": "DHL", "tracking": "JD0000000000"}
        else:  # buyer side fabricates a merchant fault through its own DELIVERY record
            other = wrong_sku(offer["sku"], rng)
            said["DELIVERY"] = dict(delivery, received=True, sku=other, unit=CATALOG[other]["unit"], note="wrong item")
    withheld = set()
    if strategy == "withhold":
        withheld = {"OFFER", "FULFILMENT"} if actor == "merchant" else {"MANDATE", "ORDER", "DELIVERY"}

    # --- TrailProof: reveal into the case ------------------------------------
    trail = await w.trail(order_id)
    by_type = {e["type"]: e for e in trail["events"]}
    tamper_user, tamper_agent, tamper_shop = {}, {}, {}
    for typ, payload in said.items():
        if typ in withheld or payload is None or typ not in by_type or payload == true_records[typ]:
            continue
        eid = by_type[typ]["event_id"]
        ({"MANDATE": tamper_user, "ORDER": tamper_agent, "DELIVERY": tamper_agent, "OFFER": tamper_shop, "FULFILMENT": tamper_shop}[typ])[eid] = payload
    if not withheld & {"MANDATE"}:
        await buyer.user.reveal_all(case_id, order_id, tamper=tamper_user)
    if not withheld & {"ORDER"}:
        await buyer.agent.reveal_all(case_id, order_id, tamper=tamper_agent)
    if not withheld & {"OFFER"}:
        await shop.reveal_all(case_id, order_id, tamper=tamper_shop)
    await psp.reveal_all(case_id, order_id)
    bundle = await buyer.bundle(case_id)

    # --- baseline: the same rules on the self-reports, nothing committed -----
    events = [dict(e) for e in trail["events"]]
    reveals: dict[str, dict[str, Any]] = {}
    for e in events:
        typ = e["type"]
        if typ in withheld:
            continue
        payload = said.get(typ)
        if payload is not None:
            reveals[e["event_id"]] = {"party_id": e["party_id"], "payload": payload, "verified": True}
    if fabricated_fulfilment is not None:
        charge_ev = by_type["CHARGE"]
        fake = {"event_id": "fabricated_fulfilment", "order_id": order_id, "type": "FULFILMENT", "party_id": shop.party_id,
                "commit": "0" * 64, "refs": [], "prev": charge_ev["commit"], "sig": "", "ledger_ts": charge_ev["ledger_ts"], "ledger_sig": ""}
        idx = events.index(charge_ev) + 1
        events.insert(idx, fake)
        reveals[fake["event_id"]] = {"party_id": shop.party_id, "payload": fabricated_fulfilment, "verified": True}
    roles = w.roles()
    return {"order_id": order_id, "case_id": case_id, "bundle": bundle,
            "baseline_input": rules.CaseInput(case_id=case_id, order_id=order_id, claim=c["claim"], events=events,
                                              reveals=reveals, roles=roles, sig_status={e["event_id"]: "ok" for e in events}),
            "oracle_input": rules.CaseInput(case_id=case_id, order_id=order_id, claim=c["claim"], events=[dict(e) for e in trail["events"]],
                                            reveals={e["event_id"]: {"party_id": e["party_id"], "payload": true_records[e["type"]], "verified": True}
                                                     for e in trail["events"] if true_records.get(e["type"]) is not None},
                                            roles=roles, sig_status={e["event_id"]: "ok" for e in trail["events"]})}


def blame(verdict: str) -> str:
    return BLAME.get(verdict, "none")


def score(truth: str, verdict: str) -> dict[str, Any]:
    b = blame(verdict)
    return {
        "verdict": verdict,
        "blame": b,
        "correct": b == truth,
        "honest_party_blamed": b != "none" and b != truth,
        "at_fault_cleared": truth != "none" and b != truth,
    }


async def run(seed: int, n: int, default_judgment: bool) -> dict[str, Any]:
    cases = generate_cases(seed, n)
    rng = random.Random(seed * 1000 + 7)
    client = DirectToolClient(TrailProofService(Ledger(":memory:"), default_judgment=default_judgment))
    w = await World.create(client)
    rows = []
    for c in cases:
        calls_before = len(client.calls)
        t0 = time.perf_counter()
        r = await run_case(w, c, rng)
        t_tp = time.perf_counter() - t0
        tp_calls = len(client.calls) - calls_before
        t1 = time.perf_counter()
        base = rules.evaluate(r["baseline_input"], default_judgment=default_judgment)
        t_base = time.perf_counter() - t1
        oracle = rules.evaluate(r["oracle_input"], default_judgment=default_judgment)
        row = dict(c)
        row.update({
            "order_id": r["order_id"],
            "case_id": r["case_id"],
            "trailproof": score(c["truth"], r["bundle"]["verdict"]) | {
                "findings": sorted({f["code"] for f in r["bundle"]["findings"]}),
                "rationale": r["bundle"]["rationale"], "seconds": round(t_tp, 4), "tool_calls": tp_calls},
            "baseline": score(c["truth"], base["verdict"]) | {
                "findings": sorted({f["code"] for f in base["findings"]}), "rationale": base["rationale"], "seconds": round(t_base, 5)},
            "oracle_blame": blame(oracle["verdict"]),
        })
        rows.append(row)
    return {"seed": seed, "n": n, "default_judgment": default_judgment, "rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def agg(sub: list[dict[str, Any]], path: str) -> dict[str, Any]:
        k = len(sub)
        return {
            "n": k,
            "correct": sum(r[path]["correct"] for r in sub),
            "accuracy": round(sum(r[path]["correct"] for r in sub) / k, 3) if k else None,
            "honest_party_blamed": sum(r[path]["honest_party_blamed"] for r in sub),
            "at_fault_cleared": sum(r[path]["at_fault_cleared"] for r in sub),
            "mean_seconds": round(sum(r[path]["seconds"] for r in sub) / k, 4) if k else None,
            **({"mean_tool_calls": round(sum(r[path]["tool_calls"] for r in sub) / k, 1)} if path == "trailproof" and k else {}),
        }

    out: dict[str, Any] = {"overall": {p: agg(rows, p) for p in ("trailproof", "baseline")}}
    out["by_strategy"] = {s: {p: agg([r for r in rows if r["strategy"] == s], p) for p in ("trailproof", "baseline")} for s in STRATEGIES}
    out["by_fault"] = {f: {p: agg([r for r in rows if r["fault"] == f], p) for p in ("trailproof", "baseline")} for f in FAULTS}
    out["oracle_agrees_with_generator"] = sum(r["oracle_blame"] == r["truth"] for r in rows)
    return out


def print_summary(res: dict[str, Any]) -> None:
    s = res["summary"]
    print(f"seed {res['seed']}  n={res['n']}  default_judgment={res['default_judgment']}  "
          f"oracle agrees with generator on {s['oracle_agrees_with_generator']}/{res['n']}")
    print(f"{'slice':<22}{'TrailProof':>18}{'Baseline':>18}   honest blamed TP/BL   at-fault cleared TP/BL")
    for name, sl in [("overall", s["overall"])] + [(f"strategy={k}", v) for k, v in s["by_strategy"].items()] + [(f"fault={k}", v) for k, v in s["by_fault"].items()]:
        tp, bl = sl["trailproof"], sl["baseline"]
        print(f"{name:<22}{tp['correct']:>7}/{tp['n']:<3} {tp['accuracy']!s:>6}{bl['correct']:>7}/{bl['n']:<3} {bl['accuracy']!s:>6}"
              f"   {tp['honest_party_blamed']:>3} / {bl['honest_party_blamed']:<3}              {tp['at_fault_cleared']:>3} / {bl['at_fault_cleared']}")
    o = s["overall"]
    print(f"cost per case: TrailProof {o['trailproof']['mean_seconds']*1000:.1f} ms, {o['trailproof']['mean_tool_calls']} tool calls; "
          f"baseline evaluation {o['baseline']['mean_seconds']*1000:.2f} ms; LLM tokens: 0")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--default-judgment", action="store_true", help="enable rule 5b (the evidence-driven change)")
    ap.add_argument("--out", default=None, help="write the full results JSON here")
    args = ap.parse_args(argv)
    res = asyncio.run(run(args.seed, args.n, args.default_judgment))
    print_summary(res)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1, sort_keys=True)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
