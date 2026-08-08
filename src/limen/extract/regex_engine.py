"""Generic regex machinery shared by the language extractors.

``strip`` removes comments *before* matching — the legacy LitRev checkers do
not do this, so a ``use`` inside a comment counts as an import there; fixing
that silent false positive is a measured improvement. Stripping blanks the
comment characters with spaces, preserving every newline, so reported line
numbers stay exact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from re import error


@dataclass(frozen=True)
class Match:
    target: str
    line: int
    via: str


def compile_strip(strip_cfg: list[dict]) -> list[re.Pattern]:
    out = []
    for entry in strip_cfg or []:
        flags = re.DOTALL if "DOTALL" in (entry.get("flags") or []) else 0
        out.append(re.compile(entry["regex"], flags))
    return out


def strip_comments(text: str, patterns: list[re.Pattern]) -> str:
    def blank(m: re.Match) -> str:
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))

    for rx in patterns:
        text = rx.sub(blank, text)
    return text


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def run_patterns(text: str, patterns: list[dict]) -> list[Match]:
    """Apply configured dependency patterns; supports plain ``group``,
    ``split`` (comma lists, e.g. PHP ``implements A, B``) and PHP group-use
    via ``prefix_group`` + ``names_group``."""
    out: list[Match] = []
    for entry in patterns:
        flags = re.MULTILINE | (re.DOTALL if "DOTALL" in (entry.get("flags") or []) else 0)
        rx = re.compile(entry["regex"], flags)
        via = entry.get("via", "import")
        for m in rx.finditer(text):
            # anchor the line on the captured symbol, not on the match start:
            # a leading `\s*` under MULTILINE may swallow preceding blank lines
            anchor = entry.get("group", entry.get("prefix_group", 0)) or 0
            try:
                pos = m.start(anchor)
            except (IndexError, error):
                pos = m.start()
            if pos < 0:
                pos = m.start()
            line = line_of(text, pos)
            if "prefix_group" in entry and "names_group" in entry:
                prefix = m.group(entry["prefix_group"]).rstrip("\\")
                for name in m.group(entry["names_group"]).split(","):
                    name = _clean_symbol(name)
                    if name:
                        out.append(Match(f"{prefix}\\{name}", line, via))
            elif entry.get("split"):
                for name in m.group(entry.get("group", 1)).split(entry["split"]):
                    name = _clean_symbol(name)
                    if name:
                        out.append(Match(name, line, via))
            else:
                target = _clean_symbol(m.group(entry.get("group", 1)))
                if target:
                    out.append(Match(target, line, via))
    return out


def _clean_symbol(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^(?:function|const)\s+", "", raw)
    raw = re.sub(r"\s+as\s+\w+$", "", raw)
    return raw.strip().strip("\\")


def find_unresolved(text: str, detectors: list[dict]) -> list[tuple[int, str, str]]:
    """(line, raw_snippet, reason) for constructs the regex engine cannot follow."""
    out: list[tuple[int, str, str]] = []
    for entry in detectors or []:
        rx = re.compile(entry["regex"], re.MULTILINE)
        for m in rx.finditer(text):
            line = line_of(text, m.start())
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.start())
            snippet = text[start: end if end >= 0 else len(text)].strip()
            out.append((line, snippet[:160], entry.get("reason", "dynamic")))
    return out
