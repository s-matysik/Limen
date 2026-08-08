---
title: CLI reference
---

[Home](./) · **CLI reference** · [Rule kinds](rules) · [Evaluation](evaluation)

All commands accept `--spec`, `--workspace`, `--facts` (pre-extracted facts JSON, repeatable) and
`--today` (freeze the clock for exemption expiry).

{: .warning }
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
| `--changed-only` | restrict to files touched in a revision range |
| `--baseline-update` | rewrite the accepted-debt snapshot |

```bash
limen check --spec "$PWD/architecture.yaml" --workspace "$PWD" --format sarif > limen.sarif
```

## `limen context`

Renders the contract for the files an agent is about to modify.

```
limen context [--paths PATH ...] [--for-diff REV_RANGE]
              [--format {md,json}] [--budget-tokens N]
```

When the token budget binds, a `warning` rule is dropped before an `error` rule is — the agent
never loses a hard constraint to make room for a soft one.

```bash
limen context --spec "$PWD/architecture.yaml" --workspace "$PWD" \
              --paths src/modules/projects --budget-tokens 4000
```

## `limen review`

```
limen review --for-diff REV_RANGE [--format {md}]
```

A checklist for a verifying agent, scoped to a diff. Deliberately a report rather than a verdict:
the verdict is `limen check`.

## `limen spec validate`

Checks the **specification**, not the code — unknown owners, unresolvable paths, dead allowlist
entries.

```
limen spec validate [--strict-spec] [--no-facts]
```

`--no-facts` runs structural checks only and skips extraction, which is fast enough for a
pre-commit hook.

## `limen baseline`

```
limen baseline {capture,diff,prune}
```

Freeze accepted debt, compare against it, and remove entries that no longer correspond to anything
in the tree.

## `limen graph`

```
limen graph [--format {dot,json,mermaid}]
```

## `limen init`

```
limen init [--detect] [--workspace WS]
```

Emits a skeleton specification inferred from an existing repository. A starting point for
authoring, not a finished contract.

## Continuous integration

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
      - run: pip install limen-arch
      - run: limen spec validate --spec "$PWD/architecture.yaml" --workspace "$PWD" --strict-spec
      - run: limen check --spec "$PWD/architecture.yaml" --workspace "$PWD" --format junit > arch.xml
```
