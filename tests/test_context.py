"""`limen context`: determinism, only-real-symbols, and budget behaviour."""
import re

from limen.context import generate_context
from limen.util import token_estimate

from conftest import TODAY, ff, make_index, spec_from

SPEC = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Billing, Audit]
public_surface:
  php:
    - "{unit}/Contracts/**"
rules:
  - id: public-api
    kind: public-api-only
    severity: error
    scope: { roots: [host] }
    exempt: []
    message: "Cross-unit dependencies must resolve to the public surface."
    remedy: "Import the Contracts entry."
  - id: no-secret
    kind: forbidden-target
    severity: error
    scope: { roots: [host] }
    forbid: ["App\\\\Secret"]
    message: "Secret is forbidden."
    remedy: "Remove the import."
  - id: soft-size
    kind: metric-threshold
    metric: lines
    max: 400
    severity: warning
    scope: { roots: [host] }
    message: "Keep files small."
    remedy: "Split large files."
  - id: soft-naming
    kind: pattern-ban
    severity: warning
    pattern: "\\\\bdd\\\\("
    scope: { roots: [host] }
    exempt_files:
      - { path: "app/Modules/Billing/Legacy.php", reason: "debug shim",
          expires: 2027-06-30 }
    message: "No dd() in committed code."
    remedy: "Remove debug calls."
"""


def _make():
    spec = spec_from(SPEC)
    idx = make_index([
        ff("app/Modules/Billing/Invoice.php", unit=None,
           declares=["App\\Modules\\Billing\\Invoice"]),
        ff("app/Modules/Audit/Contracts/AuditApi.php", unit=None,
           declares=["App\\Modules\\Audit\\Contracts\\AuditApi"]),
        ff("app/Modules/Audit/Internal/Writer.php", unit=None,
           declares=["App\\Modules\\Audit\\Internal\\Writer"]),
        ff("app/Modules/Billing/Legacy.php", unit=None,
           declares=["App\\Modules\\Billing\\Legacy"]),
    ])
    return spec, idx


PATHS = ["app/Modules/Billing/Invoice.php"]


def test_context_deterministic():
    spec, idx = _make()
    a = generate_context(spec, idx, PATHS, today=TODAY)
    spec2, idx2 = _make()
    b = generate_context(spec2, idx2, PATHS, today=TODAY)
    assert a == b


FQCN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\\[A-Za-z_][A-Za-z0-9_]*)+$")


def test_every_backticked_symbol_exists_in_facts():
    spec, idx = _make()
    text = generate_context(spec, idx, PATHS, today=TODAY)
    known = {s for f in idx.files for s in f.declares} | \
            {f.path for f in idx.files} | {f.unit for f in idx.files if f.unit}
    for sym in re.findall(r"`([^`]+)`", text):
        # the no-hallucination contract covers code symbols and file paths;
        # regexes, globs and unit names are specification text
        if FQCN.match(sym) or sym.endswith(".php"):
            assert sym in known, f"hallucinated symbol in context: {sym}"


def test_sections_present():
    spec, idx = _make()
    text = generate_context(spec, idx, PATHS, today=TODAY)
    for heading in ("### Hard rules", "### Advisory rules",
                    "### Active exemptions", "### Verify"):
        assert heading in text
    assert "[public-api]" in text and "[no-secret]" in text
    assert "[soft-size]" in text
    assert "debug shim" in text  # active exemption surfaced


def test_budget_drops_warnings_before_errors():
    spec, idx = _make()
    full = generate_context(spec, idx, PATHS, today=TODAY)
    # a budget below the full size but comfortably above the error core
    budget = token_estimate(full) - 40
    text = generate_context(spec, idx, PATHS, today=TODAY,
                            budget_tokens=budget)
    assert "[public-api]" in text and "[no-secret]" in text
    assert "truncated to fit the token budget" in text
    dropped_soft = ("[soft-size]" not in text) or ("[soft-naming]" not in text)
    assert dropped_soft


def test_budget_floor_compacts_errors_last():
    spec, idx = _make()
    text = generate_context(spec, idx, PATHS, today=TODAY, budget_tokens=1)
    # even below any feasible budget the errors survive in compact form
    assert "[public-api]" in text and "[no-secret]" in text
    assert "### Advisory rules" not in text
    assert "truncated" in text
