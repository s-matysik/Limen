"""JavaScript/TypeScript regex extractor.

Multiline ``import { a,\\n b } from 'x'`` is matched inherently (the pattern
keys on the ``from '...'`` clause). Aliases (``@/``, ``@common/``) and relative
specifiers are resolved against the discovered file set — deterministically,
with configured extension and index-file probing — into repo-relative paths,
which is what unit resolution and public-surface rules consume. A path-like
specifier that resolves to no file goes to ``unresolved``; a bare package
specifier stays as an external dependency target.
"""
from __future__ import annotations

import posixpath
from typing import Callable, Optional

from ..facts.model import Dep, FileFacts, Unresolved
from ..spec.model import RootSpec
from .base import RegexExtractorBase
from .regex_engine import (compile_strip, find_unresolved, run_patterns,
                           strip_comments)

DEFAULT_JS_CONFIG = {
    "engine": "regex",
    "patterns": [
        {"via": "import", "regex": r"""\bfrom\s+["']([^"']+)["']""", "group": 1},
        {"via": "import", "regex": r"""^\s*import\s+["']([^"']+)["']""", "group": 1},
        {"via": "import", "regex": r"""\bimport\s*\(\s*["']([^"']+)["']\s*\)""", "group": 1},
        {"via": "require", "regex": r"""\brequire\s*\(\s*["']([^"']+)["']\s*\)""", "group": 1},
    ],
    "strip": [
        {"name": "block_comment", "regex": r"/\*.*?\*/", "flags": ["DOTALL"]},
        {"name": "line_comment", "regex": r"//[^\n]*"},
    ],
    "resolve": {
        "alias": {"@/": "src/"},
        "extensions": [".js", ".jsx", ".ts", ".tsx"],
        "index_files": ["index.js", "index.jsx", "index.ts", "index.tsx"],
    },
    "unresolved": [
        {"regex": "\\bimport\\s*\\(\\s*(?![\"'])", "reason": "dynamic"},
        {"regex": "\\brequire\\s*\\(\\s*(?![\"'])", "reason": "dynamic"},
    ],
}


class JsRegexExtractor(RegexExtractorBase):
    name = "js-regex"
    languages = {"js"}
    default_config = DEFAULT_JS_CONFIG

    def make_resolver(self, root: RootSpec, file_set: set[str]) -> Callable:
        cfg = self.config.get("resolve", {})
        aliases = sorted((cfg.get("alias") or {}).items(),
                         key=lambda kv: -len(kv[0]))
        exts = cfg.get("extensions", [])
        indexes = cfg.get("index_files", [])
        prefix = root.prefix
        repo_prefix = f"{root.repo}/" if root.repo else ""

        def probe(candidate: str) -> Optional[str]:
            candidate = posixpath.normpath(candidate)
            if candidate in file_set:
                return candidate
            for ext in exts:
                if candidate + ext in file_set:
                    return candidate + ext
            for idx in indexes:
                if posixpath.join(candidate, idx) in file_set:
                    return posixpath.join(candidate, idx)
            return None

        def resolve(spec: str, importer_rel: str):
            """Returns (kind, value): ('file', repo_rel_path) | ('external', spec)
            | ('missing', attempted_path)."""
            for alias, replacement in aliases:
                if spec.startswith(alias):
                    attempted = repo_prefix + replacement + spec[len(alias):]
                    hit = probe(attempted)
                    return ("file", hit) if hit else ("missing", attempted)
            if spec.startswith("./") or spec.startswith("../"):
                base = posixpath.dirname(importer_rel)
                attempted = posixpath.normpath(posixpath.join(base, spec))
                hit = probe(attempted)
                return ("file", hit) if hit else ("missing", attempted)
            if spec.startswith("/"):
                attempted = prefix + spec
                hit = probe(attempted)
                return ("file", hit) if hit else ("missing", attempted)
            return ("external", spec)

        return resolve

    def extract_file(self, rel_path: str, text: str, resolver: Callable):
        cfg = self.config
        stripped = strip_comments(text, compile_strip(cfg.get("strip", [])))
        matches = run_patterns(stripped, cfg.get("patterns", []))

        deps: list[Dep] = []
        unresolved: list[Unresolved] = []
        seen: set[tuple[str, int, str]] = set()
        for m in matches:
            kind, value = resolver(m.target, rel_path)
            if kind == "missing":
                unresolved.append(Unresolved(rel_path, m.line, m.target,
                                             "unresolved-path"))
                continue
            key = (value, m.line, m.via)
            if key in seen:
                continue
            seen.add(key)
            deps.append(Dep(target=value, line=m.line, via=m.via))

        for line, raw, reason in find_unresolved(stripped, cfg.get("unresolved", [])):
            unresolved.append(Unresolved(rel_path, line, raw, reason))

        ff = FileFacts(path=rel_path, kind="module", declares=[rel_path],
                       lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
                       deps=deps)
        return ff, unresolved
