"""A root declared at repository level (`path: .`) is usable end to end.

Before this was supported, `path: .` produced `prefix == "."` and the Python
resolver asserted on every file, because `discover_files` hands out bare
repo-relative paths (`pkg/mod.py`), never `./pkg/mod.py`.  The only declarable
alternative was `path: <package>`, which strips the package prefix from every
module name — so a package whose own modules import it absolutely
(`from pkg.sub import X`, the dominant style in scientific Python) had its
internal edges classified as external.

Three consumers have to agree on the normalisation, hence three tests: the
spec model that produces the prefix, the Python resolver that strips it, and
the context generator that matches paths against it.
"""

from limen.extract.python import PythonRegexExtractor
from limen.spec.model import RootSpec


FILES = {
    "pkg/__init__.py": "from .sub import helper\n",
    "pkg/sub/__init__.py": "from .helper import helper\n",
    "pkg/sub/helper.py": "from pkg.base import Thing\n",
    "pkg/base.py": "class Thing: pass\n",
}


def test_dot_path_normalises_to_an_empty_prefix():
    assert RootSpec(name="r", path=".", language="python").prefix == ""
    assert RootSpec(name="r", path="./", language="python").prefix == ""
    assert RootSpec(name="r", path="pkg", language="python").prefix == "pkg"


def test_dot_path_with_a_repo_segment_does_not_keep_a_trailing_slash():
    r = RootSpec(name="r", path=".", language="python", repo="sub")
    assert r.prefix == "sub"


def test_module_names_stay_fully_qualified_under_a_repo_level_root():
    root = RootSpec(name="pkg", path=".", language="python")
    ex = PythonRegexExtractor()
    resolver = ex.make_resolver(root, set(FILES))
    assert resolver.module_of("pkg/base.py") == "pkg.base"
    assert resolver.module_of("pkg/sub/__init__.py") == "pkg.sub"


def test_absolute_self_import_resolves_inside_the_root():
    root = RootSpec(name="pkg", path=".", language="python")
    ex = PythonRegexExtractor()
    resolver = ex.make_resolver(root, set(FILES))
    edges = set()
    for rel, text in FILES.items():
        facts, _unresolved = ex.extract_file(rel, text, resolver)
        for dep in facts.deps:
            edges.add((resolver.module_of(rel), dep.target))
    # `pkg/sub/helper.py` does `from pkg.base import Thing`
    assert ("pkg.sub.helper", "pkg.base") in edges
    # and the relative imports still land in their own package
    assert ("pkg.sub", "pkg.sub.helper") in edges
