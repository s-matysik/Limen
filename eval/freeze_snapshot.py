#!/usr/bin/env python3
"""Freeze the identity of the LitRev workspace snapshot.

The LitRev repositories were delivered as GitHub zip exports, so they carry no
``.git`` directory and therefore no commit SHA. This script derives a
deterministic, content-addressed identifier for each repository instead:

    manifest = sorted [ (relative_path, sha256(file_bytes)) ]  -> eval/manifests/<repo>.tsv
    tree-id  = sha256(manifest bytes)

The tree-id is stable across machines and re-extractions of the same zip, so
every number reported in the paper can be pinned to it. If a repository *does*
contain a live ``.git`` (the user has announced they will supply live clones),
the real ``git rev-parse HEAD`` is recorded **in addition to** the tree-id, so
the two identifiers can be cross-checked.

Usage
-----
    python eval/freeze_snapshot.py                      # default workspace
    python eval/freeze_snapshot.py --workspace DIR      # explicit tree
    python eval/freeze_snapshot.py --check              # verify, do not rewrite

Outputs
-------
    eval/manifests/<repo>.tsv   per-repo `<relpath>\\t<sha256>` (LF, sorted)
    eval/frozen-commits.txt     `<repo>\\t<tree-id>\\t<n_files>\\t<snapshot|git:SHA>`
    eval/frozen-commits.json    the same, machine-readable, with metadata
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_WORKSPACE = REPO_ROOT.parent / "workspace"

#: Repositories that constitute the frozen snapshot, in report order.
REPOS = [
    "litrev-backend",
    "backend-shared",
    "litrev-core-backend",
    "litrev-frontend",
    "litrev-core-frontend",
    "frontend-shared",
]

#: Directories never part of repository identity (build output, VCS metadata,
#: dependency trees, editor state, container data volumes).
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".limen", "node_modules", "vendor", "__pycache__",
    ".venv", "venv", "dist", "build", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".next", ".turbo", "coverage",
}

#: Individual files excluded by exact name.
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db", ".limen-workspace-copied"}

#: Environment files (`.env`, `.env.local`, `.env-matysik`, …) are per-developer
#: secrets, not source: they are never read by an extractor, they differ between
#: machines, and some are unreadable under hardened sandboxes. Excluding them by
#: rule keeps the tree-id reproducible wherever the workspace is rebuilt.
def is_env_file(name: str) -> bool:
    return name == ".env" or name.startswith((".env.", ".env-"))

#: Binary / non-source suffixes: excluded so the identifier covers source only.
EXCLUDED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".ogg",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class",
    ".pyc", ".pyo", ".jar", ".war", ".sqlite", ".db", ".ibd", ".frm",
    ".pack", ".idx", ".map",
}


def is_source_file(path: Path, rel: str) -> bool:
    """True when the file counts towards repository identity."""
    parts = Path(rel).parts
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return False
    if path.name in EXCLUDED_NAMES or is_env_file(path.name):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(repo_dir: Path) -> tuple[str, int, list[str]]:
    """Return (manifest_text, n_files, unreadable_paths).

    Symlinks are not followed: the workspace deliberately contains
    ``shared/`` -> ``../backend-shared`` style links that reproduce the
    production layout, and following them would double-count files.
    """
    rows: list[tuple[str, str]] = []
    unreadable: list[str] = []
    for path in sorted(repo_dir.rglob("*")):
        rel = path.relative_to(repo_dir).as_posix()
        # name/suffix filtering first: excluded paths must not need a stat(),
        # so a file the OS refuses to even stat (sandbox-protected .env) is
        # skipped by rule rather than crashing the walk.
        if not is_source_file(path, rel):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError as exc:
            unreadable.append(f"{rel}: {exc.strerror}")
            continue
        try:
            rows.append((rel, sha256_file(path)))
        except OSError as exc:  # sandbox-protected secrets, permission errors
            unreadable.append(f"{rel}: {exc.strerror}")
    rows.sort()
    text = "".join(f"{rel}\t{digest}\n" for rel, digest in rows)
    return text, len(rows), unreadable


def git_head(repo_dir: Path) -> str | None:
    """Real commit SHA when the directory is a live git work tree."""
    if not (repo_dir / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def freeze(workspace: Path, out_dir: Path, check_only: bool = False) -> dict:
    manifests_dir = out_dir / "manifests"
    if not check_only:
        manifests_dir.mkdir(parents=True, exist_ok=True)

    entries, drift = [], []
    for repo in REPOS:
        repo_dir = workspace / repo
        if not repo_dir.is_dir():
            raise SystemExit(f"missing repository in workspace: {repo_dir}")
        text, n_files, unreadable = build_manifest(repo_dir)
        tree_id = hashlib.sha256(text.encode("utf-8")).hexdigest()

        manifest_path = manifests_dir / f"{repo}.tsv"
        if check_only:
            previous = manifest_path.read_text() if manifest_path.exists() else None
            if previous != text:
                drift.append(repo)
        else:
            manifest_path.write_text(text)

        sha = git_head(repo_dir)
        entries.append({
            "repo": repo,
            "tree_id": tree_id,
            "files": n_files,
            "source": f"git:{sha}" if sha else "snapshot",
            "git_sha": sha,
            "manifest": f"manifests/{repo}.tsv",
            "unreadable": unreadable,
        })

    total_files = sum(e["files"] for e in entries)
    workspace_id = hashlib.sha256(
        "".join(f"{e['repo']}\t{e['tree_id']}\n" for e in entries).encode()
    ).hexdigest()

    doc = {
        "schema": "limen-frozen-snapshot/1",
        "workspace": str(workspace),
        "workspace_tree_id": workspace_id,
        "total_files": total_files,
        "repos": entries,
    }

    if not check_only:
        lines = [
            "# limen frozen snapshot — identity of the LitRev workspace",
            "# columns: repo<TAB>tree-id<TAB>files<TAB>origin",
            "# tree-id = sha256 of eval/manifests/<repo>.tsv, itself the sorted",
            "#           list of (relative path, sha256(bytes)) over source files.",
            "# origin  = 'snapshot' (zip export, no .git) or 'git:<SHA>' (live repo).",
            "# regenerate: python eval/freeze_snapshot.py",
            f"# workspace: {workspace}",
        ]
        for e in entries:
            lines.append(f"{e['repo']}\t{e['tree_id']}\t{e['files']}\t{e['source']}")
        lines.append(f"WORKSPACE\t{workspace_id}\t{total_files}\tsnapshot")
        (out_dir / "frozen-commits.txt").write_text("\n".join(lines) + "\n")
        (out_dir / "frozen-commits.json").write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n")

    doc["drift"] = drift
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE,
                    help=f"workspace root (default: {DEFAULT_WORKSPACE})")
    ap.add_argument("--out", type=Path, default=HERE,
                    help="output directory (default: eval/)")
    ap.add_argument("--check", action="store_true",
                    help="verify manifests match the tree; exit 1 on drift")
    args = ap.parse_args(argv)

    doc = freeze(args.workspace.resolve(), args.out.resolve(), check_only=args.check)

    for e in doc["repos"]:
        print(f"{e['repo']:<22} {e['tree_id']}  {e['files']:>5} files  {e['source']}")
        for u in e["unreadable"]:
            print(f"  ! unreadable: {u}", file=sys.stderr)
    print(f"{'WORKSPACE':<22} {doc['workspace_tree_id']}  "
          f"{doc['total_files']:>5} files  snapshot")

    if args.check and doc["drift"]:
        print("drift in: " + ", ".join(doc["drift"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
