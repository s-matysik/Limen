#!/usr/bin/env python3
"""Verify the legacy oracle's recovered line numbers against limen's own.

    python eval/check_line_recovery.py

The legacy PHP checkers report (file, import) and never a line, so the oracle
recovers the line by re-running the reporting rule's own PCRE pattern
(`eval/legacy/php/resolve_lines.php`). That recovery is the one part of the
legacy side that is not simply "run the legacy code", so it needs its own
evidence.

The check: plant a batch of mutations that both tools detect, run both tools on
the SAME mutated tree, and compare the (file, line) coordinates they report.
Where both tools see the same violation, they must land on the same line.

This is not a full equivalence diff — that is the other track's job, and it
keys on (file, line, target) with rule mapping. This script asks the narrower
question that validates the recovery mechanism itself.

Output: eval/results/line_recovery_check.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
LIMEN_DIR = EVAL_DIR.parent
DEFAULT_WORKSPACE = LIMEN_DIR.parent / "workspace"

sys.path.insert(0, str(EVAL_DIR))
from mutation_sensitivity import catalogue, copy_workspace, run_limen  # noqa: E402

# Mutations whose violation BOTH tools detect and locate at a source line.
# Deliberately excludes: the six documented not-encoded rules (limen does not
# fire at all), and the structural rules where there is no line to compare
# (missing entrypoint, missing README, module docker dir, shared Http layer).
COMPARABLE = [
    "M-BE-02-admin-import",
    "M-BE-03-foreign-application",
    "M-BE-08-application-into-infrastructure",
    "M-BE-10-support-module-import",
    "M-BE-14-model-consumer",
    "M-BE-15-forbidden-model",
    "M-BE-16-notifications-domain-baseline",
    "M-BE-17-notifications-mail-baseline",
    "M-BE-18-module-application-allowlist",
    "M-BE-20-shared-host-import",
    "M-BE-21-shared-cross-module-concrete",
    "M-FE-01-raw-success-data",
    "M-FE-02-local-message-fallback",
    "M-FE-03-console-log",
    "M-FE-05-cross-module-deep-import",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "results" / "line_recovery_check.json")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="limen-linecheck-"))
    try:
        # Baseline: limen reports 3 host-cross-module-public-surface errors on
        # the clean snapshot, in files this batch also mutates. Inserting `use`
        # lines shifts those pre-existing findings down, so they must be
        # excluded by RULE+FILE rather than by coordinate.
        baseline_ws = tmp / "baseline"
        copy_workspace(args.workspace.resolve(), baseline_ws)
        baseline_limen = run_limen(baseline_ws)["findings"]
        preexisting_files = {
            f["file"] for f in baseline_limen if f["rule_id"] != "unresolved-dependency"
        }
        shutil.rmtree(baseline_ws, ignore_errors=True)

        mutated = tmp / "ws"
        copy_workspace(args.workspace.resolve(), mutated)

        wanted = set(COMPARABLE)
        applied = []
        for mutation in catalogue():
            if mutation.id in wanted:
                mutation.apply(mutated)
                applied.append(mutation.id)

        legacy_out = tmp / "legacy.json"
        subprocess.run(
            [sys.executable, str(EVAL_DIR / "run_legacy_oracle.py"), "--workspace", str(mutated), "--out", str(legacy_out), "--quiet"],
            check=True,
            capture_output=True,
            text=True,
        )
        legacy = json.loads(legacy_out.read_text())["findings"]
        limen = run_limen(mutated)["findings"]

        # Compare on (file, line). Limen's unresolved-dependency findings have no
        # legacy counterpart (the legacy tools do not model unresolved imports),
        # and file-level metric findings carry line: null on the limen side.
        legacy_keys = {(f["file"], f["line"]) for f in legacy if f["line"] is not None}
        limen_keys = {
            (f["file"], f["line"])
            for f in limen
            if f["rule_id"] != "unresolved-dependency" and f["line"] is not None
        }

        shared_files = ({k[0] for k in legacy_keys} & {k[0] for k in limen_keys}) - preexisting_files
        legacy_in_shared = {k for k in legacy_keys if k[0] in shared_files}
        limen_in_shared = {k for k in limen_keys if k[0] in shared_files}

        doc = {
            "schema": "line-recovery-check/1",
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "question": "Where both tools report a violation in the same file, do the recovered "
            "legacy line and limen's line coincide?",
            "mutations_applied": sorted(applied),
            "excluded_files_with_preexisting_limen_findings": sorted(preexisting_files),
            "counts": {
                "legacy_located_findings": len(legacy_keys),
                "limen_located_findings": len(limen_keys),
                "files_seen_by_both": len(shared_files),
                "coordinates_agreeing": len(legacy_in_shared & limen_in_shared),
                "legacy_only_in_shared_files": len(legacy_in_shared - limen_in_shared),
                "limen_only_in_shared_files": len(limen_in_shared - legacy_in_shared),
            },
            "agreeing": sorted(f"{f}:{n}" for f, n in legacy_in_shared & limen_in_shared),
            "legacy_only": sorted(f"{f}:{n}" for f, n in legacy_in_shared - limen_in_shared),
            "limen_only": sorted(f"{f}:{n}" for f, n in limen_in_shared - legacy_in_shared),
            "all_agree": legacy_in_shared == limen_in_shared,
        }

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({"out": str(args.out), **doc["counts"], "all_agree": doc["all_agree"]}, indent=2))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
