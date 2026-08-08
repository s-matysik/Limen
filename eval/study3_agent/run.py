#!/usr/bin/env python3
"""Study 3 — does an architecture contract change what a coding agent produces?

    export PYTHONPATH=<limen>/src
    python eval/study3_agent/run.py --plan          # write the run plan only
    python eval/study3_agent/run.py --execute       # run it (resumable)

This module is imported by the kernel (``host.llm`` lives there); ``--plan``
and ``--execute`` are the entry points, and ``execute()`` is safe to call
repeatedly — every finished run is a file on disk and is skipped.

DESIGN
------
Within-task, four conditions, R repetitions:

  A  prompt + the parent tree (module sources, no contract material at all)
  B  A + every AGENTS.md / CLAUDE.md in the frozen workspace (the prose the
     project actually ships: 33 files, 48 451 B measured)
  C  A + ``limen context --paths <the task's module dirs>`` — the scoped
     machine-readable contract
  D  C, then up to MAX_D_ITER repair rounds: run ``limen check`` on the
     agent's tree, hand back the findings, let it revise

MEASUREMENT
-----------
``limen check`` runs on the FULL frozen specification in every condition,
including D's repair loop input — except that the loop only ever SHOWS the
agent findings from shown-rule ids (see HELD-OUT below).

The primary measure is the *delta*: errors on the agent's tree minus errors on
the untouched parent tree.  The parent trees are historical and already carry
debt (3 errors on the be-01 parent, measured), so a raw count would mostly
measure pre-existing violations rather than what the agent did.  Both the raw
count and the baseline are recorded per run.

CO-PRIMARY MEASURE — functional regression
------------------------------------------
Single commits carry few tests of their own, so this is a REGRESSION criterion,
not a feature-acceptance criterion: the module suite that already exists in the
PARENT tree must not acquire NEW failures.  The criterion is set difference on
per-test identities, not "all green": the fe-* parent trees already carry one
red test (AvatarRendering, measured), and requiring green would discard four of
ten tasks for a defect that predates the agent.  ``tests_pass`` is therefore
"no failing test the parent tree did not already fail, AND not fewer passing
tests than the parent" — the second clause catches fatal errors that abort the
runner before it can name any individual test.  This cannot detect "the agent
did not implement the feature"; it detects "the agent broke what worked".
Stated as limitation L3 in the summary.

HELD-OUT RULES
--------------
Conditions C and D are given a REDUCED spec: HELDOUT_RULES are stripped before
``limen context`` renders, and D's repair findings are filtered to shown rules.
Scoring always uses the full spec.  Errors are then reported split into
``shown`` and ``heldout``.  Without this split the study would measure "the
agent did as it was told"; the held-out half measures whether the contract
changed the code in ways nobody pointed at.

CONTROLS
--------
* one model for every condition (``host.reasoning_model()``), recorded per run
* ``temperature`` is REJECTED by the API for this model ("`temperature` is
  deprecated for this model", verified) — the sampler default is used
  identically in every condition and recorded as ``temperature: null``
* execution order is a seeded shuffle of all (task, condition, rep) cells;
  the seed and the resulting order are written to the plan
* the specification is frozen: its sha256 is recorded in the plan and
  re-asserted before every run
* the agent works on a per-run copy under /tmp; the live repositories are only
  ever read via ``git archive``

REGISTRY POLICY
---------------
As in Study 2 (limitation L6 there): ``units.registry_policy: exact`` aborts
with exit 3 on any historical tree before a single rule runs, so the derived
spec used here relaxes it to ``open`` and changes nothing else.  Study 2
verified this is byte-inert on the frozen workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

EVAL = Path(__file__).resolve().parents[1]
LIMEN = EVAL.parent
ROOT = LIMEN.parent                                  # .../!!!!!Limen
LIVE = ROOT / "live"
FROZEN_WS = ROOT / "workspace"
SPEC_FROZEN = FROZEN_WS / "architecture.yaml"

SCRATCH = Path(os.environ.get("STUDY3_SCRATCH", "/tmp/limen_s3"))
RESULTS = EVAL / "results" / "study3"
PLAN_PATH = EVAL / "results" / "study3_plan.json"
TASKS = EVAL / "study3_agent" / "tasks.jsonl"

# Prepared dependency caches (built by prepare_caches(); heavy, shared, RO).
VENDOR_CACHE = Path("/tmp/laravel_check/litrev-backend/vendor")
FE_NM_CACHE = EVAL / "_scratch" / "study3" / "fe_probe" / "litrev-frontend" / "node_modules"
FS_NM_CACHE = EVAL / "_scratch" / "study3" / "fe_probe" / "app-shared" / "frontend-shared" / "node_modules"

SEED = 20260807
R_DEFAULT = 3
CONDITIONS = ("A", "B", "C", "D")
MAX_D_ITER = 2                 # protocol: at most 2 repair iterations
MAX_FILE_BYTES = 60_000        # per-file cap when showing sources to the agent
MAX_CONTEXT_BYTES = 260_000    # total source budget shown to the agent
MAX_OUTPUT_TOKENS = 16_000

SNAP = "litrev-arch-snapshot"

REPOS = {
    "litrev-backend":       {"root": "host_app",      "prefix": "app"},
    "backend-shared":       {"root": "shared",        "prefix": "src"},
    "litrev-core-backend":  {"root": "core",          "prefix": "src"},
    "litrev-frontend":      {"root": "frontend",      "prefix": "src"},
    "litrev-core-frontend": {"root": "core_frontend", "prefix": "src"},
    "frontend-shared":      {"root": None,            "prefix": "src"},
}

# Excluded at extraction time: limen ignores them anyway, they are huge, and
# docker/mysql/** is a committed database binary dump.
TAR_EXCLUDES = ["node_modules", "vendor", ".idea", ".vscode", "__pycache__",
                "dist", "build", "docker/mysql", "storage/logs", ".DS_Store"]

# --------------------------------------------------------------------------- #
# HELD-OUT RULES (cyrcularity control 3)
#
# Chosen ONCE, before any run, stratified over rule kinds so that both halves
# contain rules that empirically fire in this codebase (eval/results/
# study2_findings.csv). Frozen here as a literal so the split is auditable and
# identical on re-runs.
# --------------------------------------------------------------------------- #
HELDOUT_RULES = [
    "host-cross-module-public-surface",   # K4 public-api-only (254 findings in history)
    "host-into-core-baseline",            # K7 baseline-snapshot (276)
    "runtime-console-log",                # K8 pattern-ban (146)
    "modules-have-no-local-deployment",   # K9 structure (80)
    "support-and-models-module-import-allowlist",   # K5 scoped-allowlist (26)
    "shared-not-into-host-modules",       # K6 forbidden-target (60)
    "oversized-page-file",                # K10 metric-threshold (42)
]

# The sandbox denies reads of the user's git config; neutralise it.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": str(SCRATCH / "fakehome")}

AGENT_SYSTEM = """You are a senior developer working in an existing web application repository.

You are given a work request and the current contents of the files you are most
likely to need. Implement the request by editing and creating files.

Return your work by calling the `write_files` tool exactly once, at the end.
It takes two lists and you will normally use both:

* `edits` — for changing an EXISTING file. Each edit gives `path`, `find` (a
  snippet copied EXACTLY from the current file, including indentation, long
  enough to occur only once in that file) and `replace` (what it becomes).
  Prefer this: it is how you make a small change to a long file.
* `files` — for a file you create from scratch, or one you rewrite entirely.
  `content` must be the COMPLETE file, never an excerpt or a placeholder
  comment.

Rules of engagement:
* Paths are relative to the repository root exactly as shown in the listing.
* A `find` string that does not appear verbatim, or appears more than once, is
  rejected and that edit is lost — copy it from the text you were shown.
* Write code that runs. Follow the conventions visible in the surrounding code
  (naming, imports, error handling, response shapes).
* Do not modify or delete existing tests.
* Do not add dependencies.
* Keep the change scoped to the request. A file you do not touch stays as it
  is."""

WRITE_TOOL = {
    "name": "write_files",
    "description": "Apply your change: anchored edits to existing files and/or "
                   "complete contents for new or fully rewritten files. Call "
                   "exactly once, at the end of your work.",
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "Anchored replacements in existing files. "
                               "Preferred for changing long files.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "Repository-relative path."},
                        "find": {"type": "string",
                                 "description": "Exact snippet from the "
                                                "current file; must be unique "
                                                "within it."},
                        "replace": {"type": "string",
                                    "description": "Replacement text."},
                    },
                    "required": ["path", "find", "replace"],
                },
            },
            "files": {
                "type": "array",
                "description": "Complete contents for created or fully "
                               "rewritten files.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "Repository-relative path."},
                        "content": {"type": "string",
                                    "description": "Complete file content."},
                    },
                    "required": ["path", "content"],
                },
            },
            "notes": {"type": "string",
                      "description": "One or two sentences on what you did."},
        },
    },
}


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, env=GIT_ENV)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()[:300]}")
    return out.stdout


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tasks() -> list[dict]:
    return [json.loads(l) for l in TASKS.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def contemporaneous(repo: str, iso_date: str) -> str | None:
    """Last first-parent commit of `repo` with committer date <= iso_date."""
    out = git(LIVE / repo, "log", "--first-parent", "--format=%H",
              "--before", iso_date, "-1").strip()
    return out or None


def materialize(repo: str, sha: str) -> Path:
    """git archive <sha> | tar -x into a shared, reused, read-only tree."""
    dest = SCRATCH / "trees" / repo / sha
    marker = dest / ".limen-s3-ok"
    if marker.exists():
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    excludes = [f"--exclude={e}" for e in TAR_EXCLUDES]
    ar = subprocess.Popen(["git", "-C", str(LIVE / repo), "archive", sha],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=GIT_ENV)
    tar = subprocess.Popen(["tar", "-x", "-C", str(dest), *excludes],
                           stdin=ar.stdout, stderr=subprocess.DEVNULL)
    ar.stdout.close()
    tar.communicate()
    ar.wait()
    marker.write_text(sha + "\n", encoding="utf-8")
    return dest


def task_state(task: dict) -> dict[str, str | None]:
    """{repo: sha} for the task's parent commit plus contemporaneous others."""
    iso = git(LIVE / task["repo"], "show", "-s", "--format=%cI",
              task["parent_sha"]).strip()
    state = {task["repo"]: task["parent_sha"]}
    for repo in REPOS:
        if repo == task["repo"]:
            continue
        state[repo] = contemporaneous(repo, iso)
    return state


def derive_spec(out: Path, drop_rules: list[str] | None = None) -> Path:
    """Frozen spec with registry_policy relaxed to `open`; optionally with
    `drop_rules` removed (used for the C/D agent-facing context only)."""
    doc = yaml.safe_load(SPEC_FROZEN.read_text(encoding="utf-8"))
    doc.setdefault("units", {})["registry_policy"] = "open"
    if drop_rules:
        doc["rules"] = [r for r in doc.get("rules", [])
                        if r.get("id") not in set(drop_rules)]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# run trees
# --------------------------------------------------------------------------- #

def clone(src: Path, dest: Path) -> None:
    """Copy `src` to `dest`, using APFS copy-on-write clones when available.

    `cp -Rc` makes a 157 MB vendor/ tree cost ~7 s and almost no disk, which is
    what makes a per-run physical vendor affordable at all.  Falls back to a
    plain recursive copy on filesystems without clone support.
    """
    p = subprocess.run(["cp", "-Rc", str(src), str(dest)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        shutil.copytree(src, dest, symlinks=True)


def link_node_modules(tree: Path, cache: Path, shared_tree: Path | None) -> None:
    """Per-entry symlinks from `cache` into `tree/node_modules`.

    `@litrev` is rebuilt so `frontend-common` resolves to `shared_tree` (this
    run's writable frontend-shared) rather than to whatever tree the cache was
    installed against.
    """
    if not tree.exists() or not cache.exists():
        return
    nm = tree / "node_modules"
    if nm.is_symlink() or nm.exists():
        if nm.is_symlink():
            nm.unlink()
        else:
            shutil.rmtree(nm)
    nm.mkdir()
    for entry in cache.iterdir():
        if entry.name == "@litrev":
            continue
        (nm / entry.name).symlink_to(entry)
    if shared_tree is not None:
        scope = nm / "@litrev"
        scope.mkdir()
        (scope / "frontend-common").symlink_to(shared_tree.resolve())


def deanachronize_vendor(be: Path, shared_tree: Path) -> None:
    """Drop auto-discovered providers whose package does not exist in THIS tree.

    The vendor cache was installed against a late-2026 backend, where
    `litrev/backend-shared` is a required path package and Laravel's package
    discovery registers ``LitRev\\BackendShared\\BackendSharedServiceProvider``.
    Study-3 layer-1 parents are OLDER than the backend-shared repository
    itself (backend-shared's first commit is 2026-05-10; commit 46bbf692's
    parent is 2026-04-30), so `repos/backend-shared` is an empty stub and the
    provider file does not exist.  Laravel then aborts during bootstrap with
    "Failed to open stream" before a single test is collected — measured:
    `php artisan test --testsuite ProjectsModule` exits 1 with an empty
    summary line, which the harness would otherwise score as a task whose
    parent suite cannot run.

    The fix is anachronism removal, not a behaviour change: the provider is
    stripped from the discovery manifest only when its file is absent, so on
    trees that DO ship backend-shared nothing is touched.
    """
    if (shared_tree / "src" / "BackendSharedServiceProvider.php").exists():
        return
    installed = be / "vendor" / "composer" / "installed.json"
    if installed.exists():
        doc = json.loads(installed.read_text(encoding="utf-8"))
        packages = doc.get("packages") or []
        for pkg in packages:
            if pkg.get("name") == "litrev/backend-shared":
                pkg.get("extra", {}).get("laravel", {})["providers"] = []
        installed.write_text(json.dumps(doc), encoding="utf-8")
    # Laravel regenerates these from installed.json on the next boot.
    for stale in ("packages.php", "services.php"):
        p = be / "bootstrap" / "cache" / stale
        if p.exists():
            p.unlink()


def build_run_tree(task: dict, state: dict, run_dir: Path) -> dict:
    """Assemble a writable working copy plus the limen workspace view.

    Layout (satisfies BOTH the test runners and the spec's root paths):

        <run>/repos/litrev-backend/          writable; shared, core-backend and
                                             vendor/litrev/backend-shared wired
                                             the way composer's autoloader
                                             expects; vendor -> shared cache
                    backend-shared/          writable
                    litrev-core-backend/     writable
                    litrev-frontend/         writable
                    litrev-core-frontend/    writable
                    frontend-shared/         writable
                    app-shared/frontend-shared -> ../frontend-shared
                    app/LitRev-core-frontend   -> ../litrev-core-frontend
             ws/litrev-arch-snapshot/<repo> -> repos/<repo>

    The `app-shared` / `app` links must be SIBLINGS of litrev-frontend: its
    vite.config.js resolves `../app-shared/frontend-shared/src/...` relative to
    its own location, and runs with `preserveSymlinks: true`.

    node_modules is linked ENTRY BY ENTRY from the shared cache, except
    `@litrev/frontend-common`, which is pointed at THIS run's frontend-shared
    tree.  A whole-directory link would drag in the cache's own
    `@litrev/frontend-common`, i.e. the tree the cache was built from — the
    agent's edits would then be invisible to the test run.

    `vendor/` is CLONED, not linked (`cp -Rc`, APFS copy-on-write: 157 MB in
    ~7 s at near-zero disk cost).  A symlinked vendor makes __DIR__ inside
    vendor/composer/* resolve to the cache, so composer's classmap points the
    App namespace at the CACHE's copy of the source — tests then run against
    the snapshot the cache was built from and are blind to the agent's edits.
    Verified: with a symlinked vendor an injected `class ProjectController` ->
    `class ProjectController_BROKEN` left the suite green; with a cloned vendor
    it fails (exit 255, 41 passing tests lost), which is what the regression
    measure requires.
    """
    repos_dir = run_dir / "repos"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    repos_dir.mkdir(parents=True)

    for repo, meta in REPOS.items():
        dest = repos_dir / repo
        sha = state.get(repo)
        if sha is None:
            (dest / meta["prefix"]).mkdir(parents=True)
            continue
        clone(materialize(repo, sha), dest)

    # backend: composer autoload expects `shared` and `core-backend` beside it,
    # and vendor/litrev/backend-shared to point at the shared tree.
    be = repos_dir / "litrev-backend"
    if (be / "composer.json").exists():
        for name, target in (("shared", "../backend-shared"),
                             ("core-backend", "../litrev-core-backend")):
            link = be / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(target)
        if VENDOR_CACHE.exists():
            clone(VENDOR_CACHE, be / "vendor")
            litrev = be / "vendor" / "litrev"
            if litrev.exists():
                shutil.rmtree(litrev)
            litrev.mkdir(parents=True)
            (litrev / "backend-shared").symlink_to("../../shared")
            deanachronize_vendor(be, repos_dir / "backend-shared")

    # frontend: vite.config.js resolves ../app-shared/... and
    # ../app/LitRev-core-frontend RELATIVE TO ITSELF, so these must sit beside
    # litrev-frontend inside repos/.
    (repos_dir / "app-shared").mkdir()
    (repos_dir / "app-shared" / "frontend-shared").symlink_to(
        "../frontend-shared")
    (repos_dir / "app").mkdir()
    (repos_dir / "app" / "LitRev-core-frontend").symlink_to(
        "../litrev-core-frontend")

    link_node_modules(repos_dir / "litrev-frontend", FE_NM_CACHE,
                      repos_dir / "frontend-shared")
    link_node_modules(repos_dir / "frontend-shared", FS_NM_CACHE, None)

    ws = run_dir / "ws"
    (ws / SNAP).mkdir(parents=True)
    for repo in REPOS:
        (ws / SNAP / repo).symlink_to(repos_dir / repo)

    return {"repos": repos_dir, "ws": ws, "run_dir": run_dir}


# --------------------------------------------------------------------------- #
# limen
# --------------------------------------------------------------------------- #

def limen(args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(LIMEN / "src")}
    return subprocess.run([sys.executable, "-m", "limen.cli", *args],
                          capture_output=True, text=True, env=env)


def run_check(ws: Path, spec: Path, today: str) -> dict:
    p = limen(["check", "--spec", str(spec), "--workspace", str(ws),
               "--today", today, "--format", "json"])
    if p.returncode in (3, 4):
        return {"exit_code": p.returncode, "error": p.stderr.strip()[:600],
                "findings": None}
    report = json.loads(p.stdout)
    findings = report.get("findings") or []
    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warning"]
    return {"exit_code": p.returncode,
            "n_errors": len(errors), "n_warnings": len(warnings),
            "n_errors_shown": sum(1 for f in errors
                                  if f.get("rule_id") not in set(HELDOUT_RULES)),
            "n_errors_heldout": sum(1 for f in errors
                                    if f.get("rule_id") in set(HELDOUT_RULES)),
            "findings": findings}


def run_context(ws: Path, spec: Path, today: str, paths: list[str]) -> dict:
    p = limen(["context", "--spec", str(spec), "--workspace", str(ws),
               "--today", today, "--paths", *paths])
    return {"exit_code": p.returncode, "text": p.stdout,
            "stderr": p.stderr.strip()[:400]}


# --------------------------------------------------------------------------- #
# functional tests
# --------------------------------------------------------------------------- #

ANSI = re.compile(r"\x1b\[[0-9;]*m")

# PHPUnit's own summary; keys appear in any order and only when non-zero.
_PHP_KV = re.compile(r"(\d+)\s+(passed|failed|errors?|warnings?|risky|skipped|"
                     r"incomplete|deprecated|notices?)")
# vitest: "Tests  1 failed | 87 passed (88)"
_VITEST_KV = re.compile(r"(\d+)\s+(passed|failed|skipped|todo)")

# Per-test identities of the failures, so a run can be compared against the
# parent tree's failure SET rather than merely against "all green".
_PHP_FAIL = re.compile(r"^\s*(?:FAILED|ERROR)\s+(\S+::\S+)", re.MULTILINE)
_PHP_FAIL_LINE = re.compile(r"^\s*[⨯x]\s+(.+?)(?:\s+\d+\.\d+s)?\s*$",
                            re.MULTILINE)
_VITEST_FAIL = re.compile(r"^\s*FAIL\s+(\S+)\s*>\s*(.+?)\s*$", re.MULTILINE)
# node --test (`ℹ pass 9` / `ℹ fail 0`, failures marked `✖ name (1.0ms)`)
_NODETEST_KV = re.compile(r"^\s*(?:ℹ\s*)?(pass|fail|skipped|todo)\s+(\d+)\s*$",
                          re.MULTILINE)
_NODETEST_FAIL = re.compile(r"^\s*[✖x]\s+(.+?)(?:\s+\([\d.]+ms\))?\s*$",
                            re.MULTILINE)


def _totals(pattern: re.Pattern, line: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for n, key in pattern.findall(line):
        key = key.rstrip("s") if key.startswith("error") else key
        out[key] = out.get(key, 0) + int(n)
    return out


def run_tests(task: dict, trees: dict, timeout: int = 1200) -> dict:
    """Run the module suite that already exists in the PARENT tree.

    Returns totals plus the identity of every failing test, so the caller can
    distinguish a REGRESSION (a failure the parent tree did not have) from a
    failure the parent tree already carried.
    """
    if task["repo"] == "litrev-backend":
        cwd = trees["repos"] / "litrev-backend"
        results, fails = [], []
        for suite in task["test_suites"]:
            p = subprocess.run(["php", "artisan", "test", "--testsuite", suite],
                               cwd=str(cwd), capture_output=True, text=True,
                               timeout=timeout,
                               env={**os.environ, "APP_ENV": "testing"})
            blob = ANSI.sub("", (p.stdout or "") + (p.stderr or ""))
            summary = next((l for l in blob.splitlines()
                            if l.strip().startswith("Tests:")), "")
            g = _totals(_PHP_KV, summary)
            suite_fails = [f"{suite}::{n}" for n in _PHP_FAIL.findall(blob)] \
                or [f"{suite}::{n.strip()}" for n in _PHP_FAIL_LINE.findall(blob)]
            fails += suite_fails
            results.append({
                "suite": suite, "exit_code": p.returncode,
                "passed": g.get("passed", 0), "failed": g.get("failed", 0),
                "errors": g.get("error", 0), "summary_line": summary.strip(),
                "tail": blob[-2000:],
            })
        return {"runner": "php artisan test", "suites": results,
                "green": all(r["exit_code"] == 0 for r in results),
                "n_passed": sum(r["passed"] for r in results),
                "n_failed": sum(r["failed"] + r["errors"] for r in results),
                "failing": sorted(set(fails))}

    cwd = trees["repos"] / "litrev-frontend"
    env = {**os.environ, "npm_config_cache": str(SCRATCH / "npmcache"),
           "CI": "1"}
    # Study-3 pilot 2 reaches further back than pilot 1 did: at 2026-03-22 the
    # frontend has no `test:components` script at all (its `test` script is a
    # bare `node --test` over src/lib), so the runner has to come from the
    # task.  Defaults to pilot 1's script, which keeps every pilot-1 task and
    # its recorded baselines byte-identical.
    script = task.get("npm_test_script") or "test:components"
    p = subprocess.run(["npm", "run", script, "--silent"],
                       cwd=str(cwd), capture_output=True, text=True,
                       timeout=timeout, env=env)
    blob = ANSI.sub("", (p.stdout or "") + (p.stderr or ""))
    summary = next((l for l in blob.splitlines()
                    if l.strip().startswith("Tests ")), "")
    g = _totals(_VITEST_KV, summary)
    fails = sorted({f"{f}>{n}" for f, n in _VITEST_FAIL.findall(blob)})
    if not summary:
        # node --test (the 2026-03 frontend) reports totals as `ℹ pass 9` /
        # `ℹ fail 0` lines and names failures as `✖ <name>`; vitest's regexes
        # find nothing there and would score a healthy run as 0 passed.
        totals = {k: int(v) for k, v in _NODETEST_KV.findall(blob)}
        fails = sorted(set(_NODETEST_FAIL.findall(blob)))
        summary = f"pass {totals.get('pass', 0)} fail {totals.get('fail', 0)}"
        g = {"passed": totals.get("pass", 0), "failed": totals.get("fail", 0)}
    return {"runner": f"npm run {script}",
            "suites": [{"suite": script, "exit_code": p.returncode,
                        "passed": g.get("passed", 0),
                        "failed": g.get("failed", 0), "errors": 0,
                        "summary_line": summary.strip(), "tail": blob[-2400:]}],
            "green": p.returncode == 0,
            "n_passed": g.get("passed", 0), "n_failed": g.get("failed", 0),
            "failing": fails}


# --------------------------------------------------------------------------- #
# agent context
# --------------------------------------------------------------------------- #

CODE_EXT = {".php", ".js", ".jsx", ".ts", ".tsx", ".mjs"}
EXTRA_BE = ["routes/api.php", "app/Models"]
EXTRA_FE = ["src/shared/http", "src/app/App.jsx"]


def source_bundle(task: dict, trees: dict) -> dict:
    """The tree material every condition sees: a file listing plus contents.

    Identical across conditions by construction — the conditions differ ONLY in
    the contract material appended after it.
    """
    repos = trees["repos"]
    if task.get("source_roots"):
        # Pilot 2 reaches back to trees that predate the module layout these
        # defaults assume: at 90f87301 (2026-03-22) the frontend has no
        # src/modules and no src/shared/http, so the default roots select
        # nothing and the agent would be handed an EMPTY tree (measured: 0 B,
        # 0 files).  A task on such a tree names its own roots, repo-relative.
        base = repos / task["repo"]
        roots = [base / r for r in task["source_roots"]]
    elif task["repo"] == "litrev-backend":
        roots = [repos / "litrev-backend" / "app" / "Modules" / m
                 for m in task["modules"]]
        roots += [repos / "backend-shared" / "src" / m for m in task["modules"]]
        roots += [repos / "litrev-backend" / e for e in EXTRA_BE]
        base = repos / "litrev-backend"
    else:
        roots = [repos / "litrev-frontend" / "src" / "modules" / m
                 for m in task["modules"]]
        roots += [repos / "litrev-frontend" / e for e in EXTRA_FE]
        base = repos / "litrev-frontend"

    files: list[Path] = []
    for r in roots:
        if r.is_file():
            files.append(r)
        elif r.is_dir():
            files += [p for p in sorted(r.rglob("*"))
                      if p.is_file() and p.suffix in CODE_EXT
                      and ".test." not in p.name and not p.name.endswith("Test.php")]

    listing, blobs, total, shown, omitted = [], [], 0, 0, 0
    for p in files:
        try:
            rel = p.relative_to(base)
            repo_label = base.name
        except ValueError:
            rel = p.relative_to(repos)
            repo_label = ""
        key = f"{repo_label}/{rel}" if repo_label and repo_label != base.name else str(rel)
        key = str(rel) if p.is_relative_to(base) else str(p.relative_to(repos))
        size = p.stat().st_size
        listing.append(f"{key} ({size} B)")
        if size > MAX_FILE_BYTES or total + size > MAX_CONTEXT_BYTES:
            omitted += 1
            continue
        blobs.append(f"===== FILE: {key} =====\n"
                     + p.read_text(encoding="utf-8", errors="replace"))
        total += size
        shown += 1
    return {"listing": listing, "text": "\n\n".join(blobs),
            "n_listed": len(listing), "n_shown": shown, "n_omitted": omitted,
            "bytes": total, "base": str(base)}


def prose_bundle() -> dict:
    """Condition B: every AGENTS.md / CLAUDE.md in the frozen workspace."""
    files = sorted(
        [p for p in FROZEN_WS.rglob("AGENTS.md") if SNAP not in p.parts]
        + [p for p in FROZEN_WS.rglob("CLAUDE.md") if SNAP not in p.parts])
    parts, total = [], 0
    for p in files:
        rel = p.relative_to(FROZEN_WS)
        parts.append(f"===== {rel} =====\n"
                     + p.read_text(encoding="utf-8", errors="replace"))
        total += p.stat().st_size
    return {"text": "\n\n".join(parts), "n_files": len(files), "bytes": total}


def build_prompt(task: dict, cond: str, src: dict, prose: dict | None,
                 ctx: dict | None, findings_text: str | None) -> str:
    head = [
        "## Work request", "", task["prompt"], "",
        f"## Repository", "",
        f"You are working in `{task['repo']}`. Paths below are relative to its "
        "root.", "",
        "### Files present in the area you will touch", "",
        "\n".join(f"- {l}" for l in src["listing"]), "",
    ]
    if src["n_omitted"]:
        head.append(f"({src['n_omitted']} of these are not reproduced below "
                    "because of size; ask for none — work with what is shown.)")
        head.append("")
    head += ["### Current contents", "", src["text"], ""]

    if cond == "B" and prose:
        head += ["## Project documentation", "",
                 "The following documents are checked into the repositories "
                 "and describe how this codebase is organised. They apply to "
                 "your change.", "", prose["text"], ""]
    if cond in ("C", "D") and ctx:
        head += ["## Architecture contract for the paths you are about to "
                 "change", "",
                 "This is generated from the project's machine-checked "
                 "architecture specification and is enforced in CI.", "",
                 ctx["text"], ""]
    if findings_text:
        head += ["## The architecture check rejected your previous attempt",
                 "",
                 "Your previous version of these files produced the findings "
                 "below. Fix them and return the corrected complete files. "
                 "Keep the work request satisfied.", "", findings_text, ""]
    head += ["## Now do the work", "",
             "Call `write_files` once. Use `edits` (exact-snippet anchors) to "
             "change existing files and `files` (complete content) only for "
             "files you create or rewrite in full."]
    return "\n".join(head)


def format_findings(findings: list[dict], shown_only: bool) -> str:
    keep = [f for f in findings if f.get("severity") == "error"]
    if shown_only:
        keep = [f for f in keep if f.get("rule_id") not in set(HELDOUT_RULES)]
    if not keep:
        return ""
    lines = []
    for f in keep:
        lines.append(f"- [{f.get('rule_id')}] {f.get('file')}"
                     f":{f.get('line')}\n  {f.get('message')}\n"
                     f"  target: {f.get('target')}\n"
                     f"  remedy: {f.get('remedy')}")
    return "\n".join(lines)


def _resolve_target(base: Path, rel: str) -> tuple[Path | None, str]:
    """Path inside the repo, or (None, why) if it must be refused."""
    rel = str(rel or "").strip().lstrip("/")
    if not rel:
        return None, "empty path"
    dest = (base / rel).resolve()
    if not str(dest).startswith(str(base.resolve()) + os.sep):
        return None, "escapes repository root"
    if ".test." in dest.name or dest.name.endswith("Test.php") \
            or "/tests/" in str(dest).replace(str(base.resolve()), ""):
        return None, "test file"
    return dest, ""


def apply_files(trees: dict, task: dict, payload: dict) -> dict:
    """Apply the agent's `edits` then `files` to its own run tree.

    Refusals are recorded rather than raised, because a rejected edit is data:
    it says the agent produced an anchor that does not exist in the file.
    Test files are refused here as well as forbidden in the instructions.
    """
    base = Path(trees["repos"]) / task["repo"]
    written, rejected = [], []

    for entry in payload.get("edits") or []:
        # The model occasionally emits a bare string instead of the object the
        # schema asks for; that is data about the agent, not a harness crash.
        if not isinstance(entry, dict):
            rejected.append({"kind": "edit", "path": None,
                             "why": f"not an object ({type(entry).__name__})"})
            continue
        rel = entry.get("path")
        dest, why = _resolve_target(base, rel)
        if dest is None:
            rejected.append({"kind": "edit", "path": rel, "why": why})
            continue
        if not dest.exists():
            rejected.append({"kind": "edit", "path": rel, "why": "no such file"})
            continue
        find, repl = entry.get("find"), entry.get("replace")
        if not find or repl is None:
            rejected.append({"kind": "edit", "path": rel,
                             "why": "empty find or replace"})
            continue
        text = dest.read_text(encoding="utf-8", errors="replace")
        n = text.count(find)
        if n != 1:
            rejected.append({"kind": "edit", "path": rel,
                             "why": f"anchor occurs {n} times"})
            continue
        dest.write_text(text.replace(find, repl, 1), encoding="utf-8")
        written.append({"kind": "edit", "path": rel,
                        "delta_bytes": len(repl.encode()) - len(find.encode())})

    for entry in payload.get("files") or []:
        if not isinstance(entry, dict):
            rejected.append({"kind": "file", "path": None,
                             "why": f"not an object ({type(entry).__name__})"})
            continue
        rel = entry.get("path")
        dest, why = _resolve_target(base, rel)
        if dest is None:
            rejected.append({"kind": "file", "path": rel, "why": why})
            continue
        content = entry.get("content")
        if content is None:
            rejected.append({"kind": "file", "path": rel, "why": "no content"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        existed = dest.exists()
        dest.write_text(content, encoding="utf-8")
        written.append({"kind": "file", "path": rel,
                        "bytes": len(content.encode()), "created": not existed})

    return {"written": written, "rejected": rejected,
            "n_edits": len(payload.get("edits") or []),
            "n_files": len(payload.get("files") or [])}


def diff_of(trees: dict, task: dict, state: dict) -> str:
    """Unified diff of the agent's tree against the pristine parent tree."""
    pristine = materialize(task["repo"], state[task["repo"]])
    mutated = Path(trees["repos"]) / task["repo"]
    # `bootstrap/cache/*` and `.phpunit.result.cache` are written by Laravel and
    # PHPUnit while the tests run — they are run artefacts, not agent output.
    p = subprocess.run(
        ["diff", "-ruN",
         "--exclude=vendor", "--exclude=node_modules", "--exclude=.limen-s3-ok",
         "--exclude=shared", "--exclude=core-backend", "--exclude=docker",
         "--exclude=.phpunit.result.cache", "--exclude=storage",
         "--exclude=packages.php", "--exclude=services.php",
         str(pristine), str(mutated)],
        capture_output=True, text=True)
    return p.stdout


# --------------------------------------------------------------------------- #
# one cell
# --------------------------------------------------------------------------- #

def run_cell(task: dict, cond: str, rep: int, plan: dict, host) -> dict:
    """Execute one (task, condition, repetition) cell and write its JSON."""
    out_path = RESULTS / task["task_id"] / f"{cond}_{rep}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    assert sha256_file(SPEC_FROZEN) == plan["spec_sha256"], \
        "architecture.yaml changed mid-run — the rule set is not frozen"

    state = {k: v for k, v in plan["task_state"][task["task_id"]].items()}
    today = plan["task_today"][task["task_id"]]
    spec_full = Path(plan["spec_full"])
    spec_shown = Path(plan["spec_shown"])

    run_dir = SCRATCH / "runs" / task["task_id"] / f"{cond}_{rep}"
    trees = build_run_tree(task, state, run_dir)

    src = source_bundle(task, trees)
    prose = prose_bundle() if cond == "B" else None
    ctx = (run_context(trees["ws"], spec_shown, today, task["module_paths"])
           if cond in ("C", "D") else None)

    record = {
        "task_id": task["task_id"], "condition": cond, "repetition": rep,
        "model": plan["model"], "temperature": None,
        "spec_sha256": plan["spec_sha256"],
        "seed": plan["seed"], "heldout_rules": HELDOUT_RULES,
        "repo": task["repo"], "parent_sha": task["parent_sha"],
        "workspace_state": state, "today": today,
        "context": {
            "source_bytes": src["bytes"], "source_files_shown": src["n_shown"],
            "source_files_listed": src["n_listed"],
            "prose_bytes": (prose or {}).get("bytes", 0),
            "prose_files": (prose or {}).get("n_files", 0),
            "limen_context_bytes": len((ctx or {}).get("text", "")),
        },
        "baseline": plan["baseline"][task["task_id"]],
        "iterations": [],
    }

    findings_text = None
    total_in = total_out = 0
    for it in range(MAX_D_ITER + 1 if cond == "D" else 1):
        prompt = build_prompt(task, cond, src, prose, ctx, findings_text)
        res = host.llm({"prompt": prompt, "system": AGENT_SYSTEM,
                        "model": plan["model"], "tools": [WRITE_TOOL],
                        "tool_choice": {"type": "tool", "name": "write_files"},
                        "max_tokens": MAX_OUTPUT_TOKENS})
        usage = res.get("usage") or {}
        total_in += int(usage.get("input_tokens") or 0)
        total_out += int(usage.get("output_tokens") or 0)
        tool = res.get("tool_use") or {}
        payload = tool.get("input") or {}
        applied = apply_files(trees, task, payload)
        chk = run_check(trees["ws"], spec_full, today)
        record["iterations"].append({
            "iteration": it,
            "prompt_chars": len(prompt),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "stop_reason": res.get("stop_reason"),
            "usage": usage,
            "notes": payload.get("notes"),
            "applied": applied,
            "check": {k: v for k, v in chk.items() if k != "findings"},
            "findings_shown_to_agent": None,
        })
        if cond != "D":
            break
        nxt = format_findings(chk.get("findings") or [], shown_only=True)
        record["iterations"][-1]["findings_shown_to_agent"] = bool(nxt)
        if not nxt or it == MAX_D_ITER:
            break
        findings_text = nxt

    final = run_check(trees["ws"], spec_full, today)
    # diff BEFORE tests: the test runners write cache files into the tree.
    diff = diff_of(trees, task, state)
    tests = run_tests(task, trees)
    base = record["baseline"]
    # Regression == a failing test the PARENT tree did not already fail, OR
    # fewer tests passing than the parent.  The second clause is load-bearing:
    # a fatal error aborts the run before any per-test identity is emitted
    # (measured — an injected PHP class rename gives exit 255, an empty summary
    # line and zero parsed failures), so the identity set alone would call the
    # broken tree clean.
    new_failures = sorted(set(tests.get("failing") or [])
                          - set(base["tests"].get("failing") or []))
    lost_passes = int(base["tests"]["n_passed"]) - int(tests["n_passed"])

    record.update({
        "final_check": {k: v for k, v in final.items() if k != "findings"},
        "final_findings": final.get("findings") or [],
        "delta_errors": final["n_errors"] - base["check"]["n_errors"],
        "delta_errors_shown": final["n_errors_shown"] - base["check"]["n_errors_shown"],
        "delta_errors_heldout": final["n_errors_heldout"] - base["check"]["n_errors_heldout"],
        "tests": {k: v for k, v in tests.items() if k != "suites"},
        "tests_suites": tests["suites"],
        "tests_green": bool(tests["green"]),
        "tests_new_failures": new_failures,
        "tests_lost_passes": lost_passes,
        "tests_pass": (not new_failures) and lost_passes <= 0,
        "iterations_used": len(record["iterations"]),
        "d_clean_at_iteration": next(
            (i["iteration"] for i in record["iterations"]
             if i["check"].get("n_errors_shown") is not None
             and i["check"]["n_errors_shown"] <= base["check"]["n_errors_shown"]),
            None),
        "diff": diff,
        "diff_bytes": len(diff),
        "tokens": {"input": total_in, "output": total_out},
        "seconds": round(time.time() - t0, 2),
    })
    out_path.write_text(json.dumps(record, indent=1, ensure_ascii=False),
                        encoding="utf-8")
    shutil.rmtree(run_dir, ignore_errors=True)
    return record


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #

def make_plan(tasks: list[dict], reps: int, host) -> dict:
    (SCRATCH / "fakehome").mkdir(parents=True, exist_ok=True)
    spec_full = derive_spec(SCRATCH / "spec_full_open.yaml")
    spec_shown = derive_spec(SCRATCH / "spec_shown_open.yaml", HELDOUT_RULES)

    plan = {
        "seed": SEED, "reps": reps, "conditions": list(CONDITIONS),
        "k": len(tasks),
        "model": host.reasoning_model(),
        "session_model": host.current_model(),
        "temperature": None,
        "temperature_note": "`temperature` is rejected by the API for this "
                            "model ('deprecated for this model'); the sampler "
                            "default applies identically in every condition.",
        "spec_frozen": str(SPEC_FROZEN),
        "spec_sha256": sha256_file(SPEC_FROZEN),
        "spec_full": str(spec_full), "spec_shown": str(spec_shown),
        "heldout_rules": HELDOUT_RULES,
        "max_d_iterations": MAX_D_ITER,
        "task_state": {}, "task_today": {}, "baseline": {},
    }

    for t in tasks:
        state = task_state(t)
        iso = git(LIVE / t["repo"], "show", "-s", "--format=%cI",
                  t["parent_sha"]).strip()
        today = iso[:10]
        plan["task_state"][t["task_id"]] = state
        plan["task_today"][t["task_id"]] = today

        run_dir = SCRATCH / "baseline" / t["task_id"]
        trees = build_run_tree(t, state, run_dir)
        chk = run_check(trees["ws"], Path(plan["spec_full"]), today)
        tests = run_tests(t, trees)
        src = source_bundle(t, trees)
        ctx = run_context(trees["ws"], Path(plan["spec_shown"]), today,
                          t["module_paths"])
        ctx_full = run_context(trees["ws"], Path(plan["spec_full"]), today,
                              t["module_paths"])
        plan["baseline"][t["task_id"]] = {
            "check": {k: v for k, v in chk.items() if k != "findings"},
            "findings": chk.get("findings") or [],
            "tests": {k: v for k, v in tests.items() if k != "suites"},
            "tests_suites": tests["suites"],
            "tests_green": bool(tests["green"]),
            "source_bytes": src["bytes"], "source_files": src["n_shown"],
            "limen_context_bytes_shown": len(ctx["text"]),
            "limen_context_bytes_full": len(ctx_full["text"]),
        }
        shutil.rmtree(run_dir, ignore_errors=True)

    # A task is excluded only if its parent suite cannot RUN at all (no test
    # collected). Pre-existing red tests are handled by the set-difference
    # regression criterion, not by exclusion.
    plan["excluded_tasks"] = [
        tid for tid, b in plan["baseline"].items()
        if b["tests"]["n_passed"] + b["tests"]["n_failed"] == 0]
    live_tasks = [t["task_id"] for t in tasks
                  if t["task_id"] not in plan["excluded_tasks"]]
    cells = [(tid, c, r) for tid in live_tasks
             for c in CONDITIONS for r in range(1, reps + 1)]
    rng = random.Random(SEED)
    rng.shuffle(cells)
    plan["order"] = [{"task_id": a, "condition": b, "repetition": c}
                     for a, b, c in cells]
    plan["n_cells"] = len(cells)
    prose = prose_bundle()
    plan["prose_bundle"] = {"n_files": prose["n_files"], "bytes": prose["bytes"]}

    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    return plan


def execute(plan: dict, host, limit: int | None = None,
            only_task: str | None = None) -> dict:
    tasks = {t["task_id"]: t for t in load_tasks()}
    done, ran, failed = 0, [], []
    for cell in plan["order"]:
        if only_task and cell["task_id"] != only_task:
            continue
        p = (RESULTS / cell["task_id"]
             / f"{cell['condition']}_{cell['repetition']}.json")
        if p.exists():
            done += 1
            continue
        if limit is not None and len(ran) >= limit:
            break
        try:
            rec = run_cell(tasks[cell["task_id"]], cell["condition"],
                           cell["repetition"], plan, host)
            ran.append({"cell": cell, "delta": rec["delta_errors"],
                        "tests_pass": rec["tests_pass"]})
            print(json.dumps({"cell": cell, "ok": True,
                              "delta": rec["delta_errors"]}), flush=True)
        except Exception as exc:                      # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"[:400]
            failed.append({"cell": cell, "error": msg})
            print(json.dumps({"cell": cell, "ok": False, "error": msg[:120]}),
                  flush=True)
            # A frame-level LLM budget exhaustion will hit every remaining
            # cell identically; stop rather than burn 95 identical failures.
            if "token ceiling" in msg:
                print(json.dumps({"stopped": "llm token ceiling reached",
                                  "ran": len(ran)}), flush=True)
                break
    return {"already_done": done, "ran": ran, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--reps", type=int, default=R_DEFAULT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-task", default=None)
    args = ap.parse_args()
    raise SystemExit("run.py is driven from the kernel (host.llm lives there): "
                     "import eval/study3_agent/run.py, then make_plan(...) / "
                     "execute(...). CLI flags are accepted for documentation "
                     f"only (got {vars(args)}).")


if __name__ == "__main__":
    main()
