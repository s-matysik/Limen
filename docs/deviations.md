# Deviations: legacy LitRev checkers → limen specification

Every entry records a place where the legacy checker's semantics could **not**
be reproduced exactly by the limen rule engine, or where a faithful retrofit
required a deliberate editorial decision. Recording them is the point: each one
is either a gap in the engine (a concrete `architecture-spec/1` feature request)
or a defect the legacy checker had that the retrofit exposed.

Source of truth for the legacy side: `eval/legacy_rule_inventory.csv`.
Reproduce the retrofit state with the commands in `eval/README.md`.

Legend for **Kind**:

- **engine-gap** — limen cannot express the legacy semantics; the retrofit is an
  approximation and the difference is stated.
- **legacy-defect** — the legacy rule does not do what its name/message claims;
  the retrofit reproduces the *behaviour*, not the *intent*.
- **editorial** — semantics preserved, but the encoding required a choice.

---

## D-01 — "allow one namespace structurally, allowlist everything else" (engine-gap)

**Legacy:** `SharedBoundaryRulesTest::test_shared_does_not_import_forbidden_host_classes`
(`tests/Architecture/SharedBoundaryRulesTest.php:40-78`) collects every
`use App\…;` in backend-shared, unconditionally allows anything under
`App\Models\`, and requires every other `App\` class to appear in
`allowed_host_app_imports` (`backend-shared/config/architecture.php:11-20`).

**limen:** `K5 scoped-allowlist` has exactly the right shape (prefix + allow
list), but its `allow:` entries are validated by S4 *dead-entry* against
declared symbols, and a wildcard entry such as `App\Models\**` is reported as
dead (`no file declares 'App\Models\**'`) — verified experimentally: an S4 run
over a wildcard allow entry exits 3 under `--strict-spec`. There is no way to
say "this sub-prefix is structurally exempt, and it is not debt".

**Retrofit:** encoded as `K6 forbidden-target` `shared-not-into-host-modules`
with `forbid: ["App\Modules\**"]`. On the frozen workspace this is
*behaviourally equivalent*: the only non-`App\Models\` imports backend-shared
makes are the eight classes in the legacy allowlist, of which exactly one is an
`App\Modules\` class (the `ScheduleStatusLabel` exemption). The rules diverge
only for a hypothetical future import of a *new* `App\Support\` or
`App\Exceptions\` class — legacy would flag it, the retrofit would not.

**Proposal:** allow exemption entries to carry `structural: true` (or a separate
`allow_prefixes:` key on `scoped-allowlist`) that suppresses the S4 dead-entry
check for a deliberately-broad prefix. Alternatively, teach S4 that a target
containing a glob metacharacter is dead only if it matches no declared symbol.

## D-02 — `test_model_owner_modules_exist` is spec validation, not a rule (editorial)

**Legacy:** `ModelOwnershipRulesTest::test_model_owner_modules_exist`
(`tests/Architecture/ModelOwnershipRulesTest.php:33-50`) asserts that every value
in `model_owners` appears in the `modules` registry. It reads no source file: it
compares one config key against another.

**limen:** this is already a first-class integrity check — S3 *unknown-unit*
in `spec/validate.py` fires for any `ownership.owners.*` value absent from
`units.declared`. It runs on `limen spec validate` and, because the rule engine
calls the same validator, on `limen check` too.

**Retrofit:** deliberately **not** encoded as a rule. The legacy test is
subsumed by S3, which is strictly stronger (it covers `consumer-allowlist`,
`baseline-snapshot.exclude_units` and `reachability.units` as well). This is a
retrofit *win*, not a loss, and is the correct reading of the task brief:
config-consistency belongs to `spec validate`.

## D-03 — module-scoped database connections (engine-gap)

**Legacy:** `DeploymentBoundaryRulesTest::test_database_config_does_not_define_module_scoped_connections`
(`tests/Architecture/DeploymentBoundaryRulesTest.php:43-66`) reads Laravel's
runtime config (`config('database.connections')`) and asserts the key set is a
subset of five known drivers.

**limen:** rules consume `arch-facts/1`, which models *source files, symbols and
dependencies*. There is no fact type for "keys of a runtime configuration
array", and `K9 structure` asserts over paths, not file contents (its only
content-aware branch, `must_match`, is per-unit and regex-over-text).

**Retrofit:** **not encoded.** The other two `DeploymentBoundaryRulesTest`
assertions (single root compose file, no module-local `docker/`/`deploy/`) are
encoded as `modules-have-no-local-deployment`.

**Proposal:** either a `K9 structure` branch `file_must_not_match: {file, regex}`
for a named non-source file, or accepting an extractor for structured config
files that emits config keys as artifacts. The former is cheaper and would cover
this case (`config/database.php` must not mention a connection outside the
allowed set).

## D-04 — `index.{js,jsx,ts,tsx}` alternation in `for_each_unit` (engine-gap)

**Legacy:** `audit-frontend-architecture.mjs:8-13,305-325` treats a module as
having an entrypoint if *any* of `index.js`, `index.jsx`, `index.ts`,
`index.tsx` exists.

**limen:** `K9 structure`'s `for_each_unit` branch takes a single `file:` name
(`rules/structure.py`), with no alternation and no glob.

**Retrofit:** `frontend-module-entrypoint` asserts `index.js`. All 17 modules in
the frozen workspace use `index.js`, so the retrofit is behaviourally identical
here; a future `.ts` module would be a false positive.

**Proposal:** accept a list for `file:`, satisfied when any listed name exists.

## D-05 — allowed root-level sub-entrypoints of a frontend module (engine-gap, warning)

**Legacy:** `audit-frontend-architecture.mjs:15-40,310-314,351-359` warns
(`unexpected-module-subentrypoint`) when a module exposes a root-level source
file that is neither `index.*` nor one of a fixed allowlist (`errors`, `api`,
`routes`, `forms`, `selectors`, `tree`, in four extensions). This is a *closed
set on file names within a directory* — an anti-structure assertion.

**limen:** `K9 structure` can require files and forbid directories, but cannot
express "no file matching `{unit}/*.js` other than this allowlist". `K4
public-api-only`'s `public_surface` describes what may be *imported*, not what
may *exist*.

**Retrofit:** **not encoded.** Note the interaction: because
`public_surface.js` is `{unit}/*.{js,jsx,ts,tsx}`, limen treats every
root-level module file as public, exactly like the legacy `isPublicModuleEntry`
(`:150-163`) — so the *import* side is faithful; only the *existence* warning is
missing. On the frozen workspace all root-level files are within the legacy
allowlist, so the warning set is empty either way.

**Proposal:** a `forbidden_files:` / `allowed_files:` pair on `K9 structure`'s
`for_each_unit`, taking globs relative to the unit directory.

## D-06 — dead allowlist entries in the legacy configuration (legacy-defect)

Four FQCNs are allowlisted by the legacy configuration but declared by no file
in the frozen workspace, and three page-file paths are allowlisted by the
frontend audit script but exist in no module:

| Legacy entry | Source | Status |
| --- | --- | --- |
| `App\Modules\Audit\Application\Contracts\AuditApi` | `config/architecture.php:129,145,161,169` | dead — contract lives at `LitRev\BackendShared\Audit\Application\Contracts\AuditApi` |
| `App\Modules\Notifications\Application\Contracts\MailNotificationModuleApi` | `config/architecture.php:137,153,177` | dead — lives in backend-shared |
| `App\Modules\RelationshipRequests\Application\Contracts\RelationshipRequestsModuleApi` | `config/architecture.php:185` | dead — lives in backend-shared |
| `App\Modules\Auth\Domain\UserAuthNotificationDispatcher` | `config/architecture.php:205` | dead — no file declares it |
| `src/modules/admin-users/pages/UsersManagement.jsx` | `audit-frontend-architecture.mjs:70-72` | dead — file absent |
| `src/modules/profile/pages/AccountDetails.jsx` | `audit-frontend-architecture.mjs:78-81` | dead — file absent |
| `src/modules/auth/pages/verify-email.jsx` | `audit-frontend-architecture.mjs:78-81` | dead — file absent |

**limen:** S4 *dead-entry* is exactly the check that finds these; under
`--strict-spec` it is an error. Transcribing them verbatim would make
`limen spec validate --strict-spec` exit 3 — which is the engine working as
designed.

**Retrofit:** the dead entries are **omitted**, and every omission is recorded
in `eval/legacy_rule_inventory.csv` (`retrofit_note`) and in the rule's inline
comment. Note the direction: removing an *allow* entry can only make a rule
stricter, never looser, so no violation is hidden by this decision. This is the
clearest single argument for the engine's S4 check and belongs in the paper as
such: an allowlist rots in one direction and nothing in the legacy setup ever
noticed.

## D-07 — an allowlist entry that can never match (legacy-defect)

**Legacy:** `config/architecture.php:117-125` scopes
`app/Modules/Notifications/Application` against `import_prefix`
`App\Modules\Messages\Application` — so `ModuleContractRulesTest`
(`:10-17`) builds the regex `/use\s+(App\\Modules\\Messages\\Application\\[^;]+);/m`
— but its single `allowed_imports` entry is
`LitRev\BackendShared\Messages\Application\Events\MessageNotificationRequested`,
which the regex can never capture. The rule is therefore an unconditional ban
on `App\Modules\Messages\Application\*`, and the allowlist is decorative.

**Retrofit:** reproduced as an unconditional ban
(`notifications-application-messages-allowlist`, `allow: []`), because the
retrofit must be faithful to *behaviour*. The intent — "Notifications may
consume the shared Messages event" — is served by the shared event living
outside the banned prefix anyway. On the frozen workspace
`app/Modules/Notifications/Application` contains only `.gitkeep`, so the rule
has zero files in scope; it is kept so the constraint survives the directory
being populated.

## D-08 — legacy Http/Application boundary was blind to backend-shared (legacy-defect, retrofit is stricter)

**Legacy:** `ApplicationBoundaryRulesTest` (`:32`) and `HttpBoundaryRulesTest`
(`:19`) match only imports in the `App\Modules\…` namespace. A host controller
importing a *concrete* `LitRev\BackendShared\…\Application\` service (i.e. not a
`Contracts\` interface) was structurally invisible to them.

**limen:** `K4 public-api-only` resolves the dependency to the declaring file and
tests it against `public_surface`, regardless of namespace, so the same import
is now visible.

**Retrofit:** kept strict on purpose — `host-cross-module-public-surface`
reports 3 errors on the frozen workspace:

- `app/Modules/AdminUsers/Http/Controllers/UserController.php:24` → `LitRev\BackendShared\Audit\Application\AuditLogService`
- `app/Modules/Projects/Http/Controllers/ProjectController.php:26` → `LitRev\BackendShared\Folders\Application\FolderViewService`
- `litrev-core-backend/src/Reviews/Http/Controllers/ReviewController.php:14` → `LitRev\BackendShared\Folders\Application\FolderHomeService`

These are genuine violations of the boundary the legacy configuration *states*
(backend-shared's own comment block, `backend-shared/config/architecture.php:22-32`,
says concrete Application classes need an explicit allowlist entry) that the
legacy checker never enforced across the host↔shared edge. Per the task brief
they are left as findings — Badanie 1 material — and no exemption was written
for them.

## D-09 — warning/error split is preserved, with one asymmetry (editorial)

The legacy frontend script separates `findings` (exit 1) from `warnings`
(exit 0). The retrofit preserves the split via `severity:`:

- errors: `runtime-console-log`, `raw-success-response-data`,
  `local-message-fallback`, `shared-must-not-depend-on-app-or-modules`
  (three `layer-dependency` rules), `cross-module-*-import`,
  `missing-module-entrypoint`;
- warnings: `oversized-page-file` (600), `oversized-module-entrypoint` (80),
  `oversized-hook-file` (350), `missing-module-readme` /
  `missing-module-contract` (merged into `frontend-module-readme-contract`).

**Asymmetry:** legacy emits `missing-module-readme` and
`missing-module-contract` as two distinct warnings; limen's `K9
for_each_unit` emits the "missing file" case and the "missing section" case from
one rule, so they share a rule id and severity. Counts differ, semantics do not.

**Not encoded:** `app-deep-module-import` (`audit-frontend-architecture.mjs:249-259`)
— a warning fired when `src/app/**` imports `@/modules/<m>/{lib,hooks,components,pages}/…`.
`K4` cannot express it because the rule's severity must differ *by importing
layer* (error from a module, warning from `src/app`), and a second K4 rule
scoped to `src/app` would still be evaluated against the same
`public_surface`. On the frozen workspace all `src/app` imports of a module
target the module root, so the warning set is empty. **Proposal:** allow a
`severity_by_scope:` override, or a `scope.from_layer` filter on
`public-api-only`.

## D-10 — `collectSharedDomainHelperWarnings` (engine-gap)

**Legacy:** `audit-frontend-architecture.mjs:368-396` walks the reverse import
graph: for every `src/shared/lib/**` or `src/shared/utils.*` file not on the
infra allowlist (`http/`, `i18n/`, `ui/`, `a11y.`), it counts how many *distinct
modules* import it, and warns when that number is below 2 (while being imported
from outside `src/shared/`). It is a *fan-in cardinality* rule: "a shared helper
must be shared by at least two modules".

**limen:** `K10 metric-threshold` supports exactly one metric, `lines`
(`rules/metric.py` returns immediately for anything else). `K11 reachability`
answers "can A reach B", not "how many distinct units reach B". No rule kind
computes a per-target fan-in.

**Retrofit:** **not encoded.** Computed independently for this snapshot: 0
warnings (no `src/shared/lib/**` file is imported by fewer than two modules
from outside shared).

**Proposal:** extend `K10 metric-threshold` with a graph metric family —
`metric: importing_units`, `min:`/`max:`, plus a `target_scope:` selecting the
files to measure. That single addition also covers the common "god module"
(fan-in too high) and "orphan" (fan-in zero) checks.

---

# Study 1 outcome — what the differential diff added

`eval/study1_equivalence.py` diffs the legacy oracle
(`eval/results/legacy_findings.json`) against limen
(`eval/results/limen_findings.json`) on the key **(file, line, target)** and
requires every difference to fall into exactly one of three categories. On the
frozen workspace (`workspace-id
562c232df26dab5309088db63e3ac82723d9643479a679b8dd2ad9415a0bbe31`) the gate
passes with **zero unclassified differences**:

| Category | Count |
| --- | ---: |
| C1 — legacy false alarm removed | 0 |
| C2 — legacy gap closed | 3 |
| C3 — deliberate semantic change | **0** |
| unclassified | **0** |

**No new deviation was created by the diff.** The reason each category came out
as it did is itself a finding, and is recorded here rather than left implicit:

**C3 = 0 — no difference traced to a deliberate semantic change.** D-01 through
D-10 above record ten places where the retrofit had to deviate. None of them
*produces a difference on this snapshot*, and the two facts are consistent
rather than contradictory: D-01, D-04 and D-07 are documented as behaviourally
equivalent on the frozen tree (they diverge only for hypothetical future code —
a new `App\Support\` import, a `.ts` module entrypoint, a populated
`Notifications/Application` directory), and D-03, D-05, D-10 and the
`app-deep-module-import` half of D-09 are *not encoded at all*, so they can only
suppress a legacy finding that the legacy run does not produce either (the legacy
suite is green on this snapshot: 26 methods, 52 assertions, 0 failures; both
frontend audits exit 0). A deviation is a statement about the **rule set**; a
difference is a statement about the **findings**. On a green snapshot the two do
not have to coincide, and here they do not.

The gate encodes this distinction rather than asserting it: `classify_c3()`
accepts a deviation as evidence only when its declared kind is `engine-gap` or
`editorial`. A `legacy-defect` deviation documents a defect the retrofit
*exposes* — that is evidence for C2, never for C3 — so D-08 does not classify
the three findings below even though it describes them.

**C1 = 0 — no legacy false alarm to remove.** The C1 rule fires when a
legacy-only finding's matched text lies in a comment or a string literal (the
legacy checkers are `preg_match_all` over raw `file_get_contents` output and
never strip either; limen's extractors emit dependencies from code only). It
did not fire because there are no legacy-only findings at all — and,
independently, because the frozen tree contains **no** `use App\…;` or
`use LitRev\…;` occurrence inside a PHP comment or string in any scanned root.
The false-alarm class the retrofit removes is therefore real but unexercised by
this snapshot; the rule and its evidence machinery are covered by three
synthetic-fixture tests in `eval/tests/test_study1_equivalence.py`.

**C2 = 3 — see D-08.** All three differences are the `host-cross-module-public-surface`
errors that D-08 predicted, and the classifier verifies D-08's claim at runtime
instead of trusting it: it requires (a) the constraint to be stated in prose —
matched in `backend-shared/CLAUDE.md:52`, `backend-shared/src/AGENTS.md:15` and
`litrev-backend/app/Modules/AGENTS.md:29` — and (b) the legacy checkers to be
structurally incapable of seeing the import, confirmed by reading the actual
regexes at `ApplicationBoundaryRulesTest.php:32` and `HttpBoundaryRulesTest.php:19`,
both anchored to `App\\Modules\\`. If either probe stopped matching, the
difference would degrade to unclassified and the gate would fail. Full evidence
per finding, including the quoted source line, is in
`eval/results/study1_diff.json`.

## Scope note — the 8 `unresolved-dependency` warnings

limen reports 8 warnings that legacy has no counterpart for. They are
**excluded from the diff by an explicit rule** (`in_comparison_scope()` in
`eval/study1_equivalence.py`), not by a silent filter, and they are listed in
full under `out_of_scope` in the diff JSON.

The rule: a finding takes part in the diff only if it is a *contract-rule
verdict* — on the limen side, `spec_path` matching `rules[n]`. The
`unresolved-dependency` findings carry `spec_path: "unresolved_policy"` and
`kind: "unresolved"`; they state that the **extractor** could not statically
resolve a dependency edge, which is a claim about extractor coverage, not about
the architecture.

Why this is not category 2 ("legacy gap closed"): a gap requires a rule that
existed in prose but went unenforced. There is none here. The legacy checkers
are regex matchers — a dependency they fail to recognise produces no finding at
all, so they cannot express this judgement even in principle — and no prose rule
in any of the six repositories requires imports to be statically resolvable
(probed across every `AGENTS.md`, `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`
and `docs/architecture/**`: no occurrence). Classifying them as C2 would assert
a legacy rule that never existed in any form. All 8 are non-source assets
(`@/assets/*.png|svg`, `./index.css`, four `../../locales/*.json`), consistent
with the same reading.

---

# Study 1b — retrofit deviations (third-party corpora)

Entries below are referenced by `eval/study1b_corpus.py` and the bespoke
retrofit scripts. Unlike D-01..D-10, which record differences between limen and
the LitRev legacy suite, these record differences between limen and a
**third-party native checker** whose configuration was translated into an
`architecture.yaml`. Every C3 classification in `study1b_per_repo.csv` cites one
of these identifiers, and each citation is verified per finding — the
classifier re-reads the source line and refuses the classification when the
evidence is not present on that line.

## D-CS-1 — static imports: member granularity vs type granularity (engine-gap)

**Native tool:** checkstyle `ImportControl` (`import-control.xml`).

A Java static import names a MEMBER:

```java
import static org.apache.kafka.common.config.ConfigDef.Type.BOOLEAN;
```

checkstyle reports the violated target as the member (`ConfigDef.Type.BOOLEAN`);
limen's Java extractor records the enclosing TYPE (`ConfigDef.Type`). Both
tools flag the same statement on the same line of the same file, and both agree
it is a violation — only the reported target string differs.

The classifier accepts this only when it has re-read the source line and
confirmed the line is a `import static` statement whose member path extends
limen's target. 532 differences in the corpus carry this identifier.

## D-IL-1 — transitive chains reported at every hop (engine-gap)

**Native tool:** import-linter (`.importlinter`).

import-linter's `layers` and `independence` contracts are TRANSITIVE by
default: when `a` imports `b` and `b` imports `c`, a contract forbidding
`a -> c` is reported as a chain, and the intermediate hops (`b -> c`) appear as
findings in their own right. limen's `forbidden-target` rule is a predicate on
a single import statement and reports only the hops that are themselves
forbidden.

Chain intermediates therefore appear as native-only differences. They are
classified C3 only when the finding's `chain_head` differs from its own source
module — i.e. the finding is demonstrably an intermediate hop of a longer
chain, not a direct violation limen missed.
