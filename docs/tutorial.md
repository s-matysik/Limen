---
title: Tutorial
---

[Home](./) · [Tutorial](tutorial) · [Using limen with coding agents](agents) · [Rule kinds](rules) · [CLI reference](cli) · [Evaluation](evaluation)

This tutorial builds a contract for a small statistics package. Every output below was produced by
running limen on these files.

## 1. The project

```text
mylib/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── linalg.py          # bottom layer
│   └── legacy.py          # old adapter that imports an estimator (known debt)
├── estimators/
│   ├── __init__.py        # public entry point: re-exports every estimator
│   ├── ols.py
│   └── ridge.py           # the new estimator
└── plotting/
    └── __init__.py
```

Three rules hold in this project:

1. `mylib.core` is the bottom layer and must not import `mylib.estimators`. One old adapter,
   `core/legacy.py`, already breaks this and is scheduled for removal.
2. Estimators compute; they never import `mylib.plotting`.
3. Every estimator module is re-exported by `mylib/estimators/__init__.py`, so users find it.

A fourth, softer convention: library code does not `print`.

## 2. Write the contract

Save as `architecture.yaml` in the repository root:

```yaml
version: 1
project: mylib

roots:
  lib: { path: ., language: python }

rules:
  - id: core-does-not-import-estimators
    kind: forbidden-target
    severity: error
    scope: { roots: [lib], paths: ["mylib/core/**"] }
    forbid: ["mylib.estimators", "mylib.estimators.*"]
    message: "mylib.core is the bottom layer and must not import estimators."
    remedy: "Move the shared code into mylib.core and import it from the estimator."
    exempt:
      - target: "mylib.estimators.ols"
        scope: "mylib/core/legacy.py"
        reason: "legacy adapter, scheduled for removal"
        owner: "maintainers"
        since: 2026-10-01
        expires: 2027-06-30

  - id: estimators-do-not-plot
    kind: forbidden-target
    severity: error
    scope: { roots: [lib], paths: ["mylib/estimators/**"] }
    forbid: ["mylib.plotting", "mylib.plotting.*"]
    message: "Estimators compute; plotting lives in mylib.plotting."
    remedy: "Return the data and let the caller plot it."

  - id: no-print-in-library
    kind: pattern-ban
    severity: warning
    scope: { roots: [lib], paths: ["mylib/**/*.py"] }
    pattern: '\bprint\('
    message: "Library code does not print."
    remedy: "Use the logging module."

  - id: new-estimators-exported
    kind: required-inbound
    severity: error
    scope:
      roots: [lib]
      paths: ["mylib/estimators/*.py"]
      exclude: ["**/__init__.py"]
    from: ["mylib/estimators/__init__.py"]
    message: "Every estimator module is re-exported by mylib/estimators/__init__.py."
    remedy: "Add 'from mylib.estimators.<module> import <Class>' to mylib/estimators/__init__.py."
```

Points to note:

- **Roots.** For a Python package that imports itself absolutely (`from mylib.core import ...`),
  declare the root at repository level, `path: .`. With `path: mylib`, module names would lose their
  `mylib.` prefix and would not match the imports.
- **Severity.** `error` fails CI; `warning` is reported and fails only under `--strict`.
- **Message and remedy** are written for whoever will read the finding: a reviewer or an agent.
- **Exemptions** belong to a rule. Each names what is tolerated, why, who owns it and until when.
  After `expires`, the exemption stops suppressing and the finding becomes an error.

## 3. Validate the contract itself

```bash
limen spec validate --spec "$PWD/architecture.yaml" --workspace "$PWD" --strict-spec
```

```text
spec OK: 4 rule(s), 1 root(s)
```

`--spec` must be an absolute path; a relative path is resolved against `--workspace`.

## 4. Check the code

Suppose `ridge.py` was just added: it imports `mylib.plotting` and nobody exports it.

```bash
limen check --spec "$PWD/architecture.yaml" --workspace "$PWD"
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

`core/legacy.py` is not reported: its import is covered by an active exemption. For machines, add
`--format json` (also `junit` and `sarif`).

## 5. Fix and check again

Remove the plotting import from `ridge.py` and add one line to `mylib/estimators/__init__.py`:

```python
from mylib.estimators.ols import OLS
from mylib.estimators.ridge import Ridge
```

```text
limen check — mylib
===================
WARNING [no-print-in-library] mylib/plotting/__init__.py:2
        target: print(
        Library code does not print.
        remedy: Use the logging module.
        spec:   rules[2].exempt_files

Summary: 0 error(s), 1 warning(s), unresolved: 0. Exit code 0.
```

Exit code 0: the change can be merged. The warning remains visible without blocking.

## 6. Exemptions expire

Run the same check after the exemption's expiry date (here simulated with `--today`):

```bash
limen check --spec "$PWD/architecture.yaml" --workspace "$PWD" --today 2027-07-01
```

```text
limen check — mylib
===================
ERROR   [core-does-not-import-estimators] mylib/core/legacy.py:1
        target: mylib.estimators.ols
        mylib.core is the bottom layer and must not import estimators. [Exemption expired on 2027-06-30; reason: legacy adapter, scheduled for removal]
        remedy: Move the shared code into mylib.core and import it from the estimator.
        spec:   rules[0].exempt[0]
WARNING [no-print-in-library] mylib/plotting/__init__.py:2
        target: print(
        Library code does not print.
        remedy: Use the logging module.
        spec:   rules[2].exempt_files

Summary: 1 error(s), 1 warning(s), unresolved: 0. Exit code 1.
```

Tolerated debt does not become permanent by accident. `--today` (or the `LIMEN_TODAY` environment
variable) also makes results reproducible: the same tree, contract, engine version and date always
give byte-identical output.

## 7. Show the rules to an agent

```bash
limen context --spec "$PWD/architecture.yaml" --workspace "$PWD" --paths mylib/estimators/
```

```text
## Architecture contract for the paths you are about to change

Paths: mylib/estimators/

### Hard rules (violating these fails CI)
- [estimators-do-not-plot] Never import: `mylib.plotting`, 'mylib.plotting.*'.
  Remedy: Return the data and let the caller plot it.
- [new-estimators-exported] Every estimator module is re-exported by mylib/estimators/__init__.py.
  Anything you add under this scope must be referenced from `mylib/estimators/__init__.py`; a file nothing there imports is a violation on that file.
  Remedy: Add 'from mylib.estimators.<module> import <Class>' to mylib/estimators/__init__.py.

### Advisory rules (warnings)
- [no-print-in-library] Library code does not print.
  Forbidden pattern in these files: `\bprint\(`.
  Remedy: Use the logging module.

### Active exemptions touching these paths
(none)

### Verify before you report done
    limen check --changed-only HEAD
```

See [Using limen with coding agents](agents) for the full loop.

## Troubleshooting

- **A file that is clearly imported is reported by `required-inbound`.** Check that the root is
  declared at repository level (`path: .`) so module names match absolute imports.
- **Results did not change after editing `roots`.** limen caches extracted facts in `.limen/` in the
  workspace; delete that directory after changing roots.
- **"specification file not found".** Pass `--spec` as an absolute path.
