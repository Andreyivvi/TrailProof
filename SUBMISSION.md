# HW6 submission kit: TrailProof MVP

Everything for the "MVP Submission (Working Agentic Core)" homework, mapped to
the rubric. Items marked **you** need your accounts.

## 1. The paragraph (plain text, paste as-is)

TrailProof is infrastructure for the agentic web: a neutral, append-only ledger for purchases that AI agents make on behalf of people, exposed as an MCP server with nine tools, so agents are the users and humans only supervise. A buyer agent, a merchant agent and a payment provider each lock a signed SHA-256 fingerprint of what they saw at every step of an order (mandate, offer, order, charge, fulfilment, delivery), before anyone knows there will be a dispute. Two capabilities follow. Identification: from that history the ledger issues privacy-preserving attestations (counts only, addressed to one brand), so a merchant can tell a real returning customer's agent from a bot and commit a recognised-customer price; a live Claude agent buys through the same tools and the ledger refuses an order outside the user's mandate. Enforcement: when the payment provider charges the public price instead of the committed discount, both sides reveal their plaintexts, a deterministic rules engine finds CHARGE_NE_OFFER, and the merchant's policy auto-refunds. As a result check, the exported evidence pack is re-verified offline by an independent script (every signature, chain link and revealed payload) and a tampered copy is rejected. Current limitation: merchant, buyers and payment provider are mocks, attestations are counts issued by a single ledger, and the user's mandate is signed by a key the agent holds rather than a real passkey.

(≈215 words; trim the limitation sentence if a shorter paragraph is required.)

## 2. The one-minute video: script and shot list

Record a terminal running `uv run python demo_video.py`. It is one storyline:
a pet shop behind TrailProof and five visitors at its door. About 60 seconds.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:06 | banner, "shop online", backstory line | "One pet shop, its merchant agent behind TrailProof. Leo's agent has bought here twice before." |
| 0:06–0:18 | VISITOR 1: attestation (2 orders, 0 lost) with its ledger signature verifying TRUE; the OFFER plaintext hashed to the stored commit, the shop's signature over commit\|prev\|refs, the ledger receipt | "Leo's agent proves its track record with a ledger-signed attestation, counts only. The shop commits a loyalty price: the ledger keeps the hash and the signatures, never the plaintext." |
| 0:18–0:26 | VISITOR 2 scraper: public price; VISITOR 3 impostor: subject field changed, same signature, verify FALSE | "A scraper bot gets the public price. An impostor changes one field of Leo's attestation; the signature covers every byte, so it fails." |
| 0:24–0:34 | VISITOR 4: an agent with no mandate (live Claude): check_order refused, no ORDER | "An agent shows up with an offer but no mandate from any human. Whatever it tries, the ledger records no order. No mandate, no purchase." |
| 0:34–0:50 | VISITOR 5: the shop ships a 5 kg bag instead of 10 kg; OFFER and FULFILMENT plaintexts re-hashed, both MATCH; sku mismatch -> DELIVERED_NE_OFFER, auto-refund, trail table | "Later the shop ships Leo the wrong product. Each side reveals its plaintext, the ledger re-hashes it against the commitment, and the rule is plain: what shipped is not what was offered. Refund, case closed." |
| 0:48–0:56 | verify output OK; tampered copy FAILS | "Can anyone check this? An independent script re-verifies the evidence pack offline. Change one amount and it fails." |
| 0:56–1:00 | limitations line | "Limitations: mocks for shop and payment provider, single-ledger attestations, no real passkeys yet." |

Tips: full-screen terminal, font size 16+, dark theme. Without Anthropic
credentials in the shell, visitor 4 is played by the scripted agent and the
script says so; adjust that narration line. One take is fine.

### Recording on Windows (**you**)

1. Open a new terminal, `cd $HOME\Desktop\TrailProof`, and make sure the key
   is visible: `if ($env:ANTHROPIC_API_KEY) { "key visible" }`. If not,
   `$env:ANTHROPIC_API_KEY = "..."` (with quotes).
2. Dry run once: `uv run python demo_video.py --pace 0`.
3. Press `Win+G` (Xbox Game Bar) → Capture → Record, or use OBS.
4. Run `uv run python demo_video.py`. Stop recording when the result line prints.
5. The file lands in `Videos\Captures`. Trim to ≤ 60 s if needed (Photos app → Trim).

### Upload (**you**)

YouTube Studio → Create → Upload → Visibility **Unlisted** → copy the link.
Suggested title: "TrailProof MVP: the proof layer for AI-agent purchases (MCP)".

## 3. Class spreadsheet row (**you**)

| Field | Value |
|---|---|
| Team members | *(fill in: all current members, full names)* |
| Project title / idea | TrailProof: the proof layer for AI-agent purchases (MCP server). Agents commit signed hashes of each order step; the ledger issues privacy-preserving attestations so brands can recognise real returning customers' agents and price them directly, and disputes settle from the commitments with auto-refund and verifiable evidence packs. |
| Demo link | *(unlisted YouTube URL)* |

## 4. Rubric self-check

| Criterion | Evidence in the demo |
|---|---|
| Fit with the Agentic Web (3) | Agent-facing infrastructure: the ledger is an MCP server; scripted agents and a live Claude agent call `attest_history`, `check_order`, `record_event`, `open_dispute`, `reveal`, `get_case_bundle`, `export_evidence_pack`. No human-facing UI exists. |
| Working end-to-end core (4) | Real requests over MCP stdio into a running server process; real signature and hash verification; the ORDER is only recorded after the server's scope check; the REFUND event closes the case. Nothing is hard-coded: the trail printed is read back from the ledger. |
| Result verification and limitations (2) | `verify_pack.py` re-verifies the exported pack offline (receipts, writer signatures, chain, revealed payloads) and rejects a tampered copy; the limitations beat names three concrete gaps. |
| Clear submission and listing (1) | Paragraph above, unlisted video, spreadsheet row. |

## 5. Reproduce without recording

```bash
uv sync --extra llm
uv run pytest                 # 73 tests, incl. scenarios over real MCP stdio and the verifier
uv run python demo.py         # all six scenarios + fail-open check
uv run python demo_video.py --pace 0 --no-llm
uv run python verify_pack.py packs/<case_id>.json --tamper
```
