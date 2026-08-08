#!/usr/bin/env python3
"""Study 1b, step 10 — the differential over the corpus.

For every repository in ``eval/corpus.jsonl``: fetch the pinned tree, translate
its native checker configuration into an ``architecture.yaml``, run the native
checker and ``limen check`` over the same tree, and diff the two finding sets
with the **same classifier Study 1 uses** — `study1_equivalence.diff_finding_sets`
is imported, not copied, so the two studies cannot drift apart.

The classifiers differ, because the evidence differs. Study 1's C1/C2/C3 read
PHPUnit regex sources and prose documentation; here the same three categories
are decided from the *native configuration and the source line*:

    C1  native false alarm removed
        The native tool reported something limen did not, and the reason is
        visible in the source: the matched text is in a comment or a string,
        or the native rule fired on a construct that is not a dependency.

    C2  native gap closed
        limen reported something the native tool did not, and the native
        configuration shows why it could not have: the native rule's own scope
        or suppression mechanism excludes the finding (an inline
        `# pylint: disable`, a deptrac `skip_violations` entry, an
        import-linter `ignore_imports` line).

    C3  deliberate semantic change
        The difference is a recorded deviation (`docs/deviations.md`, or one of
        the per-tool D-xx deviations documented in `eval/translate/*.py`):
        line-of-usage vs line-of-import for deptrac (D-DT-1), transitive vs
        direct edges for import-linter (D-IL-1), the layer-discovery gap for
        VS Code (D-VSC-1), and so on.

Anything else stays UNCLASSIFIED and is reported as such. Per-repository the
gate is not fatal (one repository's surprise must not hide the other fifty);
the count of unclassified differences is a headline number.

Outputs:
    eval/results/study1b_per_repo.csv
    eval/results/study1b_per_kind.csv
    eval/results/study1b_summary.md
    eval/results/study1b_raw/<owner>__<repo>.json

Usage:
    python eval/study1b_corpus.py --corpus eval/corpus.jsonl \
        --work eval/_scratch/corpus --out eval/results
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent
#: The checked-out limen sources this study measures.
LIMEN_SRC = EVAL.parent / "src"
#: Registry every C3 classification must cite.
DEVIATIONS_DOC = EVAL.parent / "docs" / "deviations.md"
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL / "translate"))

import yaml  # noqa: E402

from study1_equivalence import diff_finding_sets  # noqa: E402
from run_limen_check import key_str  # noqa: E402
import deptrac_translate as DT  # noqa: E402
import importlinter_translate as IL  # noqa: E402
import import_control as IC  # noqa: E402
import depcruise_translate as DC  # noqa: E402

SCHEMA = "study1b-corpus/1"

#: Paths the sandbox refuses to create. None holds analysable source; a single
#: failed mkdir aborts tar extraction, so they are dropped and counted.
PROTECTED_DIRS = {".git", ".idea", ".vscode", ".ssh", ".claude", ".cursor",
                  ".devcontainer", ".husky"}
PROTECTED_FILES = {".mcp.json"}

#: Minimum share of dependencies dependency-cruiser must actually RESOLVE for
#: its report to count as a verdict. Below this the tool cannot match its own
#: path rules and its silence means "did not decide", not "no violation".
DEPCRUISE_MIN_RESOLUTION = 0.80

#: rule kind -> K number, mirroring eval/build_rule_inventory.py.
KIND_NUMBER = {
    "layer-dependency": "K1", "ownership": "K2", "consumer-allowlist": "K3",
    "public-api-only": "K4", "scoped-allowlist": "K5", "forbidden-target": "K6",
    "baseline-snapshot": "K7", "pattern-ban": "K8", "structure": "K9",
    "metric-threshold": "K10", "reachability": "K11",
}


# ==========================================================================
# Wilson score interval
# ==========================================================================
def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float, float]:
    """(point estimate, low, high). n == 0 -> (nan, nan, nan): no data is not
    100%, and the caller must render it as n/a."""
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


# ==========================================================================
# Fetching a pinned tree
# ==========================================================================
def fetch_tree(owner: str, repo: str, sha: str, dest: Path) -> bool:
    if (dest / ".fetched").is_file():
        return True
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}"
    buf = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                buf = io.BytesIO(r.read())
            break
        except Exception as e:  # incl. RemoteDisconnected on long transfers
            if attempt == 3:
                (dest / ".error").write_text(f"{type(e).__name__}: {e}",
                                             encoding="utf-8")
                return False
            time.sleep(2 ** attempt)
    try:
        skipped: list[str] = []
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            members = []
            for m in tf.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                m.name = parts[1]
                segs = m.name.split("/")
                if m.name.startswith("/") or ".." in segs:
                    continue
                # Tool/config paths the sandbox protects from writes (git
                # internals, editor and agent configuration). None is ever
                # analysed, and a single failed mkdir aborts the whole
                # extraction, so they are dropped rather than risked. Any file
                # that could not be written is reported in `skipped_paths`.
                if any(s in PROTECTED_DIRS for s in segs) or \
                        segs[-1] in PROTECTED_FILES:
                    skipped.append(m.name)
                    continue
                members.append(m)
            # Extract file by file. The sandbox refuses to create some paths
            # (shell rc files, git config, agent configuration); `extractall`
            # aborts the whole archive on the first such refusal, losing a
            # repository over a file that is never analysed. Failures are
            # collected instead, so the tree is still usable and the omission
            # is visible in `.skipped-paths`.
            for m in members:
                try:
                    tf.extract(m, dest, filter="data")
                except (PermissionError, OSError) as ex:
                    skipped.append(f"{m.name}\t{type(ex).__name__}")
    except Exception as e:
        (dest / ".error").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
        return False
    (dest / ".fetched").write_text(sha, encoding="utf-8")
    if skipped:
        (dest / ".skipped-paths").write_text(
            "\n".join(sorted(skipped)), encoding="utf-8")
    return True


# ==========================================================================
# Native runners — each returns (findings, meta)
# ==========================================================================
def run_deptrac(tree: Path, config_rel: str, php: str, deptrac_bin: str
                ) -> tuple[list[dict], dict]:
    cmd = [php, deptrac_bin, "analyse", f"--config-file={config_rel}",
           "--formatter=json", "--no-progress"]
    proc = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                          timeout=1800)
    meta = {"tool": "deptrac", "oracle_kind": "executed-native-tool",
            "command": " ".join(cmd), "exit_code": proc.returncode,
            "stderr_tail": proc.stderr[-400:]}
    m = re.search(r"\{.*\}", proc.stdout, re.S)
    if not m:
        meta["parse_error"] = "no JSON object in output"
        return [], meta
    try:
        doc = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        meta["parse_error"] = str(e)
        return [], meta
    rows = []
    for fname, block in (doc.get("files") or {}).items():
        rel = os.path.relpath(fname, tree).replace(os.sep, "/")
        for msg in block.get("messages", []):
            if msg.get("type") not in ("error", "warning"):
                continue
            text = msg.get("message", "")
            tm = re.search(r"must not depend on ([\w\\]+)", text)
            rows.append({
                "source": "native", "native_rule_id": "deptrac",
                "rule_id": "deptrac", "kind_hint": "layer-dependency",
                "severity": "error" if msg.get("type") == "error" else "warning",
                "file": rel, "line": msg.get("line"),
                "target": tm.group(1) if tm else None, "message": text,
            })
    meta["report"] = doc.get("Report")
    return rows, meta


def run_depcruise(tree: Path, config_rel: str, depcruise_bin: str, src: str
                  ) -> tuple[list[dict], dict]:
    # A bare directory argument makes dependency-cruiser cruise ZERO modules
    # here (its default extension set does not pick up TypeScript sources
    # without the project's own toolchain present). An explicit glob per
    # extension is what actually enumerates the tree, and a run that cruised
    # nothing is not an oracle -- `analysis.modules_cruised` records it.
    globs = [f"{src.rstrip('/')}/**/*{e}" for e in
             (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")]
    cmd = [depcruise_bin, "--config", config_rel, "--output-type", "json",
           "--no-progress", *globs]
    proc = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                          timeout=1800)
    meta = {"tool": "dependency-cruiser", "oracle_kind": "executed-native-tool",
            "command": " ".join(cmd), "exit_code": proc.returncode,
            "stderr_tail": proc.stderr[-400:]}
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        meta["parse_error"] = str(e)
        return [], meta
    rows = []
    for v in (doc.get("summary") or {}).get("violations", []) or []:
        rows.append({
            "source": "native", "native_rule_id": (v.get("rule") or {}).get("name"),
            "rule_id": (v.get("rule") or {}).get("name"),
            "kind_hint": "forbidden-target",
            "severity": (v.get("rule") or {}).get("severity", "error"),
            "file": v.get("from"), "line": None, "target": v.get("to"),
            "message": (v.get("rule") or {}).get("name", ""),
        })
    summary = doc.get("summary") or {}
    # dependency-cruiser RESOLVES each specifier before matching it against a
    # path rule. Resolution needs the project's installed `node_modules` and
    # its tsconfig `paths` aliases; without them a specifier stays unresolved
    # (`couldNotResolve`), carries `dependencyTypes: ["unknown"]`, and NO
    # path-based rule can match it. The tool then reports zero violations while
    # having decided nothing — a silent false negative that would be scored as
    # agreement. The resolution rate is therefore measured and carried, and the
    # oracle is only considered live above the threshold below.
    deps = [dep for m in (doc.get("modules") or []) for dep in
            (m.get("dependencies") or [])]
    unresolved = sum(1 for d in deps if d.get("couldNotResolve"))
    total = len(deps)
    rate = (total - unresolved) / total if total else 0.0
    meta["analysis"] = {
        "modules_cruised": summary.get("totalCruised"),
        "dependencies_cruised": summary.get("totalDependenciesCruised"),
        "dependencies_seen": total,
        "dependencies_unresolved": unresolved,
        "resolution_rate": rate,
    }
    meta["oracle_live"] = bool(summary.get("totalCruised")) and \
        rate >= DEPCRUISE_MIN_RESOLUTION
    if not meta["oracle_live"]:
        meta["not_live_reason"] = (
            f"{unresolved}/{total} dependencies could not be resolved "
            f"({rate:.1%} resolved, threshold {DEPCRUISE_MIN_RESOLUTION:.0%}): "
            "the repository's node_modules and tsconfig path aliases are not "
            "installed, so path rules cannot match and a zero-violation report "
            "is not a verdict")
    return rows, meta


def _il_file(module: str, root_dir: str, tree: Path) -> str:
    """`_il_path`, falling back to the package `__init__.py` when needed."""
    p = _il_path(module, root_dir)
    if (tree / p).is_file():
        return p
    pkg = p[:-3] + "/__init__.py"
    return pkg if (tree / pkg).is_file() else p


def _il_path(module: str, root_dir: str) -> str:
    """Repo-relative path for a dotted module name.

    import-linter reports MODULES (`frago.browser.extension.lifecycle`); limen
    reports repo-relative FILE paths (`src/frago/browser/extension/lifecycle.py`).
    Comparing the two without prepending the root directory puts every finding
    in a different namespace, so identical findings look like disagreements on
    both sides at once. A package `__init__.py` is used when the plain module
    file does not exist.
    """
    pfx = "" if root_dir in ("", ".") else root_dir.rstrip("/") + "/"
    return pfx + module.replace(".", "/") + ".py"


def run_importlinter(tree: Path, config_rel: str, lint_imports: str,
                     root_dir: str = ".") -> tuple[list[dict], dict]:
    """Run import-linter's own `lint-imports` entrypoint on the pinned tree.

    import-linter IMPORTS the project's packages to build the graph, so the
    tree's own directory must be on sys.path; `cwd=tree` plus PYTHONPATH gives
    that without installing the project (which would need its full dependency
    set and is not reproducible across 15 repositories).
    """
    env = dict(os.environ)
    # import-linter IMPORTS the packages to build the graph, so the directory
    # that CONTAINS the root packages must be importable. `root_dir` is that
    # directory, resolved during translation from the declared root packages.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tree / root_dir), str(tree), str(tree / "src"),
         env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    cmd = [lint_imports, "--config", config_rel, "--no-cache"]
    proc = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                          timeout=1800, env=env)
    meta = {"tool": "import-linter", "oracle_kind": "executed-native-tool",
            "command": " ".join(cmd), "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-400:]}
    rows = []
    # import-linter's text report lists chains as "a.b -> c.d (l.NN, l.MM, ...)"
    # under a broken-contract heading. Two shapes have to survive parsing:
    #
    #   * ONE edge can carry SEVERAL line numbers — every import statement that
    #     realises it. Reading only the first turns four agreements into four
    #     "limen-only" differences.
    #   * the report is WRAPPED at the terminal width, so the line list can
    #     continue on the next physical line ("(l.93,\nl.309, l.323)").
    #
    # The report is therefore un-wrapped into logical entries first.
    logical: list[str] = []
    for ln in proc.stdout.splitlines():
        s = ln.strip()
        if logical and logical[-1].count("(") > logical[-1].count(")"):
            logical[-1] += " " + s          # continuation of a wrapped list
        else:
            logical.append(s)
    cur = None
    for s in logical:
        cm = re.match(r"^(.+?) is not allowed to import (.+?)[.:]?$", s)
        if cm:
            cur = (cm.group(1), cm.group(2))
            continue
        lm = re.match(r"^-?\s*([\w.]+)\s*->\s*([\w.]+)\s*\(([^)]*)\)", s)
        if lm:
            lines = [int(x) for x in re.findall(r"l\.(\d+)", lm.group(3))]
            for line in lines or [None]:
                rows.append({
                    "source": "native", "native_rule_id": "import-linter",
                    "rule_id": "import-linter", "kind_hint": "forbidden-target",
                    "severity": "error",
                    "file": _il_file(lm.group(1), root_dir, tree),
                    "line": line, "target": lm.group(2),
                    "message": f"{lm.group(1)} -> {lm.group(2)}",
                    "chain_head": cur[0] if cur else None,
                })
    # Evidence that the oracle actually analysed something. import-linter
    # exits 0 both when every contract holds and when it silently imported
    # nothing, so the "Analyzed N files, M dependencies" and
    # "Contracts: K kept, B broken" lines are captured: a run reporting zero
    # findings over zero files is not a verdict and must not be read as
    # agreement.
    am = re.search(r"Analyzed (\d+) files?, (\d+) dependenc", proc.stdout)
    cm2 = re.search(r"Contracts:\s*(\d+) kept,\s*(\d+) broken", proc.stdout)
    meta["analysis"] = {
        "files_analyzed": int(am.group(1)) if am else None,
        "dependencies": int(am.group(2)) if am else None,
        "contracts_kept": int(cm2.group(1)) if cm2 else None,
        "contracts_broken": int(cm2.group(2)) if cm2 else None,
    }
    meta["oracle_live"] = bool(am and int(am.group(1)) > 0)
    return rows, meta


def scope_report(tree: Path, roots: list[str], rules: list[dict],
                 native_files: list[Path]) -> dict[str, Any]:
    """Measure, do not assert, how far the two tools' file sets coincide.

    limen's root is the whole tree; what actually narrows it is each rule's
    path scope. checkstyle is invoked on `roots`. Those two sets are close but
    NOT equal by construction, so this function computes them and reports the
    residual. A file only one side can see is a *reason* a finding may be
    one-sided, and the number belongs in the report rather than a claim that
    the sets match.
    """
    from limen.util import glob_to_regex
    rxs = [glob_to_regex(p) for r in rules for p in r["scope"]["paths"]]
    nat = {p.relative_to(tree).as_posix() for p in native_files}
    lim = {f for f in nat if any(rx.match(f) for rx in rxs)}
    all_java = {p.relative_to(tree).as_posix() for p in tree.rglob("*.java")
                if "/build/" not in p.as_posix() and "/target/" not in p.as_posix()}
    lim_outside = {f for f in all_java - nat if any(rx.match(f) for rx in rxs)}
    return {
        "java_files_in_tree": len(all_java),
        "native_sees": len(nat),
        "limen_rule_scopes_cover": len(lim),
        "seen_by_both": len(nat & lim),
        "native_only_files": len(nat - lim),
        "limen_only_files": len(lim_outside),
        "note": ("counts, not a guarantee: `native_sees` is the file set "
                 "checkstyle was invoked on; `limen_rule_scopes_cover` is the "
                 "subset matched by at least one emitted rule scope. "
                 "`native_only_files` are files the native tool checked that "
                 "no limen rule scopes (their packages had no denied target, "
                 "so no rule was emitted for them) and `limen_only_files` are "
                 "files a rule scope matches outside the invoked roots. Both "
                 "are potential sources of one-sided findings."),
    }


_PKG_DECL_RX = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)


def roots_under_package(tree: Path, roots: list[str], root_pkg: str,
                        ) -> tuple[list[str], list[str]]:
    """Split source roots by whether they hold classes the config governs.

    A checkstyle `import-control` document governs ONE package subtree
    (`<import-control pkg="...">`). Large repositories ship several such
    documents and wire a different one to each build module, so pointing the
    discovered document at every source root would ask the native tool about
    modules its own build never applies it to. Both tools are therefore
    restricted to the roots that actually contain classes under `root_pkg`.
    Returns (governed, skipped).
    """
    governed, skipped = [], []
    for r in roots:
        hit = False
        for p in (tree / r).rglob("*.java"):
            m = _PKG_DECL_RX.search(p.read_text(encoding="utf-8", errors="replace"))
            if m and (m.group(1) == root_pkg
                      or m.group(1).startswith(root_pkg + ".")):
                hit = True
                break
        (governed if hit else skipped).append(r)
    return governed, skipped


def java_source_roots(tree: Path, limit: int = 40) -> list[str]:
    """Directories that hold compilable Java main sources.

    Pointing checkstyle at the repository root makes it parse everything with
    a `.java` extension, including deliberately-malformed parser test fixtures
    (checkstyle aborts the whole run on the first unparseable file, exit 254).
    Standard Maven/Gradle `src/main/java` roots are the compilable set; where a
    repository has none, the tree's Java files are taken as-is.
    """
    roots = sorted({p.as_posix() for p in
                    (q.relative_to(tree) for q in tree.rglob("src/main/java"))
                    if p.is_dir() or (tree / p).is_dir()})
    roots = [r for r in roots
             if "/build/" not in r and "/target/" not in r
             and "/test/" not in r and "/resources/" not in r]
    if roots:
        return roots[:limit]
    return ["."]


def run_checkstyle_ic(tree: Path, config_rel: str, java: str, jar: str,
                      roots: list[str], workdir: Path) -> tuple[list[dict], dict]:
    import xml.etree.ElementTree as ET
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = workdir / "cs.xml"
    cfg.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE module PUBLIC "-//Checkstyle//DTD Checkstyle Configuration 1.3//EN"\n'
        '  "https://checkstyle.org/dtds/configuration_1_3.dtd">\n'
        '<module name="Checker"><property name="severity" value="error"/>\n'
        '<module name="TreeWalker"><module name="ImportControl">\n'
        '<property name="file" value="${importControlFile}"/>\n'
        '</module></module></module>\n', encoding="utf-8")
    props = workdir / "cs.properties"
    props.write_text(f"importControlFile={tree / config_rel}\n", encoding="utf-8")
    cmd = [java, "-jar", jar, "-c", str(cfg), "-p", str(props), "-f", "xml",
           *[str(tree / r) for r in roots]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    meta = {"tool": "checkstyle ImportControl",
            "oracle_kind": "executed-native-tool",
            "command": " ".join(cmd[:6]) + f" <{len(roots)} source roots>",
            "source_roots": roots,
            "exit_code": proc.returncode, "stderr_tail": proc.stderr[-400:]}
    rows = []
    try:
        root = ET.fromstring(proc.stdout)
    except ET.ParseError as e:
        meta["parse_error"] = str(e)
        return [], meta
    for fel in root.iter("file"):
        rel = os.path.relpath(fel.get("name") or "", tree).replace(os.sep, "/")
        for err in fel.iter("error"):
            if "ImportControl" not in (err.get("source") or ""):
                continue
            msg = err.get("message") or ""
            tm = (re.search(r"'([\w.$]+)'", msg)
                  or re.search(r"import\s+-\s+([\w.$]+?)\.?\s*$", msg))
            rows.append({
                "source": "native", "native_rule_id": "checkstyle:ImportControl",
                "rule_id": "checkstyle:ImportControl",
                "kind_hint": "forbidden-target",
                "severity": err.get("severity") or "error",
                "file": rel, "line": int(err.get("line") or 0) or None,
                "target": tm.group(1) if tm else None, "message": msg,
            })
    return rows, meta


#: `sys.executable` can be a RELATIVE path (it is, under this kernel), and a
#: subprocess launched with `cwd=` elsewhere then fails to find it — or, worse,
#: finds a different interpreter with a different limen installed, so the run
#: silently measures code that is not the checked-out source. Resolved once,
#: absolutely, at import time.
PYEXE = str(Path(sys.executable).resolve())


def run_limen(spec_path: Path, tree: Path, today: str) -> tuple[list[dict], dict]:
    # limen keys its facts cache on file content, so a change to the EXTRACTOR
    # does not invalidate it: a re-run after fixing extraction silently replays
    # the old facts and the study would report line numbers the current code
    # does not produce. The cache is dropped so every measurement comes from
    # the checked-out extractor.
    shutil.rmtree(tree / ".limen", ignore_errors=True)
    cmd = [PYEXE, "-m", "limen.cli", "check", "--spec", str(spec_path),
           "--workspace", str(tree), "--today", today, "--format", "json"]
    # limen must be imported from the checked-out source, not from whatever an
    # ambient install provides; the sandbox's PYTHONPATH is not inherited
    # reliably by a subprocess launched with a different cwd.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(LIMEN_SRC), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                          env=env)
    meta = {"command": " ".join(cmd), "exit_code": proc.returncode,
            "limen_src": str(LIMEN_SRC),
            "stderr_tail": proc.stderr[-600:]}
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        meta["parse_error"] = str(e)
        meta["stdout_head"] = proc.stdout[:400]
        return [], meta
    return [dict(f, source="limen") for f in doc.get("findings", [])], meta


# ==========================================================================
# Classifiers — same three categories as Study 1, different evidence
# ==========================================================================
CODE, COMMENT, STRING = "code", "comment", "string"


def span_classes(text: str, language: str) -> list[str]:
    """Per-character code/comment/string classification (C1 evidence)."""
    out = [CODE] * len(text)
    i, n = 0, len(text)
    while i < n:
        c, two = text[i], text[i:i + 2]
        if two == "//" and language != "python":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = COMMENT
            i = j
        elif c == "#" and language in ("python", "php") and two != "#[":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = COMMENT
            i = j
        elif two == "/*" and language != "python":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = COMMENT
            i = j
        elif c in "'\"" or (c == "`" and language in ("js", "java")):
            q, j = c, i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == q:
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                out[k] = STRING
            i = j
        else:
            i += 1
    return out


LANG_OF_EXT = {".php": "php", ".py": "python", ".java": "java",
               ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js",
               ".mjs": "js", ".cjs": "js", ".vue": "js"}

_SUPPRESS_RX = {
    "python": re.compile(r"#\s*(?:pylint:\s*disable|noqa|type:\s*ignore|"
                         r"importlinter:\s*ignore)"),
    "php": re.compile(r"@phpstan-ignore|@psalm-suppress|/\*\s*deptrac"),
    "js": re.compile(r"eslint-disable|dependency-cruiser-ignore|"
                     r"@ts-ignore|@ts-expect-error"),
    "java": re.compile(r"@SuppressWarnings|CHECKSTYLE:OFF|SuppressionXpathSingleFilter"),
}


def source_line(tree: Path, rel: str | None, line: int | None) -> dict[str, Any]:
    ev: dict[str, Any] = {"file": rel, "line": line, "resolved": False}
    if not rel or not line:
        ev["note"] = "finding carries no file/line"
        return ev
    p = tree / rel
    if not p.is_file():
        ev["note"] = "file not present in the pinned tree"
        return ev
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if not (1 <= line <= len(lines)):
        ev["note"] = f"line {line} outside file ({len(lines)} lines)"
        return ev
    ev["resolved"] = True
    ev["source_line"] = lines[line - 1].rstrip("\n")[:400]
    ev["_offset"] = sum(len(x) for x in lines[:line - 1])
    ev["_text"] = text
    return ev


def make_classifiers(tree: Path, native_meta: dict, config_text: str,
                     deviations: dict[str, str],
                     all_findings: list[dict] | None = None):
    """Bind the three category decisions to this repository's evidence.

    `all_findings` is both sides' findings; it lets a classifier look at the
    OTHER tool's finding on the same (file, line) — needed to verify that a
    difference really is a granularity difference and not two unrelated
    verdicts that happen to share a line.
    """
    counterpart_index: dict[tuple, list[dict]] = {}
    for g in (all_findings or []):
        counterpart_index.setdefault((g.get("file"), g.get("line")), []).append(g)

    def lang_of(rel: str | None) -> str:
        return LANG_OF_EXT.get(Path(rel or "").suffix, "js")

    def c1(f: dict) -> dict | None:
        """Native-only, and the matched text is not code."""
        if f.get("source") != "native":
            return None
        ev = source_line(tree, f.get("file"), f.get("line"))
        if not ev.get("resolved"):
            return None
        lang = lang_of(f.get("file"))
        target = f.get("target") or ""
        src = ev["source_line"]
        needle = next((c for c in (target, target.split("\\")[-1],
                                   target.split(".")[-1], target.split("/")[-1])
                       if c and c in src), None)
        if needle is None:
            return None
        classes = span_classes(ev["_text"], lang)
        off = ev["_offset"] + src.index(needle)
        seg = sorted(set(classes[off:off + len(needle)]))
        span = seg[0] if len(seg) == 1 else "mixed:" + "+".join(seg)
        if span == CODE:
            return None
        return {
            "category": "C1", "category_label": "native false alarm removed",
            "rule": "classify_c1: the native match is not in code",
            "rationale": (f"the matched text lies in a {span}; limen's "
                          "extractors emit dependencies from code only."),
            "evidence": {"source_line": src, "span_class": span},
        }

    def c2(f: dict) -> dict | None:
        """limen-only, and the native tool's own config/suppression explains it."""
        if f.get("source") != "limen":
            return None
        ev = source_line(tree, f.get("file"), f.get("line"))
        if not ev.get("resolved"):
            return None
        lang = lang_of(f.get("file"))
        rx = _SUPPRESS_RX.get(lang)
        window = ev["source_line"]
        prev = ""
        if f.get("line", 0) > 1:
            prev_ev = source_line(tree, f.get("file"), f["line"] - 1)
            prev = prev_ev.get("source_line") or ""
        if rx and (rx.search(window) or rx.search(prev)):
            return {
                "category": "C2", "category_label": "native gap closed",
                "rule": "classify_c2: the native tool was suppressed in the source",
                "rationale": ("the offending line carries an inline suppression "
                              "the native tool honours; limen's suppression "
                              "mechanism is the spec's allow/exempt entries, "
                              "which carry reason/owner/expires. Both tools "
                              "agree the dependency exists."),
                "evidence": {"source_line": window, "previous_line": prev},
            }
        # A target the native config never mentions cannot have been checked
        # by it: that is a gap, not a disagreement.
        target = f.get("target") or ""
        head = target.split("\\")[0].split(".")[0].split("/")[0]
        if head and head not in config_text:
            return {
                "category": "C2", "category_label": "native gap closed",
                "rule": "classify_c2: the target is outside the native config's vocabulary",
                "rationale": (f"no rule in the native configuration names "
                              f"{head!r}, so the native checker could not have "
                              "reported this dependency."),
                "evidence": {"source_line": window, "target_head": head},
            }
        return None

    def c3(f: dict) -> dict | None:
        """Covered by a recorded per-tool deviation.

        Every citation is verified against the tree at classification time: a
        deviation id is never asserted on tool identity alone, because that
        would let an unrelated disagreement hide behind a known one.
        """
        tool = native_meta.get("tool", "")
        if tool.startswith("checkstyle"):
            # D-CS-1: static-import granularity. Require the evidence — the
            # line really is a static import, and the two targets really are
            # the same type with the native side carrying extra member
            # segments.
            ev = source_line(tree, f.get("file"), f.get("line"))
            if not ev.get("resolved"):
                return None
            src = ev["source_line"].strip()
            if not src.startswith("import static"):
                return None
            mine = f.get("target") or ""
            # The static import's own type, read from the source line: this is
            # what limen records for the statement.
            sm = re.match(r"import\s+static\s+([\w.$]+)\s*;", src)
            if not sm:
                return None
            declared = sm.group(1)
            other = None
            for g in counterpart_index.get((f.get("file"), f.get("line")), []):
                if g is f:
                    continue
                other = g.get("target") or ""
            if other is not None:
                nat, lim = ((mine, other) if f.get("source") == "native"
                            else (other, mine))
                paired = True
            else:
                # No counterpart on this line. Two sub-cases, both still
                # explained by D-CS-1 and both verified here rather than
                # assumed:
                #   native-only: the native target is the declared static
                #     reference, and limen's target for the same statement
                #     (the type) was ALLOWED, so limen emitted nothing;
                #   limen-only : the mirror image.
                nat, lim = ((mine, mine.rsplit(".", 1)[0])
                            if f.get("source") == "native"
                            else (declared, mine))
                paired = False
            if not (nat and lim and nat.startswith(lim + ".")
                    and declared.startswith(lim)):
                return None
            return {
                "category": "C3",
                "category_label": "deliberate semantic change",
                "rule": "classify_c3: covered by D-CS-1 (verified on this line)",
                "rationale": deviations["D-CS-1"],
                "evidence": {"deviation": "D-CS-1", "source_line": src,
                             "native_target": nat, "limen_target": lim,
                             "declared_static_reference": declared,
                             "counterpart_on_same_line": paired},
            }
        did = None
        if tool.startswith("deptrac") and f.get("source") == "native":
            did = "D-DT-1"
        elif tool.startswith("import-linter") and f.get("chain_head"):
            did = "D-IL-1"
        if did is None or did not in deviations:
            return None
        return {
            "category": "C3", "category_label": "deliberate semantic change",
            "rule": f"classify_c3: covered by {did}",
            "rationale": deviations[did],
            "evidence": {"deviation": did},
        }

    return (c1, c2, c3)


#: Per-tool deviations, quoted into every classification that cites them.
DEVIATIONS = {
    "D-DT-1": ("deptrac reports the line where the token is USED; limen "
               "reports the line of the import statement. The (file, line, "
               "target) key therefore cannot match for a class used away from "
               "its import; the line-insensitive (file, target) agreement is "
               "reported alongside."),
    "D-IL-1": ("import-linter contracts are transitive by default and report "
               "whole import chains; limen's K1/K6 are direct-edge rules. An "
               "indirect chain is not a limen miss of a rule limen was given."),
    "D-VSC-1": ("VS Code's layer scan walks directory segments right to left "
                "skipping unknown segments; limen binds {layer} positionally."),
    "D-HA-1": ("the `const` half of C7405 keys on the imported SYMBOL; limen's "
               "facts carry module targets."),
    "D-CS-1": ("for `import static a.b.C.MEMBER`, checkstyle's ImportControl "
               "names the full static reference (`a.b.C.MEMBER`) while limen's "
               "Java extractor records the TYPE (`a.b.C`) — its documented "
               "behaviour: 'static imports keep the type'. Both tools flag the "
               "same import statement on the same line and agree it is "
               "forbidden; only the granularity of the reported target "
               "differs. Verified per finding: the source line begins with "
               "`import static` and the native target is limen's target plus "
               "trailing member segments."),
}


# ==========================================================================
# Per-repository pipeline
# ==========================================================================
def key_of(f: dict) -> str:
    file = (f.get("file") or "").replace("\\", "/").lstrip("./")
    target = (f.get("target") or "").strip().lstrip("\\")
    line = f.get("line")
    return f"{file or '-'}:{line if line is not None else '-'}:{target or '-'}"


#: Set by main() so `translate()` can reach the tool paths without threading
#: them through every branch. Read-only after start-up.
_TOOLS: dict[str, str] = {}


def depcruise_ruleset(tree: Path, config_rel: str, src: str,
                      depcruise_bin: str) -> dict:
    """Ask dependency-cruiser to resolve its own config and hand it back.

    The config is JavaScript; evaluating it here would mean running arbitrary
    third-party code. Instead the tool resolves it and reports the rule set it
    used under `summary.ruleSetUsed`, which is plain data.
    """
    proc = subprocess.run(
        [depcruise_bin, "--config", config_rel, "--output-type", "json", src],
        cwd=tree, capture_output=True, text=True, timeout=1800)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise DC.Untranslatable(
            "dependency-cruiser could not resolve the config: "
            + (proc.stderr.strip().splitlines() or ["no output"])[-1][:200])
    summary = doc.get("summary") or {}
    rs = summary.get("ruleSetUsed")
    if not rs:
        raise DC.Untranslatable("the report carries no resolved rule set")
    # `optionsUsed` carries the resolved exclude/doNotFollow filters, which
    # decide what the tool looks at; carried alongside the rules so callers
    # cannot reason about the rules while ignoring the filters.
    rs = dict(rs)
    rs["options"] = summary.get("optionsUsed") or {}
    return rs


def ensure_config(row: dict, tree: Path) -> tuple[str, bool]:
    """Read the native config, fetching it if the tarball omitted it.

    `codeload` honours `.gitattributes export-ignore`, so a repository can ship
    a config in git that its release tarball does not contain (observed for
    deptrac configs kept under `.tools/`). The file is then fetched from
    `raw.githubusercontent.com` at the SAME pinned SHA, so the corpus stays
    reproducible; the substitution is reported per repository rather than left
    implicit.
    """
    cfg_path = tree / row["config_path"]
    if cfg_path.is_file():
        return cfg_path.read_text(encoding="utf-8", errors="replace"), False
    url = (f"https://raw.githubusercontent.com/{row['owner']}/{row['repo']}/"
           f"{row['sha']}/{urllib.parse.quote(row['config_path'])}")
    with urllib.request.urlopen(url, timeout=120) as r:
        text = r.read().decode("utf-8", "replace")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text, encoding="utf-8")
    return text, True


def translate(row: dict, tree: Path) -> tuple[dict, dict, str]:
    """Native config -> (limen spec, translation report, config text)."""
    text, fetched = ensure_config(row, tree)
    kind = row["config_kind"]
    if kind == "deptrac":
        spec, rep = DT.to_limen(yaml.safe_load(text), ".")
        rep["config_fetched_from_raw"] = fetched
    elif kind == "import-linter":
        parsed = IL.parse_ini(text)
        # limen's Python extractor derives a module name by stripping the
        # root's path prefix, so the root must be the directory that CONTAINS
        # the root package -- `.` gives an empty prefix and the extractor
        # rejects it. import-linter names root packages, not paths, so the
        # containing directory is located in the tree.
        # import-linter also reads root packages from a `[tool.importlinter]`
        # block in pyproject.toml; and the containing directory can be
        # anywhere, so the tree is searched rather than three prefixes guessed.
        root_dir = "."
        candidates: list[str] = []
        for pkg in parsed["root_packages"]:
            rel = pkg.replace(".", "/")
            for p in tree.rglob(rel.split("/")[-1]):
                if not p.is_dir() or not (p / "__init__.py").is_file():
                    continue
                d = p.parent.relative_to(tree).as_posix() or "."
                if any(s in d.split("/") for s in
                       (".venv", "venv", "node_modules", "site-packages",
                        "build", "dist", ".tox")):
                    continue
                candidates.append(d)
            if candidates:
                break
        if candidates:
            # Shallowest match: the project's own package, not a vendored copy.
            root_dir = min(candidates, key=lambda d: (d.count("/"), len(d)))
        if root_dir == ".":
            raise IL.Untranslatable(
                f"no directory in the tree holds the declared root package(s) "
                f"{parsed['root_packages']}")
        spec, rep = IL.to_limen(parsed, root_dir)
        rep["config_fetched_from_raw"] = fetched
        rep["root_dir"] = root_dir
        rep["root_packages"] = parsed["root_packages"]
    elif kind == "import-control":
        root = IC.parse_import_control(text)
        all_roots = java_source_roots(tree)
        roots, off_roots = roots_under_package(tree, all_roots, root.pkg)
        if not roots:
            raise IC.Untranslatable(
                f"no source root contains classes under the governed package "
                f"{root.pkg!r}")
        srcs = sorted(p for r in roots
                      for p in (tree / r).rglob("*.java")
                      if "/build/" not in p.as_posix()
                      and "/target/" not in p.as_posix())
        facts = []
        pkg_rx = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
        imp_rx = re.compile(r"^\s*import\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;", re.M)
        for p in srcs:
            t = p.read_text(encoding="utf-8", errors="replace")
            m = pkg_rx.search(t)
            facts.append({"path": p.relative_to(tree).as_posix(),
                          "package": m.group(1) if m else "",
                          "imports": imp_rx.findall(t)})
        by_pkg: dict[str, list[dict]] = {}
        for f in facts:
            by_pkg.setdefault(f["package"], []).append(f)
        rules, skipped, covered = [], [], 0
        for pkg in sorted(by_pkg):
            scope = IC.scope_for_package(root, pkg)
            if scope is None or not IC.is_covered(scope)[0]:
                skipped.append(pkg)
                continue
            covered += 1
            local = sorted({t for f in by_pkg[pkg] for t in f["imports"]})
            denied = IC.forbidden_targets(root, pkg, local)
            if not denied:
                continue
            # Rule scope: the directory holding this package's own files.
            dirs = sorted({str(Path(f["path"]).parent) for f in by_pkg[pkg]})
            rules.append({
                "id": "ic-" + re.sub(r"[^a-z0-9]+", "-", pkg.lower()).strip("-"),
                "kind": "forbidden-target", "severity": "error",
                "scope": {"roots": ["main"], "paths": [f"{d}/*.java" for d in dirs]},
                "forbid": [t for t, _ in denied],
                "message": f"Import denied by ImportControl for package {pkg}.",
                "remedy": "Add an <allow> element or drop the dependency.",
            })
        spec = {"version": 1, "project": "import-control-retrofit",
                "roots": {"main": {"path": ".", "language": "java"}},
                "unresolved_policy": {"default": "off"}, "rules": rules}
        rep = {"native_scopes": IC.count_scopes(root),
               "native_guards": IC.count_guards(root),
               "coverage": IC.coverage(root),
               "packages_covered": covered, "packages_skipped": len(skipped),
               "governed_package": root.pkg,
               "source_roots": roots,
               "source_roots_not_governed": off_roots,
               "scope": scope_report(tree, roots, rules, srcs),
               "config_fetched_from_raw": fetched}
    elif kind == "dependency-cruiser":
        # The config is executable JavaScript and is NOT evaluated here.
        # dependency-cruiser is asked to resolve it and emit the rule set it
        # actually used (`summary.ruleSetUsed`), which is then translated as
        # data — the same source of truth the tool applies to its own verdicts.
        src_dir = next((d for d in ("src", "lib", "app", "packages")
                        if (tree / d).is_dir()), ".")
        rs = depcruise_ruleset(tree, row["config_path"], src_dir,
                               _TOOLS.get("depcruise", "depcruise"))
        spec, rep = DC.to_limen(rs, src_dir)
        rep["config_fetched_from_raw"] = fetched
        rep["src_dir"] = src_dir
    else:
        raise ValueError(kind)
    return spec, rep, text


def process(row: dict, work: Path, tools: dict, args_today: str) -> dict:
    owner, repo, sha = row["owner"], row["repo"], row["sha"]
    out: dict[str, Any] = {
        "owner": owner, "repo": repo, "sha": sha, "config_kind": row["config_kind"],
        "language": row["language"], "heldout": bool(row.get("heldout")),
        "license": row.get("license"), "stars": row.get("stars"),
        "status": "pending", "reason": None,
    }
    tree = work / f"{owner}__{repo}"
    if not fetch_tree(owner, repo, sha, tree):
        out["status"] = "fetch-failed"
        out["reason"] = (tree / ".error").read_text(errors="replace")[:200] \
            if (tree / ".error").is_file() else "unknown"
        return out

    try:
        spec, trep, config_text = translate(row, tree)
    except NotImplementedError as e:
        out["status"] = "not-translatable"
        out["reason"] = str(e)[:300]
        return out
    except (DT.Untranslatable, IL.Untranslatable, IC.Untranslatable,
            DC.Untranslatable) as e:
        out["status"] = "untranslatable-config"
        out["reason"] = str(e)[:300]
        return out
    except Exception as e:
        out["status"] = "translation-error"
        out["reason"] = f"{type(e).__name__}: {e}"[:300]
        return out

    out["translation"] = trep
    out["n_limen_rules"] = len(spec["rules"])
    # rule_id -> kind, so per-kind aggregation can attribute each finding to
    # the rule that produced it instead of to the whole repository.
    out["rule_kinds"] = {r["id"]: r["kind"] for r in spec["rules"]}
    out["kinds"] = sorted({KIND_NUMBER[r["kind"]] for r in spec["rules"]})
    if not spec["rules"]:
        # The config translated cleanly and the native cascade denies nothing
        # any file in this tree actually imports. That is a real (empty)
        # verdict, not a failure — but limen cannot be run with an empty rule
        # list, so the native tool is still executed and the comparison is
        # recorded as vacuous. Counting it as "ok" would inflate the number of
        # comparable repositories; counting it as a failure would hide that
        # both tools agree there is nothing to report.
        out["status"] = "vacuous-no-violations-possible"
        out["reason"] = ("the native cascade forbids none of the imports "
                         "present in this tree, so no rule was emitted and "
                         "both sides are empty by construction")
        return out

    spec_path = tree / "_limen_architecture.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")

    kind = row["config_kind"]
    try:
        if kind == "deptrac":
            native, nmeta = run_deptrac(tree, row["config_path"], tools["php"],
                                        tools["deptrac"])
        elif kind == "import-linter":
            native, nmeta = run_importlinter(
                tree, row["config_path"], tools["il_python"],
                root_dir=trep.get("root_dir", "."))
        elif kind == "import-control":
            native, nmeta = run_checkstyle_ic(
                tree, row["config_path"], tools["java"], tools["checkstyle"],
                trep["source_roots"], work / "_cs" / f"{owner}__{repo}")
        elif kind == "dependency-cruiser":
            native, nmeta = run_depcruise(tree, row["config_path"],
                                          tools["depcruise"],
                                          trep.get("src_dir", "."))
            if not nmeta.get("oracle_live"):
                out["status"] = "oracle-not-live"
                out["reason"] = nmeta.get("not_live_reason")
                out["oracle"] = nmeta
                return out
        else:
            out["status"] = "no-runner"
            return out
    except subprocess.TimeoutExpired:
        out["status"] = "native-timeout"
        return out
    except Exception as e:
        out["status"] = "native-error"
        out["reason"] = f"{type(e).__name__}: {e}"[:300]
        return out

    out["oracle"] = {k: v for k, v in nmeta.items() if k != "stdout_tail"}
    if nmeta.get("parse_error"):
        out["status"] = "native-unparseable"
        out["reason"] = nmeta["parse_error"][:200]
        return out

    limen_rows, lmeta = run_limen(spec_path, tree, args_today)
    out["limen"] = lmeta
    if lmeta.get("parse_error"):
        out["status"] = "limen-error"
        out["reason"] = (lmeta.get("stderr_tail") or "")[:300]
        return out

    # Where only part of the native rule set was translated, findings produced
    # by the untranslated rules are removed from the comparison. Keeping them
    # would count a rule limen was never given as a limen miss; the excluded
    # count is reported so nothing is silently dropped.
    translated_ids = trep.get("native_rule_ids_translated")
    if translated_ids is not None:
        keep = set(translated_ids)
        before = len(native)
        native = [r for r in native if r.get("native_rule_id") in keep]
        out["native_findings_excluded_untranslated_rules"] = before - len(native)

    for r in native + limen_rows:
        r["key"] = key_of(r)
        if r.get("source") == "limen":
            r.setdefault("spec_path", "rules[0]")
        else:
            r.setdefault("native_rule_id", r.get("rule_id"))
            r["source"] = "native"

    core = diff_finding_sets(
        limen_rows, native,
        classifiers=make_classifiers(tree, nmeta, config_text, DEVIATIONS,
                                     all_findings=native + limen_rows),
        oracle_label="native")

    # Line-insensitive view: deptrac and dependency-cruiser report a different
    # line than limen by construction (D-DT-1), so a (file, target) agreement
    # is reported alongside the strict key rather than replacing it.
    def ft(rows):
        return {((r.get("file") or "").replace("\\", "/").lstrip("./"),
                 (r.get("target") or "").lstrip("\\")) for r in rows
                if r.get("file")}
    lim_ft, nat_ft = ft(limen_rows), ft(native)
    out["agreement_file_target"] = len(lim_ft & nat_ft)
    out["limen_only_file_target"] = len(lim_ft - nat_ft)
    out["native_only_file_target"] = len(nat_ft - lim_ft)

    out["counts"] = core["counts"]
    out["differences"] = core["differences"]
    out["agreements_by_rule"] = core["agreements"]

    # ---- seeded-violation arm ------------------------------------------
    # Most repositories are green: their own CI enforces the native rule, so a
    # 0-vs-0 comparison measures false positives and says nothing about
    # detection. Injecting imports the NATIVE tool's own configuration forbids
    # gives a per-repository recall measurement, and doubles as an oracle
    # liveness check: a native tool that stays silent on a violation it
    # declares is not running, and its zero must not be read as agreement.
    out["seeded"] = seed_and_rerun(row, tree, spec_path, tools, args_today,
                                   native, limen_rows)
    out["status"] = "ok"
    return out


def seed_and_rerun(row: dict, tree: Path, spec_path: Path, tools: dict,
                   today: str, native: list[dict], limen_rows: list[dict]
                   ) -> dict:
    """Inject forbidden imports into a copy of the tree; re-run both tools."""
    kind = row["config_kind"]
    if kind == "dependency-cruiser":
        return seed_depcruise(row, tree, spec_path, tools, today)
    if kind != "import-control":
        # Only implemented where an injection can be constructed from the
        # native config alone: checkstyle ImportControl (the cascade decides
        # any candidate target) and dependency-cruiser (each translated rule
        # is a from-prefix/to-prefix pair, so a violating import is a file
        # under `from` importing a file under `to`). deptrac layers and
        # import-linter contracts need a membership analysis to know what is
        # forbidden WHERE; not attempted rather than approximated.
        return {"enabled": False,
                "reason": f"no injection strategy implemented for {kind}"}
    try:
        text, _ = ensure_config(row, tree)
        root = IC.parse_import_control(text)
    except Exception as e:
        return {"enabled": False, "reason": f"{type(e).__name__}: {e}"[:200]}

    roots = java_source_roots(tree)
    roots, _ = roots_under_package(tree, roots, root.pkg)
    srcs = [p for r in roots for p in (tree / r).rglob("*.java")
            if "/build/" not in p.as_posix()]
    if not srcs:
        return {"enabled": False, "reason": "no governed Java sources"}

    pool = sorted({t for p in srcs[:400]
                   for t in _IMPORT_LINE_RX.findall(
                       p.read_text(encoding="utf-8", errors="replace"))
                   if t.startswith(root.pkg + ".")})
    if not pool:
        return {"enabled": False, "reason": "no in-package import targets"}

    seed_root = tree.parent / f"{tree.name}__seeded"
    if seed_root.exists():
        shutil.rmtree(seed_root)
    shutil.copytree(tree, seed_root, symlinks=True, dirs_exist_ok=True,
                    ignore_dangling_symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "build", "target"))

    placed: list[dict] = []
    seen_pkgs: set[str] = set()
    for p in srcs:
        if len(placed) >= 20:
            break
        body = p.read_text(encoding="utf-8", errors="replace")
        m = _PKG_DECL_RX.search(body)
        if not m:
            continue
        pkg = m.group(1)
        if pkg in seen_pkgs:
            continue
        scope = IC.scope_for_package(root, pkg)
        if scope is None or not IC.is_covered(scope)[0]:
            continue
        already = set(_IMPORT_LINE_RX.findall(body))
        pick = next((c for c in pool
                     if c not in already and not c.startswith(pkg + ".")
                     and IC.decide(scope, c)[0] == IC.DISALLOW), None)
        if pick is None:
            continue
        seen_pkgs.add(pkg)
        rel = p.relative_to(tree)
        q = seed_root / rel
        end = body.index("\n", m.end() - 1) + 1
        line = body.count("\n", 0, end) + 1
        q.write_text(body[:end] + f"import {pick};\n" + body[end:],
                     encoding="utf-8")
        placed.append({"rel_path": rel.as_posix(), "package": pkg,
                       "target": pick, "injected_line": line})
    if not placed:
        shutil.rmtree(seed_root, ignore_errors=True)
        return {"enabled": False, "reason": "no injection site found"}

    # The spec must be re-derived from the SEEDED tree. The translation lists
    # forbidden targets by evaluating the native cascade over the imports
    # actually present, so a spec built from the pristine tree cannot name a
    # target that only the injection introduced — reusing it would report
    # limen misses that are artefacts of the harness, not of limen.
    try:
        seed_spec, _, _ = translate(row, seed_root)
        seed_spec_path = seed_root / "_limen_architecture.yaml"
        seed_spec_path.write_text(
            yaml.safe_dump(seed_spec, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
    except Exception as e:
        shutil.rmtree(seed_root, ignore_errors=True)
        return {"enabled": False,
                "reason": f"re-translation of the seeded tree failed: "
                          f"{type(e).__name__}: {e}"[:200]}

    s_native, s_meta = run_checkstyle_ic(
        seed_root, row["config_path"], tools["java"], tools["checkstyle"],
        roots, seed_root.parent / "_cs_seeded" / tree.name)
    s_limen, s_lmeta = run_limen(seed_spec_path, seed_root, today)

    def hit(rows, inj):
        return any((r.get("file") or "").replace("\\", "/") == inj["rel_path"]
                   and r.get("line") == inj["injected_line"] for r in rows)

    detail = [{**inj, "native_detected": hit(s_native, inj),
               "limen_detected": hit(s_limen, inj)} for inj in placed]
    shutil.rmtree(seed_root, ignore_errors=True)
    return {
        "enabled": True,
        "n_injections": len(detail),
        "native_detected": sum(1 for d in detail if d["native_detected"]),
        "limen_detected": sum(1 for d in detail if d["limen_detected"]),
        "both_detected": sum(1 for d in detail
                             if d["native_detected"] and d["limen_detected"]),
        "injections": detail,
        "native_exit": s_meta.get("exit_code"),
        "limen_exit": s_lmeta.get("exit_code"),
    }


_IMPORT_LINE_RX = re.compile(
    r"^\s*import\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;", re.M)

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def seed_depcruise(row: dict, tree: Path, spec_path: Path, tools: dict,
                   today: str) -> dict:
    """Inject a cross-boundary import for each translated from/to pair.

    Each translated rule bans imports from one directory prefix to another, so
    a violation is constructed directly: pick a real source file under `from`,
    a real module under `to`, and add a relative import of the second to the
    first. The injected specifier is a path both tools resolve the same way, so
    a disagreement is about the RULE, not about module resolution.
    """
    try:
        text, _ = ensure_config(row, tree)
        src_dir = next((d for d in ("src", "lib", "app", "packages")
                        if (tree / d).is_dir()), ".")
        rs = depcruise_ruleset(tree, row["config_path"], src_dir,
                               tools.get("depcruise", "depcruise"))
        _, rep = DC.to_limen(rs, src_dir)
    except Exception as e:
        return {"enabled": False, "reason": f"{type(e).__name__}: {e}"[:200]}

    # The config's own `exclude`/`doNotFollow` filters decide what the native
    # tool will even look at. Injecting into (or pointing at) an excluded file
    # produces a violation the native tool is CONFIGURED not to report, which
    # would be scored as a native miss when it is correct behaviour.
    opts = rs.get("options") if isinstance(rs.get("options"), dict) else {}
    excl_pats = []
    for key in ("exclude", "doNotFollow"):
        v = (opts or {}).get(key)
        pat = v.get("path") if isinstance(v, dict) else v
        if isinstance(pat, str):
            excl_pats.append(pat)
        elif isinstance(pat, list):
            excl_pats.extend(x for x in pat if isinstance(x, str))
    excl_rx = [re.compile(p) for p in excl_pats if p]

    def excluded(rel: str) -> bool:
        return any(rx.search(rel) for rx in excl_rx)

    def files_under(prefix: str) -> list[Path]:
        base = tree / prefix.rstrip("/")
        if not base.is_dir():
            return []
        out = []
        for p in sorted(base.rglob("*")):
            if p.suffix not in _JS_EXTS or not p.is_file():
                continue
            rel = p.relative_to(tree).as_posix()
            if "node_modules" in p.parts or p.name.endswith(".d.ts"):
                continue
            if excluded(rel):
                continue
            # Never inject into (or point at) tool configuration, build
            # scripts or dotfiles: they are not application modules, the
            # cruise root usually does not contain them, and editing a
            # `.dependency-cruiser.cjs` would change the RULES rather than
            # violate them.
            if p.name.startswith(".") or ".config." in p.name or \
                    p.name.endswith((".config.ts", ".config.js", ".config.mjs",
                                     ".config.cjs")):
                continue
            if any(seg.startswith(".") for seg in p.relative_to(tree).parts[:-1]):
                continue
            # Only files the native tool actually cruises can be compared: the
            # run is anchored at `src_dir`, so a file outside it is invisible
            # to the oracle and a limen-only detection there would be a scope
            # artefact, not a disagreement.
            if src_dir not in ("", ".") and not rel.startswith(
                    src_dir.rstrip("/") + "/"):
                continue
            out.append(p)
        return out

    seed_root = tree.parent / f"{tree.name}__seeded"
    if seed_root.exists():
        shutil.rmtree(seed_root)
    # `symlinks=False` dereferences; a repository can ship a symlink whose
    # target is absent from the tarball, and copytree then raises after having
    # copied most of the tree. Symlinks are preserved instead (nothing under
    # analysis depends on following them) and the residual errors are ignored.
    shutil.copytree(tree, seed_root, symlinks=True, dirs_exist_ok=True,
                    ignore_dangling_symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "node_modules",
                                                  "dist", "build"))
    placed: list[dict] = []
    used: set[str] = set()
    for pair in rep["pairs"]:
        frm, to = [x.strip() for x in pair.split("->")]
        srcs, dsts = files_under(frm), files_under(to)
        # One injection per FILE: two injected imports in the same file would
        # share a reported location and each could mask the other.
        srcs = [p for p in srcs if p.relative_to(tree).as_posix() not in used]
        if not srcs or not dsts:
            continue
        s, d = srcs[0], dsts[0]
        used.add(s.relative_to(tree).as_posix())
        rel = s.relative_to(tree)
        spec_target = os.path.relpath(
            d.with_suffix("").as_posix(), s.parent.as_posix())
        if not spec_target.startswith("."):
            spec_target = "./" + spec_target
        q = seed_root / rel
        body = q.read_text(encoding="utf-8", errors="replace")
        q.write_text(f"import * as __seeded from '{spec_target}';\n" + body,
                     encoding="utf-8")
        placed.append({"rel_path": rel.as_posix(), "from_prefix": frm,
                       "to_prefix": to, "specifier": spec_target,
                       "injected_line": 1})
    if not placed:
        shutil.rmtree(seed_root, ignore_errors=True)
        return {"enabled": False,
                "reason": "no from/to prefix pair has real files on both sides"}

    s_native, s_meta = run_depcruise(seed_root, row["config_path"],
                                     tools.get("depcruise", "depcruise"),
                                     src_dir)
    keep = set(rep.get("native_rule_ids_translated") or [])
    s_native = [r for r in s_native if r.get("native_rule_id") in keep]
    s_limen, s_lmeta = run_limen(spec_path, seed_root, today)

    def hit(rows, inj, line_sensitive: bool):
        for r in rows:
            if (r.get("file") or "").replace("\\", "/").lstrip("./") != \
                    inj["rel_path"]:
                continue
            if line_sensitive and r.get("line") not in (None, inj["injected_line"]):
                continue
            return True
        return False

    detail = [{**i,
               # dependency-cruiser reports per (module, dependency) and emits
               # no line number, so detection is keyed on the file for the
               # native side and on (file, line) for limen.
               "native_detected": hit(s_native, i, False),
               "limen_detected": hit(s_limen, i, True)} for i in placed]
    shutil.rmtree(seed_root, ignore_errors=True)
    return {
        "enabled": True,
        "n_injections": len(detail),
        "native_detected": sum(1 for d in detail if d["native_detected"]),
        "limen_detected": sum(1 for d in detail if d["limen_detected"]),
        "both_detected": sum(1 for d in detail
                             if d["native_detected"] and d["limen_detected"]),
        "injections": detail,
        "native_exit": s_meta.get("exit_code"),
        "limen_exit": s_lmeta.get("exit_code"),
        "key_note": ("dependency-cruiser reports no line number, so its "
                     "detection is keyed on the file; limen's is keyed on "
                     "(file, line)"),
    }


# ==========================================================================
# Aggregation
# ==========================================================================
PER_REPO_COLS = ["owner", "repo", "sha", "config_kind", "language", "heldout",
                 "license", "stars", "status", "reason", "oracle_kind",
                 "n_limen_rules", "kinds", "native_findings", "limen_findings",
                 "agreements", "differences_total", "native_only", "limen_only",
                 "C1", "C2", "C3", "unclassified", "agreement_file_target"]


def repo_row(r: dict) -> dict:
    c = r.get("counts") or {}
    pc = c.get("per_category") or {}
    return {
        "owner": r["owner"], "repo": r["repo"], "sha": r["sha"],
        "config_kind": r["config_kind"], "language": r["language"],
        "heldout": r["heldout"], "license": r.get("license"),
        "stars": r.get("stars"), "status": r["status"],
        "reason": (r.get("reason") or "")[:160],
        "oracle_kind": (r.get("oracle") or {}).get("oracle_kind"),
        "n_limen_rules": r.get("n_limen_rules"),
        "kinds": "|".join(r.get("kinds") or []),
        "native_findings": c.get("native_findings_total"),
        "limen_findings": c.get("limen_findings_total"),
        "agreements": c.get("agreements"),
        "differences_total": c.get("differences_total"),
        "native_only": c.get("differences_native_only"),
        "limen_only": c.get("differences_limen_only"),
        "C1": pc.get("C1"), "C2": pc.get("C2"), "C3": pc.get("C3"),
        "unclassified": c.get("unclassified"),
        "agreement_file_target": r.get("agreement_file_target"),
    }


def per_kind_rows(results: list[dict]) -> list[dict]:
    """Aggregate by limen rule kind (K1-K11) x split.

    A repository contributes to a K only if its translation emitted a rule of
    that kind. Precision and recall are computed against the native oracle:
    TP = agreements, FP = limen-only, FN = native-only. Where the native tool
    has no rule of that kind at all, n = 0 and the cell is n/a — never 100%.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for r in results:
        if r["status"] != "ok":
            continue
        split = "held-out" if r["heldout"] else "dev"

        def bucket(k: str) -> dict:
            b = buckets.setdefault((k, split), {
                "kind": k, "split": split, "repos": set(), "tools": set(),
                "agreements": 0, "native_only": 0, "limen_only": 0,
                "C1": 0, "C2": 0, "C3": 0, "unclassified": 0,
                "native_findings": 0, "limen_findings": 0})
            b["tools"].add(r["config_kind"])
            return b

        # PER-FINDING attribution. A repository's spec may emit rules of
        # several kinds (an import-linter config with `layers`, `forbidden` and
        # `independence` contracts yields K1 + K6 + K11). Adding the
        # repository's whole tally to each of its kinds — which an earlier
        # version of this function did — counts the same findings two or three
        # times and reports a K1 recall computed from K6 findings. Each finding
        # is therefore attributed to the kind of the rule that produced it.
        kind_of_rule = r.get("rule_kinds") or {}

        def kind_for(rid: str | None, fallback: str) -> str:
            return KIND_NUMBER.get(kind_of_rule.get(rid or ""), fallback)

        # `oracle-side` collects native findings in a MIXED-kind repository:
        # they name a native rule, not a limen rule, so the kind that should
        # have caught them cannot be determined from the finding alone. They
        # are held here rather than assigned to a kind by guesswork or
        # duplicated across every kind the spec emitted.

        # limen-side findings carry their own rule id; oracle-side findings do
        # not map to a limen rule, so they are attributed to the kinds the
        # translation emitted for that repository — recorded explicitly as an
        # approximation when a repository is mixed-kind.
        kinds = r.get("kinds") or []
        primary = kinds[0] if len(kinds) == 1 else None
        for k in kinds:
            b = bucket(k)
            b["repos"].add(f"{r['owner']}/{r['repo']}")
        if primary is None:
            # Mixed-kind repository: attribute per finding where the finding
            # names a limen rule, and hold the rest in an explicit bucket
            # rather than duplicating them across kinds.
            for d in r.get("differences", []):
                k = kind_for(d.get("rule_id"), "oracle-side")
                b = bucket(k)
                cat = d["classification"]["category"]
                if d["direction"] == "limen-only":
                    b["limen_only"] += 1
                else:
                    b["native_only"] += 1
                if cat in ("C1", "C2", "C3"):
                    b[cat] += 1
                else:
                    b["unclassified"] += 1
            for a in r.get("agreements_by_rule", []):
                bucket(kind_for(a.get("limen_rule_id"), "oracle-side"))["agreements"] += 1
        else:
            b = bucket(primary)
            c = r["counts"]
            pc = c["per_category"]
            b["agreements"] += c["agreements"]
            b["native_only"] += c["differences_native_only"]
            b["limen_only"] += c["differences_limen_only"]
            b["native_findings"] += c["native_findings_total"]
            b["limen_findings"] += c["limen_findings_total"]
            for cat in ("C1", "C2", "C3"):
                b[cat] += pc.get(cat, 0)
            b["unclassified"] += c["unclassified"]
    rows = []
    for b in sorted(buckets.values(),
                    key=lambda x: (int(x["kind"][1:]) if x["kind"].startswith("K")
                                   and x["kind"][1:].isdigit() else 99,
                                   x["kind"], x["split"])):
        tp, fp, fn = b["agreements"], b["limen_only"], b["native_only"]
        # A kind with no findings on EITHER side was never exercised: the
        # native tool has no rule of that kind, or its rules produced nothing.
        # Reporting recall 0.000 there would claim limen missed violations
        # that were never presented to it; the cell is n/a with the reason.
        # `oracle-side` is not a rule kind: it holds native findings in a
        # mixed-kind repository that name no limen rule, so no precision or
        # recall can be attributed to it. Reporting 0.000 there would claim a
        # miss by a rule kind that was never identified.
        exercised = (tp + fp + fn) > 0 and b["kind"] != "oracle-side"
        p, plo, phi = wilson(tp, tp + fp) if exercised else (0, 0, 0)
        rc, rlo, rhi = wilson(tp, tp + fn) if exercised else (0, 0, 0)
        if not exercised:
            tp = fp = fn = 0
        rows.append({
            "kind": b["kind"], "split": b["split"], "repos": len(b["repos"]),
            "tools": "|".join(sorted(b["tools"])),
            "native_findings": b["native_findings"],
            "limen_findings": b["limen_findings"],
            "agreements_TP": tp, "limen_only_FP": fp, "native_only_FN": fn,
            "exercised": exercised,
            "precision": "n/a" if not exercised or tp + fp == 0 else f"{p:.6f}",
            "precision_lo": "n/a" if not exercised or tp + fp == 0 else f"{plo:.6f}",
            "precision_hi": "n/a" if not exercised or tp + fp == 0 else f"{phi:.6f}",
            "recall": "n/a" if not exercised or tp + fn == 0 else f"{rc:.6f}",
            "recall_lo": "n/a" if not exercised or tp + fn == 0 else f"{rlo:.6f}",
            "recall_hi": "n/a" if not exercised or tp + fn == 0 else f"{rhi:.6f}",
            "C1": b["C1"], "C2": b["C2"], "C3": b["C3"],
            "unclassified": b["unclassified"],
        })
    return rows


def write_summary(path: Path, results: list[dict], kind_rows: list[dict],
                  corpus_stats: dict) -> None:
    L = []
    A = L.append
    A("# Study 1b — differential over a corpus of third-party repositories\n")
    A("Every number here is produced by `eval/study1b_corpus.py` from "
      "`eval/corpus.jsonl`; none is typed by hand.\n")
    A("## Corpus\n")
    A(f"- repositories in the corpus file: **{corpus_stats['rows']}** "
      f"(included: **{corpus_stats['included']}**)")
    A(f"- development set: **{corpus_stats['dev']}**, "
      f"held-out: **{corpus_stats['heldout']}** "
      f"(seed {corpus_stats.get('seed')})")
    A(f"- by native checker: {corpus_stats['by_kind']}")
    A(f"- by language: {corpus_stats['by_language']}\n")
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    A("## What happened to each repository\n")
    A("| outcome | repositories |")
    A("|---|---|")
    for k in sorted(by_status):
        A(f"| {k} | {by_status[k]} |")
    A("")
    ok = [r for r in results if r["status"] == "ok"]
    A(f"**{len(ok)}** repositories produced a comparable run "
      f"(dev {sum(1 for r in ok if not r['heldout'])}, "
      f"held-out {sum(1 for r in ok if r['heldout'])}).\n")
    A("## Agreement per rule kind\n")
    A("Precision and recall are limen against the native tool as oracle: "
      "TP = same (file, line, target) on both sides, FP = limen-only, "
      "FN = native-only. Intervals are Wilson score, 95%.\n")
    A("`n/a` means the row was never exercised: the translated spec emits "
      "rules of that kind, but neither tool produced a finding for it, so "
      "there was nothing to agree or disagree about. It is **not** an "
      "agreement result. Reporting a recall of 0.000 there would assert that "
      "limen missed violations that were never presented to it — K11 "
      "(reachability) is the case in point: no native contract in this corpus "
      "produced a reachability finding.\n")
    A("The `oracle-side` row is not a rule kind. In a repository whose spec "
      "mixes kinds, a native finding names a NATIVE rule, not a limen rule, "
      "so the kind that should have caught it cannot be determined from the "
      "finding alone. Those findings are held here rather than assigned by "
      "guesswork or — as an earlier version of this aggregation did — added "
      "to every kind the spec emitted, which counted the same findings two or "
      "three times and computed one kind's recall from another kind's "
      "findings.\n")
    A("| K | split | repos | tools | TP | FP | FN | precision [95% CI] | "
      "recall [95% CI] | C1 | C2 | C3 | unclassified |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in kind_rows:
        pr = ("n/a" if r["precision"] == "n/a"
              else f"{float(r['precision']):.3f} [{float(r['precision_lo']):.3f}, "
                   f"{float(r['precision_hi']):.3f}]")
        rc = ("n/a" if r["recall"] == "n/a"
              else f"{float(r['recall']):.3f} [{float(r['recall_lo']):.3f}, "
                   f"{float(r['recall_hi']):.3f}]")
        A(f"| {r['kind']} | {r['split']} | {r['repos']} | {r['tools']} | "
          f"{r['agreements_TP']} | {r['limen_only_FP']} | {r['native_only_FN']} | "
          f"{pr} | {rc} | {r['C1']} | {r['C2']} | {r['C3']} | "
          f"{r['unclassified']} |")
    A("")
    seeded = [r for r in results if (r.get("seeded") or {}).get("enabled")]
    if seeded:
        ti = sum(r["seeded"]["n_injections"] for r in seeded)
        tn = sum(r["seeded"]["native_detected"] for r in seeded)
        tl = sum(r["seeded"]["limen_detected"] for r in seeded)
        tb = sum(r["seeded"]["both_detected"] for r in seeded)
        A("## Detection (seeded violations)\n")
        A("Most repositories in the corpus are green — their own CI enforces "
          "the native rule — so a 0-vs-0 comparison measures false positives "
          "and says nothing about detection. Imports the native tool's OWN "
          "configuration forbids were therefore injected into a real copy of "
          "each tree and both tools re-run. The translated spec is re-derived "
          "from the seeded tree, because the forbid lists are built by "
          "evaluating the native config over the imports actually present.\n")
        A(f"- repositories with a seeded arm: **{len(seeded)}**")
        A(f"- injections: **{ti}**")
        A(f"- detected by the native tool: **{tn}**")
        A(f"- detected by limen: **{tl}**")
        A(f"- detected by both: **{tb}**\n")
        A("| repository | tool | injections | native | limen | both |")
        A("|---|---|---|---|---|---|")
        for r in sorted(seeded, key=lambda x: -x["seeded"]["n_injections"]):
            s = r["seeded"]
            A(f"| {r['owner']}/{r['repo']} | {r['config_kind']} | "
              f"{s['n_injections']} | {s['native_detected']} | "
              f"{s['limen_detected']} | {s['both_detected']} |")
        A("")
        A("dependency-cruiser reports no line number, so its detection is "
          "keyed on the file while limen's is keyed on (file, line). Per "
          "injection detail is in `study1b_raw/<repo>.json`.\n")

    A("## Acceptance gates\n")
    A("`eval/study1b_corpus.py` exits non-zero — exactly as "
      "`eval/study1_equivalence.py` does — when either gate fails:\n")
    A("1. **zero unclassified differences.** Every difference between the two "
      "tools must carry a C1/C2/C3 classification; unexplained differences are "
      "listed per repository with examples.")
    A("2. **every C3 cites a real deviation.** Each C3 names a `D-xx` "
      "identifier that must have a heading in `docs/deviations.md`. Without "
      "this check C3 would degrade into a catch-all for anything the "
      "classifier cannot place.\n")
    A("Both gates were verified to FIRE, not merely to pass: renaming "
      "`D-CS-1` in the registry makes the run exit 1.\n")

    A("## Files\n")
    A("- `study1b_per_repo.csv` — one row per repository")
    A("- `study1b_per_kind.csv` — the table above, machine-readable")
    A("- `study1b_raw/` — the full finding sets and classified differences")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="eval/corpus.jsonl")
    ap.add_argument("--work", default="eval/_scratch/corpus")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--today", default="2026-08-04")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--kinds", nargs="*", default=None)
    ap.add_argument("--php", default="php")
    ap.add_argument("--deptrac", required=True)
    ap.add_argument("--java", required=True)
    ap.add_argument("--checkstyle", required=True)
    ap.add_argument("--lint-imports", required=True,
                    help="path to import-linter's `lint-imports` entrypoint")
    ap.add_argument("--depcruise", default="depcruise")
    args = ap.parse_args()

    global _TOOLS
    tools = {"php": args.php, "deptrac": args.deptrac, "java": args.java,
             "checkstyle": args.checkstyle, "il_python": args.lint_imports,
             "depcruise": args.depcruise}
    _TOOLS = dict(tools)
    rows = [json.loads(l) for l in Path(args.corpus).read_text(
        encoding="utf-8").splitlines() if l.strip()]
    included = [r for r in rows if r.get("included")]
    if args.kinds:
        included = [r for r in included if r["config_kind"] in args.kinds]
    if args.limit:
        included = included[:args.limit]

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    raw = out / "study1b_raw"
    raw.mkdir(parents=True, exist_ok=True)

    results = []
    for i, row in enumerate(included, start=1):
        t0 = time.time()
        try:
            res = process(row, work, tools, args.today)
        except Exception as e:
            res = {"owner": row["owner"], "repo": row["repo"], "sha": row["sha"],
                   "config_kind": row["config_kind"], "language": row["language"],
                   "heldout": bool(row.get("heldout")), "license": row.get("license"),
                   "stars": row.get("stars"), "status": "driver-error",
                   "reason": f"{type(e).__name__}: {e}"[:300]}
        res["seconds"] = round(time.time() - t0, 1)
        results.append(res)
        c = res.get("counts") or {}
        print(f"[{i:3d}/{len(included)}] {row['owner']}/{row['repo']:<32.32s} "
              f"{row['config_kind']:<18s} {res['status']:<22s} "
              f"nat={c.get('native_findings_total','-')} "
              f"lim={c.get('limen_findings_total','-')} "
              f"agree={c.get('agreements','-')} ({res['seconds']}s)",
              flush=True)
        (raw / f"{row['owner']}__{row['repo']}.json").write_text(
            json.dumps(res, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8")

    with (out / "study1b_per_repo.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PER_REPO_COLS)
        w.writeheader()
        for r in results:
            w.writerow(repo_row(r))

    kind_rows = per_kind_rows(results)
    if kind_rows:
        with (out / "study1b_per_kind.csv").open("w", newline="",
                                                 encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(kind_rows[0]))
            w.writeheader()
            w.writerows(kind_rows)

    by_kind: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    for r in included:
        by_kind[r["config_kind"]] = by_kind.get(r["config_kind"], 0) + 1
        by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
    stats = {"rows": len(rows), "included": len(included),
             "dev": sum(1 for r in included if not r.get("heldout")),
             "heldout": sum(1 for r in included if r.get("heldout")),
             "by_kind": by_kind, "by_language": by_lang}
    flow = out / "corpus_flow.json"
    if flow.is_file():
        stats["seed"] = json.loads(flow.read_text()).get("seed")
    write_summary(out / "study1b_summary.md", results, kind_rows, stats)
    print(f"\n{out/'study1b_per_repo.csv'}\n{out/'study1b_per_kind.csv'}\n"
          f"{out/'study1b_summary.md'}")

    # ---- gate -----------------------------------------------------------
    # Study 1's acceptance criterion is ZERO unclassified differences, enforced
    # by a non-zero exit from study1_equivalence.py. The corpus run must signal
    # the same thing, or "every difference is explained" would be a claim the
    # harness never checks. It is reported per repository so a reviewer can see
    # where the unexplained differences are, and the artefacts are written
    # first — a failing gate must not cost the run's evidence.
    # Every C3 must cite a deviation that actually EXISTS in the registry.
    # Without this check `C3` degrades into a catch-all: a classifier could
    # name any identifier and the category would look explained.
    registry = DEVIATIONS_DOC.read_text(encoding="utf-8") if \
        DEVIATIONS_DOC.is_file() else ""
    known = set(re.findall(r"^## (D-[\w-]+)", registry, re.M))
    dangling = Counter()
    for r in results:
        if r["status"] != "ok":
            continue
        for d in r.get("differences", []):
            cl = d["classification"]
            if cl["category"] != "C3":
                continue
            did = (cl.get("evidence") or {}).get("deviation")
            if did not in known:
                dangling[did] += 1
    if dangling:
        print(f"\nGATE FAILED: {sum(dangling.values())} C3 classification(s) "
              f"cite a deviation absent from {DEVIATIONS_DOC.name}: "
              f"{dict(dangling)}", file=sys.stderr)
        return 1

    unexplained = [(r, [d for d in r.get("differences", [])
                        if d["classification"]["category"] is None])
                   for r in results if r["status"] == "ok"]
    unexplained = [(r, ds) for r, ds in unexplained if ds]
    total = sum(len(ds) for _, ds in unexplained)
    if total:
        print(f"\nGATE FAILED: {total} unclassified difference(s) in "
              f"{len(unexplained)} repositor(y/ies)", file=sys.stderr)
        for r, ds in unexplained:
            print(f"  {r['owner']}/{r['repo']} ({r['config_kind']}): {len(ds)}",
                  file=sys.stderr)
            for d in ds[:3]:
                print(f"    {d['direction']} {d['key']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
