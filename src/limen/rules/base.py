"""Shared rule infrastructure: findings, scope matching, exemptions, context.

Rules are pure functions over injected facts. They never touch the language
parsers; the only filesystem access is the ``tree`` abstraction needed by the
``structure`` and ``pattern-ban`` kinds, which tests replace with a fake.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from ..facts.model import FactsIndex, FileFacts
from ..spec.model import Exemption, Rule, Spec
from ..util import fqcn_match, glob_to_regex, posix

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    rule_id: str
    kind: str
    severity: str
    file: Optional[str]
    message: str
    line: Optional[int] = None
    unit: Optional[str] = None
    target: Optional[str] = None
    remedy: Optional[str] = None
    exemptible: bool = False
    spec_path: Optional[str] = None
    path_trace: Optional[str] = None

    def sort_key(self):
        return (SEVERITY_RANK.get(self.severity, 9), self.rule_id,
                self.file or "", self.line or 0, self.target or "")

    def to_json(self) -> dict:
        return {
            "rule_id": self.rule_id, "kind": self.kind, "severity": self.severity,
            "file": self.file, "line": self.line, "unit": self.unit,
            "target": self.target, "message": self.message, "remedy": self.remedy,
            "exemptible": self.exemptible, "spec_path": self.spec_path,
            "path_trace": self.path_trace,
        }


def make_finding(rule: Rule, *, file, message: Optional[str] = None,
                 line=None, unit=None, target=None, severity=None,
                 exemptible=False, spec_path=None, path_trace=None) -> Finding:
    return Finding(
        rule_id=rule.id, kind=rule.kind, severity=severity or rule.severity,
        file=posix(file) if file else file, line=line, unit=unit, target=target,
        message=message or rule.message, remedy=rule.remedy,
        exemptible=exemptible, spec_path=spec_path or rule.spec_path,
        path_trace=path_trace,
    )


def expired_message(rule: Rule, ex: Exemption) -> str:
    bits = [f"Exemption expired on {ex.expires.isoformat()}"]
    if ex.reason:
        bits.append(f"reason: {ex.reason}")
    if ex.ticket:
        bits.append(f"ticket: {ex.ticket}")
    return f"{rule.message} [{'; '.join(bits)}]"


class Scope:
    """Rule scope: roots, path globs (paths/include/exclude), cross-root pair."""

    def __init__(self, raw: dict):
        raw = raw or {}
        self.roots = raw.get("roots")
        self.paths = list(raw.get("paths") or []) + list(raw.get("include") or [])
        self.exclude = list(raw.get("exclude") or [])
        self.from_root = raw.get("from_root")
        self.to_root = raw.get("to_root")
        self._inc = [glob_to_regex(p) for p in self.paths]
        self._exc = [glob_to_regex(p) for p in self.exclude]

    def file_matches(self, f: FileFacts) -> bool:
        if self.roots is not None and f.root not in self.roots:
            return False
        if self.from_root is not None and f.root != self.from_root:
            return False
        if self._inc and not any(rx.match(f.path) for rx in self._inc):
            return False
        if any(rx.match(f.path) for rx in self._exc):
            return False
        return True

    def path_matches(self, path: str, root: Optional[str] = None) -> bool:
        if self.roots is not None and root is not None and root not in self.roots:
            return False
        if self._inc and not any(rx.match(posix(path)) for rx in self._inc):
            return False
        if any(rx.match(posix(path)) for rx in self._exc):
            return False
        return True

    def dep_matches(self, src: FileFacts, target: Optional[FileFacts]) -> bool:
        if self.to_root is not None:
            return target is not None and target.root == self.to_root
        return True


class ExemptionSet:
    """Exemption semantics shared by every rule kind (spec §3.5):
    active entries suppress, expired entries escalate to severity error."""

    def __init__(self, entries: list[Exemption], today: date):
        self.entries = entries
        self.today = today
        self.consumed: set[int] = set()

    @classmethod
    def parse(cls, raw_list, spec_path_prefix: str, today: date,
              target_key: str = "target") -> "ExemptionSet":
        entries = [Exemption.parse(raw, f"{spec_path_prefix}[{i}]", target_key)
                   for i, raw in enumerate(raw_list or [])]
        return cls(entries, today)

    def _matches(self, ex: Exemption, target: Optional[str], path: Optional[str],
                 path_target: bool) -> bool:
        if ex.target is None and ex.scope is None:
            # An exemption that names neither a target nor a scope constrains
            # nothing.  Treating it as matching would exempt everything and
            # turn the rule off without a word; it never matches instead, so
            # it surfaces as an unconsumed (dead) entry.
            return False
        if ex.target is not None:
            probe = path if path_target else target
            if probe is None:
                return False
            if path_target or "*" in ex.target:
                if path_target:
                    if not glob_to_regex(ex.target).match(posix(probe)):
                        return False
                elif not fqcn_match(ex.target, probe):
                    return False
            elif ex.target != probe:
                return False
        if ex.scope is not None:
            if path is None or not glob_to_regex(ex.scope).match(posix(path)):
                return False
        return True

    def check(self, target: Optional[str] = None, path: Optional[str] = None,
              path_target: bool = False):
        """Returns ('none'|'active'|'expired', Exemption|None)."""
        for i, ex in enumerate(self.entries):
            if self._matches(ex, target, path, path_target):
                self.consumed.add(i)
                return ex.status(self.today), ex
        return "none", None

    def unconsumed(self) -> list[Exemption]:
        return [ex for i, ex in enumerate(self.entries) if i not in self.consumed]


class FileTree:
    """Minimal filesystem view for structure/pattern rules; tests inject a fake."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)

    def exists(self, rel: str) -> bool:
        return (self.repo_root / rel).exists()

    def is_dir(self, rel: str) -> bool:
        return (self.repo_root / rel).is_dir()

    def read_text(self, rel: str) -> Optional[str]:
        p = self.repo_root / rel
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def walk_dirs(self, prefix: str) -> list[str]:
        base = self.repo_root / prefix if prefix else self.repo_root
        if not base.is_dir():
            return []
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_dir():
                out.append(posix(str(p.relative_to(self.repo_root))))
        return sorted(out)


@dataclass
class RuleContext:
    spec: Spec
    index: FactsIndex
    today: date
    tree: FileTree
    repo_root: Path = field(default_factory=lambda: Path("."))

    def owner_of(self, artifact_set: str, target: str) -> Optional[str]:
        for r in self.spec.rules:
            if r.kind == "ownership" and r.params.get("artifact_set") == artifact_set:
                owner = (r.params.get("owners") or {}).get(target)
                if owner:
                    return owner
        return None

    def artifact_members(self, set_name: str) -> dict[str, FileFacts]:
        """identity -> declaring file, per the artifact set's discovery pattern."""
        aset = self.spec.artifact_sets.get(set_name)
        if aset is None:
            return {}
        rx = glob_to_regex(aset.discovery.pattern)
        out: dict[str, FileFacts] = {}
        for f in self.index.files:
            if aset.discovery.root is not None and f.root != aset.discovery.root:
                continue
            if not rx.match(f.path):
                continue
            if aset.identity == "path":
                out[f.path] = f
            else:
                for name in sorted(f.declares):
                    out.setdefault(name, f)
        return out
