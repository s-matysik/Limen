"""K3 consumer-allowlist: a model may be imported only by its owner or a
declared consumer. Allow-list entries accept reason/owner/expires objects."""
from __future__ import annotations

from ..spec.model import Rule
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    set_name = rule.params.get("artifact_set", "")
    members = ctx.artifact_members(set_name)
    allow_raw = rule.params.get("allow") or {}
    scope = Scope(rule.scope)
    consumers: dict[str, ExemptionSet] = {
        target: ExemptionSet.parse(entries, f"{rule.spec_path}.allow.{target}",
                                   ctx.today, target_key="unit")
        for target, entries in allow_raw.items()
    }
    out: list[Finding] = []
    for f in ctx.index.files:
        if f.unit is None or not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            target = dep.target
            if target not in members or target in f.declares:
                continue
            owner = ctx.owner_of(set_name, target)
            if owner is not None and f.unit == owner:
                continue
            status, ex = ("none", None)
            if target in consumers:
                status, ex = consumers[target].check(target=f.unit)
            if status == "active":
                continue
            if status == "expired":
                out.append(make_finding(rule, file=f.path, line=dep.line,
                                        unit=f.unit, target=target,
                                        severity="error",
                                        message=expired_message(rule, ex),
                                        exemptible=True,
                                        spec_path=ex.spec_path))
                continue
            out.append(make_finding(rule, file=f.path, line=dep.line,
                                    unit=f.unit, target=target, exemptible=True,
                                    spec_path=f"{rule.spec_path}.allow"))
    return out
