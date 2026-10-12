"""Python regex extractor: import/from-import at module granularity.

Relative imports (``from .sub import x``) are resolved against the file's
package; targets inside the root become absolute dotted module names, external
packages stay verbatim. Dynamic imports (``importlib.import_module`` /
``__import__`` with non-literal arguments) are reported as unresolved.
"""
from __future__ import annotations

import re
from typing import Callable

from ..facts.model import Dep, FileFacts, Unresolved
from ..spec.model import RootSpec
from .base import RegexExtractorBase
from .regex_engine import (compile_strip, find_unresolved, strip_comments)

DEFAULT_PY_CONFIG = {
    "engine": "regex",
    "strip": [
        {"name": "line_comment", "regex": r"#[^\n]*"},
    ],
    "unresolved": [
        {"regex": r"importlib\.import_module\s*\(\s*(?![\"'])", "reason": "dynamic"},
        {"regex": r"__import__\s*\(\s*(?![\"'])", "reason": "dynamic"},
    ],
}

# The leading indent must NOT be `\s*`: `\s` matches newlines, so with
# re.MULTILINE the match can start on an earlier blank line and the reported
# line number is then too small by the number of blank lines above the
# statement. Indentation is spaces and tabs only.
_IMPORT_RX = re.compile(r"^[ \t]*import\s+([\w\., ]+)", re.MULTILINE)
_FROM_RX = re.compile(r"^[ \t]*from\s+(\.*)([\w\.]*)\s+import\b", re.MULTILINE)


class PythonRegexExtractor(RegexExtractorBase):
    name = "python-regex"
    languages = {"python"}
    default_config = DEFAULT_PY_CONFIG

    def make_resolver(self, root: RootSpec, file_set: set[str]) -> Callable:
        prefix = root.prefix.rstrip("/")

        def module_of(rel_path: str) -> str:
            assert rel_path.startswith(prefix + "/") or rel_path == prefix
            inner = rel_path[len(prefix) + 1:]
            if inner.endswith(".py"):
                inner = inner[:-3]
            if inner.endswith("/__init__"):
                inner = inner[: -len("/__init__")]
            return inner.replace("/", ".")

        modules = {module_of(p) for p in file_set}

        def resolve(dots: int, module: str, importer_rel: str):
            if dots == 0:
                top = module.split(".", 1)[0]
                internal = any(m == module or m.startswith(module + ".")
                               or module.startswith(m + ".") or m == top
                               for m in modules)
                return module, internal
            pkg_parts = module_of(importer_rel).split(".")
            # `__init__.py` IS the package, not a module inside it: `module_of`
            # already returns the package name for it, so subtracting `dots`
            # levels walks one step too far and `from .X import Y` landed in the
            # PARENT package.  Left unfixed this both drops real edges and emits
            # false ones (`pkg.sub -> pkg.base` instead of `pkg.sub.base`).
            up = dots - 1 if importer_rel.endswith("__init__.py") else dots
            base = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts
            abs_mod = ".".join(base + ([module] if module else [])).strip(".")
            return abs_mod, True

        resolve.module_of = module_of  # type: ignore[attr-defined]
        return resolve

    def extract_file(self, rel_path: str, text: str, resolver: Callable):
        cfg = self.config
        stripped = strip_comments(text, compile_strip(cfg.get("strip", [])))
        deps: list[Dep] = []
        for m in _IMPORT_RX.finditer(stripped):
            line = stripped.count("\n", 0, m.start()) + 1
            for part in m.group(1).split(","):
                mod = part.strip().split(" as ")[0].strip()
                if not mod:
                    continue
                target, _internal = resolver(0, mod, rel_path)
                deps.append(Dep(target=target, line=line, via="import"))
        for m in _FROM_RX.finditer(stripped):
            line = stripped.count("\n", 0, m.start()) + 1
            target, _internal = resolver(len(m.group(1)), m.group(2), rel_path)
            if target:
                deps.append(Dep(target=target, line=line, via="import"))

        unresolved = [Unresolved(rel_path, line, raw, reason)
                      for line, raw, reason in
                      find_unresolved(stripped, cfg.get("unresolved", []))]

        module_name = resolver.module_of(rel_path)  # type: ignore[attr-defined]
        ff = FileFacts(path=rel_path, kind="module",
                       declares=[module_name] if module_name else [],
                       lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
                       deps=deps)
        return ff, unresolved
