#!/usr/bin/env python3
"""Study 3 pilot 2 — the §6 gate analysis.

    export PYTHONPATH=<limen>/src
    python eval/study3_agent/analyze_pilot2.py

Reads every ``eval/results/study3_pilot2/<task>/A_<r>.json`` and writes
``study3_pilot2_results.csv`` + ``study3_pilot2_summary.{md,json}``.
Every number in the summary is computed here; none is typed by hand.

THE GATE MEASURE
----------------
Not the mean number of violations (pilot 1 proved that measure is a floor of
zeros) but the EVENT RATE: the fraction of condition-A runs in which the
agent's tree carries at least one violation the parent tree did not.  A
Wilson 95% interval accompanies it, because at n=18 a Wald interval on a
small proportion is meaningless.

A "new violation" is a FINDING IDENTITY absent from the parent, not a count
increase: (rule_id, file, target, line).  A count-based delta would call a
run clean when the agent removes one pre-existing violation and adds a
different one — which is precisely a run that violated the architecture.
Both are reported; ``delta_errors`` is kept for comparability with pilot 1.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
RESULTS = EVAL / "results" / "study3_pilot2"
PLAN = EVAL / "results" / "study3_pilot2_plan.json"
SPLIT = EVAL / "results" / "study3_pilot2_rule_split.json"
APPLIC = EVAL / "results" / "study3_pilot2_applicability.json"
TASKS = EVAL / "study3_agent" / "tasks_layer1.jsonl"
OUT_CSV = EVAL / "results" / "study3_pilot2_results.csv"
OUT_MD = EVAL / "results" / "study3_pilot2_summary.md"
OUT_JSON = EVAL / "results" / "study3_pilot2_summary.json"

GATE = 0.30


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def ident(f: dict) -> tuple:
    return (f.get("rule_id"), f.get("file"), f.get("target"), f.get("line"))


_PHP_USE = __import__("re").compile(r"^\+use\s+(App\\Modules\\(\w+)\\[^;]+);",
                                    __import__("re").MULTILINE)
_JS_IMPORT = __import__("re").compile(
    r"^\+.*from\s+[\"'](@/modules/([\w-]+)|\.\./\.\./([\w-]+))",
    __import__("re").MULTILINE)


def boundary_probe(diff: str, task: dict) -> dict:
    """Did the agent CROSS the tempted boundary at all, and how?

    This is the diagnostic the gate verdict turns on.  A run with no new
    violation has two very different explanations:

    * the agent crossed into another module but entered through the sanctioned
      surface — the rule was live and the agent complied; or
    * the agent never crossed at all — it solved the task entirely inside its
      own module, so no cross-module rule could fire whatever it wrote.

    Only the second is a task-design failure, and the two are
    indistinguishable from the violation count alone.  Measured on the diff:
    every ADDED cross-module import, split into imports of the tempted
    module's public surface (Contracts/Events/Domain/Presentation) versus its
    internals.
    """
    own = set(task.get("modules") or [])
    php = [(fq, mod) for fq, mod in _PHP_USE.findall(diff) if mod not in own]
    surface = [fq for fq, _ in php
               if any(f"\\{seg}\\" in fq for seg in
                      ("Contracts", "Events", "Domain", "Presentation"))]
    internals = [fq for fq, _ in php if fq not in set(surface)]
    js = sorted({m2 or m3 for _, m2, m3 in
                 ((m, a, b) for m, a, b in
                  ((g[0], g[1], g[2]) for g in _JS_IMPORT.findall(diff)))
                 if (m2 or m3) and (m2 or m3) not in own})
    return {
        "n_cross_module_imports_added": len(php) + len(js),
        "cross_modules_touched": sorted({m for _, m in php} | set(js)),
        "n_via_public_surface": len(surface),
        "n_via_internals": len(internals),
        "internals_imported": sorted(set(internals))[:6],
        "crossed_boundary": bool(php or js),
    }


def sample_size(p1: float, p0: float, alpha: float = 0.05,
                power: float = 0.80) -> int | None:
    """Two-proportion, two-sided, per arm (normal approximation)."""
    if p1 == p0:
        return None
    z_a, z_b = 1.959963984540054, 0.8416212335729143
    pbar = (p1 + p0) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar))
           + z_b * math.sqrt(p1 * (1 - p1) + p0 * (1 - p0))) ** 2
    return math.ceil(num / (p1 - p0) ** 2)


def main() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    applic = json.loads(APPLIC.read_text(encoding="utf-8"))
    tasks = {json.loads(l)["task_id"]: json.loads(l)
             for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()}
    heldout = set(split["heldout_rules"])

    rows = []
    for tid, task in tasks.items():
        base_ids = {ident(f) for f in plan["baseline"][tid]["findings"]
                    if f.get("severity") == "error"}
        for rep in range(1, plan["reps"] + 1):
            p = RESULTS / tid / f"A_{rep}.json"
            if not p.exists():
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            errs = [f for f in d["final_findings"]
                    if f.get("severity") == "error"]
            new = [f for f in errs if ident(f) not in base_ids]
            new_shown = [f for f in new if f.get("rule_id") not in heldout]
            new_held = [f for f in new if f.get("rule_id") in heldout]
            hit_target = any(f.get("rule_id") == task["target_rule_id"]
                             for f in new)
            bp = boundary_probe(d["diff"], task)
            # The probe measures MODULE crossings; on a pre-modular tree
            # (fe-04/fe-05 sit on 90f87301, which has no src/modules at all)
            # there are no module boundaries to cross, so a 0 there means
            # "not applicable", not "the agent stayed home".
            bp["probe_applicable"] = bool(task.get("modules")) or \
                task["repo"] == "litrev-backend"
            rows.append({
                "task_id": tid, "layer": task["layer"], "repo": task["repo"],
                "condition": "A", "repetition": rep,
                "model": d["model"], "spec_sha256": d["spec_sha256"],
                "split_seed": split["seed"],
                "target_rule_id": task["target_rule_id"],
                "target_rule_K": task["target_rule_K"],
                "target_rule_side": ("heldout"
                                     if task["target_rule_id"] in heldout
                                     else "shown"),
                "baseline_errors": plan["baseline"][tid]["check"]["n_errors"],
                "final_errors": d["final_check"]["n_errors"],
                "delta_errors": d["delta_errors"],
                "n_new_violations": len(new),
                "n_new_shown": len(new_shown),
                "n_new_heldout": len(new_held),
                "any_new_violation": int(bool(new)),
                "any_new_shown": int(bool(new_shown)),
                "any_new_heldout": int(bool(new_held)),
                "hit_target_rule": int(hit_target),
                "new_rule_ids": ";".join(sorted({f["rule_id"] for f in new})),
                "tests_pass": int(bool(d["tests_pass"])),
                "tests_new_failures": len(d["tests_new_failures"]),
                "tests_lost_passes": d["tests_lost_passes"],
                "crossed_boundary": int(bp["crossed_boundary"]),
                "boundary_probe_applicable": int(bp["probe_applicable"]),
                "n_cross_module_imports": bp["n_cross_module_imports_added"],
                "n_via_public_surface": bp["n_via_public_surface"],
                "n_via_internals": bp["n_via_internals"],
                "cross_modules_touched": ";".join(bp["cross_modules_touched"]),
                "empty_diff": int(d["diff_bytes"] == 0),
                "diff_bytes": d["diff_bytes"],
                "files_written": len(d["iterations"][0]["applied"]["written"]),
                "edits_rejected": len(d["iterations"][0]["applied"]["rejected"]),
                "tokens_in": d["tokens"]["input"],
                "tokens_out": d["tokens"]["output"],
                "seconds": d["seconds"],
            })

    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n = len(rows)

    def rate(key: str) -> dict:
        k = sum(r[key] for r in rows)
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": k / n if n else 0.0,
                "wilson95": [round(lo, 4), round(hi, 4)]}

    ev = rate("any_new_violation")
    per_task = {}
    for tid in tasks:
        tr = [r for r in rows if r["task_id"] == tid]
        k = sum(r["any_new_violation"] for r in tr)
        per_task[tid] = {
            "n_runs": len(tr), "n_failing_runs": k,
            "target_rule_id": tasks[tid]["target_rule_id"],
            "target_rule_side": tr[0]["target_rule_side"] if tr else None,
            "n_hit_target_rule": sum(r["hit_target_rule"] for r in tr),
            "new_rule_ids": sorted({x for r in tr for x in
                                    r["new_rule_ids"].split(";") if x}),
            "diagnostic": k > 0,
            "n_runs_crossing_boundary": sum(r["crossed_boundary"] for r in tr),
            "n_empty_diff_runs": sum(r["empty_diff"] for r in tr),
            "cross_modules_touched": sorted({x for r in tr for x in
                                             r["cross_modules_touched"].split(";") if x}),
            "tests_regressions": sum(1 for r in tr if not r["tests_pass"]),
            "mean_diff_bytes": round(sum(r["diff_bytes"] for r in tr) / len(tr))
            if tr else 0,
        }
    non_diag = sorted(t for t, v in per_task.items() if not v["diagnostic"])

    tokens_in = sum(r["tokens_in"] for r in rows)
    summary = {
        "pilot": 2, "layer": 1, "condition": "A", "n_runs": n,
        "n_tasks": len(tasks), "reps": plan["reps"],
        "model": plan["model"], "spec_sha256": plan["spec_sha256"],
        "split_seed": split["seed"],
        "gate_threshold": GATE,
        "event_rate_any_new_violation": ev,
        "event_rate_shown": rate("any_new_shown"),
        "event_rate_heldout": rate("any_new_heldout"),
        "event_rate_target_rule": rate("hit_target_rule"),
        "functional_regression_rate": {
            **rate("tests_pass"),
            "note": "tests_pass=1 means NO regression; regression rate is 1-rate",
        },
        "gate_passed": ev["rate"] >= GATE,
        "boundary_crossing_rate": rate("crossed_boundary"),
        "empty_diff_rate": rate("empty_diff"),
        "per_task": per_task,
        "non_diagnostic_tasks": non_diag,
        "diagnostic_tasks": sorted(t for t, v in per_task.items()
                                   if v["diagnostic"]),
        "tokens": {"input_total": tokens_in,
                   "input_per_cell": round(tokens_in / n) if n else 0,
                   "output_total": sum(r["tokens_out"] for r in rows)},
        "seconds_total": round(sum(r["seconds"] for r in rows)),
        "heldout_applicability": {
            t: {"n_heldout_applicable": v["n_heldout_applicable"],
                "precondition_met": v["precondition_met"]}
            for t, v in applic.items()},
    }
    # Sample size implied by the MEASURED rate, for the full study (§6).
    p_a = ev["rate"]
    summary["implied_sample_size"] = {
        "measured_control_rate": p_a,
        "per_arm_for_halving": sample_size(p_a, p_a / 2) if p_a > 0 else None,
        "per_arm_for_third": sample_size(p_a, p_a / 3) if p_a > 0 else None,
        "note": ("per-arm runs for 80% power, two-sided alpha=0.05, to detect "
                 "the control rate halved / cut to a third; None when the "
                 "measured rate is 0 and no effect size is estimable"),
    }
    n_probe = sum(r["boundary_probe_applicable"] for r in rows)
    k_probe = sum(r["crossed_boundary"] for r in rows
                  if r["boundary_probe_applicable"])
    summary["boundary_crossing_where_applicable"] = {
        "k": k_probe, "n": n_probe,
        "rate": k_probe / n_probe if n_probe else 0.0,
        "wilson95": [round(x, 4) for x in wilson(k_probe, n_probe)],
        "note": ("denominator excludes runs on the pre-modular 90f87301 tree, "
                 "which has no module boundaries to cross"),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=1, ensure_ascii=False),
                        encoding="utf-8")

    lo, hi = ev["wilson95"]
    md = [
        "# Badanie 3 — pilotaż 2 (bramka §6): czy warunek A potrafi naruszyć?",
        "",
        f"- zadania: **{len(tasks)}** (warstwa 1), powtórzenia: **{plan['reps']}**, "
        f"przebiegi: **{n}**, warunek: **A** (agent bez materiału kontraktowego)",
        f"- model: `{plan['model']}`; sha256 specyfikacji: `{plan['spec_sha256'][:16]}…`; "
        f"seed podziału reguł: `{split['seed']}`",
        f"- podział reguł: {len(split['shown_rules'])} pokazanych / "
        f"{len(split['heldout_rules'])} held-out, stratyfikacja po rodzaju",
        "",
        "## Bramka",
        "",
        f"**Odsetek przebiegów z ≥1 nowym naruszeniem: "
        f"{ev['k']}/{ev['n']} = {ev['rate']:.3f}** "
        f"(Wilson 95%: [{lo:.3f}; {hi:.3f}]); próg 0,30.",
        "",
        f"**WERDYKT: bramka {'PRZESZŁA' if summary['gate_passed'] else 'NIE PRZESZŁA'}.**",
        "",
        "| miara | k/n | odsetek | Wilson 95% |",
        "|---|---:|---:|---|",
    ]
    for label, key in (("≥1 nowe naruszenie (bramka)", "event_rate_any_new_violation"),
                       ("≥1 nowe na regułach pokazanych", "event_rate_shown"),
                       ("≥1 nowe na regułach held-out", "event_rate_heldout"),
                       ("naruszył DOKŁADNIE regułę celu", "event_rate_target_rule")):
        v = summary[key]
        md.append(f"| {label} | {v['k']}/{v['n']} | {v['rate']:.3f} | "
                  f"[{v['wilson95'][0]:.3f}; {v['wilson95'][1]:.3f}] |")
    fr = summary["functional_regression_rate"]
    md += [
        f"| regresja funkcjonalna (suite modułu) | {fr['n'] - fr['k']}/{fr['n']} | "
        f"{1 - fr['rate']:.3f} | — |",
        "",
        "## Per zadanie",
        "",
        "| zadanie | repo | reguła celu | strona | oblało | trafiło w regułę celu | "
        "przekroczyło granicę | reguły naruszone | regresje | diagnostyczne |",
        "|---|---|---|---|---:|---:|---:|---|---:|---|",
    ]
    for tid, v in per_task.items():
        md.append(
            f"| `{tid}` | {tasks[tid]['repo'].replace('litrev-', '')} | "
            f"`{v['target_rule_id']}` | {v['target_rule_side']} | "
            f"{v['n_failing_runs']}/{v['n_runs']} | {v['n_hit_target_rule']}/{v['n_runs']} | "
            f"{v['n_runs_crossing_boundary']}/{v['n_runs']} | "
            f"{', '.join(f'`{r}`' for r in v['new_rule_ids']) or '—'} | "
            f"{v['tests_regressions']}/{v['n_runs']} | "
            f"{'tak' if v['diagnostic'] else '**nie**'} |")
    md += [
        "",
        f"Zadania niediagnostyczne (0/{plan['reps']} oblanych, wypadają z pełnego "
        f"badania): {', '.join(f'`{t}`' for t in non_diag) if non_diag else 'brak'}.",
        "",
        "## Mechanizm — czy agent w ogóle podszedł do kuszonej granicy?",
        "",
        f"Przebiegi, w których agent dodał JAKIKOLWIEK import przez granicę "
        f"modułu: **{summary['boundary_crossing_where_applicable']['k']}/"
        f"{summary['boundary_crossing_where_applicable']['n']}** "
        f"({summary['boundary_crossing_where_applicable']['rate']:.3f}; "
        f"mianownik pomija {n - summary['boundary_crossing_where_applicable']['n']} "
        f"przebiegów na przedmodularnym drzewie 90f87301, gdzie granic modułów "
        f"nie ma). Przebiegi z pustym diffem (agent odmówił zmiany): "
        f"{summary['empty_diff_rate']['k']}/{n}.",
        "",
        "To rozstrzyga, dlaczego naruszeń nie ma. Reguła, która nie mogła "
        "zadziałać wobec tego, co agent napisał, to co innego niż agent, "
        "który granicę przekroczył i wszedł przez sankcjonowane wejście — "
        "z samej liczby naruszeń te dwa przypadki są nieodróżnialne.",
        "",
        "## Warunek konieczny z §4 (stosowalność reguł held-out)",
        "",
        "| zadanie | reguły held-out możliwe do naruszenia na drzewie rodzica | spełniony |",
        "|---|---:|---|",
    ]
    for t, v in applic.items():
        md.append(f"| `{t}` | {v['n_heldout_applicable']}/{len(heldout)} | "
                  f"{'tak' if v['precondition_met'] else '**nie**'} |")
    md += [
        "",
        "## Budżet",
        "",
        f"- tokeny wejściowe: {tokens_in:,} łącznie, "
        f"{summary['tokens']['input_per_cell']:,} na komórkę",
        f"- czas: {summary['seconds_total']:,} s",
        "",
    ]
    OUT_MD.write_text("\n".join(md).replace(",", ","), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in
                      ("n_runs", "event_rate_any_new_violation",
                       "event_rate_shown", "event_rate_heldout",
                       "event_rate_target_rule", "gate_passed",
                       "non_diagnostic_tasks", "implied_sample_size")},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())