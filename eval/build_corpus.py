#!/usr/bin/env python3
"""Study 1b, step 8 — build the differential corpus.

The claim Study 1 cannot make on its own is *generality*: it showed limen
equivalent to one workspace's own checkers. This script assembles the evidence
base for the general claim — **other people's repositories that already carry a
native architecture checker**, so the native tool is a ground truth nobody in
this study authored.

Discovery is by configuration filename through the GitHub code-search API
(``filename:`` works, ``path:`` does not):

    deptrac.yaml / deptrac.yml / depfile.yaml    PHP    (deptrac)
    .importlinter / setup.cfg [importlinter]     Python (import-linter)
    import-control.xml                           Java   (checkstyle ImportControl)
    .dependency-cruiser.js/.json/.cjs            JS/TS  (dependency-cruiser)

Inclusion is a **filter written in code**, and every candidate that falls out
records the step that dropped it, so the sampling flow diagram in the paper is
generated, not drawn:

    F0 repository reachable      metadata and default branch readable over the API
    F1 config fetched            the raw config is retrievable at the pinned SHA
    F2 config parses             it parses as YAML / INI / XML / JS-object
    F3 config non-empty          it declares >= 1 real rule, not just a skeleton
    F4 repo size                 >= MIN_SOURCE_FILES files in the config's language
    F5 not a fork / archived     upstream projects only
    F6 not the tool itself       deptrac/deptrac, import-linter docs, tutorials,
                                 boilerplate templates (NAME_DENYLIST)
    F7 license present           a license must be declared; it is recorded

Every included repository is pinned to the commit SHA of its default branch at
build time, so the corpus is reproducible byte-for-byte.

The held-out split is drawn with a fixed seed (``--seed``, default
``SPLIT_SEED``) over the *included* rows sorted by (owner, repo), so it is a
pure function of the corpus and the seed. Roughly ``--heldout-frac`` of the
repositories are marked ``heldout=true``; the translation is never iterated on
those.

Usage:

    export GITHUB_TOKEN=...        # a token with public_repo scope
    python eval/build_corpus.py --out eval/corpus.jsonl \
        --flow eval/results/corpus_flow.json

Rate limits: code search is 10 requests/minute and the core API 5000/hour;
the script paces itself (``--search-pause``) and reports what it spent.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import http.client
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "arch-corpus/1"
API = "https://api.github.com"


def note(msg: str) -> None:
    """Progress line. Repository names carry arbitrary Unicode; a redirected
    stream may be ASCII, and a crash while *reporting* progress would throw
    away hours of API budget. Never let the log kill the run."""
    try:
        sys.stderr.write(msg)
    except UnicodeEncodeError:
        sys.stderr.write(msg.encode("ascii", "replace").decode("ascii"))
    sys.stderr.flush()

# --------------------------------------------------------------------------
# Inclusion thresholds — the numbers the filter is made of.
# --------------------------------------------------------------------------
MIN_SOURCE_FILES = 30
SPLIT_SEED = 20260804
HELDOUT_FRAC = 1.0 / 3.0

#: Cap on INCLUDED repositories per native checker. The corpus targets 40-60
#: repositories spread over four tools; without a cap the two tools with the
#: most public configurations would supply nearly the whole corpus and the
#: "generality across tools" claim would rest on one ecosystem.
QUOTA_PER_TOOL = 15

#: Repositories that *are* the tool, teach it, or template it. Matched
#: case-insensitively against "owner/repo" and against the repo name alone.
NAME_DENYLIST = (
    "deptrac", "import-linter", "importlinter", "dependency-cruiser",
    "dependency_cruiser", "checkstyle", "phparkitect", "arkitect",
    "tutorial", "example", "examples", "demo", "boilerplate", "skeleton",
    "starter", "template", "playground", "sandbox", "workshop", "training",
    "kata", "exercise", "sample", "samples", "scaffold", "seed-project",
    "learning", "course", "awesome",
)

#: Language -> source-file extensions used for the F4 size test.
LANG_EXT = {
    "php": (".php",),
    "python": (".py",),
    "java": (".java",),
    "js": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue"),
}

#: config_kind -> (search queries, language). Several queries per kind widen
#: recall; results are de-duplicated per repository.
SEARCHES: dict[str, dict[str, Any]] = {
    "deptrac": {
        "language": "php",
        "queries": ["filename:deptrac.yaml", "filename:deptrac.yml",
                    "filename:depfile.yaml", "filename:deptrac.dist.yaml"],
    },
    "import-linter": {
        "language": "python",
        "queries": ["filename:.importlinter", "importlinter filename:setup.cfg"],
    },
    "import-control": {
        "language": "java",
        "queries": ["filename:import-control.xml", "filename:import-control-core.xml"],
    },
    "dependency-cruiser": {
        "language": "js",
        # GitHub code search rejects `filename:` for names beginning with a
        # dot, so these three are issued as bare terms. The `.mjs` variant is
        # NOT searched: it shares the bare-term namespace with `.js` and adds
        # no distinct hits. The docstring above lists exactly these three.
        "queries": [".dependency-cruiser.js", ".dependency-cruiser.json",
                    ".dependency-cruiser.cjs"],
    },
}


# ==========================================================================
# HTTP
# ==========================================================================
class GitHub:
    def __init__(self, token: str, pause: float = 7.0):
        self.token = token
        self.pause = pause
        self.calls = {"core": 0, "search": 0}
        self._last_search = 0.0

    def _req(self, url: str, accept: str = "application/vnd.github+json") -> Any:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "User-Agent": "limen-arch-corpus",
        })
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                return json.loads(body) if accept.endswith("json") else body
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    reset = e.headers.get("x-ratelimit-reset")
                    wait = 60.0
                    if reset:
                        wait = max(5.0, float(reset) - time.time() + 3)
                    wait = min(wait, 180.0)
                    sys.stderr.write(f"  [rate-limit {e.code}] sleeping {wait:.0f}s\n")
                    time.sleep(wait)
                    continue
                if e.code in (404, 451):
                    raise
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                # RemoteDisconnected, connection reset, read timeout: GitHub
                # drops long-lived connections routinely. Retry with backoff.
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"exhausted retries: {url}")

    def core(self, path: str) -> Any:
        self.calls["core"] += 1
        return self._req(f"{API}{path}")

    @staticmethod
    def seg(value: str) -> str:
        """Percent-encode one URL path segment.

        GitHub repository and owner names may contain non-ASCII characters
        (emoji are legal in repository names). `http.client` encodes the
        request line as ASCII, so an unencoded name raises UnicodeEncodeError
        and kills the run mid-corpus.
        """
        return urllib.parse.quote(value, safe="")

    def search_code(self, q: str, per_page: int = 100, pages: int = 1) -> list[dict]:
        items: list[dict] = []
        for page in range(1, pages + 1):
            gap = time.time() - self._last_search
            if gap < self.pause:
                time.sleep(self.pause - gap)
            self._last_search = time.time()
            self.calls["search"] += 1
            url = (f"{API}/search/code?q={urllib.parse.quote(q)}"
                   f"&per_page={per_page}&page={page}")
            try:
                doc = self._req(url)
            except urllib.error.HTTPError as e:
                sys.stderr.write(f"  search failed ({e.code}) for {q!r}\n")
                break
            got = doc.get("items", [])
            items.extend(got)
            if len(got) < per_page:
                break
        return items

    def raw(self, owner: str, repo: str, sha: str, path: str) -> str | None:
        try:
            doc = self.core(f"/repos/{self.seg(owner)}/{self.seg(repo)}/contents/"
                            f"{urllib.parse.quote(path)}?ref={sha}")
        except urllib.error.HTTPError:
            return None
        if isinstance(doc, dict) and doc.get("encoding") == "base64":
            try:
                return base64.b64decode(doc["content"]).decode("utf-8", "replace")
            except Exception:
                return None
        return None


# ==========================================================================
# F2/F3 — config parses, and declares at least one real rule
# ==========================================================================
def _yaml_load(text: str) -> Any:
    import yaml
    return yaml.safe_load(text)


def parse_deptrac(text: str) -> tuple[bool, int, str]:
    """(parses, n_rules, note). A deptrac rule = one ruleset source layer."""
    try:
        doc = _yaml_load(text)
    except Exception as e:
        return False, 0, f"yaml error: {type(e).__name__}"
    if not isinstance(doc, dict):
        return False, 0, "top level is not a mapping"
    cfg = doc.get("deptrac", doc)
    if not isinstance(cfg, dict):
        return False, 0, "deptrac: is not a mapping"
    rulesets = cfg.get("ruleset") or {}
    layers = cfg.get("layers") or []
    if not isinstance(rulesets, dict):
        return True, 0, "ruleset is not a mapping"
    n = len(rulesets)
    return True, n, f"{len(layers)} layers, {n} ruleset entries"


def parse_importlinter(text: str, from_setup_cfg: bool) -> tuple[bool, int, str]:
    """(parses, n_contracts, note). One contract = one [importlinter:contract:*]."""
    cp = configparser.ConfigParser(strict=False)
    try:
        cp.read_string(text)
    except Exception as e:
        return False, 0, f"ini error: {type(e).__name__}"
    contracts = [s for s in cp.sections()
                 if s.startswith("importlinter:contract")]
    has_root = any(s == "importlinter" or s.startswith("importlinter:")
                   for s in cp.sections())
    if from_setup_cfg and not has_root:
        return True, 0, "setup.cfg has no [importlinter] section"
    return True, len(contracts), f"{len(contracts)} contracts"


def parse_import_control(text: str) -> tuple[bool, int, str]:
    """(parses, n_rules, note). A rule = one allow/disallow element anywhere."""
    cleaned = re.sub(r"<!DOCTYPE[^>]*(\[[^\]]*\])?>", "", text, flags=re.DOTALL)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as e:
        return False, 0, f"xml error: {e}"
    if root.tag != "import-control":
        return False, 0, f"root element is <{root.tag}>, not <import-control>"
    n = sum(1 for el in root.iter() if el.tag in ("allow", "disallow"))
    subs = sum(1 for el in root.iter() if el.tag == "subpackage")
    return True, n, f"{n} allow/disallow, {subs} subpackages"


_DC_FORBIDDEN_RX = re.compile(r"\bforbidden\s*:", re.MULTILINE)
_DC_ALLOWED_RX = re.compile(r"\ballowed\s*:", re.MULTILINE)
_DC_NAME_RX = re.compile(r"""\bname\s*:\s*['"]([^'"]+)['"]""")


def parse_depcruise(text: str, is_json: bool) -> tuple[bool, int, str]:
    """(parses, n_rules, note).

    `.dependency-cruiser.js` is executable JavaScript; it is not evaluated
    (that would run third-party code). JSON configs are parsed properly; JS
    configs are read structurally — the `forbidden:`/`allowed:` sections and
    the `name:` keys inside them, which is exactly what a rule is in this
    tool.
    """
    if is_json:
        try:
            doc = json.loads(text)
        except Exception as e:
            return False, 0, f"json error: {type(e).__name__}"
        if not isinstance(doc, dict):
            return False, 0, "top level is not an object"
        n = len(doc.get("forbidden") or []) + len(doc.get("allowed") or [])
        return True, n, f"{n} forbidden/allowed rules (json)"
    if not (_DC_FORBIDDEN_RX.search(text) or _DC_ALLOWED_RX.search(text)):
        return True, 0, "no forbidden:/allowed: section"
    names = _DC_NAME_RX.findall(text)
    return True, len(names), f"{len(names)} named rules (js, structural read)"


def parse_config(kind: str, path: str, text: str) -> tuple[bool, int, str]:
    if kind == "deptrac":
        return parse_deptrac(text)
    if kind == "import-linter":
        return parse_importlinter(text, path.endswith("setup.cfg"))
    if kind == "import-control":
        return parse_import_control(text)
    if kind == "dependency-cruiser":
        return parse_depcruise(text, path.endswith(".json"))
    raise ValueError(kind)


# ==========================================================================
# F6 — the repository is not the tool, a tutorial, or a template
# ==========================================================================
def denylisted(owner: str, repo: str) -> str | None:
    full = f"{owner}/{repo}".lower()
    name = repo.lower()
    for bad in NAME_DENYLIST:
        if bad in name.split("-") or bad in name.split("_") or name == bad:
            return f"repository name contains {bad!r}"
        if full.startswith(bad + "/") or full.endswith("/" + bad):
            return f"repository is the tool itself ({bad})"
    return None


# ==========================================================================
def count_source_files(gh: GitHub, owner: str, repo: str, sha: str,
                       language: str) -> tuple[int, bool]:
    """(n_files_in_language, tree_complete)."""
    try:
        tree = gh.core(f"/repos/{gh.seg(owner)}/{gh.seg(repo)}/git/trees/{sha}?recursive=1")
    except urllib.error.HTTPError:
        return -1, False
    exts = LANG_EXT[language]
    n = sum(1 for e in tree.get("tree", [])
            if e.get("type") == "blob" and e["path"].endswith(exts)
            and "/vendor/" not in e["path"] and "/node_modules/" not in e["path"])
    return n, not tree.get("truncated", False)


def build(gh: GitHub, kinds: Iterable[str], pages: int, flow: dict[str, Any],
          quota: int, seed: int, resume: dict[tuple[str, str], dict],
          searches: list[dict] | None = None) -> list[dict[str, Any]]:
    """Search, filter and pin. `quota` caps *included* repositories per tool.

    The corpus targets 40-60 repositories with a usable spread over the four
    native checkers, so a per-tool quota is part of the sampling design, not a
    convenience: without it the two tools with the most public configs would
    supply almost the whole corpus and "generality across tools" would be an
    overstatement. Candidates are shuffled with the study seed **before** the
    quota applies, so which repositories fill a quota is a random draw, not an
    alphabetical prefix. Candidates never reached because a quota was already
    full are recorded as ``F8``, so the flow diagram still balances.
    """
    seen: dict[tuple[str, str], dict] = {}
    if searches is not None:
        # Replay a previous run's search hits (resume path): no new API calls.
        for it in searches:
            key = (it["owner"], it["repo"])
            seen.setdefault(key, it)
        flow["searches"] = flow.get("searches") or []
    else:
        for kind in kinds:
            spec = SEARCHES[kind]
            for q in spec["queries"]:
                items = gh.search_code(q, pages=pages)
                flow["searches"].append({"kind": kind, "query": q, "hits": len(items)})
                note(f"  {kind:20s} {q:45s} {len(items):4d} hits\n")
                for it in items:
                    r = it["repository"]
                    key = (r["owner"]["login"], r["name"])
                    if key in seen:
                        continue
                    seen[key] = {"owner": key[0], "repo": key[1],
                                 "config_path": it["path"], "config_kind": kind,
                                 "language": spec["language"], "query": q}
    note(f"\ncandidates after de-duplication: {len(seen)}\n")
    flow["candidates"] = len(seen)

    # Deterministic evaluation order: sort, then shuffle with the study seed.
    order = sorted(seen)
    random.Random(seed).shuffle(order)
    included_per_kind: dict[str, int] = {}

    rows: list[dict[str, Any]] = []
    for i, key in enumerate(order, start=1):
        cand = seen[key]
        owner, repo = key
        prior = resume.get(key)
        if prior is not None and prior.get("sha"):
            # Already decided in a previous run against the same searches.
            if prior.get("included"):
                kind = prior["config_kind"]
                if included_per_kind.get(kind, 0) >= quota:
                    prior = dict(prior, included=False,
                                 exclusion_reason=f"F8 per-tool quota of {quota} "
                                                  f"{kind} repositories already met")
                else:
                    included_per_kind[kind] = included_per_kind.get(kind, 0) + 1
            rows.append(prior)
            continue
        if included_per_kind.get(cand["config_kind"], 0) >= quota:
            rows.append(dict(cand, sha=None, stars=None, license=None,
                             n_source_files=None, n_config_rules=None,
                             config_note=None, included=False, heldout=False,
                             exclusion_reason=(f"F8 per-tool quota of {quota} "
                                               f"{cand['config_kind']} repositories "
                                               "already met")))
            continue
        row = dict(cand, sha=None, stars=None, license=None, n_source_files=None,
                   n_config_rules=None, config_note=None,
                   included=False, exclusion_reason=None, heldout=False)
        rows.append(row)
        note(f"[{i:3d}/{len(seen)}] {owner}/{repo} ({cand['config_kind']}) ")

        drop = denylisted(owner, repo)
        if drop:
            row["exclusion_reason"] = f"F6 tool/tutorial/template: {drop}"
            note("→ F6\n")
            continue
        try:
            meta = gh.core(f"/repos/{gh.seg(owner)}/{gh.seg(repo)}")
        except urllib.error.HTTPError as e:
            row["exclusion_reason"] = f"F0 repository metadata unavailable (HTTP {e.code})"
            note("→ F1\n")
            continue
        row["stars"] = meta.get("stargazers_count")
        row["license"] = (meta.get("license") or {}).get("spdx_id")
        if meta.get("fork"):
            row["exclusion_reason"] = "F5 repository is a fork"
            note("→ F5 fork\n")
            continue
        if meta.get("archived"):
            row["exclusion_reason"] = "F5 repository is archived"
            note("→ F5 archived\n")
            continue
        try:
            head = gh.core(f"/repos/{gh.seg(owner)}/{gh.seg(repo)}/commits/"
                           f"{urllib.parse.quote(meta['default_branch'], safe='')}")
        except urllib.error.HTTPError as e:
            row["exclusion_reason"] = f"F0 default branch unreadable (HTTP {e.code})"
            note("→ F1\n")
            continue
        row["sha"] = head["sha"]
        row["head_date"] = head["commit"]["committer"]["date"]

        text = gh.raw(owner, repo, row["sha"], row["config_path"])
        if text is None:
            row["exclusion_reason"] = "F1 config not retrievable at the pinned SHA"
            note("→ F1 config\n")
            continue
        row["config_bytes"] = len(text.encode("utf-8"))
        ok, n_rules, cfg_note = parse_config(row["config_kind"],
                                             row["config_path"], text)
        row["n_config_rules"], row["config_note"] = n_rules, cfg_note
        if not ok:
            row["exclusion_reason"] = f"F2 config does not parse ({cfg_note})"
            note("→ F2\n")
            continue
        if n_rules < 1:
            row["exclusion_reason"] = f"F3 config declares no rule ({cfg_note})"
            note("→ F3\n")
            continue
        n_src, complete = count_source_files(gh, owner, repo, row["sha"], row["language"])
        row["n_source_files"], row["tree_complete"] = n_src, complete
        if n_src < 0:
            row["exclusion_reason"] = "F4 file tree unreadable"
            note("→ F4\n")
            continue
        if n_src < MIN_SOURCE_FILES:
            row["exclusion_reason"] = (f"F4 only {n_src} {row['language']} source "
                                       f"files (< {MIN_SOURCE_FILES})")
            note(f"→ F4 ({n_src})\n")
            continue
        if not row["license"]:
            row["exclusion_reason"] = "F7 no license declared"
            note("→ F7\n")
            continue
        row["included"] = True
        included_per_kind[row["config_kind"]] = \
            included_per_kind.get(row["config_kind"], 0) + 1
        note(f"→ INCLUDED ({n_rules} rules, {n_src} files, {row['license']})\n")
    return rows


def assign_heldout(rows: list[dict], seed: int, frac: float) -> int:
    included = sorted([r for r in rows if r["included"]],
                      key=lambda r: (r["owner"].lower(), r["repo"].lower()))
    rng = random.Random(seed)
    k = round(len(included) * frac)
    for r in rng.sample(included, k) if k else []:
        r["heldout"] = True
    return k


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="eval/corpus.jsonl")
    ap.add_argument("--flow", default="eval/results/corpus_flow.json")
    ap.add_argument("--kinds", nargs="*", default=sorted(SEARCHES))
    ap.add_argument("--pages", type=int, default=1,
                    help="code-search result pages per query (100 hits each)")
    ap.add_argument("--search-pause", type=float, default=7.0,
                    help="seconds between code-search calls (limit: 10/min)")
    ap.add_argument("--seed", type=int, default=SPLIT_SEED)
    ap.add_argument("--heldout-frac", type=float, default=HELDOUT_FRAC)
    ap.add_argument("--quota", type=int, default=QUOTA_PER_TOOL,
                    help="cap on INCLUDED repositories per native checker")
    ap.add_argument("--resume", default=None,
                    help="a previous corpus.jsonl: rows already decided there "
                         "are reused instead of re-queried")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        note("GITHUB_TOKEN is not set; export a token with "
                         "public_repo scope and re-run\n")
        return 2

    gh = GitHub(token, pause=args.search_pause)
    flow: dict[str, Any] = {"schema": SCHEMA, "searches": [],
                            "min_source_files": MIN_SOURCE_FILES,
                            "seed": args.seed, "heldout_frac": args.heldout_frac,
                            "name_denylist": list(NAME_DENYLIST)}
    flow["quota_per_tool"] = args.quota
    resume: dict[tuple[str, str], dict] = {}
    searches: list[dict] | None = None
    if args.resume and Path(args.resume).is_file():
        for line in Path(args.resume).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            resume[(r["owner"], r["repo"])] = r
        searches = list(resume.values())
        flow["resumed_from"] = args.resume
        flow["resumed_rows"] = len(resume)
        note(f"resuming from {args.resume}: {len(resume)} rows\n")

    t0 = time.time()
    rows = build(gh, args.kinds, args.pages, flow, args.quota, args.seed,
                 resume, searches)
    n_heldout = assign_heldout(rows, args.seed, args.heldout_frac)

    steps: dict[str, int] = {}
    for r in rows:
        step = (r["exclusion_reason"] or "INCLUDED").split(" ", 1)[0]
        steps[step] = steps.get(step, 0) + 1
    flow["per_step"] = steps
    flow["included"] = sum(1 for r in rows if r["included"])
    flow["heldout"] = n_heldout
    flow["dev"] = flow["included"] - n_heldout
    flow["api_calls"] = gh.calls
    flow["wall_seconds"] = round(time.time() - t0, 1)
    by_kind: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    for r in rows:
        if r["included"]:
            by_kind[r["config_kind"]] = by_kind.get(r["config_kind"], 0) + 1
            by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
    flow["included_by_kind"], flow["included_by_language"] = by_kind, by_lang

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in sorted(rows, key=lambda r: (r["config_kind"], r["owner"].lower(),
                                             r["repo"].lower())):
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    fp = Path(args.flow)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(flow, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"{out}  ({len(rows)} candidates)")
    print(f"{fp}")
    for step in sorted(steps):
        print(f"  {step:12s} {steps[step]}")
    print(f"  included {flow['included']} = dev {flow['dev']} + held-out {n_heldout}")
    print(f"  by kind: {by_kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
