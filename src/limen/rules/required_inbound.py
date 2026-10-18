"""K12 required-inbound: a POSITIVE obligation over the dependency graph.

Every other kind in this engine either forbids an edge, caps a metric, or
asserts that a path exists.  None of them can state the obligation that
maintainers write most often about *new* code:

    "A new estimator must be exported from the package's api.py."
    "A new sampler must be re-exported by its package __init__.py."

Measured on eight statistics libraries, three of the four norms that the
vocabulary could not express were of exactly this shape (statsmodels
AGENTS.md:53-57, :58-60, :201-204).  A prohibition cannot substitute for it:
forbidding the wrong import says nothing about a file nobody imports at all.

Semantics.  For every file in `scope`, at least one file matching one of the
`from` globs must depend on it.  The dependency may be recorded against the
target's declared symbol (Python module name, PHP FQCN) or against its
repo-relative path (JS) -- resolution goes through the facts index, so the rule
is language-agnostic.  A file in scope with no inbound edge from any `from`
file is a finding ON THAT FILE, which is where the remedy belongs.

This is deliberately an *existence* obligation and not a reachability one: it
asks whether the designated entry point mentions the artefact, not whether some
path exists through the graph.  Transitive re-export is a different question and
`reachability` already owns it.

Params:
    from:   list of path globs naming the designated referencing files.
            Required; an empty list is a specification error, not a vacuous pass.
    via:    optional list of dependency kinds (import | use | require | ...).
            Default: any kind counts.
    exempt: standard exemption entries, keyed by `path` (the unexported file).
"""
from __future__ import annotations

from ..spec.model import Rule
from ..util import glob_to_regex
from .base import (ExemptionSet, Finding, RuleContext, Scope, expired_message,
                   make_finding)


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    froms = [str(p) for p in rule.params.get("from") or []]
    if not froms:
        # A positive obligation with no designated exporter would pass for every
        # tree, which is the fail-open shape this kind exists to remove.
        return [make_finding(
            rule, file=None,
            message=(f"{rule.spec_path}: `required-inbound` needs a non-empty "
                     f"`from` list naming the file(s) that must reference the "
                     f"artefacts in scope."),
            severity="error")]
    via = {str(v) for v in rule.params.get("via") or []}
    from_rx = [glob_to_regex(p) for p in froms]
    scope = Scope(rule.scope)
    exempt = ExemptionSet.parse(rule.params.get("exempt"),
                                f"{rule.spec_path}.exempt", ctx.today)

    referenced: set[str] = set()
    for g in ctx.index.files:
        if not any(rx.match(g.path) for rx in from_rx):
            continue
        for dep in g.deps:
            if via and dep.via not in via:
                continue
            target = ctx.index.resolve_target(dep.target)
            if target is not None:
                referenced.add(target.path)

    out: list[Finding] = []
    for f in ctx.index.files:
        if not scope.file_matches(f):
            continue
        if any(rx.match(f.path) for rx in from_rx):
            # the exporter never has to export itself
            continue
        if f.path in referenced:
            continue
        status, ex = exempt.check(target=f.path, path=f.path)
        if status == "active":
            continue
        if status == "expired":
            out.append(make_finding(rule, file=f.path, unit=f.unit,
                                    target=f.path, severity="error",
                                    message=expired_message(rule, ex),
                                    exemptible=True, spec_path=ex.spec_path))
            continue
        out.append(make_finding(rule, file=f.path, unit=f.unit, target=f.path,
                                exemptible=True,
                                spec_path=f"{rule.spec_path}.exempt"))
    return out
