# TrailProof for agents: interface guide

This is the documentation an AI agent (or the person configuring one) needs
to use TrailProof. It is written to be sufficient on its own.

## What TrailProof offers

TrailProof is shared infrastructure for purchases made by AI agents. It
gives a buyer agent three things:

1. **Authority it can show.** The agent buys only inside a *mandate* its
   user signed (category, budget, expiry). A neutral ledger checks every
   order against the mandate before recording it.
2. **A history it can prove.** The ledger issues a signed summary of the
   agent's past purchases, so a shop can recognise a real, returning
   customer and price accordingly. The summary contains counts only.
3. **A record that settles disputes.** Every step of an order is committed
   to the ledger as a signed hash. If a delivery is wrong, both sides
   reveal their records and deterministic rules decide.

Think of it as the cookie, rebuilt for agents: the customer holds it, it
cannot be faked, and it does not reveal where they shop.

## How an agent accesses it

Through MCP (Model Context Protocol) tools, over stdio. A buyer agent uses
one server; shops run their own.

| Server | Who runs it | Command |
|---|---|---|
| `trailproof-wallet` | the buyer: the user's own agent | `python -m wallet serve` |
| a shop's agent, e.g. `pawshop-demo` (a practice shop) | the merchant | `python -m demo_shop` |
| the ledger | shared, neutral | `python -m trailproof.server --http` |

Both the wallet and the shop talk to the ledger themselves. A buyer agent
never calls the ledger directly; it calls its wallet.

### Setup (done once, by the person, not by the agent)

```bash
git clone https://github.com/Andreyivvi/TrailProof && cd TrailProof && uv sync
uv run python -m wallet ledger                                   # terminal 1: the ledger, port 8765
uv run python -m wallet init --name mia                          # terminal 2: create the wallet
uv run python -m wallet mandate add --category pet_food --max 80 --days 30
uv run python -m wallet install                                  # prints the MCP config for the agent
```

Environment variables: `TRAILPROOF_WALLET` (wallet file, default
`~/.trailproof/wallet.json`), `TRAILPROOF_URL` (ledger URL for a shop,
default `http://127.0.0.1:8765/mcp`), `TRAILPROOF_SHOP_STATE` (the practice
shop's key file).

## Permissions and limits

- **An agent cannot create, widen or revoke a mandate.** Only the person
  can, on the command line. If no mandate fits, say so and tell the user to
  run `python -m wallet mandate add --category <c> --max <amount>`.
- **The ledger refuses orders outside the mandate**: wrong category, over
  the remaining budget, expired, wrong currency, or an offer that does not
  match what the shop committed. Refusals come back with reasons.
- **A proof works at one shop.** It is addressed to the shop named as
  `audience_party_id` and is rejected anywhere else.
- **Proofs disclose counts only**: completed orders, lost disputes, first
  and last order time. Never products, amounts or shop names.
- Amounts are strings with two decimals (`"36.90"`). One currency per order.
- Every tool returns a JSON object. A failure is a value, not an exception:
  `{"error": "CODE", "detail": "..."}`. Read the detail; it says what to do.
- The ledger must be running. If it is not, tools return
  `LEDGER_UNREACHABLE`.

## Wallet tools (`trailproof-wallet`)

| Tool | Input | Returns |
|---|---|---|
| `wallet_status` | none | `name`, `agent_party_id`, `user_party_id`, `mandates`, `orders_placed`, `open_cases` |
| `list_mandates` | `active_only` (bool, default true) | `mandates`: each with `mandate_id`, `categories`, `max_amount`, `spent`, `remaining`, `currency`, `valid_until`, `status` |
| `prove_history` | `audience_party_id` (the shop that will read it), `merchant_party_id` (default `"*"`) | a signed proof object: `subject`, `merchant`, `audience`, `stats`, `issued_at`, `ledger_sig`. Pass it on unchanged. |
| `start_order` | `mandate_id` | `order_id`, `mandate_commit`, `buyer_agent_party_id`, `allowed` (categories, remaining budget) |
| `place_order` | `order_id`, `offer` (the object the shop returned), `offer_commit` | `ordered` (bool), and either `total` and `remaining_under_mandate`, or `reasons` |
| `confirm_delivery` | `order_id`, `received` (bool), `sku`, `qty`, `unit`, `note` | `receipt`, `as_offered` (bool) |
| `open_dispute` | `order_id`, `claim` (`not_as_described`, `not_received` or `not_authorised`), `note` | `case_id`, `revealed`, `merchant_party_id` |
| `case_status` | `case_id` | `status`, `verdict`, `remedy`, `findings` |
| `order_trail` | `order_id` | the ledger's record of the order: each step with its hash and time |

`prove_history` has two modes. With `merchant_party_id` set to a shop's own
party id, the proof covers the history *with that shop*. With `"*"` it
covers every shop and names none.

## Shop tools (the practice shop `pawshop-demo`)

| Tool | Input | Returns |
|---|---|---|
| `shop_info` | none | `shop_party_id`, `products` (sku, title, category, unit, price), `loyalty`, `refund_policy` |
| `get_offer` | `order_id`, `sku`, `buyer_agent_party_id`, `proof` (optional) | `offer`, `offer_commit`, `recognised` (bool), `why`, `public_price` |
| `pay_and_ship` | `order_id`, `ship_wrong_item` (bool, for trying a dispute) | `charged`, `currency`, `parcel` (sku, qty, unit, tracking) |
| `respond_to_dispute` | `case_id`, `order_id` | `action` (`refunded` or `rejected`), `amount`, `verdict` |

## How an order goes

An order is opened by the buyer's wallet, which commits the user's mandate
and returns an order id. The shop commits an offer behind that order. The
wallet then asks the ledger to check the offer against the mandate and
record the order. Payment and shipment follow on the shop's side, and the
buyer commits what actually arrived. A dispute, if needed, is opened by the
buyer and answered by the shop.

## One small example

Finding out what the agent may spend, and getting a proof for a shop:

```
list_mandates {}
  -> {"mandates": [{"mandate_id": "mnd_87d88718", "categories": ["pet_food"],
       "max_amount": "80.00", "spent": "0.00", "remaining": "80.00",
       "currency": "EUR", "valid_until": "2026-12-12T...", "status": "active"}]}

shop_info {}
  -> {"shop_party_id": "pty_c380154909b3", "products": [...], "loyalty": "..."}

prove_history {"audience_party_id": "pty_c380154909b3"}
  -> {"subject": "pty_d031efa9b5c2", "merchant": "*", "audience": "pty_c380154909b3",
      "stats": {"orders_completed": 0, "cases_lost": 0, "first_order_ts": null,
                "last_order_ts": null, "merchants": 0},
      "issued_at": "2026-11-12T...", "ledger_sig": "9f2c..."}
```
