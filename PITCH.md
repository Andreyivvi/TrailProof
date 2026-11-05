# TrailProof pitch: identification first, disputes second

Character counts for the three form texts are checked by `python - <<EOF` in
the project (see the bottom of this file); keep edits within the limits.

## 90-second pitch (spoken, about 230 words)

Every brand is about to meet a new customer: an AI agent. It arrives without a face, without a login, without a loyalty card. Is it a real buyer with a history, or one of a thousand bots scraping prices? Today a brand cannot tell, so it shows the same price to everyone and loses the relationship it spent years building.

TrailProof gives agents something to show. It is a neutral ledger, exposed as an MCP server, so agents are the users. At every step of a purchase, the user's signed mandate, the offer, the order, the charge, the delivery, each party locks a signed fingerprint of what it saw. From that history the ledger issues an attestation: this agent's user has completed four verified orders with your brand and lost no disputes. Counts only, addressed to that brand alone, nothing about what was bought.

A brand that trusts the attestation offers a direct, recognised-customer price. A bot with no history, or a forged attestation, gets the public price. We showed this live, with a Claude agent buying under a real mandate, and the ledger refusing an order the user never authorised.

And because every step was committed, promises are enforceable. When the payment provider charged the public price instead of the recognised one, the dispute settled from the commitments in seconds, with an automatic refund and an evidence pack anyone can verify offline.

Brands pay per recognised returning customer. Payment providers pay per order for the dispute layer. Whoever sets the standard for proving who is behind an agent becomes the rail.

Evidence agreed before the fight. That is TrailProof.

## Form text 1: What problem are you trying to solve? (max 1,500 characters)

AI agents are becoming the customer. People tell an agent "buy the dog food from brand Y" and the agent shops, compares and pays. For a brand this is a new visitor it cannot read: no login, no cookie, no loyalty card, and no way to tell a returning customer's agent from one of a thousand bots scraping prices. The result is that brands show everyone the public price, lose the direct relationship they spent years building, and get commoditised by agents that only see the cheapest offer. Merchants already report floods of agent traffic they cannot classify. The second half of the problem follows from the first: when something goes wrong with an agent purchase, nobody holds neutral evidence. The merchant has its own log, the buyer has screenshots, and a new dispute type appears that never existed before: "my agent was not allowed to buy this." Forecasts put agent-driven commerce at 1.5 to 5 trillion dollars by 2030, which at today's rates means 30 to 60 million disputed agent purchases a year, under liability rules that Visa, Mastercard and the CFPB have confirmed exist but have not yet written for agents. What is missing is a way for a buyer agent to prove who it acts for and what it has done, without giving away its user's privacy, and a record of what each party committed to, made before anyone knows there will be a fight.

## Form text 2: Solution and monetisation (max 2,000 characters)

TrailProof is the proof layer for agent purchases: a neutral, append-only ledger exposed as an MCP server, so agents are the users. At every step of an order (the user's signed mandate, the merchant's offer, the order, the charge, fulfilment, delivery) each party commits a signed SHA-256 fingerprint of what it saw. Plaintext stays with the party; the ledger countersigns and timestamps. Before an order is recorded, the ledger checks it against the user's mandate, so a merchant knows the agent acts within what a real person authorised.

From that history the ledger issues attestations: "this agent's user has N verified completed orders with your brand and lost no disputes", counts only, addressed to one brand, nothing about what was bought. The brand verifies the ledger's signature and responds with a recognised-customer offer, itself committed and therefore enforceable. Bots with no history, and forged attestations, get the public price. When a dispute does arise, each side reveals its plaintext, the ledger verifies it against the commitments, and a deterministic rules engine returns findings, a verdict and an auto-refund or a chargeback-ready evidence pack that anyone can re-verify offline. The MVP runs end to end today, including a live Claude agent buying through the tools.

Monetisation: (1) Brand recognition, our first revenue: brands pay per recognised returning customer or per converted order, replacing loyalty and CRM spend that agent commerce otherwise strips away, with bot filtering included. (2) A dispute layer for payment providers and marketplaces, priced per committed order or per case. (3) The standard itself: whoever defines how an agent proves who it acts for becomes a required rail, as 3-D Secure did for liability shift; Visa's purchase of Verifi and Mastercard's purchase of Ethoca show the exit path.

## Form text 3: Elevator pitch (max 700 characters)

TrailProof is the proof layer for AI-agent purchases. Buyer agents, merchants and payment providers commit signed fingerprints of every order step to a neutral ledger. From that history the ledger issues privacy-preserving attestations, so a brand can tell a real returning customer's agent from a bot and give it a direct, enforceable price. When a purchase goes wrong, the dispute settles from the commitments: verdict, auto-refund, or an evidence pack anyone can verify offline. Agents use it through MCP; a live Claude agent buys through it today. Brands pay per recognised customer; payment providers pay per order.

## Character check

```bash
uv run python -c "import re,io;t=open('PITCH.md',encoding='utf-8').read();secs=re.split(r'^## ',t,flags=re.M);[print(s.splitlines()[0][:40],len('\n'.join(s.splitlines()[2:]).strip())) for s in secs if s.startswith('Form text')]"
```
