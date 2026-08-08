"""``limen review`` — the checklist for a verifying (read-only) agent.

From a git diff it derives: the rules that could have been violated, the
engine's own result on the changed files, and the paired-declaration reminders.
The output is a report, not a verdict — the decision stays with the agent or
the human.
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

from .context import _classify_paths, _rule_applies
from .engine import run_check
from .facts.model import FactsIndex
from .report import human
from .spec.model import Spec
from .util import posix


def changed_files(rev_range: str, repo_root: str = ".") -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", rev_range],
            cwd=repo_root, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(f"cannot compute diff for {rev_range!r}: {exc}") from exc
    return sorted({posix(line.strip()) for line in out.stdout.splitlines()
                   if line.strip()})


def generate_review(spec: Spec, index: FactsIndex, paths: list[str],
                    today: Optional[date] = None,
                    repo_root: str = ".",
                    rev_range: str = "HEAD") -> str:
    today = today or date.today()
    report = run_check(spec, index, repo_root=repo_root, today=today,
                       changed_paths=set(paths))
    infos = _classify_paths(spec, index, paths)

    out = [f"## Review checklist — diff {rev_range}",
           "",
           "This is a report, not a verdict.",
           "",
           "### Changed files"]
    out.extend(f"- `{p}`" for p in paths) if paths else out.append("(none)")
    out.append("")
    out.append("### Rules in scope of this diff")
    any_rule = False
    for rule in spec.rules:
        cov = _rule_applies(rule, infos, spec)
        if cov:
            any_rule = True
            out.append(f"- [{rule.id}] ({rule.severity}, touches {cov} "
                       f"file(s)) {rule.message}")
    if not any_rule:
        out.append("(none)")
    out.append("")
    out.append("### Paired declarations to verify manually")
    any_paired = False
    for rule in spec.rules:
        paired = (rule.params.get("assert") or {}).get("paired_declaration") \
            if rule.kind == "structure" else None
        if paired:
            any_paired = True
            out.append(f"- Rule `{paired.get('source_rule')}` exceptions must "
                       f"match `{paired.get('must_appear_in')}`; confirm both "
                       f"sides changed together.")
    if not any_paired:
        out.append("(none)")
    out.append("")
    out.append("### `limen check` on the changed files")
    out.append("")
    out.append("```")
    out.append(human.render(report).rstrip("\n"))
    out.append("```")
    return "\n".join(out) + "\n"
