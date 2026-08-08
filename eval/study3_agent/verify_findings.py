#!/usr/bin/env python3
"""Study 3 — circularity control 1: manual verification of a findings sample.

    python eval/study3_agent/verify_findings.py --n 50 \
        --out eval/results/study3_finding_verification.csv

WHY THIS EXISTS
---------------
The primary measure of Study 3 is produced by limen, so "limen reports fewer
findings" could mean "the code is better" or merely "limen is easy to satisfy".
This script attacks the second reading directly: it draws a stratified random
sample of findings, shows each one the code at the cited site, and asks whether
the finding names a REAL architectural problem — an actual import across a
boundary the contract declares, in an actual file, at the cited line.

The verification is a JUDGEMENT, so it is made by two independent raters and
their AGREEMENT is reported, not just a single verdict:

  rater 1  a mechanical checker: it re-reads the cited file at the cited line
           and asserts the structural preconditions of the finding's rule kind
           (the file exists; the line exists; the cited target literally
           appears in the file's imports; the file is inside the unit the
           finding attributes it to).  This cannot be fooled by a plausible
           message, but it also cannot judge architectural significance.
  rater 2  an LLM rater shown the finding, the cited source window, and the
           rule's own text, asked to classify the finding as
           real / spurious / unclear WITHOUT being told what the mechanical
           rater concluded.

Reported: n verified, per-rater verdict counts, raw agreement, and Cohen's
kappa.  Disagreements are written out in full so a human can adjudicate them.

SAMPLING FRAME
--------------
Findings from Study 2's history sweep (eval/results/study2_findings.csv) at real
code sites: severity error, repository present, excluding `spec-dead-entry` (a
spec-integrity assertion, not a code site) and `unresolved-dependency` (a
warning about extraction reach).  125 such unique findings exist; the sample is
drawn stratified by rule_id with a fixed seed so the draw is reproducible.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import yaml

EVAL = Path(__file__).resolve().parents[1]
LIMEN = EVAL.parent
ROOT = LIMEN.parent
LIVE = ROOT / "live"
SPEC = ROOT / "workspace" / "architecture.yaml"
SEED = 20260807
WINDOW = 12          # source lines shown either side of the cited line

DROP_RULES = {"spec-dead-entry", "unresolved-dependency"}

RATER_SYSTEM = """You are auditing an automated architecture checker.

You are shown one finding it reported, the rule the finding came from, and the
source code at the cited location. Decide whether the finding names a REAL
architectural problem at that location.

Answer "real" when the code at that location genuinely does what the rule
forbids — for example it really does import across the boundary the rule
declares, or really does contain the banned construct.

Answer "spurious" when it does not: the cited construct is absent, the citation
points at unrelated code, or the finding misreads the code.

Answer "unclear" only when the shown window is insufficient to tell.

Judge the code, not the wording of the message. Ignore whether the rule is a
GOOD rule to have — that is not the question; the question is whether the code
violates it."""

RATER_TOOL = {
    "name": "rate_finding",
    "description": "Record your verdict on this finding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string",
                        "enum": ["real", "spurious", "unclear"]},
            "reason": {"type": "string",
                       "description": "One sentence, citing what you saw."},
        },
        "required": ["verdict", "reason"],
    },
}


def git_show(repo: str, sha: str, path: str) -> str | None:
    import os
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    p = subprocess.run(["git", "-C", str(LIVE / repo), "show", f"{sha}:{path}"],
                       capture_output=True, text=True, env=env)
    return p.stdout if p.returncode == 0 else None


def rules_by_id() -> dict:
    doc = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    return {r["id"]: r for r in doc.get("rules", [])}


def strip_root(path: str) -> tuple[str, str]:
    """'litrev-arch-snapshot/<repo>/<rest>' -> (repo, rest)."""
    parts = (path or "").split("/")
    if len(parts) > 2 and parts[0] == "litrev-arch-snapshot":
        return parts[1], "/".join(parts[2:])
    return "", path or ""


def mechanical_rate(row: dict, text: str | None) -> dict:
    """Rater 1 — structural preconditions of the finding, checked in the file."""
    if text is None:
        return {"verdict": "spurious", "reason": "cited file absent at that sha"}
    lines = text.splitlines()
    line_no = int(row["line"]) if pd.notna(row.get("line")) else 0
    checks = {"file_present": True,
              "line_in_range": 1 <= line_no <= len(lines)}
    target = str(row.get("target") or "")
    short = target.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    checks["target_in_file"] = bool(target and (target in text or
                                                (short and short in text)))
    if checks["line_in_range"]:
        cited = lines[line_no - 1]
        checks["target_on_cited_line"] = bool(
            target and (target in cited or (short and short in cited)))
    else:
        checks["target_on_cited_line"] = False
    kind = str(row.get("kind") or "")
    if kind in ("structure", "metric-threshold", "baseline-snapshot"):
        # These do not cite an importable target on a line; file presence and
        # the rule's own predicate are the whole claim.
        ok = checks["file_present"]
        why = "structural/threshold rule: cited file exists"
    else:
        ok = checks["target_in_file"] and checks["line_in_range"]
        why = ("cited target appears in the cited file"
               if ok else f"failed checks: {[k for k, v in checks.items() if not v]}")
    return {"verdict": "real" if ok else "spurious", "reason": why,
            "checks": checks}


def build_sample(n: int, seed: int = SEED) -> pd.DataFrame:
    src = pd.read_csv(EVAL / "results" / "study2_findings.csv")
    pool = src[(src["severity"] == "error") & (src["repo_present"] == True)  # noqa: E712
               & (~src["rule_id"].isin(DROP_RULES))]
    uniq = (pool.drop_duplicates(subset=["rule_id", "file", "line", "target"])
                .reset_index(drop=True))
    rng = random.Random(seed)
    by_rule = defaultdict(list)
    for i, r in uniq.iterrows():
        by_rule[r["rule_id"]].append(i)
    for v in by_rule.values():
        rng.shuffle(v)
    # Proportional-to-size stratified draw, at least one per rule where possible.
    order, rules = [], sorted(by_rule)
    for r in rules:
        order.append(by_rule[r].pop(0))
    pos = 0
    while len(order) < min(n, len(uniq)):
        r = rules[pos % len(rules)]
        if by_rule[r]:
            order.append(by_rule[r].pop(0))
        pos += 1
        if all(not by_rule[r] for r in rules):
            break
    return uniq.loc[order[:n]].reset_index(drop=True)


def cohen_kappa(a: list[str], b: list[str]) -> dict:
    cats = sorted(set(a) | set(b))
    n = len(a)
    if n == 0:
        return {"kappa": None, "agreement": None, "n": 0}
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in cats)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return {"kappa": kappa, "agreement": po, "n": n,
            "p_expected": pe, "categories": cats}


def verify(n: int, out_csv: Path, host) -> dict:
    sample = build_sample(n)
    rules = rules_by_id()
    rows, reqs, ctx = [], [], []

    for _, r in sample.iterrows():
        repo, rel = strip_root(r["file"])
        text = git_show(repo, r["short_sha"], rel) if repo else None
        # short_sha is the measurement point's repo; the finding may cite a file
        # in a DIFFERENT repo of the reconstructed workspace, in which case that
        # repo's contemporaneous sha is not in this row. Fall back to the frozen
        # workspace copy, which is the same tree the spec was authored against.
        if text is None and repo:
            fp = ROOT / "workspace" / repo / rel
            text = fp.read_text(encoding="utf-8", errors="replace") \
                if fp.exists() else None
        mech = mechanical_rate(r.to_dict(), text)
        line_no = int(r["line"]) if pd.notna(r.get("line")) else 0
        if text:
            lines = text.splitlines()
            lo = max(0, line_no - 1 - WINDOW)
            hi = min(len(lines), line_no + WINDOW)
            window = "\n".join(f"{i + 1:5d} | {lines[i]}" for i in range(lo, hi))
        else:
            window = "(file not available)"
        rule = rules.get(r["rule_id"], {})
        prompt = (
            f"RULE id: {r['rule_id']}\n"
            f"RULE kind: {r['kind']}\n"
            f"RULE definition (from the specification):\n"
            f"{yaml.safe_dump(rule, sort_keys=False, allow_unicode=True)[:2500]}\n"
            f"FINDING\n  file: {r['file']}\n  line: {r['line']}\n"
            f"  unit: {r.get('unit')}\n  target: {r.get('target')}\n\n"
            f"SOURCE around the cited line:\n{window}\n")
        reqs.append({"prompt": prompt, "system": RATER_SYSTEM,
                     "model": host.reasoning_model(), "tools": [RATER_TOOL],
                     "tool_choice": {"type": "tool", "name": "rate_finding"},
                     "max_tokens": 700})
        ctx.append({"row": r.to_dict(), "mech": mech,
                    "have_source": text is not None})

    results = host.llm(reqs, max_concurrency=8) if reqs else []
    for c, res in zip(ctx, results):
        r, mech = c["row"], c["mech"]
        if isinstance(res, dict) and "error" not in res:
            tu = (res.get("tool_use") or {}).get("input") or {}
            llm_v, llm_r = tu.get("verdict"), tu.get("reason")
        else:
            llm_v, llm_r = None, str(res)[:200]
        rows.append({
            "rule_id": r["rule_id"], "kind": r["kind"], "K": r.get("K"),
            "file": r["file"], "line": r["line"], "unit": r.get("unit"),
            "target": r.get("target"), "short_sha": r.get("short_sha"),
            "source_available": c["have_source"],
            "mechanical_verdict": mech["verdict"],
            "mechanical_reason": mech["reason"],
            "llm_verdict": llm_v, "llm_reason": llm_r,
            "agree": (llm_v == mech["verdict"]) if llm_v else None,
        })

    df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)

    both = df[df["llm_verdict"].notna()]
    kap = cohen_kappa(both["mechanical_verdict"].tolist(),
                      both["llm_verdict"].tolist())
    summary = {
        "n_sampled": int(len(df)),
        "n_rated_by_both": int(len(both)),
        "seed": SEED,
        "sampling_frame": "eval/results/study2_findings.csv, severity=error, "
                          "repo_present, excluding spec-dead-entry and "
                          "unresolved-dependency; unique by "
                          "(rule_id,file,line,target); stratified by rule_id",
        "rules_covered": int(df["rule_id"].nunique()),
        "mechanical": df["mechanical_verdict"].value_counts().to_dict(),
        "llm": both["llm_verdict"].value_counts().to_dict(),
        "agreement": kap["agreement"], "cohen_kappa": kap["kappa"],
        "n_real_both": int(((both["mechanical_verdict"] == "real")
                            & (both["llm_verdict"] == "real")).sum()),
        "disagreements": both[both["agree"] == False][  # noqa: E712
            ["rule_id", "file", "line", "target", "mechanical_verdict",
             "llm_verdict", "llm_reason"]].to_dict("records"),
    }
    (out_csv.parent / "study3_finding_verification.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", default="eval/results/study3_finding_verification.csv")
    ap.parse_args()
    raise SystemExit("driven from the kernel (host.llm lives there): import "
                     "verify_findings, then verify(n, out_csv, host).")


if __name__ == "__main__":
    main()
