# Legacy oracle: what it covers, what it cannot, and how it is proved

This document accompanies `eval/results/legacy_findings.json`. It states
exactly what the "legacy" side of the equivalence study is, which parts of the
legacy checkers the oracle does **not** reproduce, and what evidence supports
the claim that the parts it does reproduce are reproduced faithfully.

Every number below is produced by a script and read out of a JSON file. The
reproducing commands are listed at the bottom.

## 1. What the oracle runs

| side | what is executed | how |
| --- | --- | --- |
| backend | the ten unmodified files in `litrev-backend/tests/Architecture/` | `eval/legacy/php/run_architecture_tests.php` under the shim in `eval/legacy/php/` |
| frontend | the unmodified `litrev-frontend/scripts/audit-frontend-architecture.mjs` | `node`, once per frontend repository, with `cwd` set to that repository |

**No rule logic is reimplemented.** The PHP rule bodies are the ones in the
repository; the shim supplies only `Tests\TestCase`,
`PHPUnit\Framework\TestCase`, `PHPUnit\Framework\Attributes\DataProvider`,
`Illuminate\Support\Str`, the global helpers `base_path()` / `config()` /
`env()` / `app_path()` / `database_path()` / `storage_path()`, and a PSR-4
autoloader. The frontend auditor is executed as-is; only its printed output is
parsed.

## 2. The one deliberate deviation from PHPUnit

Real PHPUnit aborts a test method at its first failing assertion. The shim's
`assertSame()` / `assertCount()` **record** the failure and return, so the
method continues.

This is required by the task: several legacy methods call `assertSame()` inside
a `foreach` over config-driven rules (`ModuleContractRulesTest` — 9 iterations,
`HttpBoundaryRulesTest` — 14, `ModelOwnershipRulesTest::test_forbidden_model_import_rules_are_enforced` — 6,
both `LayerBoundaryRulesTest` allowlist drivers — 1 each). Under real PHPUnit,
a violation in the first iteration hides every violation in the rest. The
oracle needs the complete list, not the first element of it.

Consequence for the shim-vs-Laravel comparison (§5): assertion counts coincide
exactly when every test passes; on a mutated tree the shim's count is an upper
bound on the real one. The comparison asserts equality of the *pass/fail
verdict per test* in both cases, which is the property that matters.

## 3. Line recovery

The diff key agreed with the limen side is `(file, line, target)`. The legacy
PHP checkers report `(file, import)` and never a line number — the frontend
auditor does report lines.

Lines are recovered in `eval/legacy/php/resolve_lines.php` by re-running **the
same PCRE pattern the reporting rule used**, over the same file, with
`PREG_OFFSET_CAPTURE`, and taking the offset of the match whose captured value
equals the value the rule reported. Offset is converted to a line with
`substr_count(prefix, "\n") + 1` — the same conversion the legacy frontend
auditor uses in `getLineNumber()`. The patterns are copied verbatim from the
legacy sources (`run_architecture_tests.php`, `$patterns`), and for the
config-driven rules they are taken from the host's own
`config/architecture.php` at runtime, not retyped.

Recovery runs in **PHP**, deliberately: a Python `re` translation of a PHP
PCRE pattern would be a re-interpretation of the rule, which is precisely what
this study must avoid.

For rules whose mechanism is `str_contains` rather than `preg` (the admin
boundary rule and the forbidden-model rules), recovery is a literal substring
search for the same needle the rule used.

Rules that report a *structural* fact rather than a matched import — a module
with a local `docker/` directory, a shared file inside `Http/`, a missing
docker-compose, a model missing its owner entry — get `line: 1` and
`target: null`. There is no line to recover; the violation is about the file's
existence or location.

**This is a coordinate added to an existing violation. It never adds, removes,
merges or reclassifies one.** The evidence is `legacy_total_delta` in
`results/mutation_sensitivity.json`: the finding count changes only by what was
injected.

Recovery correctness is checked independently by
`eval/check_line_recovery.py` → `results/line_recovery_check.json`: 15
mutations both tools detect are planted into one tree, and the `(file, line)`
coordinates the two tools report are compared. In files where both fire,
**13/13 coordinates agree, with none reported by only one tool**. Files
carrying pre-existing limen findings are excluded, because inserting a `use`
line shifts those findings down and they have no legacy counterpart on this
snapshot.

## 4. Path space

`file` is normalised to the path space `limen check` uses in its JSON report,
so `(file, line, target)` is directly comparable. Concretely,
`litrev-arch-snapshot/<repo>/<path>`, with the two backend symlinks resolved to
the repositories they point at:

| legacy reports | oracle emits |
| --- | --- |
| `<workspace>/litrev-backend/app/Modules/X/…` (absolute) | `litrev-arch-snapshot/litrev-backend/app/Modules/X/…` |
| `app/Modules/X/…` (relative, `relativePath()`) | same as above |
| `Auth/Application/Foo.php` (relative to `shared/src`, `SharedBoundaryRulesTest`) | `litrev-arch-snapshot/backend-shared/src/Auth/Application/Foo.php` |
| `src/modules/x/y.js` (relative to the frontend repo) | `litrev-arch-snapshot/litrev-frontend/src/modules/x/y.js` |

The mapping was verified against real limen output rather than assumed: with
one planted violation, both tools report
`litrev-arch-snapshot/litrev-backend/app/Modules/Folders/Http/Controllers/FolderController.php:5`
for the same import (mutation `M-BE-02-admin-import`).

## 5. Evidence that the shim is faithful

Two independent lines of evidence, both scripted.

### 5.1 Against real Laravel + PHPUnit

`repo.packagist.org` was granted, so `composer install` was run against the
frozen `composer.lock` in a copy of the backend outside the workspace, and
`eval/validate_shim_against_laravel.py` ran both runners on the same trees and
compared them per test method. Results in
`results/oracle_validation.json` → `laravel_validation`.

On the clean tree: real PHPUnit reports **26 tests, 52 assertions, 0 failures**;
the shim reports **26 executions, 52 assertions, 0 failures**; the sets of
(class, method, dataset) keys are equal and no verdict disagrees.

On each of the **20** injectable backend mutants, both runners are executed on
the same mutated tree and their **failure sets** are compared: **20/20 agree**,
with identical failure sets and no test present in one runner but not the
other. The shim is therefore faithful not only on an all-green tree — where
agreement would be cheap — but on every tree where a rule actually fires.

One trap worth recording, because it silently produces a false "everything
agrees": Composer's generated autoloader derives its base directory from the
*resolved* `vendor/` path. Symlinking `vendor/` into the mutant tree to save
disk therefore makes `Tests\TestCase` load from the pristine tree, whose
`__DIR__` sends `base_path()` back to the unmutated copy — every mutant then
passes under real PHPUnit while the shim correctly fails it. The validator
copies `vendor/` for real (`cp -Rc`, APFS clones).

### 5.2 Mutation validation (independent of Laravel)

`eval/mutation_sensitivity.py` copies the workspace, plants exactly one
synthetic violation, and re-runs both oracles. A shim that swallowed
violations would report zero on the mutants too; a shim that invented them
would overshoot. Results in `results/mutation_sensitivity.json` and, for the
backend, in `results/oracle_validation.json` → `shim_mutation_validation`.

**35 mutations** (20 backend, 15 frontend) cover **34 of the 47** inventory
rules. Every one is detected by the legacy oracle (35/35) and by limen
(35/35 — counting the six documented not-encoded rules of §6.3, where the
expected and observed limen behaviour is "does not fire"). 30 of the 35 change
the legacy finding count by exactly the number of injected violations; the
five that do not are listed below.

Where the finding delta exceeds the number of injected lines, it is because one
line genuinely breaks more than one legacy rule — the legacy suite has
overlapping rules. Each such case is recorded per mutation with the rules that
fired:

| mutation | Δ | why |
| --- | --- | --- |
| `M-BE-02-admin-import` | 2 | an admin import is also a cross-module `Http` import (L-BE-TEST-02 + L-BE-TEST-07) |
| `M-BE-09-model-into-application` | 2 | the dedicated Models→Application rule and the `model_module_import_rules` allowlist driver both fire (L-BE-TEST-09 + L-BE-TEST-11) |
| `M-BE-18-module-application-allowlist` | 2 | `ApplicationBoundaryRulesTest` and `ModuleContractRulesTest` both cover cross-module Application imports (L-BE-TEST-03 + L-BE-TEST-18) |
| `M-FE-05-cross-module-deep-import` | 2 | a deep `@/modules/x/y/z` import is both `cross-module-deep-import` and `cross-module-internal-import` (L-FE-05 + L-FE-06) |
| `M-FE-11-missing-entrypoint` | 2 | renaming `index.js` to `entry.js` removes the entrypoint *and* creates an unexpected root-level file (L-FE-11 + L-FE-14) |

This overlap is a property of the legacy suite, not of the shim, and it is
exactly the kind of redundancy the article's rule-count comparison is about.

## 6. Gaps — what the oracle does not cover

### 6.1 The clean snapshot produces an empty oracle

Both legacy checkers pass on the frozen snapshot: **0 findings**. This is the
correct result and it is verified, not assumed (§7). It is also why
`mutation_sensitivity.py` exists: two tools that both report nothing "agree"
vacuously, and that agreement carries no evidence. The mutation table is the
non-vacuous half of the comparison.

### 6.2 Legacy rules with no mechanically injectable counter-example

`eval/legacy_rule_inventory.csv` holds 47 entries. **34** have a mutation. The
remaining **13** have none, for these reasons:

| inventory id | reason |
| --- | --- |
| `L-BE-CFG-01` … `L-BE-CFG-09`, `L-SH-CFG-01`, `L-SH-CFG-02` | These are *config declarations* (`model_owners`, `allowed_model_consumers`, `module_application_rules`, …), not checkers. They have no independent verdict; they are the data the test classes consume, and they are exercised through the mutations of the rules that read them. `L-BE-CFG-04/05/06/07/08/09` are each covered indirectly by `M-BE-12/14/15/18/10/09`. |
| `L-BE-TEST-01` | `ArchitectureRulesTestCase` — the shared harness (file discovery, `relativePath`, `classNameFromFile`). It asserts nothing itself; it is exercised by every other mutation. |
| `L-BE-TEST-11` | The `model_module_import_rules` allowlist driver. It cannot be triggered *alone*: any import that breaks it also breaks `L-BE-TEST-09`. It is observed firing, alongside L-BE-TEST-09, in `M-BE-09-model-into-application`. |

### 6.3 Rules where the two tools legitimately differ

These are recorded as expected non-detections in the mutation table
(`expect_limen_rule: null`), not as failures. Each was confirmed by running the
mutation:

| mutation | legacy fires | limen fires | why |
| --- | --- | --- | --- |
| `M-BE-06-module-db-connection` | yes | no | `L-BE-TEST-06` (no module-scoped DB connections) reads `config('database.connections')`. limen's spec has no rule over Laravel config arrays — inventory status `not-encoded`, deviation documented in `docs/deviations.md`. |
| `M-BE-13-unknown-owner-module` | yes | no | `L-BE-TEST-13` is referential integrity *of the config itself* (every `model_owners` value must be a known module). In limen the equivalent check happens at `spec validate` time, not in `check`. |
| `M-BE-03b-foreign-application-dangling` | yes | no | Legacy matches the `use` statement as **text**; limen resolves dependencies to units first, and an import that resolves to no file is dropped. A dangling import is dead code, not an architecture violation — a difference in what each tool treats as a *fact*, not in the rule. `M-BE-03` (same import, target class created) is detected by both. |
| `M-FE-07-app-deep-module-import` | yes (warning) | no | `L-FE-07` — `not-encoded`. |
| `M-FE-14-unexpected-subentrypoint` | yes (warning) | no | `L-FE-14` — `not-encoded`. |
| `M-FE-15-shared-domain-helper` | yes (warning) | no | `L-FE-15` — `not-encoded`. |

### 6.4 Test methods that could not be executed

None. All 20 `test_*` methods across the 9 concrete classes execute, 26
executions counting `AdminBoundaryRulesTest`'s 7 data-provider datasets, with
0 errors. `run_legacy_oracle.py` records this in
`oracle.php.executions` and would surface any error in `oracle.php.errors`.

The only method that initially failed under the shim was
`DeploymentBoundaryRulesTest::test_database_config_does_not_define_module_scoped_connections`,
because `config/database.php:151` references `Illuminate\Support\Str::slug()`
while building the redis key prefix. A four-line `Str` stub resolves it; the
value it produces is inside the `redis` config section, which no architecture
rule reads. That stub is the only place where the shim supplies behaviour
rather than plumbing, and its output cannot reach any assertion.

### 6.5 `frontend-shared` runs a script it does not own

`frontend-shared/package.json` declares
`"audit:architecture": "node /app/scripts/audit-frontend-architecture.mjs"` —
an absolute container path that does not exist outside Docker. The oracle
therefore runs `litrev-frontend`'s copy of the script with `cwd` set to
`frontend-shared`, which is what the container mount makes happen in
production. The auditor takes all of its inputs from `process.cwd()`, so this
is behaviourally identical; it is recorded in
`legacy_findings.json` → `oracle.frontend[].script` and `.cwd`.

`frontend-shared` is also not a Tier-1 limen root (see `eval/WORKSPACE.md`), so
its findings have no limen counterpart to diff against. On this snapshot the
question is moot — it is clean — but it would matter on a dirty one.

## 7. Reproducing every number here

```bash
cd /Users/sebastianmatysik/\!\!\!\!\!Limen/limen

# the oracle itself → eval/results/legacy_findings.json
python eval/run_legacy_oracle.py

# sensitivity of both tools + shim mutation validation
#   → eval/results/mutation_sensitivity.json
#   → eval/results/oracle_validation.json (shim_mutation_validation)
python eval/mutation_sensitivity.py

# line-recovery correctness against limen's own coordinates
#   → eval/results/line_recovery_check.json
python eval/check_line_recovery.py

# shim vs. real Laravel + PHPUnit (needs a tree with vendor/ installed)
#   → eval/results/oracle_validation.json (laravel_validation)
#
# to build that tree (requires repo.packagist.org):
#   mkdir -p /tmp/laravel_check && cd /tmp/laravel_check
#   cp -R <workspace>/backend-shared . && cp -R <workspace>/litrev-core-backend .
#   rsync -a --exclude shared --exclude core-backend <workspace>/litrev-backend/ ./litrev-backend/
#   cd litrev-backend && ln -s ../backend-shared shared && ln -s ../litrev-core-backend core-backend
#   php composer.phar install --no-interaction --no-scripts --prefer-dist
python eval/validate_shim_against_laravel.py --laravel-root /tmp/laravel_check/litrev-backend
```

## 8. Files

| file | what it is |
| --- | --- |
| `results/legacy_findings.json` | **the oracle**: legacy violations on the frozen snapshot, `legacy-findings/1` schema. Empty by design. |
| `results/legacy_findings.example.json` | the same schema with 15 records, produced by planting 12 mutations into a temporary copy. Not a study result — it exists so the schema can be read and diffed against. |
| `results/mutation_sensitivity.json` | per-rule sensitivity of both tools. |
| `results/oracle_validation.json` | shim fidelity: `laravel_validation` (vs. real PHPUnit) and `shim_mutation_validation` (vs. planted violations). |
| `results/line_recovery_check.json` | recovered lines vs. limen's lines. |
| `eval/legacy/php/` | the shim (see its `README.md`). |
