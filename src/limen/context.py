"""``limen context`` — the scoped contract generator for a coding agent.

From the same specification that gates CI, produce a Markdown fragment
narrowed to the paths the agent is about to touch (spec §6.1):

1. resolve unit/layer for each path;
2. select only the rules that *can* fire on those paths — narrowing, not a
   full dump;
3. expand every rule into concrete sentences, with contract class names that
   actually exist in the extracted facts (an agent must not invent a name);
4. attach the active exemptions touching those paths, with reason/expires;
5. attach the exact verification command;
6. under ``--budget-tokens``, trim from the bottom: warnings before errors —
   an ``error`` rule is never dropped while any ``warning`` remains; if the
   budget cannot even hold the errors, emit the compact ``id + remedy`` list
   and mark the truncation.

Deterministic byte-for-byte; covered by ``test_context_is_deterministic`` and
``test_context_mentions_only_existing_symbols``.
"""
from __future__ import annotations

import re

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .facts.merge import resolve_units_layers
from .facts.model import FactsIndex
from .rules.base import RuleContext, Scope, FileTree
from .rules.public_api import is_public
from .spec.model import Exemption, Rule, Spec
from .spec.validate import iter_rule_exemptions
from .util import glob_to_regex, posix, token_estimate

SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class PathInfo:
    path: str
    root: Optional[str]
    unit: Optional[str]
    layer: Optional[str]


def _classify_paths(spec: Spec, index: FactsIndex, paths: list[str]) -> list[PathInfo]:
    out = []
    unit_res = [(d, glob_to_regex(d.pattern)) for d in spec.unit_discovery]
    layer_res = [(d, glob_to_regex(d.pattern)) for d in spec.layer_discovery]
    for p in sorted({posix(x) for x in paths}):
        known = index.by_path.get(p)
        if known is not None:
            out.append(PathInfo(p, known.root, known.unit, known.layer))
            continue
        root = None
        for r in sorted(spec.roots.values(), key=lambda r: -len(r.prefix)):
            # A repository-level root has an empty prefix and owns every path.
            if not r.prefix or p == r.prefix or p.startswith(r.prefix.rstrip("/") + "/"):
                root = r.name
                break
        unit = layer = None
        for disc, rx in unit_res:
            if disc.root is not None and disc.root != root:
                continue
            m = rx.match(p)
            if m and m.groupdict().get("unit"):
                unit = m.group("unit")
                break
        for disc, rx in layer_res:
            m = rx.match(p)
            if m and m.groupdict().get("layer"):
                layer = m.group("layer")
                break
        out.append(PathInfo(p, root, unit, layer))
    return out


def _rule_applies(rule: Rule, infos: list[PathInfo], spec: Spec) -> int:
    """Coverage: how many of the given paths this rule can fire on (0 = skip)."""
    scope = Scope(rule.scope)
    n = 0
    for info in infos:
        if rule.kind == "ownership":
            aset = spec.artifact_sets.get(rule.params.get("artifact_set", ""))
            if aset and glob_to_regex(aset.discovery.pattern).match(info.path):
                n += 1
            continue
        if rule.kind == "reachability":
            participants = set(rule.params.get("units") or []) \
                | set(rule.params.get("order") or [])
            forbid = rule.params.get("forbid") or {}
            participants |= {str(forbid.get("from", "")), str(forbid.get("to", ""))}
            if info.unit and info.unit in participants \
                    and scope.path_matches(info.path, info.root):
                n += 1
            continue
        if rule.kind == "structure":
            assertion = rule.params.get("assert") or {}
            root = assertion.get("root") or (assertion.get("for_each_unit") or {}).get("root")
            if root is None or info.root == root:
                n += 1
            continue
        if rule.kind == "required-inbound":
            # It covers a path when the path is in scope (the artefact side) or
            # is itself the designated exporter -- editing the exporter is how
            # the obligation gets discharged, so the rule is relevant there too.
            froms = [str(x) for x in rule.params.get("from") or []]
            hit = scope.path_matches(info.path, info.root) \
                or any(glob_to_regex(x).match(info.path) for x in froms)
            if not hit and not info.path.endswith(".py"):
                # A directory request (with or without a trailing slash) is how
                # an agent asks before creating a file that does not exist yet:
                # exactly the case this kind is for.  A file glob such as
                # `pkg/est/*.py` never matches the bare directory, so without
                # this branch the obligation was silently absent from the
                # context precisely when it mattered (measured: 19 of 19 tasks
                # rendered "(none)").
                d = info.path.rstrip("/") + "/"
                hit = any(x.startswith(d) for x in froms) \
                    or any(g.startswith(d) for g in scope.paths) \
                    or _dir_request_matches(scope, info)
            if hit:
                n += 1
            continue
        if rule.kind == "layer-dependency":
            if info.layer == (rule.params.get("forbid") or {}).get("from_layer") \
                    and scope.path_matches(info.path, info.root):
                n += 1
            continue
        if scope.path_matches(info.path, info.root):
            n += 1
        elif not info.path.endswith(".py") and _dir_request_matches(scope, info):
            n += 1
    return n


def _dir_request_matches(scope: Scope, info: PathInfo) -> bool:
    """A directory request asks what applies to a file the agent is about to
    create there.  File-glob scopes (`pkg/**/*.py`) never match the bare
    directory, so every such rule was silently absent from the context of a
    directory request (measured: 102 of 102 code rules over 37 real tasks).
    Probe with a hypothetical new module directly in that directory."""
    probe = info.path.rstrip("/") + "/_limen_new_module.py"
    return scope.path_matches(probe, info.root)


def _public_symbols_by_unit(ctx: RuleContext) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in ctx.index.files:
        if f.unit is None or not is_public(ctx, f):
            continue
        symbols = sorted(f.declares) or [f.path]
        out.setdefault(f.unit, [])
        for s in symbols:
            if s not in out[f.unit]:
                out[f.unit].append(s)
    for unit in out:
        out[unit].sort()
    return out


def _is_path_target(target) -> bool:
    t = str(target or "")
    return "/" in t or t.endswith(".py")


def _path_covers(requested: str, target: str) -> bool:
    req = posix(requested).rstrip("/")
    tgt = posix(target)
    if "*" in tgt:
        return bool(glob_to_regex(tgt).match(req)) or tgt.startswith(req + "/")
    return tgt == req or tgt.startswith(req + "/")


def _same_dir(requested: str, target: str) -> bool:
    req = posix(requested)
    if not req.endswith(".py") and "." not in req.rsplit("/", 1)[-1]:
        return False
    return req.rsplit("/", 1)[0] == posix(target).rsplit("/", 1)[0]


def _summarise_exemptions(lines: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for line in lines:
        m = re.match(r"- \[([^\]]+)\]", line)
        rid = m.group(1) if m else "?"
        counts[rid] = counts.get(rid, 0) + 1
    return [f"- [{rid}] {n} existing file(s) exempted as pre-existing state; "
            f"new code must still comply" for rid, n in sorted(counts.items())]


def _fmt_exemption(ex: Exemption) -> str:
    bits = []
    if ex.reason:
        bits.append(ex.reason)
    if ex.expires:
        bits.append(f"expires {ex.expires.isoformat()}")
    if ex.ticket:
        bits.append(ex.ticket)
    suffix = f" ({'; '.join(bits)})" if bits else ""
    scope = f" in `{ex.scope}`" if ex.scope else ""
    return f"`{ex.target}`{scope}{suffix}"


def _expand(rule: Rule, ctx: RuleContext, infos: list[PathInfo],
            public_by_unit: dict[str, list[str]]) -> list[str]:
    p = rule.params
    lines: list[str] = []
    path_units = sorted({i.unit for i in infos if i.unit})

    if rule.kind == "layer-dependency":
        forbid = p.get("forbid") or {}
        roots = rule.scope.get("roots")
        where = f" (roots: {', '.join(roots)})" if roots else ""
        lines.append(f"Files in layer `{forbid.get('from_layer')}` must not "
                     f"depend on layer `{forbid.get('to_layer')}`{where}.")
    elif rule.kind == "public-api-only":
        src = ", ".join(f"`{u}`" for u in path_units) or "these paths"
        lines.append(f"From {src}, another unit may be reached only through "
                     f"its public surface. Existing public entrypoints:")
        for unit in sorted(public_by_unit):
            if unit in path_units:
                continue
            symbols = public_by_unit[unit]
            shown = ", ".join(f"`{s}`" for s in symbols[:4])
            more = f" (+{len(symbols) - 4} more)" if len(symbols) > 4 else ""
            lines.append(f"  - `{unit}` → {shown}{more}")
        lines.append("  Anything else inside another unit's directory is forbidden.")
    elif rule.kind == "consumer-allowlist":
        for target in sorted((p.get("allow") or {})):
            consumers = []
            for raw in p["allow"][target] or []:
                consumers.append(raw if isinstance(raw, str)
                                 else str(raw.get("unit") or raw.get("target")))
            owner = ctx.owner_of(p.get("artifact_set", ""), target)
            allowed = sorted(set(consumers) | ({owner} if owner else set()))
            verdicts = []
            for u in path_units:
                verdicts.append(f"`{u}` is {'allowed' if u in allowed else 'NOT allowed'}")
            verdict = f" {'; '.join(verdicts)} here." if verdicts else ""
            lines.append(f"`{target}` may be imported by: "
                         f"{', '.join(allowed)}.{verdict}")
    elif rule.kind == "ownership":
        owners = p.get("owners") or {}
        touched = [i.path for i in infos]
        members = ctx.artifact_members(p.get("artifact_set", ""))
        for artifact in sorted(members):
            if members[artifact].path in touched:
                lines.append(f"`{artifact}` is owned by unit "
                             f"`{owners.get(artifact, '?')}`.")
        lines.append("Every artifact in this set needs exactly one `owners:` entry.")
    elif rule.kind == "scoped-allowlist":
        allow = [a if isinstance(a, str) else str(a.get("target"))
                 for a in p.get("allow") or []]
        lines.append(f"From `{', '.join(rule.scope.get('paths', []))}` toward "
                     f"`{p.get('target_prefix')}*`, only: "
                     f"{', '.join(f'`{a}`' for a in sorted(allow)) or '(nothing)'}.")
    elif rule.kind == "forbidden-target":
        known = {s for f in ctx.index.files for s in f.declares}
        # backticks are reserved for symbols that verifiably exist in the
        # facts; spec-side names that resolve to nothing stay plain-quoted
        targets = ", ".join(f"`{t}`" if t in known else f"'{t}'"
                            for t in p.get("forbid") or [])
        lines.append(f"Never import: {targets}.")
    elif rule.kind == "baseline-snapshot":
        expected = p.get("expected") or {}
        n = sum(len(v or []) for v in expected.values())
        lines.append(f"Cross-unit imports matching `{p.get('target_pattern')}` "
                     f"must equal the declared baseline exactly "
                     f"(currently {n} declared exception(s); adding one "
                     f"requires an `expected:` entry with reason/owner/expires, "
                     f"and removing the import means removing the entry).")
    elif rule.kind == "pattern-ban":
        # The authored message carries the norm in the project's own words and
        # usually its provenance (a doc line reference).  Emitting only the raw
        # regex replaced that with something the reader has to decompile, and
        # measurement on a real contract showed it silently dropped the norm for
        # half the rules in scope.  Norm first, regex as the operational detail.
        if rule.message:
            lines.append(rule.message.strip())
        lines.append(f"Forbidden pattern in these files: `{p.get('pattern')}`.")
    elif rule.kind == "required-inbound":
        froms = ", ".join(f"`{x}`" for x in p.get("from") or []) or "(unset)"
        if rule.message:
            lines.append(rule.message.strip())
        lines.append(f"Anything you add under this scope must be referenced "
                     f"from {froms}; a file nothing there imports is a "
                     f"violation on that file.")
    elif rule.kind == "structure":
        assertion = p.get("assert") or {}
        if assertion.get("forbidden_dirs"):
            lines.append("Forbidden directories: "
                         + ", ".join(f"`{d}`" for d in assertion["forbidden_dirs"]) + ".")
        if assertion.get("for_each_unit") is not None:
            must = ", ".join(f"`{m}`" for m in assertion.get("must_match") or [])
            lines.append(f"Each unit keeps `{assertion.get('file', 'README.md')}` "
                         f"matching: {must}.")
        if assertion.get("paired_declaration"):
            pd = assertion["paired_declaration"]
            lines.append(f"Every exception declared in rule "
                         f"`{pd.get('source_rule')}` must also appear in "
                         f"`{pd.get('must_appear_in')}` — same commit.")
        if assertion.get("required_files"):
            lines.append("Required files: "
                         + ", ".join(f"`{x}`" for x in assertion["required_files"]) + ".")
    elif rule.kind == "metric-threshold":
        lines.append(f"Keep matching files at or under {p.get('max')} "
                     f"{p.get('metric', 'lines')}.")
    elif rule.kind == "reachability":
        mode = p.get("mode", "independence")
        if mode == "independence":
            units = ", ".join(f"`{u}`" for u in sorted(p.get("units") or []))
            lines.append(f"{units} must not reach each other, directly or "
                         f"transitively.")
        elif mode == "layers":
            lines.append("Layer order (top → bottom): "
                         + " → ".join(f"`{u}`" for u in p.get("order") or [])
                         + "; a lower unit must never reach a higher one, even "
                           "indirectly.")
        else:
            forbid = p.get("forbid") or {}
            lines.append(f"`{forbid.get('from')}` must not reach "
                         f"`{forbid.get('to')}`, directly or transitively.")
    if not lines:
        lines.append(rule.message)
    return lines


@dataclass
class Block:
    severity: str
    coverage: int
    order: int
    rule: Rule
    text: str
    compact: str


def generate_context(spec: Spec, index: FactsIndex, paths: list[str],
                     today: Optional[date] = None,
                     budget_tokens: Optional[int] = None,
                     verify_command: str = "limen check --changed-only HEAD",
                     repo_root: str = ".") -> str:
    today = today or date.today()
    if not any(f.unit is not None for f in index.files):
        resolve_units_layers(spec, index)
    ctx = RuleContext(spec=spec, index=index, today=today,
                      tree=FileTree(repo_root))
    infos = _classify_paths(spec, index, paths)
    public_by_unit = _public_symbols_by_unit(ctx)

    blocks: list[Block] = []
    exemption_lines: list[str] = []
    for rule in spec.rules:
        coverage = _rule_applies(rule, infos, spec)
        if coverage == 0:
            continue
        body = _expand(rule, ctx, infos, public_by_unit)
        text_lines = [f"- [{rule.id}] {body[0]}"]
        for extra in body[1:]:
            text_lines.append(extra if extra.startswith("  ") else f"  {extra}")
        if rule.remedy:
            text_lines.append(f"  Remedy: {rule.remedy}")
        compact = f"- [{rule.id}] Remedy: {rule.remedy or rule.message}"
        blocks.append(Block(rule.severity, coverage, rule.index, rule,
                            "\n".join(text_lines), compact))

        try:
            for _name, _i, ex, _flavor in iter_rule_exemptions(rule):
                if ex.status(today) != "active" or ex.target is None:
                    continue
                relevant = ex.scope is None or any(
                    glob_to_regex(ex.scope).match(i.path) for i in infos)
                if ex.scope is None and _is_path_target(ex.target):
                    # A file exemption is relevant only when that file is one of
                    # the requested paths or lies under a requested directory.
                    # Before, any exemption carrying a reason was listed for every
                    # request: a task in pkg/a/ received pre-existing debt from
                    # pkg/b/ (measured: up to 106 lines, most of the context).
                    relevant = any(_path_covers(i.path, str(ex.target))
                                   or _same_dir(i.path, str(ex.target))
                                   or (i.unit and f"/{i.unit}/" in "/" + posix(str(ex.target)))
                                   for i in infos)
                elif ex.scope is None:
                    relevant = any(
                        i.unit and ex.target and i.unit in str(ex.target)
                        for i in infos) or ex.reason is not None
                if relevant and (ex.reason or ex.expires or ex.ticket):
                    exemption_lines.append(f"- [{rule.id}] {_fmt_exemption(ex)}")
        except Exception:
            pass

    exemption_lines = sorted(set(exemption_lines))

    def assemble(active: list[Block], compact_errors: bool,
                 truncated: bool) -> str:
        hard = [b for b in active if b.severity == "error"]
        soft = [b for b in active if b.severity != "error"]
        out = ["## Architecture contract for the paths you are about to change",
               "",
               "Paths: " + ", ".join(i.path for i in infos),
               "",
               "### Hard rules (violating these fails CI)"]
        if hard:
            out.extend((b.compact if compact_errors else b.text) for b in hard)
        else:
            out.append("(none)")
        if soft:
            out.append("")
            out.append("### Advisory rules (warnings)")
            out.extend(b.text for b in soft)
        out.append("")
        out.append("### Active exemptions touching these paths")
        out.extend(exemption_lines or ["(none)"])
        if truncated:
            out.append("")
            out.append("(context truncated to fit the token budget; "
                       "run `limen spec explain <rule-id>` for full rules)")
        out.append("")
        out.append("### Verify before you report done")
        out.append(f"    {verify_command}")
        return "\n".join(out) + "\n"

    active = sorted(blocks, key=lambda b: (SEV_ORDER.get(b.severity, 9),
                                           -b.coverage, b.order))
    if budget_tokens is None:
        return assemble(active, compact_errors=False, truncated=False)

    truncated = False
    while token_estimate(assemble(active, False, truncated)) > budget_tokens:
        droppable = [b for b in active if b.severity != "error"]
        if not droppable:
            break
        # trim from the bottom: lowest-priority warning goes first
        victim = sorted(droppable, key=lambda b: (-SEV_ORDER.get(b.severity, 9),
                                                  b.coverage, -b.order))[0]
        active.remove(victim)
        truncated = True
    text = assemble(active, False, truncated)
    if token_estimate(text) > budget_tokens and len(exemption_lines) > 0:
        # Next cheapest cut: summarise exemptions per rule instead of listing
        # every file.  The agent only needs to know that pre-existing state is
        # tolerated; the rule text itself is what it must follow.
        exemption_lines = _summarise_exemptions(exemption_lines)
        truncated = True
        text = assemble(active, False, truncated)
    if token_estimate(text) > budget_tokens:
        # budget cannot even hold the errors: compact id + remedy list
        text = assemble([b for b in active if b.severity == "error"],
                        compact_errors=True, truncated=True)
    return text
