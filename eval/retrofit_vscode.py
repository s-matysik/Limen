#!/usr/bin/env python3
"""Study 1b, step 9 — bespoke retrofit #2: VS Code / `local/code-layering`.

VS Code enforces its runtime-environment layering with a **bespoke ESLint rule**
living in the repository itself (`.eslint-plugin-local/code-layering.ts`),
configured in `eslint.config.js`. The layers are directory names on the import
path — `common`, `browser`, `node`, `worker`, `electron-browser`,
`electron-utility`, `electron-main` — and the configuration states, per layer,
which other layers it may reach.

Native semantics, read off `.eslint-plugin-local/code-layering.ts` at the
pinned SHA (the rule is 92 lines; this is a complete statement of it, not a
summary):

1. The **source layer** is found by scanning the file's *directory* segments
   from the right and taking the first segment that is a key of the rule
   options. A file in no such directory is not checked at all.
2. `allowed` = that layer's configured list **plus the layer itself**;
   `disallowed` = every other configured key.
3. For each import, a relative specifier is resolved against the importing
   file's directory (`path.join`, purely lexical — no extension probing, no
   filesystem access). The resolved specifier's *directory* segments are then
   scanned **from the right**: the first segment that is in `allowed` ends the
   check (good), the first that is in `disallowed` reports a violation. A
   segment in neither set is skipped and the scan continues leftwards.
4. The rule listens on `import ... from '...'`, dynamic `import('...')`,
   `import x = require('...')`, `export * from '...'` and
   `export {x} from '...'` (`.eslint-plugin-local/utils.ts`).

limen mapping: this is exactly `K1 layer-dependency` — one rule per forbidden
(from_layer, to_layer) ordered pair, with layer discovery by directory segment.
The pairs are the complement: for each configured layer L, every configured
layer T not in `allowed(L)` yields one rule.

Two properties of the native rule cannot be reproduced by limen's layer model
and are recorded as deviations rather than silently absorbed:

D-VSC-1  Native layer resolution scans **right to left and skips unknown
         segments**, so `.../browser/foo/common/bar.ts` resolves to `common`
         (rightmost match), whereas limen's `layers.discovery` glob binds
         `{layer}` at a fixed position. The retrofit therefore declares one
         discovery pattern per layer position observed in the tree; files whose
         layer segment is not the last-but-one directory are counted and
         reported (`layer_position_mismatch`).

D-VSC-2  The native rule is `warn`, not `error`, and never fails CI on its own.
         Severity is carried through as `warning` so the comparison is
         severity-faithful.

The oracle: real ESLint runs the repository's own rule file. Where that is not
possible in this sandbox (the plugin is TypeScript loaded through the repo's
own toolchain, which needs its full `npm ci`), the script falls back to a
**re-implementation of the rule read off its source** and records
`oracle_kind="reimplemented-from-source"` — a weaker oracle, labelled as such,
never described as having run the tool.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

EVAL = Path(__file__).resolve().parent
SCHEMA = "bespoke-retrofit/1"

#: `local/code-layering` options, copied verbatim from eslint.config.js at the
#: pinned SHA. Checked against the live file by `verify_config`.
LAYERS: dict[str, list[str]] = {
    "common": [],
    "node": ["common"],
    "browser": ["common"],
    "electron-browser": ["common", "browser"],
    "electron-utility": ["common", "node"],
    "electron-main": ["common", "node", "electron-utility"],
}

#: Import forms the native rule listens on (utils.ts::createImportRuleListener).
_IMPORT_RX = [
    re.compile(r"""^\s*import\s[^;'"]*?\bfrom\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""^\s*import\s+\w+\s*=\s*require\s*\(\s*['"]([^'"]+)['"]\s*\)""", re.M),
    re.compile(r"""^\s*export\s+\*\s+from\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""^\s*export\s*\{[^}]*\}\s*from\s*['"]([^'"]+)['"]""", re.M | re.S),
]

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")


def strip_comments(text: str) -> str:
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return _LINE_COMMENT.sub(blank, _BLOCK_COMMENT.sub(blank, text))


def verify_config(repo: Path) -> dict[str, Any]:
    """Check LAYERS against the repository's own eslint.config.js.

    A hand-copied constant that has drifted from the source would silently
    change every number below, so the drift is detected here and reported.
    """
    text = (repo / "eslint.config.js").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"'local/code-layering'\s*:\s*\[\s*'(\w+)'\s*,\s*\{(.*?)\n\t*\}\s*\n\t*\]",
                  text, re.S)
    if not m:
        return {"verified": False, "reason": "code-layering block not found"}
    severity, body = m.group(1), m.group(2)
    found: dict[str, list[str]] = {}
    for km in re.finditer(r"'([\w-]+)'\s*:\s*\[([^\]]*)\]", body):
        found[km.group(1)] = re.findall(r"'([\w-]+)'", km.group(2))
    return {
        "verified": found == LAYERS,
        "native_severity": severity,
        "parsed_from_repo": found,
        "constant_in_script": LAYERS,
    }


# ==========================================================================
# Native rule, re-implemented from its source
# ==========================================================================
def layer_of_dir(dir_posix: str, keys: set[str]) -> str | None:
    """First configured segment scanning the directory right to left."""
    for part in reversed([p for p in dir_posix.split("/") if p]):
        if part in keys:
            return part
    return None


def native_decide(src_file: str, spec: str, layers: dict[str, list[str]]
                  ) -> tuple[str, str] | None:
    """Reproduce code-layering's decision for one import.

    Returns (source_layer, offending_layer) on violation, else None.
    Mirrors the rule exactly: resolve relative specifiers with a lexical join,
    then scan the target's directory segments right to left, stopping at the
    first `allowed` (good) or `disallowed` (violation) hit.
    """
    keys = set(layers)
    src_dir = posixpath.dirname(src_file)
    src_layer = layer_of_dir(src_dir, keys)
    if src_layer is None:
        return None
    allowed = set(layers[src_layer]) | {src_layer}
    disallowed = keys - allowed

    path = spec
    if path.startswith("."):
        path = posixpath.normpath(posixpath.join(src_dir, spec))
    for part in reversed([p for p in posixpath.dirname(path).split("/") if p]):
        if part in allowed:
            return None
        if part in disallowed:
            return src_layer, part
    return None


def scan_repo(root: Path, layers: dict[str, list[str]], subdir: str
              ) -> tuple[list[dict], dict[str, Any]]:
    """Run the re-implemented rule over every .ts file under `subdir`."""
    findings: list[dict] = []
    stats = {"files": 0, "files_in_a_layer": 0, "imports": 0}
    base = root / subdir
    for p in sorted(base.rglob("*.ts")):
        rel = p.relative_to(root).as_posix()
        stats["files"] += 1
        if layer_of_dir(posixpath.dirname(rel), set(layers)) is None:
            continue
        stats["files_in_a_layer"] += 1
        text = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        seen: set[tuple[int, str]] = set()
        for rx in _IMPORT_RX:
            for m in rx.finditer(text):
                spec = m.group(1)
                line = text.count("\n", 0, m.start(1)) + 1
                if (line, spec) in seen:
                    continue
                seen.add((line, spec))
                stats["imports"] += 1
                verdict = native_decide(rel, spec, layers)
                if verdict is None:
                    continue
                src_layer, bad = verdict
                findings.append({
                    "source": "native",
                    "native_rule_id": "local/code-layering",
                    "rule_id": "local/code-layering",
                    "severity": "warning",
                    "file": rel,
                    "line": line,
                    "target": spec,
                    "message": (f"Bad layering. You are not allowed to access "
                                f"{bad} from here, allowed layers are: "
                                f"[{', '.join(sorted(set(layers[src_layer]) | {src_layer}))}]"),
                    "from_layer": src_layer,
                    "to_layer": bad,
                })
    return findings, stats


# ==========================================================================
# Translation to a limen spec
# ==========================================================================
def build_spec(layers: dict[str, list[str]], subdir: str,
               layer_dirs: list[str]) -> tuple[dict, dict]:
    rules = []
    pairs = []
    for src in sorted(layers):
        allowed = set(layers[src]) | {src}
        for dst in sorted(set(layers) - allowed):
            pairs.append((src, dst))
            rules.append({
                "id": f"layer-{src}-not-{dst}".lower(),
                "kind": "layer-dependency",
                "severity": "warning",
                "forbid": {"from_layer": src, "to_layer": dst},
                "scope": {"roots": ["main"]},
                "message": (f"Bad layering. You are not allowed to access "
                            f"{dst} from here, allowed layers are: "
                            f"[{', '.join(sorted(allowed))}]"),
                "remedy": "Move the code, or reach the other layer through a "
                          "service interface declared in a common layer.",
            })
    spec = {
        "version": 1,
        "project": "vscode-code-layering-retrofit",
        "roots": {"main": {"path": subdir, "language": "js"}},
        "layers": {
            "discovery": [{"root": "main", "pattern": p} for p in layer_dirs],
            "declared": sorted(layers),
        },
        "unresolved_policy": {"default": "off"},
        "rules": rules,
    }
    return spec, {"forbidden_pairs": len(pairs),
                  "pairs": [f"{a}->{b}" for a, b in pairs],
                  "declared_layers": sorted(layers)}


def run_limen(spec_path: Path, workspace: Path, today: str) -> tuple[list[dict], dict]:
    cmd = [sys.executable, "-m", "limen.cli", "check", "--spec", str(spec_path),
           "--workspace", str(workspace), "--today", today, "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"limen output is not JSON: {e}\n{proc.stdout[:600]}"
                           f"\n{proc.stderr[:600]}")
    rows = [dict(f, source="limen") for f in doc.get("findings", [])]
    return rows, {"command": " ".join(cmd), "exit_code": proc.returncode,
                  "stderr_tail": proc.stderr[-500:]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--subdir", default="src/vs")
    ap.add_argument("--today", default="2026-08-04")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed-violations", type=int, default=0,
                    help="inject N known-illegal cross-layer imports into a "
                         "copy of the tree and check both sides flag them")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    cfg = verify_config(repo)
    print(f"config verified against eslint.config.js: {cfg['verified']} "
          f"(native severity: {cfg.get('native_severity')})")
    if not cfg["verified"]:
        print("  REFUSING to continue: the layer table in this script does not "
              "match the repository's own configuration", file=sys.stderr)
        (out / "retrofit.json").write_text(
            json.dumps({"schema": SCHEMA, "repo": "microsoft/vscode",
                        "aborted": "config drift", "config_check": cfg},
                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 1

    native, stats = scan_repo(repo, LAYERS, args.subdir)
    print(f"native (reimplemented): {len(native)} findings over "
          f"{stats['files_in_a_layer']}/{stats['files']} files, "
          f"{stats['imports']} imports")

    # Layer discovery. limen binds `{layer}` positionally: a discovery glob
    # matches once per file and, when several globs bind different segments,
    # the merge refuses the file as `layer-ambiguous`. So one glob is the only
    # option, and `src/vs/**/{layer}/**` binds the RIGHTMOST directory segment
    # (the `**/` prefix is greedy) — close to, but not the same as, the native
    # right-to-left scan, which *skips* segments that are not configured layers
    # and keeps walking left. The residual is D-VSC-1 and is measured below
    # rather than asserted away.
    patterns = [f"{args.subdir}/**/{{layer}}/**"]
    spec, treport = build_spec(LAYERS, args.subdir, patterns)
    treport["layer_discovery_patterns"] = patterns

    rx = re.compile(
        "^" + re.escape(args.subdir) + r"/(?:[^/]+/)*(?P<layer>[^/]+)/.*$")
    mismatch: dict[str, int] = {}
    n_files = n_native_layer = n_limen_layer = n_same = 0
    examples: list[dict] = []
    for p in sorted((repo / args.subdir).rglob("*.ts")):
        rel = p.relative_to(repo).as_posix()
        n_files += 1
        nat = layer_of_dir(posixpath.dirname(rel), set(LAYERS))
        m = rx.match(rel)
        lim = m.group("layer") if m else None
        if lim not in LAYERS:
            lim = None
        n_native_layer += nat is not None
        n_limen_layer += lim is not None
        if nat == lim:
            n_same += 1
        else:
            key = f"{nat or '-'} vs {lim or '-'}"
            mismatch[key] = mismatch.get(key, 0) + 1
            if len(examples) < 15:
                examples.append({"file": rel, "native_layer": nat,
                                 "limen_layer": lim})
    treport["layer_assignment"] = {
        "files": n_files,
        "native_assigns_a_layer": n_native_layer,
        "limen_assigns_a_layer": n_limen_layer,
        "identical_assignment": n_same,
        "differing_assignment": n_files - n_same,
        "by_pair_native_vs_limen": dict(sorted(mismatch.items())),
        "examples": examples,
        "deviation": (
            "D-VSC-1: the native rule scans directory segments right to left "
            "and SKIPS segments that are not configured layers; limen binds "
            "{layer} to the rightmost segment and then drops it if it is not "
            "declared. Files whose layer directory is not the immediate parent "
            "therefore get no layer in limen and are not checked."),
    }
    print(f"layer assignment: native {n_native_layer}, limen {n_limen_layer}, "
          f"identical {n_same}/{n_files}")

    spec_path = out / "architecture.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")
    print(f"limen spec: {len(spec['rules'])} K1 rules, "
          f"{len(patterns)} discovery patterns")

    limen_rows, limen_meta = run_limen(spec_path, repo, args.today)
    print(f"limen: exit {limen_meta['exit_code']}, {len(limen_rows)} findings")

    # ---- seeded-violation arm ------------------------------------------
    # VS Code's tree is clean at the pinned SHA (CI enforces the rule), so a
    # zero-vs-zero comparison measures false positives and nothing else.
    # Injecting known-illegal imports makes detection measurable. Injections
    # are restricted to files where BOTH sides assign the same layer, so the
    # arm measures rule agreement, not the D-VSC-1 discovery gap already
    # quantified above.
    seeded: dict[str, Any] = {"enabled": bool(args.seed_violations)}
    if args.seed_violations:
        import shutil
        targets_by_layer: dict[str, str] = {}
        for p in sorted((repo / args.subdir).rglob("*.ts")):
            rel = p.relative_to(repo).as_posix()
            nat = layer_of_dir(posixpath.dirname(rel), set(LAYERS))
            m = rx.match(rel)
            lim = m.group("layer") if m else None
            if nat is not None and nat == lim and nat not in targets_by_layer:
                targets_by_layer[nat] = rel
        candidates = []
        for p in sorted((repo / args.subdir).rglob("*.ts")):
            rel = p.relative_to(repo).as_posix()
            nat = layer_of_dir(posixpath.dirname(rel), set(LAYERS))
            m = rx.match(rel)
            lim = m.group("layer") if m else None
            if nat is None or nat != lim:
                continue
            forbidden = sorted(set(LAYERS) - (set(LAYERS[nat]) | {nat}))
            pick = next((t for t in forbidden if t in targets_by_layer), None)
            if pick is None:
                continue
            candidates.append({"rel_path": rel, "from_layer": nat,
                               "to_layer": pick,
                               "import_of": targets_by_layer[pick]})
            if len(candidates) >= args.seed_violations:
                break

        seed_root = out / "_seeded"
        if seed_root.exists():
            shutil.rmtree(seed_root)
        # Copy the whole subtree for real — a symlink would let the tools read
        # the pristine files and every injection would "pass".
        (seed_root / args.subdir).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(repo / args.subdir, seed_root / args.subdir)
        placed = []
        for c in candidates:
            p = seed_root / c["rel_path"]
            text = p.read_text(encoding="utf-8", errors="replace")
            spec_rel = posixpath.relpath(
                c["import_of"][:-3], posixpath.dirname(c["rel_path"]))
            if not spec_rel.startswith("."):
                spec_rel = "./" + spec_rel
            lines = text.split("\n")
            at = 0
            for i, ln in enumerate(lines):
                if ln.startswith(" *---") or ln.strip().startswith("import "):
                    at = i + 1
            lines.insert(at, f"import * as __seeded from '{spec_rel}';")
            p.write_text("\n".join(lines), encoding="utf-8")
            placed.append(dict(c, injected_line=at + 1, specifier=spec_rel))

        s_native, s_stats = scan_repo(seed_root, LAYERS, args.subdir)
        s_limen, s_limen_meta = run_limen(spec_path, seed_root, args.today)

        def hit(rows, inj):
            for r in rows:
                if (r.get("file") or "").replace("\\", "/") != inj["rel_path"]:
                    continue
                if r.get("line") == inj["injected_line"]:
                    return True
            return False

        detail = [{**{k: i[k] for k in ("rel_path", "from_layer", "to_layer",
                                        "specifier", "injected_line")},
                   "native_detected": hit(s_native, i),
                   "limen_detected": hit(s_limen, i)} for i in placed]
        seeded.update({
            "n_injections": len(placed),
            "native_detected": sum(1 for d in detail if d["native_detected"]),
            "limen_detected": sum(1 for d in detail if d["limen_detected"]),
            "both_detected": sum(1 for d in detail
                                 if d["native_detected"] and d["limen_detected"]),
            "native_findings_total": len(s_native),
            "limen_findings_total": len(s_limen),
            "limen_run": s_limen_meta,
            "injections": detail,
            "native_findings": s_native,
            "limen_findings": s_limen,
            "injection_policy": (
                "one injection per file, only in files where the native scan "
                "and limen's discovery glob assign the SAME layer, so this arm "
                "measures rule agreement and not the D-VSC-1 discovery gap"),
        })
        print(f"seeded: {len(placed)} injections — native "
              f"{seeded['native_detected']}, limen {seeded['limen_detected']}")

    payload = {
        "schema": SCHEMA,
        "seeded": seeded,
        "repo": "microsoft/vscode",
        "subdir": args.subdir,
        "native_config": "eslint.config.js + .eslint-plugin-local/code-layering.ts",
        "config_check": cfg,
        "oracle": {
            "tool": "local/code-layering (VS Code's own ESLint rule)",
            "oracle_kind": "reimplemented-from-source",
            "oracle_note": (
                "The rule is TypeScript loaded through the repository's own "
                "build toolchain, which needs a full `npm ci` of the VS Code "
                "repo. It was NOT executed here. The decision procedure was "
                "re-implemented from `.eslint-plugin-local/code-layering.ts` "
                "and `utils.ts` at the pinned SHA; the layer table is verified "
                "against `eslint.config.js` at run time (config_check)."),
            "source_files_read": [".eslint-plugin-local/code-layering.ts",
                                  ".eslint-plugin-local/utils.ts",
                                  "eslint.config.js"],
        },
        "translation": treport,
        "limen": limen_meta,
        "scan_stats": stats,
        "native_findings": native,
        "limen_findings": limen_rows,
    }
    (out / "retrofit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out / "retrofit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
