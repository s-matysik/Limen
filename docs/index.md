---
title: limen
---

**One declarative specification. Three consumers. No LLM in the gate.**

*limen* (Latin: threshold, boundary) expresses architecture rules once, as data, and drives a
deterministic CI gate, a task-scoped context generator for coding agents, and a review checklist
from that single file.

[Get started](#installation) · [Rule kinds](rules) · [CLI reference](cli) ·
[Reproducing the evaluation](evaluation) · [GitHub](https://github.com/s-matysik/Limen)

---

## Why

Architecture rules in polyglot code bases end up scattered across language-specific checkers —
ArchUnit for Java, Deptrac for PHP, import-linter for Python, dependency-cruiser for JavaScript.
Each is a single-purpose artefact: it answers one question, in CI, and nothing else can use what it
knows. Meanwhile coding agents are told about architecture in prose (`AGENTS.md`, `CLAUDE.md`),
which nothing verifies and which drifts from the code.

limen removes the duplication. The rules that fail your build are the same rules, from the same
file, that the agent is shown before it writes anything.

## The three consumers

| Command | What it produces |
| --- | --- |
| `limen check` | Deterministic pass/fail. No model, no network. Two runs on the same tree produce a byte-identical report. |
| `limen context --paths ...` | A Markdown contract narrowed to the files being modified, token-budgeted, with the exact verification command. |
| `limen review --for-diff ...` | A checklist for a verifying agent. A report, not a verdict. |

## Installation

```bash
pip install limen-arch
```

Requires Python 3.10+ and PyYAML. Nothing else: no build step, no network access, no LLM at check
time.

## Sixty seconds

```bash
# 1. bootstrap a specification from what you already have
limen init --workspace "$PWD" > architecture.yaml

# 2. check the specification itself before checking the code
limen spec validate --spec "$PWD/architecture.yaml" --workspace "$PWD" --strict-spec

# 3. enforce it
limen check --spec "$PWD/architecture.yaml" --workspace "$PWD"
```

{: .warning }
**`--spec` must be an absolute path.** A relative path is resolved against `--workspace`, which
produces a misleading *"specification file not found"*.

## What a specification looks like

```yaml
version: 1
roots:
  - id: host_app
    repo: litrev-backend
units:
  - id: projects
    kind: module
    paths: ["app/Modules/Projects/**"]
    public_surface: ["app/Modules/Projects/Contracts/**"]
rules:
  - id: modules-through-contracts-only
    kind: public-api-only
    severity: error
    message: "cross-module access goes through Contracts\\"
exemptions:
  - rule: modules-through-contracts-only
    path: app/Legacy/Importer.php
    reason: "pre-modularisation importer, scheduled for extraction"
    owner: team-platform
    since: 2026-05-02
    expires: 2026-11-01
```

Every exemption carries a reason, an owner and a date. When it legalises debt rather than a
permanent decision, it also carries an expiry. A suppression is auditable rather than invisible.

## Evidence

limen was retrofitted onto a nine-repository polyglot system whose rules were enforced by three
unrelated mechanisms.

| | |
| --- | ---: |
| Legacy rules replaced | 47 |
| Legacy checker code replaced | 1,246 lines |
| Resulting specification | 36 declarations, 526 lines |
| Unclassified differences vs. legacy | **0** |
| Mutation sensitivity (injected violations detected) | **35 / 35** |
| Agreement with the real framework | 26/26 tests, 20/20 mutants |
| Determinism | byte-identical over 539 files, 2,274 dependencies |

Equivalence was established by **executing the unmodified legacy checkers** as an oracle — not by
reimplementing their rules, which would have compared our reading of a rule against itself.

Every number is reproducible with a single command; see [Reproducing the evaluation](evaluation).

## What limen does not claim

The tool generates scoped context for coding agents, and that context is 81.4% smaller than the
project's full architectural prose. **We do not claim this improves the architectural quality of
generated code.** Two experiments attempted to measure that and neither could: the first had a
degenerate primary measure, the second found that the agent solved tasks inside a single module and
never approached the boundaries the rules govern. Both are reported as negative results with
diagnosed causes in [`research-report.md`](https://github.com/s-matysik/Limen/blob/main/research-report.md).

## License

MIT.
