"""Context lists only exemptions for files the request covers, and a token
budget summarises exemptions before it touches the rule text."""
from limen.context import generate_context

from conftest import ff, make_index, spec_from

ENTRY = '{{ path: {p}, reason: legacy, owner: team, expires: "2099-01-01" }}'
FILES_A = [f"pkg/a/m{i}.py" for i in range(30)]
SPEC = """
version: 1
roots:
  repo: { path: ., language: python }
rules:
  - id: no-print
    kind: pattern-ban
    severity: error
    scope: { roots: [repo], paths: ["pkg/**/*.py"] }
    pattern: 'print\\('
    exempt_files: [EXEMPT]
    message: "Library code does not print."
    remedy: "Use logging."
""".replace("EXEMPT", ", ".join(ENTRY.format(p=p) for p in FILES_A + ["pkg/b/old.py"]))


def _ctx(asked, budget=None):
    files = [ff(p, language="python", kind="module") for p in FILES_A + ["pkg/b/old.py", "pkg/b/new.py"]]
    return generate_context(spec_from(SPEC), make_index(files, root="repo", language="python"),
                            [asked], budget_tokens=budget)


def test_exemptions_outside_request_are_not_listed():
    text = _ctx("pkg/b/")
    assert "`pkg/b/old.py`" in text
    assert "pkg/a/m0.py" not in text


def test_budget_summarises_exemptions_and_keeps_rule_text():
    full = _ctx("pkg/")
    assert full.count("pkg/a/m") == 30
    small = _ctx("pkg/", budget=250)
    assert "Library code does not print." in small
    assert "31 existing file(s) exempted" in small
    assert "pkg/a/m0.py" not in small
