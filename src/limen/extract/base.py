"""Extractor plugin contract and the shared orchestration.

An extractor never sees the rules: it receives a root description plus the file
list and returns an ``arch-facts/1`` document. The contract test in the suite
asserts that the same plugin on the same input twice yields identical JSON.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol

from ..facts.cache import FactsCache
from ..facts.model import Dep, FactsDocument, FileFacts, Unresolved
from ..spec.model import RootSpec
from ..util import posix


class ExtractorFailure(Exception):
    """Fatal extractor error (CLI exit code 4)."""


class Extractor(Protocol):
    name: str
    languages: set[str]

    def extract(self, root: RootSpec, files: Iterable[Path]) -> FactsDocument: ...


EXCLUDED_DIRS = {".git", ".limen", "node_modules", "vendor", "__pycache__",
                 ".venv", "venv", "dist", "build", ".idea", ".vscode"}

SOURCE_EXTENSIONS = {
    "php": (".php",),
    "js": (".js", ".jsx", ".ts", ".tsx"),
    "python": (".py",),
    "java": (".java",),
}


def discover_files(repo_root: Path, root: RootSpec) -> list[str]:
    """Repo-relative source paths under the root, lexicographically sorted —
    the report must not depend on filesystem enumeration order."""
    base = Path(repo_root) / root.prefix
    if not base.is_dir():
        raise ExtractorFailure(f"root {root.name!r}: directory not found: {base}")
    exts = SOURCE_EXTENSIONS.get(root.language, ())
    out: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix not in exts:
            continue
        if any(part in EXCLUDED_DIRS for part in p.parts):
            continue
        out.append(posix(str(p.relative_to(repo_root))))
    return sorted(out)


class RegexExtractorBase:
    """Common per-file orchestration with a content-hash cache.

    Subclasses implement ``extract_file(rel_path, text, resolver)`` returning
    ``(FileFacts, list[Unresolved])`` and provide ``default_config``.
    """

    name = "regex"
    languages: set[str] = set()
    default_config: dict = {}

    def __init__(self, config: Optional[dict] = None):
        self.config = _deep_merge(self.default_config, config or {})

    # -- plugin contract ---------------------------------------------------- #
    def extract(self, root: RootSpec, files: Iterable[Path],
                repo_root: Optional[Path] = None,
                cache: Optional[FactsCache] = None) -> FactsDocument:
        repo_root = Path(repo_root or ".")
        rel_files = sorted(posix(str(Path(f))) for f in files)
        file_set = set(rel_files)
        resolver = self.make_resolver(root, file_set)
        doc = FactsDocument(
            generated_by=f"limen-extract-{self.name}/0.1.0",
            root=root.name, language=root.language, root_path=root.prefix,
        )
        import json as _json
        cfg_fp = _json.dumps(self.config, sort_keys=True).encode()
        for rel in rel_files:
            abspath = repo_root / rel
            try:
                data = abspath.read_bytes()
            except OSError as exc:
                raise ExtractorFailure(f"cannot read {rel}: {exc}") from exc
            digest = FactsCache.content_hash(data + b"\x00" + cfg_fp)
            cached = cache.get(rel, digest) if cache else None
            if cached is not None:
                facts_json, unresolved_json = cached
                ff = FileFacts(
                    path=rel, kind=facts_json["kind"],
                    declares=list(facts_json["declares"]),
                    lines=facts_json["lines"],
                    deps=[Dep(d["target"], d["line"], d["via"])
                          for d in facts_json["deps"]],
                )
                unres = [Unresolved(u["path"], u["line"], u["raw"], u["reason"])
                         for u in unresolved_json]
            else:
                text = data.decode("utf-8", errors="replace")
                ff, unres = self.extract_file(rel, text, resolver)
                if cache:
                    cache.put(rel, digest, ff.to_json(), [u.to_json() for u in unres])
            doc.files.append(ff)
            doc.unresolved.extend(unres)
        return doc.sort()

    # -- hooks --------------------------------------------------------------- #
    def make_resolver(self, root: RootSpec, file_set: set[str]) -> Callable:
        return lambda spec_path: None

    def extract_file(self, rel_path: str, text: str, resolver: Callable):
        raise NotImplementedError


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


REGISTRY: dict[str, Callable[..., RegexExtractorBase]] = {}


def get_extractor(language: str, config: Optional[dict] = None) -> RegexExtractorBase:
    """Built-in registry plus ``limen.extractors`` entry points."""
    if not REGISTRY:
        from .java import JavaRegexExtractor
        from .js import JsRegexExtractor
        from .php import PhpRegexExtractor
        from .python import PythonRegexExtractor
        for cls in (PhpRegexExtractor, JsRegexExtractor, PythonRegexExtractor,
                    JavaRegexExtractor):
            for lang in cls.languages:
                REGISTRY[lang] = cls
        try:
            from importlib.metadata import entry_points
            for ep in entry_points(group="limen.extractors"):
                cls = ep.load()
                for lang in getattr(cls, "languages", set()):
                    REGISTRY.setdefault(lang, cls)
        except Exception:
            pass
    engine = (config or {}).get("engine", "regex")
    if engine == "treesitter":
        from .treesitter import get_treesitter_extractor
        return get_treesitter_extractor(language, config)
    try:
        cls = REGISTRY[language]
    except KeyError as exc:
        raise ExtractorFailure(f"no extractor registered for language {language!r}") from exc
    return cls(config)
