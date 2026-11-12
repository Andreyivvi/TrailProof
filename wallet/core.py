"""The wallet itself: keys, mandates, proofs, orders and disputes.

Design rules:

* **The person grants authority, the agent only uses it.** Mandates are
  created and revoked with the command line (``python -m wallet mandate ...``),
  never through the tools the agent can call. An agent cannot give itself
  permission to spend.
* **Plaintext stays here.** The ledger receives hashes and signatures; the
  wallet keeps what was hashed, and reveals it only into a dispute.
* **Proofs are per shop.** ``prove_history`` asks the ledger for a signed
  summary addressed to one shop: counts only, never what was bought.

Two keys live in the wallet file. The *user key* signs mandates; the *agent
key* signs orders, deliveries and disputes, and is the identity whose
purchase history is attested. Whoever has the file has both, so it is
written owner-only where the platform allows, and a real deployment would
keep the user key in a passkey or secure enclave instead (see README).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trailproof import crypto

CLAIMS = ("not_as_described", "not_received", "not_authorised")


class WalletError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "detail": self.detail}


def default_path() -> Path:
    return Path(os.environ.get("TRAILPROOF_WALLET", Path.home() / ".trailproof" / "wallet.json"))


class Wallet:
    def __init__(self, path: Path | str, state: dict[str, Any], client: Any = None):
        self.path = Path(path)
        self.state = state
        self.client = client
        self.user = crypto.Keypair.from_private_hex(state["user"]["private_key"])
        self.agent = crypto.Keypair.from_private_hex(state["agent"]["private_key"])

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, path: Path | str, name: str, ledger_url: str, client: Any = None) -> "Wallet":
        path = Path(path)
        if path.exists():
            raise WalletError("WALLET_EXISTS", f"{path} already exists; delete it yourself if you mean to start over")
        state = {
            "version": 1,
            "name": name,
            "ledger_url": ledger_url,
            "ledger_public_key": None,
            "user": {"private_key": crypto.Keypair.generate().private_hex, "party_id": None},
            "agent": {"private_key": crypto.Keypair.generate().private_hex, "party_id": None},
            "mandates": {},
            "orders": {},
            "cases": {},
        }
        w = cls(path, state, client)
        w.save()
        return w

    @classmethod
    def load(cls, path: Path | str | None = None, client: Any = None) -> "Wallet":
        path = Path(path) if path else default_path()
        if not path.exists():
            raise WalletError("NO_WALLET", f"no wallet at {path}; create one with: python -m wallet init --name <you>")
        return cls(path, json.loads(path.read_text(encoding="utf-8")), client)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def _client(self) -> Any:
        if self.client is None:
            from .client import HttpToolClient

            self.client = HttpToolClient(self.state["ledger_url"])
        return self.client

    async def _call(self, tool: str, **args: Any) -> dict[str, Any]:
        res = await self._client().call(tool, **args)
        if isinstance(res, dict) and "error" in res:
            raise WalletError(res["error"], str(res.get("detail", "")))
        return res

    async def register(self) -> dict[str, str]:
        """Register both keys with the ledger (once). Safe to call again."""
        for who, role, kp in (("user", "user", self.user), ("agent", "buyer_agent", self.agent)):
            if self.state[who]["party_id"]:
                continue
            res = await self._call("register_party", role=role, public_key=kp.public_hex,
                                   operator=f"wallet:{self.state['name']}")
            self.state[who]["party_id"] = res["party_id"]
            self.state["ledger_public_key"] = res.get("ledger_public_key")
        self.save()
        return {"user_party_id": self.user_id, "agent_party_id": self.agent_id}

    @property
    def user_id(self) -> str:
        return self.state["user"]["party_id"]

    @property
    def agent_id(self) -> str:
        return self.state["agent"]["party_id"]

    def _require_registered(self) -> None:
        if not self.agent_id or not self.user_id:
            raise WalletError("NOT_REGISTERED", "run: python -m wallet register  (the ledger must be reachable)")

    # ------------------------------------------------------------- mandates
    # Called from the command line only. The MCP server does not expose these.
    def add_mandate(self, categories: list[str], max_amount: str, currency: str = "EUR", merchants: Any = "*",
                    days: int = 30) -> dict[str, Any]:
        mandate_id = f"mnd_{uuid.uuid4().hex[:8]}"
        payload = {
            "user_id": self.state["name"],
            "categories": list(categories),
            "max_amount": crypto.amount_str(max_amount),
            "currency": currency,
            "merchants": merchants,
            "valid_until": crypto.iso_z(crypto.utc_now() + timedelta(days=days)),
            "nonce": mandate_id,
        }
        self.state["mandates"][mandate_id] = {"payload": payload, "created_at": crypto.iso_z(crypto.utc_now()),
                                              "revoked": False}
        self.save()
        return {"mandate_id": mandate_id, **payload}

    def revoke_mandate(self, mandate_id: str) -> None:
        if mandate_id not in self.state["mandates"]:
            raise WalletError("UNKNOWN_MANDATE", mandate_id)
        self.state["mandates"][mandate_id]["revoked"] = True
        self.save()

    def spent(self, mandate_id: str) -> Decimal:
        return sum((Decimal(o["total"]) for o in self.state["orders"].values()
                    if o["mandate_id"] == mandate_id and o.get("ordered")), Decimal("0.00"))

    def mandates(self, active_only: bool = False) -> list[dict[str, Any]]:
        out = []
        now = crypto.utc_now()
        for mid, m in self.state["mandates"].items():
            p = m["payload"]
            expired = crypto.parse_iso_z(p["valid_until"]) < now
            active = not m["revoked"] and not expired
            if active_only and not active:
                continue
            spent = self.spent(mid)
            out.append({"mandate_id": mid, "categories": p["categories"], "max_amount": p["max_amount"],
                        "currency": p["currency"], "merchants": p["merchants"], "valid_until": p["valid_until"],
                        "spent": crypto.amount_str(spent),
                        "remaining": crypto.amount_str(Decimal(p["max_amount"]) - spent),
                        "status": "revoked" if m["revoked"] else ("expired" if expired else "active")})
        return out

    def _active_mandate(self, mandate_id: str) -> dict[str, Any]:
        m = self.state["mandates"].get(mandate_id)
        if m is None:
            raise WalletError("UNKNOWN_MANDATE", f"{mandate_id}; the user creates mandates with: python -m wallet mandate add")
        if m["revoked"]:
            raise WalletError("MANDATE_REVOKED", f"{mandate_id} was revoked by the user")
        if crypto.parse_iso_z(m["payload"]["valid_until"]) < crypto.utc_now():
            raise WalletError("MANDATE_EXPIRED", f"{mandate_id} expired at {m['payload']['valid_until']}")
        return m["payload"]

    # ------------------------------------------------------------- signing
    async def _head(self, order_id: str) -> str | None:
        return (await self._call("get_trail", order_id=order_id, party_id=self.agent_id))["head"]

    async def _record(self, order_id: str, type_: str, payload: dict[str, Any], signer: str,
                      refs: list[str] | None = None) -> dict[str, Any]:
        kp, party_id = (self.user, self.user_id) if signer == "user" else (self.agent, self.agent_id)
        commit = crypto.commit(payload)
        prev = await self._head(order_id)
        sig = kp.sign(crypto.event_signing_message(commit, prev, refs or []))
        receipt = await self._call("record_event", order_id=order_id, type=type_, party_id=party_id, commit=commit,
                                   refs=refs or [], sig=sig)
        self._remember(order_id, receipt["event_id"], type_, payload, signer)
        return {"commit": commit, "receipt": receipt}

    def _remember(self, order_id: str, event_id: str, type_: str, payload: dict[str, Any], signer: str) -> None:
        self.state["orders"].setdefault(order_id, {"mandate_id": None, "total": "0.00", "events": {}})
        self.state["orders"][order_id]["events"][event_id] = {"type": type_, "payload": payload, "signer": signer}
        self.save()

    # --------------------------------------------------------------- proofs
    async def prove_history(self, audience_party_id: str, merchant_party_id: str = "*") -> dict[str, Any]:
        """A ledger-signed summary of this agent's purchase history, addressed to one shop.

        ``merchant_party_id="*"`` (the default) covers every shop and names
        none: what to show a shop the agent has never bought from. Pass the
        shop's own id for the history *with that shop*, which is what earns
        a returning-customer price.
        """
        self._require_registered()
        return await self._call("attest_history", party_id=self.agent_id, merchant_party_id=merchant_party_id,
                                audience_party_id=audience_party_id)

    # --------------------------------------------------------------- orders
    async def start_order(self, mandate_id: str) -> dict[str, Any]:
        """Open a new order under one of the user's mandates.

        Commits the MANDATE (signed with the user key) as the first event of
        a fresh order and returns what the shop needs to commit its offer
        behind it. No mandate, no order: this is the only way to begin.
        """
        self._require_registered()
        payload = self._active_mandate(mandate_id)
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        self.state["orders"][order_id] = {"mandate_id": mandate_id, "total": "0.00", "events": {}, "ordered": False}
        rec = await self._record(order_id, "MANDATE", payload, "user")
        return {"order_id": order_id, "mandate_id": mandate_id, "mandate_commit": rec["commit"],
                "buyer_agent_party_id": self.agent_id, "user_party_id": self.user_id,
                "allowed": {"categories": payload["categories"], "remaining": self.mandates_by_id(mandate_id)["remaining"],
                            "currency": payload["currency"]}}

    def mandates_by_id(self, mandate_id: str) -> dict[str, Any]:
        return next(m for m in self.mandates() if m["mandate_id"] == mandate_id)

    async def place_order(self, order_id: str, offer: dict[str, Any], offer_commit: str | None = None) -> dict[str, Any]:
        """Ask the ledger whether ``offer`` is inside the mandate, and order only if it is.

        ``offer`` is the plaintext the shop handed over; ``offer_commit`` is
        the hash the shop says it committed. If they do not match, the ledger
        says so and nothing is ordered.
        """
        order = self.state["orders"].get(order_id)
        if order is None or not order.get("mandate_id"):
            raise WalletError("UNKNOWN_ORDER", f"{order_id} was not opened with start_order")
        mandate = self._active_mandate(order["mandate_id"])
        mc, oc = crypto.commit(mandate), offer_commit or crypto.commit(offer)
        qty, total, currency = offer["qty"], offer["total"], offer["currency"]
        order_payload = {"mandate_commit": mc, "offer_commit": oc, "qty": qty, "total": total, "currency": currency}
        prev = await self._head(order_id)
        sig = self.agent.sign(crypto.event_signing_message(crypto.commit(order_payload), prev, [mc, oc]))
        res = await self._call("check_order", order_id=order_id, party_id=self.agent_id, mandate_payload=mandate,
                               mandate_commit=mc, offer_payload=offer, offer_commit=oc, qty=qty, total=total,
                               currency=currency, sig=sig)
        if not res["in_scope"]:
            return {"ordered": False, "in_scope": False, "reasons": res["reasons"]}
        order.update(ordered=True, total=crypto.amount_str(total), offer=offer)
        self._remember(order_id, res["receipt"]["event_id"], "ORDER", order_payload, "agent")
        return {"ordered": True, "in_scope": True, "receipt": res["receipt"], "total": order["total"],
                "remaining_under_mandate": self.mandates_by_id(order["mandate_id"])["remaining"]}

    async def confirm_delivery(self, order_id: str, received: bool, sku: str, qty: int, unit: str,
                               note: str = "") -> dict[str, Any]:
        """Commit what actually arrived. Say what you got, not what you ordered."""
        if order_id not in self.state["orders"]:
            raise WalletError("UNKNOWN_ORDER", order_id)
        payload = {"received": received, "sku": sku, "qty": qty, "unit": unit, "note": note}
        rec = await self._record(order_id, "DELIVERY", payload, "agent")
        offer = self.state["orders"][order_id].get("offer") or {}
        as_offered = received and all(str(payload[k]) == str(offer.get(k)) for k in ("sku", "qty", "unit"))
        return {"receipt": rec["receipt"], "as_offered": as_offered}

    # ------------------------------------------------------------- disputes
    async def open_dispute(self, order_id: str, claim: str, note: str = "") -> dict[str, Any]:
        """Open a case and reveal this wallet's plaintexts for the order into it."""
        if claim not in CLAIMS:
            raise WalletError("BAD_CLAIM", f"claim must be one of {CLAIMS}")
        order = self.state["orders"].get(order_id)
        if order is None:
            raise WalletError("UNKNOWN_ORDER", order_id)
        payload = {"claim": claim, "note": note}
        prev = await self._head(order_id)
        sig = self.agent.sign(crypto.event_signing_message(crypto.commit(payload), prev, []))
        res = await self._call("open_dispute", order_id=order_id, party_id=self.agent_id, claim=claim, note=note, sig=sig)
        case_id = res["case_id"]
        revealed = {}
        for event_id, ev in list(order["events"].items()):
            party_id = self.user_id if ev["signer"] == "user" else self.agent_id
            r = await self._call("reveal", case_id=case_id, event_id=event_id, payload=ev["payload"], party_id=party_id)
            revealed[ev["type"]] = r["verified"]
        self._remember(order_id, res["receipt"]["event_id"], "DISPUTE", payload, "agent")
        self.state["cases"][case_id] = {"order_id": order_id, "claim": claim}
        self.save()
        return {"case_id": case_id, "revealed": revealed, "merchant_party_id": res.get("merchant")}

    async def case_status(self, case_id: str) -> dict[str, Any]:
        b = await self._call("get_case_bundle", case_id=case_id, party_id=self.agent_id)
        return {"case_id": case_id, "status": b["status"], "verdict": b["verdict"], "remedy": b["remedy"],
                "rationale": b.get("rationale"), "findings": [{"code": f["code"], "detail": f["detail"]} for f in b["findings"]]}

    async def order_trail(self, order_id: str) -> dict[str, Any]:
        t = await self._call("get_trail", order_id=order_id, party_id=self.agent_id)
        return {"order_id": order_id, "events": [{"type": e["type"], "commit": e["commit"][:12], "ledger_ts": e["ledger_ts"]}
                                                 for e in t["events"]], "cases": t["cases"]}

    # --------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        orders = self.state["orders"]
        return {
            "name": self.state["name"],
            "wallet_file": str(self.path),
            "ledger_url": self.state["ledger_url"],
            "registered": bool(self.agent_id),
            "agent_party_id": self.agent_id,
            "user_party_id": self.user_id,
            "mandates": self.mandates(),
            "orders_placed": sum(1 for o in orders.values() if o.get("ordered")),
            "open_cases": list(self.state["cases"]),
        }
