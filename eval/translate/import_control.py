#!/usr/bin/env python3
"""Translate a checkstyle ``import-control.xml`` into limen rules.

Native semantics (checkstyle ``ImportControlCheck``, DTD import_control_1_4,
as implemented in ``PkgImportControl`` / ``AbstractImportControl``):

* The document root ``<import-control pkg="...">`` declares the package the
  policy governs; a file is checked only if its package is inside it.
* ``<subpackage name="x">`` narrows the scope to ``<parent>.x``; subpackages
  nest arbitrarily.
* Inside a scope, ``<allow>`` and ``<disallow>`` elements are consulted **in
  document order**; the first element matching the imported name decides. If
  no element in the innermost scope matches, the decision walks **outward** to
  the parent scope and so on to the root. An import undecided at the root is
  **disallowed** — checkstyle denies by default.
* ``pkg="a.b"`` matches ``a.b`` and everything under it; with
  ``exact-match="true"`` it matches only names directly in ``a.b``.
  ``class="a.b.C"`` matches that one name.
* ``local-only="true"`` restricts an allowance to the declaring scope; it does
  not apply to nested subpackages.

limen mapping. A first-match-wins cascade is not an allowlist, so the faithful
mapping is not ``K5 scoped-allowlist``. Each governed scope becomes one

    K6 forbidden-target rule, scoped to that package's directory,

whose ``forbid`` list is the set of import targets the native cascade denies
for files in that scope. That set is produced by **evaluating the native
cascade** over the concrete import targets present in the tree, so the
translation does not reproduce checkstyle's precedence inside limen's rule
language — it reproduces its *decisions*. The differential then measures
whether limen's extractor and engine reach the same (file, line, target)
triples, which is the property under test.

Constructs this translator REFUSES to translate (recorded, never silently
dropped, so a refusal shows up as an untranslated repo rather than a wrong
number):

* ``regex="true"`` on any element — checkstyle then reads ``pkg``/``class`` as
  a Java regex with per-attribute anchoring rules;
* ``<file name="...">`` scopes — limen scopes are path globs and cannot key on
  a declared type name;
* any ``strategyOnMismatch`` other than the default.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, Optional

ALLOW, DISALLOW = "allow", "disallow"


class Untranslatable(Exception):
    """The native config uses a construct this translator refuses to guess."""


@dataclass
class Guard:
    """One <allow>/<disallow> element."""
    verdict: str
    pkg: Optional[str]
    cls: Optional[str]
    exact_match: bool
    local_only: bool
    order: int

    def matches(self, target: str, *, local: bool) -> bool:
        if self.local_only and not local:
            return False
        if self.cls is not None:
            return target == self.cls
        if self.pkg is None:
            return False
        if self.exact_match:
            if not target.startswith(self.pkg + "."):
                return False
            return "." not in target[len(self.pkg) + 1:]
        return target == self.pkg or target.startswith(self.pkg + ".")

    def describe(self) -> str:
        what = (f"class={self.cls}" if self.cls
                else f"pkg={self.pkg}" + (" exact-match" if self.exact_match else ""))
        return f"<{self.verdict} {what}/>"


@dataclass
class ScopeNode:
    """<import-control> or <subpackage>: a package scope with ordered guards.

    ``uncovered`` is set when the scope contains a construct this translator
    refuses to reproduce (currently only ``<file>``). The scope and everything
    below it is then excluded from the comparison and counted, rather than
    translated approximately — see `coverage`.
    """
    pkg: str
    guards: list[Guard] = field(default_factory=list)
    children: dict[str, "ScopeNode"] = field(default_factory=dict)
    parent: Optional["ScopeNode"] = None
    uncovered: Optional[str] = None


def _bool(el: ET.Element, name: str) -> bool:
    return (el.get(name) or "false").strip().lower() == "true"


def _strip_doctype(text: str) -> str:
    return re.sub(r"<!DOCTYPE[^>]*(\[[^\]]*\])?>", "", text, flags=re.DOTALL)


def parse_import_control(text: str) -> ScopeNode:
    """Parse an import-control document into a scope tree."""
    root_el = ET.fromstring(_strip_doctype(text))
    if root_el.tag != "import-control":
        raise Untranslatable(f"root element is <{root_el.tag}>")
    if _bool(root_el, "regex"):
        raise Untranslatable('regex="true" on <import-control>')
    if root_el.get("strategyOnMismatch") not in (None, "disallowed"):
        raise Untranslatable(
            f"strategyOnMismatch={root_el.get('strategyOnMismatch')!r}")

    counter = [0]

    def build(el: ET.Element, pkg: str, parent: Optional[ScopeNode]) -> ScopeNode:
        node = ScopeNode(pkg=pkg, parent=parent)
        for child in el:
            tag = child.tag
            if tag in ("allow", "disallow"):
                if _bool(child, "regex"):
                    raise Untranslatable(f'regex="true" on <{tag}> in {pkg}')
                counter[0] += 1
                node.guards.append(Guard(
                    verdict=ALLOW if tag == "allow" else DISALLOW,
                    pkg=child.get("pkg"), cls=child.get("class"),
                    exact_match=_bool(child, "exact-match"),
                    local_only=_bool(child, "local-only"),
                    order=counter[0]))
            elif tag == "subpackage":
                if _bool(child, "regex"):
                    raise Untranslatable(f'regex="true" on <subpackage> in {pkg}')
                name = child.get("name")
                if not name:
                    raise Untranslatable(f"<subpackage> without name in {pkg}")
                node.children[name] = build(child, f"{pkg}.{name}", node)
            elif tag == "file":
                # A <file> scope keys on the *declared type name*, which a
                # path-glob rule scope cannot express. Rather than abandon the
                # whole document or approximate the semantics, mark this scope
                # (and, through `is_covered`, everything below it) as not
                # translated. The exclusion is counted, never silently applied.
                node.uncovered = (
                    f'<file name={child.get("name")!r}'
                    + (' regex="true"' if _bool(child, "regex") else "")
                    + "/> keys on the declared type name, which a path-glob "
                      "rule scope cannot express")
            elif isinstance(child.tag, str):
                raise Untranslatable(f"unknown element <{tag}> in {pkg}")
        return node

    return build(root_el, root_el.get("pkg") or "", None)


def scope_for_package(root: ScopeNode, package: str) -> Optional[ScopeNode]:
    """The innermost scope governing `package`, or None if outside the root.

    A ``<subpackage name="...">`` may carry a DOTTED name — Kafka's
    ``import-control-core.xml`` declares ``<subpackage name="log.remote">``,
    which governs ``kafka.log.remote``. Matching one path segment at a time
    therefore misses it, the walk stops at the parent, and every import the
    subpackage allows is reported as denied. The descent consumes as many
    segments as the child's name spells, longest name first.
    """
    if package != root.pkg and not package.startswith(root.pkg + "."):
        return None
    node = root
    rest = [p for p in package[len(root.pkg):].lstrip(".").split(".") if p]
    i = 0
    while i < len(rest):
        child = None
        for name in sorted(node.children, key=lambda n: -n.count(".")):
            parts = name.split(".")
            if rest[i:i + len(parts)] == parts:
                child, i = node.children[name], i + len(parts)
                break
        if child is None:
            break
        node = child
    return node


def decide(scope: ScopeNode, target: str) -> tuple[str, Optional[Guard], str]:
    """Evaluate the native cascade for one import target.

    Returns (verdict, deciding guard, package of the scope that decided).
    """
    node: Optional[ScopeNode] = scope
    local = True
    while node is not None:
        for g in node.guards:
            if g.matches(target, local=local):
                return g.verdict, g, node.pkg
        node = node.parent
        local = False
    return DISALLOW, None, ""


def forbidden_targets(root: ScopeNode, package: str,
                      targets: Iterable[str]) -> list[tuple[str, str]]:
    """The subset of `targets` the native cascade denies for `package`."""
    scope = scope_for_package(root, package)
    if scope is None:
        return []
    out: list[tuple[str, str]] = []
    for t in sorted(set(targets)):
        verdict, guard, at = decide(scope, t)
        if verdict != DISALLOW:
            continue
        why = (f"{guard.describe()} in scope {at or '(root)'}" if guard
               else "no allow element matched; checkstyle denies by default")
        out.append((t, why))
    return out


def is_covered(scope: ScopeNode) -> tuple[bool, str]:
    """Is this scope translated? Walks outward — an uncovered ancestor
    disqualifies every scope below it, because the cascade for those packages
    consults the ancestor's untranslated guards."""
    node: Optional[ScopeNode] = scope
    while node is not None:
        if node.uncovered:
            return False, f"{node.pkg}: {node.uncovered}"
        node = node.parent
    return True, ""


def coverage(root: ScopeNode) -> dict:
    """How much of the document the translation covers, as counts."""
    scopes = all_scopes(root)
    covered = [s for s in scopes if is_covered(s)[0]]
    return {
        "scopes_total": len(scopes),
        "scopes_covered": len(covered),
        "scopes_uncovered": len(scopes) - len(covered),
        "guards_total": sum(len(s.guards) for s in scopes),
        "guards_covered": sum(len(s.guards) for s in covered),
        "uncovered_reasons": sorted({is_covered(s)[1] for s in scopes
                                     if not is_covered(s)[0]}),
    }


def all_scopes(root: ScopeNode) -> list[ScopeNode]:
    out = [root]
    for c in sorted(root.children):
        out.extend(all_scopes(root.children[c]))
    return out


def count_guards(root: ScopeNode) -> int:
    return sum(len(s.guards) for s in all_scopes(root))


def count_scopes(root: ScopeNode) -> int:
    return len(all_scopes(root))
