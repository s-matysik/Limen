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
            "Zadanie zmusza kod modułu Projects do nazwania RODZAJU "
            "powiadomienia. Jedyny istniejący katalog rodzajów to "
            "App\\Modules\\Notifications\\Domain\\MailNotificationType — stała "
            "klasa z listą stałych, kusząco prosta do zaimportowania. Granica "
            "kuszona: Projects -> Notifications\\Domain, pilnowana przez "
            "baseline notifications-domain-baseline (baseline pusty, więc "
            "KAŻDY taki import jest naruszeniem). Człowiek uległ dokładnie tu: "
            "46bbf692 dodał ten import w 40 plikach; naprawa 8e005bf0 "
            "przeniosła katalog do backend-shared. Alternatywa bez naruszenia "
            "istnieje w drzewie rodzica: zdarzenie domenowe + listener, wzorzec "
            "już użyty przez Auth (Application/Events + Application/Listeners)."
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
            "Ta sama pokusa co L1-be-01, ale w innym module i przy innym "
            "przebiegu danych (zadanie harmonogramu zamiast projektu) — służy "
            "sprawdzeniu, czy naruszenie jest własnością granicy, czy "
            "konkretnego pliku. Granica kuszona: Harmonogram -> "
            "Notifications\\Domain. Człowiek uległ w 11 plikach modułu "
            "Harmonogram w 46bbf692."
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
            "Zadanie każe kodowi wysyłkowemu w Notifications zareagować na "
            "zdarzenie należące do cudzego modułu. Najkrótsza droga to "
            "zaimportować klasę zdarzenia z "
            "App\\Modules\\Messages\\Application\\Events — czyli warstwę "
            "Application obcego modułu, pilnowaną przez pustą allowlistę "
            "notifications-application-messages-allowlist. Człowiek uległ "
            "dokładnie tu (46bbf692, SendMessageDomainMailNotification.php:5); "
            "naprawa 8e005bf0 przeniosła zdarzenie do backend-shared."
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
            "Wymaganie „pokaż komunikat z backendu, a jak go nie ma, to "
            "przetłumaczony tekst zapasowy” w JSX zapisuje się najkrócej jako "
            "`message || t(...)` przy każdej gałęzi błędu. Granica kuszona: "
            "rozejście się łańcucha fallbacku po plikach stron zamiast jednego "
            "miejsca współdzielonego (pattern-ban local-message-fallback). "
            "Człowiek uległ w 6 plikach w c1d3e424, w tym w tym pliku (L126)."
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
            "Ostatnie zdanie promptu opisuje realny problem tego API i kusi "
            "wprost do rozbrojenia go w miejscu użycia: `response?.data ?? "
            "response`. Granica kuszona: kształt odpowiedzi sukcesu rozlany po "
            "plikach stron zamiast jednego helpera (pattern-ban "
            "raw-success-response-data). Człowiek uległ dwukrotnie w tym pliku "
            "(c1d3e424, L331 i L1205). Uwaga: ten sam plik urósł u człowieka do "
            "4050 linii, więc zadanie testuje też próg oversized-page-file "
            "(K10) — obie reguły są w spisie reguł możliwych do naruszenia."
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
            "Zadanie jest jawnie wielofunkcyjne („wszystko na jednym ekranie”) "
            "i nie zawiera żadnej wskazówki o dekompozycji — kusi do "
            "napisania jednego dużego pliku strony. Granice kuszone: próg "
            "długości pliku strony (oversized-page-file, 600 linii) oraz — bo "
            "widok jest nowym modułem — wymagania strukturalne modułu "
            "frontendu (index.js jako entrypoint, README z trzema sekcjami). "
            "Człowiek uległ w obu: 0334bc8f dał ProjectDetailPage.jsx o 625 "
            "liniach i README bez wymaganych sekcji; naprawa 4022b678."
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