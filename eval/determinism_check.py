#!/usr/bin/env python3
"""Byte-level determinism of `limen extract` and `limen check` on a workspace.

Every claim in the paper about limen being a *deterministic* gate reduces to
this: the same specification over the same tree produces byte-identical output,
run after run, regardless of filesystem enumeration order or cache state.

The check runs each command twice — once with a cold ``.limen`` cache and once
warm — and compares sha256 of stdout. It also runs the extractor a third time
in a different process-hash seed (PYTHONHASHSEED) to catch any accidental
dependence on set/dict iteration order.

Reported alongside: the number of source files and the number of dependency
edges the extractor produced, which are the population figures cited for the
LitRev snapshot.

Usage
-----
    python eval/determinism_check.py
    python eval/determinism_check.py --workspace DIR --spec FILE

Outputs
-------
    eval/determinism.json   full result, machine-readable
    exit 0 when every pair of runs is byte-identical, 1 otherwise
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_WORKSPACE = REPO_ROOT.parent / "workspace"
DEFAULT_SPEC = REPO_ROOT / "examples" / "litrev" / "architecture.yaml"
TODAY = "2026-08-04"


def run_limen(args: list[str], env_extra: dict | None = None) -> tuple[int, bytes]:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, "-m", "limen.cli", *args],
                          capture_output=True, env=env)
    if proc.returncode not in (0, 1, 2):
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"limen {' '.join(args)} failed ({proc.returncode})")
    return proc.returncode, proc.stdout


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def drop_caches(workspace: Path) -> None:
    """Remove `.limen/cache` directories so the next run is cold."""
    for cache in workspace.glob("*/.limen"):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache)
    root_cache = workspace / ".limen"
    if root_cache.is_dir() and not root_cache.is_symlink():
        shutil.rmtree(root_cache)


def facts_population(stdout: bytes) -> dict:
    """File and dependency counts from an arch-facts document."""
    doc = json.loads(stdout)
    docs = doc if isinstance(doc, list) else doc.get("documents", [doc])
    per_root = {
        d["root"]: {
            "files": len(d["files"]),
            "deps": sum(len(f["deps"]) for f in d["files"]),
            "unresolved": len(d.get("unresolved", [])),
        }
        for d in docs
    }
    return {
        "per_root": dict(sorted(per_root.items())),
        "files": sum(v["files"] for v in per_root.values()),
        "deps": sum(v["deps"] for v in per_root.values()),
        "unresolved": sum(v["unresolved"] for v in per_root.values()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--out", type=Path, default=HERE / "determinism.json")
    args = ap.parse_args(argv)

    ws = args.workspace.resolve()
    spec = args.spec.resolve()
    base = ["--spec", str(spec), "--workspace", str(ws), "--today", TODAY]

    commands = {
        "extract": ["extract", *base],
        "check-json": ["check", *base, "--format", "json"],
    }

    results, ok = {}, True
    population = None
    for name, argv_ in commands.items():
        drop_caches(ws)
        code_cold, out_cold = run_limen(argv_)          # cold cache
        code_warm, out_warm = run_limen(argv_)          # warm cache
        code_seed, out_seed = run_limen(argv_, {"PYTHONHASHSEED": "12345"})

        digests = [sha256_bytes(out_cold), sha256_bytes(out_warm),
                   sha256_bytes(out_seed)]
        identical = len(set(digests)) == 1 and len({code_cold, code_warm,
                                                    code_seed}) == 1
        ok &= identical
        results[name] = {
            "argv": argv_,
            "exit_codes": [code_cold, code_warm, code_seed],
            "sha256": {"cold": digests[0], "warm": digests[1],
                       "hashseed": digests[2]},
            "bytes": len(out_cold),
            "identical": identical,
        }
        if name == "extract":
            population = facts_population(out_cold)
        if name == "check-json":
            report = json.loads(out_cold)
            results[name]["summary"] = report["summary"]
            results[name]["findings"] = len(report["findings"])
            rule_ids = sorted({f["rule_id"] for f in report["findings"]})
            results[name]["rule_ids"] = rule_ids

    doc = {
        "schema": "limen-determinism/1",
        "workspace": str(ws),
        "spec": str(spec),
        "today": TODAY,
        "deterministic": ok,
        "population": population,
        "runs": results,
    }
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")

    for name, r in results.items():
        mark = "OK " if r["identical"] else "DIFF"
        print(f"{mark} {name:<11} sha256={r['sha256']['cold'][:16]}… "
              f"({r['bytes']} bytes, exit {r['exit_codes'][0]})")
    print(f"files={population['files']}  deps={population['deps']}  "
          f"unresolved={population['unresolved']}")
    for root, v in population["per_root"].items():
        print(f"  {root:<15} {v['files']:>4} files  {v['deps']:>5} deps")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
