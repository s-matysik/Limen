"""Extractor tests: the four PHP pitfalls, JS resolution, Python, and the
plugin contract (same input twice -> identical JSON)."""
import json

from limen.extract.js import JsRegexExtractor
from limen.extract.php import PhpRegexExtractor
from limen.extract.python import PythonRegexExtractor
from limen.spec.model import RootSpec

PHP_ROOT = RootSpec(name="host", path="app", language="php",
                    namespace="App\\")
JS_ROOT = RootSpec(name="frontend", path="src", language="js")
PY_ROOT = RootSpec(name="pkg", path="src", language="python")


def php_extract(text, rel="app/Sample.php"):
    ex = PhpRegexExtractor()
    return ex.extract_file(rel, text, ex.make_resolver(PHP_ROOT, set()))


# ---------------------------------------------------------------------- PHP

def test_php_plain_and_grouped_use():
    ff, unres = php_extract(
        "<?php\n"
        "namespace App\\Modules\\Billing;\n"
        "use App\\Models\\User;\n"
        "use App\\Support\\{Money, Clock as SystemClock};\n"
        "class Invoice {}\n")
    targets = sorted(d.target for d in ff.deps)
    assert targets == ["App\\Models\\User", "App\\Support\\Clock",
                       "App\\Support\\Money"]
    assert ff.declares == ["App\\Modules\\Billing\\Invoice"]
    assert unres == []


def test_php_use_inside_comment_not_counted():
    ff, _ = php_extract(
        "<?php\n"
        "// use App\\Models\\Secret;\n"
        "/* use App\\Models\\Hidden; */\n"
        "use App\\Models\\User;\n")
    assert [d.target for d in ff.deps] == ["App\\Models\\User"]
    assert ff.deps[0].line == 4  # stripping preserves line numbers


def test_php_extends_qualified_through_alias_map():
    ff, _ = php_extract(
        "<?php\n"
        "namespace App\\Modules\\Billing;\n"
        "use App\\Core\\BaseModel as Base;\n"
        "class Invoice extends Base implements \\App\\Contracts\\Api {}\n")
    by_via = {d.via: d.target for d in ff.deps}
    assert by_via["extends"] == "App\\Core\\BaseModel"
    assert by_via["implements"] == "App\\Contracts\\Api"


def test_php_extends_falls_back_to_own_namespace():
    ff, _ = php_extract(
        "<?php\nnamespace App\\Modules\\Billing;\n"
        "class Invoice extends AbstractInvoice {}\n")
    assert any(d.target == "App\\Modules\\Billing\\AbstractInvoice"
               and d.via == "extends" for d in ff.deps)


def test_php_dynamic_construction_is_unresolved():
    ff, unres = php_extract(
        "<?php\n$cls = 'App\\\\Models\\\\' . $name;\nnew $cls();\n")
    assert ff.deps == []
    assert len(unres) >= 1
    assert all(u.reason == "dynamic" for u in unres)


# ----------------------------------------------------------------------- JS

def js_extract(files: dict[str, str], rel: str):
    ex = JsRegexExtractor()
    resolver = ex.make_resolver(JS_ROOT, set(files))
    return ex.extract_file(rel, files[rel], resolver)


def test_js_relative_alias_and_external():
    files = {
        "src/pages/Home.tsx":
            'import Button from "../components/Button";\n'
            'import { api } from "@/lib/api";\n'
            'import React from "react";\n',
        "src/components/Button.tsx": "",
        "src/lib/api.ts": "",
    }
    ff, unres = js_extract(files, "src/pages/Home.tsx")
    targets = sorted(d.target for d in ff.deps)
    assert targets == ["react", "src/components/Button.tsx", "src/lib/api.ts"]
    assert unres == []


def test_js_multiline_import_and_dynamic():
    files = {
        "src/a.ts":
            "import {\n  one,\n  two,\n} from './b';\n"
            "const m = await import(variable);\n",
        "src/b.ts": "",
    }
    ff, unres = js_extract(files, "src/a.ts")
    assert [d.target for d in ff.deps] == ["src/b.ts"]
    assert ff.deps[0].line == 4  # the `from` line
    assert len(unres) == 1 and unres[0].reason == "dynamic"


def test_js_missing_path_is_unresolved_not_silent():
    files = {"src/a.ts": "import x from './nope';\n"}
    ff, unres = js_extract(files, "src/a.ts")
    assert ff.deps == []
    assert len(unres) == 1 and unres[0].reason == "unresolved-path"
    assert "src/nope" in unres[0].raw or unres[0].raw == "./nope"


def test_js_index_file_resolution():
    files = {"src/a.ts": "import w from './widgets';\n",
             "src/widgets/index.ts": ""}
    ff, _ = js_extract(files, "src/a.ts")
    assert [d.target for d in ff.deps] == ["src/widgets/index.ts"]


# ------------------------------------------------------------------- Python

def test_python_imports():
    ex = PythonRegexExtractor()
    resolver = ex.make_resolver(PY_ROOT, {"src/pkg/a.py", "src/pkg/b.py"})
    ff, unres = ex.extract_file(
        "src/pkg/a.py",
        "import os\nfrom pkg.b import thing\nfrom . import sibling\n",
        resolver)
    targets = sorted(d.target for d in ff.deps)
    assert "os" in targets and any("pkg.b" in t or "b" in t for t in targets)


# ---------------------------------------------------------------- contract

def test_contract_same_input_twice_identical_json(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "A.php").write_text(
        "<?php\nnamespace App;\nuse App\\B;\nclass A {}\n")
    (tmp_path / "app" / "B.php").write_text(
        "<?php\nnamespace App;\nclass B {}\n")
    ex = PhpRegexExtractor()
    files = ["app/A.php", "app/B.php"]
    d1 = ex.extract(PHP_ROOT, files, repo_root=tmp_path)
    d2 = ex.extract(PHP_ROOT, files, repo_root=tmp_path)
    j1 = json.dumps(d1.to_json(), sort_keys=True)
    j2 = json.dumps(d2.to_json(), sort_keys=True)
    assert j1 == j2
