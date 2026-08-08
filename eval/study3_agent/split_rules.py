#!/usr/bin/env python3
"""Study 3 pilot 2 — the shown / held-out rule split (redesign §4).

    export PYTHONPATH=<limen>/src
    python eval/study3_agent/split_rules.py            # writes the split
    python eval/study3_agent/split_rules.py --check    # + applicability audit

The split must be RANDOM and fixed BEFORE the first run, otherwise H2 is
unfalsifiable.  Two properties are enforced here:

STRATIFICATION BY RULE KIND (K1–K10)
    Rules are grouped by `kind` and each group is split independently, so
    every kind is represented on both sides.  Without this, a whole kind can
    land in one half and "transfer" would only measure similarity within a
    kind.  Odd-sized groups alternate which side gets the extra rule, in a
    seeded order, so the halves stay balanced overall.

APPLICABILITY (the §4 precondition, and pilot 1's fatal omission)
    `--check` measures, for every task's parent tree, how many held-out
    rules can actually FIRE there: a rule is applicable if `limen check`
    reports at least one finding for it on the parent tree, or if the parent
    tree contains at least one file inside the rule's scope so a new finding
    is reachable.  Pilot 1 never checked this and every Δ_heldout was 0 by
    construction.  Tasks that fail the precondition are REPORTED, not
    silently dropped.

Outputs (both under eval/results/):
    study3_pilot2_rule_split.json   — the split, seed, spec sha256
    study3_pilot2_applicability.json — per-task held-out applicability
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import yaml

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL / "study3_agent"))
os.environ.setdefault("STUDY3_SCRATCH", "/tmp/limen_s3")

import run as R  # noqa: E402  (needs STUDY3_SCRATCH set first)

SPLIT_SEED = 20260807          # recorded in the output; drawn once, never tuned
TASKS = EVAL / "study3_agent" / "tasks_layer1.jsonl"
OUT_SPLIT = EVAL / "results" / "study3_pilot2_rule_split.json"
OUT_APPLIC = EVAL / "results" / "study3_pilot2_applicability.json"


def load_spec() -> dict:
    return yaml.safe_load(R.SPEC_FROZEN.read_text(encoding="utf-8"))


def stratified_split(rules: list[dict], seed: int) -> tuple[list[str], list[str]]:
    by_kind: dict[str, list[str]] = defaultdict(list)
    for r in rules:
        by_kind[r["kind"]].append(r["id"])
    rng = random.Random(seed)
    shown, heldout = [], []
    # Alternate which side receives the odd rule, in a seeded kind order, so
    # neither half systematically collects the singleton kinds.
    kinds = sorted(by_kind)
    rng.shuffle(kinds)
    give_extra_to_shown = True
    for kind in kinds:
        ids = sorted(by_kind[kind])
        rng.shuffle(ids)
        half = len(ids) // 2
        if len(ids) % 2:
            take = half + 1 if give_extra_to_shown else half
            give_extra_to_shown = not give_extra_to_shown
        else:
            take = half
        shown += ids[:take]
        heldout += ids[take:]
    return sorted(shown), sorted(heldout)


def rule_scope_paths(rule: dict) -> list[str]:
    scope = rule.get("scope") or {}
    return list(scope.get("paths") or [])


def applicability(tasks: list[dict], heldout: list[str], spec_doc: dict) -> dict:
    """For each task: which held-out rules can fire on its PARENT tree.

    Two independent signals, both measured on the materialised parent tree:

    * `fires_on_parent` — the rule already produces a finding there.  The
      strongest evidence that the rule is live on this tree.
    * `has_scoped_files` — the tree contains at least one file the rule's
      scope selects, so a NEW finding is reachable even where the parent is
      currently clean.  A rule whose scope selects nothing cannot ever be
      violated by an edit to this tree, which is precisely the pilot-1
      degeneracy.
    """
    spec_full = R.derive_spec(Path(R.SCRATCH) / "spec_full_open.yaml")
    rules_by_id = {r["id"]: r for r in spec_doc["rules"]}
    out = {}
    for t in tasks:
        state = R.task_state(t)
        today = R.git(R.LIVE / t["repo"], "show", "-s", "--format=%cI",
                      t["parent_sha"]).strip()[:10]
        run_dir = Path(R.SCRATCH) / "applic" / t["task_id"]
        trees = R.build_run_tree(t, state, run_dir)
        chk = R.run_check(trees["ws"], spec_full, today)
        firing = defaultdict(int)
        for f in (chk.get("findings") or []):
            firing[f.get("rule_id")] += 1

        ws = trees["ws"]
        per_rule = {}
        for rid in heldout:
            rule = rules_by_id[rid]
            paths = rule_scope_paths(rule)
            if paths:
                n_files = sum(1 for p in paths for _ in ws.glob(p))
            else:
                # No `paths:` restriction — the rule applies to whole roots,
                # so any source file under a declared root is in scope.
                roots = (rule.get("scope") or {}).get("roots") or []
                n_files = 0
                for root in roots:
                    decl = spec_doc["roots"].get(root)
                    if not decl:
                        continue
                    base = ws / decl["repo"] / decl["path"]
                    if base.exists():
                        n_files += sum(
                            1 for p in base.rglob("*")
                            if p.is_file() and p.suffix in
                            {".php", ".js", ".jsx", ".ts", ".tsx", ".md"})
            per_rule[rid] = {
                "kind": rule["kind"],
                "fires_on_parent": firing.get(rid, 0),
                "scoped_files_on_parent": n_files,
                "applicable": bool(firing.get(rid, 0) or n_files),
            }
        n_app = sum(1 for v in per_rule.values() if v["applicable"])
        out[t["task_id"]] = {
            "repo": t["repo"], "parent_sha": t["parent_sha"], "today": today,
            "parent_errors_total": chk.get("n_errors"),
            "n_heldout_applicable": n_app,
            "precondition_met": n_app >= 1,
            "target_rule_id": t["target_rule_id"],
            "target_rule_side": "heldout" if t["target_rule_id"] in heldout
                                else "shown",
            "per_rule": per_rule,
        }
        shutil.rmtree(run_dir, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="also measure held-out applicability per task")
    args = ap.parse_args()

    spec_doc = load_spec()
    shown, heldout = stratified_split(spec_doc["rules"], SPLIT_SEED)
    kinds = {r["id"]: r["kind"] for r in spec_doc["rules"]}
    split = {
        "seed": SPLIT_SEED,
        "spec_path": str(R.SPEC_FROZEN),
        "spec_sha256": R.sha256_file(R.SPEC_FROZEN),
        "n_rules": len(spec_doc["rules"]),
        "stratified_by": "rule kind (K1-K10)",
        "shown_rules": shown,
        "heldout_rules": heldout,
        "per_kind": {
            k: {"shown": sorted(r for r in shown if kinds[r] == k),
                "heldout": sorted(r for r in heldout if kinds[r] == k)}
            for k in sorted(set(kinds.values()))
        },
    }
    # Stratification cannot put a kind on both sides when the spec contains
    # only ONE rule of that kind: the singleton goes to whichever side the
    # seeded alternation gives it and the other side has none.  Recorded
    # rather than silently tolerated, because a claim of "every kind on both
    # sides" would be false for these kinds.
    split["singleton_kinds"] = {
        k: {"n_rules": len(v["shown"]) + len(v["heldout"]),
            "side": "shown" if v["shown"] else "heldout",
            "rules": v["shown"] + v["heldout"]}
        for k, v in split["per_kind"].items()
        if not v["shown"] or not v["heldout"]}
    split["kinds_on_both_sides"] = sorted(
        k for k, v in split["per_kind"].items() if v["shown"] and v["heldout"])
    OUT_SPLIT.parent.mkdir(parents=True, exist_ok=True)
    OUT_SPLIT.write_text(json.dumps(split, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    print(json.dumps({"n_shown": len(shown), "n_heldout": len(heldout),
                      "per_kind": {k: [len(v["shown"]), len(v["heldout"])]
                                   for k, v in split["per_kind"].items()}},
                     indent=1))

    if args.check:
        tasks = [json.loads(l) for l in
                 TASKS.read_text(encoding="utf-8").splitlines() if l.strip()]
        ap_out = applicability(tasks, heldout, spec_doc)
        OUT_APPLIC.write_text(json.dumps(ap_out, indent=1, ensure_ascii=False),
                              encoding="utf-8")
        for tid, v in ap_out.items():
            print(f"{tid:32s} heldout_applicable={v['n_heldout_applicable']:2d}"
                  f"/{len(heldout)}  precondition={v['precondition_met']}"
                  f"  target_side={v['target_rule_side']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())