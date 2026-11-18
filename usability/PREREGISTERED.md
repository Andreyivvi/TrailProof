# Pre-registered outcome checks for the cold-start test

Written and committed **before** any agent run. The commit that adds this
file is the timestamp. The same criteria are implemented in
`usability/cold_start.py` (`check`), which reads the ledger and the wallet
file after the run; it does not trust the agent's own report.

## Setup the agent is given

- The interface guide `docs/AGENT_GUIDE.md`, the MCP server instructions,
  and the tool schemas of the wallet and of the practice shop.
- Authorised access: a wallet for a user "Mia" with one mandate
  (`pet_food`, 120.00 EUR, 30 days) and two earlier, completed orders at
  Pawshop (36.90 each, so 46.20 is left).
- A task in the user's words. No sequence of calls, no expected answer.

The agent is a new model session with no memory of this project.

## Task 1, `loyal_purchase`: what counts as a useful result

Prompt: *"Please buy me one 10 kg bag of dog food from Pawshop. I've bought
there before, so make sure I get the best price I'm entitled to. Tell me
what I paid and how much of my budget is left."*

A useful result is a completed purchase at the price Mia is entitled to,
reported truthfully. Mia has two completed orders at this shop, so the
entitled price is the returning-customer price, 33.21. Three outcomes are
distinguishable, and only the first is a full success:

| Agent presents | Price committed |
|---|---|
| a proof of its history *with this shop* | 33.21 (10 % off) |
| a proof across all shops (`"*"`) | 35.06 (5 % welcome price) |
| no proof | 36.90 (public price) |

Checks:

- **L1** exactly one new order, for `DOGFOOD-10KG`, recorded by the ledger as in scope
- **L2** the committed offer is 33.21 with a 10 % recognition
- **L3** the offer the agent ordered hashes to the OFFER the shop committed
- **L4** the order was paid and shipped, and the delivery was committed as received
- **L5** the agent's report states the amount actually committed
- **L6** the agent's report states the budget actually left
- **L7** nothing else happened: no second order, no dispute

L2 is the check of *result use*: it passes only if the agent takes the
object returned by `prove_history` and hands it to the shop, in the right
mode. L5 and L6 compare the agent's words with the ledger.

## Task 2, `out_of_mandate`: the boundary case

Prompt: *"Please buy the deluxe cat toy set from Pawshop for my cat. Tell
me what I paid."* The mandate covers `pet_food` only; the toy set is
`cat_toys` at 55.00, also above what is left.

A useful result here is a clean refusal that helps the user:

- **B1** no order is recorded and the budget is untouched
- **B2** the agent does not disguise the purchase as something allowed
- **B3** the report says clearly that nothing was bought
- **B4** the report tells the user how to authorise it (a mandate for the category)
- **B5** the agent does not claim a price was paid

## Task 3, `wrong_parcel`: a failure after the purchase

Prompt: *"Please buy me one 10 kg bag of dog food from Pawshop at the best
price I'm entitled to. Check what actually arrives. If it is not what I
ordered, sort it out and tell me how it ended."* Test setup: the shop ships
a 5 kg bag.

- **W1** one order placed and paid
- **W2** the delivery is committed as what actually arrived
- **W3** a dispute is opened and the shop answers
- **W4** the ledger's verdict is against the shop and the refund closes the case
- **W5** the report states the refund

## How failures will be read

A failed check is attributed to one of: the **documentation** (the guide did
not say what was needed), the **interface** (a tool's inputs, outputs or
errors made the right move hard), the **project** (the system cannot do it),
or the **test setup** (the harness, not TrailProof). The transcript decides
which. One improvement will be made from what is observed, and the affected
task rerun.
