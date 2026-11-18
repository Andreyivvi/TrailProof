"""The TrailProof ledger: an append-only SQLite store of signed commitments.

The ledger never sees plaintext payloads during an order. It stores, per event,
the writer's commitment hash, the chain link to the previous event of the same
order, the writer's signature and its own countersignature + timestamp (the
*receipt*). On a dispute, parties reveal plaintexts into a *case*, and the
ledger verifies each one against the hash it committed to earlier.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from . import crypto

EVENT_TYPES = (
    "MANDATE",
    "OFFER",
    "ORDER",
    "CHARGE",
    "FULFILMENT",
    "DELIVERY",
    "DISPUTE",
    "REFUND",
)

ROLES = ("user", "buyer_agent", "merchant", "psp", "ledger")

#: Which party role may write which event type.
WRITER_ROLES: dict[str, tuple[str, ...]] = {
    "MANDATE": ("user",),  # signed by the user key, submitted by the buyer agent
    "OFFER": ("merchant",),
    "ORDER": ("buyer_agent",),
    "CHARGE": ("psp",),
    "FULFILMENT": ("merchant",),
    "DELIVERY": ("buyer_agent",),
    "DISPUTE": ("buyer_agent", "merchant"),
    "REFUND": ("psp",),
}

CASE_OPEN = "open"
CASE_CLOSED = "closed"


class LedgerError(Exception):
    """Raised for every rejected operation. ``code`` is stable; ``detail`` is prose."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "detail": self.detail}


@dataclass
class Party:
    party_id: str
    role: str
    public_key: str
    operator: str
    revoked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "party_id": self.party_id,
            "role": self.role,
            "public_key": self.public_key,
            "operator": self.operator,
            "revoked_at": self.revoked_at,
        }


@dataclass
class Event:
    event_id: str
    order_id: str
    type: str
    party_id: str
    commit: str
    refs: list[str]
    prev: str | None
    sig: str
    ledger_ts: str
    ledger_sig: str
    seq: int = 0
    public: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "event_id": self.event_id,
            "order_id": self.order_id,
            "type": self.type,
            "party_id": self.party_id,
            "commit": self.commit,
            "refs": list(self.refs),
            "prev": self.prev,
            "sig": self.sig,
            "ledger_ts": self.ledger_ts,
            "ledger_sig": self.ledger_sig,
        }
        if self.public:
            d["public"] = dict(self.public)
        return d

    def receipt(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "ledger_ts": self.ledger_ts,
            "ledger_sig": self.ledger_sig,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS parties (
    party_id   TEXT PRIMARY KEY,
    role       TEXT NOT NULL,
    public_key TEXT NOT NULL,
    operator   TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL UNIQUE,
    order_id   TEXT NOT NULL,
    type       TEXT NOT NULL,
    party_id   TEXT NOT NULL REFERENCES parties(party_id),
    commit_    TEXT NOT NULL,
    refs       TEXT NOT NULL,
    prev       TEXT,
    sig        TEXT NOT NULL,
    ledger_ts  TEXT NOT NULL,
    ledger_sig TEXT NOT NULL,
    public     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_order ON events(order_id, seq);
CREATE INDEX IF NOT EXISTS events_commit ON events(commit_);
CREATE TABLE IF NOT EXISTS mandate_spend (
    user_id TEXT NOT NULL,
    nonce   TEXT NOT NULL,
    spent   TEXT NOT NULL,
    PRIMARY KEY (user_id, nonce)
);
CREATE TABLE IF NOT EXISTS order_spend (
    order_id TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL,
    nonce    TEXT NOT NULL,
    amount   TEXT NOT NULL,
    refunded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cases (
    case_id    TEXT PRIMARY KEY,
    order_id   TEXT NOT NULL,
    claim      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    opened_by  TEXT NOT NULL,
    dispute_event_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    opened_at  TEXT NOT NULL,
    closed_at  TEXT,
    resolution TEXT
);
CREATE TABLE IF NOT EXISTS reveals (
    case_id     TEXT NOT NULL REFERENCES cases(case_id),
    event_id    TEXT NOT NULL,
    party_id    TEXT NOT NULL,
    payload     TEXT,
    verified    INTEGER NOT NULL,
    revealed_at TEXT NOT NULL,
    PRIMARY KEY (case_id, event_id)
);
"""


class Ledger:
    """SQLite-backed commitment ledger with its own Ed25519 identity.

    ``clock`` is injectable so tests can freeze time.
    """

    def __init__(
        self,
        path: str = ":memory:",
        keypair: crypto.Keypair | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.path = path
        # check_same_thread=False: the MCP server may call tools from a worker thread;
        # the service serialises access, so one connection is safe.
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.keypair = keypair or crypto.Keypair.generate()
        self.clock = clock or crypto.utc_now
        # The ledger registers itself so its receipts are verifiable by key id.
        row = self.conn.execute("SELECT party_id FROM parties WHERE role='ledger'").fetchone()
        if row is None:
            self.ledger_party_id = self._insert_party("ledger", self.keypair.public_hex, "trailproof")
        else:
            self.ledger_party_id = row["party_id"]
            # A persisted DB with a fresh runtime key: re-register the new key.
            if self.party(self.ledger_party_id).public_key != self.keypair.public_hex:
                self.ledger_party_id = self._insert_party(
                    "ledger", self.keypair.public_hex, "trailproof"
                )

    # ------------------------------------------------------------------ util
    def now_iso(self) -> str:
        return crypto.iso_z(self.clock())

    def close(self) -> None:
        self.conn.close()

    # --------------------------------------------------------------- parties
    def _insert_party(self, role: str, public_key: str, operator: str) -> str:
        party_id = f"pty_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO parties(party_id, role, public_key, operator) VALUES (?,?,?,?)",
            (party_id, role, public_key, operator),
        )
        return party_id

    def register_party(self, role: str, public_key: str, operator: str) -> str:
        if role not in ROLES or role == "ledger":
            raise LedgerError("BAD_ROLE", f"role must be one of {ROLES[:-1]}, got {role!r}")
        try:
            crypto.public_key_from_hex(public_key)
        except (ValueError, TypeError):
            raise LedgerError("BAD_KEY", "public_key must be 32 raw Ed25519 bytes, hex-encoded")
        if not operator:
            raise LedgerError("BAD_OPERATOR", "operator must be a non-empty string")
        return self._insert_party(role, public_key, operator)

    def revoke_party(self, party_id: str) -> None:
        p = self.party(party_id)
        self.conn.execute(
            "UPDATE parties SET revoked_at=? WHERE party_id=?", (self.now_iso(), p.party_id)
        )

    def party(self, party_id: str) -> Party:
        row = self.conn.execute("SELECT * FROM parties WHERE party_id=?", (party_id,)).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_PARTY", f"no party {party_id!r}")
        return Party(**dict(row))

    def _active_party(self, party_id: str) -> Party:
        p = self.party(party_id)
        if p.revoked_at is not None:
            raise LedgerError("REVOKED_KEY", f"party {party_id} was revoked at {p.revoked_at}")
        return p

    # ---------------------------------------------------------------- events
    def _row_to_event(self, row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"],
            order_id=row["order_id"],
            type=row["type"],
            party_id=row["party_id"],
            commit=row["commit_"],
            refs=json.loads(row["refs"]),
            prev=row["prev"],
            sig=row["sig"],
            ledger_ts=row["ledger_ts"],
            ledger_sig=row["ledger_sig"],
            seq=row["seq"],
            public=json.loads(row["public"] or "{}"),
        )

    def head(self, order_id: str) -> Event | None:
        row = self.conn.execute(
            "SELECT * FROM events WHERE order_id=? ORDER BY seq DESC LIMIT 1", (order_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def get_trail(self, order_id: str) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE order_id=? ORDER BY seq", (order_id,)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> Event:
        row = self.conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_EVENT", f"no event {event_id!r}")
        return self._row_to_event(row)

    def find_by_commit(self, commit: str, order_id: str | None = None) -> list[Event]:
        if order_id is None:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE commit_=? ORDER BY seq", (commit,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE commit_=? AND order_id=? ORDER BY seq",
                (commit, order_id),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def order_parties(self, order_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT party_id FROM events WHERE order_id=?", (order_id,)
        ).fetchall()
        return {r["party_id"] for r in rows}

    def orders_of_party(self, party_id: str) -> list[str]:
        """Every order this party has written at least one event for, oldest first."""
        rows = self.conn.execute(
            "SELECT order_id, MIN(seq) AS first_seq FROM events WHERE party_id=? "
            "GROUP BY order_id ORDER BY first_seq",
            (party_id,),
        ).fetchall()
        return [r["order_id"] for r in rows]

    def record_event(
        self,
        order_id: str,
        type: str,
        party_id: str,
        commit: str,
        refs: list[str] | None,
        sig: str,
        prev: str | None = None,
        public: dict[str, Any] | None = None,
    ) -> Event:
        """Verify, countersign and append one event. Returns the stored event.

        Checks, in order: event type, party exists and is not revoked, party role
        may write this type, commit well-formed, every ref is a known commit,
        ``prev`` (if the caller states it) matches the head of the order chain,
        and the writer's signature covers ``commit|prev|refs`` with the *ledger's*
        view of ``prev``.
        """
        refs = list(refs or [])
        if type not in EVENT_TYPES:
            raise LedgerError("BAD_TYPE", f"unknown event type {type!r}")
        if not order_id or not isinstance(order_id, str):
            raise LedgerError("BAD_ORDER_ID", "order_id must be a non-empty string")
        party = self._active_party(party_id)
        if party.role not in WRITER_ROLES[type]:
            raise LedgerError(
                "WRONG_ROLE",
                f"{type} may only be written by {WRITER_ROLES[type]}, not by a {party.role}",
            )
        if not _is_sha256_hex(commit):
            raise LedgerError("BAD_COMMIT", "commit must be a 64-char lowercase sha256 hex digest")
        for ref in refs:
            if not _is_sha256_hex(ref) or not self.find_by_commit(ref):
                raise LedgerError("UNKNOWN_REF", f"ref {ref!r} is not a commit known to the ledger")

        head = self.head(order_id)
        actual_prev = head.commit if head else None
        if prev is not None and prev != actual_prev:
            raise LedgerError(
                "BAD_PREV",
                f"stated prev {prev[:12]}… does not match the order head "
                f"{(actual_prev or 'null')[:12]}",
            )
        if type == "MANDATE" and head is not None and head.type == "MANDATE":
            pass  # a second mandate is allowed (e.g. top-up); chain rules still apply

        message = crypto.event_signing_message(commit, actual_prev, refs)
        if not sig:
            raise LedgerError("MISSING_SIG", "sig is required")
        if not crypto.verify(party.public_key, message, sig):
            raise LedgerError(
                "BAD_SIG",
                "signature does not verify over commit|prev|refs with the ledger's prev "
                f"({(actual_prev or 'null')[:12]}); fetch get_trail and re-sign",
            )

        event_id = f"evt_{uuid.uuid4().hex}"
        ledger_ts = self.now_iso()
        ledger_sig = self.keypair.sign(crypto.receipt_signing_message(event_id, commit, ledger_ts))
        self.conn.execute(
            "INSERT INTO events(event_id, order_id, type, party_id, commit_, refs, prev, sig, "
            "ledger_ts, ledger_sig, public) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                order_id,
                type,
                party_id,
                commit,
                json.dumps(refs),
                actual_prev,
                sig,
                ledger_ts,
                ledger_sig,
                json.dumps(public or {}, sort_keys=True),
            ),
        )
        return self.get_event(event_id)

    def verify_receipt(self, event: Event) -> bool:
        """Anyone can check the ledger's countersignature on an event."""
        return crypto.verify(
            self.keypair.public_hex,
            crypto.receipt_signing_message(event.event_id, event.commit, event.ledger_ts),
            event.ledger_sig,
        )

    def verify_event_sig(self, event: Event) -> bool:
        party = self.party(event.party_id)
        return crypto.verify(
            party.public_key,
            crypto.event_signing_message(event.commit, event.prev, event.refs),
            event.sig,
        )

    def verify_chain(self, order_id: str) -> list[str]:
        """Return a list of problems with an order's chain (empty = good)."""
        problems: list[str] = []
        prev: str | None = None
        for ev in self.get_trail(order_id):
            if ev.prev != prev:
                problems.append(f"{ev.event_id}: prev mismatch")
            if not self.verify_event_sig(ev):
                problems.append(f"{ev.event_id}: bad writer signature")
            if not self.verify_receipt(ev):
                problems.append(f"{ev.event_id}: bad ledger receipt")
            prev = ev.commit
        return problems

    # ---------------------------------------------------------- mandate spend
    def spent_under(self, user_id: str, nonce: str) -> Decimal:
        row = self.conn.execute(
            "SELECT spent FROM mandate_spend WHERE user_id=? AND nonce=?", (user_id, nonce)
        ).fetchone()
        return Decimal(row["spent"]) if row else Decimal("0.00")

    def note_order_spend(self, order_id: str, user_id: str, nonce: str, amount: Decimal) -> None:
        """Remember which mandate an order drew on, so a refund can give the budget back."""
        self.conn.execute(
            "INSERT OR REPLACE INTO order_spend(order_id, user_id, nonce, amount, refunded) VALUES (?,?,?,?,0)",
            (order_id, user_id, nonce, crypto.amount_str(amount)),
        )

    def credit_refund(self, order_id: str) -> Decimal | None:
        """A REFUND was committed for ``order_id``: return its amount to the mandate, once.

        Returns the amount credited, or ``None`` if the order drew on no
        mandate or was already credited. The ledger sees only the REFUND's
        hash, so it credits the order's full recorded amount.
        """
        row = self.conn.execute("SELECT * FROM order_spend WHERE order_id=? AND refunded=0", (order_id,)).fetchone()
        if row is None:
            return None
        amount = Decimal(row["amount"])
        remaining = max(self.spent_under(row["user_id"], row["nonce"]) - amount, Decimal("0.00"))
        self.conn.execute("UPDATE mandate_spend SET spent=? WHERE user_id=? AND nonce=?",
                          (crypto.amount_str(remaining), row["user_id"], row["nonce"]))
        self.conn.execute("UPDATE order_spend SET refunded=1 WHERE order_id=?", (order_id,))
        return amount

    def add_spend(self, user_id: str, nonce: str, amount: Decimal) -> Decimal:
        new = self.spent_under(user_id, nonce) + amount
        self.conn.execute(
            "INSERT INTO mandate_spend(user_id, nonce, spent) VALUES (?,?,?) "
            "ON CONFLICT(user_id, nonce) DO UPDATE SET spent=excluded.spent",
            (user_id, nonce, crypto.amount_str(new)),
        )
        return new

    # ------------------------------------------------------------------ cases
    def open_case(
        self, order_id: str, claim: str, note: str, opened_by: str, dispute_event_id: str
    ) -> str:
        case_id = f"case_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO cases(case_id, order_id, claim, note, opened_by, dispute_event_id, "
            "status, opened_at) VALUES (?,?,?,?,?,?,?,?)",
            (case_id, order_id, claim, note, opened_by, dispute_event_id, CASE_OPEN, self.now_iso()),
        )
        return case_id

    def get_case(self, case_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_CASE", f"no case {case_id!r}")
        return dict(row)

    def cases_for_order(self, order_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM cases WHERE order_id=? ORDER BY opened_at", (order_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close_case(self, case_id: str, resolution: str) -> None:
        self.get_case(case_id)
        self.conn.execute(
            "UPDATE cases SET status=?, closed_at=?, resolution=? WHERE case_id=?",
            (CASE_CLOSED, self.now_iso(), resolution, case_id),
        )

    def record_reveal(
        self, case_id: str, event_id: str, party_id: str, payload: Any, verified: bool
    ) -> None:
        self.conn.execute(
            "INSERT INTO reveals(case_id, event_id, party_id, payload, verified, revealed_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(case_id, event_id) DO UPDATE SET "
            "payload=excluded.payload, verified=excluded.verified, revealed_at=excluded.revealed_at",
            (
                case_id,
                event_id,
                party_id,
                json.dumps(payload, sort_keys=True) if verified else None,
                1 if verified else 0,
                self.now_iso(),
            ),
        )

    def reveals_for_case(self, case_id: str) -> dict[str, dict[str, Any]]:
        """``{event_id: {"party_id", "payload"|None, "verified": bool}}``."""
        rows = self.conn.execute("SELECT * FROM reveals WHERE case_id=?", (case_id,)).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out[r["event_id"]] = {
                "party_id": r["party_id"],
                "payload": json.loads(r["payload"]) if r["payload"] else None,
                "verified": bool(r["verified"]),
                "revealed_at": r["revealed_at"],
            }
        return out


def _is_sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )
