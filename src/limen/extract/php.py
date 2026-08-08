"""PHP regex extractor.

Handles the four pitfalls named in the intake manifest: ``use`` inside block
and line comments (stripped), grouped ``use A\\{B, C as D};``, aliased imports,
and dynamic class construction (reported as ``unresolved``, never hidden).
Short names in ``extends``/``implements`` are qualified through the file's
use-alias map, falling back to the file's own namespace.
"""
from __future__ import annotations

import re
from typing import Callable

from ..facts.model import Dep, FileFacts, Unresolved
from .base import RegexExtractorBase
from .regex_engine import (compile_strip, find_unresolved, run_patterns,
                           strip_comments)

DEFAULT_PHP_CONFIG = {
    "engine": "regex",
    "patterns": [
        {"via": "use",
         "regex": r"^\s*use\s+(?:function\s+|const\s+)?([A-Za-z_][A-Za-z0-9_\\]*)\s*(?:as\s+\w+\s*)?;",
         "group": 1},
        {"via": "use",
         "regex": r"^\s*use\s+([A-Za-z_][A-Za-z0-9_\\]*\\)\{([^}]+)\}\s*;",
         "prefix_group": 1, "names_group": 2},
        {"via": "extends",
         "regex": r"\bextends\s+([A-Za-z_][A-Za-z0-9_\\]*)",
         "group": 1},
        {"via": "implements",
         "regex": r"\bimplements\s+([A-Za-z0-9_\\,\s]+?)(?=\s*\{)",
         "group": 1, "split": ","},
    ],
    "declares": {
        "namespace_regex": r"^\s*namespace\s+([^;]+);",
        "symbol_regex": r"^\s*(?:final\s+|abstract\s+|readonly\s+)*(class|interface|trait|enum)\s+(\w+)",
    },
    "strip": [
        {"name": "block_comment", "regex": r"/\*.*?\*/", "flags": ["DOTALL"]},
        {"name": "line_comment", "regex": r"//[^\n]*|#[^\n]*"},
    ],
    "unresolved": [
        {"regex": r"\bnew\s+\$\w+", "reason": "dynamic"},
        {"regex": r"['\"](?:[A-Za-z_][A-Za-z0-9_]*\\\\)+['\"]?\s*\.\s*\$", "reason": "dynamic"},
        {"regex": r"\bclass_exists\s*\(\s*\$", "reason": "dynamic"},
    ],
}


class PhpRegexExtractor(RegexExtractorBase):
    name = "php-regex"
    languages = {"php"}
    default_config = DEFAULT_PHP_CONFIG

    def extract_file(self, rel_path: str, text: str, resolver: Callable):
        cfg = self.config
        stripped = strip_comments(text, compile_strip(cfg.get("strip", [])))

        ns_rx = re.compile(cfg["declares"]["namespace_regex"], re.MULTILINE)
        sym_rx = re.compile(cfg["declares"]["symbol_regex"], re.MULTILINE)
        ns_match = ns_rx.search(stripped)
        namespace = ns_match.group(1).strip() if ns_match else None

        declares: list[str] = []
        kind = "file"
        for m in sym_rx.finditer(stripped):
            kind = m.group(1)
            name = m.group(2)
            declares.append(f"{namespace}\\{name}" if namespace else name)

        matches = run_patterns(stripped, cfg.get("patterns", []))

        alias_map: dict[str, str] = {}
        for m in matches:
            if m.via == "use":
                alias_map.setdefault(m.target.rsplit("\\", 1)[-1], m.target)
        # explicit aliases: use Foo\Bar as Baz;
        for am in re.finditer(
                r"^\s*use\s+([A-Za-z_][A-Za-z0-9_\\]*)\s+as\s+(\w+)\s*;",
                stripped, re.MULTILINE):
            alias_map[am.group(2)] = am.group(1)

        deps: list[Dep] = []
        for m in matches:
            target = m.target
            if m.via in ("extends", "implements") and "\\" not in target:
                if target in alias_map:
                    target = alias_map[target]
                elif namespace:
                    target = f"{namespace}\\{target}"
            deps.append(Dep(target=target, line=m.line, via=m.via))

        unresolved = [Unresolved(rel_path, line, raw, reason)
                      for line, raw, reason in
                      find_unresolved(stripped, cfg.get("unresolved", []))]

        ff = FileFacts(path=rel_path, kind=kind, declares=declares,
                       lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
                       deps=deps)
        return ff, unresolved
