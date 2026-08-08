"""SARIF 2.1.0 report for GitHub code scanning: findings become PR diff
annotations. Deterministic: sorted keys, no timestamps."""
from __future__ import annotations

import json as _json

from .. import __version__
from ..engine import Report

_LEVEL = {"error": "error", "warning": "warning", "info": "note"}


def render(report: Report, strict: bool = False) -> str:
    rule_ids = sorted({f.rule_id for f in report.findings})
    results = []
    for f in report.findings:
        result = {
            "ruleId": f.rule_id,
            "level": _LEVEL.get(f.severity, "warning"),
            "message": {"text": f.message + (f" Remedy: {f.remedy}" if f.remedy else "")},
        }
        if f.file:
            location = {"physicalLocation": {
                "artifactLocation": {"uri": f.file, "uriBaseId": "SRCROOT"}}}
            if f.line:
                location["physicalLocation"]["region"] = {"startLine": f.line}
            result["locations"] = [location]
        results.append(result)
    doc = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "limen",
                "informationUri": "https://github.com/s-matysik/limen",
                "version": __version__,
                "rules": [{"id": rid} for rid in rule_ids],
            }},
            "results": results,
        }],
    }
    return _json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
