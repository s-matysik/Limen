"""CLI integration: every subcommand and format exercised end-to-end on the
minimal example (real extraction, real filesystem, frozen clock)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from limen.cli import main

from conftest import requires_git

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "minimal-python"
TODAY = ["--today", "2026-08-04"]


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def work(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(EXAMPLE, dst)
    return dst


def ws(work):
    return ["--workspace", str(work), *TODAY]


# ------------------------------------------------------------------ formats

def test_check_human_format(work, capsys):
    code, out, _ = run(["check", *ws(work)], capsys)
    assert code == 0
    assert "0 error" in out or "OK" in out or "passed" in out.lower()


def test_check_junit_and_sarif(work, capsys):
    for fmt, marker in (("junit", "<testsuite"), ("sarif", '"version": "2.1.0"')):
        code, out, _ = run(["check", "--format", fmt, *ws(work)], capsys)
        assert code == 0
        assert marker in out


def test_check_strict_and_only(work, capsys):
    code, _, _ = run(["check", "--strict", "--only", "domain-not-infra",
                      *ws(work)], capsys)
    assert code == 0


def test_check_failure_human_shows_remedy(work, capsys):
    victim = work / "src" / "billing" / "service.py"
    victim.write_text(victim.read_text() + "\nimport core.infra.db\n")
    code, out, _ = run(["check", *ws(work)], capsys)
    assert code == 1
    assert "billing-no-direct-db" in out
    assert "core.api.get_connection" in out  # remedy printed


# -------------------------------------------------------------------- spec

def test_spec_explain_and_stats(work, capsys):
    code, out, _ = run(["spec", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY,
                        "explain", "domain-not-infra"], capsys)
    assert code == 0 and "layer-dependency" in out

    code, out, _ = run(["spec", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY, "stats"], capsys)
    assert code == 0 and "rules" in out.lower()


def test_spec_explain_unknown_rule(work, capsys):
    code, out, err = run(["spec", "--spec", str(work / "architecture.yaml"),
                          "--workspace", str(work), *TODAY,
                          "explain", "no-such-rule"], capsys)
    assert code != 0


# ------------------------------------------------------------------ extract

def test_extract_out_and_reuse_as_facts(work, tmp_path, capsys):
    out_file = tmp_path / "facts.json"
    code, _, _ = run(["extract", "--out", str(out_file), *ws(work)], capsys)
    assert code == 0
    data = json.loads(out_file.read_text())
    docs = data if isinstance(data, list) else [data]
    assert all(d["schema"] == "arch-facts/1" for d in docs)
    assert sum(len(d["files"]) for d in docs) == 10

    code, out, _ = run(["check", "--facts", str(out_file), "--format", "json",
                        *ws(work)], capsys)
    assert code == 0
    assert json.loads(out)["summary"]["errors"] == 0


def test_extract_single_root_stdout(work, capsys):
    code, out, _ = run(["extract", "--root", "main", *ws(work)], capsys)
    assert code == 0
    assert '"arch-facts/1"' in out


# -------------------------------------------------------------------- graph

def test_graph_formats(work, capsys):
    for fmt, marker in (("dot", "digraph"), ("mermaid", "graph"),
                        ("json", '"edges"')):
        code, out, _ = run(["graph", "--format", fmt, *ws(work)], capsys)
        assert code == 0, fmt
        assert marker in out


# ------------------------------------------------------------------ context

def test_context_cli_md_and_json(work, capsys):
    paths = ["src/billing/service.py"]
    code, out, _ = run(["context", "--paths", *paths, *ws(work)], capsys)
    assert code == 0
    assert "### Hard rules" in out
    assert "billing-no-direct-db" in out

    code, out, _ = run(["context", "--paths", *paths, "--format", "json",
                        "--budget-tokens", "5000", *ws(work)], capsys)
    assert code == 0
    doc = json.loads(out)
    assert "context" in doc and "token_estimate" in doc


# ------------------------------------------------------- review (git repo)

def _git(work, *args):
    subprocess.run(["git", *args], cwd=work, check=True,
                   capture_output=True, text=True)


@requires_git
def test_review_for_diff(work, capsys):
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    victim = work / "src" / "billing" / "service.py"
    victim.write_text(victim.read_text() + "\nimport core.infra.db\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "violation")

    code, out, _ = run(["review", "--for-diff", "HEAD~1..HEAD", *ws(work)],
                       capsys)
    assert code in (0, 1)
    assert "src/billing/service.py" in out
    assert "report, not a verdict" in out


@requires_git
def test_check_changed_only(work, capsys):
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "base")
    (work / "src" / "reporting" / "report.py").write_text(
        (work / "src" / "reporting" / "report.py").read_text()
        + "\n# touched\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "touch")
    code, out, _ = run(["check", "--changed-only", "HEAD~1..HEAD",
                        "--format", "json", *ws(work)], capsys)
    assert code == 0
    assert json.loads(out)["summary"]["errors"] == 0


# ----------------------------------------------------------------- baseline

def test_baseline_capture_diff_prune(work, capsys):
    code, out, _ = run(["baseline", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY,
                        "capture", "baseline-core-domain"], capsys)
    assert code == 0

    code, out, _ = run(["baseline", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY,
                        "diff", "baseline-core-domain"], capsys)
    assert code == 0

    code, out, _ = run(["baseline", "--spec", str(work / "architecture.yaml"),
                        "--workspace", str(work), *TODAY, "prune"], capsys)
    assert code == 0


def test_baseline_update_sidecar(work, capsys):
    # introduce a cross-unit core.domain import -> baseline rule fires,
    # --baseline-update writes the sidecar with the missing entries
    victim = work / "src" / "reporting" / "report.py"
    victim.write_text(victim.read_text()
                      + "\nfrom core.domain.model import Order\n")
    code, out, _ = run(["check", "--baseline-update", "--format", "json",
                        *ws(work)], capsys)
    assert code == 1
    sidecar = work / "architecture.yaml.baseline-update.yaml"
    assert sidecar.exists()
    assert "core.domain" in sidecar.read_text()


# --------------------------------------------------------------------- init

def test_init_detect_python(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    code, out, err = run(["init", "--workspace", str(tmp_path)], capsys)
    assert code == 0
    written = tmp_path / "architecture.yaml"
    assert written.exists() and "wrote" in err
    text = written.read_text()
    assert "language: python" in text and "pkg" in text
    # second run must not overwrite: skeleton goes to stdout instead
    code, out, err = run(["init", "--workspace", str(tmp_path)], capsys)
    assert code == 0 and "already exists" in err and "roots:" in out


# ------------------------------------------------------------------- errors

def test_missing_spec_exits_3(tmp_path, capsys):
    code, _, err = run(["check", "--spec", str(tmp_path / "nope.yaml"),
                        "--workspace", str(tmp_path), *TODAY], capsys)
    assert code == 3


def test_missing_root_dir_exits_4(tmp_path, capsys):
    spec = tmp_path / "architecture.yaml"
    spec.write_text(
        "version: 1\nroots:\n  main: { path: src, language: python }\n"
        "rules: []\n")
    code, _, err = run(["check", "--spec", str(spec),
                        "--workspace", str(tmp_path), *TODAY], capsys)
    assert code == 4
