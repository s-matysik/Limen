---
title: Reproducing the evaluation
---

[Home](./) · [CLI reference](cli) · [Rule kinds](rules) · **Evaluation**

Every number in the accompanying paper is produced by a script in `eval/` and reproducible with a
single command. Nothing is typed by hand: `research-report.md` is itself generated from the result
files.

## Prerequisites

Python 3.10+ for limen. Reproducing the equivalence study additionally needs **PHP 8.2+** and
**Node.js 20+**, because the legacy checkers are executed unmodified as oracles rather than
reimplemented.

```bash
git clone https://github.com/s-matysik/Limen.git && cd Limen
pip install -e .
export PYTHONPATH="$PWD/src"
```

## Study 1 — equivalence

```bash
python eval/run_legacy_oracle.py          # unmodified legacy checkers
python eval/run_limen_check.py            # limen side
python eval/study1_equivalence.py         # classify every difference; non-zero exit if any is unclassified
python eval/study1_table.py               # rule kind x rules x LOC
python eval/mutation_sensitivity.py       # one injected violation per rule
python eval/validate_shim_against_laravel.py --laravel-root <root>
```

The acceptance criterion — zero unclassified differences — is enforced by the exit code, not by
inspection.

## Study 1b — differential against native checkers

```bash
GITHUB_TOKEN=... python eval/build_corpus.py   # sampling; writes eval/corpus.jsonl
python eval/study1b_corpus.py                  # differential per repository
```

720 repositories screened, 60 included, 12 yielding a comparable run. The screening flow is part of
the result: 108 screened repositories carry a checker configuration that declares no rule at all.

## Study 2 — repository history

```bash
python eval/study2_history.py     # per-commit measurement
python eval/study2_metrics.py     # metrics, CSVs, figure
```

{: .warning }
`units.registry_policy: exact` aborts historical trees with exit 3 before any rule runs. Without
relaxing it to `open`, the study returns zero findings for every commit and the project looks clean.

## Study 3 — coding agents

```bash
python eval/study3_agent/select_tasks.py
python eval/study3_agent/run.py
python eval/study3_agent/analyze.py
```

Both attempts are reported as negative results with diagnosed causes. See
[`docs/study3-redesign.md`](https://github.com/s-matysik/Limen/blob/main/docs/study3-redesign.md)
for the protocol a properly powered replication would need.

## Regenerating the report

```bash
python eval/build_report.py       # rewrites research-report.md from eval/results/
```

## Not in the repository

Third-party source trees downloaded by the corpus scripts (7.4 GB) and raw per-run agent outputs.
Both are reproducible: the corpus from the pinned SHAs in `eval/corpus.jsonl`, the agent runs from
`eval/results/study3_plan.json`.
