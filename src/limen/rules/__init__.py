"""Rule dispatch: kind -> checker. Adding a language never touches this table;
adding a *kind* is a deliberate act — there is no 'arbitrary code' escape hatch."""
from . import (baseline, consumers, forbidden, layer, metric, ownership,
               pattern, public_api, reachability, required_inbound, scoped,
               structure)
from .base import Finding, RuleContext  # noqa: F401

DISPATCH = {
    "layer-dependency": layer.check,
    "ownership": ownership.check,
    "consumer-allowlist": consumers.check,
    "public-api-only": public_api.check,
    "scoped-allowlist": scoped.check,
    "forbidden-target": forbidden.check,
    "baseline-snapshot": baseline.check,
    "pattern-ban": pattern.check,
    "structure": structure.check,
    "metric-threshold": metric.check,
    "reachability": reachability.check,
    "required-inbound": required_inbound.check,
}

# Kinds that assert a property of the whole tree: under --changed-only they are
# computed from cached facts, never skipped (spec §4.4).
# `required-inbound` belongs here because a file's verdict depends on a DIFFERENT
# file (the designated exporter): a diff that adds the artefact but not the
# export touches only the former, so a changed-only run that skipped the tree
# would report the new file as compliant.
FULL_SCAN_KINDS = {"ownership", "baseline-snapshot", "required-inbound"}
