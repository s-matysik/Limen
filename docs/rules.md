---
title: Rule kinds
---

[Home](./) · [Tutorial](tutorial) · [Using limen with coding agents](agents) · [Rule kinds](rules) · [CLI reference](cli) · [Evaluation](evaluation)

Twelve kinds. Every rule has `id`, `kind`, `severity` (`error`, `warning` or `info`) and `message`,
and should have a `remedy`. Most rules take a `scope`: `roots` (ids from `roots`), `paths` and
`include` (globs), and `exclude`. The JSON schema is
[`schemas/architecture-spec-1.json`](https://github.com/s-matysik/Limen/blob/main/schemas/architecture-spec-1.json);
unknown keys at rule level are rejected.

| Kind | Constraint | Main parameters |
| --- | --- | --- |
| `layer-dependency` | which declared layer may depend on which | `layers` section, `forbid`, `exempt` |
| `ownership` | every artefact belongs to a declared unit | `artifact_set`, `owners`, `require_every_artifact_owned` |
| `consumer-allowlist` | only listed units may import a target | `artifact_set`, `allow` |
| `public-api-only` | consumers import only the public surface of a unit | `public_surface` section, `exempt` |
| `scoped-allowlist` | inside a scope, only listed targets may be imported | `target_prefix`, `allow` |
| `forbidden-target` | inside a scope, listed targets must not be imported | `forbid` (names or globs), `exempt` |
| `baseline-snapshot` | recorded current state may shrink but not grow | `target_pattern`, `expected`, `exclude_units` |
| `pattern-ban` | a text pattern must not occur in scoped files | `pattern` (Python regex), `exempt_files` |
| `structure` | required directories and files exist | `assert` (for example `required_files`), `exempt` |
| `metric-threshold` | file size stays below a limit | `metric: lines`, `max` |
| `reachability` | independence, layering or forbidden paths over transitive dependencies | `mode`, `units`, `order`, `forbid`, `max_depth`, `ignore_edges` |
| `required-inbound` | every scoped file is imported by one of the listed files (a positive obligation) | `from` (globs), `via`, `exempt` |

## Examples

**forbidden-target**: the core must not import estimators.

```yaml
- id: core-does-not-import-estimators
  kind: forbidden-target
  severity: error
  scope: { roots: [lib], paths: ["mylib/core/**"] }
  forbid: ["mylib.estimators", "mylib.estimators.*"]
  message: "mylib.core is the bottom layer and must not import estimators."
  remedy: "Move the shared code into mylib.core and import it from the estimator."
```

**pattern-ban**: a regular expression that must not match. Patterns are compiled without
multi-line mode; use the inline flag `(?m)` to anchor at line starts.

```yaml
- id: no-print-in-library
  kind: pattern-ban
  severity: warning
  scope: { roots: [lib], paths: ["mylib/**/*.py"] }
  pattern: '\bprint\('
  message: "Library code does not print."
  remedy: "Use the logging module."
  exempt_files:
    - { path: mylib/cli.py, reason: "command-line entry point", owner: maintainers }
```

**required-inbound**: every estimator is exported by the package. This is the only kind that
requires an edge rather than forbidding one, so it catches a new module that nothing imports.

```yaml
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

## Roots

```yaml
roots:
  lib: { path: ., language: python }       # repository-level root
  web: { path: frontend/src, language: js }
```

`language` is `python`, `js` (JavaScript and TypeScript), `php` or `java`. A root may also name a
`repo` so that one contract can span several repositories in a common workspace. For Python
packages that import themselves absolutely, use a repository-level root (`path: .`).

## Exemptions

Exemptions are attached to the rule they relax. Entries under `exempt` name a `target` (the
imported name or glob) and optionally a `scope` (glob of the importing file); entries under
`exempt_files` (for `pattern-ban`) and `exempt` of `required-inbound` name a `path`. All may carry
`reason`, `owner`, `since`, `expires` and `ticket`.

```yaml
exempt:
  - target: "mylib.estimators.ols"
    scope: "mylib/core/legacy.py"
    reason: "legacy adapter, scheduled for removal"
    owner: "maintainers"
    since: 2026-10-01
    expires: 2027-06-30
```

An active exemption suppresses the finding. After `expires`, the finding returns as an error that
names the expired exemption. An exemption that matches nothing is reported as dead under
`--strict-spec`. `--today` (or `LIMEN_TODAY`) fixes the evaluation date.

## Languages

Extraction is syntactic: imports and declarations, not runtime behaviour.

| Language | Extracted |
| --- | --- |
| Python | `import`, `from ... import` (including relative imports and submodules imported by name), re-exports through `__init__.py` |
| JavaScript / TypeScript | `import`, `require`, re-exports |
| PHP | `namespace`, `use`, class, interface and trait declarations |
| Java | `package`, `import` |
