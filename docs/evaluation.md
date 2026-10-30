---
title: Reproducing the evaluation
---

[Home](./) · [Tutorial](tutorial) · [Using limen with coding agents](agents) · [Rule kinds](rules) · [CLI reference](cli) · [Evaluation](evaluation)

The numbers for the fidelity, translation and history studies are produced by scripts in `eval/`.
`research-report.md` is generated from the result files.

## Prerequisites

Python 3.10+ for limen. Reproducing the equivalence study additionally needs **PHP 8.2+** and
**Node.js 20+**, because the legacy checkers are executed unmodified as the reference rather than
reimplemented.

```bash
git clone https://github.com/s-matysik/Limen.git && cd Limen
pip install -e .
export PYTHONPATH="$PWD/src"
```

## Study 1: equivalence with a production checker

```bash
python eval/run_legacy_oracle.py          # unmodified legacy checkers
python eval/run_limen_check.py            # limen side
python eval/study1_equivalence.py         # classify every difference; non-zero exit if any is unclassified
python eval/study1_table.py               # rule kind x rules x LOC
python eval/mutation_sensitivity.py       # one injected violation per rule
python eval/validate_shim_against_laravel.py --laravel-root <root>
```

The acceptance criterion, zero unclassified differences, is enforced by the exit code.

## Study 1b: differential against native checkers

```bash
GITHUB_TOKEN=... python eval/build_corpus.py   # sampling; writes eval/corpus.jsonl
python eval/study1b_corpus.py                  # differential per repository
```

720 repositories screened, 60 included, 12 yielding a comparable run.

## Study 2: repository history

```bash
python eval/study2_history.py     # per-commit measurement
python eval/study2_metrics.py     # metrics, CSVs, figure
```

**Note.** `units.registry_policy: exact` aborts historical trees with exit 3 before any rule runs.
Relax it to `open` for historical analysis.

## Agent studies

The first agent pilot (`eval/study3_agent/`) produced too few violations to answer its question;
see [`docs/study3-redesign.md`](https://github.com/s-matysik/Limen/blob/main/docs/study3-redesign.md).
The later prevention and repair studies on 45 open-source repositories, summarised on the
[home page](./), are described with protocols, prompts and per-repository results in the
supplementary material of the accompanying paper; their data and scripts are deposited with the
paper's data archive.

## Regenerating the report

```bash
python eval/build_report.py       # rewrites research-report.md from eval/results/
```

## Not in the repository

Third-party source trees downloaded by the corpus scripts (7.4 GB) and raw per-run agent outputs.
The corpus is reproducible from the pinned SHAs in `eval/corpus.jsonl`.
