#!/usr/bin/env python3
"""Translate an import-linter configuration into limen rules.

Native semantics (import-linter 2.x). The config declares ``root_packages``
and a list of contracts, each of a named type:

    forbidden           `source_modules` must not import `forbidden_modules`
                        (transitively unless `allow_indirect_imports` is set)
    layers              an ordered list of layers; a layer may import layers
                        BELOW it, never above. `containers` prefix the layers.
    independence        the listed modules must not import each other, in
                        either direction, directly or transitively

limen mapping:

    forbidden      -> K6 forbidden-target, scoped to the source module's path,
                      forbidding the target module and its subtree
    layers         -> K1 layer-dependency, one rule per forbidden ordered pair
                      (a layer may not import any layer above it)
    independence   -> K11 reachability, mode `independence` (limen's own
                      transitive-closure kind), which is the direct analogue

Contract types this translator does not handle (`modules`-less custom
contracts, third-party contract plugins) mark the repository untranslatable
with the reason recorded.

Semantic differences reported rather than smoothed over:

D-IL-1  import-linter contracts are TRANSITIVE by default: `a` importing `b`
        which imports `c` violates a `forbidden a -> c` contract, and the
        report names the whole chain. limen's K6/K1 are direct-edge rules;
        only K11 reachability is transitive. Study 1b therefore reports, per
        repository, how many native findings are indirect chains, and excludes
        them from the direct-edge comparison instead of counting them as
        limen misses of a rule limen was not given.
D-IL-2  import-linter works on *module* granularity resolved through Python's
        import system; limen's Python extractor resolves dotted module names
        against the file tree. Namespace packages and re-exports can differ.
"""
from __future__ import annotations

import configparser
import re
from typing import Any


class Untranslatable(Exception):
    pass


def parse_ini(text: str) -> dict[str, Any]:
    """Parse .importlinter / setup.cfg into {root_packages, contracts}."""
    cp = configparser.ConfigParser(strict=False)
    cp.read_string(text)
    root_key = None
    for cand in ("importlinter", "importlinter:importlinter", "tool:importlinter"):
        if cp.has_section(cand):
            root_key = cand
            break
    if root_key is None:
        raise Untranslatable("no [importlinter] section")

    def listify(v: str | None) -> list[str]:
        if not v:
            return []
        return [x.strip() for x in re.split(r"[\n,]", v) if x.strip()]

    roots = listify(cp.get(root_key, "root_packages", fallback=None))
    if not roots:
        one = cp.get(root_key, "root_package", fallback=None)
        roots = [one.strip()] if one else []

    contracts = []
    for sec in cp.sections():
        if not sec.startswith("importlinter:contract"):
            continue
        d = {k: cp.get(sec, k) for k in cp.options(sec)}
        contracts.append({
            "section": sec,
            "name": d.get("name", sec),
            "type": (d.get("type") or "").strip().lower(),
            "source_modules": listify(d.get("source_modules")),
            "forbidden_modules": listify(d.get("forbidden_modules")),
            "layers": listify(d.get("layers")),
            "containers": listify(d.get("containers")),
            "modules": listify(d.get("modules")),
            "allow_indirect_imports": (d.get("allow_indirect_imports") or "")
                                      .strip().lower() in ("true", "yes", "1"),
            "ignore_imports": listify(d.get("ignore_imports")),
        })
    return {"root_packages": roots, "contracts": contracts}


def _mod_to_path(mod: str) -> str:
    return mod.replace(".", "/")


def _pfx(root_path: str) -> str:
    """Root prefix for a discovery/scope glob (limen matches `<root>/<path>`)."""
    return "" if root_path in ("", ".") else root_path.rstrip("/") + "/"


def to_limen(cfg: dict, root_path: str) -> tuple[dict, dict]:
    rules: list[dict] = []
    handled: dict[str, int] = {}
    contracts = cfg["contracts"]
    if not contracts:
        raise Untranslatable("no contracts")

    for c in contracts:
        t = c["type"]
        if t == "forbidden":
            if not c["source_modules"] or not c["forbidden_modules"]:
                raise Untranslatable(f"forbidden contract {c['name']!r} is "
                                     "missing source/forbidden modules")
            # `ignore_imports` lists `a.b -> c.d` edges the contract tolerates.
            # It is import-linter's own suppression mechanism; dropping it makes
            # limen report violations the native tool is configured to accept,
            # which is a translator defect, not a limen finding. Each entry
            # becomes a limen `exempt` entry (target + path scope) so the
            # exception stays declared and auditable.
            exempt = []
            for entry in c["ignore_imports"]:
                if "->" not in entry:
                    continue
                a, b = [x.strip() for x in entry.split("->", 1)]
                exempt.append({
                    "target": b,
                    "scope": f"{_pfx(root_path)}{_mod_to_path(a)}.py",
                    "reason": f"ignore_imports in contract {c['name']!r}",
                })
                exempt.append({
                    "target": b,
                    "scope": f"{_pfx(root_path)}{_mod_to_path(a)}/**",
                    "reason": f"ignore_imports in contract {c['name']!r}",
                })
            for i, src in enumerate(c["source_modules"]):
                forbid = []
                for f in c["forbidden_modules"]:
                    forbid += [f, f + ".*", f + ".**"]
                rules.append({
                    "id": "il-" + re.sub(r"[^a-z0-9]+", "-",
                                         f"{c['name']}-{i}".lower()).strip("-"),
                    "kind": "forbidden-target",
                    "severity": "error",
                    "scope": {"roots": ["main"],
                              # root-prefixed: limen matches globs against
                              # `<root>/<path>`, not against the bare module path
                              "paths": [f"{_pfx(root_path)}{_mod_to_path(src)}/**",
                                        f"{_pfx(root_path)}{_mod_to_path(src)}.py"]},
                    "forbid": forbid,
                    "exempt": exempt,
                    "message": f"{c['name']}: {src} must not import "
                               f"{', '.join(c['forbidden_modules'])}.",
                    "remedy": "Invert the dependency or move the shared code.",
                })
            handled["forbidden"] = handled.get("forbidden", 0) + 1
        elif t == "layers":
            layers = [l.strip() for l in c["layers"] if l.strip()]
            if len(layers) < 2:
                raise Untranslatable(f"layers contract {c['name']!r} has "
                                     f"{len(layers)} layers")
            # import-linter lists layers HIGH to LOW; a layer may import those
            # below it, never above.
            for hi in range(len(layers)):
                for lo in range(hi):
                    # layers[lo] is ABOVE layers[hi]; the lower layer must not
                    # reach up.
                    rules.append({
                        "id": "il-" + re.sub(
                            r"[^a-z0-9]+", "-",
                            f"{c['name']}-{layers[hi]}-not-{layers[lo]}".lower()
                        ).strip("-"),
                        "kind": "layer-dependency",
                        "severity": "error",
                        "forbid": {"from_layer": layers[hi].split(".")[-1],
                                   "to_layer": layers[lo].split(".")[-1]},
                        "scope": {"roots": ["main"]},
                        "message": (f"{c['name']}: {layers[hi]} must not import "
                                    f"the higher layer {layers[lo]}."),
                        "remedy": "Depend downwards only.",
                    })
            handled["layers"] = handled.get("layers", 0) + 1
        elif t == "independence":
            mods = c["modules"]
            if len(mods) < 2:
                raise Untranslatable(f"independence contract {c['name']!r} has "
                                     f"{len(mods)} modules")
            rules.append({
                "id": "il-" + re.sub(r"[^a-z0-9]+", "-",
                                     f"{c['name']}-indep".lower()).strip("-"),
                "kind": "reachability",
                "severity": "error",
                "mode": "independence",
                "units": [m.split(".")[-1] for m in mods],
                "report_path": True,
                "message": f"{c['name']}: {', '.join(mods)} must stay independent.",
                "remedy": "Move the shared concern into a module all of them "
                          "may depend on.",
            })
            handled["independence"] = handled.get("independence", 0) + 1
        else:
            raise Untranslatable(f"contract type {t!r} is not translated")

    layer_contracts = [c for c in contracts if c["type"] == "layers"]
    spec: dict[str, Any] = {
        "version": 1,
        "project": "import-linter-retrofit",
        "roots": {"main": {"path": root_path, "language": "python"}},
        "unresolved_policy": {"default": "off"},
        "rules": rules,
    }
    if layer_contracts:
        names, patterns = [], set()
        # limen matches discovery globs against ROOT-PREFIXED paths (a root of
        # `api` yields `api/core/...`), so a pattern built from the module name
        # alone matches nothing and every K1 rule silently never fires. The
        # root directory is therefore prepended here.
        prefix = "" if root_path in ("", ".") else root_path.rstrip("/") + "/"
        for c in layer_contracts:
            containers = c["containers"] or [""]
            for l in c["layers"]:
                names.append(l.split(".")[-1])
            for cont in containers:
                base = _mod_to_path(cont) if cont else ""
                patterns.add(prefix + (base + "/" if base else "") + "{layer}/**")
        # limen assigns a layer POSITIONALLY and refuses a file that two
        # discovery globs bind differently (`layer-ambiguous`). Nested
        # containers produce exactly that: `core/workflow/{layer}/**` and
        # `core/workflow/graph_engine/{layer}/**` both match a file under
        # `graph_engine/response_coordinator/`. Rather than pick one and
        # silently mis-assign every file below the nesting point, the
        # repository is declared untranslatable with the colliding containers
        # named.
        pats = sorted(patterns)
        nested = [(a, b) for a in pats for b in pats
                  if a != b and b.startswith(a[:a.index("{layer}")])]
        if nested:
            a, b = nested[0]
            raise Untranslatable(
                f"nested layer containers ({a!r} and {b!r}) make the layer of "
                "a file ambiguous; limen binds {layer} to a fixed path "
                "position and refuses such files")
        spec["layers"] = {"discovery": [{"root": "main", "pattern": p}
                                        for p in pats],
                          "declared": sorted(set(names))}
    if any(c["type"] == "independence" for c in contracts):
        indep_units = sorted({m.split(".")[-1] for c in contracts
                              if c["type"] == "independence" for m in c["modules"]})
        spec["units"] = {"discovery": [{"root": "main",
                                        "pattern": _pfx(root_path) + "{unit}/**"}],
                         "declared": indep_units, "registry_policy": "subset"}

    report = {
        "contracts_total": len(contracts),
        "contracts_by_type": handled,
        "rules_emitted": len(rules),
        "transitive_by_default": [c["name"] for c in contracts
                                  if not c["allow_indirect_imports"]],
        "ignore_imports_entries": sum(len(c["ignore_imports"]) for c in contracts),
    }
    return spec, report
