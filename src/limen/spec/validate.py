"""Specification validation.

Two stages, both mapped to CLI exit code 3 when they fail:

1. JSON-Schema validation of the raw document against
   ``schemas/architecture-spec-1.json`` (a stdlib mini-validator covering the
   subset of Draft-07 those schemas use; ``jsonschema`` is used when installed).
2. Internal integrity (spec §5.2): the specification itself is verified —
   an entry that refers to nothing is detected, so rules cannot rot silently.

Integrity conditions (codes in parentheses):
  S1 schema-invalid            document does not match the JSON Schema
  S2 error-without-remedy      severity: error rule lacks a remedy
  S3 unknown-unit              owners/allow/units refer to an undeclared unit
  S4 dead-entry                allow/exempt/expected/owners entry no file declares
                               (warning by default, error under --strict-spec)
  S5 duplicate-rule-id         two rules share an id
  S6 registry-mismatch         registry_policy: exact and discovered units differ
  S7 regex-invalid             pattern-ban / structure / extractor regex fails to compile
  S8 expires-format            expires is not ISO-8601
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from ..facts.model import FactsIndex
from .model import Exemption, Rule, Spec, SpecError, SpecIssue

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


# --------------------------------------------------------------------------- #
# Stage 1: minimal JSON Schema validator (type/enum/required/properties/
# additionalProperties/items/pattern/minimum — the subset our schemas use).
# --------------------------------------------------------------------------- #

def json_schema_errors(instance: Any, schema: dict, path: str = "$") -> list[str]:
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft7Validator(schema)
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
                for e in sorted(validator.iter_errors(instance), key=str)]
    except ImportError:
        pass
    return _mini_validate(instance, schema, path)


def _type_ok(value: Any, type_name: str) -> bool:
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }[type_name](value)


def _mini_validate(value: Any, schema: dict, path: str) -> list[str]:
    errs: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} is not one of {schema['enum']}")
        return errs
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            errs.append(f"{path}: expected {'/'.join(types)}, got {type(value).__name__}")
            return errs
    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            errs.append(f"{path}: {value!r} does not match pattern {schema['pattern']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errs.append(f"{path}: {value} is below minimum {schema['minimum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{path}: missing required property {key!r}")
        additional = schema.get("additionalProperties", True)
        for key in sorted(value.keys()):
            if key in props:
                errs.extend(_mini_validate(value[key], props[key], f"{path}.{key}"))
            elif isinstance(additional, dict):
                errs.extend(_mini_validate(value[key], additional, f"{path}.{key}"))
            elif additional is False:
                errs.append(f"{path}: unexpected property {key!r}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(_mini_validate(item, schema["items"], f"{path}[{i}]"))
    return errs


# --------------------------------------------------------------------------- #
# Stage 2: internal integrity (§5.2)
# --------------------------------------------------------------------------- #

def iter_rule_exemptions(rule: Rule):
    """Yield (list_name, index, Exemption, flavor) for every exemption-bearing
    list in a rule. flavor: 'fqcn' | 'path' | 'unit' | 'edge'."""
    p = rule.params

    def entries(name: str, raw_list, flavor: str, target_key: str = "target"):
        for i, raw in enumerate(raw_list or []):
            spec_path = f"{rule.spec_path}.{name}[{i}]"
            yield name, i, Exemption.parse(raw, spec_path, target_key), flavor

    if rule.kind == "public-api-only":
        yield from entries("exempt", p.get("exempt"), "fqcn")
    if rule.kind == "scoped-allowlist":
        yield from entries("allow", p.get("allow"), "fqcn")
    if rule.kind == "pattern-ban":
        yield from entries("exempt_files", p.get("exempt_files"), "path",
                           target_key="path")
    if rule.kind == "consumer-allowlist":
        for target in sorted((p.get("allow") or {}).keys()):
            yield ("allow-key", 0,
                   Exemption(target=target, spec_path=f"{rule.spec_path}.allow.{target}"),
                   "fqcn")
    if rule.kind == "ownership":
        for target in sorted((p.get("owners") or {}).keys()):
            yield ("owners-key", 0,
                   Exemption(target=target, spec_path=f"{rule.spec_path}.owners.{target}"),
                   "fqcn")
    if rule.kind == "baseline-snapshot":
        expected = p.get("expected") or {}
        for importer in sorted(expected.keys()):
            for i, raw in enumerate(expected[importer] or []):
                spec_path = f"{rule.spec_path}.expected.{importer}[{i}]"
                ex = Exemption.parse(raw, spec_path)
                ex.scope = importer
                yield "expected", i, ex, "fqcn"
    if rule.kind == "reachability":
        for i, raw in enumerate(p.get("ignore_edges") or []):
            spec_path = f"{rule.spec_path}.ignore_edges[{i}]"
            if isinstance(raw, dict):
                ex = Exemption.parse({k: v for k, v in raw.items()
                                      if k not in ("from", "to")}, spec_path)
                ex.extra["from"] = raw.get("from")
                ex.extra["to"] = raw.get("to")
            else:
                ex = Exemption.parse(raw, spec_path)
            yield "ignore_edges", i, ex, "edge"


def _compile_or_issue(pattern: str, spec_path: str, issues: list[SpecIssue],
                      flags: int = 0) -> None:
    try:
        re.compile(pattern, flags)
    except re.error as exc:
        issues.append(SpecIssue("regex-invalid", spec_path, f"{pattern!r}: {exc}"))


def validate_integrity(spec: Spec,
                       index: Optional[FactsIndex] = None,
                       strict_spec: bool = False,
                       today: Optional[date] = None) -> list[SpecIssue]:
    """Run S2–S8. S4 and S6 need facts; without an index they are skipped.
    Returns issues; caller maps any error-level issue to exit code 3."""
    issues: list[SpecIssue] = []
    declared_units = set(spec.units_declared)

    # S5 duplicate rule id
    seen: dict[str, int] = {}
    for r in spec.rules:
        if r.id in seen:
            issues.append(SpecIssue("duplicate-rule-id", r.spec_path,
                                    f"id {r.id!r} already used at rules[{seen[r.id]}]"))
        else:
            seen[r.id] = r.index

    for r in spec.rules:
        # referenced artifact sets must exist
        if r.kind in ("ownership", "consumer-allowlist"):
            set_name = r.params.get("artifact_set")
            if set_name not in spec.artifact_sets:
                issues.append(SpecIssue(
                    "unknown-artifact-set", f"{r.spec_path}.artifact_set",
                    f"{set_name!r} is not declared in artifact_sets"))
        # S2 severity: error without remedy
        if r.severity == "error" and not (r.remedy and str(r.remedy).strip()):
            issues.append(SpecIssue("error-without-remedy", r.spec_path,
                                    "severity: error requires a remedy"))
        # S3 unknown units referenced by rule parameters
        if declared_units:
            if r.kind == "ownership":
                for target, owner in sorted((r.params.get("owners") or {}).items()):
                    if owner not in declared_units:
                        issues.append(SpecIssue(
                            "unknown-unit", f"{r.spec_path}.owners.{target}",
                            f"owner {owner!r} is not in units.declared"))
            if r.kind == "consumer-allowlist":
                for target, consumers in sorted((r.params.get("allow") or {}).items()):
                    for c in consumers or []:
                        if c not in declared_units:
                            issues.append(SpecIssue(
                                "unknown-unit", f"{r.spec_path}.allow.{target}",
                                f"consumer {c!r} is not in units.declared"))
            if r.kind == "baseline-snapshot":
                for u in r.params.get("exclude_units") or []:
                    if u not in declared_units:
                        issues.append(SpecIssue(
                            "unknown-unit", f"{r.spec_path}.exclude_units",
                            f"{u!r} is not in units.declared"))
            if r.kind == "reachability":
                for u in (r.params.get("units") or []) + (r.params.get("order") or []):
                    if u not in declared_units:
                        issues.append(SpecIssue(
                            "unknown-unit", f"{r.spec_path}.units",
                            f"{u!r} is not in units.declared"))
        # S7 regex compilation
        if r.kind == "pattern-ban" and r.params.get("pattern"):
            _compile_or_issue(str(r.params["pattern"]), f"{r.spec_path}.pattern", issues)
        if r.kind == "structure":
            assert_block = r.params.get("assert") or {}
            for i, rx in enumerate(assert_block.get("must_match") or []):
                _compile_or_issue(str(rx), f"{r.spec_path}.assert.must_match[{i}]",
                                  issues, re.MULTILINE)
        # S8 expires format (raised by Exemption.parse) + collect entries for S4
        try:
            entries = list(iter_rule_exemptions(r))
        except SpecError as exc:
            issues.extend(exc.issues)
            entries = []

        # S4 dead entries — an allow/exempt target no file declares
        if index is not None:
            level = "error" if strict_spec else "warning"
            for _name, _i, ex, flavor in entries:
                if ex.target is None:
                    continue
                if flavor == "fqcn":
                    if ex.target not in index.by_declared and ex.target not in index.by_path:
                        issues.append(SpecIssue(
                            "dead-entry", ex.spec_path,
                            f"no file declares {ex.target!r}", level=level))
                elif flavor == "path":
                    from ..util import glob_to_regex
                    rx = glob_to_regex(ex.target)
                    if not any(rx.match(f.path) for f in index.files):
                        issues.append(SpecIssue(
                            "dead-entry", ex.spec_path,
                            f"no file matches {ex.target!r}", level=level))

    # S7 regexes inside the extractor configuration
    for lang, cfg in sorted((spec.extractors or {}).items()):
        for section in ("patterns", "strip"):
            for i, entry in enumerate((cfg or {}).get(section) or []):
                if isinstance(entry, dict) and "regex" in entry:
                    flags = re.DOTALL if "DOTALL" in (entry.get("flags") or []) else 0
                    _compile_or_issue(str(entry["regex"]),
                                      f"extractors.{lang}.{section}[{i}].regex",
                                      issues, flags)
        declares = (cfg or {}).get("declares") or {}
        for key in sorted(declares.keys()):
            _compile_or_issue(str(declares[key]),
                              f"extractors.{lang}.declares.{key}", issues, re.MULTILINE)

    # S6 registry policy
    if index is not None and spec.registry_policy == "exact" and spec.units_declared:
        discovered = set(index.units())
        declared = set(spec.units_declared)
        if discovered != declared:
            missing = sorted(declared - discovered)
            extra = sorted(discovered - declared)
            issues.append(SpecIssue(
                "registry-mismatch", "units.declared",
                f"registry_policy is exact but discovered units differ "
                f"(missing in tree: {missing}, undeclared: {extra})"))

    return sorted(issues, key=lambda i: (i.level != "error", i.code, i.path, i.message))


def validate_document(raw: dict, schema: dict) -> list[SpecIssue]:
    """S1: JSON-Schema validation of the raw spec document."""
    return [SpecIssue("schema-invalid", "$", err)
            for err in json_schema_errors(raw, schema)]
