"""Unit tests for K7–K11 on injected facts and a fake tree."""
from datetime import date

from limen.rules import (baseline, metric, pattern, reachability, structure)

from conftest import FakeTree, ff, make_ctx, make_index, spec_from

YAML_HEAD = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Billing, Core, Reporting]
rules:
"""


def _spec(rules_yaml: str):
    return spec_from(YAML_HEAD + rules_yaml)


# ---------------------------------------------------------------- K7 baseline

K7_RULE = """
  - id: k7
    kind: baseline-snapshot
    severity: error
    target_pattern: "App\\\\Models\\\\*"
    scope: { roots: [host] }
    expected:
      "app/Modules/Billing/Invoice.php":
        - target: "App\\\\Models\\\\User"
          reason: "legacy join"
    message: "Direct model imports must match the declared baseline."
    remedy: "Update expected: or remove the import."
"""


def test_k7_declared_pair_passes():
    spec = _spec(K7_RULE)
    idx = make_index([
        ff("app/Modules/Billing/Invoice.php", unit="Billing",
           deps=[("App\\Models\\User", 5, "use")]),
    ])
    assert baseline.check(spec.rules[0], make_ctx(spec, idx)) == []


def test_k7_undeclared_import_fails():
    spec = _spec(K7_RULE)
    idx = make_index([
        ff("app/Modules/Billing/Invoice.php", unit="Billing",
           deps=[("App\\Models\\User", 5, "use")]),
        ff("app/Modules/Reporting/Export.php", unit="Reporting",
           deps=[("App\\Models\\User", 9, "use")]),
    ])
    fs = baseline.check(spec.rules[0], make_ctx(spec, idx))
    assert len(fs) == 1
    f = fs[0]
    assert f.file == "app/Modules/Reporting/Export.php" and f.line == 9
    assert "not in the declared baseline" in f.message


def test_k7_dead_entry_is_error():
    spec = _spec(K7_RULE)
    idx = make_index([ff("app/Modules/Billing/Other.php", unit="Billing")])
    fs = baseline.check(spec.rules[0], make_ctx(spec, idx))
    assert len(fs) == 1
    assert fs[0].severity == "error"
    assert "dead baseline entry" in fs[0].message
    assert fs[0].file == "app/Modules/Billing/Invoice.php"


def test_k7_boundary_self_declaration_not_counted():
    # A file "importing" a symbol it declares itself is not a cross reference.
    spec = _spec(K7_RULE)
    idx = make_index([
        ff("app/Modules/Billing/Invoice.php", unit="Billing",
           declares=["App\\Models\\User"],
           deps=[("App\\Models\\User", 3, "use")]),
    ])
    fs = baseline.check(spec.rules[0], make_ctx(spec, idx))
    # the actual import vanished -> only the dead-entry error remains
    assert len(fs) == 1 and "dead baseline entry" in fs[0].message


def test_k7_expired_expected_entry_escalates():
    rule_yaml = K7_RULE.replace('reason: "legacy join"',
                                'reason: "legacy join"\n          expires: 2026-01-01')
    spec = _spec(rule_yaml)
    idx = make_index([
        ff("app/Modules/Billing/Invoice.php", unit="Billing",
           deps=[("App\\Models\\User", 5, "use")]),
    ])
    fs = baseline.check(spec.rules[0], make_ctx(spec, idx))
    assert len(fs) == 1 and fs[0].severity == "error"
    assert "expired on 2026-01-01" in fs[0].message


# ---------------------------------------------------------------- K8 pattern

K8_RULE = """
  - id: k8
    kind: pattern-ban
    severity: error
    pattern: "\\\\bDB::table\\\\("
    scope: { paths: ["app/**"] }
    exempt_files: []
    message: "Raw query builder calls are banned."
    remedy: "Use the repository."
"""


def test_k8_hit_and_miss():
    spec = _spec(K8_RULE)
    idx = make_index([ff("app/A.php", unit="Core"), ff("app/B.php", unit="Core")])
    tree = FakeTree(files={
        "app/A.php": "<?php\n$x = DB::table('users');\n",
        "app/B.php": "<?php\n$x = Repo::all();\n",
    })
    fs = pattern.check(spec.rules[0], make_ctx(spec, idx, tree=tree))
    assert [(f.file, f.line) for f in fs] == [("app/A.php", 2)]


def test_k8_active_exemption_suppresses_and_expired_escalates():
    active = K8_RULE.replace(
        "exempt_files: []",
        'exempt_files:\n      - { path: "app/A.php", reason: r, expires: 2027-01-01 }')
    expired = active.replace("2027-01-01", "2026-01-01")
    idx = make_index([ff("app/A.php", unit="Core")])
    tree = FakeTree(files={"app/A.php": "DB::table('x')"})

    spec = _spec(active)
    assert pattern.check(spec.rules[0], make_ctx(spec, idx, tree=tree)) == []

    spec = _spec(expired)
    fs = pattern.check(spec.rules[0], make_ctx(spec, idx, tree=tree))
    assert len(fs) == 1 and fs[0].severity == "error"
    assert "expired on 2026-01-01" in fs[0].message


# ---------------------------------------------------------------- K9 structure

def test_k9_forbidden_and_required():
    spec = _spec("""
  - id: k9
    kind: structure
    severity: error
    assert:
      forbidden_dirs: ["app/Helpers"]
      required_files: ["docs/architecture.md"]
      required_dirs: ["app/Modules"]
    message: "Repository structure contract."
    remedy: "Fix the tree."
""")
    idx = make_index([])
    tree = FakeTree(files={"app/Helpers/str.php": "x"})
    fs = structure.check(spec.rules[0], make_ctx(spec, idx, tree=tree))
    msgs = sorted(f.message for f in fs)
    assert any("forbidden directory exists: app/Helpers" in m for m in msgs)
    assert any("required file missing: docs/architecture.md" in m for m in msgs)
    assert any("required directory missing: app/Modules" in m for m in msgs)

    tree = FakeTree(files={"docs/architecture.md": "# a",
                           "app/Modules/keep": "x"})
    assert structure.check(spec.rules[0], make_ctx(spec, idx, tree=tree)) == []


def test_k9_for_each_unit_docs():
    spec = _spec("""
  - id: k9
    kind: structure
    severity: error
    assert:
      for_each_unit: { root: host }
      file: README.md
      must_match: ["^## Public API", "^## Dependencies"]
    message: "Every unit documents its contract."
    remedy: "Add the sections."
""")
    idx = make_index([
        ff("app/Modules/Billing/A.php", unit="Billing",
           unit_dir="app/Modules/Billing"),
        ff("app/Modules/Core/A.php", unit="Core", unit_dir="app/Modules/Core"),
    ])
    tree = FakeTree(files={
        "app/Modules/Billing/README.md": "## Public API\n## Dependencies\n",
        "app/Modules/Core/README.md": "## Public API\n",
    })
    fs = structure.check(spec.rules[0], make_ctx(spec, idx, tree=tree))
    assert len(fs) == 1
    assert fs[0].unit == "Core" and "## Dependencies" in fs[0].message

    # missing file entirely
    tree = FakeTree(files={"app/Modules/Billing/README.md": "## Public API\n## Dependencies\n"})
    fs = structure.check(spec.rules[0], make_ctx(spec, idx, tree=tree))
    assert len(fs) == 1 and "missing README.md for unit Core" in fs[0].message


def test_k9_paired_declaration():
    spec = _spec(K7_RULE + """
  - id: k9
    kind: structure
    severity: error
    assert:
      paired_declaration:
        source_rule: k7
        must_appear_in: docs/architecture.md
    message: "Declared exceptions must be documented."
    remedy: "Document the exception."
""")
    idx = make_index([])
    rule = spec.rule_by_id("k9")

    tree = FakeTree(files={"docs/architecture.md": "App\\Models\\User is legacy"})
    assert structure.check(rule, make_ctx(spec, idx, tree=tree)) == []

    tree = FakeTree(files={"docs/architecture.md": "nothing here"})
    fs = structure.check(rule, make_ctx(spec, idx, tree=tree))
    assert len(fs) == 1 and "not documented: App\\Models\\User" in fs[0].message

    tree = FakeTree()
    fs = structure.check(rule, make_ctx(spec, idx, tree=tree))
    assert len(fs) == 1 and "document missing" in fs[0].message


# ---------------------------------------------------------------- K10 metric

def test_k10_lines_threshold():
    spec = _spec("""
  - id: k10
    kind: metric-threshold
    metric: lines
    max: 100
    scope: { roots: [host] }
    message: "File too long."
    remedy: "Split it."
""")
    idx = make_index([ff("app/big.php", unit="Core", lines=150),
                      ff("app/ok.php", unit="Core", lines=100)])
    fs = metric.check(spec.rules[0], make_ctx(spec, idx))
    assert len(fs) == 1
    assert fs[0].file == "app/big.php"
    assert fs[0].severity == "warning"  # loader default for metric-threshold
    assert "[150 lines > 100]" in fs[0].message


# ---------------------------------------------------------------- K11 graph

def _idx_chain():
    """Billing -> Core -> Reporting (each edge via one witness dep)."""
    return make_index([
        ff("app/Modules/Billing/A.php", unit="Billing",
           declares=["B\\A"], deps=[("C\\A", 3, "use")]),
        ff("app/Modules/Core/A.php", unit="Core",
           declares=["C\\A"], deps=[("R\\A", 7, "use")]),
        ff("app/Modules/Reporting/A.php", unit="Reporting", declares=["R\\A"]),
    ])


def test_k11_independence_transitive_hit():
    spec = _spec("""
  - id: k11
    kind: reachability
    severity: error
    mode: independence
    units: [Billing, Reporting]
    scope: { roots: [host] }
    message: "Billing and Reporting must stay independent."
    remedy: "Cut the path."
""")
    fs = reachability.check(spec.rules[0], make_ctx(spec, _idx_chain()))
    assert len(fs) == 1
    f = fs[0]
    assert f.unit == "Billing" and f.target == "Reporting"
    assert f.path_trace == "Billing -> Core -> Reporting"
    assert f.file == "app/Modules/Billing/A.php" and f.line == 3


def test_k11_independence_pass_without_path():
    spec = _spec("""
  - id: k11
    kind: reachability
    severity: error
    mode: independence
    units: [Billing, Reporting]
    scope: { roots: [host] }
    message: "m"
    remedy: "r"
""")
    idx = make_index([
        ff("app/Modules/Billing/A.php", unit="Billing", declares=["B\\A"]),
        ff("app/Modules/Reporting/A.php", unit="Reporting", declares=["R\\A"]),
    ])
    assert reachability.check(spec.rules[0], make_ctx(spec, idx)) == []


def test_k11_layers_upward_reach_fails():
    spec = _spec("""
  - id: k11
    kind: reachability
    severity: error
    mode: layers
    order: [Reporting, Core, Billing]
    scope: { roots: [host] }
    message: "Lower layers must not reach higher ones."
    remedy: "r"
""")
    # chain gives Billing -> Core -> Reporting; order top->bottom is
    # Reporting > Core > Billing, so Billing reaching Reporting (and Core)
    # and Core reaching Reporting are all violations.
    fs = reachability.check(spec.rules[0], make_ctx(spec, _idx_chain()))
    got = sorted((f.unit, f.target) for f in fs)
    assert got == [("Billing", "Core"), ("Billing", "Reporting"),
                   ("Core", "Reporting")]


def test_k11_forbidden_path_and_max_depth():
    body = """
  - id: k11
    kind: reachability
    severity: error
    mode: forbidden-path
    forbid: { from: Billing, to: Reporting }
    scope: { roots: [host] }
    message: "No path Billing -> Reporting."
    remedy: "r"
"""
    spec = _spec(body)
    fs = reachability.check(spec.rules[0], make_ctx(spec, _idx_chain()))
    assert len(fs) == 1 and "[path: Billing -> Core -> Reporting]" in fs[0].message

    spec = _spec(body.replace("scope:", "max_depth: 1\n    scope:"))
    assert reachability.check(spec.rules[0], make_ctx(spec, _idx_chain())) == []


def test_k11_ignore_edges_active_and_expired():
    body = """
  - id: k11
    kind: reachability
    severity: error
    mode: independence
    units: [Billing, Reporting]
    ignore_edges:
      - { from: Billing, to: Core, reason: migration, expires: %s }
    scope: { roots: [host] }
    message: "m"
    remedy: "r"
"""
    spec = _spec(body % "2027-01-01")
    assert reachability.check(spec.rules[0], make_ctx(spec, _idx_chain())) == []

    spec = _spec(body % "2026-01-01")
    fs = reachability.check(spec.rules[0], make_ctx(spec, _idx_chain()))
    # expired ignore -> explicit error on the edge, plus the path finding
    kinds = sorted(f.message for f in fs)
    assert any("expired on 2026-01-01" in m for m in kinds)
    assert any("[path: Billing -> Core -> Reporting]" in m for m in kinds)
    assert all(f.severity == "error" for f in fs)


def test_k11_report_path_off():
    spec = _spec("""
  - id: k11
    kind: reachability
    severity: error
    mode: independence
    units: [Billing, Reporting]
    report_path: false
    scope: { roots: [host] }
    message: "m"
    remedy: "r"
""")
    fs = reachability.check(spec.rules[0], make_ctx(spec, _idx_chain()))
    assert len(fs) == 1 and fs[0].path_trace is None and fs[0].message == "m"
