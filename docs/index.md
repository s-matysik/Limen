---
title: limen
---

[Home](./) · [Tutorial](tutorial) · [Using limen with coding agents](agents) · [Rule kinds](rules) · [CLI reference](cli) · [Evaluation](evaluation)

**One architecture contract. Checked in CI, shown to coding agents, readable by people.**

*limen* (Latin: threshold, boundary) lets a project write its architectural rules once, in a
declarative `architecture.yaml`, and use that one file in three places:

| Command | Who uses it | What it gives |
| --- | --- | --- |
| `limen check` | CI, reviewers | Pass or fail, and for every violation: file, line, offending import or text, rule, message and remedy. No model, no network, byte-identical output for the same inputs. |
| `limen context --paths ...` | coding agents, developers | The rules that apply to the files about to be changed, including rules for a new file in a directory, plus the command that will verify the work. |
| `limen review --for-diff ...` | a reviewing agent or person | A checklist for a diff. A report, not a verdict; the verdict is `limen check`. |

## Why

Researchers and organisations increasingly let AI agents change their code. The rules that keep a
codebase coherent, such as "the core package must not import the estimators" or "every new
estimator is exported from the package", usually live in three places that nobody checks against
each other: a checker script in CI, design documents, and instruction files such as `AGENTS.md` or
`CLAUDE.md`. Instructions tell an agent what to do; they do not tell anyone whether it was done.

With limen the rule that fails the build is the same rule, from the same file, that the agent is
shown before it starts and that it receives, with a remedy, when it gets it wrong.

## Installation

```bash
pip install "git+https://github.com/s-matysik/Limen.git"
limen --version
```

or, for development:

```bash
git clone https://github.com/s-matysik/Limen.git
cd Limen
pip install -e ".[dev]"
python -m pytest -q
```

Requires Python 3.10 or later and PyYAML. No build step, no network access and no language model
at check time.

## Five-minute example

A small package `mylib` has three architectural rules. The full walkthrough is in the
[tutorial](tutorial); in short:

```bash
limen spec validate --spec "$PWD/architecture.yaml" --workspace "$PWD" --strict-spec
limen check         --spec "$PWD/architecture.yaml" --workspace "$PWD"
```

```text
limen check — mylib
===================
ERROR   [estimators-do-not-plot] mylib/estimators/ridge.py:2
        target: mylib.plotting
        Estimators compute; plotting lives in mylib.plotting.
        remedy: Return the data and let the caller plot it.
        spec:   rules[1].exempt
ERROR   [new-estimators-exported] mylib/estimators/ridge.py
        target: mylib/estimators/ridge.py
        Every estimator module is re-exported by mylib/estimators/__init__.py.
        remedy: Add 'from mylib.estimators.<module> import <Class>' to mylib/estimators/__init__.py.
        spec:   rules[3].exempt
WARNING [no-print-in-library] mylib/plotting/__init__.py:2
        target: print(
        Library code does not print.
        remedy: Use the logging module.
        spec:   rules[2].exempt_files

Summary: 2 error(s), 1 warning(s), unresolved: 0. Exit code 1.
```

Every finding names the file, the line, the rule and the fix. Exit code 1 blocks the merge.

## What the evaluation showed

Details, protocols and caveats are in the accompanying paper and in [Evaluation](evaluation).

| Question | Result |
| --- | --- |
| Can it replace an existing checker? | A production checker of 47 handwritten rules (1,246 lines) became 36 declarations (526 lines) with zero unexplained differences; 35 of 35 seeded violations detected. |
| Does it agree with established tools? | On 12 repositories using dependency-cruiser, deptrac, import-control or import-linter: precision 96.9%, recall 93.4% for forbidden imports. |
| Do its findings help a coding agent? | In 25 open-source projects, after an agent broke a documented rule, one round with the findings of `limen check` made 129 of 135 answers pass the check, against 105 with the same rules as prose and 13 with a generic "review your work" request. |

Limits: one model family; changes that add a module; success means passing the check, not
functional correctness. Rules supplied before a change helped equally as limen context or as
prose; the advantage of limen is the check after the change.

## Where to go next

- [Tutorial](tutorial): write a first contract, read the findings, fix them, add exemptions.
- [Using limen with coding agents](agents): the loop context, change, check, fix.
- [Rule kinds](rules): the twelve kinds and their parameters.
- [CLI reference](cli): all commands, options, exit codes and a GitHub Actions workflow.

## Citation and licence

See [`CITATION.cff`](https://github.com/s-matysik/Limen/blob/main/CITATION.cff). MIT licence.
