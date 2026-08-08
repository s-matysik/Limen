"""M9: stack-independence proof — the Java extractor, both non-Laravel
examples end-to-end (clean pass + injected violation), and `limen init`
generating a valid specification for the Django-style layout."""
import shutil
from pathlib import Path

from limen.cli import main
from limen.extract.java import JavaRegexExtractor
from limen.spec.model import RootSpec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
TODAY = ["--today", "2026-08-04"]


def run(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


# ------------------------------------------------------------ java extractor

JAVA_ROOT = RootSpec(name="main", path="src/main/java", language="java")


def java_extract(text, rel="src/main/java/com/example/a/A.java"):
    ex = JavaRegexExtractor()
    return ex.extract_file(rel, text, ex.make_resolver(JAVA_ROOT, set()))


def test_java_package_class_and_imports():
    ff, unres = java_extract(
        "package com.example.billing.domain;\n"
        "\n"
        "import com.example.shared.Money;\n"
        "import static com.example.shared.Asserts.check;\n"
        "\n"
        "public final class Invoice {}\n")
    assert ff.declares == ["com.example.billing.domain.Invoice"]
    targets = sorted(d.target for d in ff.deps)
    assert targets == ["com.example.shared.Asserts", "com.example.shared.Money"]
    assert all(d.via == "import" for d in ff.deps)
    assert unres == []


def test_java_import_in_comment_not_counted_and_line_exact():
    ff, _ = java_extract(
        "package a;\n"
        "// import a.Hidden;\n"
        "/* import a.AlsoHidden; */\n"
        "import a.Real;\n"
        "class C {}\n")
    assert [(d.target, d.line) for d in ff.deps] == [("a.Real", 4)]


def test_java_wildcard_import_is_unresolved():
    ff, unres = java_extract(
        "package a;\nimport java.util.*;\nimport a.b.C;\nclass X {}\n")
    assert [d.target for d in ff.deps] == ["a.b.C"]
    assert len(unres) == 1 and unres[0].reason == "wildcard-import"
    assert unres[0].line == 2


def test_java_record_and_interface_declared():
    ff, _ = java_extract(
        "package a;\npublic record Money(long cents) {}\n")
    assert ff.declares == ["a.Money"] and ff.kind == "record"
    ff, _ = java_extract("package a;\npublic interface Api {}\n")
    assert ff.kind == "interface"


def test_java_dynamic_class_forname_unresolved():
    _, unres = java_extract(
        'package a;\nclass X { Object o = Class.forName(name); }\n')
    assert any(u.reason == "dynamic" for u in unres)


# ------------------------------------------------- examples: clean + broken

def test_django_like_clean_and_violation(tmp_path, capsys):
    code, out, _ = run(["check", "--workspace",
                        str(EXAMPLES / "django-like"), *TODAY], capsys)
    assert code == 0, out

    work = tmp_path / "django"
    shutil.copytree(EXAMPLES / "django-like", work)
    victim = work / "apps" / "orders" / "services.py"
    victim.write_text(victim.read_text()
                      + "\nfrom accounts.models import User  # bypasses api\n")
    code, out, _ = run(["check", "--workspace", str(work),
                        "--format", "json", *TODAY], capsys)
    assert code == 1
    assert "orders-no-direct-account-models" in out
    assert "cross-app-via-api-only" in out


def test_django_like_reachability_violation(tmp_path, capsys):
    work = tmp_path / "django"
    shutil.copytree(EXAMPLES / "django-like", work)
    victim = work / "apps" / "catalog" / "api.py"
    victim.write_text(victim.read_text()
                      + "\nfrom accounts.api import get_user  # couples apps\n")
    code, out, _ = run(["check", "--workspace", str(work),
                        "--format", "json", *TODAY], capsys)
    assert code == 1
    assert "accounts-catalog-independent" in out
    assert "catalog -> accounts" in out  # path trace


def test_spring_like_clean_and_violations(tmp_path, capsys):
    code, out, _ = run(["check", "--workspace",
                        str(EXAMPLES / "spring-like"), *TODAY], capsys)
    assert code == 0, out

    work = tmp_path / "spring"
    shutil.copytree(EXAMPLES / "spring-like", work)
    base = work / "src" / "main" / "java" / "com" / "example"

    # domain -> infra (layer) and internals import (public api)
    victim = base / "billing" / "domain" / "Invoice.java"
    victim.write_text(victim.read_text().replace(
        "import com.example.shared.Money;",
        "import com.example.shared.Money;\n"
        "import com.example.billing.infra.InvoiceRepository;\n"
        "import com.example.catalog.domain.Product;"))
    code, out, _ = run(["check", "--workspace", str(work),
                        "--format", "json", *TODAY], capsys)
    assert code == 1
    assert "domain-not-infra" in out
    assert "cross-unit-api-only" in out


def test_spring_like_forbidden_path(tmp_path, capsys):
    work = tmp_path / "spring"
    shutil.copytree(EXAMPLES / "spring-like", work)
    victim = (work / "src" / "main" / "java" / "com" / "example"
              / "catalog" / "api" / "CatalogApi.java")
    victim.write_text(victim.read_text().replace(
        "import com.example.catalog.domain.Product;",
        "import com.example.catalog.domain.Product;\n"
        "import com.example.billing.api.BillingApi;"))
    code, out, _ = run(["check", "--workspace", str(work),
                        "--format", "json", *TODAY], capsys)
    assert code == 1
    assert "no-catalog-to-billing" in out
    assert "catalog -> billing" in out


# ---------------------------------------- init --detect (M9 acceptance gate)

def test_init_detect_django_layout_yields_valid_spec(tmp_path, capsys):
    shutil.copytree(EXAMPLES / "django-like" / "apps", tmp_path / "apps")
    shutil.copy(EXAMPLES / "django-like" / "manage.py", tmp_path / "manage.py")

    code, _, err = run(["init", "--workspace", str(tmp_path)], capsys)
    assert code == 0 and "wrote" in err

    spec = tmp_path / "architecture.yaml"
    text = spec.read_text()
    assert "apps/{unit}/**" in text
    for unit in ("accounts", "catalog", "orders"):
        assert unit in text

    code, out, _ = run(["spec", "--spec", str(spec), "--workspace",
                        str(tmp_path), *TODAY, "validate"], capsys)
    assert code == 0, out

    code, _, _ = run(["check", "--spec", str(spec), "--workspace",
                      str(tmp_path), *TODAY], capsys)
    assert code == 0


def test_init_detect_maven_layout(tmp_path, capsys):
    (tmp_path / "pom.xml").write_text("<project/>")
    src = tmp_path / "src" / "main" / "java" / "com" / "acme"
    src.mkdir(parents=True)
    (src / "App.java").write_text("package com.acme;\nclass App {}\n")
    code, _, err = run(["init", "--workspace", str(tmp_path)], capsys)
    assert code == 0
    text = (tmp_path / "architecture.yaml").read_text()
    assert "language: java" in text and "src/main/java" in text
