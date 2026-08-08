"""K6 forbidden-target: a concrete target banned within a path scope,
always with a remedy pointing at the sanctioned alternative."""
from __future__ import annotations

from ..spec.model import Rule
from ..util import fqcn_match
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    forbid = [str(t) for t in rule.params.get("forbid") or []]
    scope = Scope(rule.scope)
    exempt = ExemptionSet.parse(rule.params.get("exempt"),
                                f"{rule.spec_path}.exempt", ctx.today)
    out: list[Finding] = []
    for f in ctx.index.files:
        if not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            hit = any(dep.target == t or ("*" in t and fqcn_match(t, dep.target))
                      for t in forbid)
            if not hit:
                continue
            t = ctx.index.resolve_target(dep.target)
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
