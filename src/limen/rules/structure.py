"""K9 structure: required/forbidden directories and files, per-unit
documentation contracts, and the paired code↔docs declaration.

``paired_declaration`` turns "every new baseline exception must also update the
architecture doc" — a rule that otherwise lives only in agent guidelines and is
enforced by goodwill — into a CI gate: every key declared in the source rule
must literally appear in the named document.
"""
from __future__ import annotations

import re

from ..spec.model import Rule
from ..util import glob_to_regex
from .base import Finding, RuleContext, make_finding
from .baseline import parse_expected


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    assertion = rule.params.get("assert") or {}
    out: list[Finding] = []

    root_name = assertion.get("root")
    prefix = ""
    if root_name and root_name in ctx.spec.roots:
        prefix = ctx.spec.roots[root_name].prefix

    for pat in assertion.get("forbidden_dirs") or []:
        rx = glob_to_regex(pat if pat.startswith(prefix) or not prefix
                           else f"{prefix}/{pat}".replace("//", "/"))
        loose = glob_to_regex(pat)
        for d in ctx.tree.walk_dirs(prefix):
            if rx.match(d) or loose.match(d):
                out.append(make_finding(
                    rule, file=d, target=pat,
                    message=f"{rule.message} [forbidden directory exists: {d}]"))

    for rel in assertion.get("required_files") or []:
        if not ctx.tree.exists(rel):
            out.append(make_finding(
                rule, file=rel,
                message=f"{rule.message} [required file missing: {rel}]"))

    for pat in assertion.get("required_dirs") or []:
        rx = glob_to_regex(pat)
        if not any(rx.match(d) for d in ctx.tree.walk_dirs(prefix)):
            out.append(make_finding(
                rule, file=pat,
                message=f"{rule.message} [required directory missing: {pat}]"))

    feu = assertion.get("for_each_unit")
    if feu:
        unit_root = feu.get("root")
        filename = assertion.get("file", "README.md")
        must = [str(m) for m in assertion.get("must_match") or []]
        unit_dirs: dict[str, str] = {}
        for f in ctx.index.files:
            if unit_root is not None and f.root != unit_root:
                continue
            if f.unit and f.unit_dir:
                unit_dirs.setdefault(f.unit, f.unit_dir)
        for unit in sorted(unit_dirs):
            doc_path = f"{unit_dirs[unit].rstrip('/')}/{filename}"
            text = ctx.tree.read_text(doc_path)
            if text is None:
                out.append(make_finding(
                    rule, file=doc_path, unit=unit,
                    message=f"{rule.message} [missing {filename} for unit {unit}]"))
                continue
            for rx_src in must:
                if re.search(rx_src, text, re.MULTILINE) is None:
                    out.append(make_finding(
                        rule, file=doc_path, unit=unit, target=rx_src,
                        message=(f"{rule.message} [required section not found: "
                                 f"{rx_src!r}]")))

    paired = assertion.get("paired_declaration")
    if paired:
        source = ctx.spec.rule_by_id(str(paired.get("source_rule", "")))
        doc_rel = str(paired.get("must_appear_in", ""))
        if source is None:
            out.append(make_finding(
                rule, file=None, target=paired.get("source_rule"),
                message=f"{rule.message} [source_rule not found]"))
            return out
        keys: list[str] = []
        if source.kind == "baseline-snapshot":
            keys = sorted({t for (_i, t) in parse_expected(source).keys()})
        else:
            for raw in source.params.get("exempt") or []:
                keys.append(raw if isinstance(raw, str) else str(raw.get("target", "")))
        text = ctx.tree.read_text(doc_rel)
        if text is None:
            if keys:
                out.append(make_finding(
                    rule, file=doc_rel,
                    message=f"{rule.message} [document missing: {doc_rel}]"))
            return out
        for key in sorted(k for k in keys if k):
            if key not in text:
                out.append(make_finding(
                    rule, file=doc_rel, target=key,
                    message=(f"{rule.message} [declared exception not "
                             f"documented: {key}]")))
    return out
