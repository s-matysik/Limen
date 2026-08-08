"""Focused unit tests for the smaller core surfaces: util, Scope, FileTree,
RuleContext helpers, validate_document, merge ambiguity, loader errors and
the unresolved-dependency policy in the engine."""
import pytest
import yaml

from limen.engine import run_check
from limen.facts.merge import resolve_units_layers
from limen.facts.model import Unresolved
from limen.report import human
from limen.rules.base import FileTree, RuleContext, Scope
from limen.spec.loader import load_schema, load_spec
from limen.spec.model import SpecError
from limen.spec.validate import validate_document, validate_integrity
from limen.util import (fqcn_match, glob_to_regex, parse_iso_date, path_match,
                        posix, token_estimate)

from conftest import TODAY, FakeTree, ff, make_ctx, make_index, spec_from


# --------------------------------------------------------------------- util

def test_glob_alternation_question_and_capture():
    assert path_match("src/{unit}/api.{js,ts}", "src/billing/api.ts")
    assert not path_match("src/{unit}/api.{js,ts}", "src/billing/api.php")
    assert path_match("app/?at.php", "app/cat.php")
    assert not path_match("app/?at.php", "app/coat.php")
    m = glob_to_regex("src/{unit}/**").match("src/core/deep/mod.py")
    assert m and m.group("unit") == "core"
    # unmatched brace is literal
    assert path_match("app/{x.php", "app/{x.php")


def test_glob_doublestar_slash_matches_zero_dirs():
    assert path_match("app/**/Models/*.php", "app/Models/User.php")
    assert path_match("app/**/Models/*.php", "app/Sub/Models/User.php")


def test_fqcn_star_stays_in_segment():
    assert fqcn_match("App\\Models\\*", "App\\Models\\User")
    assert not fqcn_match("App\\Models\\*", "App\\Models\\Sub\\User")
    assert fqcn_match("App\\**", "App\\Models\\Sub\\User")


def test_posix_and_token_estimate():
    assert posix("a\\b\\c.php") == "a/b/c.php"
    assert token_estimate("abcd") == 1
    assert token_estimate("") == 0


def test_parse_iso_date_variants():
    import datetime
    assert parse_iso_date("2026-01-02").isoformat() == "2026-01-02"
    assert parse_iso_date(datetime.date(2026, 1, 2)).isoformat() == "2026-01-02"
    assert parse_iso_date(datetime.datetime(2026, 1, 2, 3)).isoformat() == "2026-01-02"
    with pytest.raises(ValueError):
        parse_iso_date(20260102)
    with pytest.raises(ValueError):
        parse_iso_date("02.01.2026")


# -------------------------------------------------------------------- Scope

def test_scope_roots_include_exclude_and_pair():
    s = Scope({"roots": ["host"], "include": ["app/**"],
               "exclude": ["app/vendor/**"]})
    a = ff("app/A.php")
    a.root = "host"
    v = ff("app/vendor/lib.php")
    v.root = "host"
    other = ff("web/B.php")
    other.root = "web"
    assert s.file_matches(a)
    assert not s.file_matches(v)
    assert not s.file_matches(other)
    assert s.path_matches("app/A.php", root="host")
    assert not s.path_matches("app/A.php", root="web")

    pair = Scope({"from_root": "host", "to_root": "core"})
    src = ff("app/A.php")
    src.root = "host"
    tgt = ff("core/B.php")
    tgt.root = "core"
    stray = ff("web/C.php")
    stray.root = "web"
    assert pair.file_matches(src)
    assert pair.dep_matches(src, tgt)
    assert not pair.dep_matches(src, stray)
    assert not pair.dep_matches(src, None)


# ------------------------------------------------------------ FileTree (fs)

def test_filetree_on_disk(tmp_path):
    (tmp_path / "app" / "Modules").mkdir(parents=True)
    (tmp_path / "app" / "Modules" / "a.php").write_text("x")
    t = FileTree(tmp_path)
    assert t.exists("app/Modules/a.php")
    assert t.is_dir("app/Modules")
    assert not t.is_dir("app/Modules/a.php")
    assert t.read_text("app/Modules/a.php") == "x"
    assert t.read_text("app/nope.php") is None
    dirs = t.walk_dirs("app")
    assert "app/Modules" in dirs


# ------------------------------------------------- RuleContext conveniences

CTX_SPEC = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Auth]
artifact_sets:
  models:
    discovery: { root: host, pattern: "app/Models/*.php" }
  by_path:
    identity: path
    discovery: { pattern: "app/Config/*.php" }
rules:
  - id: own
    kind: ownership
    severity: error
    artifact_set: models
    owners: { "App\\\\Models\\\\User": Auth }
    message: "m"
    remedy: "r"
"""


def test_owner_of_and_artifact_members():
    spec = spec_from(CTX_SPEC)
    idx = make_index([
        ff("app/Models/User.php", declares=["App\\Models\\User"]),
        ff("app/Config/app.php", declares=[]),
        ff("app/Other.php", declares=["App\\Other"]),
    ])
    ctx = make_ctx(spec, idx)
    assert ctx.owner_of("models", "App\\Models\\User") == "Auth"
    assert ctx.owner_of("models", "App\\Models\\Nope") is None
    assert ctx.owner_of("nope", "x") is None
    members = ctx.artifact_members("models")
    assert list(members) == ["App\\Models\\User"]
    by_path = ctx.artifact_members("by_path")
    assert list(by_path) == ["app/Config/app.php"]
    assert ctx.artifact_members("missing") == {}


# ------------------------------------------------- validate_document (S1)

def test_mini_validator_branches():
    schema = load_schema("architecture-spec-1.json")
    # wrong scalar type deep inside
    issues = validate_document(
        {"version": 1, "roots": {"a": {"path": 5, "language": "php"}},
         "rules": []}, schema)
    assert any("roots.a.path" in i.message for i in issues)
    # enum violation
    issues = validate_document(
        {"version": 1,
         "roots": {"a": {"path": "x", "language": "php"}},
         "rules": [{"id": "r", "kind": "no-such-kind", "severity": "error",
                    "message": "m", "remedy": "r"}]}, schema)
    assert any("rules[0].kind" in i.message for i in issues)
    # wrong container type
    issues = validate_document({"version": 1, "roots": [], "rules": []}, schema)
    assert any(".roots" in i.message for i in issues)
    # missing required
    issues = validate_document({"roots": {}, "rules": []}, schema)
    assert any("version" in i.message for i in issues)


# -------------------------------------------- validate_integrity S3 variants

def _integrity(spec_yaml, files):
    spec = spec_from(spec_yaml)
    idx = make_index(files)
    resolve_units_layers(spec, idx)
    return validate_integrity(spec, index=idx, today=TODAY)


def test_s3_unknown_units_all_rule_kinds():
    y = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Auth]
rules:
  - id: c
    kind: consumer-allowlist
    severity: error
    artifact_set: models
    allow: { "App\\\\Models\\\\User": [Ghost] }
    message: m
    remedy: r
  - id: b
    kind: baseline-snapshot
    severity: error
    target_pattern: "App\\\\Models\\\\*"
    exclude_units: [Ghost]
    expected: {}
    message: m
    remedy: r
  - id: g
    kind: reachability
    severity: error
    mode: independence
    units: [Auth, Ghost]
    message: m
    remedy: r
"""
    issues = _integrity(y, [ff("app/Models/User.php",
                               declares=["App\\Models\\User"])])
    codes = sorted((i.code, i.path) for i in issues if i.code == "unknown-unit")
    assert len(codes) == 3
    # unknown-artifact-set is reported for rule c as well
    assert any(i.code == "unknown-artifact-set" for i in issues)


def test_s4_dead_path_entry_for_pattern_ban():
    y = """
version: 1
roots:
  host: { path: app, language: php }
rules:
  - id: p
    kind: pattern-ban
    severity: error
    pattern: "x"
    exempt_files:
      - { path: "app/Ghost.php", reason: gone }
    message: m
    remedy: r
"""
    issues = _integrity(y, [ff("app/Real.php")])
    dead = [i for i in issues if i.code == "dead-entry"]
    assert len(dead) == 1 and dead[0].level == "warning"
    assert "app/Ghost.php" in dead[0].message


def test_s7_bad_regex_in_extractor_config():
    y = """
version: 1
roots:
  host: { path: app, language: php }
extractors:
  php:
    patterns:
      - { via: use, regex: "([", group: 1 }
rules: []
"""
    spec = spec_from(y)
    issues = validate_integrity(spec, index=None, today=TODAY)
    assert any(i.code == "regex-invalid"
               and "extractors.php.patterns[0]" in i.path for i in issues)


# ----------------------------------------------------------- merge/ambiguity

def test_unit_discovery_ambiguity_is_spec_error():
    y = """
version: 1
roots:
  host: { path: app, language: php }
units:
  discovery:
    - { root: host, pattern: "app/Modules/{unit}/**" }
    - { root: host, pattern: "app/{unit}/Billing/**" }
  declared: [Auth, Modules]
rules: []
"""
    spec = spec_from(y)
    idx = make_index([ff("app/Modules/Billing/A.php")])
    with pytest.raises(SpecError) as exc:
        resolve_units_layers(spec, idx)
    assert any(i.code == "unit-ambiguous" for i in exc.value.issues)


def test_registry_policy_open_accepts_new_units():
    y = """
version: 1
roots:
  host: { path: app, language: php }
units:
  discovery:
    - { root: host, pattern: "app/Modules/{unit}/**" }
  declared: [Auth]
  registry_policy: open
rules: []
"""
    spec = spec_from(y)
    idx = make_index([ff("app/Modules/Newbie/A.php")])
    resolve_units_layers(spec, idx)
    assert idx.files[0].unit == "Newbie"
    issues = validate_integrity(spec, index=idx, today=TODAY)
    assert not any(i.code == "unregistered-unit" for i in issues)


# ------------------------------------------------------------ loader errors

def test_load_spec_bad_yaml(tmp_path):
    p = tmp_path / "architecture.yaml"
    p.write_text("version: [unclosed\n")
    with pytest.raises(SpecError) as exc:
        load_spec(p)
    assert exc.value.issues[0].code == "yaml-parse"


def test_load_spec_top_level_not_mapping(tmp_path):
    p = tmp_path / "architecture.yaml"
    p.write_text("- just\n- a list\n")
    with pytest.raises(SpecError) as exc:
        load_spec(p)
    assert exc.value.issues[0].code == "yaml-shape"


# --------------------------------------------- engine: unresolved policies

POLICY_SPEC = """
version: 1
roots:
  host: { path: app, language: php }
unresolved_policy:
  default: %s
rules: []
"""


def _policy_report(policy):
    spec = spec_from(POLICY_SPEC % policy)
    idx = make_index([ff("app/A.php")],
                     unresolved=[Unresolved("app/A.php", 7, "new $cls",
                                            "dynamic")])
    return run_check(spec, idx, today=TODAY)


def test_unresolved_policy_matrix():
    warn = _policy_report("warn")
    assert warn.warnings == 1 and warn.errors == 0
    assert warn.findings[0].rule_id == "unresolved-dependency"
    err = _policy_report("error")
    assert err.errors == 1 and err.exit_code() == 1
    off = _policy_report("off")
    assert off.findings == [] and off.exit_code() == 0
    # --strict: a lone warning exits 2
    assert warn.exit_code(strict=True) == 2
    assert warn.exit_code(strict=False) == 0


def test_engine_only_filter():
    spec = spec_from("""
version: 1
roots:
  host: { path: app, language: php }
rules:
  - id: a
    kind: forbidden-target
    severity: error
    forbid: ["X"]
    message: m
    remedy: r
  - id: b
    kind: forbidden-target
    severity: error
    forbid: ["Y"]
    message: m
    remedy: r
""")
    idx = make_index([ff("app/A.php", deps=[("X", 1, "use"), ("Y", 2, "use")])])
    rep = run_check(spec, idx, today=TODAY, only={"b"})
    assert [f.rule_id for f in rep.findings] == ["b"]


# ------------------------------------------------------------- human render

def test_human_render_groups_and_remedies():
    spec = spec_from("""
version: 1
roots:
  host: { path: app, language: php }
rules:
  - id: a
    kind: forbidden-target
    severity: error
    forbid: ["X"]
    message: "X is banned."
    remedy: "Use Y."
  - id: w
    kind: metric-threshold
    metric: lines
    max: 1
    severity: warning
    message: "Long."
    remedy: "Split."
""")
    idx = make_index([ff("app/A.php", lines=5, deps=[("X", 3, "use")])])
    rep = run_check(spec, idx, today=TODAY)
    text = human.render(rep)
    assert "app/A.php:3" in text
    assert "Use Y." in text
    assert "warning" in text.lower()
    ok = human.render(run_check(spec_from(POLICY_SPEC % "warn"),
                                make_index([ff("app/A.php")]), today=TODAY))
    assert "0 error" in ok or "passed" in ok.lower() or "OK" in ok
