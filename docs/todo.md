# TODO: Live Testnet and Budget Guard

Date: 2026-03-08
Status: Pending approval

## Phase A/B (completed this turn)

- [x] Research runtime/broker/config order flow
- [x] Create `docs/research.md`
- [x] Create `docs/plan.md`
- [x] Create `docs/todo.md`
- [x] Create `docs/decisions.md`
- [x] Create `docs/notes.md`

## Phase C (after `approved`)

- [ ] Add hard safety: block `run --mode live` when env is not `testnet`
- [ ] Add `--budget-guard/--no-budget-guard` CLI switch (default ON)
- [ ] Implement broker budget snapshot reader (available balance normalization)
- [ ] Add runtime pre-order budget guard hook
- [ ] On insufficient budget: skip order and record `insufficient_budget`
- [ ] Keep protective-order flow intact when budget is sufficient
- [ ] Add tests:
  - [ ] insufficient budget blocks order submission
  - [ ] sufficient budget allows order submission and protective flow
- [ ] Update docs:
  - [ ] `README.md`
  - [ ] `guide/BASELINE_STATE.md`
  - [ ] `guide/EXPERIMENT_LOG.md`
- [ ] Run verification:
  - [ ] `uv run --active pytest -q`
  - [ ] `uv run --active trader doctor --env testnet`
  - [ ] 1-symbol live smoke
  - [ ] 3-symbol live smoke

## Acceptance gates

- [ ] `doctor --env testnet` remains PASS
- [ ] Demo UI shows Positions/Open Orders/Assets updates
- [ ] Insufficient budget path sends no order and logs `insufficient_budget`
- [ ] Multi-symbol budget behavior is consistent at account level
