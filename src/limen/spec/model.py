"""Specification data model. Loaded from architecture.yaml, validated in validate.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..util import parse_iso_date


class SpecError(Exception):
    """Invalid or internally inconsistent specification (CLI exit code 3)."""

    def __init__(self, issues: list["SpecIssue"]):
        self.issues = issues
        super().__init__("; ".join(i.render() for i in issues))


@dataclass(frozen=True)
class SpecIssue:
    code: str
    path: str
    message: str
    level: str = "error"  # error | warning

    def render(self) -> str:
        return f"[{self.code}] {self.path}: {self.message}"


@dataclass(frozen=True)
class RootSpec:
    name: str
    path: str
    language: str
    namespace: Optional[str] = None
    repo: Optional[str] = None

    @property
    def prefix(self) -> str:
        """Repo-relative prefix under which this root's files live.

        A root declared at repository level (``path: .``) normalises to the
        empty string, so every consumer that joins or strips this prefix keeps
        working on bare repo-relative paths instead of growing a ``./`` segment
        that matches nothing.
        """
        path = "" if self.path in (".", "./") else self.path
        if self.repo:
            return f"{self.repo}/{path}".rstrip("/")
        return path


@dataclass(frozen=True)
class DiscoveryPattern:
    pattern: str
    root: Optional[str] = None  # None = any root


@dataclass(frozen=True)
class ArtifactSet:
    name: str
    discovery: DiscoveryPattern
    identity: str = "declared_fqcn"  # declared_fqcn | path


@dataclass
class Exemption:
    """One entry of exempt/allow/exempt_files/expected/ignore_edges.

    Plain strings are wrapped into an Exemption with only `target` set.
    Past `expires` the entry escalates to severity error (engine-wide rule).
    """

    target: Optional[str] = None      # FQCN / path glob / edge endpoint, kind-dependent
    scope: Optional[str] = None       # importer path glob
    reason: Optional[str] = None
    owner: Optional[str] = None
    expires: Optional[date] = None
    ticket: Optional[str] = None
    since: Optional[date] = None
    extra: dict = field(default_factory=dict)
    spec_path: str = ""

    @classmethod
    def parse(cls, raw: Any, spec_path: str, target_key: str = "target") -> "Exemption":
        if isinstance(raw, str):
            return cls(target=raw, spec_path=spec_path)
        if not isinstance(raw, dict):
            raise SpecError([SpecIssue("exemption-shape", spec_path,
                                       "exemption entry must be a string or a mapping")])
        data = dict(raw)
        target = data.pop(target_key, None) or data.pop("target", None)
        expires = data.pop("expires", None)
        since = data.pop("since", None)
        try:
            expires_d = parse_iso_date(expires) if expires is not None else None
            since_d = parse_iso_date(since) if since is not None else None
        except ValueError as exc:
            raise SpecError([SpecIssue("expires-format", spec_path,
                                       f"not an ISO-8601 date: {exc}")]) from exc
        return cls(
            target=target,
            scope=data.pop("scope", None),
            reason=data.pop("reason", None),
            owner=data.pop("owner", None),
            expires=expires_d,
            ticket=data.pop("ticket", None),
            since=since_d,
            extra=data,
            spec_path=spec_path,
        )

    def status(self, today: date) -> str:
        """'active' | 'expired' — an entry without expires never expires."""
        if self.expires is not None and today > self.expires:
            return "expired"
        return "active"


@dataclass
class Rule:
    id: str
    kind: str
    severity: str
    message: str
    remedy: Optional[str]
    scope: dict
    params: dict          # kind-specific keys, verbatim from YAML
    index: int            # position in the rules list (spec_path building)

    @property
    def spec_path(self) -> str:
        return f"rules[{self.index}]"


@dataclass
class Spec:
    version: int
    project: Optional[str]
    roots: dict[str, RootSpec]
    unit_discovery: list[DiscoveryPattern]
    units_declared: list[str]
    registry_policy: str
    layer_discovery: list[DiscoveryPattern]
    layers_declared: list[str]
    allowed_direction: list[str]
    public_surface: dict[str, list[str]]
    artifact_sets: dict[str, ArtifactSet]
    extractors: dict
    unresolved_default: str
    unresolved_per_root: dict[str, str]
    rules: list[Rule]
    raw: dict
    source_path: Optional[str] = None

    def rule_by_id(self, rule_id: str) -> Optional[Rule]:
        for r in self.rules:
            if r.id == rule_id:
                return r
        return None

    def unresolved_policy_for(self, root: str) -> str:
        return self.unresolved_per_root.get(root, self.unresolved_default)
