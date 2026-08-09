#!/usr/bin/env python3
"""Study 3 pilot 2 — build the layer-1 task set (§3 warstwa 1, §6 gate).

    export PYTHONPATH=<limen>/src
    python eval/study3_agent/build_tasks_layer1.py \
        > eval/study3_agent/tasks_layer1.jsonl

Layer 1 = commits that historically INTRODUCED a violation which a human
LATER REPAIRED.  That pairing is the ground truth pilot 1 lacked: the
temptation is demonstrably real (a human took it) and a correct solution
demonstrably exists (the same human wrote it afterwards).

The candidate pool is read from eval/results/study2_lifetimes.csv
(axis=workspace, variant=as-is, repaired=True) — no violation, rule id, file
or line is typed by hand here; the CSV supplies `target_rule_id`,
`target_file`, `target_line` and `target_import` for every task.

POOL ATTRITION (recorded, not hidden — the brief listed 7 candidates)
---------------------------------------------------------------------
Three of the seven cannot become tasks, for reasons measured here and
asserted at build time by ``audit_pool()``:

* ``c32d1719`` and ``9cf5ac79`` are ROOT commits (``git rev-parse <sha>^``
  fails).  A task is "implement this change on the parent tree"; a root
  commit has no parent tree, so there is nothing to hand the agent.
* ``eab22e21``'s parent ``b2a70b49`` (2026-03-26) predates modularisation:
  ``app/`` holds Console/Exceptions/Http/Models/... and no ``app/Modules``
  at all.  Its 12 violations are all K5 allowlists scoped to
  ``app/Modules/*/Http/**`` paths that do not exist in the parent, so the
  rules cannot fire — exactly the pilot-1 failure mode (redesign §2.1), and
  the reason that document's "n ≈ 7" is optimistic.

That leaves FOUR usable commits.  Two of them (``46bbf692``, ``c1d3e424``)
carry violations of several distinct rules in several distinct files, so
they are CUT into one subtask per targeted violation, as the brief requires
for large commits.  Six tasks result: 3 backend / 3 frontend, five distinct
target rules over four rule kinds (K5, K7, K8, K10).

PROMPTS
-------
Each prompt states a BUSINESS GOAL only.  No layer names, no module names as
boundaries, no "use Contracts", no mention that an architecture check exists.
The original commit message is preserved verbatim in ``source`` for audit.
``author_hypothesis`` records which boundary the author expects the task to
tempt and why — condition A's result then confirms or refutes it.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
LIMEN = EVAL.parent
LIVE = LIMEN.parent / "live"
LIFETIMES = EVAL / "results" / "study2_lifetimes.csv"

GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"}

# The seven layer-1 candidates named in the brief, with the disposition this
# script asserts rather than assumes.
POOL = {
    "46bbf692": "litrev-backend",
    "9cf5ac79": "litrev-frontend",
    "eab22e21": "litrev-backend",
    "c1d3e424": "litrev-frontend",
    "82915ff2": "litrev-frontend",
    "c32d1719": "litrev-backend",
    "0334bc8f": "litrev-frontend",
}


def git(repo: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(LIVE / repo), *args],
                       capture_output=True, text=True, env=GIT_ENV)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:200]}")
    return p.stdout


def first_parent(repo: str, sha: str) -> str | None:
    parents = git(repo, "show", "-s", "--format=%P", sha).split()
    return parents[0] if parents else None


def load_pool() -> dict[str, list[dict]]:
    rows = [r for r in csv.DictReader(LIFETIMES.open(encoding="utf-8"))
            if r["axis"] == "workspace" and r["variant"] == "as-is"
            and r["repaired"] == "True"]
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["introduced_sha"][:8], []).append(r)
    return out


def audit_pool(pool: dict[str, list[dict]]) -> dict:
    """Why each of the seven candidates is or is not usable — measured."""
    audit = {}
    for short, repo in POOL.items():
        rows = pool.get(short, [])
        parent = first_parent(repo, short)
        rec = {
            "repo": repo,
            "n_violations": len(rows),
            "rules": sorted({r["rule_id"] for r in rows}),
            "parent_sha": parent,
            "date": git(repo, "show", "-s", "--format=%cI", short).strip(),
        }
        if parent is None:
            rec["usable"] = False
            rec["reason"] = ("root commit — no parent tree exists, so there "
                             "is no state on which to pose the task")
        else:
            has_modules = bool(
                git(repo, "ls-tree", "--name-only", parent,
                    "app/Modules/" if repo == "litrev-backend"
                    else "src/modules/").strip())
            rec["parent_date"] = git(repo, "show", "-s", "--format=%cI",
                                     parent).strip()
            rec["parent_has_module_layout"] = has_modules
            if not has_modules and repo == "litrev-backend":
                rec["usable"] = False
                rec["reason"] = ("parent tree predates modularisation "
                                 "(no app/Modules); every violated rule is "
                                 "scoped to app/Modules/** paths that do not "
                                 "exist there, so no rule can fire — the "
                                 "pilot-1 failure mode")
            else:
                rec["usable"] = True
                rec["reason"] = "parent tree exists and carries the layout the rules scope to"
        audit[short] = rec
    return audit


def find_violation(pool: dict[str, list[dict]], short: str, rule_id: str,
                   file_suffix: str, line: str | None = None) -> dict:
    """The one CSV row a task targets — never hand-typed.

    A (rule, file) pair can hold several occurrences: c1d3e424 introduced
    `raw-success-response-data` TWICE in UsersManagement.jsx (L331 and
    L1205).  Those tasks pass the earliest line explicitly; every other task
    matches a unique row and passes line=None.  `n_occurrences_in_file` is
    carried into the task so the count is visible in the results rather than
    silently collapsed.
    """
    hits = [r for r in pool[short]
            if r["rule_id"] == rule_id and r["file"].endswith(file_suffix)]
    if not hits:
        raise SystemExit(f"{short}/{rule_id}/{file_suffix}: no matching row")
    if line is not None:
        hits = [r for r in hits if r["line_first"] == line]
    elif len(hits) != 1:
        raise SystemExit(f"{short}/{rule_id}/{file_suffix}: {len(hits)} rows "
                         f"at lines {[r['line_first'] for r in hits]} — pass "
                         f"`line=` to disambiguate")
    if len(hits) != 1:
        raise SystemExit(f"{short}/{rule_id}/{file_suffix}@{line}: "
                         f"{len(hits)} matching rows, expected exactly 1")
    return hits[0]


# --------------------------------------------------------------------------- #
# The six tasks.  `prompt` is the business goal; everything factual about the
# targeted violation is looked up from the CSV by (rule_id, file suffix).
#
# LANGUAGE OF THE PROMPTS — DELIBERATE, DO NOT TRANSLATE
# ------------------------------------------------------
# Every `prompt` below is written in Polish, and stays that way.  LitRev is a
# Polish-language repository: its commit messages, identifiers and in-code
# comments are Polish, so a Polish assignment is what a developer on this
# project would actually receive, and it is the material the agent was given
# in the pilot-2 run.  These exact strings are already recorded in
# ``tasks_layer1.jsonl``, which is the record of what was run; translating
# them here would desynchronise the generator from the executed experiment
# and invalidate reproduction.  `author_hypothesis` and every comment are
# English, because they are documentation rather than experimental input.
# --------------------------------------------------------------------------- #

SPECS = [
    dict(
        task_id="L1-be-01-notif-domain",
        short="46bbf692", repo="litrev-backend",
        rule="notifications-domain-baseline",
        file_suffix="Projects/Application/ChangeProjectOwnerAction.php",
        modules=["Projects"], suites=["ProjectsModule"],
        module_paths=["litrev-arch-snapshot/litrev-backend/app/Modules/Projects"],
        prompt=(
            "Gdy właściciel projektu zostaje zmieniony, obie strony powinny "
            "dostać o tym wiadomość e-mail: dotychczasowy właściciel — że "
            "projekt został mu odebrany, nowy — że go przejął. Treść ma "
            "zawierać nazwę projektu oraz imiona i nazwiska poprzedniego i "
            "nowego właściciela. Zmiana właściciela działa dziś w dwóch "
            "wariantach — dla projektu podpiętego pod folder i dla projektu "
            "wolnostojącego — powiadomienie ma powstawać w obu. Wysyłka musi "
            "respektować ustawienia powiadomień użytkownika: kto wyłączył ten "
            "rodzaj maili, nie dostaje go."
        ),
        hypothesis=(
            "The task forces the Projects module's code to name the KIND of "
            "notification. The only existing catalogue of kinds is "
            "App\\Modules\\Notifications\\Domain\\MailNotificationType — a fixed "
            "class holding a list of constants, temptingly simple to import. "
            "Boundary tempted: Projects -> Notifications\\Domain, policed by "
            "the baseline notifications-domain-baseline (the baseline is "
            "empty, so EVERY such import is a violation). A human yielded "
            "exactly here: 46bbf692 added that import in 40 files; the repair "
            "8e005bf0 moved the catalogue to backend-shared. A violation-free "
            "alternative exists in the parent tree: a domain event + listener, "
            "a pattern already used by Auth (Application/Events + "
            "Application/Listeners)."
        ),
    ),
    dict(
        task_id="L1-be-02-notif-domain-harmonogram",
        short="46bbf692", repo="litrev-backend",
        rule="notifications-domain-baseline",
        file_suffix="Harmonogram/Application/CreateProjectScheduleTaskAction.php",
        modules=["Harmonogram"], suites=["HarmonogramModule"],
        module_paths=["litrev-arch-snapshot/litrev-backend/app/Modules/Harmonogram"],
        prompt=(
            "Po utworzeniu nowego zadania w harmonogramie projektu osoby "
            "zaangażowane w ten projekt powinny dostać e-mail z informacją, że "
            "zadanie się pojawiło — z tytułem zadania i terminami, jeśli je "
            "podano. Ma to działać przy zakładaniu zadania na projekcie; "
            "użytkownik, który sam je utworzył, nie powinien dostawać "
            "wiadomości o własnym działaniu, a ustawienia powiadomień "
            "użytkownika mają być respektowane."
        ),
        hypothesis=(
            "The same temptation as L1-be-01, but in a different module and "
            "with a different data flow (a schedule task instead of a "
            "project) — it serves to check whether the violation is a property "
            "of the boundary or of a particular file. Boundary tempted: "
            "Harmonogram -> Notifications\\Domain. A human yielded in 11 files "
            "of the Harmonogram module in 46bbf692."
        ),
    ),
    dict(
        task_id="L1-be-03-messages-allowlist",
        short="46bbf692", repo="litrev-backend",
        rule="notifications-application-messages-allowlist",
        file_suffix="Listeners/SendMessageDomainMailNotification.php",
        modules=["Notifications"], suites=["Feature"],
        module_paths=["litrev-arch-snapshot/litrev-backend/app/Modules/Notifications"],
        prompt=(
            "Chcemy, żeby komentarze do elementów projektu powodowały "
            "wysyłkę powiadomień e-mail. Gdy w wątku pojawia się nowa "
            "wiadomość albo odpowiedź na istniejącą, pozostali uczestnicy "
            "wątku powinni dostać maila z tytułem i fragmentem treści oraz "
            "odnośnikiem do miejsca, którego wiadomość dotyczy. Autor "
            "wiadomości nie dostaje powiadomienia o własnym wpisie, a "
            "ustawienia powiadomień odbiorcy mają być respektowane. Sama "
            "obsługa wiadomości powstaje równolegle; twoim zadaniem jest "
            "strona wysyłkowa — reakcja na fakt „pojawiła się nowa wiadomość” "
            "i doprowadzenie maila do właściwych odbiorców."
        ),
        hypothesis=(
            "The task makes the dispatch code in Notifications react to an "
            "event belonging to another module. The shortest route is to "
            "import the event class from "
            "App\\Modules\\Messages\\Application\\Events — that is, the "
            "Application layer of a foreign module, policed by the empty "
            "allowlist notifications-application-messages-allowlist. A human "
            "yielded exactly here (46bbf692, "
            "SendMessageDomainMailNotification.php:5); the repair 8e005bf0 "
            "moved the event to backend-shared."
        ),
    ),
    dict(
        task_id="L1-fe-04-message-fallback",
        short="c1d3e424", repo="litrev-frontend",
        rule="local-message-fallback",
        file_suffix="src/pages/AccountDetails.jsx",
        modules=[], suites=["test"],
        # 90f87301 (2026-03-22) has no test:components / test:unit script; its
        # only suite is `test` -> node --test over src/lib (9 tests, verified).
        npm_test_script="test",
        # ...and no src/modules or src/shared/http either, so the harness's
        # default frontend roots select nothing (measured: 0 B).  Name the
        # roots this tree actually has.
        source_roots=["src/pages", "src/lib", "src/auth", "src/App.jsx"],
        module_paths=["litrev-arch-snapshot/litrev-frontend/src/pages"],
        prompt=(
            "Na stronie szczegółów konta użytkownik ma móc usunąć swoje konto. "
            "Przycisk pokazuje się tylko wtedy, gdy polityka instancji na to "
            "pozwala (backend wystawia tę informację), a samo usunięcie "
            "wymaga potwierdzenia w okienku dialogowym. Po udanym usunięciu "
            "użytkownik zostaje wylogowany i przeniesiony na ekran logowania. "
            "Odmowy trzeba pokazać zrozumiale: gdy funkcja jest wyłączona "
            "przez administratora, gdy usuwający jest ostatnim administratorem "
            "systemu, oraz w pozostałych przypadkach — komunikat ma być "
            "czytelny i przetłumaczony."
        ),
        hypothesis=(
            "The requirement \"show the message from the backend, and if there "
            "is none, a translated fallback text\" is written most briefly in "
            "JSX as `message || t(...)` at every error branch. Boundary "
            "tempted: the fallback chain spreading across page files instead "
            "of one shared place (pattern-ban local-message-fallback). A human "
            "yielded in 6 files in c1d3e424, including this file (L126)."
        ),
    ),
    dict(
        task_id="L1-fe-05-raw-response-data",
        short="c1d3e424", repo="litrev-frontend",
        rule="raw-success-response-data",
        file_suffix="src/pages/UsersManagement.jsx", target_line="331",
        modules=[], suites=["test"],
        # 90f87301 (2026-03-22) has no test:components / test:unit script; its
        # only suite is `test` -> node --test over src/lib (9 tests, verified).
        npm_test_script="test",
        # ...and no src/modules or src/shared/http either, so the harness's
        # default frontend roots select nothing (measured: 0 B).  Name the
        # roots this tree actually has.
        source_roots=["src/pages", "src/lib", "src/auth", "src/App.jsx"],
        module_paths=["litrev-arch-snapshot/litrev-frontend/src/pages"],
        prompt=(
            "Administrator potrzebuje ekranu zarządzania użytkownikami: lista "
            "kont z wyszukiwaniem i stronicowaniem, podgląd szczegółów konta, "
            "blokowanie i odblokowywanie, wymuszenie zmiany hasła oraz zmiana "
            "ról. Lista i akcje korzystają z API administracyjnego; ekran ma "
            "sobie radzić z tym, że część odpowiedzi zwraca dane w kopercie, a "
            "część bezpośrednio, i w obu przypadkach pokazywać poprawną "
            "zawartość zamiast pustej tabeli."
        ),
        hypothesis=(
            "The last sentence of the prompt describes a real problem of this "
            "API and tempts directly towards defusing it at the point of use: "
            "`response?.data ?? response`. Boundary tempted: the shape of the "
            "success response spread across page files instead of one helper "
            "(pattern-ban raw-success-response-data). A human yielded twice in "
            "this file (c1d3e424, L331 and L1205). Note: in the human's hands "
            "the same file grew to 4050 lines, so the task also exercises the "
            "oversized-page-file threshold (K10) — both rules are in the list "
            "of rules that can be violated."
        ),
    ),
    dict(
        task_id="L1-fe-06-oversized-page",
        short="0334bc8f", repo="litrev-frontend",
        rule="oversized-page-file",
        file_suffix="projects-view/pages/ProjectDetailPage.jsx",
        modules=["projects-list"], suites=["test:components"],
        module_paths=["litrev-arch-snapshot/litrev-frontend/src/modules/projects-list"],
        prompt=(
            "Potrzebny jest szczegółowy widok pojedynczego projektu, otwierany "
            "z listy projektów. Ma pokazywać dane projektu, drzewo jego "
            "folderów z zawartością wybranego folderu, dokumenty projektu oraz "
            "menu akcji (edycja, udostępnianie, usunięcie) — wszystko na jednym "
            "ekranie, z tłumaczeniami i czasem względnym („2 dni temu”) przy "
            "datach. Widok ma być osiągalny pod własnym adresem i podpięty do "
            "nawigacji."
        ),
        hypothesis=(
            "The task is overtly multi-functional (\"all on one screen\") and "
            "contains no hint about decomposition — it tempts towards writing "
            "one large page file. Boundaries tempted: the page file length "
            "threshold (oversized-page-file, 600 lines) and — because the view "
            "is a new module — the structural requirements of a frontend "
            "module (index.js as the entrypoint, a README with three "
            "sections). A human yielded on both: 0334bc8f produced a "
            "ProjectDetailPage.jsx of 625 lines and a README without the "
            "required sections; the repair is 4022b678."
        ),
    ),
]


def build() -> tuple[list[dict], dict]:
    pool = load_pool()
    audit = audit_pool(pool)
    tasks = []
    for spec in SPECS:
        short, repo = spec["short"], spec["repo"]
        if not audit[short]["usable"]:
            raise SystemExit(f"{spec['task_id']}: base commit {short} is "
                             f"unusable ({audit[short]['reason']})")
        row = find_violation(pool, short, spec["rule"], spec["file_suffix"],
                             spec.get("target_line"))
        n_in_file = sum(1 for r in pool[short]
                        if r["rule_id"] == spec["rule"]
                        and r["file"] == row["file"])
        sha = row["introduced_sha"]
        parent = first_parent(repo, sha)
        task = {
            "task_id": spec["task_id"],
            "layer": 1,
            "repo": repo,
            "sha": sha,
            "parent_sha": parent,
            "date": row["introduced_date"][:10],
            "prompt": spec["prompt"],
            "modules": spec["modules"],
            "module_paths": spec["module_paths"],
            "test_suites": spec["suites"],
            # Ground truth, straight from study2_lifetimes.csv:
            "target_rule_id": row["rule_id"],
            "target_rule_kind": row["kind"],
            "target_rule_K": row["K"],
            "target_file": row["file"],
            "target_line": row["line_first"] or None,
            "target_import": row["target"] or None,
            "repaired_sha": row["repaired_sha"],
            "repaired_date": row["repaired_date"][:10],
            "lifetime_days": round(float(row["lifetime_days"]), 2),
            "n_occurrences_in_file": n_in_file,
            "author_hypothesis": spec["hypothesis"],
            "source": git(repo, "show", "-s", "--format=%B", sha).strip(),
            "commit_files_changed": int(
                git(repo, "show", "--stat", "--format=", sha)
                .strip().splitlines()[-1].split()[0]),
        }
        for opt in ("npm_test_script", "source_roots"):
            if spec.get(opt):
                task[opt] = spec[opt]
        tasks.append(task)
    return tasks, audit


def main() -> int:
    tasks, audit = build()
    (EVAL / "study3_agent" / "tasks_layer1_pool_audit.json").write_text(
        json.dumps(audit, indent=1, ensure_ascii=False), encoding="utf-8")
    for t in tasks:
        print(json.dumps(t, ensure_ascii=False))
    n_usable = sum(1 for v in audit.values() if v["usable"])
    print(f"[build_tasks_layer1] {len(tasks)} tasks from {n_usable}/7 usable "
          f"layer-1 commits; rules="
          f"{sorted({t['target_rule_id'] for t in tasks})}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())