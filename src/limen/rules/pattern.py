"""K8 pattern-ban: a forbidden textual pattern with per-file exemptions.
Reads raw text through the injectable tree (tests use a fake)."""
from __future__ import annotations

import re

from ..spec.model import Rule
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    try:
        rx = re.compile(str(rule.params.get("pattern", "")))
    except re.error:
        return []  # caught earlier by spec validation (S7)
    scope = Scope(rule.scope)
    exempt = ExemptionSet.parse(rule.params.get("exempt_files"),
                                f"{rule.spec_path}.exempt_files", ctx.today)
    out: list[Finding] = []
    for f in ctx.index.files:
        if not scope.file_matches(f):
            continue
        status, ex = exempt.check(path=f.path, path_target=True)
        if status == "active":
            continue
        text = ctx.tree.read_text(f.path)
        if text is None:
            continue
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            if status == "expired":
                out.append(make_finding(rule, file=f.path, line=line,
                                        unit=f.unit, severity="error",
                                        message=expired_message(rule, ex),
                                        target=m.group(0)[:80],
                                        exemptible=True, spec_path=ex.spec_path))
            else:
                out.append(make_finding(rule, file=f.path, line=line,
                                        unit=f.unit, target=m.group(0)[:80],
                                        exemptible=True,
                                        spec_path=f"{rule.spec_path}.exempt_files"))
    return out
