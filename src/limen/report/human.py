"""Human-readable report. Byte-stable: sorted findings, no timestamps."""
from __future__ import annotations

from ..engine import Report


def render(report: Report, strict: bool = False) -> str:
    lines: list[str] = []
    title = "limen check"
    if report.project:
        title += f" — {report.project}"
    lines.append(title)
    lines.append("=" * len(title))
    if not report.findings:
        lines.append("No findings.")
    for f in report.findings:
        loc = f.file or "<spec>"
        if f.line:
            loc += f":{f.line}"
        lines.append(f"{f.severity.upper():7s} [{f.rule_id}] {loc}")
        if f.unit:
            lines.append(f"        unit:   {f.unit}")
        if f.target:
            lines.append(f"        target: {f.target}")
        lines.append(f"        {f.message}")
        if f.path_trace:
            lines.append(f"        path:   {f.path_trace}")
        if f.remedy:
            lines.append(f"        remedy: {f.remedy}")
        if f.spec_path:
            lines.append(f"        spec:   {f.spec_path}")
    s = report.summary(strict)
    lines.append("")
    lines.append(f"Summary: {s['errors']} error(s), {s['warnings']} warning(s), "
                 f"unresolved: {s['unresolved_count']}. Exit code {s['exit_code']}.")
    return "\n".join(lines) + "\n"
