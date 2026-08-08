#!/usr/bin/env python3
"""Study 3 — analysis: effect size, paired permutation test, power.

    python eval/study3_agent/analyze.py \
        --runs eval/results/study3 \
        --plan eval/results/study3_plan.json \
        --out  eval/results

Writes study3_results.csv, study3_summary.md, study3_conditions.png,
study3_conditions_data.csv, study3_power.csv.

STATISTICS
----------
The design is within-task with R repetitions, so observations are nested
(cell = task x condition, R draws each) and NOT independent.  Everything below
respects that nesting:

* The unit of analysis is the CELL MEAN — the mean over R repetitions of a
  (task, condition) pair.  This gives one number per task per condition and K
  paired differences per contrast, which is what the permutation test needs.
* PAIRED PERMUTATION TEST (exact when 2^K <= EXACT_LIMIT, else Monte-Carlo with
  N_PERM draws under a fixed seed).  The null is exchangeability of the two
  condition labels WITHIN each task, so the randomisation flips the sign of
  each task's difference independently — the same 2^K sign-flip null used for a
  paired t-test's nonparametric counterpart.  The test statistic is the mean
  paired difference.
* EFFECT SIZE with CI: the mean paired difference and Cohen's d_z (mean
  difference / SD of differences), each with a BCa-free percentile bootstrap CI
  over tasks (N_BOOT resamples of the K tasks, seeded).  Bootstrapping TASKS,
  not runs, is what makes the CI a statement about the population of tasks.
* VARIANCE DECOMPOSITION: between-task and within-cell (between-repetition)
  variance components, from a one-way random-effects decomposition per
  condition.  These are the inputs to the power analysis.
* POWER: for the observed effect, the K and R needed for 80% power under the
  paired design.  The paired SD shrinks with R because the cell mean's error
  variance is sigma_within^2 / R, so
      SD_diff(R)^2 = sigma_task_diff^2 + 2 * sigma_within^2 / R
  and required K solves
      K = ((z_{1-a/2} + z_{power}) * SD_diff(R) / delta)^2
  reported for R in 1..10.  This is a normal approximation, deliberately
  reported alongside the exact permutation p rather than instead of it.

A null result is reported as a null result: the summary states the CI and the
minimum detectable effect at the achieved K and R.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics as stats
from pathlib import Path

import pandas as pd

N_PERM = 200_000
N_BOOT = 20_000
EXACT_LIMIT = 2 ** 20          # exact enumeration up to K = 20
SEED = 20260807
CONDITIONS = ("A", "B", "C", "D")
CONTRASTS = [("A", "B"), ("A", "C"), ("A", "D"), ("B", "C"), ("B", "D"),
             ("C", "D")]
PRIMARY = ("delta_errors", "delta_errors_shown", "delta_errors_heldout")


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #

def load_runs(runs_dir: Path) -> pd.DataFrame:
    rows = []
    for p in sorted(runs_dir.glob("*/*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        base = d.get("baseline") or {}
        bc = base.get("check") or {}
        applied = [it["applied"] for it in d.get("iterations") or []]
        rows.append({
            "task_id": d["task_id"], "condition": d["condition"],
            "repetition": d["repetition"], "repo": d["repo"],
            "model": d["model"], "spec_sha256": d["spec_sha256"],
            "errors": d["final_check"]["n_errors"],
            "errors_shown": d["final_check"]["n_errors_shown"],
            "errors_heldout": d["final_check"]["n_errors_heldout"],
            "warnings": d["final_check"]["n_warnings"],
            "baseline_errors": bc.get("n_errors"),
            "baseline_errors_shown": bc.get("n_errors_shown"),
            "baseline_errors_heldout": bc.get("n_errors_heldout"),
            "delta_errors": d["delta_errors"],
            "delta_errors_shown": d["delta_errors_shown"],
            "delta_errors_heldout": d["delta_errors_heldout"],
            "tests_pass": bool(d["tests_pass"]),
            "tests_green": bool(d.get("tests_green", False)),
            "tests_new_failures": len(d.get("tests_new_failures") or []),
            "tests_lost_passes": d.get("tests_lost_passes"),
            "tests_passed": d["tests"]["n_passed"],
            "tests_failed": d["tests"]["n_failed"],
            "iterations_used": d["iterations_used"],
            "d_clean_at_iteration": d.get("d_clean_at_iteration"),
            "context_source_bytes": d["context"]["source_bytes"],
            "context_prose_bytes": d["context"]["prose_bytes"],
            "context_limen_bytes": d["context"]["limen_context_bytes"],
            "context_total_bytes": (d["context"]["source_bytes"]
                                    + d["context"]["prose_bytes"]
                                    + d["context"]["limen_context_bytes"]),
            "tokens_in": d["tokens"]["input"],
            "tokens_out": d["tokens"]["output"],
            "n_written": sum(len(a["written"]) for a in applied),
            "n_rejected": sum(len(a["rejected"]) for a in applied),
            "diff_bytes": d["diff_bytes"],
            "seconds": d["seconds"],
            "run_file": str(p.relative_to(runs_dir.parent)),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# inference
# --------------------------------------------------------------------------- #

def paired_permutation(diffs: list[float], seed: int = SEED) -> dict:
    """Two-sided sign-flip test on paired differences."""
    d = [x for x in diffs]
    k = len(d)
    if k == 0:
        return {"k": 0, "observed": None, "p_value": None, "mode": "empty"}
    obs = sum(d) / k
    if all(x == 0 for x in d):
        return {"k": k, "observed": 0.0, "p_value": 1.0, "mode": "all-zero",
                "n_perm": 0}
    target = abs(obs) - 1e-12
    if 2 ** k <= EXACT_LIMIT:
        hits = total = 0
        for signs in itertools.product((1, -1), repeat=k):
            total += 1
            if abs(sum(s * x for s, x in zip(signs, d)) / k) >= target:
                hits += 1
        return {"k": k, "observed": obs, "p_value": hits / total,
                "mode": "exact", "n_perm": total}
    import random
    rng = random.Random(seed)
    hits = 0
    for _ in range(N_PERM):
        if abs(sum(x if rng.random() < 0.5 else -x for x in d) / k) >= target:
            hits += 1
    return {"k": k, "observed": obs, "mode": "monte-carlo", "n_perm": N_PERM,
            "p_value": (hits + 1) / (N_PERM + 1)}


def bootstrap_ci(values: list[float], func, alpha: float = 0.05,
                 n_boot: int = N_BOOT, seed: int = SEED) -> tuple:
    """Percentile bootstrap CI, resampling the TASKS."""
    import random
    if not values:
        return (None, None)
    rng = random.Random(seed)
    k = len(values)
    draws = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(k)] for _ in range(k)]
        try:
            draws.append(func(sample))
        except (ZeroDivisionError, stats.StatisticsError):
            continue
    if not draws:
        return (None, None)
    draws.sort()
    lo = draws[int((alpha / 2) * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return (lo, hi)


def cohen_dz(diffs: list[float]) -> float | None:
    if len(diffs) < 2:
        return None
    sd = stats.stdev(diffs)
    if sd == 0:
        return 0.0 if stats.fmean(diffs) == 0 else math.inf
    return stats.fmean(diffs) / sd


def variance_components(cells: pd.DataFrame, metric: str) -> dict:
    """One-way random-effects split of run-level variance for one condition.

    sigma_within^2 = mean of per-cell variances over repetitions
    sigma_task^2   = variance of cell means, minus the sampling term
    """
    per_cell = cells.groupby("task_id")[metric]
    within = [g.var(ddof=1) for _, g in per_cell if g.count() > 1]
    sw2 = float(stats.fmean(within)) if within else 0.0
    means = per_cell.mean().tolist()
    r = float(stats.fmean([g.count() for _, g in per_cell])) or 1.0
    var_means = stats.variance(means) if len(means) > 1 else 0.0
    st2 = max(0.0, var_means - sw2 / r)
    return {"sigma2_within": sw2, "sigma2_task": st2, "mean_reps": r,
            "var_cell_means": var_means}


def power_table(delta: float, sigma2_task_diff: float, sigma2_within: float,
                power: float = 0.80, alpha: float = 0.05,
                r_range=range(1, 11)) -> list[dict]:
    """K required per R for 80% power on the paired difference.

    SD_diff(R)^2 = sigma_task_diff^2 + 2 * sigma_within^2 / R
    (the factor 2 is the two independent cell means being differenced)
    """
    z_a = 1.959963984540054                      # two-sided 0.05
    z_b = 0.8416212335729143                     # power 0.80
    out = []
    for r in r_range:
        sd2 = sigma2_task_diff + 2.0 * sigma2_within / r
        sd = math.sqrt(sd2)
        if delta == 0 or sd == 0:
            out.append({"R": r, "sd_diff": sd, "K_required": None})
            continue
        k = ((z_a + z_b) * sd / abs(delta)) ** 2
        out.append({"R": r, "sd_diff": sd, "K_required": math.ceil(k)})
    return out


def mde(sd_diff: float, k: int, power: float = 0.80,
        alpha: float = 0.05) -> float | None:
    """Minimum detectable effect at the achieved K and R."""
    if k < 2 or sd_diff == 0:
        return None
    z_a, z_b = 1.959963984540054, 0.8416212335729143
    return (z_a + z_b) * sd_diff / math.sqrt(k)


def wilson_ci(successes: int, n: int, z: float = 1.959963984540054) -> tuple:
    """Wilson score interval — correct at the boundary, where Wald is not.

    Needed because the event of interest here (a run introducing at least one
    new error) is rare, so the estimate sits near 0 and a Wald interval would
    be degenerate or leave [0, 1].
    """
    if n == 0:
        return (None, None)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def power_for_proportions(p1: float, p2: float, power: float = 0.80,
                          alpha: float = 0.05) -> int | None:
    """N per arm to detect p1 vs p2 (two-proportion normal approximation).

    Reported when the primary measure is a rare EVENT rather than a count with
    usable variance: with a floor at zero, "how many runs to see a difference
    in the rate at which the agent breaks a rule at all" is the answerable
    version of the power question.
    """
    if p1 == p2:
        return None
    z_a, z_b = 1.959963984540054, 0.8416212335729143
    pbar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar))
           + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p1 - p2) ** 2)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def analyse(runs_dir: Path, plan_path: Path, out_dir: Path) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    df = load_runs(runs_dir)
    assert len(df), f"no runs under {runs_dir}"
    assert df["spec_sha256"].nunique() == 1, "runs used more than one spec"

    out_dir.mkdir(parents=True, exist_ok=True)
    df.sort_values(["task_id", "condition", "repetition"]).to_csv(
        out_dir / "study3_results.csv", index=False)

    # ---- cell means (the analysis unit) ---------------------------------- #
    num = [c for c in df.columns
           if pd.api.types.is_numeric_dtype(df[c]) or df[c].dtype == bool]
    cell = (df.groupby(["task_id", "condition"])[num].mean().reset_index())
    cell.to_csv(out_dir / "study3_cell_means.csv", index=False)

    # Coverage: the planned design is K x 4 x R; a partial run must say so, and
    # every paired contrast must report how many TASKS it could actually pair.
    cov = (df.pivot_table(index="task_id", columns="condition",
                          values="repetition", aggfunc="count")
             .reindex(columns=list(CONDITIONS)).fillna(0).astype(int))
    cov.to_csv(out_dir / "study3_coverage.csv")

    summary = {
        "k_planned": int(plan["k"]),
        "r_planned": int(plan["reps"]),
        "cells_planned": int(plan["n_cells"]),
        "cells_completed": int(len(df)),
        "complete": bool(len(df) == plan["n_cells"]),
        "coverage": {t: row.to_dict() for t, row in cov.iterrows()},
        "tasks_with_all_conditions": int((cov > 0).all(axis=1).sum()),
        "k": int(df["task_id"].nunique()),
        "r": int(df.groupby(["task_id", "condition"])["repetition"].count().max()),
        "n_runs": int(len(df)),
        "model": df["model"].iloc[0],
        "temperature": plan.get("temperature"),
        "seed": plan["seed"],
        "spec_sha256": df["spec_sha256"].iloc[0],
        "heldout_rules": plan["heldout_rules"],
        "balance": df.drop_duplicates("task_id")["repo"].value_counts().to_dict(),
        "per_condition": {}, "contrasts": {}, "variance": {},
        "context": {}, "power": {}, "iterations_d": {},
    }

    for c in CONDITIONS:
        sub = df[df["condition"] == c]
        cm = cell[cell["condition"] == c]
        summary["per_condition"][c] = {
            "n_runs": int(len(sub)),
            "errors_mean": float(sub["errors"].mean()),
            "errors_median": float(sub["errors"].median()),
            "delta_mean": float(sub["delta_errors"].mean()),
            "delta_median": float(sub["delta_errors"].median()),
            "delta_shown_mean": float(sub["delta_errors_shown"].mean()),
            "delta_heldout_mean": float(sub["delta_errors_heldout"].mean()),
            "cell_mean_delta_mean": float(cm["delta_errors"].mean()),
            "tests_pass_rate": float(sub["tests_pass"].mean()),
            "tests_green_rate": float(sub["tests_green"].mean()),
            "n_runs_with_regression": int((~sub["tests_pass"]).sum()),
            "tokens_in_mean": float(sub["tokens_in"].mean()),
            "tokens_out_mean": float(sub["tokens_out"].mean()),
            "context_total_bytes_mean": float(sub["context_total_bytes"].mean()),
            "diff_bytes_median": float(sub["diff_bytes"].median()),
            "rejected_edits_total": int(sub["n_rejected"].sum()),
            "seconds_mean": float(sub["seconds"].mean()),
        }

    # ---- contrasts on the primary + held-out metrics --------------------- #
    for metric in PRIMARY + ("tests_pass",):
        piv = cell.pivot(index="task_id", columns="condition", values=metric)
        for a, b in CONTRASTS:
            if a not in piv or b not in piv:
                continue
            pair = piv[[a, b]].dropna()
            diffs = (pair[b] - pair[a]).tolist()
            perm = paired_permutation(diffs)
            m = stats.fmean(diffs) if diffs else None
            ci = bootstrap_ci(diffs, stats.fmean)
            dz = cohen_dz(diffs)
            dz_ci = bootstrap_ci(diffs, lambda s: cohen_dz(s) or 0.0)
            sd = stats.stdev(diffs) if len(diffs) > 1 else 0.0
            summary["contrasts"][f"{metric}:{a}->{b}"] = {
                "k_pairs": len(diffs),
                "mean_diff": m, "mean_diff_ci95": list(ci),
                "sd_diff": sd,
                "cohen_dz": dz, "cohen_dz_ci95": list(dz_ci),
                "permutation": perm,
                "mde_at_achieved_k": mde(sd, len(diffs)),
                "per_task_diffs": {t: float(v) for t, v in
                                   zip(pair.index, diffs)},
            }

    # ---- variance components + power ------------------------------------- #
    for c in CONDITIONS:
        summary["variance"][c] = variance_components(
            df[df["condition"] == c], "delta_errors")

    piv = cell.pivot(index="task_id", columns="condition", values="delta_errors")
    power_rows = []
    for a, b in CONTRASTS:
        if a not in piv or b not in piv:
            continue
        pair = piv[[a, b]].dropna()
        diffs = (pair[b] - pair[a]).tolist()
        obs = stats.fmean(diffs) if diffs else 0.0
        sw2 = float(stats.fmean([summary["variance"][a]["sigma2_within"],
                                 summary["variance"][b]["sigma2_within"]]))
        # variance of the TASK-level true differences, net of measurement error
        var_d = stats.variance(diffs) if len(diffs) > 1 else 0.0
        r_obs = summary["r"]
        st2_diff = max(0.0, var_d - 2.0 * sw2 / r_obs)
        tbl = power_table(obs, st2_diff, sw2)
        summary["power"][f"{a}->{b}"] = {
            "observed_effect": obs,
            "sigma2_within_pooled": sw2,
            "sigma2_task_diff": st2_diff,
            "sd_diff_observed": math.sqrt(var_d) if var_d else 0.0,
            "table": tbl,
        }
        for row in tbl:
            power_rows.append({"contrast": f"{a}->{b}",
                               "observed_effect": obs, **row})
    pd.DataFrame(power_rows).to_csv(out_dir / "study3_power.csv", index=False)

    # ---- floor-effect diagnostic ---------------------------------------- #
    # If almost every run leaves the error count unchanged, the primary measure
    # carries no variance and no amount of K or R can detect a difference in
    # its MEAN. That is a finding about the measure, so it is computed and
    # reported rather than hidden behind a p-value of 1.
    n_nonzero = int((df["delta_errors"] != 0).sum())
    summary["floor_effect"] = {
        "runs_total": int(len(df)),
        "runs_with_nonzero_delta": n_nonzero,
        "runs_with_new_error": int((df["delta_errors"] > 0).sum()),
        "runs_with_fewer_errors": int((df["delta_errors"] < 0).sum()),
        "fraction_zero": float((df["delta_errors"] == 0).mean()),
        "event_rate": float((df["delta_errors"] > 0).mean()),
        "event_rate_ci95": list(wilson_ci(int((df["delta_errors"] > 0).sum()),
                                          len(df))),
        "delta_variance_overall": float(df["delta_errors"].var(ddof=1)),
        "primary_measure_degenerate": n_nonzero <= 2,
        "per_condition_event_rate": {
            c: {"n": int((df["condition"] == c).sum()),
                "n_events": int(((df["condition"] == c)
                                 & (df["delta_errors"] > 0)).sum()),
                "rate": float(df[df["condition"] == c]["delta_errors"]
                              .gt(0).mean()) if (df["condition"] == c).any()
                else None,
                "ci95": list(wilson_ci(
                    int(((df["condition"] == c) & (df["delta_errors"] > 0)).sum()),
                    int((df["condition"] == c).sum())))}
            for c in CONDITIONS},
    }
    # Event-rate power: N per arm to detect a halving / a fixed absolute drop
    # from the observed pooled rate.
    p_obs = summary["floor_effect"]["event_rate"]
    summary["power_event_rate"] = {
        "observed_pooled_rate": p_obs,
        "n_per_arm_to_detect_halving": (
            power_for_proportions(p_obs, p_obs / 2) if p_obs > 0 else None),
        "n_per_arm_to_detect_rate_0.20_vs_0.05":
            power_for_proportions(0.20, 0.05),
        "n_per_arm_to_detect_rate_0.30_vs_0.10":
            power_for_proportions(0.30, 0.10),
    }
    # The co-primary measure (functional regression) is a proportion with real
    # variance, so unlike the primary count it supports a usable power estimate.
    p_reg = float((~df["tests_pass"]).mean())
    summary["power_regression_rate"] = {
        "regression_rate_pooled": p_reg,
        "regression_rate_ci95": list(wilson_ci(int((~df["tests_pass"]).sum()),
                                               len(df))),
        "n_runs_per_arm_0.40_vs_0.15": power_for_proportions(0.40, 0.15),
        "n_runs_per_arm_0.40_vs_0.20": power_for_proportions(0.40, 0.20),
        "n_runs_per_arm_0.20_vs_0.05": power_for_proportions(0.20, 0.05),
    }
    summary["floor_effect_note"] = (
        "delta_errors is zero in most runs and its between-task variance is 0 "
        "for every contrast, so the paired permutation test degenerates "
        "(p=1, mode='all-zero') and no K/R can detect a difference in its MEAN. "
        "The answerable power questions are the event rate (does a run "
        "introduce any new error at all) and the co-primary regression rate.")

    # ---- context reduction (secondary) ---------------------------------- #
    b_bytes = df[df["condition"] == "B"]["context_prose_bytes"]
    c_bytes = df[df["condition"] == "C"]["context_limen_bytes"]
    summary["context"] = {
        "B_prose_bytes_mean": float(b_bytes.mean()) if len(b_bytes) else None,
        "C_limen_bytes_mean": float(c_bytes.mean()) if len(c_bytes) else None,
        "reduction_factor": (float(b_bytes.mean() / c_bytes.mean())
                             if len(b_bytes) and len(c_bytes)
                             and c_bytes.mean() else None),
        "reduction_pct": (float(100 * (1 - c_bytes.mean() / b_bytes.mean()))
                          if len(b_bytes) and len(c_bytes) and b_bytes.mean()
                          else None),
        "tokens_in_mean_by_condition": {
            c: float(df[df["condition"] == c]["tokens_in"].mean())
            for c in CONDITIONS},
    }

    # ---- D loop ---------------------------------------------------------- #
    d = df[df["condition"] == "D"]
    if len(d):
        summary["iterations_d"] = {
            "iterations_mean": float(d["iterations_used"].mean()),
            "iterations_hist": d["iterations_used"].value_counts()
                                 .sort_index().to_dict(),
            "runs_reaching_max": int((d["iterations_used"]
                                      > plan["max_d_iterations"]).sum()),
        }

    (out_dir / "study3_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"df": df, "cell": cell, "summary": summary, "plan": plan}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="eval/results/study3")
    ap.add_argument("--plan", default="eval/results/study3_plan.json")
    ap.add_argument("--out", default="eval/results")
    args = ap.parse_args()
    res = analyse(Path(args.runs), Path(args.plan), Path(args.out))
    print(json.dumps({"k": res["summary"]["k"], "r": res["summary"]["r"],
                      "n_runs": res["summary"]["n_runs"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
