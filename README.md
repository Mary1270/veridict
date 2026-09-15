# Veridict

> The economically-accountable adjudication protocol for autonomous agreements.

Veridict lets two parties (agents or humans) enter a GEN-escrowed agreement,
get a fast automated GenLayer verdict on whether it was fulfilled, and — if
either side disputes that verdict — escalate to a **staked jury** that has
to put its own GEN at risk to vote, gets checked against evidence it can't
move after the fact, and gets slashed when its majority doesn't survive
that check.

```
CREATE → ESCROW → EVIDENCE (frozen) → TIER-1 VERDICT → [APPEAL] →
JURY SELECTION → COMMIT → REVEAL → GENLAYER VERIFICATION → SETTLE
```

## Why this exists

Automated on-chain adjudication (TrueStake, Mandate Court, and others)
answers **"who decides whether the agreement was fulfilled?"** — an LLM
consensus process reading evidence. None of them answer a harder question:
**"why should the decider be honest, and how do we know a disputed
decision was actually grounded in evidence rather than a coordination
game?"**

Veridict's entire reason to exist is that second question:

- **Evidence is frozen** into an immutable `EvidenceSnapshot` the moment
  either party escalates — nobody, on either side, gets to introduce new
  sources after the fact.
- Disputes go to a **staked jury**, not a single re-run of the same
  automated pipeline that was already disputed.
- Jurors vote via **commit-reveal**, so nobody can copy or herd around a
  visible leading vote.
- The jury's majority is not trusted by default. GenLayer runs one bounded
  check — *is this verdict plausible given the frozen evidence, or is it
  unsupported by anything in it?* — before anyone is paid or slashed. A
  majority that fails this check slashes nobody; the case falls back to
  the original automated verdict instead of manufacturing a result.
- Jurors who side with a **verified** majority earn a share of the slashed
  stakes and forfeited appeal bond; jurors who don't, or who commit and
  never reveal, are slashed — non-reveal costs more than an honest wrong
  vote, because free-riding is worse than disagreeing.
- A bounded, on-chain **reputation** score shifts future selection odds by
  at most ±50% — enough to matter, never enough to let reputation alone
  override stake or permanently exclude an address.

## What's in this repo

```
contract.py        the entire protocol — one GenLayer Intelligent Contract
index.html          no-build frontend (genlayer-js via esm.sh) — open it directly
                     or serve it from GitHub Pages, no npm/build step required
docs/ARCHITECTURE.md full design doc: state machine, economics, disclosed
                     limitations, and the v2 (Base + USDC + relay) roadmap
tests/               offline pytest/unittest suite against a stub `gl` SDK
                     (no network, no live GenLayer node needed to run these)
```

## Design decisions, stated plainly

- **Everything is GEN in v1** — agreement stakes, appeal bonds, juror
  stakes, rewards, and slashes. A USDC-denominated agreement escrow on
  Base, connected by a relay, is a documented v2 idea
  (`docs/ARCHITECTURE.md` §15) — deferred because it needs a Solidity
  toolchain and a live Base testnet that weren't available while this was
  being built, not because the design is wrong.
- **One contract, not a monorepo.** Everything — escrow, Tier-1
  resolution, juror staking, appeal, jury selection, commit-reveal,
  Tier-2 verification, slashing, reputation, withdrawal — lives in
  `contract.py`. This mirrors the one thing every prior GenLayer
  escrow/adjudication project this was built after has in common: the
  parts that actually move money are the parts that get tested, and a
  single deployable contract is the easiest thing to actually get live
  and verified working end to end.
- **Verification checks plausibility, not correctness from scratch.**
  If GenLayer re-derived the verdict independently at Tier-2, the jury
  would be pointless — GenLayer would just be voting a second time by
  itself. Instead it asks one narrower, mechanical question: does the
  jury's majority verdict have support in the frozen evidence? See the
  `Veridict` class docstring in `contract.py` for the full reasoning.
- **Evidence quality is enforced by count, not just presence.**
  `submit_evidence` takes a `min_evidence_sources` threshold (set per
  agreement at creation, default 1), deduplicates URLs before counting,
  and persists the accepted source count in the frozen snapshot. Added
  directly in response to a Steward review finding on an adjacent
  sports-oracle project about duplicate/degraded evidence being able to
  satisfy a source requirement.
- **Known v1 limitations are disclosed up front**, not discovered later:
  fixed jury size (5), one appeal round (no cascading appeals),
  drand-via-`gl.nondet.web` randomness rather than a trust-minimized
  on-chain VRF, and no reputation portability across a full
  unstake/restake cycle. Full list in `docs/ARCHITECTURE.md` §10.

## Deploying

1. Open [GenLayer Studio](https://studio.genlayer.com/) and paste in
   `contract.py`. Deploy it — GenLayer Studio works from a phone browser,
   no local toolchain needed.
2. Copy the deployed contract address.
3. Open `index.html`, replace the `CONTRACT_ADDRESS` constant near the
   top of the `<script type="module">` block with that address.
4. Push the repo to GitHub and enable GitHub Pages (Settings → Pages →
   deploy from branch) to serve `index.html` — or just open the file
   directly in a browser; it has no build step and no server dependency.

## Running the offline test suite

```bash
python3 -m unittest discover tests
```

No network access or live GenLayer node is required — `tests/genlayer_stub`
provides a minimal in-memory `gl` module so every state-machine path
(happy path, appeal, jury selection, commit-reveal, verified/unverified
majority, ties, double-settlement, weight caps, reputation bounds) is
exercised deterministically and fast.

## License

MIT
