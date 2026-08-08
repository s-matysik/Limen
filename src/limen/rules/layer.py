"""K1 layer-dependency: a forbidden direction between layers."""
from __future__ import annotations

from ..spec.model import Rule
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    forbid = rule.params.get("forbid") or {}
    from_layer, to_layer = forbid.get("from_layer"), forbid.get("to_layer")
    scope = Scope(rule.scope)
    exempt = ExemptionSet.parse(rule.params.get("exempt"),
                                f"{rule.spec_path}.exempt", ctx.today)
    out: list[Finding] = []
    for f in ctx.index.files:
        if f.layer != from_layer or not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            t = ctx.index.resolve_target(dep.target)
            if t is None or t is f or t.layer != to_layer:
                continue
            if not scope.dep_matches(f, t):
                continue
            status, ex = exempt.check(target=dep.target, path=f.path)
            if status == "active":
                continue
            if status == "expired":
                out.append(make_finding(rule, file=f.path, line=dep.line,
                                        unit=f.unit, target=dep.target,
                                        severity="error",
                                        message=expired_message(rule, ex),
                                        exemptible=True,
                                        spec_path=ex.spec_path))
                continue
            out.append(make_finding(rule, file=f.path, line=dep.line,
                                    unit=f.unit, target=dep.target,
                                    exemptible=True,
                                    spec_path=f"{rule.spec_path}.exempt"))
    return out
