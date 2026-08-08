"""Unit dependency graph emitters: dot | json | mermaid. Sorted, stable."""
from __future__ import annotations

import json as _json

from .facts.model import FactsIndex


def unit_edges(index: FactsIndex) -> dict[tuple[str, str], int]:
    edges: dict[tuple[str, str], int] = {}
    for f in index.files:
        if f.unit is None:
            continue
        for dep in f.deps:
            t = index.resolve_target(dep.target)
            if t is None or t.unit is None or t.unit == f.unit:
                continue
            edges[(f.unit, t.unit)] = edges.get((f.unit, t.unit), 0) + 1
    return edges


def render(index: FactsIndex, fmt: str = "dot") -> str:
    edges = unit_edges(index)
    units = sorted({u for e in edges for u in e} | set(index.units()))
    if fmt == "json":
        doc = {"units": units,
               "edges": [{"from": a, "to": b, "count": edges[(a, b)]}
                         for (a, b) in sorted(edges)]}
        return _json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if fmt == "mermaid":
        out = ["graph LR"]
        for (a, b) in sorted(edges):
            out.append(f"  {a} -->|{edges[(a, b)]}| {b}")
        return "\n".join(out) + "\n"
    out = ["digraph limen {", "  rankdir=LR;"]
    for u in units:
        out.append(f'  "{u}";')
    for (a, b) in sorted(edges):
        out.append(f'  "{a}" -> "{b}" [label="{edges[(a, b)]}"];')
    out.append("}")
    return "\n".join(out) + "\n"
