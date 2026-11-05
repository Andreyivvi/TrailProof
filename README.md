# TrailProof MVP

A neutral, append-only **commitment ledger** for purchases made by AI agents,
exposed as an **MCP server**.

Buyer agent, merchant agent and payment provider each lock a signed
*fingerprint* (SHA-256 of canonical JSON) of what they saw at every step of an
order, **before** anyone knows there will be a dispute. On a dispute each side
reveals its plaintext, the ledger checks it against the committed hashes, a
deterministic rules engine produces findings and a verdict, the merchant can
auto-refund, and otherwise an evidence pack is exported in a
chargeback-friendly shape.

> Evidence agreed before the fight, not collected after it.

## Quick start

```bash
uv sync
uv run pytest            # 75 tests, incl. the seven scenarios over real MCP stdio
uv run python demo.py    # spawns the MCP server and drives it through the mocks
```

`demo.py --direct` runs the same scenarios in-process (no subprocess);
`--verbose` also prints the evidence packs; `--db path.db` persists the ledger.

To poke at the server interactively:

```bash
uv run mcp dev trailproof/server.py
```

The server speaks stdio by default. `uv run python -m trailproof.server --http
--port 8765` serves the same tools over MCP streamable HTTP (plain JSON,
stateless) at `http://127.0.0.1:8765/mcp`, so several agent processes can
share one ledger; the NANDA Town integration uses this.

Requirements: Python 3.11+, `mcp`, `cryptography`, `pytest`. Nothing else.

> Windows note: if the project lives in a very deep folder, `uv sync` can hit
> the 260-character path limit while unpacking `jsonschema` (a dependency of
> `mcp`). Either enable long paths in Windows or put the virtualenv somewhere
> short: `UV_PROJECT_ENVIRONMENT=C:/venvs/trailproof uv sync`.

## Layout

| Path | What |
|---|---|
| `trailproof/crypto.py` | Ed25519 keypairs, canonical JSON, SHA-256 commits, the exact signing messages |
| `trailproof/ledger.py` | SQLite store: parties, per-order hash chain, receipts, mandate spend, cases, reveals |
| `trailproof/rules.py` | Scope check + findings engine + verdict table (pure functions, no LLM) |
| `trailproof/service.py` | The nine tools, transport-free; errors as `{"error", "detail"}` |
| `trailproof/server.py` | MCP server (`MCPServer`, stdio) wrapping the service |
| `mocks/transport.py` | `StdioToolClient` (real MCP) and `DirectToolClient` (in-process), fail-open errors |
| `mocks/base.py` | A party with a key and a *vault* of its own plaintexts |
| `mocks/psp.py` | Mock payment provider: `charge()`, `refund()` |
| `mocks/shop.py` | Mock pet shop + merchant agent with the auto-refund `POLICY` and the `RECOGNITION_POLICY` |
| `mocks/buyer.py` | Scripted buyer agent (user key + agent key), fail-open on outage |
| `mocks/scenarios.py` | The seven scenarios, shared by `demo.py` and the tests |
| `mocks/buyer_llm.py` | Optional: Claude as the buyer agent (Anthropic SDK tool-use loop) with a `check_order` skip metric |
| `demo.py` | Runs everything and prints each order's trail as a table |
| `demo_video.py` | Paced, narrated ~60 s run for the demo video (recognised buyer, live Claude buyer, enforcement, offline verification, limitations) |
| `verify_pack.py` | Independent offline verifier for an exported evidence pack; `--tamper` shows a doctored copy failing |
| `SUBMISSION.md` | HW6 kit: paragraph, video script, spreadsheet row, rubric self-check |
| `PITCH.md` | 90-second pitch and the three application-form texts (identification first, disputes second) |
| `tests/` | pytest: crypto, ledger, scope rules, findings + verdicts, service tools, attestations, scenarios |
| `benchmark/` | HW7 benchmark: dishonest-counterparty disputes, TrailProof vs the same rules on unverified records; results and report |

## How an order looks on the ledger

Every event stores only hashes and signatures:

```
#  type        writer       commit        prev          refs  ledger_ts     note
1  MANDATE     user         6f1a0c…       null          0     21:40:03.112
2  OFFER       merchant     a81e3b…       6f1a0c…       0     21:40:03.140
3  ORDER       buyer_agent  c02d9e…       a81e3b…       2     21:40:03.171  scope ok, spent-before 0.00
4  CHARGE      psp          11be77…       c02d9e…       0     21:40:03.190
5  FULFILMENT  merchant     f3d4a2…       11be77…       0     21:40:03.205
6  DELIVERY    buyer_agent  9c0b1f…       f3d4a2…       0     21:40:03.221
```

* `commit = sha256(canonical_json(payload))`, canonical = sorted keys, no
  whitespace, amounts as 2-dp strings.
* The writer signs `commit|prev|refs` (refs comma-joined) with its Ed25519 key;
  `prev` is the commit of the previous event of the same order, so a stale or
  forged `prev` fails the signature check.
* The ledger countersigns `event_id|commit|ledger_ts` — that is the **receipt**
  a party keeps as proof it committed in time.
* Plaintexts live in each party's vault. The only exception is `ORDER`, whose
  `mandate_commit` / `offer_commit` are public so the scope check can be tied
  to the right commitments.

## Tools (MCP)

| Tool | Caller | Does |
|---|---|---|
| `register_party(role, public_key, operator)` | any | registers an Ed25519 key; returns `party_id` and the ledger's public key |
| `record_event(order_id, type, party_id, commit, sig, refs?, prev?)` | writer | verifies role, refs, chain and signature; countersigns; returns the receipt |
| `check_order(order_id, party_id, mandate_payload, mandate_commit, offer_payload, offer_commit, qty, total, currency, sig)` | buyer agent | verifies both commits against the plaintexts, runs the scope check, records `ORDER` if in scope, tracks spend per mandate nonce; plaintexts are discarded |
| `get_trail(order_id, party_id)` | any registered party | the chain (hashes only) + head + case status |
| `open_dispute(order_id, party_id, claim, note, sig)` | buyer or merchant | records `DISPUTE`, opens a case, returns who to notify |
| `reveal(case_id, event_id, payload, party_id)` | writer of that event | verifies `sha256(payload) == commit`; the result is recorded either way and cannot be retried |
| `get_case_bundle(case_id, party_id)` | parties to the order | findings, verdict, remedy, cites |
| `export_evidence_pack(case_id, reason_code, party_id)` | parties to the order | Stripe-like evidence dict + a `trailproof` block to re-verify |
| `attest_history(party_id, merchant_party_id, audience_party_id)` | buyer agent or user key | ledger-signed counts of the caller's verified history with one merchant, addressed to one audience; no order ids or amounts |

Writer roles: `MANDATE` → user key (submitted by the buyer agent), `OFFER` /
`FULFILMENT` → merchant, `ORDER` / `DELIVERY` → buyer agent, `CHARGE` /
`REFUND` → psp, `DISPUTE` → buyer agent or merchant. A `REFUND` on an order
with an open case closes the case.

Every error is a value, never an exception over MCP:
`{"error": "BAD_SIG", "detail": "…"}`. Codes include `BAD_SIG`, `MISSING_SIG`,
`BAD_PREV`, `WRONG_ROLE`, `REVOKED_KEY`, `UNKNOWN_REF`, `COMMIT_MISMATCH`,
`NOT_A_PARTY`, `NOT_WRITER`, `ALREADY_REVEALED`, `CASE_ALREADY_OPEN`.

## Scope check (`check_order`)

`in_scope` only if **all** hold; every failing reason is returned:

* mandate not expired; merchant in `mandate.merchants` or `"*"`
* offer not expired; offer category in `mandate.categories`
* `total <= max_amount − already_spent_under_this_mandate` (spend is tracked
  per user key + nonce, so re-using a nonce is caught: scenario 6)
* order, mandate and offer currencies match
* the order's qty/total equal the offer's

## Findings and verdicts (`rules.py`)

Findings: `CHARGE_NE_OFFER`, `DELIVERED_NE_OFFER`, `NOT_RECEIVED`,
`MANDATE_IN_SCOPE` / `MANDATE_OUT_OF_SCOPE` (recomputed from the revealed
mandate + offer as of the `ORDER` timestamp), `UNVERIFIABLE` (names who owes
the reveal), `TAMPERED` (names who failed verification), `MISSING_SIG`,
`BAD_SIG`. Each finding carries `evidence: [event_ids]` and `party_id`.

Verdict table, in priority order:

1. any `TAMPERED` / `BAD_SIG` → against the tampering party
2. merchant finding + `MANDATE_IN_SCOPE` → `merchant_at_fault`, remedy = refund the charge
3. `MANDATE_OUT_OF_SCOPE` and merchant clean → `buyer_at_fault` (against the agent)
4. claim `not_authorised` + `MANDATE_IN_SCOPE` + merchant clean → `buyer_at_fault` (the mandate proves authorisation)
5. merchant finding + `MANDATE_OUT_OF_SCOPE` → `ambiguous`
6. anything `UNVERIFIABLE` and nothing conclusive → `unverifiable`
7. nothing → `ambiguous`

Opt-in rule 5b, *default judgment* (`TrailProofService(default_judgment=True)`):
when nothing is conclusive and every unverifiable finding that names a party
is owed by one side, the verdict goes against that side. The ledger holds that
side's signed commitment, so it had the plaintext and chose not to reveal it.
Events nobody ever recorded name nobody and stay `unverifiable`. Off by
default because it also punishes a party that is merely unavailable; see the
benchmark for why it exists.

## Merchant auto-refund policy (`mocks/shop.py`)

```python
POLICY = {"auto_refund_if": ["CHARGE_NE_OFFER", "DELIVERED_NE_OFFER", "NOT_RECEIVED"], "max_amount": 50.00}
```

On a dispute the shop reveals its own events, reads the bundle and refunds via
the PSP if every finding against it is in the list and the remedy is within
the cap; otherwise it rejects and either side may export the evidence pack.

## Brand recognition (`attest_history`)

The first use of the proof layer beyond disputes: a buyer agent proves to
brand Y, and only to Y, that its user is a verified repeat customer of Y, and
Y answers with a better, *committed* offer.

```
buyer agent  --attest_history(me, Y, audience=Y)-->  ledger
ledger       --signed {subject, merchant, audience, stats, issued_at}-->  buyer agent
buyer agent  --attestation (off-ledger)-->  Y
Y            verifies ledger_sig, audience == Y, merchant == Y, subject == this buyer, policy
Y            commits OFFER {..., total: discounted, recognition: {discount_pct, attestation_commit}}
```

**What is disclosed** (`stats`): `orders_completed` (orders with a `CHARGE`
and no `REFUND` whose `OFFER` was written by Y), `cases_lost` (cases on
those orders where the rules engine's verdict is `buyer_at_fault`), and the
`ledger_ts` of the first and last completed order. Everything is computed
from the hash chain and case verdicts, never from plaintext.

**What is not**: order ids, amounts, skus, history with any other merchant,
and anything about the user beyond its party id. The attestation names one
`audience`; a shop that receives one addressed to someone else, or about
history with someone else, ignores it. A copied attestation is useless to
another agent because the ledger's signature covers `subject` and the ORDER
on the new order must be signed by that subject's key.

**Verification** is offline: `crypto.verify_attestation(attestation,
ledger_public_key)` checks `ledger_sig = ed25519(ledger,
canonical_json(attestation without ledger_sig))` with the key every party
got from `register_party`. Any edit (a higher count, a new audience, a new
subject) breaks it; a self-signed forgery fails against the real key.

**Why the discount is enforceable**: the shop commits the discounted total
as an ordinary `OFFER` (with a `recognition` field citing the attestation by
hash, or `null`), the buyer's `check_order` refs that commit, and the PSP
charges against it. A charge at the public price is then a plain
`CHARGE_NE_OFFER` with `MANDATE_IN_SCOPE`, verdict `merchant_at_fault`,
remedy refund of the charge, exactly as in scenario 2. No new rule was
needed. The mock shop's policy:

```python
RECOGNITION_POLICY = {"min_orders": 2, "max_lost_cases": 0, "discount_pct": 10}
```

## Scenarios (`demo.py`, also `tests/test_scenarios.py`)

1. **Happy path** – pet-food mandate ≤ €40 → €36.90 offer → in scope → charge → fulfilment → delivery. No dispute.
2. **Merchant fault** – charged €42.90, 5 kg shipped → `CHARGE_NE_OFFER` + `DELIVERED_NE_OFFER` + `MANDATE_IN_SCOPE` → auto-refund €42.90 → `REFUND` closes the case.
3. **Friendly fraud** – all clean, user claims `not_authorised` → `buyer_at_fault` → merchant exports the evidence pack.
4. **Tampering** – merchant reveals an offer saying €42.90 while it committed €36.90 → reveal fails → `TAMPERED` → verdict against the merchant.
5. **Out of scope** – €55 cat toys under the pet-food mandate → `CATEGORY_NOT_ALLOWED` + `OVER_BUDGET`; the buyer aborts.
6. **Replay** – same mandate nonce for two €30 orders → the second fails on `OVER_BUDGET`.
7. **Recognised buyer** – a second buyer completes two clean orders, presents a ledger-signed attestation, gets a committed €33.21 offer (10 % off €36.90); the PSP charges €36.90 → `CHARGE_NE_OFFER` + `MANDATE_IN_SCOPE` → auto-refund. An unknown agent with no attestation, or a forged one, gets the public price.

Plus a client-side check that the scripted buyer fails open (`no_receipt=True`)
when the ledger is unreachable.

## Design decisions worth knowing

* **mcp 2.x API.** The installed SDK is 2.x, so the server uses
  `mcp.server.mcpserver.MCPServer` (FastMCP's successor). Tool handlers are
  `async` so every SQLite call stays on the event-loop thread.
* **Who reads a trail.** The brief says "parties to the order"; here any
  registered, non-revoked party may read a trail because a writer needs the
  current head to sign `prev` before it has written anything (the merchant's
  first `OFFER`). Trails contain hashes only.
* **Mandate per order.** The user's mandate is committed as the first event
  of every order it covers; replay protection comes from spend tracking per
  user key + nonce, not from one-mandate-one-order.
* **Reveals are sticky.** A failed reveal is recorded as `TAMPERED` evidence
  and cannot be overwritten with a second attempt.
* **Evidence pack callers.** §4 of the brief says merchant, §7 lets the buyer
  call it after a rejection; any party to the order may export.
* **No LLM anywhere** in the ledger or rules. The only model-driven component
  is the optional buyer below, which is a *client* of the ledger.

## Benchmark: dishonest counterparties (`benchmark/`)

`benchmark/bench_disputes.py` generates seeded disputes (overcharge, wrong
item, not shipped, out of scope, friendly fraud, baseless) where the party at
fault is honest, lies at reveal time, or withholds its records, runs each
through the real flow, and compares the verdict with the same rules applied to
unverified self-reports (TrailProof with commitments disabled). Honest cases:
both right. Lies: TrailProof right every time, the baseline never. Withholding
beat both until rule 5b. `benchmark/make_report.py` renders the write-up to
`benchmark/HW7_TrailProof_benchmark.pdf`.

```bash
uv run python benchmark/bench_disputes.py --seed 2 --n 120 --default-judgment
```

## Verifying a result offline

`export_evidence_pack` includes every event with its receipt, the public keys
of all parties, and the revealed plaintexts. `verify_pack.py` re-checks a pack
with no access to the ledger: ledger receipts, writer signatures, the hash
chain, and that each revealed payload hashes to its commitment.

```bash
uv run python demo_video.py --pace 0 --no-llm     # writes packs/<case_id>.json
uv run python verify_pack.py packs/<case_id>.json --tamper
```

## Optional: Claude as the buyer agent (stage 6)

`mocks/buyer_llm.py` hands the mandate and offer to Claude and gives it a small
tool surface (`get_trail`, `check_order`, `record_event`, `open_dispute`,
`done`). The wrappers hold the keys and sign; the model only decides. Writing
an `ORDER` through `record_event` instead of `check_order` is allowed on
purpose, and counted as a **skip**:

```bash
uv sync --extra llm                       # installs the anthropic SDK
uv run python -m mocks.buyer_llm --episodes 4
uv run python demo.py --llm 4             # or appended to the main demo
```

Needs credentials (`ANTHROPIC_API_KEY`, or a profile from `ant auth login`);
without them it says so and skips. Model defaults to `claude-opus-5-5`
(override with `TRAILPROOF_MODEL`); requests opt into server-side refusal
fallbacks. The default plan is four episodes: in scope, out of scope, and a
replay pair. The summary reports `check_order_used`, `check_order_skipped`,
`skip_rate`, `correct_outcomes` and `errors`. The loop is a plain manual
tool-use loop so the tests can drive it with a scripted fake client, no
network needed.

## Out of scope (by design)

Real PSPs, card-network submission, user passkeys (simulated with a user key
the buyer holds), reputation, REST, dashboards, Docker.
