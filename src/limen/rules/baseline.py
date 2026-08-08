"""K7 baseline-snapshot: the actual set of cross-unit imports matching a
target pattern must EQUAL the declared set — in both directions.

An allowlist rots in one direction; a snapshot rots in none: the rule fires
both when a new import appears and when a declared exception has vanished from
the code and lingers dead in the specification. Full-scan rule under
--changed-only.
"""
from __future__ import annotations

from ..spec.model import Exemption, Rule
from ..util import fqcn_glob_to_regex
from .base import (Finding, RuleContext, Scope, expired_message, make_finding)


def actual_pairs(rule: Rule, ctx: RuleContext) -> list[tuple[str, str, int, str]]:
    """Sorted (importer_path, target, line, unit) matching the snapshot pattern."""
    rx = fqcn_glob_to_regex(str(rule.params.get("target_pattern", "")))
    exclude = set(rule.params.get("exclude_units") or [])
    scope = Scope(rule.scope)
    out = []
    for f in ctx.index.files:
        if f.unit in exclude or not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            if not rx.match(dep.target) or dep.target in f.declares:
                continue
            out.append((f.path, dep.target, dep.line, f.unit or ""))
    return sorted(out)


def parse_expected(rule: Rule) -> dict[tuple[str, str], Exemption]:
    expected = rule.params.get("expected") or {}
    out: dict[tuple[str, str], Exemption] = {}
    for importer in sorted(expected.keys()):
        for i, raw in enumerate(expected[importer] or []):
            ex = Exemption.parse(raw, f"{rule.spec_path}.expected.{importer}[{i}]")
            ex.scope = importer
            out[(importer, ex.target or "")] = ex
    return out


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    expected = parse_expected(rule)
    consumed: set[tuple[str, str]] = set()
    out: list[Finding] = []

    for path, target, line, unit in actual_pairs(rule, ctx):
        key = (path, target)
        ex = expected.get(key)
        if ex is None:
            out.append(make_finding(
                rule, file=path, line=line, unit=unit or None, target=target,
                message=f"{rule.message} [not in the declared baseline]",
                exemptible=True, spec_path=f"{rule.spec_path}.expected"))
            continue
        consumed.add(key)
        if ex.status(ctx.today) == "expired":
            out.append(make_finding(
                rule, file=path, line=line, unit=unit or None, target=target,
                severity="error", message=expired_message(rule, ex),
                exemptible=True, spec_path=ex.spec_path))

    for key in sorted(expected.keys()):
        if key in consumed:
            continue
        importer, target = key
        ex = expected[key]
        out.append(make_finding(
            rule, file=importer, target=target, severity="error",
            message=(f"{rule.message} [dead baseline entry: the declared "
                     f"exception no longer exists in the code]"),
            exemptible=True, spec_path=ex.spec_path))
    return out
