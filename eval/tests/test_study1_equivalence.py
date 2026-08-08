"""Unit tests for the Study 1 diff gate (eval/study1_equivalence.py).

The oracle side of the comparison is produced by a separate track. These tests
run the gate against **synthetic fixtures** built here, so the decision rules
and the gate behaviour are verified independently of whether the real
`legacy_findings.json` exists yet.

Run:
    export PYTHONPATH=<LIMEN>/limen/src
    python -m pytest eval/tests/test_study1_equivalence.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1]
REPO = EVAL.parent
sys.path.insert(0, str(EVAL))

from run_limen_check import canonical_file, canonical_target, key_str  # noqa: E402
import study1_equivalence as S1  # noqa: E402


# ------------------------------------------------------------------ fixtures
@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """A synthetic workspace with the file shapes the classifiers read."""
    root = tmp_path / "workspace"

    php = root / "litrev-backend/app/Modules/Demo/Http/Controllers"
    php.mkdir(parents=True)
    (php / "CodeController.php").write_text(
        "<?php\n"
        "namespace App\\Modules\\Demo\\Http\\Controllers;\n"
        "use LitRev\\BackendShared\\Audit\\Application\\AuditLogService;\n"
        "class CodeController {}\n", encoding="utf-8")
    (php / "CommentController.php").write_text(
        "<?php\n"
        "namespace App\\Modules\\Demo\\Http\\Controllers;\n"
        "// use App\\Modules\\Other\\Application\\OtherService;\n"
        "/* use App\\Modules\\Third\\Application\\ThirdService; */\n"
        "class CommentController {}\n", encoding="utf-8")
    (php / "StringController.php").write_text(
        "<?php\n"
        "namespace App\\Modules\\Demo\\Http\\Controllers;\n"
        "$doc = 'use App\\\\Modules\\\\Other\\\\Application\\\\OtherService;';\n"
        "class StringController {}\n", encoding="utf-8")

    docs = root / "backend-shared"
    docs.mkdir(parents=True)
    (docs / "CLAUDE.md").write_text(
        "# shared\n"
        "**Forbidden:** importing concrete Application classes from a sibling "
        "module (actions, services).\n", encoding="utf-8")
    (root / "backend-shared/src").mkdir(parents=True)
    (root / "backend-shared/src/AGENTS.md").write_text(
        "- Między modułami preferuj:\n  - `Application\\Contracts\\*`\n",
        encoding="utf-8")
    mods = root / "litrev-backend/app/Modules"
    (mods / "AGENTS.md").write_text(
        "- Między modułami używaj kontraktów, interfejsów albo fasad modułowych.\n",
        encoding="utf-8")

    legacy = root / "litrev-backend/tests/Architecture"
    legacy.mkdir(parents=True)
    (legacy / "ApplicationBoundaryRulesTest.php").write_text(
        "<?php\n"
        "preg_match_all('/use\\s+App\\\\\\\\Modules\\\\\\\\([^\\\\\\\\]+)"
        "\\\\\\\\Application\\\\\\\\([^;]+);/m', $contents, $matches);\n",
        encoding="utf-8")
    (legacy / "HttpBoundaryRulesTest.php").write_text(
        "<?php\n"
        "preg_match_all('/use\\s+App\\\\\\\\Modules\\\\\\\\([^\\\\\\\\]+)"
        "\\\\\\\\Http\\\\\\\\/m', $contents, $matches);\n",
        encoding="utf-8")
    return root


FIXTURE_TREE_ID = "0" * 64


def _limen(rows, tree_id: str = FIXTURE_TREE_ID):
    return {"schema": "limen-findings/1", "tool": "limen",
            "snapshot": {"workspace_id": tree_id}, "findings": rows}


def _legacy(rows, tree_id: str = FIXTURE_TREE_ID):
    return {"schema": "legacy-findings/1", "tool": "legacy",
            "snapshot": {"workspace_tree_id": tree_id}, "findings": rows}


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run_gate(tmp_path: Path, ws: Path, limen_rows, legacy_rows,
             deviations: str | None = None, missing_oracle: str = "fail"):
    lim = _write(tmp_path / "in/limen.json", _limen(limen_rows))
    leg = tmp_path / "in/legacy.json"
    if legacy_rows is not None:
        _write(leg, _legacy(legacy_rows))
    dev = tmp_path / "deviations.md"
    dev.write_text(deviations if deviations is not None
                   else "# Deviations\n\n## D-01 — demo (engine-gap)\n\ntext\n",
                   encoding="utf-8")
    out = tmp_path / "out/diff.json"
    proc = subprocess.run(
        [sys.executable, str(EVAL / "study1_equivalence.py"),
         "--limen", str(lim), "--legacy", str(leg), "--workspace", str(ws),
         "--deviations", str(dev), "--out", str(out),
         "--missing-oracle", missing_oracle],
        capture_output=True, text=True)
    payload = json.loads(out.read_text()) if out.is_file() else None
    return proc, payload


# ------------------------------------------------------------ normalisation
def test_canonical_file_strips_snapshot_prefix():
    assert canonical_file(
        "litrev-arch-snapshot/litrev-backend/app/X.php"
    ) == "litrev-backend/app/X.php"


def test_canonical_file_is_idempotent():
    once = canonical_file("litrev-arch-snapshot/litrev-frontend/src/main.jsx")
    assert canonical_file(once) == once


def test_canonical_file_maps_legacy_base_path():
    assert canonical_file("app/Modules/X/Y.php") == "litrev-backend/app/Modules/X/Y.php"
    assert canonical_file("src/modules/a/index.js") == "litrev-frontend/src/modules/a/index.js"


def test_canonical_file_maps_shared_src_marker():
    assert canonical_file("__shared_src__/Folders/Application/S.php") == \
        "backend-shared/src/Folders/Application/S.php"


def test_canonical_target_drops_leading_separator():
    assert canonical_target("\\App\\X\\Y") == "App\\X\\Y"
    assert canonical_target("@/modules/a") == "@/modules/a"


def test_key_str_shape():
    assert key_str(("a.php", 3, "T")) == "a.php:3:T"
    assert key_str((None, None, None)) == "-:-:-"


# ------------------------------------------------------------- span scanner
def test_span_classes_marks_line_comment():
    text = "<?php\n// use App\\X;\n"
    cls = S1.span_classes(text, "php")
    assert cls[text.index("use App")] == S1.COMMENT


def test_span_classes_marks_block_comment():
    text = "<?php\n/* use App\\X; */\n"
    cls = S1.span_classes(text, "php")
    assert cls[text.index("use App")] == S1.COMMENT


def test_span_classes_marks_string():
    text = "<?php\n$s = 'use App\\\\X;';\n"
    cls = S1.span_classes(text, "php")
    assert cls[text.index("use App")] == S1.STRING


def test_span_classes_keeps_real_code_as_code():
    text = "<?php\nuse App\\X;\n"
    cls = S1.span_classes(text, "php")
    assert cls[text.index("use App")] == S1.CODE


def test_php_attribute_hash_is_not_a_comment():
    text = "<?php\n#[Attr]\nuse App\\X;\n"
    cls = S1.span_classes(text, "php")
    assert cls[text.index("use App")] == S1.CODE


# -------------------------------------------------------------- category C1
def test_c1_legacy_only_in_line_comment(tmp_path, ws):
    legacy = [{
        "source": "legacy", "legacy_rule_id": "L-BE-APP-01",
        "inventory_id": "L-BE-APP-01", "severity": "error",
        "file": "app/Modules/Demo/Http/Controllers/CommentController.php",
        "line": 3, "target": "App\\Modules\\Other\\Application\\OtherService",
        "message": "forbidden cross-module import", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, [], legacy)
    assert proc.returncode == 0, proc.stderr
    d = payload["differences"][0]
    assert d["classification"]["category"] == "C1"
    assert d["classification"]["evidence"]["source_line"]["span_class"] == "comment"
    assert "// use App" in d["classification"]["evidence"]["source_line"]["source_line"]


def test_c1_legacy_only_in_block_comment(tmp_path, ws):
    legacy = [{
        "source": "legacy", "legacy_rule_id": "L-BE-APP-01",
        "inventory_id": "L-BE-APP-01", "severity": "error",
        "file": "app/Modules/Demo/Http/Controllers/CommentController.php",
        "line": 4, "target": "App\\Modules\\Third\\Application\\ThirdService",
        "message": "forbidden cross-module import", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, [], legacy)
    assert proc.returncode == 0
    assert payload["differences"][0]["classification"]["category"] == "C1"


def test_c1_legacy_only_in_string_literal(tmp_path, ws):
    legacy = [{
        "source": "legacy", "legacy_rule_id": "L-BE-APP-01",
        "inventory_id": "L-BE-APP-01", "severity": "error",
        "file": "app/Modules/Demo/Http/Controllers/StringController.php",
        "line": 3, "target": "App\\Modules\\Other\\Application\\OtherService",
        "message": "forbidden cross-module import", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, [], legacy)
    assert proc.returncode == 0
    ev = payload["differences"][0]["classification"]["evidence"]["source_line"]
    assert payload["differences"][0]["classification"]["category"] == "C1"
    assert ev["span_class"] == "string"


# -------------------------------------------------------------- category C2
def test_c2_limen_only_backed_by_prose(tmp_path, ws):
    limen = [{
        "source": "limen", "rule_id": "host-cross-module-public-surface",
        "kind": "public-api-only", "severity": "error", "spec_path": "rules[3].exempt",
        "file": "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php",
        "line": 3, "target": "LitRev\\BackendShared\\Audit\\Application\\AuditLogService",
        "message": "public surface", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, limen, [])
    assert proc.returncode == 0, proc.stderr
    d = payload["differences"][0]
    assert d["classification"]["category"] == "C2"
    ev = d["classification"]["evidence"]
    assert len(ev["documentation"]) == 3
    assert len(ev["legacy_blindness"]) == 2
    assert ev["source_line"]["span_class"] == "code"


def test_c2_refuses_when_prose_evidence_absent(tmp_path, ws):
    (ws / "backend-shared/CLAUDE.md").write_text("# shared\n", encoding="utf-8")
    (ws / "backend-shared/src/AGENTS.md").write_text("# nothing\n", encoding="utf-8")
    (ws / "litrev-backend/app/Modules/AGENTS.md").write_text("# nothing\n",
                                                             encoding="utf-8")
    limen = [{
        "source": "limen", "rule_id": "host-cross-module-public-surface",
        "kind": "public-api-only", "severity": "error", "spec_path": "rules[3].exempt",
        "file": "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php",
        "line": 3, "target": "LitRev\\BackendShared\\Audit\\Application\\AuditLogService",
        "message": "public surface", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, limen, [])
    assert proc.returncode == 1
    assert payload["counts"]["unclassified"] == 1


def test_c2_refuses_when_legacy_blindness_no_longer_holds(tmp_path, ws):
    # legacy checker rewritten so it *would* match LitRev\ imports
    (ws / "litrev-backend/tests/Architecture/ApplicationBoundaryRulesTest.php"
     ).write_text("<?php\npreg_match_all('/use\\s+[^;]+;/m', $c, $m);\n",
                  encoding="utf-8")
    (ws / "litrev-backend/tests/Architecture/HttpBoundaryRulesTest.php"
     ).write_text("<?php\npreg_match_all('/use\\s+[^;]+;/m', $c, $m);\n",
                  encoding="utf-8")
    limen = [{
        "source": "limen", "rule_id": "host-cross-module-public-surface",
        "kind": "public-api-only", "severity": "error", "spec_path": "rules[3].exempt",
        "file": "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php",
        "line": 3, "target": "LitRev\\BackendShared\\Audit\\Application\\AuditLogService",
        "message": "public surface", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, limen, [])
    assert proc.returncode == 1
    assert payload["counts"]["unclassified"] == 1


# -------------------------------------------------------------- category C3
def test_c3_classifies_via_registered_deviation(tmp_path, ws, monkeypatch):
    limen = [{
        "source": "limen", "rule_id": "demo-rule", "kind": "structure",
        "severity": "error", "spec_path": "rules[9]",
        "file": "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php",
        "line": 3, "target": "T", "message": "m", "raw": {},
    }]
    lim = _write(tmp_path / "in/limen.json", _limen(limen))
    leg = _write(tmp_path / "in/legacy.json", _legacy([]))
    dev = tmp_path / "deviations.md"
    dev.write_text("## D-42 — demo semantic change (engine-gap)\n", encoding="utf-8")
    out = tmp_path / "out/diff.json"
    monkeypatch.setitem(S1.DEVIATION_INDEX, "demo-rule", ["D-42"])
    monkeypatch.setattr(sys, "argv",
                        ["s", "--limen", str(lim), "--legacy", str(leg),
                         "--workspace", str(ws), "--deviations", str(dev),
                         "--out", str(out)])
    assert S1.main() == 0
    payload = json.loads(out.read_text())
    d = payload["differences"][0]
    assert d["classification"]["category"] == "C3"
    assert d["classification"]["evidence"]["deviations"][0]["id"] == "D-42"


def test_c3_refuses_dangling_deviation_reference(tmp_path, ws, monkeypatch):
    limen = [{
        "source": "limen", "rule_id": "demo-rule", "kind": "structure",
        "severity": "error", "spec_path": "rules[9]",
        "file": "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php",
        "line": 3, "target": "T", "message": "m", "raw": {},
    }]
    lim = _write(tmp_path / "in/limen.json", _limen(limen))
    leg = _write(tmp_path / "in/legacy.json", _legacy([]))
    dev = tmp_path / "deviations.md"
    dev.write_text("## D-01 — other (engine-gap)\n", encoding="utf-8")
    out = tmp_path / "out/diff.json"
    monkeypatch.setitem(S1.DEVIATION_INDEX, "demo-rule", ["D-99"])
    monkeypatch.setattr(sys, "argv",
                        ["s", "--limen", str(lim), "--legacy", str(leg),
                         "--workspace", str(ws), "--deviations", str(dev),
                         "--out", str(out)])
    assert S1.main() == 1


def test_c3_refuses_legacy_defect_deviation(tmp_path, ws, monkeypatch):
    """A `legacy-defect` deviation is evidence for C2, never for C3."""
    limen = [{
        "source": "limen", "rule_id": "demo-rule", "kind": "structure",
        "severity": "error", "spec_path": "rules[9]",
        "file": "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php",
        "line": 3, "target": "T", "message": "m", "raw": {},
    }]
    lim = _write(tmp_path / "in/limen.json", _limen(limen))
    leg = _write(tmp_path / "in/legacy.json", _legacy([]))
    dev = tmp_path / "deviations.md"
    dev.write_text("## D-08 — blind legacy (legacy-defect, retrofit is stricter)\n",
                   encoding="utf-8")
    out = tmp_path / "out/diff.json"
    monkeypatch.setitem(S1.DEVIATION_INDEX, "demo-rule", ["D-08"])
    monkeypatch.setattr(sys, "argv",
                        ["s", "--limen", str(lim), "--legacy", str(leg),
                         "--workspace", str(ws), "--deviations", str(dev),
                         "--out", str(out)])
    assert S1.main() == 1


# ------------------------------------------------------------------- gating
def test_agreement_is_not_a_difference(tmp_path, ws):
    key_file = "litrev-arch-snapshot/litrev-backend/app/Modules/Demo/Http/Controllers/CodeController.php"
    limen = [{
        "source": "limen", "rule_id": "r", "kind": "public-api-only",
        "severity": "error", "spec_path": "rules[3]", "file": key_file, "line": 3,
        "target": "LitRev\\BackendShared\\Audit\\Application\\AuditLogService",
        "message": "m", "raw": {},
    }]
    legacy = [{
        "source": "legacy", "legacy_rule_id": "L-1", "inventory_id": "L-1",
        "severity": "error",
        "file": "app/Modules/Demo/Http/Controllers/CodeController.php", "line": 3,
        "target": "LitRev\\BackendShared\\Audit\\Application\\AuditLogService",
        "message": "m", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, limen, legacy)
    assert proc.returncode == 0
    assert payload["counts"]["agreements"] == 1
    assert payload["counts"]["differences_total"] == 0


def test_unclassified_difference_fails_the_gate(tmp_path, ws):
    legacy = [{
        "source": "legacy", "legacy_rule_id": "L-9", "inventory_id": "L-9",
        "severity": "error",
        "file": "app/Modules/Demo/Http/Controllers/CodeController.php", "line": 3,
        "target": "LitRev\\BackendShared\\Audit\\Application\\AuditLogService",
        "message": "m", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, [], legacy)
    assert proc.returncode == 1
    assert payload["counts"]["unclassified"] == 1
    assert "GATE FAILED" in proc.stderr


def test_unresolved_dependency_is_out_of_scope_not_a_difference(tmp_path, ws):
    limen = [{
        "source": "limen", "rule_id": "unresolved-dependency", "kind": "unresolved",
        "severity": "warning", "spec_path": "unresolved_policy",
        "file": "litrev-arch-snapshot/litrev-frontend/src/main.jsx", "line": 3,
        "target": "./index.css", "message": "unresolved", "raw": {},
    }]
    proc, payload = run_gate(tmp_path, ws, limen, [])
    assert proc.returncode == 0
    assert payload["counts"]["differences_total"] == 0
    assert payload["counts"]["limen_findings_out_of_scope"] == 1
    assert payload["out_of_scope"][0]["rule_id"] == "unresolved-dependency"


def test_missing_oracle_fails_by_default(tmp_path, ws):
    proc, payload = run_gate(tmp_path, ws, [], None)
    assert proc.returncode == 4
    assert payload is None


def test_missing_oracle_assume_empty_marks_provisional(tmp_path, ws):
    proc, payload = run_gate(tmp_path, ws, [], None,
                             missing_oracle="assume-empty")
    assert proc.returncode == 0
    assert payload["provisional"] is True
    assert payload["oracle_available"] is False


def test_snapshot_mismatch_refuses_to_diff(tmp_path, ws):
    lim = _write(tmp_path / "in/limen.json", _limen([], tree_id="a" * 64))
    leg = _write(tmp_path / "in/legacy.json", _legacy([], tree_id="b" * 64))
    dev = tmp_path / "deviations.md"
    dev.write_text("## D-01 — demo (engine-gap)\n", encoding="utf-8")
    out = tmp_path / "out/diff.json"
    proc = subprocess.run(
        [sys.executable, str(EVAL / "study1_equivalence.py"),
         "--limen", str(lim), "--legacy", str(leg), "--workspace", str(ws),
         "--deviations", str(dev), "--out", str(out)],
        capture_output=True, text=True)
    assert proc.returncode == 4
    assert "snapshot mismatch" in proc.stderr
    assert not out.is_file()


def test_snapshot_match_is_recorded(tmp_path, ws):
    proc, payload = run_gate(tmp_path, ws, [], [])
    assert proc.returncode == 0
    assert payload["snapshot"]["match"] is True
    assert payload["snapshot"]["limen_workspace_id"] == FIXTURE_TREE_ID
    assert payload["snapshot"]["legacy_workspace_id"] == FIXTURE_TREE_ID


def test_deviation_headings_parse_from_the_real_file():
    dev = S1.parse_deviations(REPO / "docs/deviations.md")
    assert "D-01" in dev and "D-10" in dev
    assert dev["D-08"]["kind"] == "legacy-defect"
    assert dev["D-03"]["kind"] == "engine-gap"
