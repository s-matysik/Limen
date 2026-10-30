# limen

*limen* (Latin: threshold, boundary) is a declarative architecture contract with three consumers
driven by **one** specification (`architecture.yaml`):

1. **A deterministic CI gate** — `limen check`. Binary result, no LLM inside. Two runs on the
   same file tree produce a byte-identical report.
2. **A scoped context generator for coding agents** — `limen context --paths ...`. From the same
   spec it emits a Markdown contract narrowed to the files the agent is about to touch, with
   concrete contract class names taken from extracted facts, active exemptions, and the exact
   verification command. Token-budgeted; an `error` rule is never dropped while a `warning` remains.
3. **A review checklist for a verifying agent** — `limen review --for-diff`. A report, not a verdict.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

**Documentation and tutorial:** https://s-matysik.github.io/Limen/

## What limen is not

- **Not a linter.** It reasons about units, boundaries and dependencies, not code style.
- **Not an LLM tool.** `limen check` contains no model and makes no network call. The agent-facing
  command only *renders* the same rules the gate enforces.
- **Not a whole-program analyser.** Extraction is syntactic: imports and declarations, not runtime
  behaviour. This keeps the run linear in tree size and free of non-termination risks.

## Installation

Requires Python 3.10+ and PyYAML. No build step, no network access, no LLM at check time.

```bash
pip install "git+https://github.com/s-matysik/Limen.git"
```

For development: `git clone https://github.com/s-matysik/Limen.git && cd Limen && pip install -e ".[dev]"`.

Reproducing the published evaluation additionally requires PHP 8.2+ and Node.js 20+, because the
legacy checkers are executed unmodified as oracles.

## Quick start

Bootstrap a specification from an existing repository, validate it, then enforce it:

```bash
limen init    --workspace /abs/path/to/repo > architecture.yaml
limen spec validate --spec /abs/path/architecture.yaml --workspace /abs/path/repo --strict-spec
limen check   --spec /abs/path/architecture.yaml --workspace /abs/path/repo
```

> **`--spec` must be an absolute path.** A relative path is resolved against `--workspace`, which
> produces a misleading "specification file not found".

## Usage

### Commands

| Command | Purpose |
| --- | --- |
| `limen check` | Deterministic CI gate; exits non-zero on violations |
| `limen context --paths <p>` | Markdown contract scoped to the files an agent will modify |
| `limen review --for-diff <range>` | Checklist for a verifying agent |
| `limen spec validate --strict-spec` | Referential integrity of the specification itself |
| `limen baseline capture \| diff \| prune` | Snapshots of accepted debt |
| `limen graph` | Export the dependency graph |
| `limen init` | Skeleton specification from an existing repository |

### Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | no errors |
| 1 | rule violation at `error` severity |
| 2 | warnings present under `--strict` |
| 3 | invalid specification |
| 4 | extractor failure |

### In continuous integration

```yaml
- name: Architecture contract
  run: |
    pip install "git+https://github.com/s-matysik/Limen.git"
    limen check --spec "$PWD/architecture.yaml" --workspace "$PWD" --format json
```

### With a coding agent

```bash
limen context --spec "$PWD/architecture.yaml" --workspace "$PWD" \
              --paths src/modules/projects --budget-tokens 4000
```

The emitted Markdown is the contract for exactly those paths — the rules the agent must respect,
the active exemptions, and the command that will verify its work.

## Rule kinds

Twelve kinds are supported; each rule carries a severity of `error` or `warning`, and the two are
never conflated in a report or an exit code.

| Kind | Enforces |
| --- | --- |
| `layer-dependency` | which layer may depend on which |
| `ownership` | every artefact has a declared owner |
| `consumer-allowlist` | who may consume a given unit |
| `public-api-only` | access only through a declared public surface |
| `scoped-allowlist` | narrow exceptions bound to a path |
| `forbidden-target` | specific targets that must not be imported |
| `baseline-snapshot` | accepted debt, frozen and monitored |
| `pattern-ban` | textual patterns that must not appear |
| `structure` | required files, directories, documentation |
| `metric-threshold` | size and complexity limits |
| `reachability` | transitive dependency constraints |
| `required-inbound` | every scoped file is imported by a designated file (for example, new modules are exported) |

Supported languages: PHP, JavaScript/TypeScript, Python, Java.

## Exemptions

Every exemption carries a `reason`, an `owner` and a `since` date; when it legalises debt rather
than a permanent decision it also carries `expires`. A suppression is therefore auditable rather
than invisible:

```yaml
rules:
  - id: shared-not-into-host-modules
    kind: forbidden-target
    severity: error
    scope: { roots: [shared], paths: ["src/**"] }
    forbid: ["App\\Modules\\*"]
    message: "Shared code must not depend on host-application modules."
    remedy: "Move the dependency behind a contract in the shared package."
    exempt:
      - target: "App\\Modules\\*"
        scope: "src/legacy/Importer.php"
        reason: "pre-modularisation importer, scheduled for extraction"
        owner: "team-platform"
        since: 2026-05-02
        expires: 2026-11-01
```

Exemptions belong to the rule they relax (`exempt`, or `exempt_files` for `pattern-ban`). After
`expires` the finding returns as an error.

## Reproducing the evaluation

Every number in the accompanying paper is produced by a script in `eval/` and reproducible with a
single command:

```bash
export PYTHONPATH="$PWD/src"

python eval/freeze_snapshot.py --check    # snapshot identity unchanged
python -m pytest -q                       # test suite

python eval/run_legacy_oracle.py          # oracle: unmodified legacy checkers
python eval/run_limen_check.py            # limen side
python eval/study1_equivalence.py         # equivalence; non-zero exit if anything unclassified
python eval/study1_table.py               # rule-kind x rules x LOC table
python eval/mutation_sensitivity.py       # one injected violation per rule
python eval/validate_shim_against_laravel.py --laravel-root <root>

GITHUB_TOKEN=... python eval/build_corpus.py   # corpus sampling
python eval/study1b_corpus.py                  # differential vs native checkers
python eval/study2_history.py                  # per-commit history
python eval/study2_metrics.py                  # metrics and figure

python eval/build_report.py               # regenerates research-report.md
```

Results land in `eval/results/`. `research-report.md` states, for every figure in the paper, the
command that reproduces it.

### What is deliberately not in this repository

- Third-party source trees downloaded by the corpus scripts (7.4 GB). They are reproducible from
  the pinned SHAs in `eval/corpus.jsonl`.
- Raw per-run agent outputs; the aggregate CSV/JSON files are kept.

## Documentation

- [`research-report.md`](research-report.md) — every measured number with its reproducing command
- [`ADOPTION.md`](ADOPTION.md) — what the studies did and did not establish, and what follows for adoption
- [`docs/deviations.md`](docs/deviations.md) — where limen deliberately departs from legacy semantics
- [`docs/study3-redesign.md`](docs/study3-redesign.md) — protocol for the agent experiment

## Citation

See [`CITATION.cff`](CITATION.cff), or cite the software paper:

> S. Matysik, limen: a declarative architecture contract for deterministic CI enforcement and
> scoped context generation for coding agents, SoftwareX (under review), 2026.

## License

MIT — see [`LICENSE`](LICENSE).
