"""JSON report: limen-report/1 with finding-1 objects. Deterministic dumps."""
from __future__ import annotations

import json as _json

from ..engine import Report


def render(report: Report, strict: bool = False) -> str:
    doc = {
        "schema": "limen-report/1",
        "project": report.project,
        "summary": report.summary(strict),
        "findings": [f.to_json() for f in report.findings],
    }
    return _json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
