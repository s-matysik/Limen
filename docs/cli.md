---
title: CLI reference
---

[Home](./) · [Tutorial](tutorial) · [Using limen with coding agents](agents) · [Rule kinds](rules) · [CLI reference](cli) · [Evaluation](evaluation)

Most commands accept `--spec` (absolute path to `architecture.yaml`), `--workspace` (repository
root), `--facts` (pre-extracted facts JSON, repeatable) and `--today` (evaluation date for
exemption expiry; the environment variable `LIMEN_TODAY` has the same effect).

**`--spec` must be absolute.** Relative paths resolve against `--workspace`.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | no errors |
| 1 | rule violation at `error` severity |
| 2 | warnings only, under `--strict` |
| 3 | invalid specification (or dead entries under `--strict-spec`) |
| 4 | extractor failure |

## `limen check`

The gate. Contains no model and makes no network call.

```
limen check [--spec SPEC] [--workspace WS] [--facts F] [--today DATE]
            [--format {human,json,junit,sarif}] [--strict] [--strict-spec]
            [--only RULE_IDS] [--changed-only REV_RANGE] [--baseline-update]
```

| Option | Effect |
| --- | --- |
| `--format` | `human`, `json`, `junit` (CI test reports), `sarif` (code scanning) |
| `--strict` | warnings alone exit 2 |
| `--strict-spec` | dead specification entries become errors (exit 3) |
| `--only` | comma-separated rule ids |
| `--changed-only` | restrict file-level rules to files touched in a revision range; whole-tree rules (`ownership`, `baseline-snapshot`, `required-inbound`) are still evaluated on the full tree |
| `--baseline-update` | rewrite the accepted-debt snapshot |

## `limen context`

Renders the rules that apply to the paths an agent is about to change. A directory path also
lists rules that would apply to a new file created there.

```
limen context [--paths PATH ...] [--for-diff REV_RANGE]
              [--format {md,json}] [--budget-tokens N]
```

When the token budget binds, a `warning` rule is dropped before an `error` rule.

## `limen review`

```
limen review --for-diff REV_RANGE
```

A checklist for a reviewing agent or person, scoped to a diff. A report rather than a verdict.

## `limen spec validate`

Checks the specification, not the code: unknown keys and owners, unresolvable paths, dead
allowlist and exemption entries.

```
limen spec validate [--strict-spec] [--no-facts]
```

`--no-facts` skips extraction, which is fast enough for a pre-commit hook.

## Other commands

| Command | Purpose |
| --- | --- |
| `limen baseline {capture,diff,prune}` | freeze accepted debt, compare against it, remove stale entries |
| `limen graph [--format {dot,json,mermaid}]` | export the dependency graph |
| `limen extract` | write the extracted facts |
| `limen init [--detect]` | emit a skeleton specification inferred from the repository; a starting point, not a finished contract |

## GitHub Actions

`.github/workflows/architecture.yml`:

```yaml
name: architecture
on: [push, pull_request]
jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.12'}
      - run: pip install "git+https://github.com/s-matysik/Limen.git"
      - run: limen spec validate --spec "$PWD/architecture.yaml" --workspace "$PWD" --strict-spec
      - run: limen check --spec "$PWD/architecture.yaml" --workspace "$PWD"
```

The job fails when `limen check` exits non-zero, which blocks the merge if the job is a required
status check. The human-readable findings appear in the job log; add `--format sarif` and upload
the file to show them in code scanning.
