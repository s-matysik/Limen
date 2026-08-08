"""limen command-line interface (spec §6).

Exit codes: 0 no errors; 1 at least one error finding; 2 warnings only under
--strict; 3 invalid or inconsistent specification; 4 extractor failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from . import (EXIT_ERRORS, EXIT_EXTRACTOR_FAILURE, EXIT_INVALID_SPEC, EXIT_OK,
               __version__)
from .context import generate_context
from .engine import run_check
from .extract.base import ExtractorFailure, discover_files, get_extractor
from .facts.cache import FactsCache
from .facts.merge import merge_documents, resolve_units_layers
from .facts.model import FactsDocument, FactsIndex
from .graph import render as render_graph
from .report import FORMATS
from .review import changed_files, generate_review
from .rules import baseline as baseline_rule
from .spec.loader import load_schema, load_spec
from .spec.model import Spec, SpecError
from .spec.validate import (iter_rule_exemptions, validate_document,
                            validate_integrity)
from .util import parse_iso_date, posix


def _today(args) -> date:
    raw = getattr(args, "today", None) or os.environ.get("LIMEN_TODAY")
    return parse_iso_date(raw) if raw else date.today()


def _repo_root(args) -> Path:
    return Path(getattr(args, "workspace", None) or ".").resolve()


def _load_spec_or_exit(args) -> Spec:
    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = _repo_root(args) / spec_path
    try:
        spec = load_spec(spec_path)
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        raise SystemExit(EXIT_INVALID_SPEC)
    schema_issues = validate_document(spec.raw, load_schema("architecture-spec-1.json"))
    if schema_issues:
        for issue in schema_issues:
            print(issue.render(), file=sys.stderr)
        raise SystemExit(EXIT_INVALID_SPEC)
    return spec


def gather_facts(spec: Spec, repo_root: Path, facts_files=None,
                 roots_filter=None, use_cache: bool = True) -> FactsIndex:
    docs: list[FactsDocument] = []
    if facts_files:
        for f in facts_files:
            docs.append(FactsDocument.from_json(
                json.loads(Path(f).read_text(encoding="utf-8"))))
        return merge_documents(docs)
    for name in sorted(spec.roots):
        if roots_filter and name not in roots_filter:
            continue
        root = spec.roots[name]
        extractor = get_extractor(root.language,
                                  (spec.extractors or {}).get(root.language))
        files = discover_files(repo_root, root)
        cache = FactsCache(repo_root, name) if use_cache else None
        docs.append(extractor.extract(root, files, repo_root=repo_root,
                                      cache=cache))
        if cache:
            cache.save()
    return merge_documents(docs)


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #

def cmd_check(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    try:
        index = gather_facts(spec, repo_root, facts_files=args.facts)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE

    changed = None
    if args.changed_only:
        changed = set(changed_files(args.changed_only, str(repo_root)))
    only = set(args.only.split(",")) if args.only else None
    try:
        report = run_check(spec, index, repo_root=repo_root, today=_today(args),
                           changed_paths=changed, only=only,
                           strict_spec=args.strict_spec)
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        return EXIT_INVALID_SPEC

    if args.baseline_update:
        _write_baseline_update(spec, index, repo_root, args)

    sys.stdout.write(FORMATS[args.format](report, strict=args.strict))
    return report.exit_code(strict=args.strict)


def _write_baseline_update(spec, index, repo_root, args) -> None:
    from .rules.base import FileTree, RuleContext
    ctx = RuleContext(spec=spec, index=index, today=_today(args),
                      tree=FileTree(repo_root), repo_root=repo_root)
    updates = {}
    for rule in spec.rules:
        if rule.kind != "baseline-snapshot":
            continue
        expected: dict[str, list[str]] = {}
        for path, target, _line, _unit in baseline_rule.actual_pairs(rule, ctx):
            expected.setdefault(path, []).append(target)
        updates[rule.id] = {k: sorted(set(v)) for k, v in sorted(expected.items())}
    sidecar = Path(str(spec.source_path or "architecture.yaml")
                   + ".baseline-update.yaml")
    sidecar.write_text(yaml.safe_dump(updates, sort_keys=True,
                                      allow_unicode=True), encoding="utf-8")
    print(f"baseline snapshot written to {sidecar} — review and merge it into "
          f"the spec's expected: blocks", file=sys.stderr)


def cmd_extract(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    roots = {args.root} if args.root else None
    if args.compare:
        return _cmd_extract_compare(spec, repo_root, roots, args)
    try:
        index = gather_facts(spec, repo_root, roots_filter=roots)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE
    payload = [doc.to_json() for doc in index.documents]
    text = json.dumps(payload if len(payload) != 1 else payload[0],
                      sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    total_files = sum(len(d.files) for d in index.documents)
    total_unresolved = sum(len(d.unresolved) for d in index.documents)
    print(f"extracted {total_files} file(s), unresolved: {total_unresolved}",
          file=sys.stderr)
    return EXIT_OK


def _cmd_extract_compare(spec, repo_root, roots, args) -> int:
    engines = [e.strip() for e in args.compare.split(",") if e.strip()]
    results = {}
    for engine in engines:
        docs = []
        for name in sorted(spec.roots):
            if roots and name not in roots:
                continue
            root = spec.roots[name]
            cfg = dict((spec.extractors or {}).get(root.language) or {})
            cfg["engine"] = engine
            try:
                extractor = get_extractor(root.language, cfg)
                files = discover_files(repo_root, root)
                docs.append(extractor.extract(root, files, repo_root=repo_root))
            except (ExtractorFailure, NotImplementedError) as exc:
                print(f"{engine}: {exc}", file=sys.stderr)
                return EXIT_EXTRACTOR_FAILURE
        results[engine] = {(f.path, d.target)
                           for doc in docs for f in doc.files for d in f.deps}
    a, b = engines[0], engines[1]
    only_a = sorted(results[a] - results[b])
    only_b = sorted(results[b] - results[a])
    both = len(results[a] & results[b])
    print(f"deps found by both engines: {both}")
    print(f"only by {a}: {len(only_a)} (likely false positives: comments/strings)")
    print(f"only by {b}: {len(only_b)} (missed by {a}: multiline/grouped forms)")
    for path, target in only_a[:50]:
        print(f"  {a}-only {path} -> {target}")
    for path, target in only_b[:50]:
        print(f"  {b}-only {path} -> {target}")
    return EXIT_OK


def cmd_spec(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    if args.spec_cmd == "validate":
        index = None
        if not args.no_facts:
            try:
                index = gather_facts(spec, repo_root, facts_files=args.facts)
                resolve_units_layers(spec, index)
            except ExtractorFailure as exc:
                print(f"(facts unavailable, structural checks only: {exc})",
                      file=sys.stderr)
            except SpecError as exc:
                for issue in exc.issues:
                    print(issue.render())
                return EXIT_INVALID_SPEC
        issues = validate_integrity(spec, index=index,
                                    strict_spec=args.strict_spec,
                                    today=_today(args))
        for issue in issues:
            print(issue.render())
        if any(i.level == "error" for i in issues):
            return EXIT_INVALID_SPEC
        print(f"spec OK: {len(spec.rules)} rule(s), "
              f"{len(spec.roots)} root(s)"
              + (f", {len(issues)} warning(s)" if issues else ""))
        return EXIT_OK

    if args.spec_cmd == "explain":
        rule = spec.rule_by_id(args.rule_id)
        if rule is None:
            print(f"no such rule: {args.rule_id}", file=sys.stderr)
            return EXIT_INVALID_SPEC
        block = {"id": rule.id, "kind": rule.kind, "severity": rule.severity,
                 "message": rule.message, "remedy": rule.remedy,
                 "scope": rule.scope, **rule.params}
        sys.stdout.write(yaml.safe_dump(block, sort_keys=False,
                                        allow_unicode=True))
        return EXIT_OK

    if args.spec_cmd == "stats":
        return _cmd_spec_stats(spec, args)
    return EXIT_OK


def _cmd_spec_stats(spec: Spec, args) -> int:
    today = _today(args)
    kinds: dict[str, int] = {}
    for r in spec.rules:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    print("rules per kind:")
    for kind in sorted(kinds):
        print(f"  {kind}: {kinds[kind]}")
    total = active = expired = 0
    lifetimes: list[int] = []
    ages: list[int] = []
    for r in spec.rules:
        try:
            entries = list(iter_rule_exemptions(r))
        except SpecError:
            continue
        for _n, _i, ex, _f in entries:
            if not (ex.reason or ex.expires or ex.owner or ex.ticket
                    or ex.since or ex.scope):
                continue  # a bare allowlist string is a rule parameter, not debt
            total += 1
            if ex.status(today) == "expired":
                expired += 1
            else:
                active += 1
            if ex.since and ex.expires:
                lifetimes.append((ex.expires - ex.since).days)
            if ex.since:
                ages.append((today - ex.since).days)

    def median(xs: list[int]) -> str:
        if not xs:
            return "n/a (add `since:` to exemptions to measure)"
        xs = sorted(xs)
        mid = len(xs) // 2
        val = xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2
        return f"{val:g} days"

    print(f"exemptions: {total} total, {active} active, {expired} expired")
    print(f"  median age: {median(ages)}")
    print(f"  median declared lifetime (expires - since): {median(lifetimes)}")
    return EXIT_OK


def cmd_baseline(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    try:
        index = gather_facts(spec, repo_root, facts_files=args.facts)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE
    try:
        resolve_units_layers(spec, index)
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        return EXIT_INVALID_SPEC
    from .rules.base import FileTree, RuleContext
    ctx = RuleContext(spec=spec, index=index, today=_today(args),
                      tree=FileTree(repo_root), repo_root=repo_root)

    def rule_or_exit(rule_id):
        rule = spec.rule_by_id(rule_id)
        if rule is None or rule.kind != "baseline-snapshot":
            print(f"no baseline-snapshot rule with id {rule_id!r}",
                  file=sys.stderr)
            raise SystemExit(EXIT_INVALID_SPEC)
        return rule

    if args.baseline_cmd == "capture":
        rule = rule_or_exit(args.rule_id)
        expected: dict[str, list[str]] = {}
        for path, target, _l, _u in baseline_rule.actual_pairs(rule, ctx):
            expected.setdefault(path, []).append(target)
        sys.stdout.write(yaml.safe_dump(
            {"expected": {k: sorted(set(v)) for k, v in sorted(expected.items())}},
            sort_keys=True, allow_unicode=True))
        return EXIT_OK

    if args.baseline_cmd == "diff":
        rule = rule_or_exit(args.rule_id)
        actual = {(p, t) for p, t, _l, _u in baseline_rule.actual_pairs(rule, ctx)}
        declared = set(baseline_rule.parse_expected(rule).keys())
        for p, t in sorted(actual - declared):
            print(f"+ {p} -> {t}   (in code, not declared)")
        for p, t in sorted(declared - actual):
            print(f"- {p} -> {t}   (declared, gone from code)")
        if actual == declared:
            print("baseline matches the code exactly")
        return EXIT_OK

    if args.baseline_cmd == "prune":
        for rule in spec.rules:
            if rule.kind != "baseline-snapshot":
                continue
            actual = {(p, t) for p, t, _l, _u
                      in baseline_rule.actual_pairs(rule, ctx)}
            for key, ex in sorted(baseline_rule.parse_expected(rule).items()):
                if key not in actual:
                    print(f"[{rule.id}] dead entry: {key[0]} -> {key[1]} "
                          f"({ex.spec_path})")
        return EXIT_OK
    return EXIT_OK


def cmd_context(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    try:
        index = gather_facts(spec, repo_root, facts_files=args.facts)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE
    paths = list(args.paths or [])
    verify = "limen check --changed-only HEAD"
    if args.for_diff:
        paths.extend(changed_files(args.for_diff, str(repo_root)))
        verify = f"limen check --changed-only {args.for_diff}"
    if not paths:
        print("nothing to do: give --paths or --for-diff", file=sys.stderr)
        return EXIT_INVALID_SPEC
    try:
        md = generate_context(spec, index, paths, today=_today(args),
                              budget_tokens=args.budget_tokens,
                              verify_command=verify,
                              repo_root=str(repo_root))
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        return EXIT_INVALID_SPEC
    if args.format == "json":
        from .util import token_estimate
        sys.stdout.write(json.dumps(
            {"paths": sorted({posix(p) for p in paths}), "context": md,
             "token_estimate": token_estimate(md)},
            sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(md)
    return EXIT_OK


def cmd_review(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    try:
        index = gather_facts(spec, repo_root, facts_files=args.facts)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE
    paths = changed_files(args.for_diff, str(repo_root))
    try:
        md = generate_review(spec, index, paths, today=_today(args),
                             repo_root=str(repo_root), rev_range=args.for_diff)
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        return EXIT_INVALID_SPEC
    sys.stdout.write(md)
    return EXIT_OK


def cmd_graph(args) -> int:
    spec = _load_spec_or_exit(args)
    repo_root = _repo_root(args)
    try:
        index = gather_facts(spec, repo_root, facts_files=args.facts)
        resolve_units_layers(spec, index)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        return EXIT_INVALID_SPEC
    sys.stdout.write(render_graph(index, args.format))
    return EXIT_OK


def cmd_init(args) -> int:
    repo_root = _repo_root(args)
    roots: dict[str, dict] = {}
    if (repo_root / "composer.json").exists():
        path = "app" if (repo_root / "app").is_dir() else "src"
        roots["backend"] = {"path": path, "language": "php"}
    if (repo_root / "package.json").exists() and (repo_root / "src").is_dir():
        roots["frontend"] = {"path": "src", "language": "js"}
    if ((repo_root / "pom.xml").exists()
            or (repo_root / "build.gradle").exists()
            or (repo_root / "build.gradle.kts").exists()):
        for candidate in ("src/main/java", "src"):
            base = repo_root / candidate
            if base.is_dir() and any(base.rglob("*.java")):
                roots["java"] = {"path": candidate, "language": "java"}
                break
    if (repo_root / "manage.py").exists():
        # Django convention: apps either at the repository root or under apps/
        path = "apps" if (repo_root / "apps").is_dir() else "."
        roots["python"] = {"path": path, "language": "python"}
    if "python" not in roots and (
            (repo_root / "pyproject.toml").exists()
            or (repo_root / "setup.py").exists()):
        for candidate in ("src", "."):
            base = repo_root / candidate
            if base.is_dir() and any(p.suffix == ".py" for p in base.rglob("*.py")):
                roots["python"] = {"path": candidate if candidate != "." else "src",
                                   "language": "python"}
                break
    if not roots:
        roots["main"] = {"path": "src", "language": "python"}

    discovery, declared = [], set()
    for name, r in sorted(roots.items()):
        base = repo_root / r["path"]
        for module_dir in ("Modules", "modules"):
            probe = base / module_dir
            if probe.is_dir():
                discovery.append({"root": name,
                                  "pattern": f"{r['path']}/{module_dir}/{{unit}}/**"})
                declared |= {p.name for p in sorted(probe.iterdir()) if p.is_dir()}
                break
        else:
            if base.is_dir():
                subdirs = [p.name for p in sorted(base.iterdir())
                           if p.is_dir() and not p.name.startswith((".", "_"))]
                if subdirs:
                    discovery.append({"root": name,
                                      "pattern": f"{r['path']}/{{unit}}/**"})
                    declared |= set(subdirs)

    skeleton = {
        "version": 1,
        "project": repo_root.name,
        "roots": roots,
        "units": {"discovery": discovery,
                  "declared": sorted(declared),
                  "registry_policy": "exact"},
        "public_surface": {
            "php": ["{unit}/Application/Contracts/**", "{unit}/Domain/**"],
            "js": ["{unit}/index.{js,jsx,ts,tsx}"],
            "python": ["{unit}/api.py", "{unit}/__init__.py"],
        },
        "unresolved_policy": {"default": "warn"},
        "rules": [
            {"id": "cross-unit-public-api-only", "kind": "public-api-only",
             "severity": "error", "exempt": [],
             "message": "Cross-unit dependencies must resolve to the target "
                        "unit's public surface.",
             "remedy": "Import the unit's declared entrypoint instead of its "
                       "internals."},
            {"id": "file-size", "kind": "metric-threshold",
             "severity": "warning", "metric": "lines", "max": 600,
             "scope": {"include": ["**/*"]},
             "message": "File exceeds the review threshold.",
             "remedy": "Extract helpers or submodules."},
        ],
    }
    text = yaml.safe_dump(skeleton, sort_keys=False, allow_unicode=True)
    out_path = repo_root / "architecture.yaml"
    if out_path.exists():
        sys.stdout.write(text)
        print("architecture.yaml already exists; skeleton printed to stdout",
              file=sys.stderr)
    else:
        out_path.write_text(text, encoding="utf-8")
        print(f"wrote {out_path}", file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="limen",
                                     description=__doc__)
    parser.add_argument("--version", action="version",
                        version=f"limen {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p, nested=False):
        # In nested subparsers the defaults must be SUPPRESS: argparse applies
        # a subparser's defaults after the parent has parsed, so a real default
        # here would clobber `limen spec --spec X validate`.
        d = (lambda v: argparse.SUPPRESS) if nested else (lambda v: v)
        p.add_argument("--spec", default=d("architecture.yaml"))
        p.add_argument("--workspace", default=d(None),
                       help="directory containing the repo(s); enables "
                            "multi-repository specs with roots[].repo")
        p.add_argument("--facts", action="append", default=d(None),
                       help="pre-extracted arch-facts JSON (repeatable)")
        p.add_argument("--today", default=d(None),
                       help="freeze the clock (ISO date) for exemption expiry")

    p = sub.add_parser("check", help="run the contract; exit 0/1/2/3/4")
    common(p)
    p.add_argument("--format", choices=sorted(FORMATS), default="human")
    p.add_argument("--strict", action="store_true",
                   help="warnings alone exit 2")
    p.add_argument("--strict-spec", action="store_true",
                   help="dead spec entries become errors (exit 3)")
    p.add_argument("--only", default=None, help="comma-separated rule ids")
    p.add_argument("--changed-only", metavar="REV_RANGE", default=None)
    p.add_argument("--baseline-update", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("extract", help="emit arch-facts JSON")
    common(p)
    p.add_argument("--root", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--compare", default=None, metavar="ENGINE,ENGINE",
                   help="e.g. regex,treesitter — agreement table")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("spec", help="validate | explain | stats")
    common(p)
    spec_sub = p.add_subparsers(dest="spec_cmd", required=True)
    v = spec_sub.add_parser("validate")
    common(v, nested=True)
    v.add_argument("--strict-spec", action="store_true")
    v.add_argument("--no-facts", action="store_true",
                   help="structural checks only, skip extraction")
    e = spec_sub.add_parser("explain")
    common(e, nested=True)
    e.add_argument("rule_id")
    st = spec_sub.add_parser("stats")
    common(st, nested=True)
    p.set_defaults(func=cmd_spec, strict_spec=False, no_facts=False)

    p = sub.add_parser("baseline", help="capture | diff | prune")
    common(p)
    b = p.add_subparsers(dest="baseline_cmd", required=True)
    for name in ("capture", "diff"):
        bp = b.add_parser(name)
        common(bp, nested=True)
        bp.add_argument("rule_id")
    common(b.add_parser("prune"), nested=True)
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("context", help="scoped contract for a coding agent")
    common(p)
    p.add_argument("--paths", nargs="*", default=None)
    p.add_argument("--for-diff", metavar="REV_RANGE", default=None)
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--budget-tokens", type=int, default=None)
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("review", help="checklist for a verifying agent")
    common(p)
    p.add_argument("--for-diff", metavar="REV_RANGE", required=True)
    p.add_argument("--format", choices=["md"], default="md")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("graph", help="unit dependency graph")
    common(p)
    p.add_argument("--format", choices=["dot", "json", "mermaid"],
                   default="dot")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("init", help="skeleton spec from an existing repo")
    p.add_argument("--detect", action="store_true", default=True)
    p.add_argument("--workspace", default=None)
    p.set_defaults(func=cmd_init)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except ExtractorFailure as exc:
        print(f"extractor failure: {exc}", file=sys.stderr)
        return EXIT_EXTRACTOR_FAILURE
    except SpecError as exc:
        for issue in exc.issues:
            print(issue.render(), file=sys.stderr)
        return EXIT_INVALID_SPEC
    return code


if __name__ == "__main__":
    raise SystemExit(main())
