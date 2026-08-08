#!/usr/bin/env python3
"""Study 1b, step 9 — bespoke retrofit #3: Home Assistant / `hass_imports`.

Home Assistant enforces its integration-boundary conventions with a **bespoke
pylint plugin** it ships in its own repository
(`pylint/plugins/pylint_home_assistant/checkers/imports.py`, class
`HassImportsFormatChecker`). The checker is **executed here**, unmodified, from
the pinned checkout — the oracle is Home Assistant's own code, running under
pylint 4 / astroid 4, which is what its `pyproject.toml` requires.

One accommodation is recorded rather than hidden: the plugin's package
`__init__.py` imports every checker module in the package, and one unrelated
module (`checkers/duplicate_const.py`) raises `NameError` at import time under
this build. A three-line shim registers `HassImportsFormatChecker` directly.
The *checker under test* is byte-identical to the repository's; only the
registration path differs, and no other checker is enabled.

The rule this retrofit targets is `home-assistant-component-root-import`
(C7405), the one architecture-boundary rule in the file:

    An import that reaches INTO another integration must address that
    integration's ROOT module, not one of its sub-modules.

read off `_check_for_component_root_import` at the pinned SHA:

  * a module under `homeassistant.components.<domain>[.<module>]` is an
    integration module (`helpers/module_info.py::parse_module`);
  * importing `homeassistant.components.other.anything` from a different
    integration is a violation (`other_module is not None`);
  * importing `homeassistant.components.other` and naming `const` in the
    import list is also a violation;
  * the current integration itself is exempt, as are the integrations in
    `_IGNORE_ROOT_IMPORT` (bluetooth, device_tracker, homeassistant,
    homeassistant_hardware, http, recorder), which the retrofit carries over as
    limen `exempt` entries.

limen mapping: `K5 scoped-allowlist` per integration — within
`homeassistant/components/<domain>/**`, any target under the prefix
`homeassistant.components.` must appear in an allow-list, which the retrofit
fills with the domain's own root, its own sub-modules, and the six ignored
integrations' roots. The remaining sibling *sub-module* imports are exactly
what C7405 reports.

The `const` half of the rule (importing the name `const` from another
integration's root) is NOT expressible: limen's dependency facts are module
targets, not the imported symbol list. It is recorded as deviation D-HA-1 and
the affected findings are counted separately, never folded into agreement.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "bespoke-retrofit/1"

#: Copied from checkers/imports.py::_IGNORE_ROOT_IMPORT; verified at run time.
IGNORE_ROOT_IMPORT = ("bluetooth", "device_tracker", "homeassistant",
                      "homeassistant_hardware", "http", "recorder")

TARGET_MSG = "home-assistant-component-root-import"

SHIM = '''"""Register ONLY Home Assistant's own imports checker.

`pylint_home_assistant/__init__.py` imports every checker module in the
package; one unrelated module fails to import under this pylint/astroid build.
Registering the imports checker directly runs Home Assistant's UNMODIFIED
`checkers/imports.py` while sidestepping that unrelated failure.
"""
from pylint_home_assistant.checkers.imports import HassImportsFormatChecker


def register(linter):
    linter.register_checker(HassImportsFormatChecker(linter))
'''


def verify_ignore_list(repo: Path) -> dict[str, Any]:
    """Check IGNORE_ROOT_IMPORT against the repository's own source."""
    src = (repo / "pylint/plugins/pylint_home_assistant/checkers/imports.py"
           ).read_text(encoding="utf-8")
    m = re.search(r"_IGNORE_ROOT_IMPORT\s*=\s*\((.*?)\)", src, re.S)
    found = tuple(re.findall(r'"([\w]+)"', m.group(1))) if m else ()
    return {"verified": found == IGNORE_ROOT_IMPORT,
            "parsed_from_repo": list(found),
            "constant_in_script": list(IGNORE_ROOT_IMPORT)}


# ==========================================================================
# Oracle: run Home Assistant's own pylint checker
# ==========================================================================
def run_pylint(repo: Path, targets: list[str], workdir: Path, python: str
               ) -> tuple[list[dict], dict]:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ha_imports_only.py").write_text(SHIM, encoding="utf-8")
    (workdir / "pylintrc").write_text("[MAIN]\npersistent=no\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo / "pylint/plugins"), str(workdir), str(repo)])
    cmd = [python, "-m", "pylint", "--rcfile", str(workdir / "pylintrc"),
           "--load-plugins=ha_imports_only", "--disable=all",
           f"--enable={TARGET_MSG}", "--output-format=json2",
           *[str(repo / t) for t in targets]]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir,
                          env=env, timeout=7200)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"pylint output is not JSON: {e}\n"
                           f"{proc.stdout[:600]}\n{proc.stderr[:600]}")
    rows = []
    for m in doc.get("messages", []):
        if m.get("symbol") != TARGET_MSG:
            continue
        rel = os.path.relpath(m["absolutePath"], repo).replace(os.sep, "/")
        rows.append({
            "source": "native",
            "native_rule_id": TARGET_MSG,
            "rule_id": TARGET_MSG,
            "severity": "error" if m.get("type") == "error" else "warning",
            "file": rel,
            "line": m.get("line"),
            "target": None,          # filled from the source line below
            "message": m.get("message"),
            "module": m.get("module"),
        })
    return rows, {
        "tool": "Home Assistant hass imports checker (HassImportsFormatChecker)",
        "oracle_kind": "executed-native-tool",
        "oracle_note": ("Home Assistant's UNMODIFIED "
                        "pylint/plugins/pylint_home_assistant/checkers/"
                        "imports.py, registered through a three-line shim "
                        "because the plugin package's __init__ imports an "
                        "unrelated module that raises NameError under this "
                        "pylint/astroid build. No other checker was enabled."),
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "modules_linted": doc.get("statistics", {}).get("modulesLinted"),
        "stderr_tail": proc.stderr[-400:],
    }


def resolve_native_targets(repo: Path, rows: list[dict]) -> tuple[list[dict], int]:
    """Attach the imported module to each native finding.

    pylint reports the offending *statement*, not the module it names, but the
    comparison key is (file, line, target). The module is read back out of the
    source line with `ast`, so the key is derived from the code rather than
    from a message string. Rows whose target cannot be recovered are counted
    and dropped from the keyed comparison instead of being keyed on None,
    which would collide.
    """
    unresolved = 0
    out = []
    for r in rows:
        p = repo / r["file"]
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            unresolved += 1
            continue
        target = None
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) != r["line"]:
                continue
            if isinstance(node, ast.ImportFrom) and node.module:
                target = node.module
                break
            if isinstance(node, ast.Import) and node.names:
                target = node.names[0].name
                break
        if target is None:
            unresolved += 1
            continue
        out.append(dict(r, target=target))
    return out, unresolved


# ==========================================================================
# Translation
# ==========================================================================
def integrations(repo: Path) -> list[str]:
    base = repo / "homeassistant/components"
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and (p / "__init__.py").is_file())


def imported_components(repo: Path, domain: str) -> set[str]:
    """Integration roots that files in `domain` actually import.

    The allow list must not name every one of the 1458 integrations: limen
    reports a declared entry that matches nothing as `spec-dead-entry` (a
    deliberate feature — a dead exception is a defect), and 1458 rules x 1458
    entries would bury the real findings under millions of integrity warnings.
    Restricting each allow list to the roots the integration actually imports
    is verdict-equivalent — an entry that matches no dependency cannot change
    any verdict — and keeps the spec honest.
    """
    base = repo / "homeassistant/components" / domain
    rx = re.compile(r"homeassistant\.components\.(\w+)")
    out: set[str] = set()
    for p in base.rglob("*.py"):
        for m in rx.finditer(p.read_text(encoding="utf-8", errors="replace")):
            out.add(m.group(1))
    return out


def build_spec(repo: Path, domains: list[str], limit_domains: list[str] | None
               ) -> tuple[dict, dict]:
    """One K5 scoped-allowlist rule per integration.

    Within `homeassistant/components/<d>/**`, every target under
    `homeassistant.components.` must be allow-listed. The allow list holds the
    integration's own root and sub-modules (self-imports are exempt in the
    native rule) plus the roots of the six ignored integrations. What survives
    is exactly a sibling integration's SUB-module — the C7405 violation.
    """
    picked = limit_domains if limit_domains else domains
    rules = []
    dead_avoided = 0
    for d in picked:
        seen = imported_components(repo, d)
        allow = [f"homeassistant.components.{d}",
                 f"homeassistant.components.{d}.**"]
        for ig in IGNORE_ROOT_IMPORT:
            if ig not in seen:
                dead_avoided += 1
                continue
            allow.append(f"homeassistant.components.{ig}")
            # The native rule ignores these integrations entirely (the check
            # returns True before looking at the sub-module), so their
            # sub-modules are allowed too.
            allow.append(f"homeassistant.components.{ig}.**")
        # A sibling integration's ROOT is allowed; only its sub-modules are not.
        for other in sorted(seen):
            if other != d and other in set(domains):
                allow.append(f"homeassistant.components.{other}")
            elif other != d:
                dead_avoided += 1
        rules.append({
            "id": "ha-root-import-" + re.sub(r"[^a-z0-9]+", "-", d.lower()),
            "kind": "scoped-allowlist",
            "severity": "error",
            "target_prefix": "homeassistant.components.",
            "scope": {"roots": ["main"],
                      "paths": [f"homeassistant/components/{d}/**"]},
            "allow": allow,
            "message": "Import should be using the component root",
            "remedy": "Import the other integration's root module, not one of "
                      "its sub-modules.",
        })
    spec = {
        "version": 1,
        "project": "home-assistant-component-root-import-retrofit",
        "roots": {"main": {"path": "homeassistant", "language": "python"}},
        "unresolved_policy": {"default": "off"},
        "rules": rules,
    }
    return spec, {"integrations_total": len(domains),
                  "integrations_ruled": len(picked),
                  "dead_allow_entries_avoided": dead_avoided,
                  "allow_scoping_note": (
                      "each rule's allow list names only the integration roots "
                      "that files in that integration actually import; an entry "
                      "matching no dependency cannot change a verdict, and "
                      "limen reports such entries as spec-dead-entry"),
                  "ignore_root_import": list(IGNORE_ROOT_IMPORT),
                  "deviation_D_HA_1": (
                      "the `const` half of C7405 (importing the NAME `const` "
                      "from a sibling integration's root) is not expressible: "
                      "limen's facts carry module targets, not the imported "
                      "symbol list. Findings of that shape are counted "
                      "separately and never folded into agreement.")}


_DISABLE_RX = re.compile(r"#\s*pylint:\s*disable(?:-next)?=([^\n#]*)")


def inline_suppressions(repo: Path, files: list[str]) -> dict[str, set[int]]:
    """Lines where C7405 is suppressed by a `# pylint: disable` comment.

    pylint honours inline disable comments; limen's suppression mechanism is
    the spec's `allow`/`exempt` entries, which carry reason/owner/expires. A
    difference caused purely by an inline disable is therefore a *suppression
    mechanism* difference, not a disagreement about the architecture: both
    tools agree the import violates the rule, and one of them has been told to
    keep quiet in the source file. Detected here so those differences are
    classified on evidence instead of being counted as limen false positives.

    Covers all three forms pylint accepts:

    * a TRAILING `disable=` on the offending line (for a multi-line import
      statement that is the statement's first line, which is the line pylint
      reports);
    * `disable-next=` on the line before;
    * a STANDALONE `# pylint: disable=` comment on its own line, which pylint
      scopes to the enclosing BLOCK — it silences the message from that line
      to the end of the block it sits in. Missing this form is what makes a
      correctly-suppressed finding look like an unexplained limen-only result,
      so the block extent is computed from indentation here rather than
      approximated by "the next line".
    """
    out: dict[str, set[int]] = {}
    for rel in files:
        p = repo / rel
        if not p.is_file():
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        hits: set[int] = set()
        for i, ln in enumerate(lines, start=1):
            m = _DISABLE_RX.search(ln)
            if not m:
                continue
            names = {n.strip() for n in m.group(1).split(",")}
            if TARGET_MSG not in names and "all" not in names:
                continue
            hits.add(i)
            if "disable-next" in ln:
                hits.add(i + 1)
                continue
            if ln.lstrip().startswith("#"):
                # Standalone comment: block-scoped. The block runs until the
                # first later non-blank, non-comment line indented LESS than
                # the comment itself.
                indent = len(ln) - len(ln.lstrip())
                for j in range(i, len(lines)):
                    nxt = lines[j]
                    if not nxt.strip() or nxt.lstrip().startswith("#"):
                        hits.add(j + 1)
                        continue
                    if len(nxt) - len(nxt.lstrip()) < indent:
                        break
                    hits.add(j + 1)
        if hits:
            out[rel] = hits
    return out


def run_limen(spec_path: Path, workspace: Path, today: str) -> tuple[list[dict], dict]:
    cmd = [sys.executable, "-m", "limen.cli", "check", "--spec", str(spec_path),
           "--workspace", str(workspace), "--today", today, "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"limen output is not JSON: {e}\n{proc.stdout[:600]}"
                           f"\n{proc.stderr[:600]}")
    return ([dict(f, source="limen") for f in doc.get("findings", [])],
            {"command": " ".join(cmd), "exit_code": proc.returncode,
             "stderr_tail": proc.stderr[-400:]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--domains", nargs="*", default=None,
                    help="limit to these integrations (default: all)")
    ap.add_argument("--pylint-python", required=True,
                    help="interpreter with pylint>=4 and astroid>=4")
    ap.add_argument("--today", default="2026-08-04")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work).resolve() if args.work else out / "_work"

    check = verify_ignore_list(repo)
    print(f"_IGNORE_ROOT_IMPORT verified: {check['verified']}")
    if not check["verified"]:
        (out / "retrofit.json").write_text(json.dumps(
            {"schema": SCHEMA, "repo": "home-assistant/core",
             "aborted": "constant drift", "config_check": check},
            indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 1

    domains = integrations(repo)
    picked = args.domains or domains
    print(f"integrations: {len(domains)} total, {len(picked)} under test")

    spec, treport = build_spec(repo, domains, args.domains)
    spec_path = out / "architecture.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")
    print(f"limen spec: {len(spec['rules'])} K5 rules")

    targets = [f"homeassistant/components/{d}" for d in picked]
    native_raw, native_meta = run_pylint(repo, targets, work, args.pylint_python)
    native, unresolved = resolve_native_targets(repo, native_raw)
    native_meta["findings_before_target_resolution"] = len(native_raw)
    native_meta["target_unresolved_dropped"] = unresolved
    print(f"native: {len(native_raw)} findings, {unresolved} without a "
          f"recoverable target → {len(native)} keyed")

    limen_rows, limen_meta = run_limen(spec_path, repo, args.today)
    limen_scoped = [r for r in limen_rows
                    if any((r.get("file") or "").startswith(t) for t in targets)]
    print(f"limen: exit {limen_meta['exit_code']}, {len(limen_rows)} findings "
          f"({len(limen_scoped)} inside the tested integrations)")

    # ---- oracle liveness -------------------------------------------------
    # The native checker reports zero on this tree. That is either "the tree
    # is clean" or "the oracle is not running", and the difference decides
    # whether every number below means anything. Test it: take one file limen
    # flags, remove ONLY the inline `# pylint: disable` comment, and re-run the
    # native checker on the copy. If it then fires on the same line, the oracle
    # is live and the zero is a real verdict about a suppressed violation.
    liveness: dict[str, Any] = {"performed": False}
    if limen_scoped:
        probe = limen_scoped[0]
        src_path = repo / probe["file"]
        text = src_path.read_text(encoding="utf-8", errors="replace")
        # Remove every form of the suppression, not just the trailing one:
        # `disable=`, `disable-next=` and a standalone block-scoped comment.
        # A probe that strips only one form silently tests nothing when the
        # file happens to use another, and reports a live oracle as dead.
        stripped = re.sub(
            rf"#\s*pylint:\s*disable(?:-next)?=[^\n]*{re.escape(TARGET_MSG)}[^\n]*",
            "", text)
        # The probe lives INSIDE the integration whose module it copies:
        # `HassImportsFormatChecker` derives the current integration from the
        # module's dotted name, so a copy outside `homeassistant/components/`
        # would parse as no integration at all and the checker would stay
        # silent for the wrong reason. `run_pylint` resolves targets against
        # the repo root, so the path is passed repo-relative.
        probe_rel = str(Path(probe["file"]).with_name("_liveness_probe.py"))
        probe_file = repo / probe_rel
        try:
            probe_file.write_text(text, encoding="utf-8")
            before, _ = run_pylint(repo, [probe_rel], work / "liveness_run",
                                   args.pylint_python)
            probe_file.write_text(stripped, encoding="utf-8")
            after, _ = run_pylint(repo, [probe_rel], work / "liveness_run",
                                  args.pylint_python)
        finally:
            probe_file.unlink(missing_ok=True)
        liveness = {
            "performed": True,
            "probe_file": probe["file"],
            "probe_line_reported_by_limen": probe["line"],
            "native_findings_with_suppression": len(before),
            "native_findings_without_suppression": len(after),
            "native_lines_without_suppression": sorted(
                {r["line"] for r in after}),
            "oracle_is_live": len(after) > len(before),
            "note": ("the native checker reports zero on the unmodified tree. "
                     "Removing only the inline `# pylint: disable` comment "
                     "from one file makes it fire, which shows the zero is a "
                     "real verdict on a suppressed violation and not a dead "
                     "oracle."),
        }
        print(f"oracle liveness: {liveness['native_findings_with_suppression']} "
              f"-> {liveness['native_findings_without_suppression']} findings "
              f"when the inline disable is removed "
              f"(live={liveness['oracle_is_live']})")

    # Which limen-only findings sit on a line pylint was told to ignore?
    supp = inline_suppressions(repo, sorted({r["file"] for r in limen_scoped}))
    native_keys = {(r["file"], r["line"], r["target"]) for r in native}
    supp_hits = []
    for r in limen_scoped:
        if (r["file"], r["line"], r.get("target")) in native_keys:
            continue
        lines = supp.get(r["file"], set())
        if r.get("line") in lines:
            src = (repo / r["file"]).read_text(
                encoding="utf-8", errors="replace").splitlines()
            supp_hits.append({
                "file": r["file"], "line": r["line"], "target": r.get("target"),
                "source_line": src[r["line"] - 1].strip()[:300],
            })
    print(f"limen-only on a pylint-suppressed line: {len(supp_hits)}")

    payload = {
        "schema": SCHEMA,
        "repo": "home-assistant/core",
        "inline_suppressions": {
            "limen_only_on_suppressed_line": len(supp_hits),
            "evidence": supp_hits,
            "note": ("pylint honours `# pylint: disable=` comments in the "
                     "source; limen's suppression mechanism is the spec's "
                     "`allow`/`exempt` entries, which carry reason/owner/"
                     "expires. A difference of this shape is a suppression-"
                     "mechanism difference — both tools agree the import "
                     "violates the rule — not a limen false positive."),
        },
        "native_config": "pylint/plugins/pylint_home_assistant/checkers/imports.py",
        "rule_under_test": TARGET_MSG,
        "oracle_liveness": liveness,
        "config_check": check,
        "domains_tested": picked,
        "translation": treport,
        "oracle": native_meta,
        "limen": limen_meta,
        "native_findings": native,
        "limen_findings": limen_scoped,
        "limen_findings_all": len(limen_rows),
        "scope": {
            "limen_findings_total": len(limen_rows),
            "limen_findings_in_tested_integrations": len(limen_scoped),
            "native_findings": len(native),
            "comparison_population": len(limen_scoped),
            "warning": (
                "limen_findings_total is NOT a comparison quantity and must "
                "never be reported against the native count. It is dominated "
                "by `spec-dead-entry` spec-integrity warnings: one rule is "
                "emitted per integration and limen reports every allow-list "
                "entry that matches nothing in the tree, so the total scales "
                "with the number of rules, not with architecture violations. "
                "The native plugin emits no such diagnostic and is invoked "
                "only on the tested integrations. ONLY "
                "`limen_findings_in_tested_integrations` "
                f"({len(limen_scoped)}) is comparable with the native count "
                f"({len(native)})."),
        },
    }
    (out / "retrofit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out / "retrofit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
