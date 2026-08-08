#!/usr/bin/env python3
"""Study 1, step 7 — the article table.

Produces `eval/results/study1_table.csv` and `.md`:

    rule kind (K1–K11) × limen rules × legacy rules × legacy LOC replaced × YAML LOC

plus the diff counts **per category, never aggregated**.

Every figure is computed here from four measured sources; nothing is typed in:

* `eval/legacy_rule_inventory.csv` — 47 legacy rules, each with a source file,
  a line range and its `K` kind. This is what attributes legacy LOC to a kind.
* `eval/legacy_loc.csv` / `.json` — per-file LOC of the legacy checkers, three
  definitions (`total_lines` = `wc -l`, `code_lines`, `non_blank_lines`).
* `eval/spec_loc.json` and the spec YAML itself — rule counts per kind and the
  YAML lines each rule block occupies.
* `eval/results/study1_diff.json` — the per-category difference counts.

## LOC attribution method

**Legacy → kind.** Each inventory row carries `source_file` + `source_lines`
(a comma-separated list of `a-b` ranges, or a bare line number). Every line of
every legacy source file is assigned to the set of inventory rows whose ranges
cover it:

* covered by exactly one row → the whole line is attributed to that row's kind;
* covered by *n > 1* rows spanning *k* distinct kinds → the line is
  attributed to the row "shared infrastructure" when k > 1, because splitting
  it between kinds would be arbitrary; when all n rows share one kind the line
  goes to that kind (no ambiguity exists);
* covered by no row → "unattributed", reported as its own row, never spread.

Additionally, rows whose kind is not a `K`-kind (engine infrastructure, spec
roots, `spec validate` integrity checks — e.g. `ArchitectureRulesTestCase`,
which every checker inherits from) are collected into **"shared
infrastructure"** rather than smeared across the kinds that use them. That is
the row the task brief asks for.

Two LOC definitions are carried through the whole table (`total` = `wc -l`,
`code` = blank/comment-stripped) because they differ by ~17% and the paper
must say which it quotes.

**Spec → kind.** A rule's YAML block runs from its `- id:` line to the line
before the next `- id:` (or the end of file for the last one). Lines before
the first rule block are the spec *preamble* (roots, extractors, units,
layers, public_surface, artifact_sets, unresolved_policy) — shared by every
rule, so they form the spec-side "shared infrastructure" row too.

Usage:

    python eval/study1_table.py \
        --inventory eval/legacy_rule_inventory.csv \
        --legacy-loc eval/legacy_loc.json \
        --spec <LIMEN>/workspace/architecture.yaml \
        --spec-loc eval/spec_loc.json \
        --diff eval/results/study1_diff.json \
        --litrev <LIMEN>/LitRev \
        --out-csv eval/results/study1_table.csv \
        --out-md  eval/results/study1_table.md
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import count_legacy_loc  # noqa: E402  — single source of the "code line" definition

SHARED = "shared infrastructure"
# Lines no inventory row claims, split mechanically by position rather than
# lumped together: a line before the first / after the last claimed line of its
# file is scaffolding (php open tag, namespace, imports, class declaration,
# closing brace); a line *between* two claimed ranges is helper machinery the
# surrounding rules share (e.g. `assertImportsMatchBaseline`,
# ModuleBoundaryBaselineRulesTest.php:26-68, or the frontend auditor's scanning
# core at :90-177). Both are reported; neither is distributed over the kinds.
SCAFFOLDING = "file scaffolding (unclaimed)"
INTERSTITIAL = "shared helper machinery (unclaimed)"

# The eleven rule kinds of architecture-spec/1, in spec order.
K_ORDER = [
    "K1 layer-dependency", "K2 ownership", "K3 consumer-allowlist",
    "K4 public-api-only", "K5 scoped-allowlist", "K6 forbidden-target",
    "K7 baseline-snapshot", "K8 pattern-ban", "K9 structure",
    "K10 metric-threshold", "K11 reachability",
]
KIND_OF_K = {k.split(" ", 1)[1]: k for k in K_ORDER}
K_RX = re.compile(r"^K(\d+)\s")

# Inventory `source_file` is repo-relative in two different ways; map it to the
# path used in eval/legacy_loc.json (which is rooted at LitRev/<repo>-main/).
INVENTORY_TO_LOC: tuple[tuple[str, str], ...] = (
    ("litrev-backend/config/architecture.php",
     "litrev-backend-main/config/architecture.php"),
    ("backend-shared/config/architecture.php",
     "backend-shared-main/config/architecture.php"),
    ("tests/Architecture/", "litrev-backend-main/tests/Architecture/"),
    ("scripts/", "litrev-frontend-main/scripts/"),
)


def loc_path(source_file: str) -> str:
    for prefix, repl in INVENTORY_TO_LOC:
        if source_file.startswith(prefix):
            return repl + source_file[len(prefix):]
    return source_file


def parse_ranges(spec: str) -> list[tuple[int, int]]:
    """'7-33', '62,263-272' -> [(7,33)] / [(62,62),(263,272)]"""
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((int(a), int(b)))
        else:
            out.append((int(part), int(part)))
    return out


def normalise_kind(raw: str) -> str:
    """Map an inventory `limen_kind` cell onto a K-kind or SHARED."""
    raw = (raw or "").strip()
    if K_RX.match(raw):
        return raw
    # '(engine infrastructure)', '(spec roots:)', '(units.declared + S3/S6)',
    # '(spec validate S3)', '(none)' — none of these is a rule kind.
    return SHARED


def code_line_flags(text: str, language: str) -> list[bool]:
    """Per-line 'is a code line' flags, using the study's single definition.

    This re-implements `count_legacy_loc.classify()` line by line instead of
    as a total, so the per-kind sums reconcile with `eval/legacy_loc.json`
    exactly. The stateful block-comment handling (including Laravel's `|`
    continuation style) is what makes a naive per-line heuristic disagree —
    the cross-checks at the end of `main()` are what caught that.
    """
    open_rx, close_rx = count_legacy_loc.BLOCK[language]
    line_prefixes = count_legacy_loc.LINE[language]
    in_block = False
    flags: list[bool] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            flags.append(False)
            continue
        if in_block:
            is_code = False
            if re.search(close_rx, stripped):
                in_block = False
                after = re.split(close_rx, stripped, maxsplit=1)[1].strip()
                is_code = bool(after)
            flags.append(is_code)
            continue
        if re.match(open_rx, stripped):
            if not re.search(close_rx, stripped):
                in_block = True
                flags.append(False)
                continue
            after = re.split(close_rx, stripped, maxsplit=1)[1].strip()
            flags.append(bool(after))
            continue
        if any(stripped.startswith(p) for p in line_prefixes):
            flags.append(False)
            continue
        flags.append(True)
    return flags


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", default="eval/legacy_rule_inventory.csv")
    ap.add_argument("--legacy-loc", default="eval/legacy_loc.json")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--spec-loc", default="eval/spec_loc.json")
    ap.add_argument("--diff", default="eval/results/study1_diff.json")
    ap.add_argument("--litrev", required=True,
                    help="root of the unpacked LitRev snapshots (legacy sources)")
    ap.add_argument("--out-csv", default="eval/results/study1_table.csv")
    ap.add_argument("--out-md", default="eval/results/study1_table.md")
    ap.add_argument("--out-json", default="eval/results/study1_table.json")
    args = ap.parse_args()

    litrev = Path(args.litrev)
    inventory = list(csv.DictReader(open(args.inventory, encoding="utf-8")))
    legacy_loc = json.loads(Path(args.legacy_loc).read_text(encoding="utf-8"))
    spec_loc = json.loads(Path(args.spec_loc).read_text(encoding="utf-8"))
    diff = json.loads(Path(args.diff).read_text(encoding="utf-8"))

    # ---------------- legacy LOC attribution -----------------------------
    # file -> line number -> set of kinds claiming it
    claims: dict[str, dict[int, set[str]]] = {}
    rules_per_kind_legacy: dict[str, int] = {}
    for row in inventory:
        kind = normalise_kind(row["limen_kind"])
        rules_per_kind_legacy[kind] = rules_per_kind_legacy.get(kind, 0) + 1
        path = loc_path(row["source_file"])
        per_file = claims.setdefault(path, {})
        for a, b in parse_ranges(row["source_lines"]):
            for ln in range(a, b + 1):
                per_file.setdefault(ln, set()).add(kind)

    per_file_loc = {r["path"]: r for r in legacy_loc["per_file"]}
    missing = sorted(set(claims) - set(per_file_loc))
    if missing:
        raise SystemExit(f"inventory references files absent from legacy_loc: {missing}")

    legacy_total: dict[str, int] = {}
    legacy_code: dict[str, int] = {}
    contested_lines = 0
    for path, meta in per_file_loc.items():
        text = (litrev / path).read_text(encoding="utf-8", errors="replace")
        flags = code_line_flags(text, meta["language"])
        per_file = claims.get(path, {})
        lo = min(per_file) if per_file else None
        hi = max(per_file) if per_file else None
        for i, is_code in enumerate(flags, start=1):
            kinds = per_file.get(i, set())
            if not kinds:
                bucket = (INTERSTITIAL if lo is not None and lo < i < hi
                          else SCAFFOLDING)
            elif len(kinds) == 1:
                bucket = next(iter(kinds))
            else:
                bucket = SHARED
                contested_lines += 1
            legacy_total[bucket] = legacy_total.get(bucket, 0) + 1
            if is_code:
                legacy_code[bucket] = legacy_code.get(bucket, 0) + 1

    # ---------------- spec YAML attribution ------------------------------
    spec_path = Path(args.spec)
    spec_lines = spec_path.read_text(encoding="utf-8").splitlines()
    doc_ids = [(i, l) for i, l in enumerate(spec_lines, start=1)
               if re.match(r"^\s*-\s+id:", l)]
    if len(doc_ids) != spec_loc["rule_count"]:
        raise SystemExit(f"found {len(doc_ids)} `- id:` blocks but spec_loc says "
                         f"{spec_loc['rule_count']} rules")
    import yaml  # local import: only this step needs it
    spec_doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    rule_kinds = [r["kind"] for r in spec_doc["rules"]]

    bounds = [doc_ids[i][0] for i in range(len(doc_ids))] + [len(spec_lines) + 1]
    yaml_total: dict[str, int] = {}
    yaml_code: dict[str, int] = {}
    rules_per_kind_limen: dict[str, int] = {}
    for idx, kind in enumerate(rule_kinds):
        k = KIND_OF_K[kind]
        rules_per_kind_limen[k] = rules_per_kind_limen.get(k, 0) + 1
        block = spec_lines[bounds[idx] - 1: bounds[idx + 1] - 1]
        yaml_total[k] = yaml_total.get(k, 0) + len(block)
        yaml_code[k] = yaml_code.get(k, 0) + sum(
            1 for l in block if l.strip() and not l.strip().startswith("#"))
    preamble = spec_lines[: bounds[0] - 1]
    yaml_total[SHARED] = len(preamble)
    yaml_code[SHARED] = sum(1 for l in preamble
                            if l.strip() and not l.strip().startswith("#"))

    # ---------------- assemble -------------------------------------------
    order = K_ORDER + [SHARED, INTERSTITIAL, SCAFFOLDING]
    rows: list[dict[str, Any]] = []
    for k in order:
        rows.append({
            "kind": k,
            "limen_rules": rules_per_kind_limen.get(k, 0),
            "legacy_rules": rules_per_kind_legacy.get(k, 0),
            "legacy_loc_total": legacy_total.get(k, 0),
            "legacy_loc_code": legacy_code.get(k, 0),
            "yaml_loc_total": yaml_total.get(k, 0),
            "yaml_loc_code": yaml_code.get(k, 0),
        })
    totals = {
        "kind": "TOTAL",
        "limen_rules": sum(r["limen_rules"] for r in rows),
        "legacy_rules": sum(r["legacy_rules"] for r in rows),
        "legacy_loc_total": sum(r["legacy_loc_total"] for r in rows),
        "legacy_loc_code": sum(r["legacy_loc_code"] for r in rows),
        "yaml_loc_total": sum(r["yaml_loc_total"] for r in rows),
        "yaml_loc_code": sum(r["yaml_loc_code"] for r in rows),
    }

    # cross-checks against the independently-measured figures
    checks = {
        "legacy_total_matches_legacy_loc":
            totals["legacy_loc_total"] == legacy_loc["totals"]["total_lines"],
        "legacy_code_matches_legacy_loc":
            totals["legacy_loc_code"] == legacy_loc["totals"]["code_lines"],
        "yaml_total_matches_spec_loc":
            totals["yaml_loc_total"] == spec_loc["total_lines"],
        "yaml_code_matches_spec_loc":
            totals["yaml_loc_code"] == spec_loc["code_lines"],
        "limen_rules_matches_spec_loc":
            totals["limen_rules"] == spec_loc["rule_count"],
        "legacy_rules_matches_inventory":
            totals["legacy_rules"] == len(inventory),
    }

    diff_counts = diff["counts"]
    per_category = diff_counts["per_category"]

    payload = {
        "schema": "study1-table/1",
        "sources": {
            "inventory": args.inventory, "legacy_loc": args.legacy_loc,
            "spec": str(spec_path), "spec_loc": args.spec_loc, "diff": args.diff,
        },
        "attribution_method": {
            "legacy": "inventory line ranges per source file; lines claimed by "
                      "rows of more than one kind, and rows whose kind is not a "
                      "K-kind (engine infrastructure, spec roots, spec-validate "
                      "integrity), go to 'shared infrastructure'; lines claimed "
                      "by no row are split by position into 'shared helper "
                      "machinery (unclaimed)' (between two claimed ranges of "
                      "the same file) and 'file scaffolding (unclaimed)' "
                      "(before the first or after the last claimed line)",
            "spec": "a rule owns the YAML lines from its `- id:` to the line "
                    "before the next `- id:`; the preamble (roots, extractors, "
                    "units, layers, public_surface, artifact_sets, "
                    "unresolved_policy) is 'shared infrastructure'",
            "contested_legacy_lines": contested_lines,
        },
        "rows": rows,
        "totals": totals,
        "cross_checks": checks,
        "differences": {
            "categories": diff["categories"],
            "per_category": per_category,
            "unclassified": diff_counts["unclassified"],
            "agreements": diff_counts["agreements"],
            "total": diff_counts["differences_total"],
            "legacy_only": diff_counts["differences_legacy_only"],
            "limen_only": diff_counts["differences_limen_only"],
            "out_of_scope_limen": diff_counts["limen_findings_out_of_scope"],
            "out_of_scope_legacy": diff_counts["legacy_findings_out_of_scope"],
            "limen_findings_total": diff_counts["limen_findings_total"],
            "legacy_findings_total": diff_counts["legacy_findings_total"],
        },
    }

    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(payload, indent=2, sort_keys=True,
                                              ensure_ascii=False) + "\n",
                                   encoding="utf-8")

    fields = ["kind", "limen_rules", "legacy_rules", "legacy_loc_total",
              "legacy_loc_code", "yaml_loc_total", "yaml_loc_code"]
    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        w.writerow(totals)

    md = _markdown(payload)
    Path(args.out_md).write_text(md, encoding="utf-8")

    print(f"{args.out_csv}\n{args.out_md}\n{args.out_json}")
    for name, ok in checks.items():
        print(f"  check {name}: {'OK' if ok else 'FAILED'}")
    if not all(checks.values()):
        print("cross-check failed: the table does not reconcile with the "
              "independently measured totals")
        return 1
    return 0


def _markdown(p: dict[str, Any]) -> str:
    rows, t, d = p["rows"], p["totals"], p["differences"]
    out = ["# Study 1 — rule kinds, rule counts and replaced LOC", "",
           "Generated by `eval/study1_table.py`. Every figure is computed; "
           "nothing in this file is typed by hand.", "",
           "## Rule kinds", "",
           "| Rule kind | limen rules | legacy rules | legacy LOC (wc -l) | "
           "legacy LOC (code) | YAML lines | YAML lines (code) |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in rows:
        out.append(f"| {r['kind']} | {r['limen_rules']} | {r['legacy_rules']} | "
                   f"{r['legacy_loc_total']} | {r['legacy_loc_code']} | "
                   f"{r['yaml_loc_total']} | {r['yaml_loc_code']} |")
    out.append(f"| **{t['kind']}** | **{t['limen_rules']}** | **{t['legacy_rules']}** | "
               f"**{t['legacy_loc_total']}** | **{t['legacy_loc_code']}** | "
               f"**{t['yaml_loc_total']}** | **{t['yaml_loc_code']}** |")
    out += ["", "`shared infrastructure` collects legacy code no single rule kind "
            "owns (`ArchitectureRulesTestCase` and the other rows whose inventory "
            "kind is not a K-kind, plus lines claimed by rows of several kinds) "
            "and, on the spec side, the YAML preamble every rule depends on. "
            "The two `(unclaimed)` rows are legacy source no inventory row "
            "covers, split by position: `shared helper machinery` sits between "
            "two claimed ranges of the same file (scanning and assertion "
            "helpers the surrounding rules call), `file scaffolding` sits "
            "before the first or after the last claimed line (open tag, "
            "namespace, imports, class declaration, closing brace). Neither is "
            "distributed over the rule kinds.", "",
            f"Contested legacy lines (claimed by rows of more than one kind): "
            f"{p['attribution_method']['contested_legacy_lines']}.", "",
            "## Differences, per category", "",
            "Reported separately by construction; the three categories are never "
            "summed into a single 'differences' figure.", "",
            "| Category | Meaning | Count |", "| --- | --- | ---: |"]
    for cid, label in d["categories"].items():
        out.append(f"| {cid} | {label} | {d['per_category'][cid]} |")
    out.append(f"| — | **unclassified (acceptance criterion: 0)** | "
               f"**{d['unclassified']}** |")
    out += ["", "| Quantity | Count |", "| --- | ---: |",
            f"| limen findings (total) | {d['limen_findings_total']} |",
            f"| legacy findings (total) | {d['legacy_findings_total']} |",
            f"| agreements on (file, line, target) | {d['agreements']} |",
            f"| differences, legacy-only | {d['legacy_only']} |",
            f"| differences, limen-only | {d['limen_only']} |",
            f"| limen findings outside the comparison scope | {d['out_of_scope_limen']} |",
            f"| legacy findings outside the comparison scope | {d['out_of_scope_legacy']} |",
            ""]
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
