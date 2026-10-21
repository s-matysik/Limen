"""`from . import mod` and `from pkg import mod` create an edge to the submodule."""
from limen.extract.python import PythonRegexExtractor, _imported_names


def test_imported_names_forms():
    t = "from . import (\n    hmc,\n    nuts as n,\n)\nx = 1\n"
    assert _imported_names(t, t.find("import") + 6) == ["hmc", "nuts"]
    t2 = "from . import a, b \\\n    , c\n"
    assert _imported_names(t2, t2.find("import") + 6) == ["a", "b", "c"]


def _deps(files, path):
    from limen.spec.model import RootSpec
    ex = PythonRegexExtractor()
    root = RootSpec(name="repo", path=".", language="python")
    resolver = ex.make_resolver(root, set(files))
    ff, _ = ex.extract_file(path, files[path], resolver)
    return {d.target for d in ff.deps}


def test_relative_submodule_import_in_init():
    files = {"pkg/__init__.py": "from . import hmc\nfrom .nuts import NUTS\n",
             "pkg/hmc.py": "", "pkg/nuts.py": "", "pkg/util.py": ""}
    deps = _deps(files, "pkg/__init__.py")
    assert "pkg.hmc" in deps and "pkg.nuts" in deps and "pkg.util" not in deps


def test_absolute_submodule_import():
    files = {"pkg/api.py": "from pkg.mcmc import (\n    hmc,\n    helper,\n)\n",
             "pkg/__init__.py": "", "pkg/mcmc/__init__.py": "", "pkg/mcmc/hmc.py": ""}
    deps = _deps(files, "pkg/api.py")
    assert "pkg.mcmc.hmc" in deps and "pkg.mcmc.helper" not in deps
