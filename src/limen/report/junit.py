"""JUnit XML report: one testcase per rule with findings folded into failures.
No timestamps — byte-identical across runs."""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from ..engine import Report


def render(report: Report, strict: bool = False) -> str:
    by_rule: dict[str, list] = {}
    for f in report.findings:
        by_rule.setdefault(f.rule_id, []).append(f)
    failures = sum(1 for fs in by_rule.values()
                   if any(f.severity == "error" for f in fs))
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(f'<testsuite name="limen" tests="{max(len(by_rule), 1)}" '
               f'failures="{failures}">')
    if not by_rule:
        out.append('  <testcase name="architecture-contract"/>')
    for rule_id in sorted(by_rule):
        fs = by_rule[rule_id]
        errors = [f for f in fs if f.severity == "error"]
        out.append(f'  <testcase name={quoteattr(rule_id)}>')
        if errors:
            body = "\n".join(
                f"{f.file or '<spec>'}:{f.line or 0} {f.message}"
                + (f" | remedy: {f.remedy}" if f.remedy else "")
                for f in errors)
            out.append(f'    <failure message={quoteattr(errors[0].message)}>'
                       f'{escape(body)}</failure>')
        else:
            body = "\n".join(f"{f.file or '<spec>'}:{f.line or 0} {f.message}"
                             for f in fs)
            out.append(f'    <system-out>{escape(body)}</system-out>')
        out.append('  </testcase>')
    out.append('</testsuite>')
    return "\n".join(out) + "\n"
