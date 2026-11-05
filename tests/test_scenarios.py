"""Scenarios 1-7 as integration tests: in-process, and once more over real MCP stdio."""

import asyncio

import pytest

from mocks import scenarios as S
from mocks.transport import DirectToolClient, StdioToolClient
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService


def run_direct(fn):
    async def go():
        client = DirectToolClient(TrailProofService(Ledger(":memory:")))
        world = await S.World.create(client)
        return await fn(world)

    return asyncio.run(go())


def assert_checks(result):
    failed = [name for name, ok in result["checks"].items() if not ok]
    assert not failed, f"failed checks: {failed}"


def test_scenario_1_happy_path():
    r = run_direct(S.scenario_happy)
    assert_checks(r)
    assert len(r["trail"]["events"]) == 6


def test_scenario_2_merchant_fault_auto_refund():
    r = run_direct(S.scenario_merchant_fault)
    assert_checks(r)
    assert r["bundle"]["status"] == "closed"
    assert r["decision"]["refund"]["payload"]["amount"] == "42.90"


def test_scenario_3_friendly_fraud_evidence_pack():
    r = run_direct(S.scenario_friendly_fraud)
    assert_checks(r)
    pack = r["pack"]
    assert pack["reason_code"] == "fraudulent"
    assert "Premium dog food" in pack["product_description"]
    assert pack["shipping_documentation"]["carrier"] == "DHL"
    assert "buyer_at_fault" in pack["uncategorized_text"]
    assert pack["receipt"]["ledger_receipt"]["ledger_sig"]
    assert len(pack["trailproof"]["ledger_public_key"]) == 64


def test_scenario_4_tampering():
    r = run_direct(S.scenario_tampering)
    assert_checks(r)
    assert r["decision"]["action"] == "rejected"  # TAMPERED is not in the auto-refund list
    assert r["bundle"]["status"] == "open"


def test_scenario_5_out_of_scope():
    r = run_direct(S.scenario_out_of_scope)
    assert_checks(r)


def test_scenario_6_replay():
    r = run_direct(S.scenario_replay)
    assert_checks(r)
    assert [e["type"] for e in r["trail"]["events"]] == ["MANDATE", "OFFER"]


def test_scenario_7_recognised_buyer():
    r = run_direct(S.scenario_recognised)
    assert_checks(r)
    assert r["bundle"]["status"] == "closed"
    assert r["decision"]["refund"]["payload"]["amount"] == "36.90"
    assert r["recognitions"]["ord_leo_3"]["recognised"] is True
    assert r["recognitions"]["ord_stranger_1"]["recognised"] is False


def test_fail_open_when_server_unreachable():
    r = asyncio.run(S.scenario_fail_open())
    assert_checks(r)


def test_all_scenarios_over_real_mcp_stdio():
    """Spawn the real server once and run every scenario through MCP."""

    async def go():
        async with StdioToolClient() as client:
            assert set(client.tools) == {
                "register_party", "record_event", "check_order", "get_trail",
                "open_dispute", "reveal", "get_case_bundle", "export_evidence_pack", "attest_history",
            }
            world = await S.World.create(client)
            results = {}
            for num, title, fn in S.SCENARIOS:
                results[num] = await fn(world)
            return results, client.calls

    results, calls = asyncio.run(go())
    for num, r in results.items():
        failed = [name for name, ok in r["checks"].items() if not ok]
        assert not failed, f"scenario {num} failed checks: {failed}"
    assert "check_order" in calls and "export_evidence_pack" in calls and "attest_history" in calls
    assert 7 in results and results[7]["attestation"]["stats"]["orders_completed"] == 2
