"""Independently re-verify a TrailProof evidence pack.

This script does not talk to the ledger. Given only the exported JSON, it
recomputes every check a card-network reviewer (or an arbiter agent) would
need to trust the pack:

  1. every event's ledger receipt: ed25519(ledger, event_id|commit|ledger_ts)
  2. every writer signature:        ed25519(party, commit|prev|refs)
  3. the hash chain:                each event's prev == the previous commit
  4. every revealed plaintext:      sha256(canonical_json(payload)) == commit
  5. the findings' evidence ids all point at events in the pack

    uv run python verify_pack.py packs/case_xxx.json
    uv run python verify_pack.py packs/case_xxx.json --tamper   # flip one byte, watch it fail

Exit code 0 = pack verifies, 1 = problems found.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from typing import Any

from trailproof import crypto


def verify_pack(pack: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    """Return ``(problems, counts)``; an empty problem list means the pack verifies."""
    problems: list[str] = []
    counts = {"receipts": 0, "writer_sigs": 0, "chain_links": 0, "reveals": 0, "findings": 0}
    tp = pack.get("trailproof") or {}
    ledger_key = tp.get("ledger_public_key", "")
    parties = tp.get("parties") or {}
    events = tp.get("events") or []
    revealed = tp.get("revealed_payloads") or {}
    event_ids = {e["event_id"] for e in events}

    prev: str | None = None
    for e in events:
        eid = e["event_id"]
        # 1. ledger receipt
        rc = e.get("ledger_receipt") or {}
        msg = crypto.receipt_signing_message(eid, e["commit"], rc.get("ledger_ts", ""))
        if crypto.verify(ledger_key, msg, rc.get("ledger_sig", "")):
            counts["receipts"] += 1
        else:
            problems.append(f"{eid} ({e['type']}): ledger receipt signature does not verify")
        # 2. writer signature
        party = parties.get(e["party_id"])
        if not party:
            problems.append(f"{eid} ({e['type']}): writer {e['party_id']} has no public key in the pack")
        else:
            msg = crypto.event_signing_message(e["commit"], e.get("prev"), e.get("refs") or [])
            if crypto.verify(party["public_key"], msg, e.get("sig", "")):
                counts["writer_sigs"] += 1
            else:
                problems.append(f"{eid} ({e['type']}): writer signature by {party['role']} does not verify")
        # 3. chain
        if e.get("prev") == prev:
            counts["chain_links"] += 1
        else:
            problems.append(f"{eid} ({e['type']}): prev link broken (expected {(prev or 'null')[:12]})")
        prev = e["commit"]
        # 4. revealed plaintext
        if eid in revealed:
            if crypto.commit(revealed[eid]) == e["commit"]:
                counts["reveals"] += 1
            else:
                problems.append(f"{eid} ({e['type']}): revealed payload does not hash to the commitment")

    # 5. findings cite real events
    for f in tp.get("findings") or []:
        counts["findings"] += 1
        for ev in f.get("evidence") or []:
            if ev not in event_ids:
                problems.append(f"finding {f['code']} cites unknown event {ev}")

    if not events:
        problems.append("pack contains no events")
    return problems, counts


def tamper(pack: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with one revealed amount changed, to show the verifier catches it."""
    p = copy.deepcopy(pack)
    for eid, payload in (p["trailproof"].get("revealed_payloads") or {}).items():
        if isinstance(payload, dict) and "total" in payload:
            payload["total"] = "0.01"
            return p
        if isinstance(payload, dict) and "amount" in payload:
            payload["amount"] = "0.01"
            return p
    # no amount to change: flip the first event's commit instead
    e = p["trailproof"]["events"][0]
    e["commit"] = ("0" if e["commit"][0] != "0" else "1") + e["commit"][1:]
    return p


def report(pack: dict[str, Any], label: str = "") -> bool:
    problems, counts = verify_pack(pack)
    tp = pack.get("trailproof", {})
    print(f"verify {label or tp.get('case_id', '?')}: {len(tp.get('events', []))} events, verdict={tp.get('verdict')}")
    print(
        f"  receipts ok {counts['receipts']}  writer sigs ok {counts['writer_sigs']}  chain links ok {counts['chain_links']}  "
        f"reveals ok {counts['reveals']}  findings {counts['findings']}"
    )
    if problems:
        for pr in problems:
            print(f"  FAIL {pr}")
        print("  => PACK DOES NOT VERIFY")
        return False
    print("  => PACK VERIFIES (every hash and signature checks out, offline)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="evidence pack JSON exported by export_evidence_pack")
    ap.add_argument("--tamper", action="store_true", help="also verify a copy with one revealed amount altered")
    args = ap.parse_args()
    with open(args.pack, encoding="utf-8") as f:
        pack = json.load(f)
    ok = report(pack)
    if args.tamper:
        print()
        ok_t = report(tamper(pack), label="tampered copy")
        return 0 if (ok and not ok_t) else 1
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
