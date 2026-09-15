import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, gl,
)


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


class TestHappyPathFulfilled(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def test_full_lifecycle_fulfilled_pays_party_b(self):
        set_caller(PARTY_A_ADDRESS)
        agr_id = call_payable(
            self.c, "create_agreement", 100,
            PARTY_B_ADDRESS, "Build a landing page", "Must be live and responsive",
            future_iso(7200),
        )

        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_agreement", 100, agr_id)

        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(agr_id, ["https://example.com/site"])

        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "a live responsive page"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"verdict": "FULFILLED", "reasoning": "page is live"}):
            self.c.resolve_tier1(agr_id)

        agreement = json.loads(self.c.get_agreement(agr_id))
        self.assertEqual(agreement["status"], "tier1_resolved")
        self.assertEqual(agreement["tier1_verdict"], "FULFILLED")

        # simulate appeal window passing
        agreement["appeal_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.agreements[agr_id] = json.dumps(agreement, sort_keys=True)

        self.c.finalize_unappealed(agr_id)

        self.assertEqual(self.c.get_pending_withdrawal(PARTY_B_ADDRESS), "200")
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "0")

        set_caller(PARTY_B_ADDRESS)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1]["to"], PARTY_B_ADDRESS)
        self.assertEqual(int(gl.evm.transfers[-1]["value"]), 200)

    def test_cannot_settle_twice(self):
        set_caller(PARTY_A_ADDRESS)
        agr_id = call_payable(
            self.c, "create_agreement", 100,
            PARTY_B_ADDRESS, "Ship a report", "Delivered by deadline",
            future_iso(7200),
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_agreement", 100, agr_id)
        self.c.submit_evidence(agr_id, ["https://example.com/report"])

        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "report delivered"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"verdict": "FULFILLED", "reasoning": "ok"}):
            self.c.resolve_tier1(agr_id)

        agreement = json.loads(self.c.get_agreement(agr_id))
        agreement["appeal_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.agreements[agr_id] = json.dumps(agreement, sort_keys=True)

        self.c.finalize_unappealed(agr_id)
        with self.assertRaises(Exception):
            self.c.finalize_unappealed(agr_id)  # already finalized -> wrong status
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_B_ADDRESS), "200")


if __name__ == "__main__":
    unittest.main()
