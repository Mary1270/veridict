import datetime
import json
import unittest

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS,
)


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


class TestMinimumEvidenceSources(unittest.TestCase):
    """
    Covers the gap flagged in a Steward review of an adjacent sports-oracle
    project (MatchGuard): evidence quality must be enforceable, duplicate
    URLs must not be able to satisfy a source-count requirement, and the
    accepted source count must be persisted for audit. Veridict has no
    multi-round challenge flow (so the duplicate-round-exhaustion and
    cumulative-counter-evidence failure modes don't apply here - see the
    chat record for why), but the underlying "is the evidence actually
    corroborated" concern is real and is what these tests cover.
    """

    def setUp(self):
        self.c = make_contract()
        reset_transfers()

    def _create(self, min_sources=1):
        set_caller(PARTY_A_ADDRESS)
        agr_id = call_payable(
            self.c, "create_agreement", 1000,
            PARTY_B_ADDRESS, "Ship a fact with independent corroboration",
            "At least the configured number of independent sources must agree",
            future_iso(7200), min_sources,
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_agreement", 1000, agr_id)
        return agr_id

    def test_default_minimum_is_one_source(self):
        agr_id = self._create()  # default min_evidence_sources
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(agr_id, ["https://example.com/only-source"])
        agreement = json.loads(self.c.get_agreement(agr_id))
        self.assertEqual(agreement["status"], "evidence_locked")
        self.assertEqual(agreement["evidence_snapshot"]["accepted_source_count"], 1)

    def test_below_minimum_sources_is_rejected(self):
        agr_id = self._create(min_sources=3)
        set_caller(PARTY_B_ADDRESS)
        with self.assertRaises(Exception):
            self.c.submit_evidence(agr_id, ["https://a.example.com", "https://b.example.com"])
        # and the agreement must remain open, not partially locked
        agreement = json.loads(self.c.get_agreement(agr_id))
        self.assertEqual(agreement["status"], "open")
        self.assertIsNone(agreement["evidence_snapshot"])

    def test_duplicate_urls_are_deduplicated_before_counting(self):
        agr_id = self._create(min_sources=2)
        set_caller(PARTY_B_ADDRESS)
        # 3 raw URLs but only 1 distinct one -> must still fail the min=2 bar
        with self.assertRaises(Exception):
            self.c.submit_evidence(
                agr_id,
                ["https://a.example.com", "https://a.example.com", "https://a.example.com "],  # trailing space variant
            )
        agreement = json.loads(self.c.get_agreement(agr_id))
        self.assertIsNone(agreement["evidence_snapshot"])

    def test_meeting_minimum_with_duplicates_present_locks_with_correct_count(self):
        agr_id = self._create(min_sources=2)
        set_caller(PARTY_B_ADDRESS)
        self.c.submit_evidence(
            agr_id,
            ["https://a.example.com", "https://a.example.com", "https://b.example.com"],
        )
        agreement = json.loads(self.c.get_agreement(agr_id))
        self.assertEqual(agreement["status"], "evidence_locked")
        # accepted_source_count reflects the DEDUPLICATED count, not the raw input length
        self.assertEqual(agreement["evidence_snapshot"]["accepted_source_count"], 2)
        self.assertEqual(len(agreement["evidence_snapshot"]["source_urls"]), 2)

    def test_zero_min_evidence_sources_is_rejected_at_creation(self):
        set_caller(PARTY_A_ADDRESS)
        with self.assertRaises(Exception):
            call_payable(
                self.c, "create_agreement", 1000,
                PARTY_B_ADDRESS, "obj", "criteria", future_iso(7200), 0,
            )


if __name__ == "__main__":
    unittest.main()
