#!/usr/bin/env python3
"""Count the retrofitted specification's size, three ways.

Reported alongside the legacy LOC (eval/count_legacy_loc.py) so the
"1497 lines of imperative checker → N lines of declarative spec" comparison in
the paper is computed, not asserted. `code` excludes blank and `#`-comment
lines; the retrofit is heavily commented on purpose (every rule cites the
legacy source it reproduces), so the two figures differ substantially and both
belong in the paper.

Usage:
    python eval/count_spec_loc.py --spec ../workspace/architecture.yaml \
        --out eval/spec_loc.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default="eval/spec_loc.json")
    args = ap.parse_args()

    path = Path(args.spec)
    lines = path.read_text(encoding="utf-8").splitlines()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))

    kinds: dict[str, int] = {}
    severities: dict[str, int] = {}
    for r in doc.get("rules", []):
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        sev = r.get("severity", "error")
        severities[sev] = severities.get(sev, 0) + 1

    payload = {
        "spec_path": str(path),
        "total_lines": len(lines),
        "non_blank_lines": sum(1 for line in lines if line.strip()),
        "code_lines": sum(1 for line in lines
                          if line.strip() and not line.strip().startswith("#")),
        "rule_count": len(doc.get("rules", [])),
        "root_count": len(doc.get("roots", {})),
        "declared_unit_count": len((doc.get("units") or {}).get("declared") or []),
        "rules_per_kind": dict(sorted(kinds.items())),
        "rules_per_severity": dict(sorted(severities.items())),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"{path.name}: total={payload['total_lines']} "
          f"non_blank={payload['non_blank_lines']} "
          f"code={payload['code_lines']}")
    print(f"rules={payload['rule_count']} roots={payload['root_count']} "
          f"units={payload['declared_unit_count']}")
    for kind, n in payload["rules_per_kind"].items():
        print(f"  {kind}: {n}")
    print(f"severity: {payload['rules_per_severity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
