"""K11 reachability: transitive reachability over the unit dependency graph.

Modes (mirroring import-linter's contract families, which check indirect
dependencies too):
  independence   — the listed units must not reach each other in any direction;
  layers         — ``order`` is top→bottom; a lower unit must not reach a
                   higher one, directly or transitively;
  forbidden-path — ``forbid: {from, to}`` must not be transitively reachable.

The graph is built once from facts; BFS visits sorted edges and records the
path, because a finding without the path is non-diagnostic in this mode
(``report_path``). ``ignore_edges`` carries the same ``expires`` semantics as
every other exemption list.
"""
from __future__ import annotations

from typing import Optional

from ..spec.model import Exemption, Rule
from .base import Finding, RuleContext, Scope, make_finding


def build_unit_graph(ctx: RuleContext, scope: Scope):
    """edges[(a, b)] = (src_path, line, dep_target) — the lexicographically
    first witness, so reports are stable."""
    edges: dict[tuple[str, str], tuple[str, int, str]] = {}
    for f in ctx.index.files:
        if f.unit is None or not scope.file_matches(f):
            continue
        for dep in sorted(f.deps, key=lambda d: (d.line, d.target)):
            t = ctx.index.resolve_target(dep.target)
            if t is None or t.unit is None or t.unit == f.unit:
                continue
            key = (f.unit, t.unit)
            witness = (f.path, dep.line, dep.target)
            if key not in edges or witness < edges[key]:
                edges[key] = witness
    return edges


def _parse_ignores(rule: Rule):
    out = []
    for i, raw in enumerate(rule.params.get("ignore_edges") or []):
        spec_path = f"{rule.spec_path}.ignore_edges[{i}]"
        if isinstance(raw, dict):
            ex = Exemption.parse(
                {k: v for k, v in raw.items() if k not in ("from", "to")}, spec_path)
            src = str(raw.get("from", "")).split(".", 1)[0]
            dst = str(raw.get("to", "")).split(".", 1)[0]
        else:
            src, _, dst = str(raw).partition("->")
            ex = Exemption(spec_path=spec_path)
            src, dst = src.strip(), dst.strip()
        out.append((src, dst, ex))
    return out


def _bfs_path(adj: dict[str, list[str]], start: str, goal: str,
              max_depth: Optional[int]) -> Optional[list[str]]:
    if start == goal:
        return None
    from collections import deque

    parent: dict[str, str] = {}
    depth = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if max_depth is not None and depth[node] >= max_depth:
            continue
        for nxt in adj.get(node, []):
            if nxt in depth:
                continue
            depth[nxt] = depth[node] + 1
            parent[nxt] = node
            if nxt == goal:
                path = [goal]
                while path[-1] != start:
                    path.append(parent[path[-1]])
                return list(reversed(path))
            queue.append(nxt)
    return None


def check(rule: Rule, ctx: RuleContext) -> list[Finding]:
    scope = Scope(rule.scope)
    mode = rule.params.get("mode", "independence")
    max_depth = rule.params.get("max_depth")
    report_path = rule.params.get("report_path", True)
    edges = build_unit_graph(ctx, scope)
    out: list[Finding] = []

    ignores = _parse_ignores(rule)
    for src, dst, ex in ignores:
        if ex.status(ctx.today) == "expired" and (src, dst) in edges:
            path, line, target = edges[(src, dst)]
            out.append(make_finding(
                rule, file=path, line=line, unit=src, target=target,
                severity="error",
                message=(f"{rule.message} [ignored edge {src} -> {dst} "
                         f"expired on {ex.expires.isoformat()}]"),
                exemptible=True, spec_path=ex.spec_path))
        elif ex.status(ctx.today) == "active":
            edges.pop((src, dst), None)

    adj: dict[str, list[str]] = {}
    for (a, b) in sorted(edges):
        adj.setdefault(a, []).append(b)
    for a in adj:
        adj[a].sort()

    pairs: list[tuple[str, str]] = []
    if mode == "independence":
        units = sorted(rule.params.get("units") or [])
        pairs = [(a, b) for a in units for b in units if a != b]
    elif mode == "layers":
        order = list(rule.params.get("order") or rule.params.get("units") or [])
        for hi_idx, hi in enumerate(order):
            for lo in order[hi_idx + 1:]:
                pairs.append((lo, hi))  # lower must not reach higher
    elif mode == "forbidden-path":
        forbid = rule.params.get("forbid") or {}
        if forbid.get("from") and forbid.get("to"):
            pairs = [(str(forbid["from"]), str(forbid["to"]))]

    for a, b in sorted(pairs):
        path_units = _bfs_path(adj, a, b, max_depth)
        if not path_units:
            continue
        first_edge = edges[(path_units[0], path_units[1])]
        trace = " -> ".join(path_units) if report_path else None
        msg = rule.message
        if trace:
            msg = f"{rule.message} [path: {trace}]"
        out.append(make_finding(rule, file=first_edge[0], line=first_edge[1],
                                unit=a, target=b, message=msg,
                                path_trace=trace,
                                exemptible=True,
                                spec_path=f"{rule.spec_path}.ignore_edges"))
    return out
