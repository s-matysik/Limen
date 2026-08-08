"""Unit/layer resolution over merged facts (engine step 4) and document merging."""
from __future__ import annotations

from ..spec.model import Spec, SpecError, SpecIssue
from ..util import glob_to_regex
from .model import FactsDocument, FactsIndex


def merge_documents(documents: list[FactsDocument]) -> FactsIndex:
    return FactsIndex(documents)


def resolve_units_layers(spec: Spec, index: FactsIndex) -> None:
    """Assign unit/layer/unit_dir to every file. Ambiguity (two discovery
    patterns yielding different units for one file) is a spec-integrity error
    and must abort with exit code 3."""
    unit_res = [(d, glob_to_regex(d.pattern)) for d in spec.unit_discovery]
    layer_res = [(d, glob_to_regex(d.pattern)) for d in spec.layer_discovery]
    issues: list[SpecIssue] = []

    for f in index.files:
        units_found: dict[str, str] = {}  # unit -> unit_dir
        for disc, rx in unit_res:
            if disc.root is not None and disc.root != f.root:
                continue
            m = rx.match(f.path)
            if m and "unit" in m.groupdict() and m.group("unit"):
                units_found[m.group("unit")] = f.path[: m.end("unit")]
        if len(units_found) > 1:
            issues.append(SpecIssue(
                "unit-ambiguous", "units.discovery",
                f"{f.path}: matches multiple units {sorted(units_found)}"))
            continue
        if units_found:
            (unit, unit_dir), = units_found.items()
            f.unit, f.unit_dir = unit, unit_dir

        layers_found: set[str] = set()
        for disc, rx in layer_res:
            if disc.root is not None and disc.root != f.root:
                continue
            m = rx.match(f.path)
            if m and "layer" in m.groupdict() and m.group("layer"):
                layers_found.add(m.group("layer"))
        if len(layers_found) > 1:
            issues.append(SpecIssue(
                "layer-ambiguous", "layers.discovery",
                f"{f.path}: matches multiple layers {sorted(layers_found)}"))
            continue
        if layers_found:
            f.layer = layers_found.pop()
            if spec.layers_declared and f.layer not in spec.layers_declared:
                f.layer = None  # a directory name that is not a declared layer

    if issues:
        raise SpecError(issues)
