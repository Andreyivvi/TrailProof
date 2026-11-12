"""The wallet's command line: the human side.

    python -m wallet ledger                         # run a local ledger (keeps its data in ~/.trailproof)
    python -m wallet init --name mia                # create the wallet and register it
    python -m wallet mandate add --category pet_food --max 40 --days 30
    python -m wallet mandate list
    python -m wallet mandate revoke mnd_1a2b3c4d
    python -m wallet status
    python -m wallet install                        # how to connect it to Claude
    python -m wallet serve                          # what the agent's MCP config runs (stdio)

The wallet file is ~/.trailproof/wallet.json (override with TRAILPROOF_WALLET).
Mandates are created here and only here: the agent's tools cannot grant
themselves permission to spend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from .client import LedgerUnreachable
from .core import Wallet, WalletError, default_path

DEFAULT_LEDGER = "http://127.0.0.1:8765/mcp"
ROOT = Path(__file__).resolve().parent.parent


def home() -> Path:
    return default_path().parent


def cmd_ledger(args: argparse.Namespace) -> int:
    home().mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("TRAILPROOF_DB", str(home() / "ledger.db"))
    print(f"TrailProof ledger on http://127.0.0.1:{args.port}/mcp   data: {env['TRAILPROOF_DB']}   (Ctrl+C to stop)")
    return subprocess.call([sys.executable, "-m", "trailproof.server", "--http", "--port", str(args.port)], cwd=ROOT, env=env)


def cmd_init(args: argparse.Namespace) -> int:
    w = Wallet.create(default_path(), args.name, args.ledger)
    print(f"created {w.path}")
    try:
        ids = asyncio.run(w.register())
        print(f"registered with {args.ledger}\n  agent {ids['agent_party_id']}\n  user  {ids['user_party_id']}")
    except LedgerUnreachable as e:
        print(f"wallet created, but the ledger is not reachable ({e}).\nStart it (python -m wallet ledger) and run: python -m wallet register")
    print("next: python -m wallet mandate add --category pet_food --max 40")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    print(json.dumps(asyncio.run(Wallet.load().register()), indent=2))
    return 0


def cmd_mandate(args: argparse.Namespace) -> int:
    w = Wallet.load()
    if args.action == "add":
        m = w.add_mandate(args.category, args.max, args.currency, args.merchant or "*", args.days)
        print(f"signed mandate {m['mandate_id']}: {', '.join(m['categories'])} up to {m['max_amount']} {m['currency']} "
              f"until {m['valid_until'][:10]}, merchants: {m['merchants']}")
    elif args.action == "revoke":
        w.revoke_mandate(args.mandate_id)
        print(f"revoked {args.mandate_id}")
    else:
        rows = w.mandates()
        if not rows:
            print("no mandates. add one: python -m wallet mandate add --category pet_food --max 40")
        for m in rows:
            print(f"{m['mandate_id']}  {m['status']:<8} {', '.join(m['categories']):<20} {m['remaining']:>8} of {m['max_amount']} "
                  f"{m['currency']} left, until {m['valid_until'][:10]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(json.dumps(Wallet.load().status(), indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import build_server

    build_server().run("stdio")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    root = str(ROOT)
    cfg = {"mcpServers": {
        "trailproof-wallet": {"command": "uv", "args": ["--directory", root, "run", "python", "-m", "wallet", "serve"]},
        "pawshop-demo": {"command": "uv", "args": ["--directory", root, "run", "python", "-m", "demo_shop"]},
    }}
    print("Claude Code, from any folder:\n")
    print(f'  claude mcp add trailproof-wallet -- uv --directory "{root}" run python -m wallet serve')
    print(f'  claude mcp add pawshop-demo -- uv --directory "{root}" run python -m demo_shop\n')
    print("Claude Desktop: add this to claude_desktop_config.json (Settings > Developer > Edit Config), then restart:\n")
    print(json.dumps(cfg, indent=2))
    print("\npawshop-demo is a practice shop to buy from. Keep the ledger running: python -m wallet ledger")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wallet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ledger", help="run a local ledger"); p.add_argument("--port", type=int, default=8765); p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("init", help="create the wallet"); p.add_argument("--name", required=True)
    p.add_argument("--ledger", default=os.environ.get("TRAILPROOF_URL", DEFAULT_LEDGER)); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("register", help="register the wallet's keys with the ledger"); p.set_defaults(fn=cmd_register)
    p = sub.add_parser("mandate", help="add, list or revoke mandates")
    p.add_argument("action", choices=["add", "list", "revoke"]); p.add_argument("mandate_id", nargs="?")
    p.add_argument("--category", action="append", default=[]); p.add_argument("--max", default="0")
    p.add_argument("--currency", default="EUR"); p.add_argument("--days", type=int, default=30)
    p.add_argument("--merchant", action="append", help="limit to these merchant party ids (default: any)")
    p.set_defaults(fn=cmd_mandate)
    p = sub.add_parser("status", help="show the wallet"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("serve", help="run the MCP server (stdio)"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("install", help="show how to connect the wallet to Claude"); p.set_defaults(fn=cmd_install)
    args = ap.parse_args(argv)
    if args.cmd == "mandate" and args.action == "add" and (not args.category or float(args.max) <= 0):
        ap.error("mandate add needs --category and --max")
    if args.cmd == "mandate" and args.action == "revoke" and not args.mandate_id:
        ap.error("mandate revoke needs the mandate id")
    try:
        return args.fn(args)
    except WalletError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except LedgerUnreachable as e:
        print(f"error: the ledger is not reachable: {e}\nstart it with: python -m wallet ledger", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
