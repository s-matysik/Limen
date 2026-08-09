# Study 3 — how to test the hypothesis the pilot did not test

Design document. All diagnostic numbers come from `eval/results/`
(`study3_summary.json`, `study2_lifetimes.csv`) and are reproducible with the commands in `research-report.md`.

## 1. The hypothesis, literally

The protocol (step 17c): *"held-out rules: a subset of tasks is scored with rules absent from the
context given to the agent — if the improvement carries over to the rules that were not given, this
is the strongest result of the article."*

It is broken into two hypotheses, because they have a different evidential status and a different cost:

- **H1 (conformance):** an agent with a contract introduces fewer violations of the rules **it was
  shown** than an agent with the full prose. The weaker one — it measures whether the agent follows
  an instruction.
- **H2 (transfer):** the improvement carries over to the rules **it was not shown**. The stronger one —
  it measures whether the contract teaches a principle rather than just a list of prohibitions. This
  is the thesis of the article.

The pilot refuted neither of them. It failed to test them.

## 2. Why the pilot did not work — a diagnosis, not an excuse

Δ error count = 0 in 23 of 25 runs; between-task variance = 0 in all six
contrasts; the permutation test in all-zero mode (p = 1). Three causes, all measured:

1. **The tasks could not violate the rules.** The rules enforce boundaries *between* modules; the tasks
   were changes *within* a single module.
2. **The parent trees were clean on the shown rules.** The entire historical debt lay on the
   held-out rules, so there was nothing to repair or to worsen.
3. **Violations are a rare event in this project.** In the whole history **9 commits**
   introduced any violation at all (89 episodes), of which **7 were later repaired**.
   A randomly chosen task has a low chance of landing in a situation where a rule can fire at all.

Point 3 is the most important: **LitRev does not have enough material for K=20 violation-prone
tasks.** Increasing K within this repository will not help.

## 3. Task sampling — three layers instead of one

Instead of "commits that add a use case" (the sampling that produced the floor), three layers of
increasing susceptibility to violation. Each task gets a layer label and **is reported separately**.

### Layer 1 — tasks with a documented violation (n ≈ 7)

Seven commits that historically introduced a violation **later repaired by a human**:
`46bbf692` (41 violations), `9cf5ac79` (15), `eab22e21` (12), `c1d3e424` (11), `82915ff2` (3),
`c32d1719` (2), `0334bc8f` (2). The value: it is known that the temptation is real (a human yielded to it)
**and** that a correct solution exists (a human later wrote it). This is ground truth that
cannot be invented.

A limitation to record: these are mostly large commits, so a task must be cut down to a
subtask covering a single violation.

### Layer 2 — cross-module tasks from history (n ≈ 8)

Commits touching ≥ 2 modules: backend `8e005bf0` (16 modules), `eab22e21` (12), `46bbf692` (7),
`bf64942d` (5), `67cef387` (3), `6deb7853` (2); frontend 4 more. They require crossing a
module boundary, so a rule has a chance to fire. Large commits are cut into subtasks.

### Layer 3 — synthetic, preregistered tasks (n ≈ 10)

Layers 1–2 will not yield K=20 from LitRev alone. The missing tasks are composed synthetically —
**but with a safeguard against fitting them to the rules**:

- the tasks are written **before** the split into shown and held-out rules is drawn;
- the content of a task describes a **business goal** ("add an e-mail notification when the owner of a
  project changes"), never a mechanism ("use Contracts");
- each task records **which architectural boundary it naturally tempts**
  and why — this is the author's hypothesis, verified afterwards by the result of condition A;
- tasks in which **condition A violated nothing in any repetition** are rejected
  as non-diagnostic — with the number of rejections reported. A task that cannot be failed
  measures nothing.

Layer 3 is marked in `tasks.jsonl` and in every table. If the result on layers 1–2
differs from the result on layer 3, that is information about generalisation, not noise to be averaged away.

## 4. The rule split — the core of the H2 design

The split into shown and held-out must be **random and performed before the first run**,
otherwise H2 is unfalsifiable.

- **Stratification by rule kind** (K1–K10). Without it all rules of one kind could
  land in one half and "transfer" would mean only similarity within a kind.
- **Two independent splits** (seed A and seed B), every task passing through both. Transfer that
  holds under both is robust to sampling chance; transfer visible under only one
  is an artefact.
- **A necessary condition, checked before the start:** the parent tree of every task must have
  ≥ 1 possible held-out rule violation. In the pilot this condition was violated and that was the
  direct cause of Δ_heldout = 0 everywhere.
- The split itself is recorded in the results together with the sha256 of the specification.

## 5. Measures — swapping the primary for the co-primary

| Measure | Role | Rationale |
|---|---|---|
| functional regression (the module's existing suite fails) | **primary** | the only one measured with non-zero variance: 0.32 [0.17, 0.52]; independent of limen |
| violations of **held-out** rules | **primary for H2** | this is the thesis of the article |
| violations of shown rules | secondary | degenerate in the pilot; reported, not tested as the main measure |
| context tokens | secondary | measured: reduction 81.4% (5.37×) |
| iterations to a clean D | secondary | |

The swap of the primary measure is the most important change relative to the protocol and follows
directly from measurement, not from convenience. The rationale goes into the article together with the
number that forced it.

**An event measure instead of a count measure.** Instead of the mean number of violations (dominated by
zeros) — the proportion of runs with at least one violation, with Wilson intervals and a mixed
logistic model (task as a random effect). For rare events this is the appropriate
model, not a t-test on zeros.

## 6. Sample size

From the pilot: functional regression 0.32 [0.17, 0.52], event rate 0.08 [0.02, 0.25].

| Target | Runs per arm |
|---|---:|
| regression 0.40 vs 0.15 | 49 |
| regression 0.40 vs 0.20 | 82 |
| events 0.30 vs 0.10 | 62 |
| events 0.20 vs 0.05 | 76 |

This is **K = 20, R = 4** (80 runs per arm, 320 in total) — with the caveat that these numbers
assume the new task sampling will raise the event rate from 0.08 to the order of 0.20–0.30.
**This condition must be checked with a second pilot** before the full run is launched:
K = 6 tasks from layer 1, conditions A and C only, R = 3 (36 runs, ~2.2M input tokens
at the measured 60,299 per cell — see the table in §7).
If condition A does not fail ≥ 30% of runs, the task design is still bad and the full run
would be a waste.

## 7. Budget and execution

Measured: 60,299 input tokens per cell, 1,507,477 for 25 cells.

| Stage | Cells | Input tokens | Sessions (limit 2M) |
|---|---:|---:|---:|
| pilot 2 (sampling validation) | 36 | ~2.2M | 2 |
| full study K=20, R=4, 4 conditions | 320 | ~19.3M | 10–11 |
| two rule splits (§4) | ×2 for H2 | ~+9.6M | +5 |

Execution must be **incremental and resumable** — the plan frozen before the start
(`study3_plan.json` already does this), every run recorded separately, resumption adding the missing
cells. This worked in the pilot and is the only reason why stopping at 25/120 did not
destroy the work.

## 8. Circularity control — what was missing

The protocol requires three measures; the pilot delivered one and a half.

1. **Manual verification of ≥ 50 findings with agreement between two raters** — *not met*.
   The mechanical rater checked 50 items from 13 rules (50/50 real, the target on the cited line
   in 46/50), the second rater is implemented and seeded, but was not run.
   Without the second rater there is neither agreement nor kappa — this must either be done or
   explicitly reported as absent.
2. **An independent functional measure** — *met*, and it is promoted to the primary measure.
3. **Held-out rules** — *implemented, but non-diagnostic*, because the condition from §4 was not
   checked. The fix is described above.

## 9. What counts as a result

- **H2 confirmed:** a significantly lower rate of held-out rule violations in C/D than in A/B,
  under both rule splits, without a worsening of functional regression. This is the result the
  article is about.
- **H1 confirmed, H2 not:** the contract works as a list of prohibitions — a useful tool,
  a weaker thesis. Also publishable, but it changes the title of the work.
- **Neither confirmed at a healthy event rate** (A fails ≥ 30%): an honest negative
  result, saying that with this model and these tasks the contract does not improve the architecture.
  It requires reporting with confidence intervals and power, not with a p-value alone.
- **The event rate still at the floor:** the task design is bad, the result does not exist.
  It is then not permissible to report "no difference" as a result — that is the error the pilot
  came close to committing.
