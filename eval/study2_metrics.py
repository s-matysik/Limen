#!/usr/bin/env python3
"""Study 2, step 12 — temporal metrics over the per-commit reports.

Reads ``eval/results/study2/**/<sha>.json`` (written by study2_history.py) and
produces:

  results/study2_timeline.csv            one row per measurement point
  results/study2_per_kind.csv            findings per rule kind (K1..K11) per point
  results/study2_findings.csv            one row per (point, finding) — the raw grain
  results/study2_lifetimes.csv           one row per finding episode, with censoring
  results/study2_sensitivity.json        identity-rule sensitivity analysis
  results/study2_exemption_lifetimes.csv actual vs declared exemption lifetime
  results/study2_metrics.json            everything the report quotes

FINDING IDENTITY (documented decision)
--------------------------------------
Line numbers move whenever anything above a violation is edited, so a key that
includes the line would report a "fix" plus a "new violation" on every unrelated
edit.  The primary identity is therefore

    (rule_id, file, target)

with ``line`` kept as an attribute, not part of the key.  ``file`` is the
snapshot-relative path (``litrev-arch-snapshot/<repo>/...``), which is stable
across commits.  ``target`` is the violated symbol/path/pattern; where limen
emits no target (K9 structure, K10 metric-threshold) it is the empty string and
the key degrades to (rule_id, file) — which is exactly right for those kinds:
a file is either oversized or not, there is no second violation of the same
rule in the same file.

The sensitivity analysis re-runs the whole episode reconstruction with the
variant key (rule_id, file, target, line) and reports how many episodes, how
many introductions, and how many repairs change.  Both numbers are reported.

TIME-TO-REPAIR AND CENSORING
----------------------------
An episode opens at the first measurement point where its key is present and
closes at the first later point where it is absent (repair time := that point's
commit date).  An episode still open at the last measurement point is
RIGHT-CENSORED: we know only that its lifetime exceeds the observed span.  The
median is estimated with Kaplan-Meier (censoring honoured) and the confidence
interval by Efron's bootstrap over episodes (percentile method, B=10000, fixed
seed).  A naive complete-case median (repaired episodes only) is reported
alongside so the size of the censoring bias is visible.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

KIND_NUMBER = {
    "layer-dependency": "K1", "ownership": "K2", "consumer-allowlist": "K3",
    "public-api-only": "K4", "scoped-allowlist": "K5", "forbidden-target": "K6",
    "baseline-snapshot": "K7", "pattern-ban": "K8", "structure": "K9",
    "metric-threshold": "K10", "reachability": "K11",
}
ALL_KINDS = [f"K{i}" for i in range(1, 12)]


def kind_label(kind: str) -> str:
    """limen emits three classes of finding.  Only the first is a rule kind:

    rule            kind in K1..K11 — a code violation of a declared rule.
    spec-integrity  rule_id 'spec-dead-entry' — an allow/exempt/expected/owners
                    entry that NO file in the tree declares.  This is an
                    assertion about the spec, not the code; on historical trees
                    it is the anachronism of L1 made visible (today's spec names
                    symbols that did not exist yet).  Identity lives in
                    spec_path, not in file/target.
    unresolved      an import the extractor could not resolve to a file in the
                    workspace.  On the per-repo axis this is inflated by
                    construction (L5).
    """
    return KIND_NUMBER.get(kind, kind)


def finding_class(f: dict) -> str:
    if f["kind"] in KIND_NUMBER:
        return "rule"
    if f["kind"] == "spec-integrity":
        return "spec-integrity"
    if f["kind"] == "unresolved":
        return "unresolved"
    return "other"


def load_points(root: Path) -> list[dict]:
    pts = []
    for p in sorted(root.rglob("*.json")):
        if p.name in {"index.json", "today_verification.json",
                      "registry_verification.json"}:
            continue
        pts.append(json.loads(p.read_text(encoding="utf-8")))
    pts.sort(key=lambda r: (r["axis"], r["variant"], r["point_repo"], r["date"]))
    return pts


def days(a: str, b: str) -> float:
    fa = datetime.fromisoformat(a)
    fb = datetime.fromisoformat(b)
    return (fb - fa).total_seconds() / 86400.0


# --------------------------------------------------------------------------- #
# episodes
# --------------------------------------------------------------------------- #

def key_primary(f: dict) -> tuple:
    """Primary identity.  spec-integrity findings carry no file/target; their
    identity is the spec path the dead entry sits at."""
    if f["kind"] == "spec-integrity":
        return (f["rule_id"], f.get("spec_path") or "", f.get("message") or "")
    return (f["rule_id"], f.get("file") or "", f.get("target") or "")


def key_with_line(f: dict) -> tuple:
    return key_primary(f) + (f.get("line"),)


def episodes(series: list[dict], keyfn, *, classes: set[str],
             exclude_absent_repo: bool) -> list[dict]:
    """Reconstruct violation episodes over an ordered series of measurement
    points.  Returns one record per episode with censoring flag."""
    open_ep: dict[tuple, dict] = {}
    out: list[dict] = []
    for pt in series:
        cur = {}
        for f in pt["findings"]:
            if finding_class(f) not in classes:
                continue
            if exclude_absent_repo and not f.get("repo_present", True):
                continue
            cur[keyfn(f)] = f
        for k, f in cur.items():
            if k not in open_ep:
                open_ep[k] = {"key": k, "rule_id": f["rule_id"],
                              "kind": f["kind"], "K": kind_label(f["kind"]),
                              "class": finding_class(f),
                              "file": f.get("file"), "target": f.get("target"),
                              "line_first": f.get("line"),
                              "severity": f["severity"],
                              "repo": f.get("repo"),
                              "introduced_sha": pt["sha"],
                              "introduced_date": pt["date"],
                              "introduced_point_repo": pt["point_repo"],
                              "n_points_seen": 0}
            open_ep[k]["n_points_seen"] += 1
            open_ep[k]["last_seen_date"] = pt["date"]
            open_ep[k]["last_seen_sha"] = pt["sha"]
        for k in list(open_ep):
            if k not in cur:
                ep = open_ep.pop(k)
                ep.update({"repaired": True, "repaired_sha": pt["sha"],
                           "repaired_date": pt["date"], "censored": False,
                           "lifetime_days": days(ep["introduced_date"],
                                                 pt["date"])})
                out.append(ep)
    last = series[-1] if series else None
    for k, ep in open_ep.items():
        ep.update({"repaired": False, "repaired_sha": None,
                   "repaired_date": None, "censored": True,
                   "lifetime_days": days(ep["introduced_date"], last["date"])})
        out.append(ep)
    out.sort(key=lambda e: (e["introduced_date"], e["rule_id"],
                            e["file"] or "", str(e["target"])))
    return out


# --------------------------------------------------------------------------- #
# survival
# --------------------------------------------------------------------------- #

def kaplan_meier(eps: list[dict]) -> dict:
    """KM survival curve with right-censoring.  Returns curve + median (the
    smallest t with S(t) <= 0.5; None if the curve never drops that far)."""
    if not eps:
        return {"curve": [], "median": None, "n": 0, "events": 0, "censored": 0}
    times = sorted({e["lifetime_days"] for e in eps if e["repaired"]})
    n = len(eps)
    at_risk = n
    surv = 1.0
    curve = []
    median = None
    for t in times:
        d = sum(1 for e in eps if e["repaired"] and e["lifetime_days"] == t)
        risk = sum(1 for e in eps if e["lifetime_days"] >= t)
        if risk == 0:
            continue
        surv *= (1 - d / risk)
        curve.append({"t": t, "survival": surv, "at_risk": risk, "events": d})
        if median is None and surv <= 0.5:
            median = t
    return {"curve": curve, "median": median, "n": n,
            "events": sum(1 for e in eps if e["repaired"]),
            "censored": sum(1 for e in eps if e["censored"])}


def bootstrap_median(eps: list[dict], b: int = 10000, seed: int = 20260804) -> dict:
    """Percentile bootstrap CI of the KM median over episodes."""
    if not eps:
        return {"point": None, "lo": None, "hi": None, "b": b,
                "resamples_with_median": 0}
    rng = random.Random(seed)
    point = kaplan_meier(eps)["median"]
    meds = []
    for _ in range(b):
        sample = [eps[rng.randrange(len(eps))] for _ in range(len(eps))]
        m = kaplan_meier(sample)["median"]
        if m is not None:
            meds.append(m)
    meds.sort()
    if not meds:
        return {"point": point, "lo": None, "hi": None, "b": b,
                "resamples_with_median": 0}
    lo = meds[int(0.025 * (len(meds) - 1))]
    hi = meds[int(0.975 * (len(meds) - 1))]
    return {"point": point, "lo": lo, "hi": hi, "b": b,
            "resamples_with_median": len(meds),
            "resamples_without_median": b - len(meds)}


def naive_median(eps: list[dict]) -> float | None:
    xs = sorted(e["lifetime_days"] for e in eps if e["repaired"])
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


# --------------------------------------------------------------------------- #
# exemption lifetimes
# --------------------------------------------------------------------------- #

def exemption_lifetimes(points: list[dict], out_csv: Path) -> dict:
    """How long the violations that today's exemptions legalise have ACTUALLY
    existed in the code.

    The exemptions themselves were authored on 2026-08-04 (all four carry
    since: 2026-08-04) and therefore have no history: `limen spec stats` on any
    historical tree reports the same four entries.  What DOES have a history is
    the debt each exemption covers.  We measure it by comparing the two
    variants: a finding present under `no-exemptions` but absent under `as-is`
    at the same measurement point is a violation that today's spec forgives.
    Its first appearance dates the debt; the span to the last point is its
    actual (right-censored) age.
    """
    by_variant = defaultdict(dict)
    for pt in points:
        if pt["axis"] != "workspace":
            continue
        by_variant[pt["variant"]][pt["sha"]] = pt
    asis, noex = by_variant.get("as-is", {}), by_variant.get("no-exemptions", {})
    shas = sorted(set(asis) & set(noex), key=lambda s: asis[s]["date"])
    first_seen: dict[tuple, dict] = {}
    last_seen: dict[tuple, str] = {}
    for sha in shas:
        a = {key_primary(f) for f in asis[sha]["findings"]
             if finding_class(f) == "rule"}
        for f in noex[sha]["findings"]:
            if finding_class(f) != "rule":
                continue
            k = key_primary(f)
            if k in a:
                continue                      # not forgiven at this point
            first_seen.setdefault(k, {"rule_id": f["rule_id"],
                                      "kind": f["kind"], "K": kind_label(f["kind"]),
                                      "file": f.get("file"),
                                      "target": f.get("target"),
                                      "first_date": noex[sha]["date"],
                                      "first_sha": sha})
            last_seen[k] = noex[sha]["date"]
    rows = []
    end = asis[shas[-1]]["date"] if shas else None
    for k, meta in first_seen.items():
        rows.append({**meta, "last_date": last_seen[k],
                     "actual_age_days": days(meta["first_date"], last_seen[k]),
                     "open_at_end": last_seen[k] == end,
                     "declared_since": "2026-08-04",
                     "declared_expires": "2026-12-31",
                     "declared_lifetime_days": 149})
    rows.sort(key=lambda r: (r["first_date"], r["rule_id"], str(r["target"])))
    if rows:
        with out_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    ages = sorted(r["actual_age_days"] for r in rows)
    med = None
    if ages:
        m = len(ages) // 2
        med = ages[m] if len(ages) % 2 else (ages[m - 1] + ages[m]) / 2
    return {"n_forgiven_findings": len(rows),
            "median_actual_age_days": med,
            "declared_median_lifetime_days": 149,
            "all_open_at_end": all(r["open_at_end"] for r in rows) if rows else None,
            "rows": rows}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--points", default=str(here / "results" / "study2"))
    ap.add_argument("--out", default=str(here / "results"))
    args = ap.parse_args()
    pts_dir, out = Path(args.points), Path(args.out)
    points = load_points(pts_dir)
    index = json.loads((pts_dir / "index.json").read_text(encoding="utf-8"))

    # ---- timeline ------------------------------------------------------- #
    timeline = []
    per_kind_rows = []
    finding_rows = []
    for pt in points:
        counts = Counter(kind_label(f["kind"]) for f in pt["findings"])
        cls = Counter(finding_class(f) for f in pt["findings"])
        real = [f for f in pt["findings"] if finding_class(f) == "rule"]
        present = [f for f in real if f.get("repo_present", True)]
        row = {
            "axis": pt["axis"], "variant": pt["variant"],
            "point_repo": pt["point_repo"], "sha": pt["sha"],
            "short_sha": pt["short_sha"], "date": pt["date"],
            "today": pt["today"], "subject": pt["subject"],
            "files_total": pt["files_total"],
            "exit_code": pt["limen"]["exit_code"],
            "errors": pt["limen"]["summary"]["errors"],
            "warnings": pt["limen"]["summary"]["warnings"],
            "unresolved": pt["limen"]["summary"]["unresolved_count"],
            "findings_total": len(pt["findings"]),
            "findings_rules_only": len(real),
            "findings_rules_present_repos": len(present),
            "findings_spec_integrity": cls.get("spec-integrity", 0),
            "findings_unresolved": cls.get("unresolved", 0),
            "units_discovered": len(pt.get("units_discovered") or []),
            "seconds": pt["limen"]["seconds"],
        }
        for r in ("host_app", "shared", "core", "frontend", "core_frontend"):
            row[f"files_{r}"] = (pt.get("files_by_root") or {}).get(r, 0)
        for k in ALL_KINDS + ["unresolved"]:
            row[f"n_{k}"] = counts.get(k, 0)
        timeline.append(row)
        for k in ALL_KINDS:
            per_kind_rows.append({"axis": pt["axis"], "variant": pt["variant"],
                                  "point_repo": pt["point_repo"],
                                  "date": pt["date"], "short_sha": pt["short_sha"],
                                  "kind": k, "n": counts.get(k, 0)})
        for f in pt["findings"]:
            finding_rows.append({
                "axis": pt["axis"], "variant": pt["variant"],
                "point_repo": pt["point_repo"], "date": pt["date"],
                "short_sha": pt["short_sha"], "rule_id": f["rule_id"],
                "kind": f["kind"], "K": kind_label(f["kind"]),
                "class": finding_class(f),
                "severity": f["severity"], "file": f.get("file"),
                "line": f.get("line"), "target": f.get("target"),
                "spec_path": f.get("spec_path"),
                "unit": f.get("unit"), "repo": f.get("repo"),
                "repo_present": f.get("repo_present"),
            })

    write_csv(out / "study2_per_kind.csv", per_kind_rows,
              ["axis", "variant", "point_repo", "date", "short_sha", "kind", "n"])
    write_csv(out / "study2_findings.csv", finding_rows,
              list(finding_rows[0].keys()))

    # ---- episodes, per axis/variant -------------------------------------- #
    metrics = {"schema": "limen-study2-metrics/1",
               "n_measurement_points": len(points),
               "n_commits_first_parent": index["n_commits_first_parent"],
               "limitations": index["limitations"],
               "identity_rule": "(rule_id, file, target); line is an attribute",
               "axes": {}}
    lifetime_rows = []
    sensitivity = {}
    for axis in sorted({p["axis"] for p in points}):
        for variant in sorted({p["variant"] for p in points}):
            series_all = [p for p in points
                          if p["axis"] == axis and p["variant"] == variant]
            if not series_all:
                continue
            groups = ({"__all__": sorted(series_all, key=lambda p: p["date"])}
                      if axis == "workspace" else
                      {r: sorted([p for p in series_all if p["point_repo"] == r],
                                 key=lambda p: p["date"])
                       for r in sorted({p["point_repo"] for p in series_all})})
            eps_p, eps_l, eps_si = [], [], []
            for gname, series in groups.items():
                eps_p += [dict(e, group=gname) for e in
                          episodes(series, key_primary, classes={"rule"},
                                   exclude_absent_repo=True)]
                eps_l += [dict(e, group=gname) for e in
                          episodes(series, key_with_line, classes={"rule"},
                                   exclude_absent_repo=True)]
                eps_si += [dict(e, group=gname) for e in
                           episodes(series, key_primary,
                                    classes={"spec-integrity"},
                                    exclude_absent_repo=False)]
            km = kaplan_meier(eps_p)
            boot = bootstrap_median(eps_p)
            per_k = Counter(e["K"] for e in eps_p)
            key = f"{axis}/{variant}"
            metrics["axes"][key] = {
                "n_points": len(series_all),
                "n_episodes": len(eps_p),
                "n_introduced": len(eps_p),
                "n_repaired": km["events"],
                "n_open_at_end_censored": km["censored"],
                "km_median_days": km["median"],
                "km_median_ci95_bootstrap": [boot["lo"], boot["hi"]],
                "bootstrap": boot,
                "naive_complete_case_median_days": naive_median(eps_p),
                # The KM median is the smallest t with S(t) <= 0.5.  When
                # lifetimes are heavily tied to a few commit dates the curve can
                # sit just ABOVE 0.5 across a long flat stretch, so the median
                # jumps to the far end of that stretch and is unstable.  The
                # curve around the crossing is exported so a reader can see it.
                "km_curve_near_median": [c for c in km["curve"]
                                         if 0.35 <= c["survival"] <= 0.65],
                "km_curve": km["curve"],
                "episodes_per_kind": {k: per_k.get(k, 0) for k in ALL_KINDS},
                "spec_integrity_episodes": len(eps_si),
                "spec_integrity_repaired": sum(1 for e in eps_si if e["repaired"]),
                "spec_integrity_open_at_end": sum(1 for e in eps_si
                                                  if e["censored"]),
                "findings_at_last_point": (
                    sorted(series_all, key=lambda p: p["date"])[-1]
                    ["limen"]["summary"]),
            }
            sensitivity[key] = {
                "episodes_primary_key": len(eps_p),
                "episodes_with_line_in_key": len(eps_l),
                "delta_episodes": len(eps_l) - len(eps_p),
                "repaired_primary": sum(1 for e in eps_p if e["repaired"]),
                "repaired_with_line": sum(1 for e in eps_l if e["repaired"]),
                "km_median_primary": kaplan_meier(eps_p)["median"],
                "km_median_with_line": kaplan_meier(eps_l)["median"],
            }
            # introduced / repaired per measurement point (the flow behind the
            # stock plotted in the figure)
            intro = Counter(e["introduced_sha"] for e in eps_p)
            fixed = Counter(e["repaired_sha"] for e in eps_p if e["repaired"])
            for row in timeline:
                if row["axis"] == axis and row["variant"] == variant:
                    row["introduced"] = intro.get(row["sha"], 0)
                    row["repaired"] = fixed.get(row["sha"], 0)
            for e in eps_p:
                lifetime_rows.append({"axis": axis, "variant": variant,
                                      "group": e["group"], **{
                    k: e[k] for k in ("rule_id", "kind", "K", "class",
                                      "severity", "repo",
                                      "file", "target", "line_first",
                                      "introduced_sha", "introduced_date",
                                      "repaired_sha", "repaired_date",
                                      "repaired", "censored", "lifetime_days",
                                      "n_points_seen")}})
    for row in timeline:
        row.setdefault("introduced", 0)
        row.setdefault("repaired", 0)
    write_csv(out / "study2_timeline.csv", timeline, list(timeline[0].keys()))
    if lifetime_rows:
        write_csv(out / "study2_lifetimes.csv", lifetime_rows,
                  list(lifetime_rows[0].keys()))
    metrics["sensitivity_line_in_key"] = sensitivity
    (out / "study2_sensitivity.json").write_text(
        json.dumps(sensitivity, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ---- exemptions ------------------------------------------------------ #
    ex = exemption_lifetimes(points, out / "study2_exemption_lifetimes.csv")
    metrics["exemptions"] = {k: v for k, v in ex.items() if k != "rows"}
    metrics["exemptions"]["spec_stats_today"] = (
        index["spec_stats"]["as-is"]["stdout"])
    metrics["exemption_manifest_removed"] = index["exemption_manifest"]["removed"]

    (out / "study2_metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items()
                      if k not in ("limitations",)}, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
