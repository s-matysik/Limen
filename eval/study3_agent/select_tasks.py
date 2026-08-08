#!/usr/bin/env python3
"""Study 3 — select the K task set from the mapped candidate pool.

    export PYTHONPATH=<limen>/src
    python eval/study3_agent/select_tasks.py \
        --pool  eval/results/study3_candidate_pool.tsv \
        --live  /Users/.../Limen/live \
        --k 10 \
        --out   eval/study3_agent/tasks.jsonl

WHAT THIS DOES
--------------
The pool (34 commits, 17 backend + 17 frontend) is filtered by three CODE
criteria — never by prose judgement:

  C1  the commit adds a use case in a module.  Four mechanical thresholds,
      fixed before selection and applied uniformly (measured values for the
      whole pool are in the audit file, column block `c1`):
        C1a  >= 1 changed file is module product code — under a module
             directory of a declared root, not a test, not a locale;
        C1b  <= MAX_PRODUCT_FILES changed non-test product files.  Product
             excludes docker/, .env, dotfiles, README/docs, committed
             node_modules/dist/vendor and lockfiles.  This is the
             "describable as ONE request" bound;
        C1c  >= MIN_NET_MODULE_LINES net added lines of module product code.
             This separates additive behaviour from relabelings (+3/-0),
             reformulations of existing behaviour (+24/-23) and extractions
             (net < 0), none of which is a use case to implement;
        C1d  locale insertions < MAX_LOCALE_SHARE of all product+locale
             insertions — a translation pass is not a use case.
  C2  the parent tree has a runnable module test suite: pool column
      `module_tests_at_parent` >= 1 AND the module the commit touches maps to
      a PHPUnit testsuite (backend) or a vitest glob (frontend) that exists in
      the parent tree.
  C3  the scope is describable as a task without revealing the architecture:
      after mechanical redaction + rewrite, the prompt must contain none of
      the ARCH_LEXICON terms (hard gate, asserted).

PROMPT CONSTRUCTION (Step 1 of the protocol)
--------------------------------------------
`prompt` is derived from the commit subject+body in three stages:

  S1  mechanical strip: trailer lines (Co-Authored-By, Signed-off-by), file
      statistics, and any line that names a path under app/ or src/.
  S2  LLM rewrite into a product-owner request, under a system prompt that
      forbids every ARCH_LEXICON term and forbids naming files, classes,
      directories, layers or modules-as-boundaries.
  S3  hard gate: the rewritten prompt is lower-cased and matched against
      ARCH_LEXICON; a hit is an error, not a warning.  Up to
      --rewrite-attempts attempts, then the task is DROPPED (a dropped task
      is recorded in the audit file with the reason).

The untouched original message is stored in `source` so a reader can verify
exactly what was removed.  `redaction_removed` lists the S1 deletions.

BALANCE
-------
Selection walks backend and frontend alternately (most-recent-first within
each repo, since later commits sit on trees whose module suites are largest)
and reports the achieved split rather than forcing 50/50.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# C3 — the architecture lexicon.  A prompt containing any of these leaks the
# contract into condition A and destroys the design, so it is a hard gate.
# Matched case-insensitively as substrings on the rewritten prompt.
# --------------------------------------------------------------------------- #
ARCH_LEXICON = [
    # layer names (en + pl)
    "application", "aplikacyjn", "domain", "domen", "infrastructure",
    "infrastruktur", "presentation", "prezentacj", "contracts", "kontrakt",
    "layer", "warstw", "http layer",
    # module-as-boundary vocabulary
    "module", "modul", "moduł", "boundary", "boundaries", "granic",
    "public surface", "public api", "publiczne api", "entrypoint",
    "entry point", "punkt wejści", "index.js", "agents.md", "claude.md",
    "architektur", "architecture", "arch ", "ddd", "monolit",
    # limen / checker vocabulary
    "limen", "baseline", "exempt", "wyjątek regu", "allowlist", "gateway",
    "adapter", "port ", "facade", "fasad",
    # code-shape vocabulary that would give away the target file
    "controller", "kontroler", "service class", "serwis klas", "namespace",
    "psr-4", "use statement", "import statement",
]

# S1 — mechanical strip.
TRAILER = re.compile(
    r"^\s*(Co-Authored-By|Signed-off-by|Reviewed-by|Refs|Closes|Fixes)\s*:",
    re.IGNORECASE)
STAT_LINE = re.compile(r"\d+\s+(insertion|deletion|file)s?\s*\(", re.IGNORECASE)
PATHY = re.compile(
    r"(app/Modules|src/modules|backend-shared/src|core-backend/src|"
    r"routes/api\.php|database/(migrations|seeders)|tests?/|\.php\b|\.jsx?\b|"
    r"\.mjs\b|\.ts\b|\.json\b|\.yml\b)")

REWRITE_SYSTEM = """You rewrite git commit messages into task requests.

You are given a commit message from a web application. Rewrite it as a work
request that a non-technical product owner would send to a developer: what the
user of the application should be able to do after the change, and nothing else.

HARD CONSTRAINTS — a violation makes the output useless:
1. Never name a file, directory, class, function, database table or column.
2. Never name or allude to any internal code organisation: no talk of layers,
   of modules as units of separation, of contracts, interfaces, gateways,
   adapters, entry points, public surfaces, namespaces, or of where code
   should live in the tree.
3. Never mention testing tools, linters, checkers or CI.
4. Do NOT say how to implement it. Say only what must be possible.
5. It is fine and expected to name user-visible things: HTTP endpoints and
   their URLs, request/response fields, screens, buttons, filters, labels.
6. Write in the SAME language as the input message (Polish input -> Polish
   output; English input -> English output). No preamble, no headings, no
   bullet list unless the input enumerates several separate abilities.
7. 2-6 sentences.

Output only the rewritten request."""

# C1 thresholds — fixed before selection, applied uniformly to the whole pool.
MAX_PRODUCT_FILES = 12      # C1b
MIN_NET_MODULE_LINES = 4    # C1c
MAX_LOCALE_SHARE = 0.40     # C1d

# Paths that mark a commit as touching product code inside a module.
MODULE_PATH = re.compile(
    r"^(app/Modules/(?P<be>[A-Za-z]+)/|"
    r"src/modules/(?P<fe>[a-z0-9-]+)/)")

IS_TEST = re.compile(r"(^tests?/|/tests?/|\.test\.[jt]sx?$|Test\.php$)")
IS_LOCALE = re.compile(r"(^src/locales/|/locales/|/i18n/|^src/shared/i18n/)")
IS_DOCS = re.compile(r"(^docs/|\.md$)")

# Paths that make a change infrastructure rather than a use case.
INFRA_PATH = re.compile(
    r"^(docker/|docker-compose|\.env|\.gitignore|\.gitattributes|"
    r"\.editorconfig|\.qsyncignore|\.codex/|\.cursor/|README|Dockerfile|"
    r"entrypoint\.sh|setup-|node_modules/|dist/|vendor/|package-lock\.json|"
    r"composer\.lock|src/locales/|\.DS_Store|laravel-artisan|"
    r"eslint\.config|tailwind\.config|postcss\.config|"
    r"ftp-upload|validate-sync|litrev_login_test|testy\.txt)")

# --------------------------------------------------------------------------- #
# backend module -> PHPUnit testsuite name (phpunit.xml of the parent tree is
# the authority; this maps a module directory onto the suite that covers it).
# --------------------------------------------------------------------------- #
BE_SUITE = {
    "AdminUsers": "AdminUsersModule",
    "Auth": "AuthModule",
    "FolderItems": "FolderItemsModule",
    "Folders": "FoldersModule",
    "Harmonogram": "HarmonogramModule",
    "Messages": "MessagesModule",
    "PermissionProfiles": "PermissionProfilesModule",
    "Projects": "ProjectsModule",
    "Relationships": "RelationshipsModule",
    "Reviews": "ReviewsModule",
    "Profile": "Feature",           # phpunit.xml: tests/Feature/Profile
    "Notifications": "Feature",
    "Audit": "AdminUsersModule",
}

# frontend: the repo has ONE component-test script covering a fixed glob set;
# the suite is therefore the same for every frontend task, and the module only
# decides which module_paths limen context is scoped to.
FE_SUITE = "test:components"


# The sandbox denies reads of the user's ~/.gitconfig and ~/.config/git/ignore;
# git then aborts. Neutralise both so `git` works read-only on the live repos.
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": os.environ.get("TMPDIR", "/tmp")}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, env=GIT_ENV)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()[:300]}")
    return out.stdout


def commit_message(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s%n%n%b", sha).strip()


def commit_paths(repo: Path, sha: str) -> list[str]:
    raw = git(repo, "show", "--name-only", "--format=", sha)
    return [p for p in (l.strip() for l in raw.splitlines()) if p]


def numstat(repo: Path, sha: str) -> list[tuple[int, int, str]]:
    """(added, deleted, path) per changed file; binary files count as 0/0."""
    rows = []
    for line in git(repo, "show", "--numstat", "--format=", sha).splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        rows.append((0 if add == "-" else int(add),
                     0 if dele == "-" else int(dele), path))
    return rows


def mechanical_strip(msg: str) -> tuple[str, list[str]]:
    """S1.  Returns (stripped message, removed lines)."""
    kept, removed = [], []
    for line in msg.splitlines():
        if TRAILER.match(line) or STAT_LINE.search(line) or PATHY.search(line):
            if line.strip():
                removed.append(line.strip())
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed


def lexicon_hits(text: str) -> list[str]:
    """S3 gate.  Returns the ARCH_LEXICON terms present in `text`."""
    low = text.lower()
    return sorted({t.strip() for t in ARCH_LEXICON if t in low})


def classify(repo: str, stats: list[tuple[int, int, str]]) -> dict:
    """C1.  Measure the diff shape and evaluate C1a..C1d.

    Returns the measured values alongside the pass/fail verdict, so the audit
    file records WHY every pool member was kept or dropped.
    """
    modules: set[str] = set()
    product, module_prod, locale_ins, product_ins = [], [], 0, 0
    for add, dele, p in stats:
        if IS_LOCALE.match(p):
            locale_ins += add
            continue
        if INFRA_PATH.match(p) or IS_DOCS.match(p):
            continue
        if IS_TEST.search(p):
            continue                     # tests are not product files (C1b)
        product.append(p)
        product_ins += add
        m = MODULE_PATH.match(p)
        if m:
            modules.add(m.group("be") or m.group("fe"))
            module_prod.append((add, dele, p))

    net = sum(a for a, _, _ in module_prod) - sum(d for _, d, _ in module_prod)
    denom = product_ins + locale_ins
    share = (locale_ins / denom) if denom else 0.0

    c1 = {
        "n_module_product_files": len(module_prod),
        "n_product_files": len(product),
        "net_module_lines": net,
        "locale_share": round(share, 4),
        "c1a_pass": len(module_prod) >= 1,
        "c1b_pass": len(product) <= MAX_PRODUCT_FILES,
        "c1c_pass": net >= MIN_NET_MODULE_LINES,
        "c1d_pass": share < MAX_LOCALE_SHARE,
    }
    c1["pass"] = all(c1[k] for k in
                     ("c1a_pass", "c1b_pass", "c1c_pass", "c1d_pass"))
    return {"modules": sorted(modules), "product_paths": product, "c1": c1}


def parent_has_suite(live: Path, repo: str, parent_sha: str,
                     modules: list[str]) -> dict:
    """C2.  Does the PARENT tree actually carry the suite we would run?"""
    if repo == "litrev-backend":
        try:
            xml = git(live / repo, "show", f"{parent_sha}:phpunit.xml")
        except RuntimeError:
            return {"ok": False, "why": "no phpunit.xml at parent"}
        suites = sorted({BE_SUITE[m] for m in modules if m in BE_SUITE})
        present = [s for s in suites if f'name="{s}"' in xml]
        tree = git(live / repo, "ls-tree", "-r", "--name-only", parent_sha)
        test_paths = sorted(
            {p for p in tree.splitlines()
             if p.startswith("tests/") and p.endswith("Test.php")
             and any(seg in p for seg in modules)})
        return {"ok": bool(present) and bool(test_paths),
                "suites": present, "test_paths": test_paths,
                "why": "" if present and test_paths else
                       f"suites={present} test_paths={len(test_paths)}"}
    # frontend
    try:
        pkg = json.loads(git(live / repo, "show", f"{parent_sha}:package.json"))
    except (RuntimeError, json.JSONDecodeError):
        return {"ok": False, "why": "no package.json at parent"}
    script = (pkg.get("scripts") or {}).get(FE_SUITE)
    if not script:
        return {"ok": False, "why": f"no {FE_SUITE} script at parent"}
    tree = git(live / repo, "ls-tree", "-r", "--name-only", parent_sha)
    test_paths = sorted({p for p in tree.splitlines()
                         if ".test." in p and p.startswith("src/")
                         and "node_modules" not in p})
    return {"ok": bool(test_paths), "suites": [FE_SUITE],
            "test_paths": test_paths,
            "why": "" if test_paths else "no src/**.test.* at parent"}


def module_paths_for(repo: str, modules: list[str]) -> list[str]:
    """Workspace-relative module directories, for `limen context --paths`."""
    snap = "litrev-arch-snapshot"
    out = []
    for m in modules:
        if repo == "litrev-backend":
            out.append(f"{snap}/litrev-backend/app/Modules/{m}")
            out.append(f"{snap}/backend-shared/src/{m}")
        else:
            out.append(f"{snap}/litrev-frontend/src/modules/{m}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--live", required=True)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--audit", default=None)
    ap.add_argument("--rewrite-attempts", type=int, default=3)
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    live = Path(args.live)

    rows = list(csv.DictReader(open(args.pool, encoding="utf-8"),
                              delimiter="\t"))
    audit = {"pool_size": len(rows), "considered": [], "dropped": [],
             "arch_lexicon": ARCH_LEXICON}

    # ---- C1 + C2 --------------------------------------------------------- #
    eligible = []
    for r in rows:
        repo, sha, parent = r["repo"], r["sha"], r["parent_sha"]
        rec = {"repo": repo, "sha": sha, "parent_sha": parent,
               "date": r["date"], "subject": r["subject"]}
        stats = numstat(live / repo, sha)
        cls = classify(repo, stats)
        rec.update(n_changed=len(stats), modules=cls["modules"], c1=cls["c1"])
        if not cls["c1"]["pass"]:
            failed = [k[:3] for k in ("c1a_pass", "c1b_pass", "c1c_pass",
                                      "c1d_pass") if not cls["c1"][k]]
            rec["drop"] = "C1 failed " + ",".join(failed)
            audit["dropped"].append(rec)
            continue
        if int(r["module_tests_at_parent"]) < 1:
            rec["drop"] = "C2 module_tests_at_parent < 1"
            audit["dropped"].append(rec)
            continue
        suite = parent_has_suite(live, repo, parent, cls["modules"])
        rec["suite"] = {k: v for k, v in suite.items() if k != "test_paths"}
        rec["n_test_paths"] = len(suite.get("test_paths") or [])
        if not suite["ok"]:
            rec["drop"] = "C2 " + suite["why"]
            audit["dropped"].append(rec)
            continue
        rec["_suite_full"] = suite
        eligible.append(rec)
        audit["considered"].append({k: v for k, v in rec.items()
                                    if not k.startswith("_")})

    # ---- balance: alternate repos, newest first --------------------------- #
    by_repo: dict[str, list[dict]] = {}
    for rec in eligible:
        by_repo.setdefault(rec["repo"], []).append(rec)
    for v in by_repo.values():
        v.sort(key=lambda x: x["date"], reverse=True)
    order = sorted(by_repo, key=lambda k: -len(by_repo[k]))
    queue: list[dict] = []
    i = 0
    while any(by_repo[r] for r in order):
        r = order[i % len(order)]
        if by_repo[r]:
            queue.append(by_repo[r].pop(0))
        i += 1

    # ---- S1..S3 prompts, take the first K that pass the gate -------------- #
    tasks = []
    for rec in queue:
        if len(tasks) >= args.k:
            break
        repo, sha = rec["repo"], rec["sha"]
        original = commit_message(live / repo, sha)
        stripped, removed = mechanical_strip(original)
        prompt, attempts = None, []
        for _ in range(args.rewrite_attempts):
            res = host.llm(  # noqa: F821 — injected in the kernel
                # `temperature` is deprecated for the reasoning model and is
                # rejected by the API (verified); the sampler default is used.
                {"prompt": stripped, "max_tokens": 1600,
                 "system": REWRITE_SYSTEM,
                 "model": host.reasoning_model()})  # noqa: F821
            cand = (res.get("text") or "").strip()
            hits = lexicon_hits(cand)
            # A truncated rewrite is an invalid prompt: it would hand the agent
            # a half-sentence requirement. Treated as a failed attempt.
            truncated = res.get("stop_reason") == "max_tokens"
            attempts.append({"hits": hits, "chars": len(cand),
                             "stop_reason": res.get("stop_reason"),
                             "truncated": truncated})
            if not hits and not truncated:
                prompt = cand
                break
        if prompt is None:
            rec2 = {k: v for k, v in rec.items() if not k.startswith("_")}
            rec2["drop"] = "C3 prompt could not be de-architected"
            rec2["attempts"] = attempts
            audit["dropped"].append(rec2)
            continue
        suite = rec["_suite_full"]
        tasks.append({
            "task_id": f"{'be' if repo == 'litrev-backend' else 'fe'}-"
                       f"{len(tasks) + 1:02d}-{sha[:8]}",
            "repo": repo,
            "sha": sha,
            "parent_sha": rec["parent_sha"],
            "date": rec["date"],
            "prompt": prompt,
            "test_paths": suite["test_paths"],
            "test_suites": suite["suites"],
            "module_paths": module_paths_for(repo, rec["modules"]),
            "modules": rec["modules"],
            "source": original,
            "redaction_removed": removed,
            "rewrite_attempts": len(attempts),
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")

    audit["selected"] = [{"task_id": t["task_id"], "repo": t["repo"],
                          "sha": t["sha"], "modules": t["modules"],
                          "n_test_paths": len(t["test_paths"])}
                         for t in tasks]
    from collections import Counter
    audit["balance"] = dict(Counter(t["repo"] for t in tasks))
    ap_path = Path(args.audit or (out.parent / "tasks_audit.json"))
    ap_path.write_text(json.dumps(audit, indent=1, ensure_ascii=False),
                       encoding="utf-8")

    print(json.dumps({"selected": len(tasks), "balance": audit["balance"],
                      "eligible": len(eligible),
                      "dropped": len(audit["dropped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
