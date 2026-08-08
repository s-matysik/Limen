# eval/ — research-plan harness (M10, not yet implemented)

This directory is reserved for the evaluation protocol from the research plan
(`limen-plan-badan.md`): agent-task benchmarks comparing three conditions —

1. **baseline** — agent + task, no contract;
2. **context** — agent + task + `limen context --paths ... --budget-tokens N`;
3. **loop** — condition 2, plus `limen check` after each attempt with the
   failing findings fed back to the agent.

Planned contents:

- `tasks/` — task definitions (repository snapshot, instruction, target diff);
- `run.py` — harness driving the agent API, recording per-attempt
  `limen check --format json` output;
- `metrics.py` — violation rate, first-pass success, token cost of context,
  time-to-green;
- `results/` — raw JSON per run (git-ignored).

The deterministic checker and `limen context` are already stable enough to
script against; the harness intentionally lands after M8 so the CI bridges can
be reused as the ground-truth oracle.

---

# Faza 0 — retrofit of the legacy LitRev contract

Every number this phase contributes to the paper is produced by one of the
scripts below. Nothing is typed into a report by hand; re-running the block
regenerates all of it.

## Prerequisites

- workspace built per `eval/WORKSPACE.md` at `<LIMEN>/workspace`
  (six repository copies + the `litrev-arch-snapshot/` symlink farm);
- unpacked snapshots at `<LIMEN>/LitRev` (the legacy sources are read from
  there, never from the workspace copy, so the counts describe the shipped
  repositories);
- conda env `limen-eval` (Python 3.13 + pyyaml).

Shell prelude used by every command below:

```bash
LIMEN=~/'!!!!!Limen'
export PYTHONPATH="$LIMEN/limen/src"
```

## 1. Legacy checker size (LOC)

```bash
cd "$LIMEN/limen"
python eval/count_legacy_loc.py \
    --litrev "$LIMEN/LitRev" \
    --out eval/legacy_loc.json \
    --csv eval/legacy_loc.csv
```

Writes per-file and per-group counts. Three definitions are reported because
they differ by ~17%: `total_lines` (`wc -l`), `code_lines` (blank and
comment-only lines removed) and `non_blank_lines`.

**Result on the frozen snapshot:**

| Source group | Files | total | code | non-blank |
| --- | ---: | ---: | ---: | ---: |
| `backend_config` (`litrev-backend/config/architecture.php`) | 1 | 210 | 199 | 199 |
| `backend_shared_config` (`backend-shared/config/architecture.php`) | 1 | 36 | 16 | 33 |
| `backend_tests` (`litrev-backend/tests/Architecture/`) | 10 | 776 | 620 | 622 |
| `frontend_audit` (`litrev-frontend/scripts/audit-frontend-architecture.mjs`) | 1 | 475 | 411 | 411 |
| **TOTAL** | **13** | **1497** | **1246** | **1265** |

The research plan quotes **1251 LOC**. That figure is 5 lines off the
*code-line* count (1246) and 246 lines off `wc -l` (1497) — so the plan is
almost certainly quoting comment-stripped lines. Whichever the paper uses, it
must say which.

## 2. Retrofitted specification size

```bash
cd "$LIMEN/limen"
python eval/count_spec_loc.py \
    --spec "$LIMEN/workspace/architecture.yaml" \
    --out eval/spec_loc.json
```

**Result:** 795 total lines, 740 non-blank, **526 code lines** (the retrofit
cites its legacy source in a comment above every rule, hence the gap);
36 rules, 5 roots, 32 declared units; 31 `error` / 5 `warning`.

## 3. Legacy rule inventory

```bash
cd "$LIMEN/limen"
python eval/build_rule_inventory.py \
    --spec "$LIMEN/workspace/architecture.yaml" \
    --rows eval/_inventory_rows.json \
    --csv eval/legacy_rule_inventory.csv \
    --md  eval/legacy_rule_inventory.md \
    --loc eval/legacy_loc.json
```

`_inventory_rows.json` holds the hand-extracted catalogue (reading 1497 lines
of PHP and JS is the research contribution and is not automatable). The script
is the guard rail: it fails with exit 1 if any row cites a `limen_rule_id` that
is not in the spec, if a row's claimed kind disagrees with the kind the spec
declares for that rule, or if a spec rule is claimed by no row and is not on
the `NON_LEGACY_RULE_IDS` list. All counts in the Markdown are computed.

**Result:** 47 legacy rules → 36 limen rules; 34 `encoded`, 3 `partial`,
6 `subsumed`, 4 `not-encoded`. The four not-encoded ones and the three partials
are written up in `docs/deviations.md` (D-01 … D-10).

## 4. Specification validation — the Faza 0 gate

```bash
cd "$LIMEN/workspace"
python -m limen.cli spec validate --spec architecture.yaml --workspace . --strict-spec
echo "exit=$?"
```

```
spec OK: 36 rule(s), 5 root(s)
exit=0
```

Note on the flag: the research plan writes `limen spec validate --strict-spec`
and that is exactly what the CLI accepts — `--strict-spec` is registered on the
`spec validate` subparser (`src/limen/cli.py`, `v.add_argument("--strict-spec")`)
and promotes S4 *dead-entry* issues from warning to error (exit 3). No
discrepancy against the plan. The same flag also exists on `limen check`.

Equivalent run against the copy committed in the limen repository:

```bash
cd "$LIMEN/workspace"
python -m limen.cli spec validate \
    --spec "$LIMEN/limen/examples/litrev/architecture.yaml" \
    --workspace . --strict-spec
```

Both copies are byte-identical and both exit 0.

## 5. Contract run (informational, not a Faza 0 gate)

```bash
cd "$LIMEN/workspace"
python -m limen.cli check --spec architecture.yaml --workspace . --format json \
    > /tmp/limen-check.json
echo "exit=$?"        # 1 — findings exist, by design
```

**Result:** 3 errors, 8 warnings (all 8 are `unresolved-dependency` from the JS
extractor), exit code 1. The 3 errors are all `host-cross-module-public-surface`
and are genuine: host and core controllers import concrete backend-shared
Application services rather than a `Contracts\` interface. The legacy checker
could not see them (it matched only the `App\Modules\…` namespace). Per the
task brief the violations were left standing — no rule was relaxed and no
exemption written. See `docs/deviations.md` D-08.

`limen spec stats` reports 4 exemptions, all active, none expired.

## Artifacts produced by this phase

| Path | Content |
| --- | --- |
| `workspace/architecture.yaml` | the retrofitted contract (also mirrored to `limen/examples/litrev/architecture.yaml`) |
| `eval/count_legacy_loc.py` | legacy LOC counter |
| `eval/legacy_loc.json`, `eval/legacy_loc.csv` | its output |
| `eval/count_spec_loc.py`, `eval/spec_loc.json` | spec size counter and output |
| `eval/_inventory_rows.json` | hand-extracted legacy rule catalogue (input) |
| `eval/build_rule_inventory.py` | validator + renderer |
| `eval/legacy_rule_inventory.csv` / `.md` | the inventory |
| `docs/deviations.md` | semantics limen cannot express, with engine proposals |

---

# Study 1 — differential equivalence (steps 5–7)

Three commands, run from `$LIMEN/limen` with the shell prelude above. Each
writes its own artefact; no figure is transcribed between them.

## 5. limen side

```bash
python eval/run_limen_check.py \
    --spec      "$LIMEN/workspace/architecture.yaml" \
    --workspace "$LIMEN/workspace" \
    --today     2026-08-04 \
    --out       eval/results/limen_findings.json
```

Runs `limen check --format json` and normalises every finding to the study's
comparison key **(file, line, target)**. The mapping from `finding-1` fields to
that key, and the ordered path-rewrite table that turns limen's
`litrev-arch-snapshot/…` root paths and the legacy checkers' repo-relative
paths into one workspace-relative namespace, are written into the output under
`field_mapping` so a reviewer can check them without reading the code.

**Result:** 11 findings — 3 `host-cross-module-public-surface` errors, 8
`unresolved-dependency` warnings; exit code 1.

## 6. The diff gate

```bash
python eval/study1_equivalence.py \
    --limen      eval/results/limen_findings.json \
    --legacy     eval/results/legacy_findings.json \
    --workspace  "$LIMEN/workspace" \
    --deviations docs/deviations.md \
    --out        eval/results/study1_diff.json
```

This is a **gate**: it exits 1 if a single difference is left unclassified, and
4 if the legacy oracle is absent or describes a different snapshot (both sides'
workspace tree-ids must match). Each difference is classified by an executable
predicate, not a comment — C1 by lexically classifying the legacy match as
code/comment/string, C2 by re-reading the prose rule *and* the legacy regex that
cannot match it, C3 by resolving a `D-nn` heading in `docs/deviations.md` whose
declared kind is `engine-gap` or `editorial`. Every verdict carries its evidence
and the quoted source line into the JSON.

**Result on the frozen snapshot:** 3 differences, all C2, **0 unclassified**;
exit 0. The 8 `unresolved-dependency` warnings are held outside the comparison
by an explicit scope rule (`in_comparison_scope()`) and listed under
`out_of_scope`; the justification is in `docs/deviations.md`.

```bash
python -m pytest eval/tests/test_study1_equivalence.py -q     # 28 passed
```

The unit tests drive the gate against synthetic fixtures — a `use` in a line
comment, in a block comment, in a string literal, an agreement, a dangling
deviation reference, a snapshot mismatch — so the decision rules are verified
independently of what the frozen snapshot happens to contain.

## 7. The article table

```bash
python eval/study1_table.py \
    --spec   "$LIMEN/workspace/architecture.yaml" \
    --litrev "$LIMEN/LitRev"
```

Writes `eval/results/study1_table.{csv,md,json}`: rule kind (K1–K11) × limen
rules × legacy rules × legacy LOC replaced × YAML LOC, plus the difference
counts per category, reported separately and never summed.

Legacy LOC is attributed to a kind through the inventory's per-rule line
ranges. Lines claimed by rows of more than one kind, and rows whose kind is not
a K-kind (`ArchitectureRulesTestCase` and the config/spec-validate rows), go to
a **shared infrastructure** row instead of being spread over the kinds. Lines no
row claims are split by position into *shared helper machinery* (between two
claimed ranges — the scanning and assertion helpers) and *file scaffolding*
(before the first or after the last claimed line). The script imports
`count_legacy_loc.classify`'s definition of a code line rather than
re-implementing it, and ends with six cross-checks that the per-kind sums
reconcile with `legacy_loc.json` and `spec_loc.json`; it exits 1 if any fails.
