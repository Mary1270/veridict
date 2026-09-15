# Veridict Protocol — Architecture Document (v0.3)

> **v0.3 pivot:** everything below now runs as **one GenLayer-native
> Intelligent Contract**, denominated entirely in **GEN** — no separate
> Solidity contracts, no Base L2, no relay/keeper. This mirrors TrueStake's
> proven pattern: `contract.py` itself custodies and pays out GEN via
> `gl.evm.contract_interface` / `emit_transfer`, exactly as TrueStake's
> `withdraw()` already does. Reasons for the pivot, recorded honestly:
> (1) it removes an entire trust surface (a relay that could misreport a
> verdict) instead of documenting one away; (2) it's the only version that
> can actually be written, offline-tested, and verified working in this
> build session — no Solidity toolchain (forge/solc) is available; (3) it
> is fully buildable and deployable from a phone browser (GenLayer Studio
> + GitHub web/app), matching how this project will actually be shipped.
> The USDC/Base/relay design from v0.2 is kept below in §15 as a
> documented **v2 roadmap item**, not discarded — it's a legitimate future
> upgrade once there's a toolchain to actually build and test it.
>
> Name: **Veridict**. Everything (juror stake, agreement stake, appeal
> bond, rewards, slashing) denominated in **GEN**. Scope: **two-party
> only in v1** (no single-sided claim markets — depth over feature count).

## 1. One-line pitch

A two-tier adjudication protocol: a fast automated GenLayer verdict for the
common case, backed by an **economically accountable appeal layer** —
staked jurors who put GEN at risk for their vote, checked against frozen
evidence, so slashing is never just "you lost a popularity contest."

## 2. Why this is a step beyond Mandate Court and TrueStake

| Capability | TrueStake | Mandate Court | **Veridict** |
|---|---|---|---|
| Escrow + automatic payout | done | done (Base + relay) | done (Base + relay) |
| Multi-source LLM verdict | done | done | done (reused, hardened) |
| Appeal window | no | done (mechanism undisclosed) | done (fully specified) |
| Economic accountability for the *adjudicator* | no | no | **new primitive** |
| Verdict grounded in immutable evidence | partial | partial | frozen `EvidenceSnapshot` |
| Reputation across cases | no | no | on-chain, bounded |

TrueStake and Mandate Court both answer **"who decides?"**. Veridict adds
**"why should the decider be honest, and how do we know the decision was
actually grounded in the evidence rather than a coordination game?"**

## 3. The core design problem, and how v0.2 fixes it

v0.1 said: *majority vote wins, minority gets slashed.* That's a
**circular** truth model — "majority = correct" only proves coordination,
not correctness. Two additions fix this without making the jury pointless:

1. **Evidence is frozen before the jury ever sees the case.**
2. **GenLayer performs a bounded *plausibility check* on the majority
   verdict against that frozen evidence — it doesn't re-derive a verdict
   from scratch.** If it did, the jury would be theater: GenLayer would
   just be voting a second time by itself. Instead, GenLayer's job is
   narrow and mechanical: *"is the majority's verdict consistent with
   what's actually in the evidence, or did they land on something the
   evidence doesn't support?"* A pass grounds the slash in something
   more than nose-counting. A fail means **nobody is slashed this
   round** — the case escalates to a larger jury or holds at the Tier-1
   verdict, because an ungrounded majority is itself a signal of
   collusion or error, not a verdict to enforce.

This keeps the jury meaningful (it supplies the interpretive judgment call
that a hard/disputed case actually needs) while keeping GenLayer as the
backstop against a captured or careless jury — genuinely GenLayer-native,
not just "LLM as decoration."

**Cost/latency note (disclosed, not hidden):** every appealed case now
costs two GenLayer consensus rounds (Tier-1 + post-jury verification),
occasionally three if it escalates. This is real overhead versus
TrueStake/Mandate Court's single-round model — acceptable because appeals
are the minority path by design, not the common case.

## 4. Actors

- **Principal** — creates the agreement, escrows GEN.
- **Respondent** — bound counterparty (required; no single-sided claims in v1).
- **Juror** — any address pre-staking GEN into the juror pool.
- **GenLayer network** — Tier-1 verdict engine and Tier-2 verification engine.
- ~~Relay/Keeper~~ — not needed in v1. GenLayer pays out natively via
  `gl.evm.contract_interface` (see v0.3 pivot note above); see §15 for
  where a relay re-enters the picture in v2.

## 5. Lifecycle / state machine

```
Created -> Funded (USDC escrow) -> Accepted -> EvidenceWindow
   -> EvidenceSnapshot LOCKED (hash, source list, retrieval timestamps -- immutable from here on)
   -> Tier1_Resolving (GenLayer multi-source LLM verdict)
   -> Tier1_Resolved -----------------> Finalized (no appeal within window)
        |
        v appeal + bond posted within AppealWindow
   Appealed
   -> JurySelection      (weighted-random draw of K=5, against the SAME locked snapshot)
   -> CommitPhase        (hash(vote, salt))
   -> RevealPhase        (non-reveal = liveness slash, worse than a wrong vote)
   -> ProvisionalMajority
   -> GenLayerVerification  (plausibility check against EvidenceSnapshot -- pass/fail, not a re-vote)
        | pass                                  | fail
        v                                       v
   Tier2_Resolved (verified)          Escalate (larger jury) or hold Tier1 verdict
   -> Finalized
   -> Settled (USDC to winner, GEN reward/slash, reputation update)
   -> Withdrawn (pull-payment)
```

Escape hatches with explicit refund paths, same non-negotiable rule as
TrueStake: `Cancelled` (pre-acceptance), `Expired` (deadline passed
unresolved) -- no state ever neither pays nor refunds.

## 6. EvidenceSnapshot (frozen before appeal, not just before jury)

```
caseId
evidenceRoot          # hash of the full evidence set
sourceURLs[]           # deduplicated before locking
sourceHashes[]
accepted_source_count  # persisted explicitly, not just inferred from array length
retrievalTimestamp
tier1Verdict
lockedAt
```

`submit_evidence` enforces a `min_evidence_sources` threshold set by the
principal at agreement creation (default 1) -- duplicate URLs are
deduplicated BEFORE being counted against that threshold, so repeating
the same link cannot be used to fake corroboration. This directly answers
a Steward finding on an adjacent sports-oracle project regarding
duplicate/degraded evidence; see §10 for what's still out of scope
(source *quality* weighting, as opposed to *count*).

Once `Appealed`, this snapshot is immutable. Jurors vote on it, and — this
is the part that makes slashing defensible — **GenLayer's verification
step checks the majority verdict against this exact snapshot**, not
against anything a juror claims to have seen later. Same evidence,
independent opinions, commit-reveal, then a grounded check — that's the
actual truth-seeking pipeline, not majority-vote alone.

## 7. Jury mechanics

- **Size:** 5 (v1, fixed).
- **Selection weight:** `weight_i = min(stake_i, MAX_EFFECTIVE_STAKE) x
  reputation_multiplier_i`. The cap is what actually stops a whale from
  buying selection odds -- a stated cap without an enforced `min()` is not
  a real defense.
- **Auditable selection:** the draw is a deterministic function of
  `(randomnessBeacon, caseId, juryPoolSnapshot)`; the result
  (`selectedJurorsHash`) is recorded so anyone can independently recompute
  and verify the 5 selected addresses were not hand-picked. v1 randomness
  source: a public beacon (drand) fetched via `gl.nondet.web` -- disclosed
  as not a fully trust-minimized on-chain VRF (see section 10).
- **Commit-reveal:** `hash(vote, salt)` submitted first; reveal afterward.
  Prevents copying/herding around a visible leading vote.
- **Aggregation:** simple majority of *revealed* votes -> provisional
  verdict, subject to GenLayer verification (section 3). A tie, or insufficient
  reveals to reach quorum, never manufactures a verdict -- it falls back to
  the Tier-1 verdict standing.

## 8. Reputation (on-chain, bounded)

```
Juror
 |-- stake
 |-- reputation            # bounded, e.g. [-50, +50]
 |-- cases_participated
 |-- correct_consensus_votes
 |-- minority_votes
 |-- non_reveals
 `-- last_active
```

Update rule (v1 defaults, tunable):

| Outcome | Reputation delta |
|---|---|
| Majority + reveal, verification passes | +2 |
| Minority + reveal | -2 |
| Non-reveal | -3 |

`reputation_multiplier = clamp(1.0 + reputation/100, 0.5, 1.5)` -- so
reputation shifts selection odds by at most +/-50%, never enough to let
reputation alone override stake, and never enough to permanently exclude
or permanently favor an address.

## 9. Economics

| Event | Consequence |
|---|---|
| Juror votes with verified majority | pro-rata share of the slashed pool + forfeited appeal bond, weighted by stake |
| Juror votes with minority (majority verified) | slash 10% of staked amount |
| Juror commits but never reveals | slash 15% (liveness penalty > wrong-vote penalty) |
| Majority fails GenLayer verification | no slashing this round; escalate or hold Tier-1 verdict |
| Appellant loses appeal | appeal bond forfeited to the reward pool |
| Appellant wins appeal | bond returned |

**Appeal bond:** `appealBond = clamp(stake * 20%, MIN_BOND, MAX_BOND)` --
a flat 20% is unusable at both extremes (near-free on tiny disputes,
prohibitive on huge ones); the clamp keeps appeal accessible but not free.

All fund movement -- agreement stakes, appeal bonds, juror stakes, rewards,
slashes -- is GEN, and all of it is credit-then-withdraw
(`pending_withdrawals` + `withdraw()`), identical to TrueStake's proven
pattern -- never push-on-resolve.

## 10. Known limitations (v1, disclosed up front)

- Randomness source (drand via `gl.nondet.web`) is not a fully
  trust-minimized on-chain VRF.
- Fixed jury size (5), no dynamic scaling by dispute value.
- One appeal round; no cascading appeals-of-appeals.
- No reputation portability if a juror fully unstakes and re-stakes later.
- Two-party only -- no single-sided claim markets in v1 (explicitly cut for
  scope discipline, not an oversight).
- Two-round GenLayer consensus cost per appeal (section 3), occasionally three on
  escalation.
- Evidence quality is enforced by count (`min_evidence_sources`, set per
  agreement at creation, deduplicated before counting), not by weighing
  source reliability -- a principal who sets it to 1 can still be shown a
  single low-quality source. Cross-source corroboration *quality* (as
  opposed to *count*) is out of scope for v1.

## 11. Repo layout (v1, single GenLayer-native contract)

```
veridict/
  contract.py             single Intelligent Contract -- agreements, escrow,
                           Tier-1 resolution, juror staking, appeal, jury
                           selection, commit-reveal, Tier-2 verification,
                           slashing, reputation, pull-payment withdraw
  index.html               no-build static frontend (genlayer-js via esm.sh),
                           deployable straight to GitHub Pages
  tests/
    genlayer_stub/         offline `gl` SDK stub (TrueStake pattern)
    test_*.py              offline pytest suite (section 12 list, adapted)
  README.md
  docs/ARCHITECTURE.md     (this file)
```

No `apps/`, no `packages/`, no `relay/`, no Solidity in v1 -- see §15 for
why that's deliberate, not a shortcut.

## 12. Attack-oriented test list (this is what actually earns Steward trust, not raw test count)

```
test_wrong_majority_cannot_fake_verdict
test_majority_failing_verification_slashes_nobody
test_non_reveal_slashes_more_than_wrong_vote
test_commit_cannot_be_changed
test_reveal_must_match_commit
test_evidence_cannot_change_after_appeal
test_juror_selection_respects_weight_cap
test_whale_cannot_dominate_selection
test_unfunded_appeal_cannot_start
test_tie_falls_back_to_tier1
test_insufficient_reveals_fall_back_to_tier1
test_double_settlement_impossible
test_withdrawal_is_pull_payment
test_reputation_is_bounded
test_replay_attack_impossible
test_selected_jurors_hash_is_independently_reproducible
```

## 13. What must actually work in the demo (not just be documented)

1. Real GenLayer Tier-1 verdict (no mocks)
2. Frozen `EvidenceSnapshot` with hash/timestamp/source set
3. Real appeal bond paid on-chain
4. Real GEN juror staking
5. Real commit-reveal with hash verification
6. Real slashing that actually reduces stake
7. Real GenLayer post-jury verification (not skipped)
8. Reputation actually affecting the *next* case's selection odds

## 14. Build milestones

1. M0 -- this document, confirmed
2. M1 -- Solidity: Escrow, JurorStakingPool, SlashingEngine, ReputationRegistry + unit tests
3. M2 -- GenLayer Python: Tier-1 resolver (adapted from TrueStake) + jury_orchestrator with verification step + offline test suite (section 12 list)
4. M3 -- Relay/keeper service
5. M4 -- Frontend (Next.js)
6. M5 -- End-to-end deploy: GenLayer testnet + Base Sepolia
7. M6 -- README, Notes, X post, Portal submission

---

### Still open
- Exact `MIN_BOND` / `MAX_BOND` / `MAX_EFFECTIVE_STAKE` values -- placeholders
  until we know realistic stake sizes from testnet dry-runs.
- Escalation jury size on verification-fail (larger fixed number, e.g. 9?).

## 15. v2 roadmap (not built in v1, kept here so the idea isn't lost)

The original v0.2 design split economics across two chains: **USDC on
Base** for the underlying principal/respondent agreement (real-world
economic weight, stablecoin-denominated) and **GEN** for the juror
truth-seeking layer, connected by a relay/keeper (1Shot/Gelato-style,
the same pattern visible in Mandate Court's own "relay jobs" panel) that
forwards a finalized GenLayer verdict to a Base-side escrow contract,
which independently re-reads canonical GenLayer state before paying out
so the keeper itself can never forge an outcome.

This remains a legitimate, arguably stronger long-term design -- USDC
escrow is a real pitch advantage for anyone who doesn't want agreement
value exposed to GEN's price swings. It is v2 rather than v1 for one
practical reason, not a design flaw: it requires a Solidity toolchain
(Foundry/Hardhat) and a live Base testnet to build and verify, neither of
which was available in this build session. Once that tooling exists,
`Escrow.sol`, `JurorStakingPool.sol`, `SlashingEngine.sol`, and
`ReputationRegistry.sol` as sketched in v0.2 are the starting point, with
the GenLayer contract in this document becoming the verdict/jury engine
that the relay reads from.
