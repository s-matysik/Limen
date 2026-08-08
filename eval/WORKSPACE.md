# The LitRev evaluation workspace

This document describes the frozen **six-repository** workspace that every
number in the limen study is computed against, why it is laid out the way it
is, and how to rebuild it from scratch.

Note the two different counts that recur below, both correct:

- **six repositories** are physically copied and independently frozen —
  litrev-backend, backend-shared, litrev-core-backend, litrev-frontend,
  litrev-core-frontend, **frontend-shared**;
- **five roots** are declared in `limen/examples/litrev/architecture.yaml`
  (`host_app`, `shared`, `core`, `frontend`, `core_frontend`) and therefore
  five appear in the extracted-population table.

`frontend-shared` is the difference: it is part of the frozen snapshot because
the frontend repo depends on it (`@litrev/frontend-common`,
`npm --prefix /app-shared/frontend-shared`) and the legacy `*:common` checkers
run against it, but the Tier-1 limen spec does not yet scan it as a root. Its
files are counted in the manifest totals and not in the extractor totals.

## Why a workspace and not the bare zip exports

The legacy architecture checkers that limen is being compared against are not
path-agnostic. They assume the *deployed* layout, in which two of the shared
repositories appear **inside** the host backend repository:

| Assumption | Source | Consequence for the layout |
| --- | --- | --- |
| `repositories: [{ type: path, url: "shared", symlink: true }]` | `litrev-backend/composer.json` | `shared/` must exist at the root of the backend repo and resolve to backend-shared. |
| autoload psr-4 `LitRev\CoreBackend\` → `core-backend/src/` | `litrev-backend/composer.json` | `core-backend/` must exist at the root of the backend repo and resolve to litrev-core-backend. |
| `require base_path('shared') . '/config/architecture.php'` | `litrev-backend/tests/Architecture/SharedBoundaryRulesTest.php:14,17` | the same `shared/` link must expose `config/architecture.php`. |
| `npm --prefix /app-shared/frontend-shared …` | `litrev-frontend/package.json` (`lint:common`, `test:common`, `audit:architecture:common`) | frontend-shared must be reachable from the frontend repo; in containers it is the absolute path `/app-shared/frontend-shared`. |
| `"@litrev/frontend-common": "file:/app-shared/frontend-shared"` | `litrev-frontend/package.json` dependencies | same. |
| root prefix `litrev-arch-snapshot/<repo>/…` | `limen/examples/litrev/architecture.yaml` (`roots:` and every `units.discovery` pattern) | the limen spec addresses repositories through a `litrev-arch-snapshot/` prefix. |

The zip exports carry none of that structure, so the workspace re-creates it
with symlinks over a single physical copy of each repository. Nothing is
duplicated on disk, and `LitRev/` itself is never modified.

## Layout

```
/Users/sebastianmatysik/!!!!!Limen/workspace/
├── litrev-backend/                     ← copy of LitRev/litrev-backend-main
│   ├── shared/         -> ../backend-shared           (symlink)
│   ├── core-backend/   -> ../litrev-core-backend      (symlink)
│   ├── app/                                 limen root `host_app`
│   └── tests/Architecture/                  legacy PHP checkers
├── backend-shared/                     ← copy of LitRev/backend-shared-main
│   └── src/                                 limen root `shared`
├── litrev-core-backend/                ← copy of LitRev/litrev-core-backend-main
│   └── src/                                 limen root `core`
├── litrev-frontend/                    ← copy of LitRev/litrev-frontend-main
│   ├── app-shared/frontend-shared/ -> ../../frontend-shared   (symlink)
│   ├── src/                                 limen root `frontend`
│   └── scripts/audit-frontend-architecture.mjs   legacy JS checker
├── litrev-core-frontend/               ← copy of LitRev/litrev-core-frontend-main
│   └── src/                                 limen root `core_frontend`
├── frontend-shared/                    ← copy of LitRev/frontend-shared-main
└── litrev-arch-snapshot/               spec-facing view: one symlink per repo
    ├── litrev-backend       -> ../litrev-backend
    ├── backend-shared       -> ../backend-shared
    ├── litrev-core-backend  -> ../litrev-core-backend
    ├── litrev-frontend      -> ../litrev-frontend
    ├── litrev-core-frontend -> ../litrev-core-frontend
    └── frontend-shared      -> ../frontend-shared
```

### Production-path mapping

| Production path | Workspace path |
| --- | --- |
| `<backend>/shared` (composer path repository) | `workspace/litrev-backend/shared` → `workspace/backend-shared` |
| `<backend>/core-backend` (psr-4 `LitRev\CoreBackend\`) | `workspace/litrev-backend/core-backend` → `workspace/litrev-core-backend` |
| `/app-shared/frontend-shared` (container mount) | `workspace/litrev-frontend/app-shared/frontend-shared` → `workspace/frontend-shared` |
| `/app/scripts/audit-frontend-architecture.mjs` (container mount, used by frontend-shared's `audit:architecture`) | `workspace/litrev-frontend/scripts/audit-frontend-architecture.mjs` — run it with `--prefix` from the frontend repo, or bind-mount as in the compose file |
| limen spec roots `litrev-arch-snapshot/<repo>` | `workspace/litrev-arch-snapshot/<repo>` |

`/app-shared/frontend-shared` and `/app/…` are absolute container paths. The
workspace reproduces the *relative* equivalent; running the frontend
`*:common` npm scripts outside a container needs `--prefix
app-shared/frontend-shared` instead of the absolute path. The repo-local
`audit:architecture` script (which is what the comparison actually uses) needs
no adjustment and runs green on the workspace:

```
cd workspace/litrev-frontend && node scripts/audit-frontend-architecture.mjs
→ Frontend architecture audit passed: no blocked anti-patterns found.  (exit 0)
```

## What was excluded from the copy, and why

| Excluded | Reason |
| --- | --- |
| `litrev-backend/docker/mysql/data/` (161 MB) | MySQL data volume — a container runtime artefact, not source. Its presence would dominate the repo size and add non-deterministic binary content. |
| `node_modules/`, `vendor/` | not present in the exports anyway; excluded defensively so a later `npm install` / `composer install` cannot change the snapshot identity. |
| `__pycache__/`, `.DS_Store` | build/OS noise. |
| `litrev-backend/.env.testing`, `litrev-frontend/.env.production`, `litrev-frontend/.env.local` | **read-blocked by the sandbox's secret protection** — these three files could not be copied. They are the only respect in which the workspace is not a bit-exact copy of `LitRev/`. Because `.env*` files are excluded from the manifest scope by rule (see below), this has no effect on any tree-id or reported figure. |

Everything else — including `composer.json`, `composer.lock`, `package.json`,
docs, and the legacy test suites — is copied verbatim.

## Snapshot identity

The exports contain no `.git`, so there are no commit SHAs to cite. Identity is
therefore content-addressed:

```
manifest(repo) = sorted [ (relative_path, sha256(file_bytes)) ]   → eval/manifests/<repo>.tsv
tree-id(repo)  = sha256(manifest bytes)
workspace-id   = sha256( concat "<repo>\t<tree-id>\n" over repos in fixed order )
```

Manifests cover source files only. Excluded from identity: the directories and
files listed above, binary/asset suffixes (images, fonts, archives, media,
compiled objects), and **every `.env*` file** — those are per-developer secrets,
never read by an extractor, different on every machine, and unreadable under
hardened sandboxes; excluding them by rule is what makes the tree-id
reproducible wherever the workspace is rebuilt. Symlinks are never followed, so
the `shared/` and `core-backend/` links do not double-count.

Frozen values for this snapshot (`eval/frozen-commits.txt`):

| repo | files | tree-id (sha256) |
| --- | ---: | --- |
| litrev-backend | 304 | `5c93950e4ce0a9caf63bd75b36cfa1057bff31f505cfdc7af98e430e90520c44` |
| backend-shared | 291 | `aeb2afad253f3bec1f2c44b3f41f99eb10db6312b354dc20c2c9f4d216143a42` |
| litrev-core-backend | 23 | `29a9de7b1a87f697d6d59d6532131f840b3a5b728309048495da60c72e944fdb` |
| litrev-frontend | 212 | `0c521febf0219ee2819c4f74d7323ef741e74675b516eba7adfd915a594127ac` |
| litrev-core-frontend | 30 | `26a2d7d77205e8540da4233b53762686fa79049d4ba74148244cf1a9d061d1fc` |
| frontend-shared | 202 | `8b6911ff54120f144ccf3e8c12a401875982a647344fd881413cd8aa3de1618b` |
| **WORKSPACE** | **1062** | `562c232df26dab5309088db63e3ac82723d9643479a679b8dd2ad9415a0bbe31` |

These manifests were verified to be **byte-identical** to manifests computed
directly over the untouched `LitRev/*-main/` source trees (with
`docker/mysql/data/` filtered out), which is the evidence that the copy step
introduced no drift and that the tree-ids do not depend on this machine.

When live clones replace the exports, `freeze_snapshot.py` detects `.git`
automatically and records `git:<SHA>` in the fourth column **next to** the
tree-id, so both identifiers stay available and cross-checkable.

## Extracted population

`limen extract` over the five spec roots — a subset of the 1062 manifest files:
only `.php/.js/.jsx/.ts/.tsx`, only under each root's `path` (so e.g.
`litrev-backend/tests/`, `config/`, `database/` are frozen but not extracted),
and none of frontend-shared, which is frozen but not a Tier-1 root:

| root | repo | files | deps |
| --- | --- | ---: | ---: |
| host_app | litrev-backend/app | 99 | 725 |
| shared | backend-shared/src | 258 | 897 |
| core | litrev-core-backend/src | 7 | 46 |
| frontend | litrev-frontend/src | 148 | 540 |
| core_frontend | litrev-core-frontend/src | 27 | 66 |
| **total** | | **539** | **2274** |

8 dependencies stay unresolved, all in the frontend root and all non-source
assets (`@/assets/*.png|svg`, `./index.css`, four `../../locales/*.json`
imports); `unresolved_policy.default: warn` reports them as warnings.
`limen check` on this snapshot: **0 errors, 8 warnings, exit 0**.

The research plan quotes 538 files / 2270 dependencies. The measured figures on
this snapshot are **539 / 2274** — the plan's numbers are close but not exact
and should be replaced by the measured ones.

## Rebuilding

The workspace is a derived artefact; `LitRev/` is the source of truth.

```bash
# 1. materialise the workspace (idempotent; re-running does not modify LitRev/)
python - <<'PY'
import shutil, datetime
from pathlib import Path
BASE = Path("/Users/sebastianmatysik/!!!!!Limen"); L = BASE/"LitRev"; W = BASE/"workspace"
IGNORE = {"node_modules","vendor",".DS_Store","__pycache__"}
def ignore(d, names):
    out = {n for n in names if n in IGNORE}
    if Path(d).name == "mysql" and "data" in names: out.add("data")
    return out
MAP = [("litrev-backend-main","litrev-backend"),("backend-shared-main","backend-shared"),
       ("litrev-core-backend-main","litrev-core-backend"),("litrev-frontend-main","litrev-frontend"),
       ("litrev-core-frontend-main","litrev-core-frontend"),("frontend-shared-main","frontend-shared")]
W.mkdir(exist_ok=True)
for src, dst in MAP:
    shutil.copytree(L/src, W/dst, ignore=ignore, symlinks=True, dirs_exist_ok=True)
def link(t, l):
    if not l.exists() and not l.is_symlink(): l.symlink_to(t)
link(Path("../backend-shared"), W/"litrev-backend/shared")
link(Path("../litrev-core-backend"), W/"litrev-backend/core-backend")
(W/"litrev-frontend/app-shared").mkdir(exist_ok=True)
link(Path("../../frontend-shared"), W/"litrev-frontend/app-shared/frontend-shared")
(W/"litrev-arch-snapshot").mkdir(exist_ok=True)
for _, dst in MAP: link(Path(f"../{dst}"), W/"litrev-arch-snapshot"/dst)
PY

# 2. re-derive the identity and verify it matches the frozen values
python eval/freeze_snapshot.py
python eval/freeze_snapshot.py --check      # exit 1 on any drift

# 3. re-derive the population figures and the determinism claim
python eval/determinism_check.py            # exit 1 if any run differs bytewise
```

A rebuild on an unrestricted machine will additionally copy the three `.env*`
files the sandbox blocked here. Because `.env*` is excluded from manifest scope
by rule, that rebuild reproduces **exactly** the tree-ids above; `python
eval/freeze_snapshot.py --check` is the assertion, and it exits 1 on any drift.

## Cache directory

`limen` writes a `.limen/` cache into the workspace root. It is derived state,
excluded from manifests, and `determinism_check.py` deletes it before the cold
run of every command.
