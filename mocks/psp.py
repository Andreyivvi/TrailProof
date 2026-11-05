"""Mock payment provider: charges and refunds, each committed to the ledger."""

from __future__ import annotations

import itertools
from typing import Any

from trailproof import crypto

from .base import MockParty


class MockPSP(MockParty):
    def __init__(self, client: Any, operator: str = "mockpay"):
        super().__init__(client, "psp", operator)
        self._seq = itertools.count(1)
        self.charges: dict[str, dict[str, Any]] = {}  # order_id -> CHARGE payload
        self.refunds: dict[str, list[dict[str, Any]]] = {}

    async def charge(self, order_id: str, amount: str, currency: str) -> dict[str, Any]:
        payload = {"amount": crypto.amount_str(amount), "currency": currency, "psp_ref": f"ch_{next(self._seq):04d}"}
        res = await self.record(order_id, "CHARGE", payload)
        if "event_id" in res:
            self.charges[order_id] = payload
        return {"payload": payload, "receipt": res}

    async def refund(self, order_id: str, amount: str, currency: str, reason: str) -> dict[str, Any]:
        payload = {
            "amount": crypto.amount_str(amount),
            "currency": currency,
            "psp_ref": f"re_{next(self._seq):04d}",
            "reason": reason,
        }
        res = await self.record(order_id, "REFUND", payload)
        if "event_id" in res:
            self.refunds.setdefault(order_id, []).append(payload)
        return {"payload": payload, "receipt": res}
