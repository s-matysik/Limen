#!/usr/bin/env python3
"""Translate a dependency-cruiser rule set into limen rules.

A `.dependency-cruiser.js/.cjs` config is executable JavaScript, and this study
does not evaluate third-party code. It does not have to: dependency-cruiser
itself emits the **fully resolved** rule set under `summary.ruleSetUsed` in its
JSON report, with every `require`, spread and computed value already applied.
The translator therefore reads the tool's own resolved output as data — the
same source of truth the tool used for its verdicts.

Native semantics of a `forbidden` rule (dependency-cruiser 17.x):

    { name, from: {...}, to: {...}, severity }

A dependency is a violation when the SOURCE module matches every `from`
criterion and the TARGET matches every `to` criterion. The ONLY criterion this
translator reproduces is:

    from.path / to.path         and only when the regex is a plain directory
                                prefix (`^src/domain/`), never a general regex

Everything else is refused, and refused for the same reason in each case: the
emitted limen rule would not carry the constraint, so the translated rule would
ban strictly more than the native rule does. That includes `pathNot` (limen's
rule scope has no negated-target form), `dependencyTypes`/`dependencyTypesNot`
(no equivalent in a K6 forbid list), and any non-prefix `path` regex.

limen mapping: every emitted rule is `K6 forbidden-target`, scoped by the
`from` prefix with the `to` prefix as the forbidden target. `K1
layer-dependency` is deliberately NOT used: it would require binding each
prefix to a layer name through limen's positional `{layer}` discovery, and
dependency-cruiser prefixes are arbitrary paths that need not sit at a common
depth (`packages/clients/web/src/` and `packages/core/` in the same config), so
the binding would be a guess.

Criteria that are NOT path predicates (`to.circular`, `to.orphan`,
`to.reachable`, `to.moreThanOneDependencyType`, `to.license`,
`from.orphan`, ...) express properties of the dependency GRAPH or of package
metadata rather than of a single edge. `circular` and `reachable` correspond to
limen's `K11 reachability`, but their semantics differ enough (cycle detection
vs declared independence) that mapping them silently would misstate what limen
checks; a rule using one is reported as untranslatable and the repository is
excluded, with the criterion named.
"""
from __future__ import annotations

import re
from typing import Any

#: The only criterion reproduced. `pathNot` is deliberately absent: it appears
#: in the refusal list below, not here, because nothing in the emitted rule
#: carries it.
PATH_KEYS = {"path"}
#: Criteria that are graph/metadata predicates, not edge predicates.
GRAPH_KEYS = {"circular", "orphan", "reachable", "couldNotResolve",
              "moreThanOneDependencyType", "license", "licenseNot",
              "exoticallyRequired", "exoticRequire", "exoticRequireNot",
              "dynamic", "preCompilationOnly", "matchesDoc", "via", "viaOnly",
              "viaNot", "viaSomeNot", "reachableViaSomeSubgraph"}


class Untranslatable(Exception):
    pass


def _simple_prefix(pattern: str | None) -> str | None:
    """A `path` regex that is really a directory prefix, or None.

    dependency-cruiser path criteria are regexes. `^src/domain/` and
    `src/domain/` are prefixes; `\\.(spec|test)\\.ts$` is not. Only the prefix
    form is translated — guessing at a general regex would silently change
    which files a rule covers.
    """
    if not pattern:
        return None
    p = pattern.lstrip("^")
    if re.search(r"[\\\[\](){}|+*?$]", p):
        return None
    return p.rstrip("/") + "/"


def to_limen(rule_set: dict, root_path: str) -> tuple[dict, dict]:
    """Emit a limen spec from dependency-cruiser's own `ruleSetUsed`."""
    forbidden = rule_set.get("forbidden") or []
    if not forbidden:
        raise Untranslatable("the resolved rule set declares no `forbidden` rule")

    # Rule-by-rule, not all-or-nothing: a config that pairs three path bans
    # with one `no-circular` rule is mostly translatable, and discarding the
    # repository would throw away three real comparisons. Untranslated rules
    # are named and counted, and the comparison is confined to the findings of
    # the translated ones (see `native_rule_ids_translated`, which the driver
    # uses to filter the native side — a native finding from an untranslated
    # rule is not a limen miss).
    rules: list[dict] = []
    pairs: list[tuple[str, str]] = []
    untranslated: list[dict[str, str]] = []
    translated_names: list[str] = []
    for r in forbidden:
        name = r.get("name") or "unnamed"
        frm, to = r.get("from") or {}, r.get("to") or {}
        bad = sorted((set(frm) | set(to)) & GRAPH_KEYS)
        if bad:
            untranslated.append({"rule": name, "reason":
                                 f"graph/metadata criteria {bad} are not "
                                 "predicates over a single dependency edge"})
            continue
        # Every criterion must be either APPLIED below or refused here.
        # `dependencyTypes`/`dependencyTypesNot` narrow a rule to a class of
        # edge (npm, local, core, ...); limen's K6 forbid-list has no
        # equivalent, so accepting one and ignoring it would emit a rule that
        # bans strictly more than the native rule does.
        unknown = sorted((set(frm) | set(to)) - PATH_KEYS)
        if unknown:
            untranslated.append({"rule": name, "reason":
                                 f"criteria {unknown} are not expressible and "
                                 "would be silently dropped, widening the rule"})
            continue
        src = _simple_prefix(frm.get("path"))
        dst = _simple_prefix(to.get("path"))
        if src is None or dst is None:
            untranslated.append({"rule": name, "reason":
                                 f"from.path={frm.get('path')!r} / "
                                 f"to.path={to.get('path')!r} is a regex, not "
                                 "a directory prefix"})
            continue
        if frm.get("pathNot") or to.get("pathNot"):
            untranslated.append({"rule": name, "reason":
                                 "pathNot is not expressible on the target side"})
            continue
        translated_names.append(name)
        severity = "error" if r.get("severity", "error") == "error" else "warning"
        pairs.append((src, dst))
        rules.append({
            "id": "dc-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
            "kind": "forbidden-target",
            "severity": severity,
            "scope": {"roots": ["main"], "paths": [src + "**"]},
            "forbid": [dst + "**", dst.rstrip("/")],
            "message": f"{name}: {src} must not depend on {dst}.",
            "remedy": "Invert the dependency, or route it through an "
                      "interface the source layer may import.",
        })
    if not rules:
        raise Untranslatable(
            "no `forbidden` rule is a directory-prefix ban; untranslated: "
            + "; ".join(f"{u['rule']} ({u['reason']})" for u in untranslated[:4]))
    spec = {
        "version": 1,
        "project": "dependency-cruiser-retrofit",
        "roots": {"main": {"path": root_path, "language": "js"}},
        "unresolved_policy": {"default": "off"},
        "rules": rules,
    }
    return spec, {"native_forbidden_rules": len(forbidden),
                  "rules_emitted": len(rules),
                  "rules_untranslated": len(untranslated),
                  "untranslated": untranslated,
                  "native_rule_ids_translated": translated_names,
                  "pairs": [f"{a} -> {b}" for a, b in pairs]}
