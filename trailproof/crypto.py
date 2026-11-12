"""Cryptographic primitives for TrailProof.

- Ed25519 keypairs (``cryptography`` library)
- canonical JSON (sorted keys, no whitespace, UTF-8)
- SHA-256 commitments over canonical JSON
- the exact byte strings that parties and the ledger sign

Everything here is deterministic and side-effect free so that any party (or an
auditor) can recompute commitments and verify signatures offline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ---------------------------------------------------------------------------
# Canonical JSON + hashing
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to the canonical form every party must agree on.

    Sorted keys, no whitespace, non-ASCII kept as-is. ``Decimal`` values are
    rejected on purpose: amounts must be passed as 2-dp strings (see
    :func:`amount_str`) so that ``36.9`` and ``36.90`` cannot hash differently.
    """

    def _default(o: Any) -> Any:  # pragma: no cover - defensive
        raise TypeError(
            f"{type(o).__name__} is not JSON-serialisable; convert amounts with amount_str()"
        )

    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default
    )


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def commit(payload: dict[str, Any]) -> str:
    """The fingerprint of a plaintext payload: ``sha256(canonical_json(payload))``."""
    return sha256_hex(canonical_json(payload))


# ---------------------------------------------------------------------------
# Amounts and timestamps (conventions from the brief, §13)
# ---------------------------------------------------------------------------

TWO_DP = Decimal("0.01")


def amount_str(value: Decimal | str | int | float) -> str:
    """Serialise an amount as a string with exactly two decimal places."""
    if isinstance(value, float):
        value = repr(value)
    d = Decimal(value).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    return f"{d:.2f}"


def parse_amount(value: Any) -> Decimal:
    """Parse an amount from a payload; raises ``ValueError`` on garbage."""
    try:
        return Decimal(str(value)).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601 with a ``Z`` suffix and millisecond precision."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso_z(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Keys and signatures
# ---------------------------------------------------------------------------


@dataclass
class Keypair:
    """An Ed25519 keypair. ``public_hex`` is what gets registered with the ledger."""

    private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> "Keypair":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_hex(cls, private_hex: str) -> "Keypair":
        """Rebuild a keypair from the 32 raw private bytes, hex-encoded (see :attr:`private_hex`)."""
        return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex)))

    @property
    def private_hex(self) -> str:
        """The raw private key, hex-encoded. Whoever holds this *is* the party; keep it out of logs."""
        return self.private_key.private_bytes_raw().hex()

    @property
    def public_hex(self) -> str:
        return public_key_hex(self.private_key.public_key())

    def sign(self, message: bytes | str) -> str:
        return sign(self.private_key, message)


def public_key_hex(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes_raw().hex()


def public_key_from_hex(hex_value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_value))


def sign(private_key: Ed25519PrivateKey, message: bytes | str) -> str:
    if isinstance(message, str):
        message = message.encode("utf-8")
    return private_key.sign(message).hex()


def verify(public_hex: str, message: bytes | str, signature_hex: str) -> bool:
    """Return ``True`` iff ``signature_hex`` is a valid signature of ``message``."""
    if isinstance(message, str):
        message = message.encode("utf-8")
    try:
        public_key_from_hex(public_hex).verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Signing messages (the exact bytes under each signature)
# ---------------------------------------------------------------------------


def event_signing_message(commit_hex: str, prev: str | None, refs: list[str]) -> str:
    """What the *writer* of an event signs: ``commit|prev|refs``.

    ``prev`` is the empty string for the first event of an order; ``refs`` are
    joined with commas in the order supplied.
    """
    return f"{commit_hex}|{prev or ''}|{','.join(refs)}"


def receipt_signing_message(event_id: str, commit_hex: str, ledger_ts: str) -> str:
    """What the *ledger* signs when it countersigns: ``event_id|commit|ledger_ts``."""
    return f"{event_id}|{commit_hex}|{ledger_ts}"


# ---------------------------------------------------------------------------
# History attestations (brand-direct customer recognition)
# ---------------------------------------------------------------------------

ATTESTATION_SIG_FIELD = "ledger_sig"


def attestation_signing_message(attestation: dict[str, Any]) -> str:
    """What the ledger signs on an attestation: canonical JSON of everything but ``ledger_sig``."""
    body = {k: v for k, v in attestation.items() if k != ATTESTATION_SIG_FIELD}
    return canonical_json(body)


def sign_attestation(private_key: Ed25519PrivateKey, attestation: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``attestation`` carrying the ledger's signature."""
    body = {k: v for k, v in attestation.items() if k != ATTESTATION_SIG_FIELD}
    body[ATTESTATION_SIG_FIELD] = sign(private_key, attestation_signing_message(body))
    return body


def verify_attestation(attestation: Any, ledger_public_hex: str) -> bool:
    """Any party can check an attestation offline with the ledger's public key.

    ``True`` iff ``attestation`` is a dict whose ``ledger_sig`` is a valid
    Ed25519 signature by the ledger over the canonical JSON of the remaining
    fields. Any edit to ``subject``, ``merchant``, ``audience``, ``stats`` or
    ``issued_at`` invalidates it.
    """
    if not isinstance(attestation, dict):
        return False
    sig = attestation.get(ATTESTATION_SIG_FIELD)
    if not isinstance(sig, str) or not sig:
        return False
    try:
        message = attestation_signing_message(attestation)
    except (TypeError, ValueError):
        return False
    return verify(ledger_public_hex, message, sig)
