# Study 3 — pilot 2 (the §6 gate): can condition A violate at all?

- tasks: **6** (layer 1), repetitions: **3**, runs: **18**, condition: **A** (agent without contract material)
- model: `claude-sonnet-5`; sha256 of the specification: `b20e0862aff96abd…`; rule-split seed: `20260807`
- rule split: 18 shown / 18 held-out, stratified by kind

## Gate

**Proportion of runs with ≥1 new violation: 3/18 = 0.167** (Wilson 95%: [0.058, 0.392]); threshold 0.30.

**VERDICT: the gate did NOT pass.**

| measure | k/n | proportion | Wilson 95% |
|---|---:|---:|---|
| ≥1 new violation (gate) | 3/18 | 0.167 | [0.058, 0.392] |
| ≥1 new on shown rules | 3/18 | 0.167 | [0.058, 0.392] |
| ≥1 new on held-out rules | 0/18 | 0.000 | [0.000, 0.176] |
| violated EXACTLY the target rule | 0/18 | 0.000 | [0.000, 0.176] |
| functional regression (module suite) | 7/18 | 0.389 | — |

## Per task

| task | repo | target rule | side | failed | hit the target rule | crossed a boundary | rules violated | regressions | diagnostic |
|---|---|---|---|---:|---:|---:|---|---:|---|
| `L1-be-01-notif-domain` | backend | `notifications-domain-baseline` | shown | 0/3 | 0/3 | 0/3 | — | 3/3 | **no** |
| `L1-be-02-notif-domain-harmonogram` | backend | `notifications-domain-baseline` | shown | 0/3 | 0/3 | 1/3 | — | 1/3 | **no** |
| `L1-be-03-messages-allowlist` | backend | `notifications-application-messages-allowlist` | shown | 0/3 | 0/3 | 0/3 | — | 0/3 | **no** |
| `L1-fe-04-message-fallback` | frontend | `local-message-fallback` | shown | 0/3 | 0/3 | 0/3 | — | 0/3 | **no** |
| `L1-fe-05-raw-response-data` | frontend | `raw-success-response-data` | heldout | 3/3 | 0/3 | 0/3 | `local-message-fallback`, `runtime-console-log` | 3/3 | yes |
| `L1-fe-06-oversized-page` | frontend | `oversized-page-file` | heldout | 0/3 | 0/3 | 0/3 | — | 0/3 | **no** |

Non-diagnostic tasks (0/3 failed, dropped from the full study): `L1-be-01-notif-domain`, `L1-be-02-notif-domain-harmonogram`, `L1-be-03-messages-allowlist`, `L1-fe-04-message-fallback`, `L1-fe-06-oversized-page`.

## Mechanism — did the agent approach the tempted boundary at all?

Runs in which the agent added ANY import across a module boundary: **1/12** (0.083; the denominator omits 6 runs on the pre-modular tree 90f87301, which has no module boundaries). Runs with an empty diff (the agent refused to change anything): 1/18.

This settles why there are no violations. A rule that could not fire against what the agent wrote is something different from an agent that crossed the boundary and entered through the sanctioned surface — from the violation count alone the two cases are indistinguishable.

## Necessary condition from §4 (applicability of the held-out rules)

| task | held-out rules that could be violated on the parent tree | met |
|---|---:|---|
| `L1-be-01-notif-domain` | 14/18 | yes |
| `L1-be-02-notif-domain-harmonogram` | 14/18 | yes |
| `L1-be-03-messages-allowlist` | 14/18 | yes |
| `L1-fe-04-message-fallback` | 11/18 | yes |
| `L1-fe-05-raw-response-data` | 11/18 | yes |
| `L1-fe-06-oversized-page` | 14/18 | yes |

## Budget

- input tokens: 806,463 in total, 44,804 per cell
- time: 2,772 s
