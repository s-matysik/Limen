#!/usr/bin/env python3
"""Study 1, step 5 — run `limen check` on the frozen workspace and normalise
its findings into the shape the legacy oracle emits, so the two sets can be
diffed on a common key.

The comparison key is ``(file, line, target)``. Neither side produces it
natively:

* limen (schema ``finding-1``) reports ``file`` as a **spec-root path**, i.e.
  prefixed with ``litrev-arch-snapshot/`` because that is how
  ``workspace/architecture.yaml`` addresses the six repositories;
* the legacy PHP checkers report paths relative to ``base_path()`` (the host
  backend repo) or, in ``SharedBoundaryRulesTest``, relative to
  ``shared/src``; the legacy JS auditor reports paths relative to the frontend
  repo root.

Both are therefore rewritten to one canonical form — **workspace-relative**,
``<repo>/<path inside repo>`` — by the ordered rewrite table
``PATH_REWRITES`` below.  The table is data, not prose: `study1_equivalence.py`
imports the same function and applies it to the oracle's findings, and
``eval/tests/test_study1_equivalence.py`` asserts its behaviour, so a reviewer
can check the normalisation without trusting this docstring.

Field mapping limen → comparison key (verified empirically against
``schemas/finding-1.json`` and against the JSON this very script captures; the
mapping is echoed into the output under ``field_mapping``):

    key part   limen finding-1 field   note
    --------   ---------------------   -------------------------------------
    file       ``file``                str|null, spec-root path → canonical
    line       ``line``                int|null, 1-based, kept verbatim
    target     ``target``              str|null, PHP FQCN (single backslash in
                                       JSON) or JS import specifier

Usage:

    export PYTHONPATH=<LIMEN>/limen/src
    python eval/run_limen_check.py \
        --spec     <LIMEN>/workspace/architecture.yaml \
        --workspace <LIMEN>/workspace \
        --today    2026-08-04 \
        --out      eval/results/limen_findings.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "limen-findings/1"

# --------------------------------------------------------------------------
# Path normalisation
# --------------------------------------------------------------------------
# Ordered (prefix, replacement) rewrites applied to a reported file path until
# one matches.  Every entry states which side of the comparison emits that
# shape.  The result is always workspace-relative: "<repo>/<path in repo>".
#
# The rewrite is *idempotent*: a path already in canonical form matches none of
# the prefix rules and is returned unchanged (guarded by KNOWN_REPOS).
PATH_REWRITES: tuple[tuple[str, str], ...] = (
    # limen: spec roots address repositories through the snapshot symlink farm
    ("litrev-arch-snapshot/", ""),
    # legacy SharedBoundaryRulesTest: str_replace($this->sharedSrc . '/', '')
    # leaves paths relative to backend-shared/src
    ("__shared_src__/", "backend-shared/src/"),
)

# Repository directory names in the frozen workspace (eval/frozen-commits.txt).
KNOWN_REPOS: frozenset[str] = frozenset({
    "litrev-backend",
    "backend-shared",
    "litrev-core-backend",
    "litrev-frontend",
    "litrev-core-frontend",
    "frontend-shared",
})

# Legacy checkers that report paths relative to a repository root rather than
# to the workspace.  Used by study1_equivalence.py when the oracle emits a
# bare repo-relative path (e.g. "app/Modules/X.php" from base_path()).
LEGACY_BASE_REPO: dict[str, str] = {
    # base_path() in litrev-backend/tests/Architecture/*.php
    "app/": "litrev-backend",
    "config/": "litrev-backend",
    "tests/": "litrev-backend",
    "database/": "litrev-backend",
    # audit-frontend-architecture.mjs runs with cwd = litrev-frontend
    "src/": "litrev-frontend",
    "scripts/": "litrev-frontend",
}


def canonical_file(path: str | None) -> str | None:
    """Rewrite a reported file path to workspace-relative canonical form.

    Returns None unchanged (finding-1 allows a null file).
    """
    if path is None:
        return None
    p = path.replace(os.sep, "/").strip()
    if p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    for prefix, replacement in PATH_REWRITES:
        if p.startswith(prefix):
            p = replacement + p[len(prefix):]
            break
    head = p.split("/", 1)[0]
    if head in KNOWN_REPOS:
        return p
    for prefix, repo in LEGACY_BASE_REPO.items():
        if p.startswith(prefix):
            return f"{repo}/{p}"
    # Unrecognised shape: return as-is so the diff surfaces it rather than
    # silently guessing a repository.
    return p


def canonical_target(target: str | None) -> str | None:
    """Normalise a dependency target.

    PHP fully-qualified class names are compared without a leading separator
    (``\\App\\X`` and ``App\\X`` denote the same class); JS specifiers are
    compared verbatim apart from surrounding whitespace.
    """
    if target is None:
        return None
    t = target.strip()
    if t.startswith("\\"):
        t = t.lstrip("\\")
    return t


def finding_key(finding: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """The study's comparison key: (file, line, target), canonicalised."""
    line = finding.get("line")
    return (
        canonical_file(finding.get("file")),
        int(line) if isinstance(line, int) else None,
        canonical_target(finding.get("target")),
    )


def key_str(key: tuple[str | None, int | None, str | None]) -> str:
    """Stable string form of a key, for JSON dict indices and messages."""
    f, ln, t = key
    return f"{f or '-'}:{ln if ln is not None else '-'}:{t or '-'}"


# --------------------------------------------------------------------------
# Snapshot identity
# --------------------------------------------------------------------------
def read_snapshot(frozen: Path) -> dict[str, Any]:
    """Parse eval/frozen-commits.txt into the snapshot block both sides carry."""
    repos: dict[str, dict[str, Any]] = {}
    workspace_id = None
    for raw in frozen.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        name, tree_id, files, origin = parts[0], parts[1], parts[2], parts[3]
        if name == "WORKSPACE":
            workspace_id = tree_id
            repos_total_files = int(files)
        else:
            repos[name] = {"tree_id": tree_id, "files": int(files), "origin": origin}
    return {
        "workspace_id": workspace_id,
        "manifest_files": repos_total_files,
        "repos": repos,
        "frozen_commits": str(frozen),
    }


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True,
                    help="ABSOLUTE path to the spec; relative paths are "
                         "resolved against --workspace by the CLI")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--today", default="2026-08-04",
                    help="freeze the clock for exemption expiry")
    ap.add_argument("--frozen", default=None,
                    help="eval/frozen-commits.txt (default: next to this script)")
    ap.add_argument("--out", default="eval/results/limen_findings.json")
    args = ap.parse_args()

    spec = Path(args.spec).resolve()
    workspace = Path(args.workspace).resolve()
    frozen = Path(args.frozen) if args.frozen else Path(__file__).with_name("frozen-commits.txt")

    cmd = [
        sys.executable, "-m", "limen.cli", "check",
        "--spec", str(spec),
        "--workspace", str(workspace),
        "--today", args.today,
        "--format", "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1, 2):
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"limen check failed with exit {proc.returncode}")
    report = json.loads(proc.stdout)

    raw_findings = report.get("findings", [])
    findings: list[dict[str, Any]] = []
    for f in raw_findings:
        key = finding_key(f)
        findings.append({
            "source": "limen",
            "rule_id": f.get("rule_id"),
            "kind": f.get("kind"),
            "severity": f.get("severity"),
            "file": key[0],
            "line": key[1],
            "target": key[2],
            "unit": f.get("unit"),
            "message": f.get("message"),
            "remedy": f.get("remedy"),
            "exemptible": f.get("exemptible"),
            "spec_path": f.get("spec_path"),
            "path_trace": f.get("path_trace"),
            "key": key_str(key),
            "raw": f,
        })
    findings.sort(key=lambda f: (f["key"], f["rule_id"] or ""))

    payload = {
        "schema": SCHEMA,
        "tool": "limen",
        "tool_version": report.get("version"),
        "report_schema": report.get("schema"),
        "project": report.get("project"),
        "snapshot": read_snapshot(frozen),
        "command": " ".join(["python", "-m", "limen.cli", "check",
                             "--spec", str(spec), "--workspace", str(workspace),
                             "--today", args.today, "--format", "json"]),
        "exit_code": proc.returncode,
        "summary": report.get("summary"),
        "field_mapping": {
            "note": "limen finding-1 field -> study comparison key part",
            "file": "finding-1.file (spec-root path) -> canonical_file()",
            "line": "finding-1.line (1-based int|null) -> verbatim",
            "target": "finding-1.target -> canonical_target()",
            "path_rewrites": [list(pair) for pair in PATH_REWRITES],
            "known_repos": sorted(KNOWN_REPOS),
        },
        "counts": {
            "findings": len(findings),
            "by_severity": _tally(findings, "severity"),
            "by_rule_id": _tally(findings, "rule_id"),
            "by_kind": _tally(findings, "kind"),
        },
        "findings": findings,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{out}: {len(findings)} findings, exit_code={proc.returncode}")
    for k, v in sorted(payload["counts"]["by_rule_id"].items()):
        print(f"  {k}: {v}")
    return 0


def _tally(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(field))] = out.get(str(r.get(field)), 0) + 1
    return dict(sorted(out.items()))


if __name__ == "__main__":
    raise SystemExit(main())
