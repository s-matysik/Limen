"""K10 metric-threshold: a numeric threshold, warning by default."""
from __future__ import annotations

from ..spec.model import Rule
from .base import Finding, RuleContext, Scope, make_finding


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    metric = rule.params.get("metric", "lines")
    maximum = rule.params.get("max")
    scope = Scope(rule.scope)
    out: list[Finding] = []
    if metric != "lines" or maximum is None:
        return out
    for f in ctx.index.files:
        if not scope.file_matches(f):
            continue
        if f.lines > int(maximum):
            out.append(make_finding(
                rule, file=f.path, unit=f.unit,
                message=f"{rule.message} [{f.lines} lines > {maximum}]"))
    return out
