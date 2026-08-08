---
title: Rule kinds
---

[Home](./) · [CLI reference](cli) · **Rule kinds** · [Evaluation](evaluation)

Eleven kinds. Every rule carries a severity of `error` or `warning`; the two are never conflated in
a report or an exit code.

| Kind | Enforces | Typical use |
| --- | --- | --- |
| `layer-dependency` | which layer may depend on which | domain must not import infrastructure |
| `ownership` | every artefact has a declared owner | each model belongs to exactly one module |
| `consumer-allowlist` | who may consume a given unit | only listed modules read this model |
| `public-api-only` | access only through a declared public surface | cross-module calls go through contracts |
| `scoped-allowlist` | narrow exceptions bound to a path | this directory may import these classes |
| `forbidden-target` | targets that must never be imported | no direct framework access from domain |
| `baseline-snapshot` | accepted debt, frozen and monitored | existing violations must not grow |
| `pattern-ban` | textual patterns that must not appear | no `console.log` in shipped code |
| `structure` | required files, directories, documentation | every module has a README with a contract |
| `metric-threshold` | size and complexity limits | no page component over 400 lines |
| `reachability` | transitive dependency constraints | this unit must not reach that one, however indirectly |

## Languages

Extraction is syntactic — imports and declarations, not runtime behaviour.

| Language | Extracted |
| --- | --- |
| PHP | `namespace`, `use`, class/interface/trait declarations |
| JavaScript / TypeScript | `import`, `require`, re-exports |
| Python | `import`, `from ... import` |
| Java | `package`, `import` |

## Multi-repository specifications

`roots[].repo` lets one specification span several repositories under a common workspace directory.
This is what makes cross-repository rules possible at all: a per-repository checker cannot see a
boundary that runs between repositories.

```yaml
roots:
  - id: host_app
    repo: litrev-backend
  - id: shared
    repo: backend-shared
rules:
  - id: shared-not-into-host-modules
    kind: forbidden-target
    from: shared
    to: host_app
    severity: error
```

## Exemptions

```yaml
exemptions:
  - rule: shared-not-into-host-modules
    path: src/Legacy/Importer.php
    reason: "pre-modularisation importer, scheduled for extraction"
    owner: team-platform
    since: 2026-05-02
    expires: 2026-11-01
```

`--today` freezes the clock, which makes expiry deterministic in tests and in historical analysis.

{: .note }
In the published study, exemptions legalised debt with a median actual age of 76 days against a
declared lifetime of 149 days, and all were still open at the end of the observation window. An
exemption without `expires` is forever.
