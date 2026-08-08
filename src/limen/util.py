"""Shared helpers: deterministic path/pattern handling, dates, token estimate.

Everything here is deterministic: no wall-clock, no filesystem-order dependence.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_PLACEHOLDERS = ("unit", "layer")


def posix(path: str) -> str:
    """Normalize a path to forward slashes (repo-relative, any OS)."""
    return path.replace("\\", "/")


def glob_to_regex(pattern: str, capture: tuple[str, ...] = _PLACEHOLDERS) -> re.Pattern:
    """Compile a path glob supporting **, *, ?, {a,b} alternation and
    {unit}/{layer} named captures. Path separator is always '/'."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "{":
            j = pattern.find("}", i)
            if j < 0:
                out.append(re.escape(pattern[i]))
                i += 1
                continue
            inner = pattern[i + 1 : j]
            if inner in capture:
                out.append(f"(?P<{inner}>[^/]+)")
            else:
                alts = "|".join(re.escape(x) for x in inner.split(","))
                out.append(f"(?:{alts})")
            i = j + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_match(pattern: str, path: str) -> bool:
    return glob_to_regex(pattern).match(posix(path)) is not None


def any_path_match(patterns: list[str], path: str) -> bool:
    return any(path_match(p, path) for p in patterns)


def fqcn_glob_to_regex(pattern: str) -> re.Pattern:
    """Glob over backslash-separated fully qualified names.
    '*' matches within one namespace segment, '**' crosses segments."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^\\]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def fqcn_match(pattern: str, name: str) -> bool:
    return fqcn_glob_to_regex(pattern).match(name) is not None


def parse_iso_date(value) -> date:
    """Strict ISO-8601 date (or datetime) parser; raises ValueError otherwise."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        raise ValueError(f"expires must be an ISO-8601 string, got {type(value).__name__}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value).date()


def token_estimate(text: str) -> int:
    """Deterministic, model-agnostic token estimate (~4 chars/token)."""
    return (len(text) + 3) // 4


def stable_sorted(items, key=None):
    return sorted(items, key=key)
