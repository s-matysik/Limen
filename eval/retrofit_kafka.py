#!/usr/bin/env python3
"""Study 1b, step 9 — bespoke retrofit #1: Apache Kafka / checkstyle ImportControl.

Kafka governs its Java module boundaries with ``checkstyle/import-control.xml``
(a hand-written 675-line policy), enforced by the ``ImportControl`` check in
checkstyle. That checker is **executed here**, from the official
``checkstyle-N-all.jar``, over the same file set limen sees — so the oracle is
the real tool on a real third-party repository, not a reimplementation.

What this script does:

1. runs checkstyle's ImportControl over the target source root and parses its
   XML report into findings;
2. translates ``import-control.xml`` into an ``architecture.yaml`` with
   ``eval/translate/import_control.py`` (K6 forbidden-target per package
   scope, forbid-lists computed by evaluating the native cascade over the
   import targets actually present in the tree);
3. runs ``limen check`` on the same tree;
4. writes both finding sets in the shape ``study1_equivalence.diff_finding_sets``
   consumes.

Kafka's tree at the pinned SHA is clean — both tools report zero violations,
which measures false positives but says nothing about detection. So the script
also runs a **seeded-violation** arm: it injects known-illegal imports into
copies of real files, one per injection, and checks that both tools flag them.
That is what makes recall measurable on a third-party repository whose native
gate is green.

Usage:

    python eval/retrofit_kafka.py \
        --repo   eval/_scratch/repos/apache-kafka \
        --module clients/src/main/java \
        --java   <path to java> --checkstyle <path to checkstyle-all.jar> \
        --out    eval/results/bespoke_retrofits/kafka
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL / "translate"))

import import_control as IC  # noqa: E402

SCHEMA = "bespoke-retrofit/1"
CHECKSTYLE_CONFIG = """<?xml version="1.0"?>
<!DOCTYPE module PUBLIC "-//Checkstyle//DTD Checkstyle Configuration 1.3//EN"
  "https://checkstyle.org/dtds/configuration_1_3.dtd">
<module name="Checker">
  <property name="severity" value="error"/>
  <module name="TreeWalker">
    <module name="ImportControl">
      <property name="file" value="${importControlFile}"/>
    </module>
  </module>
</module>
"""

_PKG_RX = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_IMPORT_RX = re.compile(
    r"^\s*import\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")


def strip_comments(text: str) -> str:
    """Blank out comments, preserving offsets so line numbers stay exact."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return _LINE_COMMENT.sub(blank, _BLOCK_COMMENT.sub(blank, text))


def java_facts(root: Path) -> list[dict[str, Any]]:
    """(path, package, [(target, line, is_static)]) for every .java file."""
    out = []
    for p in sorted(root.rglob("*.java")):
        text = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        m = _PKG_RX.search(text)
        pkg = m.group(1) if m else ""
        imports = []
        for im in _IMPORT_RX.finditer(text):
            line = text.count("\n", 0, im.start()) + 1
            imports.append((im.group(1), line))
        out.append({"path": str(p), "package": pkg, "imports": imports})
    return out


# ==========================================================================
# Oracle: run real checkstyle
# ==========================================================================
def run_checkstyle(java: str, jar: str, control: Path, target: Path,
                   workdir: Path, tree_root: Path | None = None
                   ) -> tuple[list[dict], dict]:
    """Run checkstyle's ImportControl and normalise its report.

    checkstyle reports absolute paths; limen reports paths relative to the
    workspace root. `tree_root` is that root, so both sides meet in one
    namespace and the comparison key (file, line, target) is meaningful.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = workdir / "checkstyle-importcontrol.xml"
    cfg.write_text(CHECKSTYLE_CONFIG, encoding="utf-8")
    props = workdir / "checkstyle.properties"
    props.write_text(f"importControlFile={control}\n", encoding="utf-8")
    cmd = [java, "-jar", jar, "-c", str(cfg), "-p", str(props), "-f", "xml",
           str(target)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    findings: list[dict] = []
    try:
        root = ET.fromstring(proc.stdout)
    except ET.ParseError as e:
        raise RuntimeError(f"checkstyle output is not XML: {e}\n"
                           f"{proc.stdout[:800]}\n{proc.stderr[:800]}")
    for fel in root.iter("file"):
        fname = fel.get("name") or ""
        for err in fel.iter("error"):
            if "ImportControl" not in (err.get("source") or ""):
                continue
            msg = err.get("message") or ""
            # checkstyle 13.x: "Disallowed import - a.b.C." (no quotes).
            # Older/localised builds quote the name; accept both shapes rather
            # than silently yielding target=None, which would make every
            # comparison key mismatch and fake a total disagreement.
            # The trailing "." is the sentence's full stop, not part of the
            # FQCN; leaving it on makes every comparison key miss by one
            # character and fakes a total disagreement.
            tm = (re.search(r"'([\w.$]+)'", msg)
                  or re.search(r"import\s+-\s+([\w.$]+?)\.?\s*$", msg))
            rel = fname
            if tree_root is not None:
                try:
                    rel = str(Path(fname).resolve().relative_to(
                        Path(tree_root).resolve()))
                except ValueError:
                    rel = fname
            findings.append({
                "source": "native",
                "native_rule_id": "checkstyle:ImportControl",
                "rule_id": "checkstyle:ImportControl",
                "severity": err.get("severity") or "error",
                "file": rel.replace("\\", "/"),
                "abs_file": fname,
                "line": int(err.get("line") or 0) or None,
                "target": tm.group(1) if tm else None,
                "message": msg,
            })
    meta = {
        "tool": "checkstyle ImportControl",
        "oracle_kind": "executed-native-tool",
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "jar": jar,
        "stderr_tail": proc.stderr[-500:],
    }
    return findings, meta


# ==========================================================================
# Translation: import-control.xml -> architecture.yaml
# ==========================================================================
def build_spec(control_root: IC.ScopeNode, facts: list[dict], root_path: str,
               repo_root: Path) -> tuple[dict, dict]:
    """Emit a limen spec plus a translation report.

    One K6 `forbidden-target` rule per package scope that actually contains
    source files, whose `forbid` list is the set of import targets the native
    cascade denies for that package. Scopes the translator refuses to cover
    are skipped and counted, never approximated.
    """
    by_pkg: dict[str, list[dict]] = {}
    for f in facts:
        by_pkg.setdefault(f["package"], []).append(f)

    all_targets = sorted({t for f in facts for t, _ in f["imports"]})
    rules: list[dict] = []
    skipped: list[dict] = []
    covered_pkgs = 0
    for pkg in sorted(by_pkg):
        scope = IC.scope_for_package(control_root, pkg)
        if scope is None:
            skipped.append({"package": pkg,
                            "reason": "outside the <import-control pkg> root"})
            continue
        ok, why = IC.is_covered(scope)
        if not ok:
            skipped.append({"package": pkg, "reason": why})
            continue
        # Only the targets that files in THIS package actually import can
        # produce a finding; restricting the forbid list to them keeps the
        # spec finite and is verdict-equivalent.
        local_targets = sorted({t for f in by_pkg[pkg] for t, _ in f["imports"]})
        denied = IC.forbidden_targets(control_root, pkg, local_targets)
        covered_pkgs += 1
        if not denied:
            continue
        rules.append({
            "id": "ic-" + re.sub(r"[^a-z0-9]+", "-", pkg.lower()).strip("-"),
            "kind": "forbidden-target",
            "severity": "error",
            "scope": {"roots": ["main"],
                      "paths": [f"{root_path}/{pkg.replace('.', '/')}/*.java"]},
            "forbid": [t for t, _ in denied],
            "message": (f"Import denied by checkstyle ImportControl for package "
                        f"{pkg}."),
            "remedy": "Add an <allow> element to checkstyle/import-control.xml "
                      "or drop the dependency.",
            "_evidence": {t: why for t, why in denied},
        })
    spec = {
        "version": 1,
        "project": "kafka-import-control-retrofit",
        "roots": {"main": {"path": root_path, "language": "java"}},
        "unresolved_policy": {"default": "off"},
        "rules": [{k: v for k, v in r.items() if not k.startswith("_")}
                  for r in rules],
    }
    report = {
        "native_scopes": IC.count_scopes(control_root),
        "native_guards": IC.count_guards(control_root),
        "coverage": IC.coverage(control_root),
        "packages_with_sources": len(by_pkg),
        "packages_covered": covered_pkgs,
        "packages_skipped": len(skipped),
        "skipped": skipped[:50],
        "limen_rules_emitted": len(rules),
        "distinct_import_targets": len(all_targets),
        "rule_evidence": {r["id"]: r["_evidence"] for r in rules},
    }
    return spec, report


def run_limen(spec_path: Path, workspace: Path, today: str) -> tuple[list[dict], dict]:
    cmd = [sys.executable, "-m", "limen.cli", "check", "--spec", str(spec_path),
           "--workspace", str(workspace), "--today", today, "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"limen output is not JSON: {e}\n{proc.stdout[:800]}"
                           f"\n{proc.stderr[:800]}")
    rows = []
    for f in doc.get("findings", []):
        g = dict(f)
        g["source"] = "limen"
        rows.append(g)
    return rows, {"command": " ".join(cmd), "exit_code": proc.returncode,
                  "stderr_tail": proc.stderr[-500:]}


# ==========================================================================
# Seeded-violation arm
# ==========================================================================
def plan_injections(control_root: IC.ScopeNode, facts: list[dict],
                    pool: list[str], limit: int) -> list[dict]:
    """Pick (file, illegal target) pairs the native cascade must reject.

    One injection per file, each in a different package where possible, so the
    injections are independent: ImportControl decides per file, and limen's
    rules are per package scope, so no injection can mask another. The chosen
    target must (a) be denied by the native cascade for that file's package and
    (b) not already be imported by the file.
    """
    by_pkg: dict[str, list[dict]] = {}
    for f in facts:
        by_pkg.setdefault(f["package"], []).append(f)

    out: list[dict] = []
    for pkg in sorted(by_pkg):
        if len(out) >= limit:
            break
        scope = IC.scope_for_package(control_root, pkg)
        if scope is None or not IC.is_covered(scope)[0]:
            continue
        f = sorted(by_pkg[pkg], key=lambda x: x["path"])[0]
        already = {t for t, _ in f["imports"]}
        pick = None
        for cand in pool:
            if cand in already or cand.startswith(pkg + "."):
                continue
            verdict, guard, at = IC.decide(scope, cand)
            if verdict == IC.DISALLOW:
                pick = (cand, guard.describe() if guard else "default deny", at)
                break
        if pick is None:
            continue
        out.append({"file": f["path"], "package": pkg, "target": pick[0],
                    "native_reason": pick[1], "decided_at": pick[2] or "(root)"})
    return out


def apply_injections(module: Path, dest: Path, injections: list[dict]) -> list[dict]:
    """Clone the module tree and add one illegal import per selected file.

    The tree is copied for real (``shutil.copytree``): the Study 1 trap was a
    symlinked dependency directory silently resolving back to the pristine
    tree, so every mutant "passed". A real copy cannot do that.

    The import is inserted immediately after the ``package`` statement, and the
    resulting line number is recorded, so the expected finding is a concrete
    (file, line, target) triple rather than "somewhere in this file".
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(module, dest)
    placed = []
    for inj in injections:
        rel = Path(inj["file"]).relative_to(module)
        p = dest / rel
        text = p.read_text(encoding="utf-8", errors="replace")
        m = _PKG_RX.search(strip_comments(text))
        if m is None:
            continue
        end = text.index("\n", m.end() - 1) + 1
        line = text.count("\n", 0, end) + 1
        p.write_text(text[:end] + f"import {inj['target']};\n" + text[end:],
                     encoding="utf-8")
        placed.append(dict(inj, injected_file=str(p), injected_line=line,
                           rel_path=str(rel)))
    return placed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--module", default="clients/src/main/java")
    ap.add_argument("--control", default="checkstyle/import-control.xml")
    ap.add_argument("--java", required=True)
    ap.add_argument("--checkstyle", required=True)
    ap.add_argument("--today", default="2026-08-04")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None)
    ap.add_argument("--seed-violations", type=int, default=0,
                    help="inject N known-illegal imports into a copy of the "
                         "tree and check both tools flag them (0 = off)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    module = repo / args.module
    control = repo / args.control
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work).resolve() if args.work else out / "_work"
    work.mkdir(parents=True, exist_ok=True)

    facts = java_facts(module)
    print(f"java files: {len(facts)}")

    control_root = IC.parse_import_control(control.read_text(encoding="utf-8"))
    spec, report = build_spec(control_root, facts, args.module, repo)
    print(f"native: {report['native_scopes']} scopes, {report['native_guards']} guards"
          f"  →  limen: {report['limen_rules_emitted']} rules")

    spec_path = out / "architecture.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")

    native, native_meta = run_checkstyle(args.java, args.checkstyle, control,
                                         module, work, tree_root=repo)
    print(f"checkstyle: exit {native_meta['exit_code']}, {len(native)} findings")

    limen_rows, limen_meta = run_limen(spec_path, repo, args.today)
    print(f"limen     : exit {limen_meta['exit_code']}, {len(limen_rows)} findings")

    # ---- seeded-violation arm ------------------------------------------
    seeded: dict[str, Any] = {"enabled": bool(args.seed_violations)}
    if args.seed_violations:
        pool = sorted({t for f in facts for t, _ in f["imports"]
                       if t.startswith("org.apache.kafka.")})
        plan = plan_injections(control_root, facts, pool, args.seed_violations)
        seed_root = work / "seeded"
        placed = apply_injections(module, seed_root / args.module, plan)
        print(f"seeded: {len(placed)} injections")

        seed_facts = java_facts(seed_root / args.module)
        seed_spec, seed_report = build_spec(control_root, seed_facts,
                                            args.module, seed_root)
        seed_spec_path = out / "architecture.seeded.yaml"
        seed_spec_path.write_text(
            yaml.safe_dump(seed_spec, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
        s_native, s_native_meta = run_checkstyle(
            args.java, args.checkstyle, control, seed_root / args.module,
            work / "seeded_cs", tree_root=seed_root)
        s_limen, s_limen_meta = run_limen(seed_spec_path, seed_root, args.today)

        def hit(rows, inj, path_key):
            for r in rows:
                f = (r.get("file") or "").replace("\\", "/")
                if not f.endswith(inj["rel_path"]):
                    continue
                if r.get("line") == inj["injected_line"] and \
                        (r.get("target") or "").lstrip(".") == inj["target"]:
                    return True
            return False

        detail = []
        for inj in placed:
            detail.append({
                **{k: inj[k] for k in ("rel_path", "package", "target",
                                       "native_reason", "injected_line")},
                "native_detected": hit(s_native, inj, "file"),
                "limen_detected": hit(s_limen, inj, "file"),
            })
        seeded.update({
            "n_injections": len(placed),
            "native_detected": sum(1 for d in detail if d["native_detected"]),
            "limen_detected": sum(1 for d in detail if d["limen_detected"]),
            "both_detected": sum(1 for d in detail
                                 if d["native_detected"] and d["limen_detected"]),
            "native_findings_total": len(s_native),
            "limen_findings_total": len(s_limen),
            "oracle": s_native_meta, "limen_run": s_limen_meta,
            "translation": {k: v for k, v in seed_report.items()
                            if k != "rule_evidence"},
            "injections": detail,
            "native_findings": s_native,
            "limen_findings": s_limen,
        })
        print(f"  detected — native {seeded['native_detected']}/{len(placed)}, "
              f"limen {seeded['limen_detected']}/{len(placed)}")

    payload = {
        "schema": SCHEMA,
        "seeded": seeded,
        "repo": "apache/kafka",
        "module": args.module,
        "native_config": args.control,
        "translation": report,
        "oracle": native_meta,
        "limen": limen_meta,
        "native_findings": native,
        "limen_findings": limen_rows,
        "n_java_files": len(facts),
    }
    (out / "retrofit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out / "retrofit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
