from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
import yaml  # noqa: E402

from limen.facts.model import Dep, FactsDocument, FactsIndex, FileFacts  # noqa: E402
from limen.rules.base import FileTree, RuleContext  # noqa: E402
from limen.spec.loader import build_spec  # noqa: E402

TODAY = date(2026, 8, 4)


# --------------------------------------------------------------- git capability
# Three integration tests build a throw-away repository with `git init` to
# exercise the --for-diff / --changed-only code paths. Some sandboxed and
# hardened environments (e.g. agent sandboxes that write-protect .git, or
# machines without git at all) refuse to create .git, which makes those tests
# error out in their *fixture* rather than in limen. The probe below detects
# that once per session so the tests skip cleanly there and still run for
# everyone with a working git. The same code paths are covered without git by
# tests/test_diff_paths.py, which stubs the `git` executable.

_GIT_INIT_WORKS: bool | None = None


def git_init_works() -> bool:
    """True when `git init` can actually create a repository here."""
    global _GIT_INIT_WORKS
    if _GIT_INIT_WORKS is None:
        import shutil as _shutil
        import subprocess as _subprocess
        import tempfile as _tempfile
        ok = False
        if _shutil.which("git"):
            with _tempfile.TemporaryDirectory() as tmp:
                proc = _subprocess.run(["git", "init", "-q", tmp],
                                       capture_output=True, text=True)
                ok = proc.returncode == 0 and Path(tmp, ".git").exists()
        _GIT_INIT_WORKS = ok
    return _GIT_INIT_WORKS


requires_git = pytest.mark.skipif(
    not git_init_works(),
    reason="`git init` unavailable here (missing git or a sandbox that "
           "write-protects .git); diff paths are covered by test_diff_paths.py")


def ff(path, declares=(), deps=(), unit=None, layer=None, unit_dir=None,
       lines=10, kind="class", language="php"):
    return FileFacts(path=path, kind=kind, declares=list(declares),
                     lines=lines,
                     deps=[Dep(t, ln, via) for (t, ln, via) in deps],
                     unit=unit, layer=layer, unit_dir=unit_dir,
                     language=language)


def make_index(files, root="host", language="php", unresolved=()):
    doc = FactsDocument(generated_by="test", root=root, language=language,
                        root_path="app")
    doc.files = list(files)
    doc.unresolved = list(unresolved)
    return FactsIndex([doc])


class FakeTree:
    """In-memory tree for structure/pattern rules."""

    def __init__(self, files: dict[str, str] | None = None,
                 dirs: list[str] | None = None):
        self.files = files or {}
        self.dirs = set(dirs or [])
        for path in self.files:
            parts = path.split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/".join(parts[:i]))

    def exists(self, rel):
        return rel in self.files or rel in self.dirs

    def is_dir(self, rel):
        return rel in self.dirs

    def read_text(self, rel):
        return self.files.get(rel)

    def walk_dirs(self, prefix):
        prefix = prefix.rstrip("/")
        return sorted(d for d in self.dirs
                      if not prefix or d == prefix or d.startswith(prefix + "/"))


def spec_from(text: str):
    return build_spec(yaml.safe_load(text))


def make_ctx(spec, index, tree=None, today=TODAY):
    return RuleContext(spec=spec, index=index, today=today,
                       tree=tree or FakeTree())


@pytest.fixture
def frozen_today():
    return TODAY
