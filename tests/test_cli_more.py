"""Second CLI batch: --compare, spec stats medians, baseline capture/diff/
prune, context --for-diff, graph on a broken spec, init detection variants —
plus a rich `limen context` spec exercising every rule kind's expansion."""
import shutil
import subprocess
from pathlib import Path

import pytest

from limen.cli import main
from limen.context import generate_context

from conftest import TODAY as FROZEN, ff, make_index, requires_git, spec_from

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "minimal-python"
TODAY = ["--today", "2026-08-04"]


def run(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture()
def work(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(EXAMPLE, dst)
    return dst


def ws(work):
    return ["--workspace", str(work), *TODAY]


# --------------------------------------------------------- extract --compare

def test_extract_compare_regex_regex(work, capsys):
    code, out, _ = run(["extract", "--compare", "regex,regex", *ws(work)],
                       capsys)
    assert code == 0
    assert "deps found by both engines" in out
    assert "only by regex: 0" in out


def test_extract_compare_treesitter_unavailable(work, capsys):
    code, _, err = run(["extract", "--compare", "regex,treesitter", *ws(work)],
                       capsys)
    assert code == 4
    assert "treesitter" in err or "tree-sitter" in err


# ------------------------------------------------------------- spec stats

def test_spec_stats_medians(tmp_path, capsys):
    (tmp_path / "src" / "a").mkdir(parents=True)
    (tmp_path / "src" / "a" / "m.py").write_text("import b.n\n")
    (tmp_path / "src" / "b").mkdir()
    (tmp_path / "src" / "b" / "n.py").write_text("x = 1\n")
    spec = tmp_path / "architecture.yaml"
    spec.write_text("""
version: 1
roots:
  main: { path: src, language: python }
units:
  discovery: [{ root: main, pattern: "src/{unit}/**" }]
  declared: [a, b]
rules:
  - id: bl
    kind: baseline-snapshot
    severity: error
    target_pattern: "b.*"
    expected:
      "src/a/m.py":
        - { target: "b.n", reason: legacy, since: 2026-01-01,
            expires: 2026-06-01 }
    message: m
    remedy: r
""")
    code, out, _ = run(["spec", "--spec", str(spec), "--workspace",
                        str(tmp_path), *TODAY, "stats"], capsys)
    assert code == 0
    assert "rules per kind" in out
    assert "1 total, 0 active, 1 expired" in out
    assert "median age: 215 days" in out
    assert "median declared lifetime" in out and "151 days" in out


# ------------------------------------------------------------- baseline ops

def _inject_core_domain_import(work):
    victim = work / "src" / "reporting" / "report.py"
    victim.write_text(victim.read_text()
                      + "\nfrom core.domain.model import Order\n")


def test_baseline_capture_prints_actual(work, capsys):
    _inject_core_domain_import(work)
    code, out, _ = run(["baseline", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY,
                        "capture", "baseline-core-domain"], capsys)
    assert code == 0
    assert "src/reporting/report.py" in out and "core.domain.model" in out


def test_baseline_diff_directions_and_prune(work, capsys):
    _inject_core_domain_import(work)
    # + direction: in code, not declared
    code, out, _ = run(["baseline", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY,
                        "diff", "baseline-core-domain"], capsys)
    assert code == 0 and "+ src/reporting/report.py" in out

    # declare a ghost entry -> "-" direction and prune reports it
    spec_path = work / "architecture.yaml"
    spec_path.write_text(spec_path.read_text().replace(
        "expected: {}",
        'expected:\n      "src/billing/service.py":\n'
        '        - { target: "core.domain.model", reason: ghost }'))
    code, out, _ = run(["baseline", "--spec", str(spec_path),
                        "--workspace", str(work), *TODAY,
                        "diff", "baseline-core-domain"], capsys)
    assert code == 0 and "- src/billing/service.py" in out

    code, out, _ = run(["baseline", "--spec", str(spec_path),
                        "--workspace", str(work), *TODAY, "prune"], capsys)
    assert code == 0 and "dead entry" in out


def test_baseline_wrong_rule_id_exits_3(work, capsys):
    code, _, err = run(["baseline", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY,
                        "capture", "domain-not-infra"], capsys)
    assert code == 3
    assert "baseline-snapshot" in err


# --------------------------------------------------------- context --for-diff

def _git(work, *args):
    subprocess.run(["git", *args], cwd=work, check=True,
                   capture_output=True, text=True)


@requires_git
def test_context_for_diff(work, capsys):
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    (work / "src" / "billing" / "service.py").write_text(
        (work / "src" / "billing" / "service.py").read_text() + "\n# touch\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "touch")
    code, out, _ = run(["context", "--for-diff", "HEAD~1..HEAD", *ws(work)],
                       capsys)
    assert code == 0
    assert "src/billing/service.py" in out
    assert "limen check --changed-only HEAD~1..HEAD" in out


def test_context_without_paths_exits_3(work, capsys):
    code, _, err = run(["context", *ws(work)], capsys)
    assert code == 3
    assert "--paths" in err


# --------------------------------------------------------------- graph error

def test_graph_ambiguous_units_exits_3(tmp_path, capsys):
    (tmp_path / "src" / "Modules" / "Billing").mkdir(parents=True)
    (tmp_path / "src" / "Modules" / "Billing" / "a.py").write_text("x=1\n")
    spec = tmp_path / "architecture.yaml"
    spec.write_text("""
version: 1
roots:
  main: { path: src, language: python }
units:
  discovery:
    - { root: main, pattern: "src/Modules/{unit}/**" }
    - { root: main, pattern: "src/{unit}/Billing/**" }
  declared: [Billing, Modules]
rules: []
""")
    code, _, err = run(["graph", "--spec", str(spec),
                        "--workspace", str(tmp_path), *TODAY], capsys)
    assert code == 3
    assert "unit-ambiguous" in err


# ------------------------------------------------------------ init variants

def test_init_composer_and_package(tmp_path, capsys):
    (tmp_path / "composer.json").write_text("{}")
    (tmp_path / "app" / "Modules" / "Auth").mkdir(parents=True)
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "src" / "components").mkdir(parents=True)
    code, out, err = run(["init", "--workspace", str(tmp_path)], capsys)
    assert code == 0
    text = (tmp_path / "architecture.yaml").read_text()
    assert "language: php" in text and "language: js" in text
    assert "app/Modules/{unit}/**" in text
    assert "Auth" in text and "components" in text


def test_init_empty_dir_defaults(tmp_path, capsys):
    code, out, err = run(["init", "--workspace", str(tmp_path)], capsys)
    assert code == 0
    assert "language: python" in (tmp_path / "architecture.yaml").read_text()


# -------------------------------------------- rich context: every rule kind

RICH_SPEC = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Auth, Billing, Audit, Reporting]
layers:
  declared: [Application, Infrastructure]
  discovery:
    - { pattern: "app/Modules/*/{layer}/**" }
artifact_sets:
  models:
    discovery: { root: host, pattern: "app/Models/*.php" }
public_surface:
  php:
    - "{unit}/Contracts/**"
rules:
  - id: layers
    kind: layer-dependency
    severity: error
    forbid: { from_layer: Application, to_layer: Infrastructure }
    scope: { roots: [host] }
    message: "App must not touch Infra."
    remedy: "Invert it."
  - id: owners
    kind: ownership
    severity: error
    artifact_set: models
    owners: { "App\\\\Models\\\\User": Auth }
    message: "One owner per model."
    remedy: "Declare the owner."
  - id: consumers
    kind: consumer-allowlist
    severity: error
    artifact_set: models
    allow: { "App\\\\Models\\\\User": [Billing] }
    message: "Only owner and declared consumers."
    remedy: "Go through Auth's contract."
  - id: scoped
    kind: scoped-allowlist
    severity: error
    scope: { paths: ["app/Modules/Billing/**"] }
    target_prefix: "App\\\\Modules\\\\Audit\\\\"
    allow: ["App\\\\Modules\\\\Audit\\\\Contracts\\\\AuditApi"]
    message: "Only AuditApi."
    remedy: "Use AuditApi."
  - id: structure
    kind: structure
    severity: error
    assert:
      forbidden_dirs: ["app/Helpers"]
      required_files: ["docs/architecture.md"]
      for_each_unit: { root: host }
      file: README.md
      must_match: ["^## Public API"]
      paired_declaration:
        source_rule: snapshot
        must_appear_in: docs/architecture.md
    message: "Structure contract."
    remedy: "Fix the tree."
  - id: snapshot
    kind: baseline-snapshot
    severity: error
    target_pattern: "App\\\\Models\\\\*"
    expected: {}
    message: "Snapshot must match."
    remedy: "Update expected:."
  - id: chain
    kind: reachability
    severity: error
    mode: layers
    order: [Reporting, Billing]
    message: "No upward reach."
    remedy: "Cut it."
  - id: nopath
    kind: reachability
    severity: error
    mode: forbidden-path
    forbid: { from: Billing, to: Audit }
    message: "Billing must not reach Audit."
    remedy: "Cut it."
"""


def _rich_index():
    return make_index([
        ff("app/Modules/Billing/Application/Invoice.php", unit="Billing",
           unit_dir="app/Modules/Billing", layer="Application",
           declares=["App\\Modules\\Billing\\Application\\Invoice"]),
        ff("app/Modules/Auth/Contracts/AuthApi.php", unit="Auth",
           unit_dir="app/Modules/Auth",
           declares=["App\\Modules\\Auth\\Contracts\\AuthApi"]),
        ff("app/Models/User.php", unit="Auth",
           declares=["App\\Models\\User"]),
        ff("app/Modules/Audit/Contracts/AuditApi.php", unit="Audit",
           unit_dir="app/Modules/Audit",
           declares=["App\\Modules\\Audit\\Contracts\\AuditApi"]),
    ])


def test_rich_context_expands_every_kind():
    spec = spec_from(RICH_SPEC)
    text = generate_context(
        spec, _rich_index(),
        ["app/Modules/Billing/Application/Invoice.php",
         "app/Modules/Billing/Application/NewFile.php"],  # unknown path
        today=FROZEN)
    assert "layer `Application` must not depend on layer `Infrastructure`" in text
    assert "`App\\Models\\User` may be imported by: Auth, Billing" in text
    assert "`Billing` is allowed" in text
    assert "toward `App\\Modules\\Audit\\" in text
    assert "AuditApi" in text
    assert "Forbidden directories: `app/Helpers`" in text
    assert "Required files: `docs/architecture.md`" in text
    assert "README.md" in text and "^## Public API" in text
    assert "declared in rule `snapshot` must also appear in" in text
    assert "must equal the declared baseline exactly" in text
    assert "Layer order (top → bottom): `Reporting` → `Billing`" in text
    assert "`Billing` must not reach `Audit`" in text
    assert "### Verify before you report done" in text


def test_rich_context_ownership_line_for_touched_model():
    spec = spec_from(RICH_SPEC)
    text = generate_context(spec, _rich_index(), ["app/Models/User.php"],
                            today=FROZEN)
    assert "`App\\Models\\User` is owned by unit `Auth`" in text
