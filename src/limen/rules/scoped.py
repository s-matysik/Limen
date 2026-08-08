"""K5 scoped-allowlist: within a path scope, targets under a prefix must come
from an explicit allow list."""
from __future__ import annotations

from ..spec.model import Rule
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    prefix = rule.params.get("target_prefix", "")
    scope = Scope(rule.scope)
    allow = ExemptionSet.parse(rule.params.get("allow"),
                               f"{rule.spec_path}.allow", ctx.today)
    out: list[Finding] = []
    for f in ctx.index.files:
        if not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            if not dep.target.startswith(prefix):
                continue
            t = ctx.index.resolve_target(dep.target)
            if not scope.dep_matches(f, t):
                continue
            status, ex = allow.check(target=dep.target, path=f.path)
            if status == "active":
                continue
            if status == "expired":
                out.append(make_finding(rule, file=f.path, line=dep.line,
                                        unit=f.unit, target=dep.target,
                                        severity="error",
                                        message=expired_message(rule, ex),
                                        exemptible=True, spec_path=ex.spec_path))
                continue
            out.append(make_finding(rule, file=f.path, line=dep.line,
                                    unit=f.unit, target=dep.target,
                                    exemptible=True,
                                    spec_path=f"{rule.spec_path}.allow"))
    return out
