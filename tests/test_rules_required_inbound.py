"""`required-inbound`: a positive obligation, and the cases that must not pass.

The kind exists because every other kind in the engine is prohibitive or an
existence assertion, while the norms maintainers write about NEW code are mostly
of the opposite shape ("a new estimator must be exported from api.py").  A
prohibition cannot stand in for it: forbidding the wrong import says nothing
about a file that nobody imports at all.

The tests pin the behaviours that decide whether the rule has teeth: it fires on
an unexported artefact, it stays silent once the designated exporter references
it, and it refuses to pass vacuously when the spec names no exporter -- the
fail-open shape this kind exists to remove.
"""
from __future__ import annotations

from limen.rules import DISPATCH, FULL_SCAN_KINDS

from conftest import ff, make_ctx, make_index, spec_from

SPEC_YAML = """
version: 1
roots:
  repo: { path: ., language: python }
rules:
  - id: exported
    kind: required-inbound
    severity: error
    scope: { paths: ["pkg/est/**"] }
    from: ["pkg/api.py"]
    message: "New estimators must be exported from pkg/api.py."
    remedy: "Add the import to pkg/api.py."
"""


def _run(params, exporter_imports, exporter_via="import"):
    spec = spec_from(SPEC_YAML)
    rule = spec.rules[0]
    if "from" not in params:
        rule.params.pop("from", None)
    rule.params.update(params)
    files = [
        ff("pkg/api.py", declares=["pkg.api"], language="python", kind="module",
           deps=[(t, i + 1, exporter_via) for i, t in enumerate(exporter_imports)]),
        ff("pkg/est/alpha.py", declares=["pkg.est.alpha"], language="python",
           kind="module"),
        ff("pkg/est/beta.py", declares=["pkg.est.beta"], language="python",
           kind="module"),
    ]
    ctx = make_ctx(spec, make_index(files, root="repo", language="python"))
    return DISPATCH["required-inbound"](rule, ctx)


def test_unexported_artifact_is_a_finding_on_that_file():
    out = _run({"from": ["pkg/api.py"]}, ["pkg.est.alpha"])
    assert [f.file for f in out] == ["pkg/est/beta.py"]
    assert out[0].severity == "error"


def test_everything_exported_is_silent():
    assert _run({"from": ["pkg/api.py"]}, ["pkg.est.alpha", "pkg.est.beta"]) == []


def test_nothing_exported_fires_for_every_artifact():
    assert sorted(f.file for f in _run({"from": ["pkg/api.py"]}, [])) == [
        "pkg/est/alpha.py", "pkg/est/beta.py"]


def test_missing_from_list_is_a_specification_error_not_a_vacuous_pass():
    out = _run({}, ["pkg.est.alpha", "pkg.est.beta"])
    assert len(out) == 1 and out[0].severity == "error"
    assert "from" in out[0].message


def test_via_filter_rejects_the_wrong_edge_kind():
    out = _run({"from": ["pkg/api.py"], "via": ["import"]},
               ["pkg.est.alpha", "pkg.est.beta"], exporter_via="text")
    assert sorted(f.file for f in out) == ["pkg/est/alpha.py", "pkg/est/beta.py"]


def test_exemption_suppresses_a_known_gap():
    out = _run({"from": ["pkg/api.py"],
                "exempt": [{"target": "pkg/est/beta.py", "reason": "legacy",
                            "owner": "team", "expires": "2099-01-01"}]},
               ["pkg.est.alpha"])
    assert out == []


def test_kind_is_full_scan_because_the_verdict_lives_in_another_file():
    assert "required-inbound" in FULL_SCAN_KINDS


def test_context_shows_obligation_for_a_directory_request():
    """An agent asks for context BEFORE creating the file, so the request is a
    directory.  A file-glob scope never matches a bare directory; before the fix
    the obligation rendered as "(none)" on every one of 19 real tasks."""
    from limen.context import generate_context
    spec = spec_from(SPEC_YAML)
    files = [ff("pkg/api.py", declares=["pkg.api"], language="python", kind="module"),
             ff("pkg/est/alpha.py", declares=["pkg.est.alpha"], language="python",
                kind="module")]
    index = make_index(files, root="repo", language="python")
    for asked in ("pkg/est/", "pkg/est", "pkg/est/gamma.py"):
        text = generate_context(spec, index, [asked])
        assert "exported" in text, asked
    assert "exported" not in generate_context(spec, index, ["pkg/other/"])
