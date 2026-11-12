"""The TrailProof wallet: what a person installs into their own AI agent.

It is the customer's side of TrailProof, the "cookie jar": it holds the
person's keys, the mandates they signed, and the plaintext of everything
their agent committed. The agent uses it to prove its purchase history to
shops, to order inside a mandate, and to dispute an order that went wrong.
"""

from .core import Wallet, WalletError  # noqa: F401
