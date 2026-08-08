#!/usr/bin/env python3
"""Translate a deptrac configuration into limen rules.

Native semantics (deptrac 4.x):

* ``layers:`` assigns tokens (classes, functions, files) to named layers via
  ``collectors``. Many collector types exist; this translator handles the ones
  that are *statically decidable from the file tree*:

      directory   regex over the file path
      className   regex over the fully-qualified class name
      classNameRegex  same
      bool/and/or     composed from the above (a subset: pure ``must``)

  Any other collector type (``inherits``, ``implements``, ``uses``,
  ``composer``, ``layer``, ``method``, ``attribute``, ``superglobal``, …) needs
  type resolution deptrac gets from its PHP parser and limen deliberately does
  not do. A layer using one is marked untranslatable and the whole repository
  is excluded from the differential, with the reason recorded.

* ``ruleset:`` lists, per layer, the layers it MAY depend on. Anything not
  listed is a violation. Entries prefixed ``+`` inherit another layer's
  allowances; ``skip_violations`` records accepted violations per class.

limen mapping: ``ruleset`` is a per-layer allowlist over layers, which is
exactly ``K1 layer-dependency`` — one rule per forbidden ordered pair
(from_layer, to_layer), the complement of the declared ruleset.

Known semantic differences, reported and never smoothed over:

D-DT-1  deptrac reports the line of the **token usage** (e.g. where the class
        is instantiated); limen reports the line of the **import statement**.
        The comparison key (file, line, target) therefore cannot match on line
        for a class used away from its import. Study 1b reports both a
        line-sensitive and a line-insensitive (file, target) agreement, and
        states which is which.
D-DT-2  deptrac resolves dependencies through PHP type inference (inheritance,
        parameter types, return types, attributes); limen's PHP extractor reads
        `use` statements. Targets deptrac finds without an import are limen
        misses by construction.
"""
from __future__ import annotations

import re
from typing import Any

STATIC_COLLECTORS = {"directory", "classname", "classnameregex", "filename",
                     "bool", "class_name", "directory_name"}


class Untranslatable(Exception):
    pass


def _norm(t: str) -> str:
    return (t or "").strip().lower().replace("-", "").replace("_", "")


def collector_is_static(c: dict) -> tuple[bool, str]:
    t = _norm(c.get("type", ""))
    if t in ("bool",):
        for key in ("must", "must_not"):
            for sub in c.get(key) or []:
                ok, why = collector_is_static(sub)
                if not ok:
                    return False, why
        return True, ""
    if t in STATIC_COLLECTORS:
        return True, ""
    return False, f"collector type {c.get('type')!r} needs PHP type resolution"


def load(cfg: dict) -> dict[str, Any]:
    """Normalise a parsed deptrac.yaml into {layers, ruleset, paths}."""
    doc = cfg.get("deptrac", cfg)
    if not isinstance(doc, dict):
        raise Untranslatable("deptrac: is not a mapping")
    layers = doc.get("layers") or []
    ruleset = doc.get("ruleset") or {}
    if not isinstance(ruleset, dict):
        raise Untranslatable("ruleset is not a mapping")
    if not isinstance(layers, list):
        raise Untranslatable("layers is not a list")
    if not layers:
        # deptrac supports `imports:` — layer definitions pulled in from other
        # files. Translating one file in isolation would silently drop those
        # layers and every rule about them, so this is refused rather than
        # half-translated.
        raise Untranslatable(
            "no `layers:` in this file"
            + (f"; it uses `imports:` ({len(doc['imports'])} entries) to pull "
               "layer definitions from other files"
               if doc.get("imports") else ""))
    names = []
    for l in layers:
        if not isinstance(l, dict) or "name" not in l:
            raise Untranslatable("a layer has no name")
        names.append(l["name"])
        for c in l.get("collectors") or []:
            ok, why = collector_is_static(c if isinstance(c, dict) else {})
            if not ok:
                raise Untranslatable(f"layer {l['name']!r}: {why}")
    return {"layers": layers, "layer_names": names, "ruleset": ruleset,
            "paths": doc.get("paths") or ["."]}


def expand_ruleset(ruleset: dict, names: list[str]) -> dict[str, set[str]]:
    """Resolve `+layer` inheritance into concrete allow sets."""
    out: dict[str, set[str]] = {}

    def resolve(layer: str, seen: set[str]) -> set[str]:
        if layer in out:
            return out[layer]
        if layer in seen:
            return set()
        seen = seen | {layer}
        allow: set[str] = set()
        # A `ruleset` entry may be None (`LayerName:` with nothing after it),
        # which means the layer may depend on NOTHING — an empty allow set,
        # not a missing declaration.
        for entry in (ruleset.get(layer) or []):
            e = str(entry)
            if e.startswith("+"):
                allow |= resolve(e[1:], seen)
            else:
                allow.add(e)
        out[layer] = allow
        return allow

    for n in names:
        resolve(n, set())
    return out


def directory_patterns(layer: dict) -> list[str]:
    """Path globs for a layer's `directory` collectors.

    deptrac's `directory` value is a REGEX over the path, commonly written as
    `src/Foo/.*`. Converted to a limen path glob by replacing the regex tail
    with `**`; a value using regex features beyond a literal prefix plus `.*`
    is refused rather than approximated.
    """
    pats = []
    for c in layer.get("collectors") or []:
        if not isinstance(c, dict) or _norm(c.get("type", "")) not in (
                "directory", "directory_name"):
            continue
        v = str(c.get("value", ""))
        m = re.fullmatch(r"([\w./\-]*?)/?\.\*", v) or re.fullmatch(r"([\w./\-]+)", v)
        if not m:
            raise Untranslatable(f"directory collector value {v!r} is a regex "
                                 "this translator will not approximate")
        pats.append(m.group(1).rstrip("/") + "/**")
    return pats


def to_limen(cfg: dict, root_path: str) -> tuple[dict, dict]:
    """Emit a limen spec (K1 layer-dependency) plus a translation report."""
    d = load(cfg)
    names = d["layer_names"]
    allow = expand_ruleset(d["ruleset"], names)

    discovery, layer_paths = [], {}
    for l in d["layers"]:
        pats = directory_patterns(l)
        if not pats:
            raise Untranslatable(
                f"layer {l['name']!r} has no directory collector; its members "
                "cannot be located by path alone")
        layer_paths[l["name"]] = pats

    rules, pairs = [], []
    for src in names:
        for dst in names:
            if dst == src or dst in allow.get(src, set()):
                continue
            pairs.append((src, dst))
            rules.append({
                "id": "dt-" + re.sub(r"[^a-z0-9]+", "-",
                                     f"{src}-not-{dst}".lower()).strip("-"),
                "kind": "layer-dependency",
                "severity": "error",
                "forbid": {"from_layer": src, "to_layer": dst},
                "scope": {"roots": ["main"]},
                "message": f"{src} must not depend on {dst}.",
                "remedy": f"Add {dst} to the {src} ruleset in deptrac.yaml, or "
                          "drop the dependency.",
            })

    # Layer discovery. limen binds `{layer}` POSITIONALLY: a discovery glob
    # captures one path segment and that captured text is the layer name. A
    # deptrac layer is therefore expressible only when its directory's last
    # segment IS the layer name — `src/Domain/.*` -> layer "Domain" works,
    # `src/Legacy/Stuff/.*` -> layer "Domain" does not. Repositories that break
    # the convention are refused here rather than translated approximately,
    # because a mis-assigned layer silently changes every finding.
    depth_patterns: set[str] = set()
    for name, pats in sorted(layer_paths.items()):
        for p in pats:
            segs = p[:-len("/**")].split("/")
            if segs[-1] != name:
                raise Untranslatable(
                    f"layer {name!r} is collected from {p!r}, whose directory "
                    "name is not the layer name; limen binds {layer} to a path "
                    "segment, so this mapping is not expressible")
            depth_patterns.add("/".join(segs[:-1] + ["{layer}", "**"]))
    discovery = [{"root": "main", "pattern": p} for p in sorted(depth_patterns)]

    spec = {
        "version": 1,
        "project": "deptrac-retrofit",
        "roots": {"main": {"path": root_path, "language": "php"}},
        "layers": {"discovery": discovery, "declared": sorted(names)},
        "unresolved_policy": {"default": "off"},
        "rules": rules,
    }
    report = {
        "native_layers": len(names),
        "native_ruleset_entries": sum(len(v) for v in d["ruleset"].values()),
        "forbidden_pairs": len(pairs),
        "layer_paths": layer_paths,
        "allow_expanded": {k: sorted(v) for k, v in allow.items()},
    }
    return spec, report
