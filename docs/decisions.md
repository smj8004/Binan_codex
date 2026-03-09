# Decisions

Date: 2026-03-08
Status legend: `proposed` (pending approval), `accepted` (implemented)

## D-001 Live env safety
- status: proposed
- decision: allow `run --mode live` only when env is `testnet`
- rationale: requirement is strict no-mainnet live trading

## D-002 Budget guard default
- status: proposed
- decision: pre-order account budget guard is ON by default
- rationale: safe default, with opt-out rollback switch

## D-003 Budget source
- status: proposed
- decision: broker provides normalized account snapshot using exchange available-balance fields
- rationale: runtime internal equity is not equivalent to exchange available margin

## D-004 Insufficient budget behavior
- status: proposed
- decision: skip order submission, record reason `insufficient_budget`, do not halt full runtime
- rationale: keep system running while preserving traceability

## D-005 Protective order policy
- status: proposed
- decision: reduce-only protective orders are not blocked by entry budget guard
- rationale: risk protection must remain active

## D-006 Multi-symbol consistency
- status: proposed
- decision: use a per-bar account budget snapshot with immediate reservation updates
- rationale: current orchestrator has one consumer loop, so this is simple and consistent
