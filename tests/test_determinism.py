"""Determinism: same input -> byte-identical reports; file order and path
separators must not leak into the output."""
from limen.engine import run_check
from limen.facts.merge import resolve_units_layers
from limen.report import json as report_json
from limen.report import junit, sarif

from conftest import TODAY, ff, make_index, spec_from

SPEC = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Billing, Core]
rules:
  - id: no-secret
    kind: forbidden-target
    severity: error
    scope: { roots: [host] }
    forbid: ["App\\\\Secret"]
    message: "Secret is forbidden."
    remedy: "Remove it."
  - id: size
    kind: metric-threshold
    metric: lines
    max: 5
    scope: { roots: [host] }
    message: "Too long."
    remedy: "Split."
"""

FILES = [
    ff("app/Modules/Billing/A.php", unit=None, lines=9,
       deps=[("App\\Secret", 4, "use")]),
    ff("app/Modules/Core/B.php", unit=None, lines=2,
       deps=[("App\\Secret", 8, "use")]),
]


def _run(files):
    spec = spec_from(SPEC)
    idx = make_index([ff(f.path, deps=[(d.target, d.line, d.via) for d in f.deps],
                         lines=f.lines) for f in files])
    resolve_units_layers(spec, idx)
    return run_check(spec, idx, today=TODAY)


def test_json_report_twice_byte_identical():
    r1 = report_json.render(_run(FILES))
    r2 = report_json.render(_run(FILES))
    assert r1 == r2
    assert "timestamp" not in r1.lower()


def test_report_independent_of_input_order():
    a = report_json.render(_run(FILES))
    b = report_json.render(_run(list(reversed(FILES))))
    assert a == b


def test_findings_sorted_by_severity_then_rule_file_line():
    rep = _run(FILES)
    keys = [(f.severity, f.rule_id, f.file, f.line) for f in rep.findings]
    # errors (rank 0) strictly before warnings; within severity sorted
    sev_rank = [0 if s == "error" else 1 for (s, *_rest) in keys]
    assert sev_rank == sorted(sev_rank)
    assert keys == sorted(keys, key=lambda k: (0 if k[0] == "error" else 1,
                                               k[1], k[2] or "", k[3] or 0))


def test_backslash_paths_normalized():
    files = [ff("app\\Modules\\Billing\\A.php".replace("\\", "/"), lines=1,
                deps=[("App\\Secret", 1, "use")])]
    out = report_json.render(_run(files))
    assert "app/Modules/Billing/A.php" in out
    assert "app\\\\Modules" not in out


def test_junit_and_sarif_have_no_timestamps():
    rep = _run(FILES)
    for renderer in (junit.render, sarif.render):
        text = renderer(rep)
        assert renderer(rep) == text
        assert "timestamp" not in text.lower()
