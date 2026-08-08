"""M1 acceptance: `limen spec validate` detects every §5.2 condition, each
terminating with exit code 3 (dead entries require --strict-spec by design)."""
from __future__ import annotations

import textwrap

from limen.cli import main
from limen.spec.loader import load_schema
from limen.spec.validate import validate_document

from conftest import spec_from  # noqa: F401

GOOD_RULE = """
  - id: ok-rule
    kind: forbidden-target
    severity: error
    scope: { paths: ["app/**"] }
    forbid: ["App\\\\Models\\\\X"]
    message: "no"
    remedy: "yes"
"""


def write_spec(tmp_path, body: str, tree: dict | None = None) -> str:
    (tmp_path / "app").mkdir(exist_ok=True)
    for rel, content in (tree or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    spec = tmp_path / "architecture.yaml"
    spec.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(spec)


BASE = """
version: 1
project: t
roots:
  host: { path: app, language: php }
units:
  discovery: [ { root: host, pattern: "app/Modules/{unit}/**" } ]
  declared: [Auth]
  registry_policy: exact
rules:
"""

AUTH_FILE = {"app/Modules/Auth/Svc.php":
             "<?php\nnamespace App\\Modules\\Auth;\nclass Svc {}\n"}


def run_validate(tmp_path, spec_path, *extra) -> int:
    return main(["spec", "--spec", spec_path, "--workspace", str(tmp_path),
                 "validate", *extra])


def test_s1_schema_invalid_exits_3(tmp_path, capsys):
    spec = write_spec(tmp_path, "version: 7\nroots: {}\nrules: []\n")
    assert run_validate(tmp_path, spec) == 3
    assert "schema-invalid" in capsys.readouterr().err + capsys.readouterr().out


def test_s1_mini_validator_reports_paths():
    schema = load_schema("architecture-spec-1.json")
    issues = validate_document({"version": 1, "roots": {"a": {"path": "x"}},
                                "rules": []}, schema)
    assert any("language" in i.message for i in issues)


def test_s2_error_without_remedy_exits_3(tmp_path):
    spec = write_spec(tmp_path, BASE + """
  - id: bad
    kind: forbidden-target
    severity: error
    scope: { paths: ["app/**"] }
    forbid: ["X"]
    message: "no remedy here"
""", AUTH_FILE)
    assert run_validate(tmp_path, spec) == 3


def test_s3_unknown_owner_unit_exits_3(tmp_path):
    spec = write_spec(tmp_path, BASE.replace("rules:", """
artifact_sets:
  models:
    discovery: { root: host, pattern: "app/Models/*.php" }
rules:""") + """
  - id: own
    kind: ownership
    severity: error
    artifact_set: models
    owners: { "App\\\\Models\\\\User": Ghost }
    message: "m"
    remedy: "r"
""", AUTH_FILE)
    assert run_validate(tmp_path, spec) == 3


def test_s4_dead_entry_warns_by_default_and_exits_3_when_strict(tmp_path, capsys):
    body = BASE + """
  - id: api
    kind: public-api-only
    severity: error
    scope: { roots: [host] }
    exempt: ["App\\\\Ghost\\\\Nothing"]
    message: "m"
    remedy: "r"
"""
    spec = write_spec(tmp_path, body, AUTH_FILE)
    assert run_validate(tmp_path, spec) == 0
    out = capsys.readouterr().out
    assert "dead-entry" in out
    assert run_validate(tmp_path, spec, "--strict-spec") == 3


def test_s5_duplicate_rule_id_exits_3(tmp_path):
    spec = write_spec(tmp_path, BASE + GOOD_RULE + GOOD_RULE, AUTH_FILE)
    assert run_validate(tmp_path, spec) == 3


def test_s6_registry_exact_mismatch_exits_3(tmp_path):
    tree = dict(AUTH_FILE)
    tree["app/Modules/Rogue/S.php"] = "<?php\nnamespace App\\Modules\\Rogue;\nclass S {}\n"
    spec = write_spec(tmp_path, BASE + GOOD_RULE, tree)
    assert run_validate(tmp_path, spec) == 3


def test_s7_regex_invalid_exits_3(tmp_path):
    spec = write_spec(tmp_path, BASE + """
  - id: ban
    kind: pattern-ban
    severity: error
    scope: { roots: [host] }
    pattern: "([unclosed"
    message: "m"
    remedy: "r"
""", AUTH_FILE)
    assert run_validate(tmp_path, spec) == 3


def test_s8_expires_format_exits_3(tmp_path):
    spec = write_spec(tmp_path, BASE + """
  - id: api
    kind: public-api-only
    severity: error
    scope: { roots: [host] }
    exempt:
      - { target: "App\\\\Modules\\\\Auth\\\\Svc", expires: "next tuesday" }
    message: "m"
    remedy: "r"
""", AUTH_FILE)
    assert run_validate(tmp_path, spec) == 3


def test_valid_spec_exits_0(tmp_path):
    spec = write_spec(tmp_path, BASE + GOOD_RULE, AUTH_FILE)
    assert run_validate(tmp_path, spec) == 0
