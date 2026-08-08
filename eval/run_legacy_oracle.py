#!/usr/bin/env python3
"""Run both legacy oracles and emit eval/results/legacy_findings.json.

One command reproduces the complete list of violations the ORIGINAL legacy
checkers report on a workspace:

    python eval/run_legacy_oracle.py

- backend: eval/legacy/php/run_architecture_tests.php loads the ten unmodified
  tests/Architecture/*.php classes under a thin PHPUnit/Laravel shim,
  discovers every test_* method by reflection, and dumps every assertion event.
  No rule logic is reimplemented here; this script only decodes what the legacy
  classes themselves serialised into their assertion messages.
- frontend: scripts/audit-frontend-architecture.mjs is executed unmodified with
  `cwd` set to each frontend repository, and its stdout/stderr are parsed.

Both are normalised into the `legacy-findings/1` schema, with `file` expressed
in the SAME path space limen uses in its report (litrev-arch-snapshot/<repo>/…)
so that (file, line, target) is a usable diff key.

Line recovery: the legacy PHP checkers report (file, import) and never a line.
Lines are recovered by re-running the SAME PCRE pattern the reporting rule used
over the same file (eval/legacy/php/resolve_lines.php, executed by PHP so the
pattern is interpreted by the same engine). This adds a coordinate; it never
adds, removes or reclassifies a violation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
LIMEN_DIR = EVAL_DIR.parent
DEFAULT_WORKSPACE = LIMEN_DIR.parent / "workspace"
PHP_DIR = EVAL_DIR / "legacy" / "php"

SNAPSHOT_PREFIX = "litrev-arch-snapshot"

# repository directory name inside the workspace -> prefix used by limen
BACKEND_SUBREPOS = [
    ("shared", "backend-shared"),
    ("core-backend", "litrev-core-backend"),
]

# ---------------------------------------------------------------------------
# legacy rule inventory (eval/legacy_rule_inventory.csv) cross-reference
# ---------------------------------------------------------------------------

BACKEND_INVENTORY = {
    "AdminBoundaryRulesTest::test_module_does_not_import_admin_modules_directly": "L-BE-TEST-02",
    "ApplicationBoundaryRulesTest::test_modules_do_not_import_foreign_application_internals": "L-BE-TEST-03",
    "DeploymentBoundaryRulesTest::test_repo_has_single_root_docker_compose_file": "L-BE-TEST-04",
    "DeploymentBoundaryRulesTest::test_modules_do_not_define_local_deploy_or_docker_directories": "L-BE-TEST-05",
    "DeploymentBoundaryRulesTest::test_database_config_does_not_define_module_scoped_connections": "L-BE-TEST-06",
    "HttpBoundaryRulesTest::test_module_does_not_import_http_layer_of_other_modules": "L-BE-TEST-07",
    "LayerBoundaryRulesTest::test_application_layer_does_not_import_module_infrastructure": "L-BE-TEST-08",
    "LayerBoundaryRulesTest::test_models_do_not_import_module_application_classes": "L-BE-TEST-09",
    "LayerBoundaryRulesTest::test_support_imports_only_allowlisted_module_classes": "L-BE-TEST-10",
    "LayerBoundaryRulesTest::test_models_import_only_allowlisted_module_classes": "L-BE-TEST-11",
    "ModelOwnershipRulesTest::test_every_model_has_declared_owner_module": "L-BE-TEST-12",
    "ModelOwnershipRulesTest::test_model_owner_modules_exist": "L-BE-TEST-13",
    "ModelOwnershipRulesTest::test_model_imports_are_limited_to_owner_or_declared_consumers": "L-BE-TEST-14",
    "ModelOwnershipRulesTest::test_forbidden_model_import_rules_are_enforced": "L-BE-TEST-15",
    "ModuleBoundaryBaselineRulesTest::test_cross_module_notifications_domain_imports_match_declared_baseline": "L-BE-TEST-16",
    "ModuleBoundaryBaselineRulesTest::test_cross_module_notifications_mail_imports_match_declared_baseline": "L-BE-TEST-17",
    "ModuleContractRulesTest::test_module_paths_use_only_allowlisted_cross_module_application_imports": "L-BE-TEST-18",
    "SharedBoundaryRulesTest::test_shared_has_no_http_or_infrastructure_layers": "L-BE-TEST-19",
    "SharedBoundaryRulesTest::test_shared_does_not_import_forbidden_host_classes": "L-BE-TEST-20",
    "SharedBoundaryRulesTest::test_shared_cross_module_imports_use_contracts_events_domain_or_presentation": "L-BE-TEST-21",
}

FRONTEND_INVENTORY = {
    "raw-success-response-data": "L-FE-01",
    "local-message-fallback": "L-FE-02",
    "runtime-console-log": "L-FE-03",
    "shared-must-not-depend-on-app-or-modules": "L-FE-04",
    "cross-module-deep-import": "L-FE-05",
    "cross-module-internal-import": "L-FE-06",
    "app-deep-module-import": "L-FE-07",
    "oversized-page-file": "L-FE-08",
    "oversized-module-entrypoint": "L-FE-09",
    "oversized-hook-file": "L-FE-10",
    "missing-module-entrypoint": "L-FE-11",
    "missing-module-readme": "L-FE-12",
    "missing-module-contract": "L-FE-13",
    "unexpected-module-subentrypoint": "L-FE-14",
    "unexpected-shared-domain-helper": "L-FE-15",
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _short(class_name: str) -> str:
    return class_name.rsplit("\\", 1)[-1]


def _rule_key(event: dict) -> str:
    return f"{_short(event['class'])}::{event['method']}"


class PathSpace:
    """Maps legacy-reported paths into limen's report path space."""

    def __init__(self, backend_root: Path, workspace: Path):
        self.backend_root = backend_root.resolve()
        self.workspace = workspace.resolve()

    def from_backend_abs(self, abs_path: str) -> str:
        p = Path(abs_path)
        try:
            rel = p.relative_to(self.backend_root)
        except ValueError:
            # not under the backend root: fall back to a workspace-relative path
            try:
                return str(p.relative_to(self.workspace))
            except ValueError:
                return str(p)
        return self.from_backend_rel(str(rel))

    def from_backend_rel(self, rel: str) -> str:
        rel = rel.replace("\\", "/").lstrip("/")
        for link_name, repo in BACKEND_SUBREPOS:
            if rel == link_name or rel.startswith(link_name + "/"):
                tail = rel[len(link_name):].lstrip("/")
                return f"{SNAPSHOT_PREFIX}/{repo}/{tail}" if tail else f"{SNAPSHOT_PREFIX}/{repo}"
        return f"{SNAPSHOT_PREFIX}/litrev-backend/{rel}"

    def from_shared_src_rel(self, rel: str) -> str:
        """SharedBoundaryRulesTest reports paths relative to shared/src."""
        return self.from_backend_rel("shared/src/" + rel.replace("\\", "/").lstrip("/"))

    def from_frontend(self, repo_dir_name: str, rel: str) -> str:
        return f"{SNAPSHOT_PREFIX}/{repo_dir_name}/{rel.replace(chr(92), '/').lstrip('/')}"


def resolve_lines(queries: list[dict]) -> list[int | None]:
    """Recover line numbers via PHP/PCRE (see legacy/php/resolve_lines.php)."""
    if not queries:
        return []
    proc = subprocess.run(
        ["php", str(PHP_DIR / "resolve_lines.php")],
        input=json.dumps(queries),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# backend oracle
# ---------------------------------------------------------------------------


def run_backend_oracle(backend_root: Path) -> dict:
    proc = subprocess.run(
        ["php", str(PHP_DIR / "run_architecture_tests.php")],
        capture_output=True,
        text=True,
        env={**_env(), "LIMEN_BACKEND_ROOT": str(backend_root)},
        check=True,
    )
    return json.loads(proc.stdout)


def _env() -> dict:
    import os

    return dict(os.environ)


def backend_findings(raw: dict, space: PathSpace) -> list[dict]:
    """Turn recorded assertion events into schema findings.

    Every violation below comes verbatim out of the legacy payload; the only
    values computed here are (a) the path rewrite into limen's path space and
    (b) the recovered line number.
    """
    cfg = raw["config_patterns"]
    line_patterns = raw["line_patterns"]
    backend_root = Path(raw["backend_root"])

    findings: list[dict] = []
    pending: list[tuple[dict, dict]] = []  # (finding, line-query)

    # ordinal of each assertion within its (class, method, dataset)
    counters: dict[tuple, int] = {}

    for event in raw["events"]:
        key = (event["class"], event["method"], event["dataset"])
        ordinal = counters.get(key, 0)
        counters[key] = ordinal + 1

        if event["passed"]:
            continue

        rule = _rule_key(event)
        payload = event["payload"]
        legacy_rule_id = f"{rule}"
        inventory_id = BACKEND_INVENTORY.get(rule)
        base = {
            "source": "php-tests",
            "legacy_rule_id": legacy_rule_id,
            "inventory_id": inventory_id,
            "severity": "error",
            "message": event["message"],
        }

        def emit(file_path: str, target, line_query: dict | None, raw_item, line=None):
            finding = dict(base)
            finding.update(
                {
                    "file": file_path,
                    "line": line,
                    "target": target,
                    "raw": raw_item,
                }
            )
            if line_query is None:
                findings.append(finding)
            else:
                pending.append((finding, line_query))
            return finding

        pat = line_patterns.get(f"{event['class']}::{event['method']}", {})

        # -- rules whose payload is a list of {file, import} ------------------
        if rule in (
            "AdminBoundaryRulesTest::test_module_does_not_import_admin_modules_directly",
            "ApplicationBoundaryRulesTest::test_modules_do_not_import_foreign_application_internals",
            "LayerBoundaryRulesTest::test_application_layer_does_not_import_module_infrastructure",
            "LayerBoundaryRulesTest::test_models_do_not_import_module_application_classes",
        ):
            for item in payload or []:
                abs_file = item["file"]
                emit(
                    space.from_backend_abs(abs_file),
                    item["import"],
                    {
                        "file": abs_file,
                        "needle": item["import"],
                        "mode": pat.get("mode", "capture"),
                        "pattern": pat.get("pattern"),
                    },
                    item,
                )

        elif rule == "HttpBoundaryRulesTest::test_module_does_not_import_http_layer_of_other_modules":
            for item in payload or []:
                abs_file = item["file"]
                emit(
                    space.from_backend_abs(abs_file),
                    item["imported_module"],
                    {
                        "file": abs_file,
                        "needle": item["imported_module"],
                        "mode": "capture",
                        "pattern": pat.get("pattern"),
                    },
                    item,
                )

        # -- allowlist drivers: pattern comes from config, per ordinal --------
        elif rule == "LayerBoundaryRulesTest::test_support_imports_only_allowlisted_module_classes":
            rules = cfg["support_module_import_rules"]
            entry = rules[ordinal] if ordinal < len(rules) else {}
            for item in payload or []:
                emit(
                    space.from_backend_abs(item["file"]),
                    item["import"],
                    {"file": item["file"], "needle": item["import"], "mode": "capture", "pattern": entry.get("pattern")},
                    item,
                )

        elif rule == "LayerBoundaryRulesTest::test_models_import_only_allowlisted_module_classes":
            rules = cfg["model_module_import_rules"]
            entry = rules[ordinal] if ordinal < len(rules) else {}
            for item in payload or []:
                emit(
                    space.from_backend_abs(item["file"]),
                    item["import"],
                    {"file": item["file"], "needle": item["import"], "mode": "capture", "pattern": entry.get("pattern")},
                    item,
                )

        elif rule == "ModuleContractRulesTest::test_module_paths_use_only_allowlisted_cross_module_application_imports":
            rules = cfg["module_application_rules"]
            entry = rules[ordinal] if ordinal < len(rules) else {}
            for item in payload or []:
                emit(
                    space.from_backend_abs(item["file"]),
                    item["import"],
                    {"file": item["file"], "needle": item["import"], "mode": "capture", "pattern": entry.get("pattern")},
                    item,
                )

        # -- model ownership -------------------------------------------------
        elif rule == "ModelOwnershipRulesTest::test_model_imports_are_limited_to_owner_or_declared_consumers":
            for item in payload or []:
                abs_file = str(backend_root / item["file"])
                emit(
                    space.from_backend_rel(item["file"]),
                    item["model"],
                    {
                        "file": abs_file,
                        "needle": item["model"],
                        "mode": "capture",
                        "pattern": pat.get("pattern"),
                    },
                    item,
                )

        elif rule == "ModelOwnershipRulesTest::test_forbidden_model_import_rules_are_enforced":
            rules = cfg["forbidden_model_import_rules"]
            entry = rules[ordinal] if ordinal < len(rules) else {}
            literal = entry.get("literal")
            for rel in payload or []:
                abs_file = str(backend_root / rel)
                emit(
                    space.from_backend_rel(rel),
                    literal[4:-1] if literal else None,
                    {"file": abs_file, "needle": literal, "mode": "literal", "pattern": None},
                    {"file": rel, "literal": literal},
                )

        elif rule == "ModelOwnershipRulesTest::test_every_model_has_declared_owner_module":
            # payload entries are either a FQCN (class present, owner missing)
            # or a relative path (no class could be parsed).
            for entry_value in payload or []:
                if entry_value.endswith(".php"):
                    emit(space.from_backend_rel(entry_value), None, None, entry_value, line=1)
                else:
                    rel = "app/Models/" + entry_value.rsplit("\\", 1)[-1] + ".php"
                    abs_file = str(backend_root / rel)
                    emit(
                        space.from_backend_rel(rel),
                        entry_value,
                        {
                            "file": abs_file,
                            "needle": entry_value.rsplit("\\", 1)[-1],
                            "mode": "literal",
                            "pattern": None,
                        },
                        entry_value,
                    )

        elif rule == "ModelOwnershipRulesTest::test_model_owner_modules_exist":
            # config-only rule: no source file is read
            for model_class, owner in (payload or {}).items():
                emit(
                    space.from_backend_rel("config/architecture.php"),
                    model_class,
                    {
                        "file": str(backend_root / "config/architecture.php"),
                        "needle": model_class.rsplit("\\", 1)[-1],
                        "mode": "literal",
                        "pattern": None,
                    },
                    {"model": model_class, "owner": owner},
                )

        # -- baselines: two-directional set equality -------------------------
        elif rule in (
            "ModuleBoundaryBaselineRulesTest::test_cross_module_notifications_domain_imports_match_declared_baseline",
            "ModuleBoundaryBaselineRulesTest::test_cross_module_notifications_mail_imports_match_declared_baseline",
        ):
            actual = payload or {}
            expected = event["expected"] or {}
            pattern = (
                r"/use\s+(App\\\\Modules\\\\Notifications\\\\Domain\\\\[^;]+);/m"
                if "domain" in event["method"]
                else r"/use\s+(App\\\\Modules\\\\Notifications\\\\Mail\\\\[^;]+);/m"
            )
            for rel, imports in actual.items():
                for imp in imports:
                    if imp in (expected.get(rel) or []):
                        continue
                    emit(
                        space.from_backend_rel(rel),
                        imp,
                        {"file": str(backend_root / rel), "needle": imp, "mode": "capture", "pattern": pattern},
                        {"file": rel, "import": imp, "direction": "unexpected"},
                    )
            for rel, imports in expected.items():
                for imp in imports:
                    if imp in (actual.get(rel) or []):
                        continue
                    emit(
                        space.from_backend_rel(rel),
                        imp,
                        None,
                        {"file": rel, "import": imp, "direction": "missing-from-actual"},
                        line=1,
                    )

        # -- deployment ------------------------------------------------------
        elif rule == "DeploymentBoundaryRulesTest::test_modules_do_not_define_local_deploy_or_docker_directories":
            for abs_dir in payload or []:
                emit(space.from_backend_abs(abs_dir), None, None, abs_dir, line=1)

        elif rule == "DeploymentBoundaryRulesTest::test_database_config_does_not_define_module_scoped_connections":
            for connection in payload or []:
                emit(
                    space.from_backend_rel("config/database.php"),
                    connection,
                    {
                        "file": str(backend_root / "config/database.php"),
                        "needle": f"'{connection}' =>",
                        "mode": "literal",
                        "pattern": None,
                    },
                    connection,
                )

        elif rule == "DeploymentBoundaryRulesTest::test_repo_has_single_root_docker_compose_file":
            emit(
                space.from_backend_rel("docker-compose.yml"),
                None,
                None,
                {"expected": event["expected"], "actual": event["actual"], "files": event["extra"].get("haystack")},
                line=1,
            )

        # -- shared ----------------------------------------------------------
        elif rule == "SharedBoundaryRulesTest::test_shared_has_no_http_or_infrastructure_layers":
            for rel in payload or []:
                emit(space.from_shared_src_rel(rel), None, None, rel, line=1)

        elif rule in (
            "SharedBoundaryRulesTest::test_shared_does_not_import_forbidden_host_classes",
            "SharedBoundaryRulesTest::test_shared_cross_module_imports_use_contracts_events_domain_or_presentation",
        ):
            shared_src = Path(raw["shared_src"])
            for item in payload or []:
                abs_file = str(shared_src / item["file"])
                emit(
                    space.from_shared_src_rel(item["file"]),
                    item["import"],
                    {
                        "file": abs_file,
                        "needle": item["import"],
                        "mode": "capture",
                        "pattern": pat.get("pattern"),
                    },
                    item,
                )

        else:  # pragma: no cover - guards against a new legacy rule appearing
            raise SystemExit(f"run_legacy_oracle: unmapped failing legacy rule {rule!r}")

    lines = resolve_lines([q for _, q in pending])
    for (finding, _), line in zip(pending, lines):
        finding["line"] = line
        findings.append(finding)

    return findings


# ---------------------------------------------------------------------------
# frontend oracle
# ---------------------------------------------------------------------------

_FE_HEAD = re.compile(r"^- \[([a-z0-9-]+)\] (.+):(\d+)$")


def parse_frontend_output(text: str, severity: str) -> list[dict]:
    """Parse the auditor's three-line record format.

    - [ruleId] path:line
      <description>
      Matched: <snippet>      (findings)
      Observed: <snippet>     (warnings)
    """
    items: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _FE_HEAD.match(lines[i].rstrip())
        if not m:
            i += 1
            continue
        rule_id, rel_path, line_no = m.group(1), m.group(2), int(m.group(3))
        description = lines[i + 1].strip() if i + 1 < len(lines) else ""
        snippet = None
        if i + 2 < len(lines):
            third = lines[i + 2].strip()
            for prefix in ("Matched: ", "Observed: "):
                if third.startswith(prefix):
                    snippet = third[len(prefix):]
        items.append(
            {
                "rule_id": rule_id,
                "relative_path": rel_path,
                "line": line_no,
                "description": description,
                "snippet": snippet,
                "severity": severity,
            }
        )
        i += 3
    return items


def run_frontend_oracle(script: Path, repo: Path, repo_key: str, space: PathSpace) -> tuple[list[dict], dict]:
    proc = subprocess.run(
        ["node", str(script)],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + "\n" + proc.stderr

    clean = "no blocked anti-patterns found" in proc.stdout
    # findings are printed to stderr, warnings to stderr when findings exist and
    # to stdout otherwise; parse both streams and split by section header.
    findings_section, warnings_section = _split_sections(combined)

    parsed = parse_frontend_output(findings_section, "error") + parse_frontend_output(warnings_section, "warning")

    out = []
    for item in parsed:
        out.append(
            {
                "source": "frontend-audit",
                "legacy_rule_id": item["rule_id"],
                "inventory_id": FRONTEND_INVENTORY.get(item["rule_id"]),
                "severity": item["severity"],
                "file": space.from_frontend(repo_key, item["relative_path"]),
                "line": item["line"],
                "target": item["snippet"],
                "message": item["description"],
                "raw": {
                    "repo": repo_key,
                    "relative_path": item["relative_path"],
                    "snippet": item["snippet"],
                },
            }
        )

    meta = {
        "repo": repo_key,
        "cwd": str(repo),
        "script": str(script),
        "exit_code": proc.returncode,
        "clean": clean,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "findings": sum(1 for f in out if f["severity"] == "error"),
        "warnings": sum(1 for f in out if f["severity"] == "warning"),
    }
    return out, meta


def _split_sections(text: str) -> tuple[str, str]:
    """Split the auditor output at the 'Warnings:' header."""
    marker = "\nWarnings:\n"
    idx = text.find(marker)
    if idx == -1:
        return text, ""
    return text[:idx], text[idx + len(marker):]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def workspace_tree_id(eval_dir: Path) -> str | None:
    frozen = eval_dir / "frozen-commits.txt"
    if not frozen.is_file():
        return None
    for line in frozen.read_text().splitlines():
        if line.startswith("WORKSPACE\t"):
            return line.split("\t")[1]
    return None


def check_inventory_ids(eval_dir: Path) -> list[str]:
    csv_path = eval_dir / "legacy_rule_inventory.csv"
    if not csv_path.is_file():
        return []
    known = {row["rule_id"] for row in csv.DictReader(csv_path.open(newline=""))}
    used = set(BACKEND_INVENTORY.values()) | set(FRONTEND_INVENTORY.values())
    return sorted(used - known)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--backend-root", type=Path, default=None)
    parser.add_argument("--frontend-repos", nargs="*", default=["litrev-frontend", "frontend-shared"])
    parser.add_argument("--audit-script-repo", default="litrev-frontend")
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "results" / "legacy_findings.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    backend_root = (args.backend_root or workspace / "litrev-backend").resolve()
    space = PathSpace(backend_root, workspace)

    unknown = check_inventory_ids(EVAL_DIR)
    if unknown:
        raise SystemExit(f"inventory ids not present in legacy_rule_inventory.csv: {unknown}")

    raw = run_backend_oracle(backend_root)
    be = backend_findings(raw, space)

    audit_script = workspace / args.audit_script_repo / "scripts" / "audit-frontend-architecture.mjs"
    fe: list[dict] = []
    fe_meta = []
    for repo_key in args.frontend_repos:
        items, meta = run_frontend_oracle(audit_script, workspace / repo_key, repo_key, space)
        fe.extend(items)
        fe_meta.append(meta)

    findings = be + fe
    findings.sort(key=lambda f: (f["source"], f["file"], f["line"] if f["line"] is not None else -1, f["legacy_rule_id"], str(f["target"])))

    per_source: dict[str, int] = {}
    per_rule: dict[str, int] = {}
    for f in findings:
        per_source[f["source"]] = per_source.get(f["source"], 0) + 1
        per_rule[f["legacy_rule_id"]] = per_rule.get(f["legacy_rule_id"], 0) + 1

    doc = {
        "schema": "legacy-findings/1",
        "tool": "legacy",
        "snapshot": {
            "workspace_tree_id": workspace_tree_id(EVAL_DIR),
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "workspace": str(workspace),
            "backend_root": str(backend_root),
            "php_version": raw["php_version"],
        },
        "oracle": {
            "php": {
                "classes": raw["classes"],
                "executions": raw["executions"],
                "errors": raw["errors"],
                "methods_executed": sum(1 for e in raw["executions"] if e["status"] == "ran"),
                "methods_errored": sum(1 for e in raw["executions"] if e["status"] == "error"),
                "assertions": sum(e["assertions"] for e in raw["executions"]),
                "failed_assertions": sum(e["failed_assertions"] for e in raw["executions"]),
            },
            "frontend": fe_meta,
        },
        "counts": {
            "total": len(findings),
            "per_source": per_source,
            "per_legacy_rule_id": per_rule,
        },
        "findings": findings,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    if not args.quiet:
        print(json.dumps({"out": str(args.out), **doc["counts"], "php": {k: v for k, v in doc["oracle"]["php"].items() if k not in ("classes", "executions", "errors")}}, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
