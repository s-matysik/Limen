"""Java regex extractor — enough of the language for architecture facts.

Declares: ``package a.b;`` + top-level ``class|interface|enum|record C`` →
FQCN ``a.b.C``. Dependencies: ``import a.b.C;`` (static imports keep the type:
``import static a.b.C.method`` → ``a.b.C``). Wildcard imports
(``import a.b.*;``) cannot be resolved to a concrete type and are reported as
``unresolved`` instead of being silently dropped — the same honesty rule the
PHP extractor applies to dynamic construction.
"""
from __future__ import annotations

import re
from typing import Callable

from ..facts.model import Dep, FileFacts, Unresolved
from .base import RegexExtractorBase
from .regex_engine import (compile_strip, find_unresolved, run_patterns,
                           strip_comments)

DEFAULT_JAVA_CONFIG = {
    "engine": "regex",
    "patterns": [
        {"via": "import",
         "regex": r"^\s*import\s+(?!static\s)([A-Za-z_][\w.]*[A-Za-z_0-9])\s*;",
         "group": 1},
        {"via": "import-static",
         "regex": r"^\s*import\s+static\s+([A-Za-z_][\w.]*)\.[A-Za-z_]\w*\s*;",
         "group": 1},
    ],
    "declares": {
        "namespace_regex": r"^\s*package\s+([\w.]+)\s*;",
        "symbol_regex": (r"^\s*(?:public\s+|final\s+|abstract\s+|sealed\s+"
                         r"|non-sealed\s+|static\s+)*"
                         r"(class|interface|enum|record)\s+(\w+)"),
    },
    "strip": [
        {"name": "block_comment", "regex": r"/\*.*?\*/", "flags": ["DOTALL"]},
        {"name": "line_comment", "regex": r"//[^\n]*"},
    ],
    "unresolved": [
        {"regex": r"^\s*import\s+(?:static\s+)?[\w.]+\.\*\s*;",
         "reason": "wildcard-import"},
        {"regex": r"\bClass\.forName\s*\(", "reason": "dynamic"},
    ],
}


class JavaRegexExtractor(RegexExtractorBase):
    name = "java-regex"
    languages = {"java"}
    default_config = DEFAULT_JAVA_CONFIG

    def extract_file(self, rel_path: str, text: str, resolver: Callable):
        cfg = self.config
        stripped = strip_comments(text, compile_strip(cfg.get("strip", [])))

        ns_rx = re.compile(cfg["declares"]["namespace_regex"], re.MULTILINE)
        m = ns_rx.search(stripped)
        package = m.group(1) if m else ""

        sym_rx = re.compile(cfg["declares"]["symbol_regex"], re.MULTILINE)
        declares: list[str] = []
        kind = "file"
        for sm in sym_rx.finditer(stripped):
            kind = sm.group(1)
            name = sm.group(2)
            declares.append(f"{package}.{name}" if package else name)

        deps: list[Dep] = []
        seen: set[tuple[str, int, str]] = set()
        for match in run_patterns(stripped, cfg.get("patterns", [])):
            target = match.target
            key = (target, match.line, "import")
            if key in seen:
                continue
            seen.add(key)
            deps.append(Dep(target=target, line=match.line, via="import"))

        unresolved = [Unresolved(rel_path, line, raw, reason)
                      for line, raw, reason
                      in find_unresolved(stripped, cfg.get("unresolved", []))]

        ff = FileFacts(path=rel_path, kind=kind,
                       declares=sorted(set(declares)),
                       lines=text.count("\n") + (0 if text.endswith("\n")
                                                 or not text else 1),
                       deps=deps)
        return ff, unresolved
