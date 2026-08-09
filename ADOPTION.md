# limen in practice: what four studies established

A companion to `research-report.md`. That document holds the numbers and the commands that
reproduce them; this one covers what follows from them for someone considering limen in their own
project. Every quantity cited here comes from `eval/results/`.

## 1. What limen is demonstrably good for

**Replacing scattered architecture checkers with a single specification.**
The retrofit turned 47 rules spread across 1246 lines of code in three languages (PHP config,
PHPUnit classes, a Node script) into 36 declarations in 526 lines of YAML. Semantics were
preserved in a checkable way: 0 unclassified differences, and the oracle ran **unmodified** legacy
code validated against a real Laravel installation (26/26 tests, 20/20 mutants with identical
failure sets).

**A CI gate that requires reproducibility.**
Byte-level determinism was confirmed on 539 files and 2274 dependencies, across three execution
variants (cold cache, warm cache, randomised PYTHONHASHSEED). For a gate that blocks merges this
is the critical property, and the one property that must not be taken on trust.

**Generating compact context for a coding agent.**
`limen context` produces 9.0 kB where the full architectural prose takes 48.5 kB — a reduction of
81.4% (5.37×). This number is a property of the generator, not of the sample, so it does not depend
on the size of the experiment. **It is the only measured effect of Study 3.**

**Finding gaps in the checkers you already have.**
The three `host-cross-module-public-surface` violations that limen found were invisible to legacy,
because its regexes were anchored on `App\Modules\`, so imports from `LitRev\BackendShared\` slipped
past. The retrofit has diagnostic value regardless of whether you stay with the new tool.

## 2. What the studies did NOT establish

**That the contract improves the quality of an agent's work.** Study 3 did not measure this — the
primary measure (count of new violations) turned out to be degenerate: zero in 23 of 25 runs, zero
between-task variance across all six contrasts. This is not a "no effect" result but a "the
measurement could not have detected an effect" result. Anyone wanting to defend that claim has to
design the tasks differently (see §4).

**That the tool will handle any existing config.** Of the 60 repositories in the corpus, the
comparison completed in **12**. The rest dropped out mostly on untranslatable config. If you have a
working deptrac or import-linter, assume the migration will be manual.

**That layer and ownership rules work in production practice.** Across a history of 69 commits,
**five of the eleven rule kinds never fired once** (K1 layer-dependency, K2 ownership,
K3 consumer-allowlist, K6 forbidden-target outside the exemption-free variant, K11 reachability).
That they work is known from mutation testing; that they catch real debt in a typical project is
not.

## 3. When it is worth adopting, and when it is not

**Worth adopting when:**
- you have **more than one** architecture checker and they are starting to diverge;
- you work in a multi-repo setup and need rules that span **repository boundaries** — per-repo
  legacy checkers cannot see them by construction (in Study 2 only the reconstructed-workspace axis
  exposed K4);
- you feed a coding agent architectural prose and pay for it in context;
- rules have to outlive the person who wrote them — YAML with `reason`/`owner`/`expires` on every
  exemption is readable for a successor; a regex inside a test class is not.

**Not worth adopting when:**
- you have a single working checker and nobody is complaining about it;
- the project is single-module — the rules enforce boundaries **between** modules, so there is
  nothing for them to guard;
- you are counting on automated migration from an existing tool.

## 4. How to adopt: an order the studies support

1. **Start with an inventory, not with writing YAML.** Write down every rule you actually enforce:
   file, lines, semantics, exemptions. Without this you cannot tell a retrofit apart from rewriting
   the architecture along the way.
2. **Do not relax a rule to make the code pass.** You map the rule faithfully; a violation stays a
   violation. That is the difference between a migration and hiding debt.
3. **Measure equivalence before you switch off the old checker.** Run both on the same tree and
   classify every difference. Acceptance criterion: zero uncategorised differences, enforced by a
   non-zero exit code — a gate, not a report.
4. **Add a sensitivity test.** Two tools that are both silent on a clean tree only appear to
   "agree". Inject one synthetic violation per rule and check that **both** catch it. In our case
   35 mutations, 35/35 detected — only that counts as evidence.
5. **Give exemptions an expiry date from the start.** The measured median age of debt legalised by
   exemptions was 76.3 days against a declared lifetime of 149 days, and all 4 exemptions were still
   open at the end of the window. Without `expires`, an exemption is permanent.
6. **Adopt `limen context` independently of the CI gate.** The context reduction is measured and
   does not require the specification to be complete.

## 5. Pitfalls that cost us time

**The `--spec` path must be absolute.** Relative paths are resolved against `--workspace`, which
produces a misleading "specification file not found".

**`units.registry_policy: exact` aborts historical trees with exit code 3** before it runs any rule
at all. Historical analysis without relaxing this to `open` will return zero findings for every
commit and will look like a clean project.

**The fact cache is keyed on file contents, not on the extractor version.** After changing extractor
code you have to clear it by hand, otherwise you get stale results.

**When validating with Composer, do not symlink `vendor/`.** The autoloader derives baseDir from the
*resolved* path, so classes load from the original tree and every mutant "passes" — a false agreement
that looks like success. Copy for real (`cp -Rc`).

**Check whether the comparison oracle is alive at all.** dependency-cruiser without `node_modules`
fails to resolve most dependencies and reports zero, having decided nothing. Without a resolvability
threshold such a repository is counted as full agreement.

## 6. If you want to replicate Study 3

The pilot exposed a design defect for 21% of the budget, and that is its main value. Conditions for
a meaningful replication:

- **Tasks must touch more than one module.** The rules enforce inter-module boundaries; a change
  inside a single module has no way to violate them, regardless of sample size.
- **The primary measure should be functional regression**, not the violation count — that is the one
  with variance (0.32 [0.17, 0.52]).
- **Sample size:** 49 runs per arm for an effect of 0.40 vs 0.15; 82 for 0.40 vs 0.20. That is K=20
  at R=3–5.
- **Budget:** 60,299 input tokens per cell. 120 cells is 7.2M — this does not fit in a single
  session, so plan a split with incremental per-run persistence.
- **Circularity control requires a second rater.** In our case the agreement between two raters was
  not computed, and this is recorded as an unmet requirement rather than passed over in silence.

## 7. In one sentence

limen is proven as a **deterministic CI gate and a consolidator of scattered architecture rules**,
with a real and measured benefit in the form of 5.4× smaller context for an agent; the claim that it
improves the quality of an agent's work remains unresolved, and migration from existing tools is in
practice manual work.
