"""Diff-scoped code paths (`--changed-only`, `--for-diff`) without a real repo.

The three integration tests that build a throw-away repository with `git init`
cannot run in environments that write-protect `.git` (agent sandboxes) or ship
no git at all. What limen actually needs from git is one command —
``git diff --name-only <range>`` (limen/review.py::changed_files) — so these
tests put a stub `git` on PATH that answers exactly that, and then exercise the
same CLI plumbing end-to-end. They run everywhere and keep the diff paths
covered when the git-backed tests skip.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from limen.cli import main
from limen.review import changed_files

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "minimal-python"
TODAY = ["--today", "2026-08-04"]

#: `BROKEN` stands in for a range real git cannot resolve (unknown revision),
#: which is how changed_files() must surface a diff failure.
STUB = """#!/usr/bin/env python3
import sys
if sys.argv[1:3] == ["diff", "--name-only"] and not any(
        "BROKEN" in a for a in sys.argv[3:]):
    sys.stdout.write(open(%r).read())
    sys.exit(0)
sys.stderr.write("fatal: bad revision\\n")
sys.exit(128)
"""


@pytest.fixture()
def work(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(EXAMPLE, dst)
    return dst


@pytest.fixture()
def stub_git(tmp_path, monkeypatch):
    """Put a fake `git` on PATH whose diff output the test controls."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    listing = tmp_path / "changed.txt"
    listing.write_text("")
    exe = bindir / "git"
    exe.write_text(STUB % str(listing))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def set_changed(*paths):
        listing.write_text("".join(f"{p}\n" for p in paths))
    return set_changed


def run(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def ws(work):
    return ["--workspace", str(work), *TODAY]


def test_changed_files_parses_git_output(stub_git, tmp_path):
    stub_git("src/billing/service.py", "src/reporting/report.py",
             "src/billing/service.py")   # duplicates collapse
    assert changed_files("HEAD~1..HEAD", str(tmp_path)) == [
        "src/billing/service.py", "src/reporting/report.py"]


def test_changed_files_reports_git_failure(stub_git, tmp_path):
    with pytest.raises(RuntimeError, match="cannot compute diff"):
        changed_files("BROKEN..HEAD", str(tmp_path))  # stub exits 128


def test_check_changed_only(work, stub_git, capsys):
    stub_git("src/reporting/report.py")
    code, out, _ = run(["check", "--changed-only", "HEAD~1..HEAD",
                        "--format", "json", *ws(work)], capsys)
    assert code == 0
    assert json.loads(out)["summary"]["errors"] == 0


def test_check_changed_only_scopes_findings(work, stub_git, capsys):
    victim = work / "src" / "billing" / "service.py"
    victim.write_text(victim.read_text() + "\nimport core.infra.db\n")

    # a diff that does not touch the violating file stays green ...
    stub_git("src/reporting/report.py")
    code, out, _ = run(["check", "--changed-only", "HEAD~1..HEAD",
                        "--format", "json", *ws(work)], capsys)
    assert code == 0, out

    # ... and the same tree reports the violation once the file is in the diff
    stub_git("src/billing/service.py")
    code, out, _ = run(["check", "--changed-only", "HEAD~1..HEAD",
                        "--format", "json", *ws(work)], capsys)
    assert code == 1
    report = json.loads(out)
    assert report["summary"]["errors"] >= 1
    assert any(f["rule_id"] == "billing-no-direct-db" for f in report["findings"])


def test_review_for_diff(work, stub_git, capsys):
    stub_git("src/billing/service.py")
    code, out, _ = run(["review", "--for-diff", "HEAD~1..HEAD", *ws(work)],
                       capsys)
    assert code in (0, 1)
    assert "src/billing/service.py" in out
    assert "report, not a verdict" in out


def test_context_for_diff(work, stub_git, capsys):
    stub_git("src/billing/service.py")
    code, out, _ = run(["context", "--for-diff", "HEAD~1..HEAD", *ws(work)],
                       capsys)
    assert code == 0
    assert "src/billing/service.py" in out
    assert "limen check --changed-only HEAD~1..HEAD" in out


def test_stub_is_the_only_git_used(stub_git):
    """Guard: the stub really shadows any system git for these tests."""
    assert Path(shutil.which("git")).name == "git"
    proc = subprocess.run(["git", "status"], capture_output=True)
    assert proc.returncode != 0  # our stub answers only `diff --name-only`
    assert b"bad revision" in proc.stderr
