#!/usr/bin/env python3
"""Prove the PHPUnit/Laravel shim is faithful, by running the real thing beside it.

    python eval/validate_shim_against_laravel.py --laravel-root /tmp/laravel_check

The shim in eval/legacy/php/ exists because the frozen snapshot has no vendor/
directory. Its whole claim is that it changes the *plumbing* (PHPUnit's runner,
Laravel's container) and not the *rules*. This script tests that claim head to
head: on the same tree, real `php artisan test --testsuite=Architecture` and the
shim must agree on

  * which test methods exist and run,
  * how many assertions they make,
  * exactly which of them pass and which fail.

It does so on the clean tree AND on one mutated tree per injectable backend
rule, because agreement on an all-green run is agreement about nothing. On the
mutants, the real runner's failure set (parsed from its output) must equal the
shim's failure set — same class, same method, same dataset.

--laravel-root must point at a tree with vendor/ installed: a physical copy of
litrev-backend with `shared` and `core-backend` symlinks resolved and
`composer install` completed (requires repo.packagist.org).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
PHP_DIR = EVAL_DIR / "legacy" / "php"

sys.path.insert(0, str(EVAL_DIR))
from mutation_sensitivity import catalogue  # noqa: E402

# PHPUnit's JUnit testcase name: "<method>" or '<method> with data set "<name>"'.
# JUnit XML is used rather than `artisan test`'s console output because the
# console renderer truncates long test names with an ellipsis, which would make
# the comparison keys ambiguous.
_DATASET = re.compile(r'^(.*?) with data set "(.+)"$')


def run_real(root: Path) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
        junit = Path(handle.name)

    try:
        proc = subprocess.run(
            ["vendor/bin/phpunit", "--testsuite", "Architecture", "--log-junit", str(junit)],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        tree = ET.parse(junit)
    finally:
        junit.unlink(missing_ok=True)

    outcomes = []
    total_assertions = 0

    for case in tree.getroot().iter("testcase"):
        name = case.get("name", "")
        dataset = None
        match = _DATASET.match(name)
        if match:
            name, dataset = match.group(1), match.group(2)

        failures = [child.text or "" for child in case if child.tag in ("failure", "error")]
        total_assertions += int(case.get("assertions", "0"))

        outcomes.append(
            {
                "class": case.get("class"),
                "method": name,
                "dataset": dataset,
                "assertions": int(case.get("assertions", "0")),
                "passed": not failures,
                "failure_text": failures[0][:2000] if failures else None,
            }
        )

    return {
        "exit_code": proc.returncode,
        "outcomes": outcomes,
        "assertions": total_assertions,
        "tests": len(outcomes),
        "tail": (proc.stdout + proc.stderr)[-2000:],
    }


def run_shim(root: Path) -> dict:
    import os

    proc = subprocess.run(
        ["php", str(PHP_DIR / "run_architecture_tests.php")],
        capture_output=True,
        text=True,
        env={**os.environ, "LIMEN_BACKEND_ROOT": str(root)},
        check=True,
    )
    raw = json.loads(proc.stdout)

    outcomes = []
    for execution in raw["executions"]:
        outcomes.append(
            {
                "class": execution["class"],
                "method": execution["method"],
                "dataset": execution["dataset"],
                "assertions": execution["assertions"],
                "passed": execution["status"] == "ran" and execution["failed_assertions"] == 0,
                "failure_text": None,
            }
        )

    return {
        "outcomes": outcomes,
        "assertions": sum(e["assertions"] for e in raw["executions"]),
        "tests": len(outcomes),
        "executions": raw["executions"],
        "errors": raw["errors"],
    }


def key(outcome: dict) -> tuple:
    return (outcome["class"], outcome["method"], outcome["dataset"])


def compare(real: dict, shim: dict) -> dict:
    real_map = {key(o): o["passed"] for o in real["outcomes"]}
    shim_map = {key(o): o["passed"] for o in shim["outcomes"]}

    only_real = sorted(str(k) for k in real_map.keys() - shim_map.keys())
    only_shim = sorted(str(k) for k in shim_map.keys() - real_map.keys())
    disagree = [
        {"test": str(k), "real_passed": real_map[k], "shim_passed": shim_map[k]}
        for k in sorted(real_map.keys() & shim_map.keys(), key=str)
        if real_map[k] != shim_map[k]
    ]

    real_failures = sorted(str(k) for k, ok in real_map.items() if not ok)
    shim_failures = sorted(str(k) for k, ok in shim_map.items() if not ok)

    # Real PHPUnit aborts a test method at its FIRST failing assertion; the
    # shim records the failure and continues (that is the one deliberate
    # deviation, and the reason the oracle can enumerate every violation rather
    # than only the first). Assertion counts therefore coincide only when every
    # method passes. On a mutant, the shim's count is an upper bound: >= real.
    all_pass = not real_failures and not shim_failures
    assertions_match = real["assertions"] == shim["assertions"]
    assertions_consistent = assertions_match if all_pass else shim["assertions"] >= real["assertions"]

    return {
        "tests_real": len(real_map),
        "tests_shim": len(shim_map),
        "test_sets_equal": not only_real and not only_shim,
        "assertions_real": real["assertions"],
        "assertions_shim": shim["assertions"],
        "assertions_match": assertions_match,
        "assertions_consistent": assertions_consistent,
        "all_pass": all_pass,
        "only_in_real": only_real,
        "only_in_shim": only_shim,
        "disagreements": disagree,
        "real_failures": real_failures,
        "shim_failures": shim_failures,
        "failure_sets_equal": real_failures == shim_failures,
        "agree": (
            not only_real
            and not only_shim
            and not disagree
            and real_failures == shim_failures
            and assertions_consistent
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--laravel-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "results" / "oracle_validation.json")
    parser.add_argument("--skip-mutants", action="store_true")
    args = parser.parse_args()

    root = args.laravel_root.resolve()
    if not (root / "vendor" / "autoload.php").is_file():
        raise SystemExit(f"{root} has no vendor/: run composer install there first")

    report = {
        "clean": None,
        "mutants": [],
    }

    real = run_real(root)
    shim = run_shim(root)
    report["clean"] = {"comparison": compare(real, shim), "real_tests": real["tests"], "real_assertions": real["assertions"]}

    if not args.skip_mutants:
        # The mutation catalogue addresses paths as "<repo>/<path>"; the Laravel
        # tree has the backend at its root with shared/ as a symlink, so the
        # prefixes are rewritten and shared mutations are applied through the
        # symlink into a private copy.
        tmp = Path(tempfile.mkdtemp(prefix="limen-shimval-"))
        try:
            for mutation in catalogue():
                if mutation.side != "backend":
                    continue

                work = tmp / mutation.id
                _clone_laravel_tree(root, work)

                try:
                    touched = mutation.apply(_PrefixAdapter(work))
                except Exception as exc:  # noqa: BLE001
                    report["mutants"].append(
                        {"mutation_id": mutation.id, "status": "apply-failed", "error": str(exc)}
                    )
                    shutil.rmtree(work, ignore_errors=True)
                    continue

                m_real = run_real(work / "litrev-backend")
                m_shim = run_shim(work / "litrev-backend")
                comparison = compare(m_real, m_shim)

                report["mutants"].append(
                    {
                        "mutation_id": mutation.id,
                        "inventory_id": mutation.inventory_id,
                        "touched_files": touched,
                        "status": "ok",
                        "real_tests": m_real["tests"],
                        "real_assertions": m_real["assertions"],
                        "comparison": comparison,
                    }
                )
                shutil.rmtree(work, ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    mutants = [m for m in report["mutants"] if m["status"] == "ok"]
    summary = {
        "clean_agrees": report["clean"]["comparison"]["agree"],
        "mutants_compared": len(mutants),
        "mutants_agreeing": sum(1 for m in mutants if m["comparison"]["agree"]),
        "mutants_failure_sets_equal": sum(1 for m in mutants if m["comparison"]["failure_sets_equal"]),
        "mutants_disagreeing": [m["mutation_id"] for m in mutants if not m["comparison"]["agree"]],
    }

    doc = {
        "schema": "oracle-validation/1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "laravel_validation": {
            "claim": "The shim reproduces real PHPUnit + Laravel: same test methods, same assertion "
            "count, same pass/fail verdict — on the clean tree and on every injectable backend mutant.",
            "laravel_root": str(root),
            "summary": summary,
            "detail": report,
        },
    }

    existing = {}
    if args.out.is_file():
        existing = json.loads(args.out.read_text())
    existing.update(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")

    print(json.dumps({"out": str(args.out), **summary}, indent=2))
    return 0


class _PrefixAdapter:
    """Lets the mutation catalogue (which addresses `<repo>/<path>`) write into
    the Laravel tree, where the repositories sit side by side."""

    def __init__(self, base: Path):
        self.base = base

    def __truediv__(self, rel: str) -> Path:
        return self.base / rel


def _clone_laravel_tree(root: Path, dest: Path) -> None:
    """Copy the installed Laravel tree, vendor/ included.

    vendor/ must be a real directory, not a symlink to the pristine tree:
    Composer's generated autoloader derives $baseDir from the RESOLVED vendor
    path, so a shared symlink makes it load Tests\\TestCase from the original
    tree, whose __DIR__ then makes base_path() point back at the unmutated
    copy — and every mutant would silently pass. `cp -Rc` requests APFS
    clones so the ~160 MB copy is cheap; it degrades to a plain copy
    elsewhere.
    """
    dest.mkdir(parents=True)
    for name in ("backend-shared", "litrev-core-backend"):
        shutil.copytree(root.parent / name, dest / name, symlinks=True)

    backend = dest / "litrev-backend"
    flags = "-Rc" if sys.platform == "darwin" else "-R"
    subprocess.run(["cp", flags, str(root), str(backend)], check=True)

    # the two path-repository links must point inside the clone, not outside it
    for link_name, target in (("shared", "../backend-shared"), ("core-backend", "../litrev-core-backend")):
        link = backend / link_name
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            shutil.rmtree(link)
        link.symlink_to(target)


if __name__ == "__main__":
    sys.exit(main())
