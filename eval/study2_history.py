#!/usr/bin/env python3
"""Study 2 — architecture findings across repository history.

WHAT THIS DOES
--------------
For every first-parent commit of the six LitRev repositories it materialises the
tree (``git archive <SHA> | tar -x`` — never ``git checkout``; the live repos are
read-only), assembles a workspace in the layout today's specification expects,
and runs ``limen check --format json --today <commit date>``.

METHODOLOGICAL LIMITATIONS — read before reading any number
-----------------------------------------------------------
L1  ANACHRONISM.  ONE specification is used for the whole history: today's
    ``architecture.yaml``.  Rules are NOT reconstructed per commit.  This is a
    deliberate choice: reconstructing rules per commit would measure how the
    rules changed, not how the code changed.  The consequence is that a finding
    dated before the rule (or the legacy checker it retrofits) existed is
    ANACHRONISTIC — it says "this commit would violate today's contract", not
    "this commit violated a contract in force at the time".

L2  ``--today`` ONLY DRIVES EXEMPTION EXPIRY.  It is passed as the commit's
    calendar date so that ``expires:`` is evaluated against commit time rather
    than wall clock (``limen/spec/model.py::Exemption.status``).  Every debt
    exemption in today's spec carries ``since: 2026-08-04, expires:
    2026-12-31``; every commit in this window predates the expiry, so within
    this history ``--today`` is a verified no-op.  ``--verify-today`` reruns the
    last time point with the expiry crossed to demonstrate that the flag is
    live and not silently ignored.

L3  WORKSPACE RECONSTRUCTION (axis ``workspace``).  The specification describes
    a five-root workspace, but history is per repository and the repositories
    are not committed in lockstep.  Axis ``workspace`` reconstructs the state at
    time T by taking, for each repository, its last first-parent commit with
    committer date <= T.  This yields a single, consistent timeline on which
    cross-repository rules (K4 public-api-only, K7 baseline-snapshot) can be
    evaluated at all — but a reconstructed combination need never have existed
    on any developer's disk simultaneously.  It is a reconstruction, not a
    historical state.

L4  ABSENT REPOSITORIES.  Before a repository's first commit its root directory
    is materialised EMPTY (limen aborts with exit 4 on a missing root).  An
    empty root contributes no dependency facts, but K9 structure rules do fire
    ("required file missing").  Every finding carries ``repo_present`` so those
    can be excluded; the summary reports both totals.

L5  AXIS ``per-repo``.  Each repository is walked on its own commit timeline
    with the other roots empty.  Cross-repository dependencies then resolve to
    nothing and degrade into ``unresolved-dependency`` warnings, so this axis
    cannot evaluate cross-repository rules.  It exists as a robustness check:
    do the conclusions drawn on the reconstructed axis survive without the
    reconstruction?

L6  REGISTRY POLICY.  Today's spec sets ``units.registry_policy: exact`` with a
    32-entry ``declared:`` list (counted from the spec, not transcribed; the
    list is echoed into the run index as ``units_declared_today``).  That is a
    spec-integrity assertion about TODAY's module registry (validate.py S6):
    on any historical tree the
    discovered unit set differs and limen aborts with exit 3 BEFORE a single
    rule runs — no findings at all, for every commit.  The history therefore
    runs against a derived spec identical to today's except
    ``registry_policy: open``.  Nothing else is touched.  The change is inert
    with respect to findings: ``--verify-registry`` reruns today's frozen
    workspace under both policies and asserts byte-identical reports.  The
    registry delta that ``exact`` would have flagged is not discarded — it is
    recorded per commit as data (``units_declared_missing`` /
    ``units_undeclared``), which is the useful form of the same information:
    it dates the appearance of every module.

L7  SAMPLING DENSITY.  The protocol allows "every 10th commit, or all commits
    from 12 months".  LitRev history is ~4.5 months (2026-03-22..2026-08-03) and
    69 first-parent commits, so ALL of them are taken; every 10th would leave
    ~7 measurement points.  ``--every N`` is kept for larger repositories and
    defaults to 1.

VARIANTS
--------
``as-is``          today's spec verbatim.
``no-exemptions``  the same spec with every DEBT exemption stripped (entries
                   carrying reason/owner/ticket/since/expires/scope — the same
                   test ``limen spec stats`` uses to separate debt from plain
                   rule parameters) and every baseline ``expected:`` emptied.
                   This measures how long the violations that today's four
                   exemptions legalise have ACTUALLY existed in the code — the
                   exemptions themselves were authored on 2026-08-04 and have no
                   history of their own.

USAGE
-----
    export PYTHONPATH=<limen>/src
    python eval/study2_history.py \
        --live   /Users/.../Limen/live \
        --spec   /Users/.../Limen/workspace/architecture.yaml \
        --out    eval/results/study2

Every measurement point is written incrementally to
``<out>/<axis>/<variant>/<repo>/<sha>.json`` so an interrupted run loses nothing;
``--resume`` skips points already on disk.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

# Repository -> (root name in the spec, path prefix inside the repo).
# frontend-shared has no root: today's spec declares five roots and leaves
# frontend-shared unscanned (architecture.yaml header, "Scope:").  Its commits
# still define measurement points on the workspace axis.
REPOS = {
    "litrev-backend":       {"root": "host_app",      "prefix": "app"},
    "backend-shared":       {"root": "shared",        "prefix": "src"},
    "litrev-core-backend":  {"root": "core",          "prefix": "src"},
    "litrev-frontend":      {"root": "frontend",      "prefix": "src"},
    "litrev-core-frontend": {"root": "core_frontend", "prefix": "src"},
    "frontend-shared":      {"root": None,            "prefix": "src"},
}

SNAPSHOT_DIR = "litrev-arch-snapshot"   # layout today's spec expects

# tar/rglob exclusions: limen ignores these itself (extract/base.py
# EXCLUDED_DIRS); excluding them at extraction time keeps the scratch trees
# small and avoids sandbox-protected paths inside committed node_modules.
TAR_EXCLUDES = ["node_modules", "vendor", ".idea", ".vscode", "__pycache__",
                "dist", "build"]

LIMITATIONS = [
    "L1 one specification (today's) for the whole history; rules are NOT "
    "reconstructed per commit, so findings dated before the rule existed are "
    "anachronistic — they state that the commit would violate today's "
    "contract, not that it violated a contract in force at the time.",
    "L2 --today only drives exemption expiry; all debt exemptions expire "
    "2026-12-31, after the end of this window, so within this history it is a "
    "verified no-op (see --verify-today).",
    "L3 axis 'workspace' RECONSTRUCTS the workspace at time T from each "
    "repository's last commit <= T; such a combination need not have existed "
    "simultaneously.",
    "L4 before a repository's first commit its root is materialised empty; "
    "K9 structure rules still fire on it — findings carry repo_present.",
    "L5 axis 'per-repo' evaluates one repository with the others empty, so "
    "cross-repository dependencies degrade to unresolved-dependency.",
    "L6 units.registry_policy is relaxed from 'exact' to 'open' for the "
    "history run: 'exact' is an assertion about TODAY's module registry (the "
    "declared list is recorded verbatim in index.json as units_declared_today) "
    "and aborts every historical tree with exit 3 before any rule runs. The "
    "relaxation is verified inert on today's tree (--verify-registry) and the "
    "registry delta is recorded per commit instead.",
]


# --------------------------------------------------------------------------- #
# git (read-only)
# --------------------------------------------------------------------------- #

def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {out.stderr.strip()}")
    return out.stdout


def first_parent(repo: Path) -> list[dict]:
    """Oldest-first list of first-parent commits: sha, committer date, subject."""
    raw = git(repo, "log", "--first-parent", "--reverse",
              "--format=%H%x1f%cI%x1f%s")
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, iso, subject = line.split("\x1f", 2)
        rows.append({"sha": sha, "date": iso, "subject": subject})
    return rows


def materialize(live: Path, scratch: Path, repo: str, sha: str) -> Path:
    """Extract the tree at <sha> into scratch (never touches .git, never
    checks out).  Idempotent: an existing tree with a .ok marker is reused."""
    dest = scratch / "trees" / repo / sha
    marker = dest / ".limen-study2-ok"
    if marker.exists():
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    excludes = [f"--exclude={e}" for e in TAR_EXCLUDES]
    ar = subprocess.Popen(["git", "-C", str(live / repo), "archive", sha],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    tar = subprocess.Popen(["tar", "-x", "-C", str(dest), *excludes],
                           stdin=ar.stdout, stderr=subprocess.DEVNULL)
    ar.stdout.close()
    tar.communicate()
    ar.wait()
    marker.write_text(sha + "\n", encoding="utf-8")
    return dest


# --------------------------------------------------------------------------- #
# workspace assembly
# --------------------------------------------------------------------------- #

def build_workspace(ws: Path, state: dict[str, dict], live: Path,
                    scratch: Path) -> None:
    """Assemble <ws>/litrev-arch-snapshot/<repo> for the given state.

    ``state[repo]`` is either a commit row or None (repository does not exist
    yet at this time point).  Present repositories are symlinked to their
    materialised tree — the symlink sits ABOVE the root prefix, so limen's
    rglob walks real directories only.  Absent repositories get an empty
    directory tree so that discover_files() finds the root and returns [],
    instead of aborting the whole run with exit 4 (limitation L4).
    """
    if ws.exists():
        shutil.rmtree(ws)
    snap = ws / SNAPSHOT_DIR
    snap.mkdir(parents=True)
    for repo, meta in REPOS.items():
        target = snap / repo
        row = state.get(repo)
        if row is not None:
            tree = materialize(live, scratch, repo, row["sha"])
            target.symlink_to(tree)
        else:
            (target / meta["prefix"]).mkdir(parents=True)


# --------------------------------------------------------------------------- #
# spec variants
# --------------------------------------------------------------------------- #

def _is_debt(entry) -> bool:
    """Mirror of limen/cli.py::_cmd_spec_stats — a bare string in an allowlist
    is a rule parameter; an entry carrying reason/owner/ticket/since/expires/
    scope is architectural debt."""
    if not isinstance(entry, dict):
        return False
    return any(entry.get(k) for k in
               ("reason", "owner", "ticket", "since", "expires", "scope"))


def open_registry(spec_path: Path, out_path: Path) -> Path:
    """Today's spec with units.registry_policy relaxed to 'open' (L6).
    Nothing else is modified."""
    doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    doc.setdefault("units", {})["registry_policy"] = "open"
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
    return out_path


def declared_units(spec_path: Path) -> list[str]:
    doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    return list((doc.get("units") or {}).get("declared") or [])


def discovered_units(spec: Path, ws: Path) -> list[str]:
    cmd = [sys.executable, "-m", "limen.cli", "graph",
           "--spec", str(spec), "--workspace", str(ws), "--format", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return []
    return sorted(json.loads(p.stdout).get("units") or [])


def strip_exemptions(spec_path: Path, out_path: Path) -> dict:
    """Write a variant spec with debt exemptions removed.  Returns a manifest of
    what was removed, so the caller can attribute findings back to exemptions."""
    doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    removed = []
    for rule in doc.get("rules", []):
        for key in ("exempt", "exempt_files", "allow"):
            entries = rule.get(key)
            if not isinstance(entries, list):
                continue
            keep = []
            for e in entries:
                if _is_debt(e):
                    removed.append({"rule_id": rule.get("id"), "field": key,
                                    "target": e.get("target"),
                                    "since": str(e.get("since")),
                                    "expires": str(e.get("expires"))})
                else:
                    keep.append(e)
            rule[key] = keep
        if rule.get("kind") == "baseline-snapshot":
            for path, entries in (rule.get("expected") or {}).items():
                for e in entries or []:
                    if isinstance(e, dict):
                        removed.append({"rule_id": rule.get("id"),
                                        "field": "expected",
                                        "file": path,
                                        "target": e.get("target"),
                                        "since": str(e.get("since")),
                                        "expires": str(e.get("expires"))})
            rule["expected"] = {}
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
    return {"spec": str(out_path), "removed": removed}


# --------------------------------------------------------------------------- #
# limen invocation
# --------------------------------------------------------------------------- #

def run_check(spec: Path, ws: Path, today: str) -> dict:
    cmd = [sys.executable, "-m", "limen.cli", "check",
           "--spec", str(spec), "--workspace", str(ws),
           "--today", today, "--format", "json"]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.time() - t0
    if p.returncode in (3, 4):
        raise RuntimeError(f"limen exit {p.returncode}: {p.stderr.strip()[:400]}")
    return {"cmd": cmd, "exit_code": p.returncode, "seconds": dt,
            "report": json.loads(p.stdout)}


def run_extract(spec: Path, ws: Path) -> dict[str, int]:
    """Files per root actually reaching the extractors — the honest answer to
    'did the structure the spec expects exist yet at this commit?'."""
    cmd = [sys.executable, "-m", "limen.cli", "extract",
           "--spec", str(spec), "--workspace", str(ws)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return {}
    docs = json.loads(p.stdout)
    if isinstance(docs, dict):
        docs = [docs]
    out: dict[str, int] = {}
    for doc in docs:
        out[doc.get("root", "?")] = len(doc.get("files", []))
    return out


# --------------------------------------------------------------------------- #
# measurement points
# --------------------------------------------------------------------------- #

def workspace_axis_points(history: dict[str, list[dict]], every: int) -> list[dict]:
    """Union of all first-parent commits, ordered by committer date; the state
    at each point is each repository's last commit <= T (limitation L3)."""
    events = []
    for repo, rows in history.items():
        for i, row in enumerate(rows):
            events.append({"repo": repo, "index": i, **row})
    events.sort(key=lambda e: (e["date"], e["repo"], e["sha"]))
    events = events[::every] if every > 1 else events
    points = []
    for ev in events:
        t = ev["date"]
        state = {}
        for repo, rows in history.items():
            prior = [r for r in rows if r["date"] <= t]
            state[repo] = prior[-1] if prior else None
        points.append({"axis": "workspace", "repo": ev["repo"], "sha": ev["sha"],
                       "date": ev["date"], "subject": ev["subject"],
                       "state": state})
    return points


def per_repo_axis_points(history: dict[str, list[dict]], every: int) -> list[dict]:
    """Each repository on its own timeline, the other roots empty (L5)."""
    points = []
    for repo, rows in history.items():
        sel = rows[::every] if every > 1 else rows
        for row in sel:
            state = {r: None for r in REPOS}
            state[repo] = row
            points.append({"axis": "per-repo", "repo": repo, "sha": row["sha"],
                           "date": row["date"], "subject": row["subject"],
                           "state": state})
    points.sort(key=lambda p: (p["repo"], p["date"]))
    return points


def repo_of_path(path: str | None) -> str | None:
    if not path:
        return None
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == SNAPSHOT_DIR:
        return parts[1]
    return None


def measure(point: dict, spec: Path, variant: str, live: Path, scratch: Path,
            out_dir: Path, spec_stats: dict, declared: list[str]) -> dict:
    ws = scratch / "ws" / f"{point['axis']}_{variant}"
    build_workspace(ws, point["state"], live, scratch)
    today = point["date"][:10]
    files_by_root = run_extract(spec, ws)
    units = discovered_units(spec, ws)
    res = run_check(spec, ws, today)
    report = res["report"]

    present = {r: (point["state"].get(r) is not None) for r in REPOS}
    findings = []
    for f in report["findings"]:
        repo = repo_of_path(f.get("file"))
        findings.append({**f, "repo": repo,
                         "repo_present": present.get(repo, True)})

    rec = {
        "schema": "limen-study2-point/1",
        "axis": point["axis"],
        "variant": variant,
        "point_repo": point["repo"],
        "sha": point["sha"],
        "short_sha": point["sha"][:7],
        "date": point["date"],
        "today": today,
        "subject": point["subject"],
        "workspace_state": {r: (None if v is None else
                                {"sha": v["sha"], "date": v["date"],
                                 "subject": v["subject"]})
                            for r, v in point["state"].items()},
        "files_by_root": files_by_root,
        "files_total": sum(files_by_root.values()),
        "units_discovered": units,
        "units_declared_missing": sorted(set(declared) - set(units)),
        "units_undeclared": sorted(set(units) - set(declared)),
        "limen": {"cmd": res["cmd"], "exit_code": res["exit_code"],
                  "seconds": round(res["seconds"], 3),
                  "summary": report["summary"]},
        "findings": findings,
        "spec_stats": spec_stats,
        "limitations": LIMITATIONS,
    }
    dest = out_dir / point["axis"] / variant / point["repo"]
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{point['sha']}.json").write_text(
        json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return rec


def spec_stats(spec: Path, ws: Path, today: str) -> dict:
    cmd = [sys.executable, "-m", "limen.cli", "spec", "stats",
           "--spec", str(spec), "--workspace", str(ws), "--today", today]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {"cmd": cmd, "stdout": p.stdout, "exit_code": p.returncode}


def verify_today(spec: Path, ws: Path, base_today: str) -> dict:
    """Empirical proof that --today is live: run the same tree twice, once at
    the commit date and once past every exemption's expiry."""
    late = "2027-01-01"
    a = run_check(spec, ws, base_today)
    b = run_check(spec, ws, late)
    return {
        "today_commit": base_today, "today_late": late,
        "findings_at_commit_date": len(a["report"]["findings"]),
        "findings_past_expiry": len(b["report"]["findings"]),
        "delta": len(b["report"]["findings"]) - len(a["report"]["findings"]),
        "new_rule_ids_past_expiry": sorted(
            {f["rule_id"] for f in b["report"]["findings"]}
            - {f["rule_id"] for f in a["report"]["findings"]}),
    }


def verify_registry(spec_exact: Path, spec_open: Path, ws: Path,
                    today: str) -> dict:
    """Empirical proof that relaxing registry_policy to 'open' changes no
    finding: run today's frozen workspace under both and compare reports."""
    a = run_check(spec_exact, ws, today)
    b = run_check(spec_open, ws, today)
    ja = json.dumps(a["report"], sort_keys=True)
    jb = json.dumps(b["report"], sort_keys=True)
    return {"today": today,
            "exact_findings": len(a["report"]["findings"]),
            "open_findings": len(b["report"]["findings"]),
            "exact_summary": a["report"]["summary"],
            "open_summary": b["report"]["summary"],
            "reports_identical": ja == jb}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent
    ap.add_argument("--live", default="/Users/sebastianmatysik/!!!!!Limen/live")
    ap.add_argument("--spec", default="/Users/sebastianmatysik/!!!!!Limen/"
                                      "workspace/architecture.yaml")
    ap.add_argument("--scratch", default=str(root / "_scratch"))
    ap.add_argument("--out", default=str(root / "results" / "study2"))
    ap.add_argument("--every", type=int, default=1,
                    help="sample every Nth commit (default 1 = all; see L6)")
    ap.add_argument("--axis", choices=["workspace", "per-repo", "both"],
                    default="both")
    ap.add_argument("--variant", choices=["as-is", "no-exemptions", "both"],
                    default="both")
    ap.add_argument("--resume", action="store_true",
                    help="skip measurement points already written")
    ap.add_argument("--verify-today", action="store_true",
                    help="prove --today drives exemption expiry, then exit")
    ap.add_argument("--verify-registry", metavar="FROZEN_WS", default=None,
                    help="prove registry_policy:open is inert on the frozen "
                         "workspace, then exit")
    args = ap.parse_args()

    live = Path(args.live)
    spec = Path(args.spec).resolve()
    scratch = Path(args.scratch)
    out_dir = Path(args.out)
    scratch.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    history = {r: first_parent(live / r) for r in REPOS}
    n_commits = sum(len(v) for v in history.values())
    print(f"[study2] {n_commits} first-parent commits across {len(REPOS)} repos",
          flush=True)

    declared = declared_units(spec)
    # L6: history runs against today's spec with registry_policy relaxed to
    # 'open'; everything else is byte-identical.
    spec_hist = open_registry(spec, scratch / "architecture-history.yaml")

    if args.verify_registry:
        v = verify_registry(spec, spec_hist, Path(args.verify_registry),
                            "2026-08-04")
        print(json.dumps(v, indent=2))
        (out_dir / "registry_verification.json").write_text(
            json.dumps(v, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0 if v["reports_identical"] else 1

    variant_specs = {"as-is": spec_hist}
    manifest = {}
    if args.variant in ("no-exemptions", "both"):
        alt = scratch / "architecture-history-no-exemptions.yaml"
        manifest = strip_exemptions(spec_hist, alt)
        variant_specs["no-exemptions"] = alt
        print(f"[study2] variant spec: stripped {len(manifest['removed'])} "
              f"debt entries -> {alt}", flush=True)
    if args.variant == "no-exemptions":
        variant_specs.pop("as-is")

    if args.verify_today:
        pts = workspace_axis_points(history, 1)
        ws = scratch / "ws" / "verify"
        build_workspace(ws, pts[-1]["state"], live, scratch)
        v = verify_today(spec, ws, pts[-1]["date"][:10])
        print(json.dumps(v, indent=2))
        (out_dir / "today_verification.json").write_text(
            json.dumps(v, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0

    axes = []
    if args.axis in ("workspace", "both"):
        axes.append(workspace_axis_points(history, args.every))
    if args.axis in ("per-repo", "both"):
        axes.append(per_repo_axis_points(history, args.every))

    ws_stats = scratch / "ws" / "stats"
    build_workspace(ws_stats, {r: (history[r][-1] if history[r] else None)
                               for r in REPOS}, live, scratch)
    stats = {name: spec_stats(sp, ws_stats, "2026-08-04")
             for name, sp in variant_specs.items()}

    index = []
    for points in axes:
        for variant, sp in variant_specs.items():
            for i, pt in enumerate(points, 1):
                dest = (out_dir / pt["axis"] / variant / pt["repo"] /
                        f"{pt['sha']}.json")
                if args.resume and dest.exists():
                    rec = json.loads(dest.read_text(encoding="utf-8"))
                else:
                    rec = measure(pt, sp, variant, live, scratch, out_dir,
                                  stats[variant], declared)
                index.append({k: rec[k] for k in
                              ("axis", "variant", "point_repo", "sha",
                               "short_sha", "date", "files_total")}
                             | {"findings": len(rec["findings"]),
                                "errors": rec["limen"]["summary"]["errors"],
                                "warnings": rec["limen"]["summary"]["warnings"]})
                print(f"[{pt['axis']}/{variant}] {i:3d}/{len(points)} "
                      f"{pt['repo']:20s} {pt['sha'][:7]} {pt['date'][:10]} "
                      f"files={rec['files_total']:4d} "
                      f"findings={len(rec['findings']):3d}", flush=True)

    (out_dir / "index.json").write_text(
        json.dumps({"schema": "limen-study2-index/1",
                    "n_commits_first_parent": n_commits,
                    "every": args.every,
                    "spec": str(spec),
                    "spec_history": str(spec_hist),
                    "units_declared_today": declared,
                    "exemption_manifest": manifest,
                    "spec_stats": stats,
                    "limitations": LIMITATIONS,
                    "points": index}, indent=2, sort_keys=True,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[study2] wrote {len(index)} measurement points -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
