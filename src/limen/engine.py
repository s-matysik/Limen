"""The deterministic engine (spec §5.1).

Execution order: validate the spec (JSON Schema, then internal integrity) →
resolve unit/layer per file (ambiguity aborts with exit 3) → run rules in spec
order → sort findings by (severity, rule_id, file, line, target) → emit.
No wall clock inside; ``today`` is injected so exemption expiry is testable
with a frozen date and reports stay byte-identical across runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from . import (EXIT_ERRORS, EXIT_OK, EXIT_WARNINGS_STRICT)
from .facts.merge import resolve_units_layers
from .facts.model import FactsIndex
from .rules import DISPATCH, FULL_SCAN_KINDS
from .rules.base import (Finding, FileTree, RuleContext, SEVERITY_RANK)
from .spec.model import Spec, SpecError
from .spec.validate import validate_integrity
from .util import posix

STANDARD_UNRESOLVED_REMEDY = (
    "Make the dependency static, or extend the extractor patterns; "
    "an unresolved import is invisible to every rule.")


@dataclass
class Report:
    project: Optional[str]
    findings: list[Finding]
    unresolved_count: int
    spec_issues: list = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def exit_code(self, strict: bool = False) -> int:
        if self.errors:
            return EXIT_ERRORS
        if strict and self.warnings:
            return EXIT_WARNINGS_STRICT
        return EXIT_OK

    def summary(self, strict: bool = False) -> dict:
        return {
            "errors": self.errors,
            "warnings": self.warnings,
            "unresolved_count": self.unresolved_count,
            "exit_code": self.exit_code(strict),
        }


def run_check(spec: Spec,
              index: FactsIndex,
              repo_root: Path | str = ".",
              today: Optional[date] = None,
              changed_paths: Optional[set[str]] = None,
              only: Optional[set[str]] = None,
              strict_spec: bool = False,
              tree: Optional[FileTree] = None) -> Report:
    today = today or date.today()
    repo_root = Path(repo_root)

    # Step 4: unit/layer resolution — ambiguity is a spec error (exit 3).
    resolve_units_layers(spec, index)

    # Step 2: internal integrity with facts available (S4/S6 need them).
    issues = validate_integrity(spec, index=index, strict_spec=strict_spec,
                                today=today)
    hard = [i for i in issues if i.level == "error"]
    if hard:
        raise SpecError(hard)

    ctx = RuleContext(spec=spec, index=index, today=today,
                      tree=tree or FileTree(repo_root), repo_root=repo_root)

    findings: list[Finding] = []

    # Spec-integrity warnings surface in the report instead of vanishing.
    for issue in issues:
        findings.append(Finding(
            rule_id=f"spec-{issue.code}", kind="spec-integrity",
            severity="warning", file=None, message=issue.message,
            spec_path=issue.path,
            remedy="Fix or remove the entry; run `limen spec validate --strict-spec`."))

    # Step 5: rules in specification order.
    for rule in spec.rules:
        if only is not None and rule.id not in only:
            continue
        checker = DISPATCH.get(rule.kind)
        if checker is None:
            continue
        rule_findings = checker(rule, ctx)
        if changed_paths is not None and rule.kind not in FULL_SCAN_KINDS:
            rule_findings = [f for f in rule_findings
                            if f.file is None or f.file in changed_paths]
        findings.extend(rule_findings)

    # Unresolved-dependency policy (§3.4): never silent about blind spots.
    unresolved = index.unresolved
    for u in unresolved:
        root = index.by_path[u.path].root if u.path in index.by_path else ""
        policy = spec.unresolved_policy_for(root)
        if policy == "off":
            continue
        if changed_paths is not None and u.path not in changed_paths:
            continue
        findings.append(Finding(
            rule_id="unresolved-dependency", kind="unresolved",
            severity="error" if policy == "error" else "warning",
            file=posix(u.path), line=u.line, target=u.raw[:120],
            message=f"Dependency could not be resolved statically ({u.reason}).",
            remedy=STANDARD_UNRESOLVED_REMEDY,
            spec_path="unresolved_policy"))

    # Step 6: deterministic ordering.
    findings.sort(key=lambda f: f.sort_key())
    return Report(project=spec.project, findings=findings,
                  unresolved_count=len(unresolved), spec_issues=issues)


def severity_rank(sev: str) -> int:
    return SEVERITY_RANK.get(sev, 9)
