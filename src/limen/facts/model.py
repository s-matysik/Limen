"""arch-facts/1 data model and the merged, engine-facing index."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..util import posix

FACTS_SCHEMA = "arch-facts/1"


@dataclass
class Dep:
    target: str
    line: int
    via: str  # use | import | require | extends | implements | annotation | text

    def to_json(self) -> dict:
        return {"target": self.target, "line": self.line, "via": self.via}


@dataclass
class Unresolved:
    path: str
    line: int
    raw: str
    reason: str

    def to_json(self) -> dict:
        return {"path": self.path, "line": self.line, "raw": self.raw, "reason": self.reason}


@dataclass
class FileFacts:
    path: str                       # repo-relative, '/' separated
    kind: str                       # class | interface | trait | enum | module | file
    declares: list[str]
    lines: int
    deps: list[Dep]
    root: str = ""                  # root name (not serialized per-file; document-level)
    language: str = ""
    unit: Optional[str] = None      # resolved by the engine, not the extractor
    layer: Optional[str] = None
    unit_dir: Optional[str] = None  # path prefix of the owning unit directory

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "unit": self.unit,
            "layer": self.layer,
            "kind": self.kind,
            "declares": sorted(self.declares),
            "lines": self.lines,
            "deps": [d.to_json() for d in sorted(self.deps, key=lambda d: (d.line, d.target, d.via))],
        }


@dataclass
class FactsDocument:
    generated_by: str
    root: str
    language: str
    root_path: str
    files: list[FileFacts] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)

    def sort(self) -> "FactsDocument":
        self.files.sort(key=lambda f: f.path)
        self.unresolved.sort(key=lambda u: (u.path, u.line, u.raw))
        return self

    def to_json(self) -> dict:
        self.sort()
        return {
            "schema": FACTS_SCHEMA,
            "generated_by": self.generated_by,
            "root": self.root,
            "language": self.language,
            "root_path": self.root_path,
            "files": [f.to_json() for f in self.files],
            "unresolved": [u.to_json() for u in self.unresolved],
        }

    @classmethod
    def from_json(cls, data: dict) -> "FactsDocument":
        doc = cls(
            generated_by=data.get("generated_by", "unknown"),
            root=data.get("root", ""),
            language=data.get("language", ""),
            root_path=data.get("root_path", ""),
        )
        for f in data.get("files", []):
            doc.files.append(FileFacts(
                path=posix(f["path"]),
                kind=f.get("kind", "file"),
                declares=list(f.get("declares", [])),
                lines=int(f.get("lines", 0)),
                deps=[Dep(d["target"], int(d["line"]), d.get("via", "import"))
                      for d in f.get("deps", [])],
                root=doc.root,
                language=doc.language,
                unit=f.get("unit"),
                layer=f.get("layer"),
            ))
        for u in data.get("unresolved", []):
            doc.unresolved.append(Unresolved(posix(u["path"]), int(u["line"]),
                                             u["raw"], u.get("reason", "dynamic")))
        return doc.sort()


class FactsIndex:
    """Merged view over all roots; the only structure rules ever consume."""

    def __init__(self, documents: Iterable[FactsDocument]):
        self.documents: list[FactsDocument] = sorted(documents, key=lambda d: d.root)
        self.files: list[FileFacts] = []
        self.by_path: dict[str, FileFacts] = {}
        self.by_declared: dict[str, FileFacts] = {}
        for doc in self.documents:
            for f in doc.files:
                f.root = doc.root
                f.language = doc.language
                self.files.append(f)
                self.by_path[f.path] = f
                for name in f.declares:
                    self.by_declared.setdefault(name, f)
        self.files.sort(key=lambda f: f.path)

    @property
    def unresolved(self) -> list[Unresolved]:
        out: list[Unresolved] = []
        for doc in self.documents:
            out.extend(doc.unresolved)
        return sorted(out, key=lambda u: (u.path, u.line, u.raw))

    def units(self) -> list[str]:
        return sorted({f.unit for f in self.files if f.unit})

    def resolve_target(self, dep_target: str) -> Optional[FileFacts]:
        """A dependency target is either a declared symbol (PHP FQCN, Python module)
        or an already path-resolved repo-relative file (JS)."""
        hit = self.by_declared.get(dep_target)
        if hit is not None:
            return hit
        return self.by_path.get(posix(dep_target))
