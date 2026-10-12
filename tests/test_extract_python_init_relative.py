"""Relative imports written inside `__init__.py` resolve to the package itself.

Regression guard for a resolver defect measured on three statistics libraries
(pgmpy, EconML, PyMC) before study 4.  `module_of` maps `pkg/sub/__init__.py` to
`pkg.sub`, so the generic "strip `dots` levels" rule walked one level too far for
init files only.  On pgmpy that dropped 131 real edges and invented 9 false ones
(for example `pgmpy.estimators -> pgmpy.base` in place of
`pgmpy.estimators.base`, and a self-loop `pgmpy.utils -> pgmpy.utils`).  False
edges are the worse half: a contract gate reports a violation that is not there.

With the fix, recall against an independent `ast` reference graph is 1.000 on
direct edges for all three libraries.
"""

from limen.extract.python import PythonRegexExtractor
from limen.spec.model import RootSpec


FILES = {
    "pkg/__init__.py": "from .base import Thing\nfrom .sub import helper\n",
    "pkg/base.py": "class Thing: pass\n",
    "pkg/sub/__init__.py": "from .helper import helper\nfrom ..base import Thing\n",
    "pkg/sub/helper.py": "def helper(): pass\n",
    "pkg/sub/deep/__init__.py": "from ...base import Thing\n",
    "pkg/consumer.py": "from .sub.helper import helper\n",
}


def _edges(root_path: str = "."):
    root = RootSpec(name="pkg", path=root_path, language="python")
    ex = PythonRegexExtractor()
    resolver = ex.make_resolver(root, set(FILES))
    out = set()
    for rel, text in FILES.items():
        facts, _unresolved = ex.extract_file(rel, text, resolver)
        for dep in facts.deps:
            out.add((resolver.module_of(rel), dep.target))
    return out


def test_single_dot_in_init_stays_inside_its_own_package():
    edges = _edges()
    # `pkg/__init__.py` does `from .base import Thing`
    assert ("pkg", "pkg.base") in edges
    # `pkg/sub/__init__.py` does `from .helper import helper`
    assert ("pkg.sub", "pkg.sub.helper") in edges


def test_init_does_not_leak_into_the_parent_package():
    edges = _edges()
    # the defect produced these: one level too high, and a self-loop
    assert ("pkg.sub", "pkg.helper") not in edges
    assert ("pkg", "base") not in edges
    assert ("pkg.sub", "pkg.sub") not in edges


def test_multi_dot_in_init_still_climbs_the_right_number_of_levels():
    edges = _edges()
    # `pkg/sub/__init__.py`  : `from ..base import Thing`  -> pkg.base
    assert ("pkg.sub", "pkg.base") in edges
    # `pkg/sub/deep/__init__.py` : `from ...base import Thing` -> pkg.base
    assert ("pkg.sub.deep", "pkg.base") in edges


def test_non_init_modules_are_unchanged():
    edges = _edges()
    # `pkg/consumer.py` does `from .sub.helper import helper`
    assert ("pkg.consumer", "pkg.sub.helper") in edges
