"""K4 public-api-only: cross-unit dependencies must resolve to the target
unit's public surface (per-language patterns, {unit} = the unit directory)."""
from __future__ import annotations

from ..spec.model import Rule
from ..util import glob_to_regex
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def is_public(ctx: RuleContext, target_file) -> bool:
    patterns = ctx.spec.public_surface.get(target_file.language, [])
    if not patterns or target_file.unit_dir is None:
        return False
    for pat in patterns:
        full = pat.replace("{unit}", target_file.unit_dir.rstrip("/"))
        if glob_to_regex(full).match(target_file.path):
            return True
    return False


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    scope = Scope(rule.scope)
    exempt = ExemptionSet.parse(rule.params.get("exempt"),
                                f"{rule.spec_path}.exempt", ctx.today)
    out: list[Finding] = []
    for f in ctx.index.files:
        if f.unit is None or not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            t = ctx.index.resolve_target(dep.target)
            if t is None or t.unit is None or t.unit == f.unit:
                continue
            if not scope.dep_matches(f, t):
                continue
            if is_public(ctx, t):
                continue
            status, ex = exempt.check(target=dep.target, path=f.path)
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
                                    spec_path=f"{rule.spec_path}.exempt"))
    return out
