"""LitRev retrofit spec (full legacy retrofit, Faza 0): structural validation
always; the full check against the workspace snapshot runs when
LIMEN_LITREV_WS points at a directory containing litrev-arch-snapshot/
(e.g. LIMEN_LITREV_WS=/tmp/litrev-smoke).

The snapshot check does NOT assert a clean tree. The retrofit reproduces the
legacy rules faithfully, and three host/core controllers genuinely import
concrete backend-shared Application services instead of a Contracts\\
interface — a boundary the legacy checker could not see because it matched
only the App\\Modules\\ namespace (docs/deviations.md D-08). Those findings are
pinned here so that the retrofit's known-violation set cannot drift silently
in either direction: a new violation fails, and so does one that quietly
disappears."""
import json
import os
from pathlib import Path

import pytest

from limen.cli import main

SPEC = (Path(__file__).resolve().parents[1]
        / "examples" / "litrev" / "architecture.yaml")
WS = os.environ.get("LIMEN_LITREV_WS")

# Known violations on the frozen snapshot: host/core controllers importing a
# concrete backend-shared Application service rather than a Contracts\ one.
# See docs/deviations.md D-08.
EXPECTED_ERRORS = {
    ("host-cross-module-public-surface",
     "LitRev\\BackendShared\\Audit\\Application\\AuditLogService"),
    ("host-cross-module-public-surface",
     "LitRev\\BackendShared\\Folders\\Application\\FolderViewService"),
    ("host-cross-module-public-surface",
     "LitRev\\BackendShared\\Folders\\Application\\FolderHomeService"),
}


def run(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_litrev_spec_validates_structurally(capsys):
    code, out, err = run(["spec", "--spec", str(SPEC), "--today", "2026-08-04",
                          "validate", "--no-facts"], capsys)
    assert code == 0, out + err


@pytest.mark.skipif(
    not (WS and (Path(WS) / "litrev-arch-snapshot").is_dir()),
    reason="set LIMEN_LITREV_WS to the workspace containing litrev-arch-snapshot/")
def test_litrev_check_on_snapshot(capsys):
    code, out, _ = run(["check", "--spec", str(SPEC), "--workspace", WS,
                        "--format", "json", "--today", "2026-08-04"], capsys)
    report = json.loads(out)

    errors = [f for f in report["findings"] if f["severity"] == "error"]
    assert {(f["rule_id"], f["target"]) for f in errors} == EXPECTED_ERRORS, out

    # every warning is an honestly-reported non-source asset import
    warnings = [f for f in report["findings"] if f["severity"] != "error"]
    assert all(f["rule_id"] == "unresolved-dependency" for f in warnings), out

    assert code == 1, out  # errors present by design -> exit 1
