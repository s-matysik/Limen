#!/usr/bin/env python3
"""Count the legacy architecture-checker sources LitRev shipped before limen.

The research plan quotes "1251 LOC" for the legacy checkers. This script
recomputes that number from the frozen snapshot so the paper never carries a
hand-copied figure, and reports three counts per file, because "LOC" is
ambiguous and the choice of definition moves the total by ~30%:

  total    — every physical line in the file (``wc -l`` semantics)
  code     — lines that are neither blank nor comment-only
  sloc_nb  — lines that are not blank (comments included)

Comment syntax is per-language: PHP recognises ``//``, ``#``, ``/* */`` and the
``|``-prefixed continuation lines Laravel config files use inside block
comments; JavaScript recognises ``//`` and ``/* */``.

Usage:
    python eval/count_legacy_loc.py --litrev /path/to/LitRev \
        --out eval/legacy_loc.json --csv eval/legacy_loc.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

# (source-group, repo-relative path or directory, language)
SOURCES = [
    ("backend_config", "litrev-backend-main/config/architecture.php", "php"),
    ("backend_shared_config", "backend-shared-main/config/architecture.php", "php"),
    ("backend_tests", "litrev-backend-main/tests/Architecture", "php"),
    ("frontend_audit",
     "litrev-frontend-main/scripts/audit-frontend-architecture.mjs", "js"),
]

BLOCK = {"php": (r"/\*", r"\*/"), "js": (r"/\*", r"\*/")}
LINE = {"php": (r"//", r"#"), "js": (r"//",)}


def classify(text: str, language: str) -> tuple[int, int, int]:
    """Return (total, code, non_blank) line counts for one source file."""
    lines = text.splitlines()
    total = len(lines)
    open_rx, close_rx = BLOCK[language]
    line_prefixes = LINE[language]
    in_block = False
    code = 0
    non_blank = 0
    for raw in lines:
        stripped = raw.strip()
        if stripped:
            non_blank += 1
        if not stripped:
            continue
        if in_block:
            # Laravel config files continue block comments with a leading '|'
            if re.search(close_rx, stripped):
                in_block = False
                after = re.split(close_rx, stripped, maxsplit=1)[1].strip()
                if after:
                    code += 1
            continue
        if re.match(open_rx, stripped):
            if not re.search(close_rx, stripped):
                in_block = True
                continue
            after = re.split(close_rx, stripped, maxsplit=1)[1].strip()
            if after:
                code += 1
            continue
        if any(stripped.startswith(p) for p in line_prefixes):
            continue
        code += 1
    return total, code, non_blank


def iter_files(base: Path, rel: str, language: str):
    target = base / rel
    if target.is_file():
        yield target
        return
    if not target.is_dir():
        raise SystemExit(f"missing legacy source: {target}")
    ext = ".php" if language == "php" else ".mjs"
    for p in sorted(target.rglob(f"*{ext}")):
        if p.is_file():
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--litrev", required=True,
                    help="directory holding the unpacked LitRev snapshots")
    ap.add_argument("--out", default="eval/legacy_loc.json")
    ap.add_argument("--csv", default="eval/legacy_loc.csv")
    args = ap.parse_args()

    base = Path(args.litrev).resolve()
    rows = []
    for group, rel, language in SOURCES:
        for path in iter_files(base, rel, language):
            total, code, non_blank = classify(
                path.read_text(encoding="utf-8", errors="replace"), language)
            rows.append({
                "source_group": group,
                "path": str(path.relative_to(base)),
                "language": language,
                "total_lines": total,
                "code_lines": code,
                "non_blank_lines": non_blank,
            })

    per_group: dict[str, dict] = {}
    for r in rows:
        g = per_group.setdefault(r["source_group"], {
            "files": 0, "total_lines": 0, "code_lines": 0, "non_blank_lines": 0})
        g["files"] += 1
        for k in ("total_lines", "code_lines", "non_blank_lines"):
            g[k] += r[k]

    totals = {k: sum(r[k] for r in rows)
              for k in ("total_lines", "code_lines", "non_blank_lines")}
    totals["files"] = len(rows)

    payload = {
        "litrev_root": str(base),
        "per_file": sorted(rows, key=lambda r: (r["source_group"], r["path"])),
        "per_group": {k: per_group[k] for k in sorted(per_group)},
        "totals": totals,
        "plan_claim_total_loc": 1251,
        "delta_vs_plan_total_lines": totals["total_lines"] - 1251,
        "delta_vs_plan_code_lines": totals["code_lines"] - 1251,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    with Path(args.csv).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_group", "path", "language",
                                           "total_lines", "code_lines",
                                           "non_blank_lines"])
        w.writeheader()
        w.writerows(payload["per_file"])

    for group in sorted(per_group):
        g = per_group[group]
        print(f"{group:24s} files={g['files']:2d} total={g['total_lines']:4d} "
              f"code={g['code_lines']:4d} non_blank={g['non_blank_lines']:4d}")
    print(f"{'TOTAL':24s} files={totals['files']:2d} "
          f"total={totals['total_lines']:4d} code={totals['code_lines']:4d} "
          f"non_blank={totals['non_blank_lines']:4d}")
    print(f"plan claim: 1251 | delta(total)={payload['delta_vs_plan_total_lines']:+d} "
          f"| delta(code)={payload['delta_vs_plan_code_lines']:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
