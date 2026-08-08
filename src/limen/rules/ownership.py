"""K2 ownership: every artifact in the set has exactly one declared owner.

Full-scan rule: under --changed-only it is computed from cached facts,
never skipped.
"""
from __future__ import annotations

from ..spec.model import Rule
from .base import Finding, RuleContext, make_finding


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    owners = rule.params.get("owners") or {}
    members = ctx.artifact_members(rule.params.get("artifact_set", ""))
    out: list[Finding] = []
    if rule.params.get("require_every_artifact_owned", True):
        for artifact in sorted(members):
            if artifact not in owners:
                f = members[artifact]
                out.append(make_finding(
                    rule, file=f.path, unit=f.unit, target=artifact,
                    message=f"{rule.message} [no owner declared for {artifact}]",
                    spec_path=f"{rule.spec_path}.owners"))
    return out
