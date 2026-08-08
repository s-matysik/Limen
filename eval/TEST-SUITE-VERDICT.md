# Verdict on the three failing CLI tests

## Symptom

Before this change, `python -m pytest -q` in `limen/` reported
**121 passed, 3 failed, 1 skipped**. The three failures were:

- `tests/test_cli_integration.py::test_review_for_diff`
- `tests/test_cli_integration.py::test_check_changed_only`
- `tests/test_cli_more.py::test_context_for_diff`

all with `subprocess.CalledProcessError`.

## Diagnosis: environment, not a limen bug

The exception is raised in the test's **own fixture helper**, on its first
statement, before any limen code executes:

```
tests/test_cli_integration.py:140:  _git(work, "init", "-q")
tests/test_cli_integration.py:135:  in _git  → subprocess.run([...], check=True)
E   subprocess.CalledProcessError: Command '['git', 'init', '-q']' returned non-zero exit status 1
```

Reproduced directly outside pytest:

```
$ mkdir /tmp/gittest && cd /tmp/gittest && git init -q
/private/tmp/gittest/.git: Operation not permitted
```

The sandbox this suite runs in write-protects `.git` structures in every
writable location and blocks `git init` / `git clone` (its own banner:
*"git protection is running in COARSE mode … .git structures are write-denied
in every writable location and git init/clone is blocked"*). `git` itself is
installed and readable operations work; only repository creation is denied.

limen's contact with git is a single command — `git diff --name-only <range>`
in `limen/review.py::changed_files`, used by `check --changed-only`,
`context --for-diff` and `review --for-diff`. None of those three tests ever
reached it. **No limen code was fixed, because none was broken.**

## Resolution

1. `tests/conftest.py` gains a one-shot capability probe, `git_init_works()`,
   which actually attempts `git init` in a temporary directory, and the marker
   `requires_git` built from it. The three tests carry `@requires_git`: they
   run normally wherever `git init` works, and skip with an explanatory reason
   where it does not (sandbox, or git not installed at all).
2. Skipping is not enough on its own — it would silently drop coverage of the
   diff-scoped code paths in exactly this environment. `tests/test_diff_paths.py`
   is new and covers the same paths **without a repository**: it puts a stub
   `git` on `PATH` that answers only `git diff --name-only` from a
   test-controlled file, then drives `check --changed-only`,
   `context --for-diff` and `review --for-diff` end-to-end, asserts that
   `changed_files()` de-duplicates and sorts, that a failing git surfaces as
   `RuntimeError`, and that `--changed-only` really scopes findings (same tree
   is green when the diff misses the violating file and reports
   `billing-no-direct-db` when it includes it).

## After

| | before | after |
| --- | --- | --- |
| passed | 121 | **128** |
| failed | 3 | **0** |
| skipped | 1 | 4 |
| line coverage (`--cov=limen`) | — | **93%** (2465 statements, 177 missed) |

The four skips are the three `requires_git` tests plus the pre-existing
`test_litrev_check_on_snapshot`, which needs `LIMEN_LITREV_WS`. Pointing that
variable at the workspace un-skips it and it passes:

```
$ LIMEN_LITREV_WS=/Users/sebastianmatysik/!!!!!Limen/workspace python -m pytest -q
129 passed, 3 skipped
```

On a machine with unrestricted git, all three `requires_git` tests run and the
expected result is **131 passed, 1 skipped** (or 132/0 with `LIMEN_LITREV_WS`
also set).

## Reproduce

```bash
cd /Users/sebastianmatysik/!!!!!Limen/limen
python -m pytest -q                                   # 128 passed, 4 skipped
python -m pytest -q -rs                               # shows the skip reasons
python -m pytest -q --cov=limen --cov-report=term-missing
LIMEN_LITREV_WS=/Users/sebastianmatysik/!!!!!Limen/workspace python -m pytest -q
```
