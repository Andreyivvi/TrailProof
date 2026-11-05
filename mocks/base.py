"""Shared behaviour of every mock party: a keypair, a vault, and signing helpers."""

from __future__ import annotations

from typing import Any

from trailproof import crypto


class MockParty:
    """A party that holds its own key and its own plaintexts (the *vault*).

    The ledger never sees the vault. On a dispute the party reveals entries
    from it with :meth:`reveal_all`.
    """

    def __init__(self, client: Any, role: str, operator: str):
        self.client = client
        self.role = role
        self.operator = operator
        self.kp = crypto.Keypair.generate()
        self.party_id: str | None = None
        self.vault: dict[str, dict[str, Any]] = {}  # event_id -> payload
        self.vault_orders: dict[str, str] = {}  # event_id -> order_id
        self.ledger_public_key: str | None = None

    async def register(self) -> str:
        res = await self.client.call("register_party", role=self.role, public_key=self.kp.public_hex, operator=self.operator)
        if "error" in res:
            raise RuntimeError(f"register_party failed: {res}")
        self.party_id = res["party_id"]
        self.ledger_public_key = res.get("ledger_public_key")
        return self.party_id

    async def head(self, order_id: str) -> str | None:
        trail = await self.client.call("get_trail", order_id=order_id, party_id=self.party_id)
        if "error" in trail:
            raise RuntimeError(f"get_trail failed: {trail}")
        return trail["head"]

    async def trail(self, order_id: str) -> dict[str, Any]:
        return await self.client.call("get_trail", order_id=order_id, party_id=self.party_id)

    async def sign_for(self, order_id: str, commit: str, refs: list[str] | None = None) -> str:
        prev = await self.head(order_id)
        return self.kp.sign(crypto.event_signing_message(commit, prev, refs or []))

    async def record(self, order_id: str, type_: str, payload: dict[str, Any], refs: list[str] | None = None) -> dict[str, Any]:
        """Commit a payload, sign it against the current head, and append it."""
        c = crypto.commit(payload)
        sig = await self.sign_for(order_id, c, refs)
        res = await self.client.call(
            "record_event", order_id=order_id, type=type_, party_id=self.party_id, commit=c, refs=refs or [], sig=sig
        )
        if "event_id" in res:
            self.vault[res["event_id"]] = payload
            self.vault_orders[res["event_id"]] = order_id
        return res

    def events_for(self, order_id: str) -> dict[str, dict[str, Any]]:
        return {eid: p for eid, p in self.vault.items() if self.vault_orders.get(eid) == order_id}

    async def reveal_all(self, case_id: str, order_id: str, tamper: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        """Reveal every vault entry of this order into the case.

        ``tamper`` maps event_id -> the (false) payload to reveal instead; used
        by the tampering scenario.
        """
        out: dict[str, dict[str, Any]] = {}
        for event_id, payload in self.events_for(order_id).items():
            p = (tamper or {}).get(event_id, payload)
            res = await self.client.call("reveal", case_id=case_id, event_id=event_id, payload=p, party_id=self.party_id)
            if res.get("error") == "ALREADY_REVEALED":
                continue  # the DISPUTE event is auto-revealed by the server
            out[event_id] = res
        return out

    async def bundle(self, case_id: str) -> dict[str, Any]:
        return await self.client.call("get_case_bundle", case_id=case_id, party_id=self.party_id)

    async def export_evidence(self, case_id: str, reason_code: str) -> dict[str, Any]:
        return await self.client.call("export_evidence_pack", case_id=case_id, reason_code=reason_code, party_id=self.party_id)
