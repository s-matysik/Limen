"""Context for a directory request lists every rule that would apply to a new
file created in that directory, whatever the rule kind."""
from limen.context import generate_context

from conftest import ff, make_index, spec_from

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
    message: "Library code does not print."
    remedy: "Use logging."
  - id: only-in-est
    kind: pattern-ban
    severity: error
    scope: { roots: [repo], paths: ["pkg/est/*.py"] }
    pattern: 'eval\\('
    message: "No eval in estimators."
    remedy: "Remove eval."
"""


def _ctx(asked):
    spec = spec_from(SPEC)
    files = [ff("pkg/est/alpha.py", language="python", kind="module"),
             ff("pkg/other/beta.py", language="python", kind="module")]
    return generate_context(spec, make_index(files, root="repo", language="python"), [asked])


def test_recursive_glob_rule_shown_for_directory_request():
    for asked in ("pkg/est/", "pkg/est", "pkg/other/"):
        assert "[no-print]" in _ctx(asked), asked


def test_directory_scoped_rule_only_for_its_directory():
    assert "[only-in-est]" in _ctx("pkg/est/")
    assert "[only-in-est]" not in _ctx("pkg/other/")


def test_unrelated_directory_gets_nothing():
    text = _ctx("docs/")
    assert "[no-print]" not in text and "[only-in-est]" not in text
