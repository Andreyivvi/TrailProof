# TrailProof pitch: the cookie, rebuilt for AI agents

Framing adopted after presentation feedback: TrailProof is to agent commerce
what the cookie was to the web, with the three things a cookie gets wrong
fixed. The customer holds it, it cannot be faked, and it cannot be used to
track them. Disputes are the reason it can be trusted, not a second product.

## One line

A cookie for AI agents that the customer holds, that cannot be faked, and that cannot be used to track them.

## 60-second narration for `presentation/index.html`

| Time | On screen | Say |
|---|---|---|
| 0:00 | "A cookie for AI agents" | "On the web, a cookie lets a shop recognise you. Your AI agent has no cookie, so every shop treats it as a stranger or a bot. TrailProof gives it one." |
| 0:04 | Visitor 1, recognised | "Leo's agent returns to a pet shop and shows proof of its history, signed by a neutral ledger. The shop commits a loyalty price." |
| 0:12 | Visitor 2, a second shop | "The same agent at a shop it has never visited. Its proof travels: a clean record, without saying where Leo shops. It gets a welcome price, and the proof works at this one shop only." |
| 0:20 | Visitor 3, scraper | "A scraper bot has nothing to show. It sees the public price and cannot order." |
| 0:28 | Visitor 4, impostor | "An impostor copies Leo's proof and swaps in its own name. Unlike a cookie, it cannot be copied: the signature breaks." |
| 0:36 | Visitor 5, no mandate | "An agent nobody authorised tries to buy. No mandate from a human, no purchase." |
| 0:44 | Visitor 6, wrong product | "And because every step was committed, when the shop ships the wrong product the dispute settles itself, with an automatic refund." |
| 0:52 | "Can anyone check this?" | "Anyone can verify the evidence without us. Change one number and it fails." |
| 0:58 | "The cookie, rebuilt for agents" | "The customer holds it, it cannot be faked, it cannot track you." |

## 90-second pitch (spoken, about 230 words)

The web runs on a small trick called the cookie. It lets a shop recognise you when you come back: your basket, your loyalty price, your history. Now people are sending AI agents to shop for them, and an agent has no cookie. To a merchant, every agent is a stranger. Is it a loyal customer, or one of a thousand bots scraping prices? It cannot tell, so everyone gets the same price and the customer relationship is gone.

TrailProof is the cookie, rebuilt for agents, with the three things a cookie gets wrong fixed.

First, the customer holds it. It is free, it lives with their agent, and it works at any shop.

Second, it cannot be faked. A cookie is a claim anyone can copy. Ours is a proof, signed by a neutral ledger and backed by real purchases the ledger has recorded step by step. In our demo an impostor copies it and is caught in one check.

Third, it cannot track you. The agent shows a shop only counts: two completed orders, no lost disputes. Never what was bought, never where. And each proof works at one shop only.

Because every purchase step is committed, the same record does a second job: when an order goes wrong, the dispute settles from proof, with an automatic refund.

It runs today, as an MCP server with a live Claude agent buying through it. Customers get it free. Merchants pay per recognised customer, the money they already spend on loyalty.

TrailProof: the cookie, rebuilt for agents.

## Form text 1: What problem are you trying to solve? (max 1,500 characters)

The web's customer relationships run on the cookie: it lets a shop recognise a returning visitor and offer the basket, the loyalty price, the history. People are now sending AI agents to shop for them, and an agent has no cookie. It arrives without a login, a browser or a loyalty card. To a merchant every agent looks the same, so it cannot tell a loyal customer's agent from one of a thousand bots scraping prices. The result: everyone gets the public price, loyalty programmes stop working, and brands are reduced to whoever is cheapest. Customers lose too, because the loyalty they earned does not follow their agent. The obvious fix, giving agents a cookie, repeats the cookie's three flaws. A cookie is set and owned by the merchant, not the customer. It is only a claim, so it can be copied or forged, which matters far more when the visitor is software. And it tracks people across sites, which regulators are already dismantling. On top of this, agent purchases create disputes nobody has evidence for: "my agent was not allowed to buy this", or a wrong delivery where the merchant's log is the only record. Forecasts put agent-driven commerce at 1.5 to 5 trillion dollars by 2030. That trade needs a way for an agent to prove who it acts for and how it has behaved, that the customer controls, that cannot be faked, and that does not become a tracking tool.

## Form text 2: Solution and monetisation (max 2,000 characters)

TrailProof is the cookie, rebuilt for AI agents. A customer's agent carries proof of its purchase history and shows it to merchants to be recognised, at shops it knows and at shops it has never visited. Three things make it better than a cookie. The customer holds it: it is free, lives with their agent, and works across merchants. It cannot be faked: it is not a claim but an attestation signed by a neutral ledger, computed from purchases the ledger itself recorded; a copied or edited proof fails its signature. It cannot track: it discloses counts only ("12 completed orders, no lost disputes"), never what was bought or where, and each proof is addressed to one merchant, so it cannot be replayed elsewhere.

Underneath is an append-only ledger, exposed as an MCP server so agents use it directly. At each step of an order (the user's signed mandate, the offer, the order, the charge, the delivery) each party commits a signed fingerprint; plaintext stays with the party. That gives two more guarantees. An agent cannot buy without a mandate signed by its user, so merchants know a real person stands behind it. And when a purchase goes wrong, both sides reveal, the ledger checks the reveals against the commitments, and deterministic rules decide: automatic refund, or an evidence pack anyone can verify offline. The MVP runs end to end today, with a live Claude agent buying through it and an integration in the Nanda Town sandbox.

Monetisation: free for customers and their agents, which drives adoption. Merchants pay (1) per recognised customer or per converted order, replacing loyalty and CRM spend that agent commerce otherwise destroys, with bot filtering included; (2) for loyalty features on top: points and redemptions that vest only on completed, undisputed orders; (3) a per-order fee from payment providers and marketplaces for dispute evidence. Long term, whoever defines how an agent proves its identity and history becomes a required rail, as 3-D Secure did for payments.

## Form text 3: Elevator pitch (max 700 characters)

TrailProof is the cookie, rebuilt for AI agents. On the web a cookie lets a shop recognise a returning customer; an AI agent has none, so merchants cannot tell a loyal customer from a bot. TrailProof gives the customer's agent a free proof of its purchase history that works at any shop. Unlike a cookie, the customer holds it, it cannot be faked (it is signed by a neutral ledger and backed by real purchases), and it cannot be used for tracking (counts only, one shop at a time). The same record settles disputes automatically. It runs today as an MCP server with a live Claude agent. Merchants pay per recognised customer.
