"""Run the TrailProof demo: happy path, five dispute/scope scenarios and brand recognition, end to end.

By default this spawns the real MCP server (``python -m trailproof.server``)
and drives it over stdio through the mock buyer, shop and PSP. Use
``--direct`` to call the service in-process instead (no subprocess).

    uv run python demo.py            # real MCP over stdio
    uv run python demo.py --direct   # in-process
    uv run python demo.py --db trail.db --verbose
    uv run python demo.py --llm 4      # also run 4 episodes with Claude as the buyer (needs credentials)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from mocks.scenarios import SCENARIOS, World, scenario_fail_open
from mocks.transport import DirectToolClient, StdioToolClient
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService

W = 100


def hr(ch: str = "-") -> str:
    return ch * W


def print_trail(trail: dict[str, Any], roles: dict[str, str], recognitions: dict[str, Any] | None = None) -> None:
    rows = trail.get("events", [])
    print(f"  trail for {trail['order_id']}  ({len(rows)} events, head {short(trail.get('head'))})")
    print(f"  {'#':>2}  {'type':<10} {'writer':<11} {'commit':<12} {'prev':<12} {'refs':<4} {'ledger_ts':<13} note")
    for i, e in enumerate(rows, 1):
        note = ""
        pub = e.get("public") or {}
        if pub.get("scope_checked"):
            note = f"scope ok, spent-before {pub.get('already_spent')}"
        if e["type"] == "OFFER" and recognitions and trail["order_id"] in recognitions:
            r = recognitions[trail["order_id"]]
            note = ("recognised: " if r["recognised"] else "public price: ") + r["reason"]
        print(
            f"  {i:>2}  {e['type']:<10} {roles.get(e['party_id'], '?'):<11} {short(e['commit']):<12} "
            f"{short(e['prev']):<12} {len(e['refs']):<4} {e['ledger_ts'][11:23]:<13} {note}"
        )
    for c in trail.get("cases", []):
        print(f"      case {c['case_id']}  claim={c['claim']}  status={c['status']}")


def short(h: str | None) -> str:
    return (h[:10] + "…") if h else "null"


def print_bundle(bundle: dict[str, Any]) -> None:
    print(f"  bundle {bundle['case_id']}  claim={bundle['claim']}  status={bundle.get('status')}")
    for f in bundle["findings"]:
        print(f"    [{f['severity']:<6}] {f['code']:<22} {f['detail'][:70]}")
    print(f"    verdict: {bundle['verdict']}  remedy: {bundle['remedy']['type']}"
          + (f" {bundle['remedy']['amount']} {bundle['remedy']['currency']}" if bundle['remedy'].get('amount') else ""))
    print(f"    rationale: {bundle.get('rationale')}")


async def run_llm(client: Any, episodes: int) -> bool:
    from mocks.buyer_llm import DEFAULT_MODEL, credentials_available, default_episode_plan, run_llm_episodes

    print()
    print(hr())
    print(f"LLM BUYER: Claude ({DEFAULT_MODEL}) as the buyer agent, {episodes} episodes")
    print(hr())
    if not credentials_available():
        print("  skipped: no Anthropic credentials (set ANTHROPIC_API_KEY or run `ant auth login`; install with `uv sync --extra llm`)")
        return True
    base = default_episode_plan()
    plan = []
    for i in range(episodes):
        item = dict(base[i % len(base)])
        if i >= len(base):
            item["order_id"] = f"{item['order_id']}_{i}"
            item["nonce"] = f"{item['nonce']}_{i // len(base)}"
        plan.append(item)
    result = await run_llm_episodes(client, plan)
    s = result["summary"]
    print(f"  check_order used in {s['check_order_used']}/{s['episodes']}, skipped in {s['check_order_skipped']}/{s['episodes']} "
          f"({s['skip_rate']:.0%}); correct outcomes {s['correct_outcomes']}/{s['episodes']}; errors {s['errors']}")
    return s["errors"] == 0


async def run(client: Any, verbose: bool, llm_episodes: int = 0) -> bool:
    world = await World.create(client)
    roles = world.roles()
    print(hr("="))
    print("TrailProof demo  |  evidence agreed before the fight, not collected after it")
    print(hr("="))
    print("parties:", ", ".join(f"{r}={p[:12]}" for p, r in roles.items()))
    all_ok = True

    for num, title, fn in SCENARIOS:
        world.log.clear()
        print()
        print(hr())
        print(f"SCENARIO {num}: {title}")
        print(hr())
        result = await fn(world)
        roles = world.roles()  # a scenario may register parties of its own
        for line in world.log:
            print(f"  > {line}")
        print()
        print_trail(result["trail"], roles, result.get("recognitions"))
        if "trail_a" in result:
            print()
            print_trail(result["trail_a"], roles, result.get("recognitions"))
        if "bundle" in result:
            print()
            print_bundle(result["bundle"])
        if "attestation" in result:
            a = result["attestation"]
            print(f"  attestation for {a['subject'][:12]} -> audience {a['audience'][:12]}: {json.dumps(a['stats'])}  ledger_sig {short(a['ledger_sig'])}")
        if "decision" in result:
            d = result["decision"]
            print(f"  merchant decision: {d['action']}" + (f" EUR {d['amount']}" if d["action"] == "refunded" else f" ({d.get('reason')})"))
        if "placed" in result and not result["placed"]["placed"]:
            for r in result["placed"]["reasons"]:
                print(f"  reason: {r['code']}: {r['detail']}")
        if "placed_b" in result:
            for r in result["placed_b"]["reasons"]:
                print(f"  reason: {r['code']}: {r['detail']}")
        if verbose and "pack" in result:
            print("  evidence pack:")
            print("   " + json.dumps(result["pack"], indent=2, ensure_ascii=False).replace("\n", "\n   "))
        print()
        for name, ok in result["checks"].items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            all_ok &= bool(ok)

    print()
    print(hr())
    print("EXTRA: fail-open when the ledger is unreachable (client-side)")
    print(hr())
    extra = await scenario_fail_open()
    print(f"  > buyer.place_order -> {extra['result']}")
    for name, ok in extra["checks"].items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        all_ok &= bool(ok)

    if llm_episodes:
        all_ok &= await run_llm(client, llm_episodes)

    print()
    print(hr("="))
    print(f"tool calls made: {len(client.calls)}   result: {'ALL GREEN' if all_ok else 'FAILURES'}")
    print(hr("="))
    return all_ok


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direct", action="store_true", help="call the service in-process instead of over MCP stdio")
    ap.add_argument("--db", default=":memory:", help="SQLite path for the ledger (default: in-memory)")
    ap.add_argument("--verbose", action="store_true", help="print evidence packs in full")
    ap.add_argument("--llm", type=int, default=0, metavar="N", help="run N episodes with Claude as the buyer agent (needs Anthropic credentials)")
    args = ap.parse_args()

    if args.direct:
        client = DirectToolClient(TrailProofService(Ledger(args.db)))
        ok = await run(client, args.verbose, args.llm)
    else:
        async with StdioToolClient(db_path=args.db) as client:
            print(f"connected to MCP server over stdio; tools: {', '.join(client.tools)}")
            ok = await run(client, args.verbose, args.llm)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
