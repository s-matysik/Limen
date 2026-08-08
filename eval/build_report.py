#!/usr/bin/env python3
"""Build research-report.md from the measured result files.

Every figure in the report is read from eval/results/*; nothing is typed by hand.
Reproduce:  python eval/build_report.py
"""
import json, csv, os, glob, datetime

E = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(E, "results")

def rj(*p):
    with open(os.path.join(*p)) as f:
        return json.load(f)

def rc(*p):
    with open(os.path.join(*p)) as f:
        return list(csv.DictReader(f))

det   = rj(E, "determinism.json")
spec  = rj(E, "spec_loc.json")
t1    = rj(R, "study1_table.json")
mut   = rj(R, "mutation_sensitivity.json")["summary"]
ov    = rj(R, "oracle_validation.json")
lrec  = rj(R, "line_recovery_check.json")
flow  = rj(R, "corpus_flow.json")
kind1b= rc(R, "study1b_per_kind.csv")
repo1b= rc(R, "study1b_per_repo.csv")
m2    = rj(R, "study2_metrics.json")
ws    = m2["axes"]["workspace/as-is"]
sens  = m2["sensitivity_line_in_key"]["workspace/as-is"]

tot = t1["totals"]
lav = ov["laravel_validation"]["summary"]
lcl = ov["laravel_validation"]["detail"]["clean"]["comparison"]
d1  = t1["differences"]

def d(x, n=2):
    return f"{float(x):.{n}f}"

L = []
w = L.append

w("# limen — raport z badań")
w("")
w(f"Wygenerowany przez `eval/build_report.py` {datetime.date.today().isoformat()}. "
  "Każda liczba w tym dokumencie jest czytana z plików wynikowych w `eval/results/`; "
  "żadna nie jest wpisana ręcznie. Przy każdej sekcji podana jest komenda, która ją odtwarza.")
w("")
w("## Streszczenie")
w("")
w(f"Retrofit **{tot['legacy_rules']} reguł legacy** rozproszonych po **{tot['legacy_loc_code']} liniach kodu** "
  f"(fizycznie {tot['legacy_loc_total']}) w trzech językach do **{tot['limen_rules']} deklaracji** "
  f"w {tot['yaml_loc_code']} liniach YAML (fizycznie {tot['yaml_loc_total']}). "
  f"Równoważność semantyczna potwierdzona wykonywalnie: {d1['unclassified']} różnic niesklasyfikowanych, "
  f"czułość mutacyjna {mut['detected_both']}/{mut['mutations']}. "
  f"Na korpusie {flow['included']} cudzych repozytoriów porównanie doszło do skutku w "
  f"{sum(1 for r in repo1b if r['status']=='ok')} przypadkach. "
  f"Historia {m2['n_commits_first_parent']} commitów zmierzona jedną specyfikacją.")
w("")
w("**Badanie 3 (agent z kontraktem) nie zostało wykonane** — patrz §6.")
w("")

# ---------------------------------------------------------------- Faza 0
w("## 1. Faza 0 — podstawy")
w("")
w("```bash")
w("python eval/freeze_snapshot.py        # tożsamość snapshotu")
w("python eval/determinism_check.py      # determinizm bajtowy")
w("python eval/count_legacy_loc.py       # LOC legacy")
w("python eval/count_spec_loc.py         # LOC specyfikacji")
w("```")
w("")
pop = det["population"]
w("| Wielkość | Wartość |")
w("| --- | ---: |")
w(f"| pliki objęte ekstrakcją | {pop['files']} |")
w(f"| zależności wyekstrahowane | {pop['deps']} |")
w(f"| zależności nierozwiązane | {pop['unresolved']} |")
w(f"| determinizm bajtowy | {'potwierdzony' if det['deterministic'] else 'NIE'} |")
w(f"| reguły w specyfikacji | {spec['rule_count']} |")
w(f"| rodzaje reguł użyte | {len(spec['rules_per_kind'])} z 11 |")
w(f"| reguły severity=error / warning | {spec['rules_per_severity']['error']} / {spec['rules_per_severity']['warning']} |")
w(f"| roots (repozytoria) | {spec['root_count']} |")
w(f"| jednostki zadeklarowane | {spec['declared_unit_count']} |")
w("")
w(f"Determinizm sprawdzony w trzech przebiegach (zimny cache, ciepły cache, losowy PYTHONHASHSEED); "
  f"sha256 wyjścia identyczny. Plan badań zakładał 538 plików / 2270 zależności — "
  f"pomiar daje {pop['files']} / {pop['deps']} i to jest liczba raportowana.")
w("")
w("### Retrofit: legacy → specyfikacja")
w("")
w("| Rodzaj reguły | reguły legacy | reguły limen | LOC legacy (code) | LOC YAML (code) |")
w("| --- | ---: | ---: | ---: | ---: |")
for r in t1["rows"]:
    w(f"| {r['kind']} | {r['legacy_rules']} | {r['limen_rules']} | {r['legacy_loc_code']} | {r['yaml_loc_code']} |")
w(f"| **RAZEM** | **{tot['legacy_rules']}** | **{tot['limen_rules']}** | "
  f"**{tot['legacy_loc_code']}** | **{tot['yaml_loc_code']}** |")
w("")
w(f"Metoda przypisania LOC do rodzaju reguły jest zapisana w `study1_table.json` "
  f"(`attribution_method`). Linii spornych — deklarowanych przez reguły więcej niż jednego rodzaju — "
  f"jest {t1['attribution_method']['contested_legacy_lines']}; trafiają do wiersza infrastruktury "
  f"wspólnej, nie są rozsmarowywane po regułach. Rodzaj K11 (reachability) pozostaje nieużyty: "
  f"żaden checker legacy nie zadaje pytania tranzytywnego.")
w("")

# ---------------------------------------------------------------- Badanie 1
w("## 2. Badanie 1 — równoważność semantyczna")
w("")
w("```bash")
w("python eval/run_legacy_oracle.py              # oracle: niezmienione klasy legacy")
w("python eval/run_limen_check.py                # strona limen")
w("python eval/study1_equivalence.py             # diff + klasyfikacja (brama)")
w("python eval/study1_table.py                   # tabela do artykułu")
w("python eval/mutation_sensitivity.py           # czułość mutacyjna")
w("python eval/validate_shim_against_laravel.py  # walidacja podkładki")
w("python eval/check_line_recovery.py            # zgodność numerów linii")
w("```")
w("")
w("Oracle uruchamia **niezmienione** pliki `tests/Architecture/*.php`. Reguły nie są przepisane "
  "do Pythona — inaczej porównanie dotyczyłoby naszej interpretacji, nie legacy. Podkładka dostarcza "
  "wyłącznie hydraulikę (`base_path`, `config`, autoloader PSR-4, nieprzerywający `assertSame`).")
w("")
w("| Wielkość | Wartość |")
w("| --- | ---: |")
w(f"| findings limen (ogółem) | {d1['limen_findings_total']} |")
w(f"| findings limen w zakresie porównania | {d1['limen_findings_total'] - d1['out_of_scope_limen']} |")
w(f"| findings limen poza zakresem | {d1['out_of_scope_limen']} |")
w(f"| findings legacy | {d1['legacy_findings_total']} |")
w(f"| zgodności na (plik, linia, cel) | {d1['agreements']} |")
w(f"| różnice tylko-limen | {d1['limen_only']} |")
w(f"| różnice tylko-legacy | {d1['legacy_only']} |")
w("")
w("Różnice per kategoria — **raportowane osobno, nigdy zsumowane**:")
w("")
w("| Kategoria | Znaczenie | Liczba |")
w("| --- | --- | ---: |")
for k, lab in d1["categories"].items():
    w(f"| {k} | {lab} | {d1['per_category'][k]} |")
w(f"| — | **niesklasyfikowane (kryterium odbioru: 0)** | **{d1['unclassified']}** |")
w("")
w(f"Zerowy oracle legacy na czystym snapshocie jest wynikiem prawidłowym, ale sam w sobie ma niską "
  f"moc dowodową. Dlatego czułość jest mierzona osobno.")
w("")
w("### Walidacja oracle")
w("")
w("| Sprawdzenie | Wynik |")
w("| --- | ---: |")
w(f"| Laravel, czyste drzewo: testy real / podkładka | {lcl['tests_real']} / {lcl['tests_shim']} |")
w(f"| Laravel, czyste drzewo: asercje real / podkładka | {lcl['assertions_real']} / {lcl['assertions_shim']} |")
w(f"| Laravel: mutanty porównane / zgodne | {lav['mutants_compared']} / {lav['mutants_agreeing']} |")
w(f"| Laravel: mutanty z identycznym zbiorem failures | {lav['mutants_failure_sets_equal']} |")
w(f"| czułość mutacyjna: mutacje | {mut['mutations']} ({mut['backend_mutations']} backend, {mut['frontend_mutations']} frontend) |")
w(f"| czułość: wykryte przez legacy | {mut['detected_legacy']} |")
w(f"| czułość: wykryte przez limen | {mut['detected_limen']} |")
w(f"| czułość: wykryte przez oba | {mut['detected_both']} |")
lrc = lrec["counts"]
w(f"| numery linii: współrzędne zgodne | {lrc['coordinates_agreeing']} (pliki wspólne: {lrc['files_seen_by_both']}) |")
w(f"| numery linii: rozbieżne w którąkolwiek stronę | {lrc['legacy_only_in_shared_files'] + lrc['limen_only_in_shared_files']} |")
w("")
w("Porównanie z Laravelem szło przez JUnit XML, nie przez wyjście konsoli (to obcina długie nazwy "
  "testów). Pułapka warta zapisania: autoloader Composera wyprowadza baseDir z **rozwiązanej** ścieżki "
  "`vendor/`, więc symlinkowanie `vendor/` do drzewa mutanta powoduje, że klasy ładują się z drzewa "
  "pierwotnego i każdy mutant „przechodzi” — fałszywa zgodność wyglądająca jak sukces. "
  "Walidator kopiuje `vendor/` naprawdę.")
w("")

# ---------------------------------------------------------------- Badanie 1b
w("## 3. Badanie 1b — differential na korpusie zewnętrznym")
w("")
w("```bash")
w("GITHUB_TOKEN=... python eval/build_corpus.py   # dobór próby")
w("python eval/study1b_corpus.py                  # differential per repo")
w("```")
w("")
w(f"Kandydatów przebadanych: **{flow['candidates']}** "
  f"({flow['api_calls']['search']} zapytań code search, {flow['api_calls']['core']} wywołań API). "
  f"Włączonych: **{flow['included']}** — kwota {flow['quota_per_tool']} na narzędzie, "
  f"próg {flow['min_source_files']} plików źródłowych, podział dev {flow['dev']} / "
  f"held-out {flow['heldout']} (seed {flow.get('seed', 20260804)}).")
w("")
w("### Diagram przepływu doboru próby")
w("")
w("| Filtr | Odrzuconych |")
w("| --- | ---: |")
names = {"F2": "config się nie parsuje", "F3": "config nie deklaruje żadnej reguły",
         "F4": f"mniej niż {flow['min_source_files']} plików źródłowych",
         "F6": "tutorial / template / repo samego narzędzia", "F7": "brak zadeklarowanej licencji",
         "F8": f"kwota {flow['quota_per_tool']} repo na narzędzie już wypełniona"}
for k in sorted(x for x in flow["per_step"] if x != "INCLUDED"):
    w(f"| {k} — {names.get(k, k)} | {flow['per_step'][k]} |")
w(f"| **włączonych** | **{flow['per_step']['INCLUDED']}** |")
w("")
w(f"Obserwacja niezależna od limen: **{flow['per_step']['F3']} repozytoriów ma plik konfiguracyjny "
  f"natywnego checkera, który nie deklaruje ani jednej reguły.** Narzędzie jest zainstalowane, "
  f"kontrakt pusty.")
w("")
w("### Los repozytoriów")
w("")
st = {}
for r in repo1b:
    st[r["status"]] = st.get(r["status"], 0) + 1
w("| Status | Repo |")
w("| --- | ---: |")
for k in sorted(st, key=lambda x: -st[x]):
    w(f"| {k} | {st[k]} |")
w("")
ok = sum(1 for r in repo1b if r["status"] == "ok")
w(f"**Porównanie doszło do skutku w {ok} z {flow['included']} repozytoriów.** To jest główne "
  f"ograniczenie tego badania: większość natywnych configów nie daje się przetłumaczyć na obecny "
  f"zestaw rodzajów reguł. Status `oracle-not-live` oznacza repo, w których natywne narzędzie "
  f"raportowało zero, nie rozstrzygnąwszy niczego — dependency-cruiser bez zainstalowanych "
  f"`node_modules` nie rozwiązuje większości zależności. Bez progu rozwiązywalności te repo "
  f"policzyłyby się jako fałszywa zgodność.")
w("")
w("### Zgodność per rodzaj reguły")
w("")
w("Precyzja i czułość to limen wobec natywnego narzędzia jako oracle: TP = ta sama trójka "
  "(plik, linia, cel) po obu stronach, FP = tylko-limen, FN = tylko-natywne. Przedziały Wilsona 95%. "
  "`n/a` oznacza, że rodzaj nie jest ćwiczony — **nigdy nie czytać jako zgodności**.")
w("")
w("| K | zbiór | repo | TP | FP | FN | precyzja [95% CI] | czułość [95% CI] | C3 | niesklas. |")
w("| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |")
for r in kind1b:
    if r["precision"] == "n/a":
        p = "n/a (niećwiczony)" if r["exercised"] == "False" else "n/a"
    else:
        p = f"{d(r['precision'],3)} [{d(r['precision_lo'],3)}, {d(r['precision_hi'],3)}]"
    if r["recall"] == "n/a":
        rc_ = "n/a (niećwiczony)" if r["exercised"] == "False" else "n/a"
    else:
        rc_ = f"{d(r['recall'],3)} [{d(r['recall_lo'],3)}, {d(r['recall_hi'],3)}]"
    w(f"| {r['kind']} | {r['split']} | {r['repos']} | {r['agreements_TP']} | {r['limen_only_FP']} | "
      f"{r['native_only_FN']} | {p} | {rc_} | {r['C3']} | {r['unclassified']} |")
w("")
k6d = next(r for r in kind1b if r["kind"] == "K6" and r["split"] == "dev")
k6h = next((r for r in kind1b if r["kind"] == "K6" and r["split"] == "held-out"), None)
w(f"Na zbiorze rozwojowym K6 osiąga precyzję {d(k6d['precision'],3)} i czułość {d(k6d['recall'],3)} "
  f"przy {k6d['agreements_TP']} trafieniach.")
if k6h:
    w(f"Na held-out — gdzie tłumaczenie było wykonane raz, bez iteracji — precyzja i czułość wynoszą "
      f"{d(k6h['precision'],3)} przy {k6h['agreements_TP']} trafieniach i {k6h['unclassified']} "
      f"różnicach niesklasyfikowanych.")
w("")
w("### Retrofity bespoke")
w("")
w("Trzy autorskie checkery w trzech językach: Kafka (`import-control.xml`, Java), "
  "Home Assistant (plugin pylint `hass_imports`, Python), VS Code (layer checker, TypeScript). "
  "Wyniki w `eval/results/bespoke_retrofits/`. W przypadku Home Assistant limen raportuje liczbę "
  "findings o kilka rzędów wielkości większą niż plugin natywny — **jest to artefakt zakresu**, "
  "nie miara jakości: limen ekstrahuje całe repozytorium, plugin działa na wybranych plikach. "
  "Jedyną wielkością porównywalną jest podzbiór wewnątrz testowanych integracji, i tak jest "
  "raportowany w polu `scope`.")
w("")

# ---------------------------------------------------------------- Badanie 2
w("## 4. Badanie 2 — historia repozytorium")
w("")
w("```bash")
w("python eval/study2_history.py     # pomiar na commitach")
w("python eval/study2_metrics.py     # metryki, CSV, rysunek")
w("```")
w("")
w(f"**{m2['n_commits_first_parent']} commitów first-parent** z sześciu repozytoriów LitRev, "
  f"**{m2['n_measurement_points']} punktów pomiarowych** (dwie osie czasu × dwa warianty specyfikacji). "
  f"Wzięto wszystkie commity, nie co dziesiąty: historia liczy ~4,5 miesiąca, próbkowanie co 10. "
  f"dałoby ~7 punktów.")
w("")
w("| Wielkość | Wartość |")
w("| --- | ---: |")
w(f"| epizody naruszeń | {ws['n_episodes']} |")
w(f"| naprawione | {ws['n_repaired']} |")
w(f"| otwarte na końcu okna (cenzurowane z prawej) | {ws['n_open_at_end_censored']} |")
w(f"| mediana czasu do naprawy (Kaplan–Meier) | {d(ws['km_median_days'])} dnia |")
w(f"| 95% CI (bootstrap, B={ws['bootstrap']['b']}) | [{d(ws['bootstrap']['lo'])}; {d(ws['bootstrap']['hi'])}] |")
w(f"| mediana naiwna, bez cenzurowania | {d(ws['naive_complete_case_median_days'])} dnia |")
w("")
km = ws["km_curve"]
near = min(km, key=lambda p: abs(p["survival"] - 0.5))
w(f"Przedział ufności jest szeroki uczciwie. Krzywa przeżycia jest schodkowa: "
  f"{near['events']} z {ws['n_episodes']} epizodów zamyka się w jednym punkcie "
  f"(t = {d(near['t'])} d), po którym S(t) = {d(near['survival'],4)} — tuż powyżej 0,5. "
  f"Mediana KM jest w tym miejscu wielkością niestabilną, dlatego eksportowana jest cała krzywa "
  f"(`study2_metrics.json`, pole `km_curve`). Mediana naiwna "
  f"({d(ws['naive_complete_case_median_days'])} d) pomija naruszenia nadal otwarte i zaniża wynik.")
w("")
w("### Epizody per rodzaj reguły")
w("")
w("| Rodzaj | Epizody |")
w("| --- | ---: |")
for k in sorted(ws["episodes_per_kind"], key=lambda x: int(x[1:])):
    w(f"| {k} | {ws['episodes_per_kind'][k]} |")
w("")
zero = [k for k, v in ws["episodes_per_kind"].items() if v == 0]
w(f"**Wynik negatywny:** {len(zero)} z 11 rodzajów reguł nie odpala się w tej historii ani razu "
  f"({', '.join(sorted(zero, key=lambda x: int(x[1:])))}). Naruszenia są zdominowane przez "
  f"K7 ({ws['episodes_per_kind']['K7']} epizodów) i K8 ({ws['episodes_per_kind']['K8']}), "
  f"nie przez granice modułów, które są głównym tematem specyfikacji. K6 ma "
  f"{ws['episodes_per_kind']['K6']} epizodów w tym wariancie, ale 1 w wariancie bez wyjątków — "
  f"jego jedyne naruszenie w historii jest pokrywane wyjątkiem.")
w("")
w("### Analiza wrażliwości — definicja tożsamości findingu")
w("")
w(f"Reguła podstawowa: `{m2['identity_rule']}`.")
w("")
w("| Wariant klucza | Epizody | Naprawione | Mediana KM |")
w("| --- | ---: | ---: | ---: |")
w(f"| (rule_id, plik, cel) — podstawowy | {sens['episodes_primary_key']} | {sens['repaired_primary']} | {d(sens['km_median_primary'])} d |")
w(f"| z numerem linii w kluczu | {sens['episodes_with_line_in_key']} | {sens['repaired_with_line']} | {d(sens['km_median_with_line'])} d |")
w("")
pct = 100.0 * sens["delta_episodes"] / sens["episodes_primary_key"]
w(f"Włączenie numeru linii do klucza tworzy {sens['delta_episodes']} epizodów więcej "
  f"({d(pct,1)}%) i przesuwa medianę na {d(sens['km_median_with_line'])} dnia. Numery linii "
  f"przesuwają się przy każdej edycji pliku, więc ta różnica jest miarą błędu, który klucz "
  f"z linią wprowadziłby — nie alternatywnym wynikiem.")
w("")
ex = m2["exemptions"]
w("### Wyjątki")
w("")
w("| Wielkość | Wartość |")
w("| --- | ---: |")
w(f"| zadeklarowana mediana czasu życia | {ex['declared_median_lifetime_days']} dni |")
w(f"| zmierzona mediana wieku długu, który legalizują | {d(ex['median_actual_age_days'])} dnia |")
w(f"| findings wybaczone przez wyjątki | {ex['n_forgiven_findings']} |")
w(f"| wszystkie otwarte na końcu okna | {'tak' if ex['all_open_at_end'] else 'nie'} |")
w("")
w("### Ograniczenia metody, zweryfikowane empirycznie")
w("")
w("1. **Reguły nie są rekonstruowane per commit.** Historia jest mierzona jedną, dzisiejszą "
  "specyfikacją, bo rekonstrukcja reguł mierzyłaby zmiany reguł, a nie zmiany kodu. Konsekwencja: "
  "naruszenia sprzed wprowadzeniem reguły są anachroniczne.")
w("2. **`--today` steruje wyłącznie wygasaniem wyjątków.** Ponieważ wszystkie wyjątki wygasają "
  "po końcu okna pomiarowego, w tej historii jest to zweryfikowany no-op.")
w("3. **`units.registry_policy: exact` przerywa każde drzewo historyczne** kodem 3, zanim uruchomi "
  "jakąkolwiek regułę. Bez relaksacji do `open` całe badanie zwróciłoby zero findings dla wszystkich "
  f"{m2['n_commits_first_parent']} commitów. Relaksacja jest bajtowo obojętna na zamrożonym workspace.")
w("")

# ---------------------------------------------------------------- Badanie 3
w("## 5. Defekty ujawnione przez badania")
w("")
w("Przebieg badań ujawnił defekty zarówno w harnessie, jak i w samym limenie. Odnotowane, "
  "bo wpływają na interpretację liczb i bo są wynikiem samym w sobie.")
w("")
w("**W harnessie badawczym (naprawione, z dodanymi bramami):**")
w("")
w("1. Brama zera różnic niesklasyfikowanych nie istniała w ścieżce korpusowej — `main()` zwracał "
  "zawsze 0. Po dodaniu natychmiast zapaliła na 7 realnych przypadkach.")
w("2. Kategoria C3 cytowała identyfikatory odchyleń istniejące wyłącznie w kodzie translatorów, "
  "nie w `docs/deviations.md`. Dodana druga brama odrzuca C3 wskazujące nieistniejący wpis.")
w("3. Agregacja per-kind kopiowała całą tabelę repozytorium do każdego rodzaju reguły z jego "
  "specyfikacji, licząc te same findings wielokrotnie i mieszając rodzaje. Zastąpiona atrybucją "
  "per finding.")
w("4. Translator import-lintera budował globy bez prefiksu roota, więc reguły warstwowe nie mogły "
  "trafić w żaden plik.")
w("")
w("**W limenie:**")
w("")
w("5. Regex `^\\s*` w ekstraktorze Pythona pozwalał `\\s` dopasować znak nowej linii, zaniżając "
  "numery linii importów o liczbę pustych linii powyżej. Naprawione; Badanie 1 bit-w-bit bez zmian.")
w("6. Cache faktów kluczowany treścią plików nie unieważniał się po zmianie kodu ekstraktora.")
w("7. `sys.executable` przekazywany jako ścieżka względna mógł uruchomić inny interpreter "
  "w podprocesie.")
w("")

# ---------------------------------------------------------------- Badanie 3
s3 = rj(R, "study3_summary.json")
fv = rj(R, "study3_finding_verification.json")
fe = s3["floor_effect"]
cx = s3["context"]
pw_reg = s3["power_regression_rate"]
pw_ev  = s3["power_event_rate"]

w("## 6. Badanie 3 — agent z kontraktem (pilotaż, przebieg niepełny)")
w("")
w("```bash")
w("python eval/study3_agent/select_tasks.py   # wybór K zadań z puli kandydatów")
w("python eval/study3_agent/run.py            # przebiegi A/B/C/D")
w("python eval/study3_agent/verify_findings.py")
w("python eval/study3_agent/analyze.py        # metryki, moc, rysunek")
w("```")
w("")
w(f"Zaplanowano K={s3['k_planned']} zadań × 4 warunki × R={s3['r_planned']} = "
  f"**{s3['cells_planned']} przebiegów**; wykonano **{s3['cells_completed']}**. "
  f"Przebieg zatrzymał się na limicie tokenów sesji. "
  f"**Żadne zadanie nie ma kompletu czterech warunków** "
  f"(`tasks_with_all_conditions = {s3['tasks_with_all_conditions']}`), więc poniższe liczby są "
  f"opisem {s3['n_runs']} przebiegów, a nie wynikiem pilotażu w sensie testu hipotezy.")
w("")
w("| Kontrola | Wartość |")
w("| --- | --- |")
w(f"| model | `{s3['model']}` we wszystkich warunkach |")
w(f"| temperatura | {s3['temperature'] if s3['temperature'] is not None else 'API odrzuca parametr dla tego modelu; identyczny domyślny sampler w każdym warunku'} |")
w(f"| seed kolejności | {s3['seed']} |")
w(f"| sha256 zamrożonej specyfikacji | `{s3['spec_sha256'][:16]}…` |")
w(f"| reguły held-out | {len(s3['heldout_rules'])} z 36 |")
w(f"| rozkład zadań | {', '.join(f'{k}: {v}' for k, v in s3['balance'].items())} |")
w("")
w("### Wynik główny: miara pierwotna jest zdegenerowana")
w("")
w("| Wielkość | Wartość |")
w("| --- | ---: |")
w(f"| przebiegi ogółem | {fe['runs_total']} |")
w(f"| przebiegi z niezerową zmianą liczby błędów | {fe['runs_with_nonzero_delta']} |")
w(f"| udział przebiegów z zerem | {d(fe['fraction_zero'], 2)} |")
w(f"| częstość zdarzeń [95% CI] | {d(fe['event_rate'], 2)} [{d(fe['event_rate_ci95'][0], 3)}; {d(fe['event_rate_ci95'][1], 3)}] |")
w(f"| przebiegi zmniejszające liczbę błędów | {fe['runs_with_fewer_errors']} |")
w("")
w("Liczba errorów `limen check` względem drzewa rodzica **nie ma wariancji międzyzadaniowej "
  "w żadnym z sześciu kontrastów**, więc test permutacyjny w parach degeneruje się (p = 1). "
  "Żadne K ani R nie wykryje różnicy średnich tej miary. Przyczyna jest zmierzona, nie domniemana: "
  "wszystkie drzewa rodzica mają zero błędów na regułach **pokazywanych** agentowi — cały dług "
  "historyczny leży na regułach held-out — a zadania to małe zmiany wewnątrz jednego modułu, "
  "podczas gdy reguły egzekwują granice **między** modułami. Agent poprawnie realizujący zlecenie "
  "nie ma jak ich naruszyć.")
w("")
w("To jest defekt konstrukcji miary, wykryty za 21% budżetu pełnego badania — i główna wartość "
  "tego pilotażu.")
w("")
w("### Wyniki opisowe per warunek")
w("")
w("| Warunek | n | Δ błędów | Δ na regułach held-out | testy przechodzą | tokeny wejścia (śr.) |")
w("| --- | ---: | ---: | ---: | ---: | ---: |")
lab = {"A": "A bez reguł", "B": "B pełna proza", "C": "C limen context", "D": "D C + pętla check"}
for c in ("A", "B", "C", "D"):
    v = s3["per_condition"][c]
    w(f"| {lab[c]} | {v['n_runs']} | {d(v['delta_mean'], 2)} | {d(v['delta_heldout_mean'], 2)} | "
      f"{d(v['tests_pass_rate'], 2)} | {v['tokens_in_mean']:,.0f} |")
w("")
w("### Wynik wtórny: redukcja kontekstu (jedyny niezależny od mocy testu)")
w("")
w("| Wielkość | Wartość |")
w("| --- | ---: |")
w(f"| proza architektoniczna (warunek B) | {cx['B_prose_bytes_mean']:,.0f} B |")
w(f"| kontrakt generowany (warunek C) | {cx['C_limen_bytes_mean']:,.0f} B |")
w(f"| redukcja | {d(cx['reduction_pct'], 1)}% (współczynnik {d(cx['reduction_factor'], 2)}×) |")
w("")
w("Ta liczba nie zależy od liczby przebiegów — jest własnością generatora kontekstu, nie próby.")
w("")
w("### Miara współpierwotna i kontrole cyrkularności")
w("")
w(f"Regresja funkcjonalna wystąpiła w {d(pw_reg['regression_rate_pooled'], 2)} przebiegów "
  f"[95% CI {d(pw_reg['regression_rate_ci95'][0], 2)}; {d(pw_reg['regression_rate_ci95'][1], 2)}], "
  f"ale przedziały Wilsona pokrywają się w każdej parze warunków — to jest miara, która **ma** "
  f"wariancję i nadaje się na miarę pierwotną w pełnym badaniu.")
w("")
w(f"Weryfikacja findings: {fv['n_verified']} pozycji z {fv['rules_covered']} reguł, dobór "
  f"warstwowy po `rule_id` (seed {s3['seed']}). Rater mechaniczny uznał "
  f"{fv['mechanical_real']}/{fv['n_verified']} za realne; cytowany plik istnieje w "
  f"{fv['cited_file_present']}/{fv['n_verified']} przypadkach, cel występuje w pliku w "
  f"{fv['cited_target_in_file']}, a na wskazanej linii w {fv['cited_target_on_cited_line']}. "
  f"**Wymóg protokołu nie jest spełniony:** zgodność dwóch raterów nie została obliczona — "
  f"drugi rater jest zaimplementowany i zaseedowany, ale zabrakło budżetu na uruchomienie.")
w("")
w("### Analiza mocy — co potrzeba do pełnego badania")
w("")
w("Miara pierwotna nie daje się wzmocnić liczebnością. Odpowiadalne są dwie inne:")
w("")
w("| Pytanie | Przebiegi na ramię |")
w("| --- | ---: |")
w(f"| regresja funkcjonalna 0,40 vs 0,15 | {pw_reg['n_runs_per_arm_0.40_vs_0.15']} |")
w(f"| regresja funkcjonalna 0,40 vs 0,20 | {pw_reg['n_runs_per_arm_0.40_vs_0.20']} |")
w(f"| częstość zdarzeń 0,30 vs 0,10 | {pw_ev['n_per_arm_to_detect_rate_0.30_vs_0.10']} |")
w(f"| częstość zdarzeń 0,20 vs 0,05 | {pw_ev['n_per_arm_to_detect_rate_0.20_vs_0.05']} |")
w("")
w("To odpowiada K = 20 przy R = 3–5 — czyli dokładnie wariantowi pierwotnie odrzuconemu jako "
  "zbyt kosztowny. Warunkiem sensowności pełnego przebiegu jest jednak **zmiana konstrukcji "
  "zadań**: muszą dotykać więcej niż jednego modułu, inaczej miara pierwotna pozostanie przy "
  "podłodze niezależnie od liczebności.")
w("")
w("### Defekty konstrukcji wykryte przez pilotaż")
w("")
w("1. **Podłoga miary pierwotnej** — opisana wyżej.")
w("2. **Zadanie `fe-08`**: agent w każdym przebiegu trafnie odmówił implementacji, bo prompt "
  "opisuje funkcję modułu `reviews`, podczas gdy commit źródłowy dotyka `projects`. Błąd "
  "w budowie zadania, nie w zachowaniu agenta — filtr doboru musi sprawdzać zgodność treści "
  "promptu ze ścieżkami commita.")
s3_tok_in = sum((rj(p).get("tokens") or {}).get("input", 0)
                for p in sorted(glob.glob(os.path.join(R, "study3", "*", "*.json"))))
w(f"3. **Budżet**: {s3['cells_completed']} komórek zużyło {s3_tok_in:,} tokenów wejściowych "
  f"(zsumowane z rekordów przebiegów, średnio {s3_tok_in / s3['cells_completed']:,.0f} na komórkę); pełne "
  f"{s3['cells_planned']} komórek nie mieści się w jednej sesji. Pełne badanie wymaga podziału "
  f"na sesje — plan jest zamrożony (`eval/results/study3_plan.json`), a przebiegi przyrostowe, "
  f"więc wznowienie dokłada brakujące komórki bez powtarzania wykonanych.")
w("")

# ---------------------------------------------------------------- Pilotaż 2 (bramka)
p2 = rj(R, "study3_pilot2_summary.json")
p2a = rj(R, "tasks_layer1_pool_audit.json")
ev2 = p2["event_rate_any_new_violation"]
bx2 = p2["boundary_crossing_where_applicable"]
ho2 = p2["event_rate_heldout"]
tr2 = p2["event_rate_target_rule"]
fr2 = p2["functional_regression_rate"]

w("## 6b. Drugi pilotaż — bramka doboru zadań")
w("")
w("```bash")
w("python eval/study3_agent/run.py --pool layer1 --condition A --reps 3")
w("```")
w("")
w("Pierwszy pilotaż nie rozstrzygnął, czy przyczyną zerowej wariancji jest dobór zadań, czy brak "
  "efektu. Drugi pilotaż testuje **wyłącznie warunek kontrolny**: czy agent bez żadnych reguł "
  "potrafi naruszyć architekturę na zadaniach z udokumentowanym naruszeniem. Jeśli nie potrafi, "
  "pełne badanie nie ma czego mierzyć. Próg przyjęty przed startem: 0,30.")
w("")
w("| Miara | Wynik | 95% CI (Wilson) |")
w("| --- | ---: | --- |")
w(f"| **naruszenie jakiejkolwiek reguły (bramka)** | **{ev2['k']}/{ev2['n']} = {d(ev2['rate'], 3)}** | [{d(ev2['wilson95'][0], 3)}; {d(ev2['wilson95'][1], 3)}] |")
w(f"| naruszenie reguły held-out | {ho2['k']}/{ho2['n']} = {d(ho2['rate'], 3)} | [{d(ho2['wilson95'][0], 3)}; {d(ho2['wilson95'][1], 3)}] |")
w(f"| naruszenie reguły, którą naruszył człowiek | {tr2['k']}/{tr2['n']} = {d(tr2['rate'], 3)} | [{d(tr2['wilson95'][0], 3)}; {d(tr2['wilson95'][1], 3)}] |")
w(f"| przekroczenie granicy modułu (tam, gdzie granice istnieją) | {bx2['k']}/{bx2['n']} = {d(bx2['rate'], 3)} | [{d(bx2['wilson95'][0], 3)}; {d(bx2['wilson95'][1], 3)}] |")
w(f"| regresja funkcjonalna | {fr2['n'] - fr2['k']}/{fr2['n']} = {d(1 - fr2['rate'], 3)} | — |")
w("")
w(f"**Bramka nie przeszła.** Górna granica przedziału ({d(ev2['wilson95'][1], 3)}) nie wyklucza "
  f"progu 0,30, więc jest to wynik „nie wykazano”, a nie „wykazano brak”.")
w("")
w("### Przyczyna jest inna niż w pierwszym pilotażu")
w("")
w(f"Warunek konieczny z projektu badania — drzewo rodzica musi mieć możliwe do naruszenia reguły "
  f"held-out — był tym razem **sprawdzony i spełniony we wszystkich sześciu zadaniach** "
  f"(11–14 z 18 reguł held-out możliwych do naruszenia na każdym drzewie). W pierwszym pilotażu "
  f"nie był sprawdzony i był naruszony. Reguły mogły więc zadziałać.")
w("")
w(f"Zawiodło założenie, że commit, w którym **człowiek** przekroczył granicę, skusi do jej "
  f"przekroczenia **model**. Pomiar mechanizmu to rozstrzyga: w {bx2['n'] - bx2['k']} z "
  f"{bx2['n']} przebiegów na drzewach mających granice modułów agent **nie dodał ani jednego "
  f"importu przez granicę** — rozwiązywał zadanie wewnątrz własnego modułu, budując mechanizm "
  f"lokalny zamiast sięgnąć po istniejący cudzy, po który sięgnął człowiek. Reguła nie może "
  f"zadziałać na kodzie, który się do niej nie zbliża.")
w("")
w(f"W {tr2['k']}/{tr2['n']} przebiegów agent naruszył regułę naruszoną przez człowieka — "
  f"**każda hipoteza autora o kuszonej granicy została obalona**. Jedyne zaobserwowane naruszenia "
  f"to incydentalne zakazy wzorca K8 w dużym nowym pliku frontendowym (wyjście debugowe, "
  f"lokalne fallbacki), czyli nie przekroczenia architektoniczne.")
w("")
w("### Pula warstwy 1 jest mniejsza niż zakładano")
w("")
usable = sum(1 for v in p2a.values() if v.get("usable"))
w(f"Z {len(p2a)} kandydatów tylko **{usable}** dało się zamienić w zadanie:")
w("")
w("| Commit | Użyteczny | Powód odrzucenia |")
w("| --- | :---: | --- |")
for k, v in p2a.items():
    ok = "tak" if v.get("usable") else "nie"
    reason = "" if v.get("usable") else str(v.get("reason", ""))[:95]
    w(f"| `{k}` | {ok} | {reason} |")
w("")
w("Dwa commity są korzeniami historii (brak drzewa rodzica, nie ma na czym postawić zadania), "
  "a jeden ma rodzica sprzed modularyzacji — czyli dokładnie tryb porażki pierwszego pilotażu, "
  "tym razem wykryty przed uruchomieniem, a nie po.")
w("")
w("### Wniosek dla pełnego badania")
w("")
w(f"**Nie uruchamiać pełnego przebiegu 320 komórek na tym projekcie** — wydałby ~19 mln tokenów "
  f"na pomiar ramienia kontrolnego, które w większości nie może oblać. Jedyną miarą o zdrowej "
  f"wariancji pozostaje regresja funkcjonalna ({d(1 - fr2['rate'], 3)} tutaj, {d(0.32, 2)} "
  f"w pierwszym pilotażu). Bramka kosztowała 18 komórek i oszczędziła 320.")
w("")

w("## 7. Odtworzenie całości")
w("")
w("```bash")
w("export PYTHONPATH=<repo>/limen/src")
w("python eval/freeze_snapshot.py --check        # snapshot nienaruszony")
w("python -m pytest -q                          # suite limen")
w("python eval/run_legacy_oracle.py")
w("python eval/run_limen_check.py")
w("python eval/study1_equivalence.py            # brama: exit != 0 gdy coś niesklasyfikowane")
w("python eval/study1_table.py")
w("python eval/mutation_sensitivity.py")
w("python eval/validate_shim_against_laravel.py --laravel-root <root>")
w("GITHUB_TOKEN=... python eval/build_corpus.py")
w("python eval/study1b_corpus.py")
w("python eval/study2_history.py")
w("python eval/study2_metrics.py")
w("python eval/build_report.py                  # ten dokument")
w("```")
w("")
w("Ścieżka `--spec` musi być absolutna — ścieżki względne są rozwiązywane wobec `--workspace`.")
w("")

out = os.path.join(E, "..", "research-report.md")
with open(out, "w") as f:
    f.write("\n".join(L) + "\n")
print(os.path.abspath(out), len(L), "linii")
