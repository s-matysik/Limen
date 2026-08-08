# The legacy backend oracle shim

A thin PHPUnit/Laravel stand-in that lets the **unmodified**
`litrev-backend/tests/Architecture/*.php` classes run against the frozen
snapshot, which has no `vendor/` directory.

The point of the shim is that the rules are not reimplemented. If they were,
the equivalence study would be comparing limen against *our reading* of the
legacy checkers rather than against the checkers themselves.

## Layout

```
bootstrap.php               globals + PSR-4 autoloader; LIMEN_BACKEND_ROOT picks the tree
run_architecture_tests.php  reflection-driven runner; dumps every assertion event as JSON
resolve_lines.php           line recovery via the legacy rules' own PCRE patterns
stubs/
  Recorder.php                      collects assertion events; decodes the legacy JSON payload
  Tests/TestCase.php                stand-in for the app's Laravel-booting base class
  PHPUnit/Framework/TestCase.php    non-aborting assertSame/assertCount/assertTrue/assertEquals
  PHPUnit/Framework/Attributes/DataProvider.php
  Illuminate/Support/Str.php        only reachable from config/database.php:151
```

## What it provides, and why each piece is needed

| surface | used by | note |
| --- | --- | --- |
| `Tests\TestCase` | `ArchitectureRulesTestCase` extends it | The real one boots Laravel purely so `config()`/`base_path()` resolve; no architecture test touches the container, DB or HTTP kernel. |
| `assertSame`, `assertCount` | every rule | **Non-aborting** — records and continues, so a method reports all violations, not only the first. This is the single deliberate behavioural deviation; see `results/oracle_gaps.md` §2. |
| `#[DataProvider]` | `AdminBoundaryRulesTest` | 7 datasets. |
| `base_path()` | every rule | Rooted at `LIMEN_BACKEND_ROOT`. |
| `config()` | `architecture()`, `DeploymentBoundaryRulesTest` | Dot-notation reader over the host's real `config/*.php`. Nothing is inlined. |
| PSR-4 `App\` | `config/architecture.php` uses `App\Models\Folder::class` | PHP resolves `::class` on an undefined class without autoloading, so this is belt-and-braces; it also lets `ModelOwnershipRulesTest` see real class names. |
| `Illuminate\Support\Str` | `config/database.php:151` | Builds a redis key prefix; the value can reach no assertion. |

`SharedBoundaryRulesTest::setUp()` does
`require base_path('shared').'/config/architecture.php'`, which works because
the workspace keeps `litrev-backend/shared -> ../backend-shared` as a symlink,
exactly as the deployed layout does.

## Running it

Normally you do not call these directly — `eval/run_legacy_oracle.py` does:

```bash
python eval/run_legacy_oracle.py
```

Directly, for debugging:

```bash
LIMEN_BACKEND_ROOT=/path/to/litrev-backend php run_architecture_tests.php | jq '.executions'
```

## Fidelity

The shim is not asserted to be faithful; it is tested against the real thing.
See `eval/results/oracle_gaps.md` §5 and `eval/results/oracle_validation.json`.
