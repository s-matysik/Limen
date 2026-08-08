"""Exemption semantics across rules: active suppresses, expired escalates to
an error naming the date (frozen clock)."""
from limen.rules import forbidden

from conftest import ff, make_ctx, make_index, spec_from

BODY = """
version: 1
roots:
  host: { path: app, language: php }
units:
  declared: [Billing]
rules:
  - id: k6
    kind: forbidden-target
    severity: error
    scope: { roots: [host] }
    forbid: ["App\\\\Legacy\\\\*"]
    exempt:
      - target: "App\\\\Legacy\\\\Mailer"
        reason: "migration in flight"
        ticket: SEO-123
        expires: %s
    message: "Legacy namespace is frozen."
    remedy: "Use the new mailer."
"""

IDX_FILES = [ff("app/Modules/Billing/Send.php", unit="Billing",
                deps=[("App\\Legacy\\Mailer", 12, "use")])]


def test_active_exemption_suppresses(frozen_today):
    spec = spec_from(BODY % "2027-01-01")
    fs = forbidden.check(spec.rules[0], make_ctx(spec, make_index(IDX_FILES)))
    assert fs == []


def test_expired_exemption_is_error_with_date(frozen_today):
    spec = spec_from(BODY % "2026-01-01")
    fs = forbidden.check(spec.rules[0], make_ctx(spec, make_index(IDX_FILES)))
    assert len(fs) == 1
    f = fs[0]
    assert f.severity == "error"
    assert "expired on 2026-01-01" in f.message
    assert f.exemptible and f.spec_path and "exempt" in f.spec_path


def test_expiry_boundary_is_inclusive(frozen_today):
    # expires == today -> still active (escalation starts the day after)
    spec = spec_from(BODY % frozen_today.isoformat())
    fs = forbidden.check(spec.rules[0], make_ctx(spec, make_index(IDX_FILES)))
    assert fs == []


def test_exemption_without_expires_never_expires(frozen_today):
    text = (BODY % "2027-01-01").replace("        expires: 2027-01-01\n", "")
    spec = spec_from(text)
    fs = forbidden.check(spec.rules[0], make_ctx(spec, make_index(IDX_FILES)))
    assert fs == []
