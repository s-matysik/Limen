"""Load architecture.yaml into a Spec object. Structural errors raise SpecError."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .model import (ArtifactSet, DiscoveryPattern, RootSpec, Rule, Spec,
                    SpecError, SpecIssue)

_RULE_COMMON_KEYS = {"id", "kind", "severity", "message", "remedy", "scope"}


def load_spec(path: str | Path) -> Spec:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SpecError([SpecIssue("spec-missing", str(path),
                                   "specification file not found")]) from None
    except yaml.YAMLError as exc:
        raise SpecError([SpecIssue("yaml-parse", str(path), str(exc))]) from exc
    if not isinstance(raw, dict):
        raise SpecError([SpecIssue("yaml-shape", str(path), "top level must be a mapping")])
    spec = build_spec(raw)
    spec.source_path = str(path)
    return spec


def build_spec(raw: dict) -> Spec:
    issues: list[SpecIssue] = []

    roots: dict[str, RootSpec] = {}
    for name, r in (raw.get("roots") or {}).items():
        if not isinstance(r, dict):
            issues.append(SpecIssue("root-shape", f"roots.{name}", "must be a mapping"))
            continue
        roots[name] = RootSpec(
            name=name,
            path=str(r.get("path", "")),
            language=str(r.get("language", "")),
            namespace=r.get("namespace"),
            repo=r.get("repo"),
        )

    units = raw.get("units") or {}
    unit_discovery = [
        DiscoveryPattern(pattern=d["pattern"], root=d.get("root"))
        for d in units.get("discovery", []) or []
        if isinstance(d, dict) and "pattern" in d
    ]
    layers = raw.get("layers") or {}
    layer_discovery = [
        DiscoveryPattern(pattern=d["pattern"], root=d.get("root"))
        for d in layers.get("discovery", []) or []
        if isinstance(d, dict) and "pattern" in d
    ]

    artifact_sets: dict[str, ArtifactSet] = {}
    for name, a in (raw.get("artifact_sets") or {}).items():
        disc = (a or {}).get("discovery") or {}
        artifact_sets[name] = ArtifactSet(
            name=name,
            discovery=DiscoveryPattern(pattern=disc.get("pattern", ""), root=disc.get("root")),
            identity=(a or {}).get("identity", "declared_fqcn"),
        )

    unresolved = raw.get("unresolved_policy") or {}

    def _policy(v):
        # YAML 1.1 parses a bare `off` as boolean False; users mean the policy
        if v is False:
            return "off"
        if v is True:
            return "warn"
        return v


    rules: list[Rule] = []
    for i, r in enumerate(raw.get("rules") or []):
        if not isinstance(r, dict):
            issues.append(SpecIssue("rule-shape", f"rules[{i}]", "must be a mapping"))
            continue
        params = {k: v for k, v in r.items() if k not in _RULE_COMMON_KEYS}
        kind = str(r.get("kind", ""))
        default_severity = "warning" if kind == "metric-threshold" else "error"
        rules.append(Rule(
            id=str(r.get("id", f"rule-{i}")),
            kind=kind,
            severity=str(r.get("severity", default_severity)),
            message=str(r.get("message", "")),
            remedy=r.get("remedy"),
            scope=r.get("scope") or {},
            params=params,
            index=i,
        ))

    if issues:
        raise SpecError(issues)

    return Spec(
        version=raw.get("version", 0),
        project=raw.get("project"),
        roots=roots,
        unit_discovery=unit_discovery,
        units_declared=list(units.get("declared") or []),
        registry_policy=units.get("registry_policy", "open"),
        layer_discovery=layer_discovery,
        layers_declared=list(layers.get("declared") or []),
        allowed_direction=list(layers.get("allowed_direction") or []),
        public_surface={k: list(v or []) for k, v in (raw.get("public_surface") or {}).items()},
        artifact_sets=artifact_sets,
        extractors=raw.get("extractors") or {},
        unresolved_default=_policy(unresolved.get("default", "warn")),
        unresolved_per_root={k: _policy(v) for k, v in
                             (unresolved.get("per_root") or {}).items()},
        rules=rules,
        raw=raw,
    )


def schemas_dir() -> Path:
    """Locate the shipped JSON schemas (repo checkout or installed package)."""
    here = Path(__file__).resolve()
    for candidate in (
        here.parents[3] / "schemas",          # src/limen/spec -> repo root
        here.parents[1] / "schemas",          # installed alongside package
    ):
        if (candidate / "architecture-spec-1.json").exists():
            return candidate
    raise FileNotFoundError("schemas/architecture-spec-1.json not found")


def load_schema(name: str) -> dict:
    import json
    return json.loads((schemas_dir() / name).read_text(encoding="utf-8"))
