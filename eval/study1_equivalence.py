#!/usr/bin/env python3
"""Study 1, step 6 — diff the legacy oracle against limen and classify every
difference.

This is a **gate**, not a report. Every difference between the two finding sets
must fall into exactly one of three categories; if a single difference is left
unclassified the script exits non-zero.

    C1  legacy false alarm removed
        The legacy checker reported something limen does not, and the legacy
        finding's own source line shows why: the matched text is inside a
        comment or a string literal. The legacy checkers are `preg_match_all`
        over raw file contents (`ArchitectureRulesTestCase::phpFilesIn` +
        `file_get_contents`, no comment stripping); limen's extractors parse
        `use`/`import` statements out of code only. Decided by
        `classify_c1()`, whose evidence is the quoted source line, the byte
        offset of the match and the lexical span class at that offset.

    C2  legacy gap closed
        limen reported something legacy did not, and the constraint is stated
        in prose (AGENTS.md / CLAUDE.md / docs/architecture/**) that no legacy
        checker enforced. Decided by `classify_c2()` against `DOC_EVIDENCE`;
        the evidence is verified at runtime — the documentation file is read,
        the pattern must match, and the matching line is quoted into the
        report. A registry entry whose pattern no longer matches does **not**
        classify: it degrades to unclassified and the gate fails.

    C3  deliberate semantic change
        The difference is a recorded deviation in `docs/deviations.md`.
        Decided by `classify_c3()` against `DEVIATION_INDEX`; the referenced
        D-nn heading must exist in the file or the classification is refused.

Two further outcomes are not "differences" and are counted separately, never
folded into the three categories:

    AGREEMENT   the same (file, line, target) key is present on both sides.
    OUT-OF-SCOPE  a finding that is not a contract-rule judgement at all and
        therefore cannot have a counterpart on the other side. Decided by
        `in_comparison_scope()` — see the docstring there for the rule and its
        justification. Out-of-scope findings are listed in full in the report.

Comparison key: (file, line, target), canonicalised by
`run_limen_check.canonical_file` / `canonical_target` so that limen's
spec-root paths and the legacy checkers' repo-relative paths meet in one
workspace-relative namespace.

Usage:

    python eval/study1_equivalence.py \
        --limen  eval/results/limen_findings.json \
        --legacy eval/results/legacy_findings.json \
        --workspace <LIMEN>/workspace \
        --deviations docs/deviations.md \
        --out eval/results/study1_diff.json

Exit codes: 0 = every difference classified; 1 = at least one unclassified
difference; 4 = the legacy oracle file is missing (unless
`--missing-oracle assume-empty` is given, which records the assumption in the
report and keeps the gate running on the limen-only side).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_limen_check import canonical_file, canonical_target, finding_key, key_str  # noqa: E402

SCHEMA = "study1-diff/1"

CATEGORIES = {
    "C1": "legacy false alarm removed",
    "C2": "legacy gap closed",
    "C3": "deliberate semantic change",
}

# ==========================================================================
# Scope of the comparison
# ==========================================================================
#
# A finding takes part in the diff only if it is a *judgement of a declared
# contract rule*. limen additionally emits engine diagnostics that are not
# rule verdicts:
#
#   * `unresolved-dependency` (kind `unresolved`, `spec_path:
#     "unresolved_policy"`) states that the extractor could not statically
#     resolve a dependency edge. It is a statement about the *extractor's*
#     coverage, not about the architecture.
#
# The legacy checkers cannot produce such a statement even in principle: they
# are regex matchers that either match a pattern or do not, and a dependency
# they fail to recognise simply produces no finding. There is also no prose
# rule requiring imports to be statically resolvable (probed: no occurrence of
# "unresolved"/"static resolution" in any AGENTS.md, CLAUDE.md, README.md or
# docs/architecture/** of the six repositories). Classifying these as C2
# ("gap closed") would assert a legacy rule that never existed in any form.
#
# They are therefore OUT OF SCOPE, counted and listed separately, never
# silently dropped.
RULE_VERDICT_SPEC_PATH = re.compile(r"^rules\[\d+\]")


def in_comparison_scope(finding: dict[str, Any]) -> tuple[bool, str]:
    """Is this finding a contract-rule judgement comparable across the two tools?

    Returns (in_scope, reason).
    """
    if finding.get("source") != "limen":
        # Every oracle finding must carry a rule identity proving it came from
        # a declared rule. Study 1's legacy oracle supplies
        # `legacy_rule_id`/`inventory_id`; Study 1b's native checkers supply
        # `native_rule_id`. A finding with no rule identity is not a rule
        # verdict and cannot have a counterpart.
        source = finding.get("source") or "oracle"
        if (finding.get(f"{source}_rule_id") or finding.get("legacy_rule_id")
                or finding.get("native_rule_id") or finding.get("inventory_id")):
            return True, f"{source} rule verdict"
        return False, f"{source} finding carries no rule identity"
    spec_path = finding.get("spec_path") or ""
    if RULE_VERDICT_SPEC_PATH.match(spec_path):
        return True, f"rule verdict (spec_path={spec_path})"
    return False, (
        f"engine diagnostic, not a contract rule (spec_path={spec_path or 'null'}); "
        "the legacy checkers are pattern matchers and cannot express this "
        "judgement, so no counterpart is possible by construction"
    )


# ==========================================================================
# C2 evidence registry — prose rules that no legacy checker enforced
# ==========================================================================
# Each entry names a documentation file (workspace-relative) and a regex that
# must match a line in it. The matching line is quoted into the report.
# Entries are keyed by limen rule_id.
# The documentation of the studied repositories is written in Polish, so some
# patterns below are verbatim quotations of Polish sentences. They are data,
# not prose: translating them would stop the pattern matching the file under
# study and silently drop the evidence.
DOC_EVIDENCE: dict[str, list[dict[str, str]]] = {
    "host-cross-module-public-surface": [
        {
            "file": "backend-shared/CLAUDE.md",
            "pattern": r"Forbidden:.*importing concrete Application classes from a sibling module",
            "claim": "backend-shared states that concrete Application classes of another "
                     "module are forbidden across a module boundary and need an explicit "
                     "allowlist entry; no legacy checker applied this to imports coming "
                     "from the host app or from core-backend.",
        },
        {
            "file": "backend-shared/src/AGENTS.md",
            "pattern": r"Między modułami preferuj",
            "claim": "backend-shared/src/AGENTS.md enumerates the public surface "
                     "(Application\\Contracts, Application\\Events, Domain, Presentation) "
                     "as the only cross-module entry.",
        },
        {
            "file": "litrev-backend/app/Modules/AGENTS.md",
            "pattern": r"Między modułami używaj kontraktów, interfejsów albo fasad modułowych",
            "claim": "the host app's own module contract requires cross-module "
                     "communication through contracts/interfaces/facades, without "
                     "restricting the statement to the App\\Modules namespace.",
        },
    ],
}

# Legacy-side blindness that makes the above a *gap* rather than a
# disagreement: the regexes each legacy checker uses, and the namespace they
# are anchored to. Verified at runtime against the legacy source file.
LEGACY_BLINDNESS: dict[str, list[dict[str, str]]] = {
    "host-cross-module-public-surface": [
        {
            "file": "litrev-backend/tests/Architecture/ApplicationBoundaryRulesTest.php",
            "pattern": r"preg_match_all\('/use\\s\+App\\\\\\\\Modules",
            "claim": "the cross-module Application check is anchored to `App\\Modules\\`, "
                     "so an import of a concrete `LitRev\\BackendShared\\...\\Application\\` "
                     "service never matches.",
        },
        {
            "file": "litrev-backend/tests/Architecture/HttpBoundaryRulesTest.php",
            "pattern": r"preg_match_all\('/use\\s\+App\\\\\\\\Modules",
            "claim": "the Http boundary check is anchored to `App\\Modules\\` as well.",
        },
    ],
}


# ==========================================================================
# C3 registry — recorded deviations
# ==========================================================================
# rule_id (limen or legacy) -> deviation ids that cover it. Every id must
# resolve to a `## D-nn` heading in docs/deviations.md or the classification
# is refused.
DEVIATION_INDEX: dict[str, list[str]] = {
    # D-08 records that the retrofit is deliberately stricter here. It is
    # listed so a reviewer sees the link, but it does NOT classify the
    # difference: D-08 itself states the findings are kept as study material,
    # i.e. the *cause* is the legacy blind spot (C2), not a semantic choice.
    # See C3_REQUIRES_SEMANTIC_CHANGE below.
}

# A deviation classifies a difference as C3 only when the deviation changes
# what is *forbidden*. Deviations that merely document a legacy defect the
# retrofit exposes are evidence for C2, not C3. The distinction is encoded as
# a required deviation kind parsed from the heading.
C3_DEVIATION_KINDS = {"engine-gap", "editorial"}


# ==========================================================================
# Lexical span analysis (C1 evidence)
# ==========================================================================
CODE, COMMENT, STRING = "code", "comment", "string"


def span_classes(text: str, language: str) -> list[str]:
    """Classify every character of `text` as code / comment / string.

    A small hand-rolled scanner, deliberately independent of limen's
    extractors so the C1 evidence is not circular. Handles: `//` and `#`
    line comments (PHP 8 attributes `#[` are code), `/* */` blocks, single-
    and double-quoted strings with backslash escapes, and JS template
    literals.
    """
    out = [CODE] * len(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = COMMENT
            i = j
        elif c == "#" and language == "php" and two != "#[":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = COMMENT
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = COMMENT
            i = j
        elif c in "'\"" or (c == "`" and language != "php"):
            quote = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                out[k] = STRING
            i = j
        else:
            i += 1
    return out


def language_of(path: str) -> str:
    if path.endswith(".php"):
        return "php"
    return "js"


def line_evidence(workspace: Path, file: str, line: int | None,
                  target: str | None) -> dict[str, Any]:
    """Quote the source line behind a finding and classify the match lexically."""
    ev: dict[str, Any] = {
        "file": file, "line": line, "resolved": False,
        "source_line": None, "match_offset": None, "span_class": None,
    }
    if not file or line is None:
        ev["note"] = "finding carries no file/line"
        return ev
    p = workspace / file
    if not p.is_file():
        ev["note"] = f"file not found in workspace: {p}"
        return ev
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if not (1 <= line <= len(lines)):
        ev["note"] = f"line {line} outside file ({len(lines)} lines)"
        return ev
    start = sum(len(x) for x in lines[:line - 1])
    src = lines[line - 1].rstrip("\n")
    ev["resolved"] = True
    ev["source_line"] = src
    classes = span_classes(text, language_of(file))

    needle = None
    if target:
        for cand in (target, target.replace("\\", "\\\\"), target.split("\\")[-1],
                     target.split("/")[-1]):
            if cand and cand in src:
                needle = cand
                break
    if needle is None:
        m = re.search(r"\b(use|import|require|from)\b", src)
        needle = m.group(0) if m else None
    if needle is None:
        ev["note"] = "neither the target nor an import keyword occurs on the line"
        ev["span_class"] = "not-found"
        return ev
    off = src.index(needle)
    ev["match_offset"] = off
    ev["matched_text"] = needle
    seg = classes[start + off: start + off + len(needle)]
    uniq = sorted(set(seg))
    ev["span_class"] = uniq[0] if len(uniq) == 1 else "mixed:" + "+".join(uniq)
    return ev


# ==========================================================================
# Classifiers — each returns a verdict dict or None
# ==========================================================================
def classify_c1(finding: dict[str, Any], workspace: Path) -> dict[str, Any] | None:
    """Legacy-only difference whose matched text is not code.

    Decision rule: the finding comes from the legacy side, its source line
    resolves in the frozen workspace, and the lexical span containing the
    matched text is a comment or a string literal. limen's extractors read
    code only, so the legacy hit is a false alarm the retrofit removes.
    """
    if finding.get("source") != "legacy":
        return None
    ev = line_evidence(workspace, finding.get("file"), finding.get("line"),
                       finding.get("target"))
    if not ev["resolved"]:
        return None
    span = ev.get("span_class") or ""
    if span == COMMENT or span == STRING or span.startswith("mixed:"):
        if span == COMMENT or span == STRING:
            why = f"the matched text lies inside a {span}"
        else:
            why = f"the matched text straddles {span[len('mixed:'):]} spans"
        return {
            "category": "C1",
            "category_label": CATEGORIES["C1"],
            "rule": "classify_c1: legacy match is not in code",
            "rationale": (
                f"{why}; the legacy checkers run preg_match_all over raw "
                "file_get_contents output and never strip comments or strings, "
                "while limen's extractors emit dependencies from code only."
            ),
            "evidence": {"source_line": ev},
        }
    return None


def classify_c2(finding: dict[str, Any], workspace: Path) -> dict[str, Any] | None:
    """limen-only difference backed by a prose rule no legacy checker enforced.

    Decision rule: the finding comes from limen, its rule_id has an entry in
    DOC_EVIDENCE, and at least one documented pattern actually matches in the
    frozen workspace right now. The legacy blind spot, where registered, is
    verified the same way. Registry entries that no longer match do not
    classify.
    """
    if finding.get("source") != "limen":
        return None
    rid = finding.get("rule_id")
    probes = DOC_EVIDENCE.get(rid or "")
    if not probes:
        return None
    hits = _probe(workspace, probes)
    if not hits:
        return None
    registered_blindness = LEGACY_BLINDNESS.get(rid or "", [])
    blind = _probe(workspace, registered_blindness)
    if registered_blindness and not blind:
        # The registry claims a legacy blind spot but the claimed regex is no
        # longer in the legacy source. Refuse to classify rather than assert
        # an unverified gap.
        return None
    return {
        "category": "C2",
        "category_label": CATEGORIES["C2"],
        "rule": "classify_c2: prose rule exists, no legacy checker enforced it",
        "rationale": (
            f"the constraint behind `{rid}` is stated in {len(hits)} documentation "
            "location(s) verified in this run; the legacy checkers that would have "
            "had to enforce it are anchored to a namespace that cannot match the "
            "offending import, so the rule existed only in prose."
        ),
        "evidence": {
            "documentation": hits,
            "legacy_blindness": blind,
            "source_line": line_evidence(workspace, finding.get("file"),
                                         finding.get("line"), finding.get("target")),
        },
    }


def classify_c3(finding: dict[str, Any], deviations: dict[str, dict[str, str]]
                ) -> dict[str, Any] | None:
    """Difference covered by a recorded deviation that changes the semantics.

    Decision rule: the finding's rule id appears in DEVIATION_INDEX, the
    referenced `D-nn` heading exists in docs/deviations.md, and the
    deviation's declared kind is one that changes what the contract forbids
    (engine-gap or editorial). A `legacy-defect` deviation documents a defect
    the retrofit *exposes*; that is evidence for C2, not a semantic change.
    """
    rid = finding.get("rule_id") or finding.get("legacy_rule_id")
    ids = DEVIATION_INDEX.get(rid or "")
    if not ids:
        return None
    resolved, kinds = [], []
    for d in ids:
        if d not in deviations:
            return None  # dangling reference: refuse to classify
        resolved.append({"id": d, **deviations[d]})
        kinds.append(deviations[d]["kind"])
    if not any(k in C3_DEVIATION_KINDS for k in kinds):
        return None
    return {
        "category": "C3",
        "category_label": CATEGORIES["C3"],
        "rule": "classify_c3: covered by a recorded deviation of a semantic kind",
        "rationale": f"deviation(s) {', '.join(ids)} record the deliberate change.",
        "evidence": {"deviations": resolved},
    }


def _probe(workspace: Path, probes: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for probe in probes:
        p = workspace / probe["file"]
        if not p.is_file():
            continue
        rx = re.compile(probe["pattern"])
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace")
                               .splitlines(), start=1):
            if rx.search(ln):
                hits.append({
                    "file": probe["file"], "line": i,
                    "quote": ln.strip()[:400],
                    "claim": probe["claim"],
                })
                break
    return hits


def parse_deviations(path: Path) -> dict[str, dict[str, str]]:
    """Parse `## D-nn — title (kind)` headings out of docs/deviations.md."""
    out: dict[str, dict[str, str]] = {}
    rx = re.compile(r"^##\s+(D-\d+)\s+—\s+(.*?)\s*(?:\(([^)]*)\))?\s*$")
    for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = rx.match(ln)
        if m:
            kinds = [k.strip() for k in (m.group(3) or "").split(",")]
            out[m.group(1)] = {
                "title": m.group(2),
                "kind": kinds[0] if kinds else "",
                "kinds": ", ".join(k for k in kinds if k),
                "heading_line": str(i),
                "source": str(path),
            }
    return out


# ==========================================================================
def load_side(path: Path, expect_tool: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for f in doc.get("findings", []):
        g = dict(f)
        g.setdefault("source", expect_tool)
        key = finding_key(g)
        g["file"], g["line"], g["target"] = key
        g["key"] = key_str(key)
        rows.append(g)
    meta = {k: v for k, v in doc.items() if k != "findings"}
    return rows, meta


# ==========================================================================
# Reusable diff core
# ==========================================================================
# Study 1b (eval/study1b_corpus.py) runs the *same* comparison against a
# corpus of third-party repositories, where the oracle is that repository's own
# native checker rather than the LitRev legacy tests. Only the classifiers
# differ (the C1/C2/C3 evidence for a deptrac config is not the evidence for a
# PHPUnit regex test); the scoping, keying, set algebra, tally and gate are
# identical and live here, so the two studies cannot drift apart.
#
# `main()` below calls this function, so every behaviour asserted by
# eval/tests/test_study1_equivalence.py exercises this code path.
def diff_finding_sets(
    limen_rows: list[dict[str, Any]],
    oracle_rows: list[dict[str, Any]],
    classifiers,
    *,
    oracle_label: str = "legacy",
) -> dict[str, Any]:
    """Scope, key, diff and classify two finding sets.

    `classifiers` is a sequence of callables ``f(finding) -> verdict|None``;
    the first non-None verdict classifies the difference. A difference no
    classifier claims is reported as UNCLASSIFIED and makes the caller's gate
    fail — the categories are never widened to absorb a surprise.

    Returns the block of the report that describes the comparison itself:
    counts, agreements, out_of_scope and differences. Provenance (snapshot
    identity, input paths) stays with the caller, which knows what it ran.
    """
    def split(rows):
        keep, drop = [], []
        for r in rows:
            ok, why = in_comparison_scope(r)
            r["_scope_reason"] = why
            (keep if ok else drop).append(r)
        return keep, drop

    limen_in, limen_out = split(limen_rows)
    oracle_in, oracle_out = split(oracle_rows)

    limen_by_key = {r["key"]: r for r in limen_in}
    oracle_by_key = {r["key"]: r for r in oracle_in}

    agreements = sorted(set(limen_by_key) & set(oracle_by_key))
    limen_only = sorted(set(limen_by_key) - set(oracle_by_key))
    oracle_only = sorted(set(oracle_by_key) - set(limen_by_key))

    differences: list[dict[str, Any]] = []
    for key in oracle_only + limen_only:
        f = oracle_by_key.get(key) or limen_by_key[key]
        verdict = None
        for classify in classifiers:
            verdict = classify(f)
            if verdict is not None:
                break
        differences.append({
            "key": key,
            "direction": f"{oracle_label}-only" if key in oracle_by_key else "limen-only",
            "source": f.get("source"),
            "rule_id": f.get("rule_id") or f.get(f"{oracle_label}_rule_id"),
            "severity": f.get("severity"),
            "file": f.get("file"),
            "line": f.get("line"),
            "target": f.get("target"),
            "message": f.get("message"),
            "classification": verdict or {
                "category": None,
                "category_label": "UNCLASSIFIED",
                "rule": None,
                "rationale": "no decision rule matched this difference",
                "evidence": {},
            },
        })
    differences.sort(key=lambda d: (d["direction"], d["key"]))

    per_category = {c: 0 for c in CATEGORIES}
    unclassified = 0
    for d in differences:
        c = d["classification"]["category"]
        if c in per_category:
            per_category[c] += 1
        else:
            unclassified += 1

    return {
        "counts": {
            "limen_findings_total": len(limen_rows),
            "limen_findings_in_scope": len(limen_in),
            "limen_findings_out_of_scope": len(limen_out),
            f"{oracle_label}_findings_total": len(oracle_rows),
            f"{oracle_label}_findings_in_scope": len(oracle_in),
            f"{oracle_label}_findings_out_of_scope": len(oracle_out),
            "agreements": len(agreements),
            "differences_total": len(differences),
            f"differences_{oracle_label}_only": len(oracle_only),
            "differences_limen_only": len(limen_only),
            "per_category": per_category,
            "unclassified": unclassified,
        },
        "agreements": [{"key": k,
                        "limen_rule_id": limen_by_key[k].get("rule_id"),
                        f"{oracle_label}_rule_id":
                            oracle_by_key[k].get(f"{oracle_label}_rule_id")
                            or oracle_by_key[k].get("rule_id")}
                       for k in agreements],
        "out_of_scope": [{
            "source": r.get("source"), "key": r["key"],
            "rule_id": r.get("rule_id") or r.get(f"{oracle_label}_rule_id"),
            "severity": r.get("severity"),
            "spec_path": r.get("spec_path"),
            "reason": r["_scope_reason"],
            "message": r.get("message"),
        } for r in limen_out + oracle_out],
        "differences": differences,
    }


def snapshot_id(meta: dict[str, Any]) -> str | None:
    """Pull the workspace tree-id out of either side's snapshot block.

    limen side writes `snapshot.workspace_id` (read from frozen-commits.txt);
    the legacy oracle writes `snapshot.workspace_tree_id`. Both denote the
    same sha256 over the manifest of manifests.
    """
    snap = meta.get("snapshot") or {}
    return snap.get("workspace_id") or snap.get("workspace_tree_id")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limen", default="eval/results/limen_findings.json")
    ap.add_argument("--legacy", default="eval/results/legacy_findings.json")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--deviations", default="docs/deviations.md")
    ap.add_argument("--out", default="eval/results/study1_diff.json")
    ap.add_argument("--missing-oracle", choices=["fail", "assume-empty"],
                    default="fail",
                    help="what to do when the legacy oracle file is absent")
    args = ap.parse_args()

    workspace = Path(args.workspace).resolve()
    limen_rows, limen_meta = load_side(Path(args.limen), "limen")

    legacy_path = Path(args.legacy)
    oracle_available = legacy_path.is_file()
    if not oracle_available and args.missing_oracle == "fail":
        sys.stderr.write(
            f"legacy oracle not found: {legacy_path}\n"
            "the diff cannot be certified; re-run with "
            "--missing-oracle assume-empty to analyse the limen-only side\n")
        return 4
    if oracle_available:
        legacy_rows, legacy_meta = load_side(legacy_path, "legacy")
    else:
        legacy_rows, legacy_meta = [], {}

    # Both sides must describe the same frozen snapshot, or the diff compares
    # two different trees and every count below is meaningless.
    limen_snap, legacy_snap = snapshot_id(limen_meta), snapshot_id(legacy_meta)
    snapshot_match = (not oracle_available) or (
        limen_snap is not None and limen_snap == legacy_snap)
    if oracle_available and not snapshot_match:
        sys.stderr.write(
            "snapshot mismatch: limen side reports workspace-id "
            f"{limen_snap!r}, legacy oracle reports {legacy_snap!r}; "
            "the two finding sets do not describe the same tree\n")
        return 4

    deviations = parse_deviations(Path(args.deviations))

    # -- scope, diff, classify (shared with Study 1b) ----------------------
    # The classifiers are bound to this study's evidence sources; the diff
    # machinery itself is `diff_finding_sets`, which Study 1b reuses verbatim.
    core = diff_finding_sets(
        limen_rows, legacy_rows,
        classifiers=(
            lambda f: classify_c1(f, workspace),
            lambda f: classify_c2(f, workspace),
            lambda f: classify_c3(f, deviations),
        ),
        oracle_label="legacy",
    )
    # Study 1 quotes the source line behind an unclassified difference; the
    # shared core cannot, because it does not know where the tree is.
    for d in core["differences"]:
        if d["classification"]["category"] is None:
            d["classification"]["evidence"] = {"source_line": line_evidence(
                workspace, d.get("file"), d.get("line"), d.get("target"))}
    differences = core["differences"]
    per_category = core["counts"]["per_category"]
    unclassified = core["counts"]["unclassified"]

    payload = {
        "schema": SCHEMA,
        "workspace": str(workspace),
        "inputs": {
            "limen": str(Path(args.limen)),
            "legacy": str(legacy_path),
            "deviations": str(Path(args.deviations)),
        },
        "snapshot": {
            "limen_workspace_id": limen_snap,
            "legacy_workspace_id": legacy_snap,
            "match": snapshot_match,
        },
        "legacy_oracle_provenance": {
            k: legacy_meta.get(k) for k in ("schema", "tool", "counts")
        } | ({"php": {kk: vv for kk, vv in
                      (legacy_meta.get("oracle", {}).get("php") or {}).items()
                      if kk != "executions"}} if legacy_meta.get("oracle") else {}),
        "oracle_available": oracle_available,
        "provisional": not oracle_available,
        "provisional_note": (
            None if oracle_available else
            "the legacy oracle file was absent; the legacy finding set was "
            "assumed empty. Every number below describes the limen-only side "
            "and must be recomputed once the oracle lands."
        ),
        "comparison_key": ["file", "line", "target"],
        "scope_rule": (
            "a finding takes part in the diff only if it is a contract-rule "
            "verdict (limen: spec_path matches rules[n]; legacy: the finding "
            "carries a legacy rule identity). Engine diagnostics are listed "
            "under out_of_scope and counted separately."
        ),
        "categories": CATEGORIES,
        "counts": core["counts"],
        "agreements": core["agreements"],
        "out_of_scope": core["out_of_scope"],
        "differences": differences,
        "deviations_seen": sorted(deviations),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n", encoding="utf-8")

    c = payload["counts"]
    print(f"{out}")
    print(f"  limen   : {c['limen_findings_total']} findings "
          f"({c['limen_findings_in_scope']} in scope, "
          f"{c['limen_findings_out_of_scope']} out of scope)")
    print(f"  legacy  : {c['legacy_findings_total']} findings "
          f"({c['legacy_findings_in_scope']} in scope, "
          f"{c['legacy_findings_out_of_scope']} out of scope)"
          + ("  [ASSUMED EMPTY — oracle absent]" if not oracle_available else ""))
    print(f"  agree   : {c['agreements']}")
    print(f"  differ  : {c['differences_total']} "
          f"(legacy-only {c['differences_legacy_only']}, "
          f"limen-only {c['differences_limen_only']})")
    for cat, label in CATEGORIES.items():
        print(f"    {cat} {label:<32} {per_category[cat]}")
    print(f"    unclassified                       {unclassified}")
    if unclassified:
        print("GATE FAILED: unclassified differences remain", file=sys.stderr)
        for d in differences:
            if d["classification"]["category"] is None:
                print(f"  {d['direction']} {d['key']} ({d['rule_id']})",
                      file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
