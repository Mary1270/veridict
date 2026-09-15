import datetime
import hashlib
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    make_contract, set_caller, reset_transfers, call_payable, gl,
    PARTY_A_ADDRESS, PARTY_B_ADDRESS, STRANGER_ADDRESS, JUROR_ADDRESSES,
)


def future_iso(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def commit_hash(vote, salt):
    return hashlib.sha256(f"{vote}:{salt}".encode()).hexdigest()


class JuryTestBase(unittest.TestCase):
    """Shared setup: an agreement through Tier-1 resolution (BREACHED),
    plus 6 staked jurors, ready for appeal."""

    def setUp(self):
        self.c = make_contract()
        reset_transfers()

        set_caller(PARTY_A_ADDRESS)
        self.agr_id = call_payable(
            self.c, "create_agreement", 1000,
            PARTY_B_ADDRESS, "Deliver a security audit report",
            "Report must cover all listed contract files",
            future_iso(7200),
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(self.c, "accept_agreement", 1000, self.agr_id)
        self.c.submit_evidence(self.agr_id, ["https://example.com/audit-report"])

        with patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": "partial audit only"), \
             patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": {"verdict": "BREACHED", "reasoning": "missing files"}):
            self.c.resolve_tier1(self.agr_id)

        self.jurors = JUROR_ADDRESSES[:6]
        for i, addr in enumerate(self.jurors):
            set_caller(addr)
            call_payable(self.c, "register_juror", 1000 + i * 100)

    def do_appeal(self, appellant_address=PARTY_A_ADDRESS):
        set_caller(appellant_address)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "fixed-test-beacon"}'):
            call_payable(self.c, "appeal", 200, self.agr_id)  # 20% of 1000
        return json.loads(self.c.get_agreement(self.agr_id))


class TestAppealAndJurySelection(JuryTestBase):
    def test_appeal_selects_jury_of_five_and_is_reproducible(self):
        agreement = self.do_appeal()
        self.assertEqual(agreement["status"], "appealed")
        jury = agreement["jury"]
        self.assertEqual(len(jury["selected_jurors"]), 5)
        self.assertEqual(len(set(a.lower() for a in jury["selected_jurors"])), 5)

        check = json.loads(self.c.verify_jury_selection(self.agr_id))
        self.assertTrue(check["match"])

    def test_wrong_appeal_bond_rejected(self):
        set_caller(PARTY_A_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(self.c, "appeal", 1, self.agr_id)

    def test_stranger_cannot_appeal(self):
        set_caller(STRANGER_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(self.c, "appeal", 200, self.agr_id)

    def test_appeal_fails_without_enough_staked_jurors(self):
        c2 = make_contract()
        set_caller(PARTY_A_ADDRESS)
        agr_id = call_payable(
            c2, "create_agreement", 1000, PARTY_B_ADDRESS, "obj", "crit", future_iso(7200)
        )
        set_caller(PARTY_B_ADDRESS)
        call_payable(c2, "accept_agreement", 1000, agr_id)
        c2.submit_evidence(agr_id, ["https://example.com/x"])
        with patch.object(gl.nondet.web, "render", return_value="x"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"verdict": "BREACHED", "reasoning": "x"}):
            c2.resolve_tier1(agr_id)
        # only register 2 jurors -- not enough for JURY_SIZE=5
        for addr in JUROR_ADDRESSES[:2]:
            set_caller(addr)
            call_payable(c2, "register_juror", 500)
        set_caller(PARTY_A_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value='{"randomness": "x"}'):
            with self.assertRaises(Exception):
                call_payable(c2, "appeal", 200, agr_id)


class TestCommitReveal(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.agreement = self.do_appeal()
        self.selected = self.agreement["jury"]["selected_jurors"]

    def _open_reveal(self):
        agreement = json.loads(self.c.get_agreement(self.agr_id))
        agreement["jury"]["commit_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.agreements[self.agr_id] = json.dumps(agreement, sort_keys=True)

    def test_only_selected_jurors_can_commit(self):
        non_juror = [a for a in JUROR_ADDRESSES if a.lower() not in [s.lower() for s in self.selected]][0]
        set_caller(non_juror)
        with self.assertRaises(Exception):
            self.c.commit_vote(self.agr_id, commit_hash("BREACHED", "salt"))

    def test_reveal_must_match_commit(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.agr_id, commit_hash("BREACHED", "saltA"))
        self._open_reveal()
        set_caller(juror)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.agr_id, "BREACHED", "wrong-salt")
        self.c.reveal_vote(self.agr_id, "BREACHED", "saltA")

    def test_commit_cannot_be_changed(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.agr_id, commit_hash("BREACHED", "s1"))
        with self.assertRaises(Exception):
            self.c.commit_vote(self.agr_id, commit_hash("FULFILLED", "s2"))

    def test_cannot_reveal_before_commit_window_closes(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.agr_id, commit_hash("BREACHED", "s1"))
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.agr_id, "BREACHED", "s1")

    def test_cannot_replay_a_reveal(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.agr_id, commit_hash("BREACHED", "s1"))
        self._open_reveal()
        set_caller(juror)
        self.c.reveal_vote(self.agr_id, "BREACHED", "s1")
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.agr_id, "BREACHED", "s1")


class TestFinalizeJury(JuryTestBase):
    def setUp(self):
        super().setUp()
        self.agreement = self.do_appeal()
        self.selected = self.agreement["jury"]["selected_jurors"]

    def _commit_and_reveal(self, votes: dict):
        """votes: {address: vote}; jurors not present in votes never commit."""
        salts = {addr: f"salt-{i}" for i, addr in enumerate(self.selected)}
        for addr in self.selected:
            vote = votes.get(addr.lower())
            if vote is not None:
                set_caller(addr)
                self.c.commit_vote(self.agr_id, commit_hash(vote, salts[addr]))

        agreement = json.loads(self.c.get_agreement(self.agr_id))
        agreement["jury"]["commit_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.agreements[self.agr_id] = json.dumps(agreement, sort_keys=True)

        for addr in self.selected:
            vote = votes.get(addr.lower())
            if vote is not None:
                set_caller(addr)
                self.c.reveal_vote(self.agr_id, vote, salts[addr])

        agreement = json.loads(self.c.get_agreement(self.agr_id))
        agreement["jury"]["reveal_deadline"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        self.c.agreements[self.agr_id] = json.dumps(agreement, sort_keys=True)

    def test_verified_majority_overturns_tier1_and_slashes_minority(self):
        votes = {addr.lower(): "FULFILLED" for addr in self.selected[:4]}
        votes[self.selected[4].lower()] = "BREACHED"
        self._commit_and_reveal(votes)

        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.agr_id)

        final_agreement = json.loads(self.c.get_agreement(self.agr_id))
        self.assertEqual(final_agreement["final_verdict"], "FULFILLED")
        self.assertTrue(final_agreement["jury"]["verification_passed"])
        # tier1 was BREACHED, tier2 verdict differs -> appellant (party_a) wins, bond returned
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "200")
        minority = json.loads(self.c.get_juror(self.selected[4]))
        self.assertEqual(minority["minority_votes"], 1)
        majority_addr = self.selected[0]
        self.assertGreater(int(self.c.get_pending_withdrawal(majority_addr)), 0)
        # party_b (winner of FULFILLED verdict) got the pot
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_B_ADDRESS), "2000")

    def test_appellant_loses_appeal_forfeits_bond_to_reward_pool(self):
        # majority agrees WITH tier1 (BREACHED) -> appellant loses, bond forfeited
        votes = {addr.lower(): "BREACHED" for addr in self.selected}
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.agr_id)

        final_agreement = json.loads(self.c.get_agreement(self.agr_id))
        self.assertEqual(final_agreement["final_verdict"], "BREACHED")
        # party_a is BOTH the appellant (loses appeal, bond forfeited) AND the
        # BREACHED-verdict winner (gets the full 2000 pot) -- bond forfeiture
        # only means the extra +200 bond never lands on top of that pot.
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "2000")
        # all 5 jurors were unanimous majority -> each gets a share of the forfeited 200 bond
        total_juror_rewards = sum(int(self.c.get_pending_withdrawal(a)) for a in self.selected)
        self.assertEqual(total_juror_rewards, 200)

    def test_unverified_majority_slashes_nobody_for_disagreeing_but_slashes_non_reveal(self):
        votes = {addr.lower(): "FULFILLED" for addr in self.selected[:4]}  # 5th never reveals
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": False}):
            self.c.finalize_jury(self.agr_id)

        final_agreement = json.loads(self.c.get_agreement(self.agr_id))
        self.assertEqual(final_agreement["final_verdict"], final_agreement["tier1_verdict"])
        self.assertFalse(final_agreement["jury"]["verification_passed"])
        non_reveal_juror = json.loads(self.c.get_juror(self.selected[4]))
        self.assertEqual(non_reveal_juror["non_reveals"], 1)
        # verdict holds at tier1 (BREACHED) -> party_a gets the 2000 pot as
        # the winning party, PLUS the 200 bond refunded (verification
        # failure is nobody's fault) = 2200 total.
        self.assertEqual(self.c.get_pending_withdrawal(PARTY_A_ADDRESS), "2200")
        # the 4 who revealed FULFILLED were NOT slashed for disagreeing with tier1
        for addr in self.selected[:4]:
            record = json.loads(self.c.get_juror(addr))
            self.assertEqual(record["minority_votes"], 0)

    def test_tie_falls_back_to_tier1_no_slashing(self):
        votes = {
            self.selected[0].lower(): "FULFILLED",
            self.selected[1].lower(): "FULFILLED",
            self.selected[2].lower(): "BREACHED",
            self.selected[3].lower(): "BREACHED",
            # 5th never reveals -> 2-2 tie among reveals, no majority
        }
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.agr_id)

        final_agreement = json.loads(self.c.get_agreement(self.agr_id))
        self.assertEqual(final_agreement["final_verdict"], final_agreement["tier1_verdict"])
        self.assertIsNone(final_agreement["jury"]["provisional_verdict"])
        for addr in self.selected[:4]:
            record = json.loads(self.c.get_juror(addr))
            self.assertEqual(record["minority_votes"], 0)  # nobody slashed for a genuine tie

    def test_double_finalize_is_impossible(self):
        votes = {addr.lower(): "BREACHED" for addr in self.selected}
        self._commit_and_reveal(votes)
        with patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            self.c.finalize_jury(self.agr_id)
        with self.assertRaises(Exception):
            self.c.finalize_jury(self.agr_id)  # already finalized -> wrong status


class TestReputationAndWeightCap(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_reputation_is_bounded(self):
        addr = JUROR_ADDRESSES[0]
        set_caller(addr)
        call_payable(self.c, "register_juror", 1000)
        record = self.c._load_juror(addr)
        for _ in range(200):
            record["reputation"] = self.c._clamp_reputation(record["reputation"] + 5)
        self.assertEqual(record["reputation"], self.c.REPUTATION_MAX)
        for _ in range(200):
            record["reputation"] = self.c._clamp_reputation(record["reputation"] - 5)
        self.assertEqual(record["reputation"], self.c.REPUTATION_MIN)

    def test_selection_weight_respects_stake_cap(self):
        whale = {"stake": str(10 ** 30), "reputation": 0}
        normal = {"stake": str(1000), "reputation": 0}
        w_whale = self.c._selection_weight(whale)
        w_normal = self.c._selection_weight(normal)
        # whale's weight is capped at MAX_EFFECTIVE_STAKE, not the raw stake
        self.assertEqual(w_whale, float(int(self.c.MAX_EFFECTIVE_STAKE)))
        self.assertLess(w_whale / w_normal, float(self.c.MAX_EFFECTIVE_STAKE))


if __name__ == "__main__":
    unittest.main()
