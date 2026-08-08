# Study 1b — differential over a corpus of third-party repositories

Every number here is produced by `eval/study1b_corpus.py` from `eval/corpus.jsonl`; none is typed by hand.

## Corpus

- repositories in the corpus file: **720** (included: **60**)
- development set: **40**, held-out: **20** (seed 20260804)
- by native checker: {'dependency-cruiser': 15, 'deptrac': 15, 'import-control': 15, 'import-linter': 15}
- by language: {'js': 15, 'php': 15, 'java': 15, 'python': 15}

## What happened to each repository

| outcome | repositories |
|---|---|
| ok | 12 |
| oracle-not-live | 6 |
| translation-error | 1 |
| untranslatable-config | 37 |
| vacuous-no-violations-possible | 4 |

**12** repositories produced a comparable run (dev 7, held-out 5).

## Agreement per rule kind

Precision and recall are limen against the native tool as oracle: TP = same (file, line, target) on both sides, FP = limen-only, FN = native-only. Intervals are Wilson score, 95%.

`n/a` means the row was never exercised: the translated spec emits rules of that kind, but neither tool produced a finding for it, so there was nothing to agree or disagree about. It is **not** an agreement result. Reporting a recall of 0.000 there would assert that limen missed violations that were never presented to it — K11 (reachability) is the case in point: no native contract in this corpus produced a reachability finding.

The `oracle-side` row is not a rule kind. In a repository whose spec mixes kinds, a native finding names a NATIVE rule, not a limen rule, so the kind that should have caught it cannot be determined from the finding alone. Those findings are held here rather than assigned by guesswork or — as an earlier version of this aggregation did — added to every kind the spec emitted, which counted the same findings two or three times and computed one kind's recall from another kind's findings.

| K | split | repos | tools | TP | FP | FN | precision [95% CI] | recall [95% CI] | C1 | C2 | C3 | unclassified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K1 | dev | 1 | import-linter | 0 | 0 | 0 | n/a | n/a | 0 | 0 | 0 | 0 |
| K6 | dev | 7 | dependency-cruiser|import-control|import-linter | 5203 | 164 | 369 | 0.969 [0.964, 0.974] | 0.934 [0.927, 0.940] | 0 | 1 | 532 | 0 |
| K6 | held-out | 5 | dependency-cruiser|import-control|import-linter | 167 | 0 | 0 | 1.000 [0.978, 1.000] | 1.000 [0.978, 1.000] | 0 | 0 | 0 | 0 |
| K11 | dev | 1 | import-linter | 0 | 0 | 0 | n/a | n/a | 0 | 0 | 0 | 0 |
| oracle-side | dev | 0 | import-linter | 0 | 0 | 0 | n/a | n/a | 0 | 0 | 4 | 0 |

## Detection (seeded violations)

Most repositories in the corpus are green — their own CI enforces the native rule — so a 0-vs-0 comparison measures false positives and says nothing about detection. Imports the native tool's OWN configuration forbids were therefore injected into a real copy of each tree and both tools re-run. The translated spec is re-derived from the seeded tree, because the forbid lists are built by evaluating the native config over the imports actually present.

- repositories with a seeded arm: **5**
- injections: **49**
- detected by the native tool: **45**
- detected by limen: **49**
- detected by both: **45**

| repository | tool | injections | native | limen | both |
|---|---|---|---|---|---|
| apache/kafka | import-control | 20 | 20 | 20 | 20 |
| formkiq/formkiq-core | import-control | 20 | 20 | 20 | 20 |
| anieyrudh/Paideia | dependency-cruiser | 4 | 0 | 4 | 0 |
| juliusz-cwiakalski/marksync | dependency-cruiser | 4 | 4 | 4 | 4 |
| ZainZhao/eat-kafka | import-control | 1 | 1 | 1 | 1 |

dependency-cruiser reports no line number, so its detection is keyed on the file while limen's is keyed on (file, line). Per injection detail is in `study1b_raw/<repo>.json`.

## Acceptance gates

`eval/study1b_corpus.py` exits non-zero — exactly as `eval/study1_equivalence.py` does — when either gate fails:

1. **zero unclassified differences.** Every difference between the two tools must carry a C1/C2/C3 classification; unexplained differences are listed per repository with examples.
2. **every C3 cites a real deviation.** Each C3 names a `D-xx` identifier that must have a heading in `docs/deviations.md`. Without this check C3 would degrade into a catch-all for anything the classifier cannot place.

Both gates were verified to FIRE, not merely to pass: renaming `D-CS-1` in the registry makes the run exit 1.

## Files

- `study1b_per_repo.csv` — one row per repository
- `study1b_per_kind.csv` — the table above, machine-readable
- `study1b_raw/` — the full finding sets and classified differences
