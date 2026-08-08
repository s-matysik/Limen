"""End-to-end on examples/minimal-python: clean tree passes, an injected
violation is caught with a concrete finding."""
import json
import shutil
from pathlib import Path

from limen.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "minimal-python"


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr().out
    return code, out


def test_clean_example_exits_zero(capsys):
    code, out = run(["check", "--workspace", str(EXAMPLE),
                     "--format", "json", "--today", "2026-08-04"], capsys)
    assert code == 0, out
    data = json.loads(out)
    assert data["summary"]["errors"] == 0


def test_injected_violation_exits_one(tmp_path, capsys):
    work = tmp_path / "proj"
    shutil.copytree(EXAMPLE, work)
    victim = work / "src" / "billing" / "service.py"
    victim.write_text(victim.read_text() +
                      "\nimport core.infra.db  # injected violation\n")
    code, out = run(["check", "--workspace", str(work),
                     "--format", "json", "--today", "2026-08-04"], capsys)
    assert code == 1
    data = json.loads(out)
    hits = [f for f in data["findings"]
            if f["file"].endswith("billing/service.py")]
    assert hits, out
    assert any(f["severity"] == "error" for f in hits)


def test_spec_validate_on_example(capsys):
    code, _ = run(["spec", "validate",
                   "--spec", str(EXAMPLE / "architecture.yaml"),
                   "--workspace", str(EXAMPLE)], capsys)
    assert code == 0
