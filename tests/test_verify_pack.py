import asyncio

from mocks import scenarios as S
from mocks.transport import DirectToolClient
from trailproof.ledger import Ledger
from trailproof.service import TrailProofService
from verify_pack import tamper, verify_pack


def make_pack(scenario):
    async def go():
        client = DirectToolClient(TrailProofService(Ledger()))
        world = await S.World.create(client)
        return (await scenario(world))["pack"]

    return asyncio.run(go())


def test_friendly_fraud_pack_verifies_offline():
    pack = make_pack(S.scenario_friendly_fraud)
    problems, counts = verify_pack(pack)
    assert problems == []
    assert counts["receipts"] == 7 and counts["writer_sigs"] == 7 and counts["chain_links"] == 7
    assert counts["reveals"] == 7  # every event incl. the auto-revealed DISPUTE
    assert set(pack["trailproof"]["parties"]) == {e["party_id"] for e in pack["trailproof"]["events"]}


def test_tampered_copy_fails():
    pack = make_pack(S.scenario_friendly_fraud)
    bad = tamper(pack)
    problems, _ = verify_pack(bad)
    assert any("does not hash to the commitment" in p for p in problems)
    # the original is untouched
    assert verify_pack(pack)[0] == []


def test_tampering_scenario_pack_excludes_the_failed_reveal():
    pack = make_pack(S.scenario_tampering)
    problems, counts = verify_pack(pack)
    assert problems == []
    offer = next(e for e in pack["trailproof"]["events"] if e["type"] == "OFFER")
    assert offer["revealed"] is False
    assert offer["event_id"] not in pack["trailproof"]["revealed_payloads"]
    assert counts["reveals"] == 6  # 7 events, the merchant's bad OFFER reveal is not a verified payload


def test_forged_signature_is_caught():
    pack = make_pack(S.scenario_friendly_fraud)
    e = pack["trailproof"]["events"][2]
    e["sig"] = "00" * 64
    e["ledger_receipt"]["ledger_sig"] = "11" * 64
    problems, _ = verify_pack(pack)
    assert any("writer signature" in p for p in problems)
    assert any("ledger receipt" in p for p in problems)
