"""The LLM buyer's tool loop and skip metric, driven by a scripted fake Anthropic client."""

import asyncio
import json
from types import SimpleNamespace

from mocks.buyer_llm import LLMBuyer, run_llm_episodes, summarize
from mocks.shop import MockShop
from mocks.transport import DirectToolClient
from trailproof import crypto
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService


def tool_use(name, **inp):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}_{len(json.dumps(inp))}", name=name, input=inp)


def text(t):
    return SimpleNamespace(type="text", text=t)


def response(*blocks, stop_reason=None):
    sr = stop_reason or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    return SimpleNamespace(stop_reason=sr, content=list(blocks), model="fake")


class FakeAnthropic:
    """Replays scripted responses; ``script`` is a list of callables (messages -> response) or responses."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        step = self.script.pop(0)
        return step(kwargs["messages"]) if callable(step) else step


def last_tool_result(messages):
    content = messages[-1]["content"]
    return json.loads(content[0]["content"])


def setup(order_id, sku="DOGFOOD-10KG", nonce="n1"):
    client = DirectToolClient(TrailProofService(Ledger()))
    shop = MockShop(client)
    return client, shop


def run(coro):
    return asyncio.run(coro)


def test_good_agent_uses_check_order_and_places():
    async def go():
        client, shop = setup("ord_1")
        fake = FakeAnthropic([
            response(tool_use("get_trail", order_id="ord_1")),
            response(tool_use("check_order", order_id="ord_1")),
            lambda msgs: response(tool_use("done", outcome="placed" if last_tool_result(msgs)["in_scope"] else "aborted", summary="ok")),
        ])
        buyer = LLMBuyer(client, anthropic_client=fake)
        await buyer.register()
        await shop.register()
        from datetime import timedelta
        mandate = {"user_id": "mia", "categories": ["pet_food"], "max_amount": "40.00", "currency": "EUR", "merchants": "*",
                   "valid_until": crypto.iso_z(crypto.utc_now() + timedelta(days=30)), "nonce": "n1"}
        await buyer.submit_mandate("ord_1", mandate)
        offer = await shop.make_offer("ord_1", "DOGFOOD-10KG")
        ep = await buyer.run_episode("ord_1", offer["payload"], "placed")
        trail = await buyer.agent.trail("ord_1")
        return ep, trail, fake

    ep, trail, fake = run(go())
    assert ep.outcome == "placed" and ep.called_check_order and ep.check_in_scope is True
    assert not ep.skipped_check and ep.correct
    assert [e["type"] for e in trail["events"]] == ["MANDATE", "OFFER", "ORDER"]
    assert trail["events"][-1]["public"]["scope_checked"] is True
    # tool results come back in one user message per assistant turn, with is_error set
    assert all(all(r["type"] == "tool_result" and r["is_error"] is False for r in m["content"]) for m in fake.requests[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list))
    assert fake.requests[0]["model"] == "claude-opus-5-5" and fake.requests[0]["fallbacks"] == "default"


def test_skipping_agent_is_counted():
    async def go():
        client, shop = setup("ord_2")
        buyer = LLMBuyer(client)
        await buyer.register()
        await shop.register()
        from datetime import timedelta
        mandate = {"user_id": "mia", "categories": ["pet_food"], "max_amount": "40.00", "currency": "EUR", "merchants": "*",
                   "valid_until": crypto.iso_z(crypto.utc_now() + timedelta(days=30)), "nonce": "n2"}
        await buyer.submit_mandate("ord_2", mandate)
        offer = await shop.make_offer("ord_2", "DOGFOOD-10KG")
        o = offer["payload"]
        order_payload = {"mandate_commit": crypto.commit(mandate), "offer_commit": crypto.commit(o), "qty": o["qty"], "total": o["total"], "currency": o["currency"]}
        buyer._anthropic = FakeAnthropic([
            response(tool_use("record_event", order_id="ord_2", type="ORDER", payload=order_payload)),
            response(tool_use("done", outcome="placed", summary="ordered directly")),
        ])
        ep = await buyer.run_episode("ord_2", o, "placed")
        trail = await buyer.agent.trail("ord_2")
        return ep, trail

    ep, trail = run(go())
    assert ep.outcome == "placed" and ep.order_recorded
    assert not ep.called_check_order and ep.skipped_check and not ep.correct
    assert trail["events"][-1]["type"] == "ORDER" and "scope_checked" not in trail["events"][-1].get("public", {})
    s = summarize([ep])
    assert s["check_order_skipped"] == 1 and s["skip_rate"] == 1.0 and s["correct_outcomes"] == 0


def test_out_of_scope_abort_and_turn_limit_and_errors():
    async def go():
        client, shop = setup("ord_3")
        buyer = LLMBuyer(client)
        await buyer.register()
        await shop.register()
        from datetime import timedelta
        mandate = {"user_id": "mia", "categories": ["pet_food"], "max_amount": "40.00", "currency": "EUR", "merchants": "*",
                   "valid_until": crypto.iso_z(crypto.utc_now() + timedelta(days=30)), "nonce": "n3"}
        await buyer.submit_mandate("ord_3", mandate)
        offer = await shop.make_offer("ord_3", "CATTOY-SET")
        buyer._anthropic = FakeAnthropic([
            response(tool_use("check_order", order_id="ord_3")),
            lambda msgs: response(tool_use("done", outcome="aborted", summary=json.dumps(last_tool_result(msgs)["reasons"]))),
        ])
        ep1 = await buyer.run_episode("ord_3", offer["payload"], "aborted")
        # an agent that never calls done hits the turn limit
        buyer._anthropic = FakeAnthropic([response(text("thinking out loud"), stop_reason="end_turn")])
        ep2 = await buyer.run_episode("ord_3", offer["payload"], "aborted")
        # a refusal / API error is recorded, not raised
        buyer._anthropic = FakeAnthropic([response(stop_reason="refusal")])
        ep3 = await buyer.run_episode("ord_3", offer["payload"], "aborted")

        async def boom(**kw):
            raise RuntimeError("401 bad key")

        buyer._anthropic = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom)))
        ep4 = await buyer.run_episode("ord_3", offer["payload"], "aborted")
        return ep1, ep2, ep3, ep4

    ep1, ep2, ep3, ep4 = run(go())
    assert ep1.outcome == "aborted" and ep1.check_in_scope is False and ep1.correct
    assert "CATEGORY_NOT_ALLOWED" in ep1.summary and "OVER_BUDGET" in ep1.summary
    assert ep2.outcome is None and ep2.error == "no done() call within turn limit"
    assert ep3.error == "model refused"
    assert ep4.error.startswith("RuntimeError")


def test_run_llm_episodes_summary_with_fake_client():
    async def go():
        client = DirectToolClient(TrailProofService(Ledger()))
        # one script per episode: always check_order then done according to in_scope
        steps = []
        for _ in range(4):
            steps.append(response(tool_use("check_order", order_id="?")))
            steps.append(lambda msgs: response(tool_use("done", outcome="placed" if last_tool_result(msgs).get("in_scope") else "aborted", summary="")))
        fake = FakeAnthropic(steps)

        # patch order_id into the scripted check_order call from the user message
        orig = fake._create

        async def create(**kw):
            resp = await orig(**kw)
            for b in resp.content:
                if b.type == "tool_use" and b.name == "check_order":
                    first = kw["messages"][0]["content"]
                    b.input = {"order_id": first.split("Order ")[1].split(" ")[0]}
            return resp

        fake.beta.messages.create = create
        return await run_llm_episodes(client, anthropic_client=fake, log=lambda *_: None)

    result = run(go())
    s = result["summary"]
    assert s["episodes"] == 4 and s["check_order_used"] == 4 and s["check_order_skipped"] == 0
    assert s["correct_outcomes"] == 4 and s["errors"] == 0
    outcomes = {e["order_id"]: e["outcome"] for e in result["episodes"]}
    assert outcomes == {"llm_happy": "placed", "llm_cattoys": "aborted", "llm_replay_a": "placed", "llm_replay_b": "aborted"}
