# limen — raport z badań

Wygenerowany przez `eval/build_report.py` 2026-08-07. Każda liczba w tym dokumencie jest czytana z plików wynikowych w `eval/results/`; żadna nie jest wpisana ręcznie. Przy każdej sekcji podana jest komenda, która ją odtwarza.

## Streszczenie

Retrofit **47 reguł legacy** rozproszonych po **1246 liniach kodu** (fizycznie 1497) w trzech językach do **36 deklaracji** w 526 liniach YAML (fizycznie 795). Równoważność semantyczna potwierdzona wykonywalnie: 0 różnic niesklasyfikowanych, czułość mutacyjna 35/35. Na korpusie 60 cudzych repozytoriów porównanie doszło do skutku w 12 przypadkach. Historia 69 commitów zmierzona jedną specyfikacją.

**Badanie 3 (agent z kontraktem) nie zostało wykonane** — patrz §6.

## 1. Faza 0 — podstawy

```bash
python eval/freeze_snapshot.py        # tożsamość snapshotu
python eval/determinism_check.py      # determinizm bajtowy
python eval/count_legacy_loc.py       # LOC legacy
python eval/count_spec_loc.py         # LOC specyfikacji
```

| Wielkość | Wartość |
| --- | ---: |
| pliki objęte ekstrakcją | 539 |
| zależności wyekstrahowane | 2274 |
| zależności nierozwiązane | 8 |
| determinizm bajtowy | potwierdzony |
| reguły w specyfikacji | 36 |
| rodzaje reguł użyte | 10 z 11 |
| reguły severity=error / warning | 31 / 5 |
| roots (repozytoria) | 5 |
| jednostki zadeklarowane | 32 |

Determinizm sprawdzony w trzech przebiegach (zimny cache, ciepły cache, losowy PYTHONHASHSEED); sha256 wyjścia identyczny. Plan badań zakładał 538 plików / 2270 zależności — pomiar daje 539 / 2274 i to jest liczba raportowana.

### Retrofit: legacy → specyfikacja

| Rodzaj reguły | reguły legacy | reguły limen | LOC legacy (code) | LOC YAML (code) |
| --- | ---: | ---: | ---: | ---: |
| K1 layer-dependency | 2 | 4 | 37 | 32 |
| K2 ownership | 2 | 1 | 35 | 20 |
| K3 consumer-allowlist | 2 | 1 | 62 | 20 |
| K4 public-api-only | 7 | 3 | 156 | 35 |
| K5 scoped-allowlist | 7 | 5 | 150 | 61 |
| K6 forbidden-target | 5 | 6 | 131 | 78 |
| K7 baseline-snapshot | 3 | 3 | 22 | 38 |
| K8 pattern-ban | 3 | 4 | 36 | 52 |
| K9 structure | 6 | 5 | 88 | 50 |
| K10 metric-threshold | 3 | 4 | 29 | 49 |
| K11 reachability | 0 | 0 | 0 | 0 |
| shared infrastructure | 7 | 0 | 216 | 91 |
| shared helper machinery (unclaimed) | 0 | 0 | 108 | 0 |
| file scaffolding (unclaimed) | 0 | 0 | 176 | 0 |
| **RAZEM** | **47** | **36** | **1246** | **526** |

Metoda przypisania LOC do rodzaju reguły jest zapisana w `study1_table.json` (`attribution_method`). Linii spornych — deklarowanych przez reguły więcej niż jednego rodzaju — jest 5; trafiają do wiersza infrastruktury wspólnej, nie są rozsmarowywane po regułach. Rodzaj K11 (reachability) pozostaje nieużyty: żaden checker legacy nie zadaje pytania tranzytywnego.

## 2. Badanie 1 — równoważność semantyczna

```bash
python eval/run_legacy_oracle.py              # oracle: niezmienione klasy legacy
python eval/run_limen_check.py                # strona limen
python eval/study1_equivalence.py             # diff + klasyfikacja (brama)
python eval/study1_table.py                   # tabela do artykułu
python eval/mutation_sensitivity.py           # czułość mutacyjna
python eval/validate_shim_against_laravel.py  # walidacja podkładki
python eval/check_line_recovery.py            # zgodność numerów linii
```

Oracle uruchamia **niezmienione** pliki `tests/Architecture/*.php`. Reguły nie są przepisane do Pythona — inaczej porównanie dotyczyłoby naszej interpretacji, nie legacy. Podkładka dostarcza wyłącznie hydraulikę (`base_path`, `config`, autoloader PSR-4, nieprzerywający `assertSame`).

| Wielkość | Wartość |
| --- | ---: |
| findings limen (ogółem) | 11 |
| findings limen w zakresie porównania | 3 |
| findings limen poza zakresem | 8 |
| findings legacy | 0 |
| zgodności na (plik, linia, cel) | 0 |
| różnice tylko-limen | 3 |
| różnice tylko-legacy | 0 |

Różnice per kategoria — **raportowane osobno, nigdy zsumowane**:

| Kategoria | Znaczenie | Liczba |
| --- | --- | ---: |
| C1 | legacy false alarm removed | 0 |
| C2 | legacy gap closed | 3 |
| C3 | deliberate semantic change | 0 |
| — | **niesklasyfikowane (kryterium odbioru: 0)** | **0** |

Zerowy oracle legacy na czystym snapshocie jest wynikiem prawidłowym, ale sam w sobie ma niską moc dowodową. Dlatego czułość jest mierzona osobno.

### Walidacja oracle

| Sprawdzenie | Wynik |
| --- | ---: |
| Laravel, czyste drzewo: testy real / podkładka | 26 / 26 |
| Laravel, czyste drzewo: asercje real / podkładka | 52 / 52 |
| Laravel: mutanty porównane / zgodne | 20 / 20 |
| Laravel: mutanty z identycznym zbiorem failures | 20 |
| czułość mutacyjna: mutacje | 35 (20 backend, 15 frontend) |
| czułość: wykryte przez legacy | 35 |
| czułość: wykryte przez limen | 35 |
| czułość: wykryte przez oba | 35 |
| numery linii: współrzędne zgodne | 13 (pliki wspólne: 11) |
| numery linii: rozbieżne w którąkolwiek stronę | 0 |

Porównanie z Laravelem szło przez JUnit XML, nie przez wyjście konsoli (to obcina długie nazwy testów). Pułapka warta zapisania: autoloader Composera wyprowadza baseDir z **rozwiązanej** ścieżki `vendor/`, więc symlinkowanie `vendor/` do drzewa mutanta powoduje, że klasy ładują się z drzewa pierwotnego i każdy mutant „przechodzi” — fałszywa zgodność wyglądająca jak sukces. Walidator kopiuje `vendor/` naprawdę.

## 3. Badanie 1b — differential na korpusie zewnętrznym

```bash
GITHUB_TOKEN=... python eval/build_corpus.py   # dobór próby
python eval/study1b_corpus.py                  # differential per repo
```

Kandydatów przebadanych: **720** (11 zapytań code search, 707 wywołań API). Włączonych: **60** — kwota 15 na narzędzie, próg 30 plików źródłowych, podział dev 40 / held-out 20 (seed 20260804).

### Diagram przepływu doboru próby

| Filtr | Odrzuconych |
| --- | ---: |
| F2 — config się nie parsuje | 1 |
| F3 — config nie deklaruje żadnej reguły | 108 |
| F4 — mniej niż 30 plików źródłowych | 4 |
| F6 — tutorial / template / repo samego narzędzia | 13 |
| F7 — brak zadeklarowanej licencji | 31 |
| F8 — kwota 15 repo na narzędzie już wypełniona | 503 |
| **włączonych** | **60** |

Obserwacja niezależna od limen: **108 repozytoriów ma plik konfiguracyjny natywnego checkera, który nie deklaruje ani jednej reguły.** Narzędzie jest zainstalowane, kontrakt pusty.

### Los repozytoriów

| Status | Repo |
| --- | ---: |
| untranslatable-config | 37 |
| ok | 12 |
| oracle-not-live | 6 |
| vacuous-no-violations-possible | 4 |
| translation-error | 1 |

**Porównanie doszło do skutku w 12 z 60 repozytoriów.** To jest główne ograniczenie tego badania: większość natywnych configów nie daje się przetłumaczyć na obecny zestaw rodzajów reguł. Status `oracle-not-live` oznacza repo, w których natywne narzędzie raportowało zero, nie rozstrzygnąwszy niczego — dependency-cruiser bez zainstalowanych `node_modules` nie rozwiązuje większości zależności. Bez progu rozwiązywalności te repo policzyłyby się jako fałszywa zgodność.

### Zgodność per rodzaj reguły

Precyzja i czułość to limen wobec natywnego narzędzia jako oracle: TP = ta sama trójka (plik, linia, cel) po obu stronach, FP = tylko-limen, FN = tylko-natywne. Przedziały Wilsona 95%. `n/a` oznacza, że rodzaj nie jest ćwiczony — **nigdy nie czytać jako zgodności**.

| K | zbiór | repo | TP | FP | FN | precyzja [95% CI] | czułość [95% CI] | C3 | niesklas. |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |
| K1 | dev | 1 | 0 | 0 | 0 | n/a (niećwiczony) | n/a (niećwiczony) | 0 | 0 |
| K6 | dev | 7 | 5203 | 164 | 369 | 0.969 [0.964, 0.974] | 0.934 [0.927, 0.940] | 532 | 0 |
| K6 | held-out | 5 | 167 | 0 | 0 | 1.000 [0.978, 1.000] | 1.000 [0.978, 1.000] | 0 | 0 |
| K11 | dev | 1 | 0 | 0 | 0 | n/a (niećwiczony) | n/a (niećwiczony) | 0 | 0 |
| oracle-side | dev | 0 | 0 | 0 | 0 | n/a (niećwiczony) | n/a (niećwiczony) | 4 | 0 |

Na zbiorze rozwojowym K6 osiąga precyzję 0.969 i czułość 0.934 przy 5203 trafieniach.
Na held-out — gdzie tłumaczenie było wykonane raz, bez iteracji — precyzja i czułość wynoszą 1.000 przy 167 trafieniach i 0 różnicach niesklasyfikowanych.

### Retrofity bespoke

Trzy autorskie checkery w trzech językach: Kafka (`import-control.xml`, Java), Home Assistant (plugin pylint `hass_imports`, Python), VS Code (layer checker, TypeScript). Wyniki w `eval/results/bespoke_retrofits/`. W przypadku Home Assistant limen raportuje liczbę findings o kilka rzędów wielkości większą niż plugin natywny — **jest to artefakt zakresu**, nie miara jakości: limen ekstrahuje całe repozytorium, plugin działa na wybranych plikach. Jedyną wielkością porównywalną jest podzbiór wewnątrz testowanych integracji, i tak jest raportowany w polu `scope`.

## 4. Badanie 2 — historia repozytorium

```bash
python eval/study2_history.py     # pomiar na commitach
python eval/study2_metrics.py     # metryki, CSV, rysunek
```

**69 commitów first-parent** z sześciu repozytoriów LitRev, **276 punktów pomiarowych** (dwie osie czasu × dwa warianty specyfikacji). Wzięto wszystkie commity, nie co dziesiąty: historia liczy ~4,5 miesiąca, próbkowanie co 10. dałoby ~7 punktów.

| Wielkość | Wartość |
| --- | ---: |
| epizody naruszeń | 89 |
| naprawione | 86 |
| otwarte na końcu okna (cenzurowane z prawej) | 3 |
| mediana czasu do naprawy (Kaplan–Meier) | 14.55 dnia |
| 95% CI (bootstrap, B=10000) | [2.46; 19.68] |
| mediana naiwna, bez cenzurowania | 3.71 dnia |

Przedział ufności jest szeroki uczciwie. Krzywa przeżycia jest schodkowa: 1 z 89 epizodów zamyka się w jednym punkcie (t = 4.96 d), po którym S(t) = 0.5056 — tuż powyżej 0,5. Mediana KM jest w tym miejscu wielkością niestabilną, dlatego eksportowana jest cała krzywa (`study2_metrics.json`, pole `km_curve`). Mediana naiwna (3.71 d) pomija naruszenia nadal otwarte i zaniża wynik.

### Epizody per rodzaj reguły

| Rodzaj | Epizody |
| --- | ---: |
| K1 | 0 |
| K2 | 0 |
| K3 | 0 |
| K4 | 3 |
| K5 | 13 |
| K6 | 0 |
| K7 | 42 |
| K8 | 22 |
| K9 | 2 |
| K10 | 7 |
| K11 | 0 |

**Wynik negatywny:** 5 z 11 rodzajów reguł nie odpala się w tej historii ani razu (K1, K2, K3, K6, K11). Naruszenia są zdominowane przez K7 (42 epizodów) i K8 (22), nie przez granice modułów, które są głównym tematem specyfikacji. K6 ma 0 epizodów w tym wariancie, ale 1 w wariancie bez wyjątków — jego jedyne naruszenie w historii jest pokrywane wyjątkiem.

### Analiza wrażliwości — definicja tożsamości findingu

Reguła podstawowa: `(rule_id, file, target); line is an attribute`.

| Wariant klucza | Epizody | Naprawione | Mediana KM |
| --- | ---: | ---: | ---: |
| (rule_id, plik, cel) — podstawowy | 89 | 86 | 14.55 d |
| z numerem linii w kluczu | 126 | 123 | 19.68 d |

Włączenie numeru linii do klucza tworzy 37 epizodów więcej (41.6%) i przesuwa medianę na 19.68 dnia. Numery linii przesuwają się przy każdej edycji pliku, więc ta różnica jest miarą błędu, który klucz z linią wprowadziłby — nie alternatywnym wynikiem.

### Wyjątki

| Wielkość | Wartość |
| --- | ---: |
| zadeklarowana mediana czasu życia | 149 dni |
| zmierzona mediana wieku długu, który legalizują | 76.32 dnia |
| findings wybaczone przez wyjątki | 5 |
| wszystkie otwarte na końcu okna | tak |

### Ograniczenia metody, zweryfikowane empirycznie

1. **Reguły nie są rekonstruowane per commit.** Historia jest mierzona jedną, dzisiejszą specyfikacją, bo rekonstrukcja reguł mierzyłaby zmiany reguł, a nie zmiany kodu. Konsekwencja: naruszenia sprzed wprowadzeniem reguły są anachroniczne.
2. **`--today` steruje wyłącznie wygasaniem wyjątków.** Ponieważ wszystkie wyjątki wygasają po końcu okna pomiarowego, w tej historii jest to zweryfikowany no-op.
3. **`units.registry_policy: exact` przerywa każde drzewo historyczne** kodem 3, zanim uruchomi jakąkolwiek regułę. Bez relaksacji do `open` całe badanie zwróciłoby zero findings dla wszystkich 69 commitów. Relaksacja jest bajtowo obojętna na zamrożonym workspace.

## 5. Defekty ujawnione przez badania

Przebieg badań ujawnił defekty zarówno w harnessie, jak i w samym limenie. Odnotowane, bo wpływają na interpretację liczb i bo są wynikiem samym w sobie.

**W harnessie badawczym (naprawione, z dodanymi bramami):**

1. Brama zera różnic niesklasyfikowanych nie istniała w ścieżce korpusowej — `main()` zwracał zawsze 0. Po dodaniu natychmiast zapaliła na 7 realnych przypadkach.
2. Kategoria C3 cytowała identyfikatory odchyleń istniejące wyłącznie w kodzie translatorów, nie w `docs/deviations.md`. Dodana druga brama odrzuca C3 wskazujące nieistniejący wpis.
3. Agregacja per-kind kopiowała całą tabelę repozytorium do każdego rodzaju reguły z jego specyfikacji, licząc te same findings wielokrotnie i mieszając rodzaje. Zastąpiona atrybucją per finding.
4. Translator import-lintera budował globy bez prefiksu roota, więc reguły warstwowe nie mogły trafić w żaden plik.

**W limenie:**

5. Regex `^\s*` w ekstraktorze Pythona pozwalał `\s` dopasować znak nowej linii, zaniżając numery linii importów o liczbę pustych linii powyżej. Naprawione; Badanie 1 bit-w-bit bez zmian.
6. Cache faktów kluczowany treścią plików nie unieważniał się po zmianie kodu ekstraktora.
7. `sys.executable` przekazywany jako ścieżka względna mógł uruchomić inny interpreter w podprocesie.

## 6. Badanie 3 — agent z kontraktem (pilotaż, przebieg niepełny)

```bash
python eval/study3_agent/select_tasks.py   # wybór K zadań z puli kandydatów
python eval/study3_agent/run.py            # przebiegi A/B/C/D
python eval/study3_agent/verify_findings.py
python eval/study3_agent/analyze.py        # metryki, moc, rysunek
```

Zaplanowano K=10 zadań × 4 warunki × R=3 = **120 przebiegów**; wykonano **25**. Przebieg zatrzymał się na limicie tokenów sesji. **Żadne zadanie nie ma kompletu czterech warunków** (`tasks_with_all_conditions = 0`), więc poniższe liczby są opisem 25 przebiegów, a nie wynikiem pilotażu w sensie testu hipotezy.

| Kontrola | Wartość |
| --- | --- |
| model | `claude-sonnet-5` we wszystkich warunkach |
| temperatura | API odrzuca parametr dla tego modelu; identyczny domyślny sampler w każdym warunku |
| seed kolejności | 20260807 |
| sha256 zamrożonej specyfikacji | `b20e0862aff96abd…` |
| reguły held-out | 7 z 36 |
| rozkład zadań | litrev-backend: 6, litrev-frontend: 4 |

### Wynik główny: miara pierwotna jest zdegenerowana

| Wielkość | Wartość |
| --- | ---: |
| przebiegi ogółem | 25 |
| przebiegi z niezerową zmianą liczby błędów | 2 |
| udział przebiegów z zerem | 0.92 |
| częstość zdarzeń [95% CI] | 0.08 [0.022; 0.250] |
| przebiegi zmniejszające liczbę błędów | 0 |

Liczba errorów `limen check` względem drzewa rodzica **nie ma wariancji międzyzadaniowej w żadnym z sześciu kontrastów**, więc test permutacyjny w parach degeneruje się (p = 1). Żadne K ani R nie wykryje różnicy średnich tej miary. Przyczyna jest zmierzona, nie domniemana: wszystkie drzewa rodzica mają zero błędów na regułach **pokazywanych** agentowi — cały dług historyczny leży na regułach held-out — a zadania to małe zmiany wewnątrz jednego modułu, podczas gdy reguły egzekwują granice **między** modułami. Agent poprawnie realizujący zlecenie nie ma jak ich naruszyć.

To jest defekt konstrukcji miary, wykryty za 21% budżetu pełnego badania — i główna wartość tego pilotażu.

### Wyniki opisowe per warunek

| Warunek | n | Δ błędów | Δ na regułach held-out | testy przechodzą | tokeny wejścia (śr.) |
| --- | ---: | ---: | ---: | ---: | ---: |
| A bez reguł | 6 | 0.17 | 0.00 | 0.83 | 55,717 |
| B pełna proza | 5 | 0.00 | 0.00 | 0.80 | 75,935 |
| C limen context | 5 | 0.00 | 0.00 | 0.60 | 40,521 |
| D C + pętla check | 9 | 0.11 | 0.00 | 0.56 | 65,655 |

### Wynik wtórny: redukcja kontekstu (jedyny niezależny od mocy testu)

| Wielkość | Wartość |
| --- | ---: |
| proza architektoniczna (warunek B) | 48,451 B |
| kontrakt generowany (warunek C) | 9,023 B |
| redukcja | 81.4% (współczynnik 5.37×) |

Ta liczba nie zależy od liczby przebiegów — jest własnością generatora kontekstu, nie próby.

### Miara współpierwotna i kontrole cyrkularności

Regresja funkcjonalna wystąpiła w 0.32 przebiegów [95% CI 0.17; 0.52], ale przedziały Wilsona pokrywają się w każdej parze warunków — to jest miara, która **ma** wariancję i nadaje się na miarę pierwotną w pełnym badaniu.

Weryfikacja findings: 50 pozycji z 13 reguł, dobór warstwowy po `rule_id` (seed 20260807). Rater mechaniczny uznał 50/50 za realne; cytowany plik istnieje w 50/50 przypadkach, cel występuje w pliku w 49, a na wskazanej linii w 46. **Wymóg protokołu nie jest spełniony:** zgodność dwóch raterów nie została obliczona — drugi rater jest zaimplementowany i zaseedowany, ale zabrakło budżetu na uruchomienie.

### Analiza mocy — co potrzeba do pełnego badania

Miara pierwotna nie daje się wzmocnić liczebnością. Odpowiadalne są dwie inne:

| Pytanie | Przebiegi na ramię |
| --- | ---: |
| regresja funkcjonalna 0,40 vs 0,15 | 49 |
| regresja funkcjonalna 0,40 vs 0,20 | 82 |
| częstość zdarzeń 0,30 vs 0,10 | 62 |
| częstość zdarzeń 0,20 vs 0,05 | 76 |

To odpowiada K = 20 przy R = 3–5 — czyli dokładnie wariantowi pierwotnie odrzuconemu jako zbyt kosztowny. Warunkiem sensowności pełnego przebiegu jest jednak **zmiana konstrukcji zadań**: muszą dotykać więcej niż jednego modułu, inaczej miara pierwotna pozostanie przy podłodze niezależnie od liczebności.

### Defekty konstrukcji wykryte przez pilotaż

1. **Podłoga miary pierwotnej** — opisana wyżej.
2. **Zadanie `fe-08`**: agent w każdym przebiegu trafnie odmówił implementacji, bo prompt opisuje funkcję modułu `reviews`, podczas gdy commit źródłowy dotyka `projects`. Błąd w budowie zadania, nie w zachowaniu agenta — filtr doboru musi sprawdzać zgodność treści promptu ze ścieżkami commita.
3. **Budżet**: 25 komórek zużyło 1,507,477 tokenów wejściowych (zsumowane z rekordów przebiegów, średnio 60,299 na komórkę); pełne 120 komórek nie mieści się w jednej sesji. Pełne badanie wymaga podziału na sesje — plan jest zamrożony (`eval/results/study3_plan.json`), a przebiegi przyrostowe, więc wznowienie dokłada brakujące komórki bez powtarzania wykonanych.

## 6b. Drugi pilotaż — bramka doboru zadań

```bash
python eval/study3_agent/run.py --pool layer1 --condition A --reps 3
```

Pierwszy pilotaż nie rozstrzygnął, czy przyczyną zerowej wariancji jest dobór zadań, czy brak efektu. Drugi pilotaż testuje **wyłącznie warunek kontrolny**: czy agent bez żadnych reguł potrafi naruszyć architekturę na zadaniach z udokumentowanym naruszeniem. Jeśli nie potrafi, pełne badanie nie ma czego mierzyć. Próg przyjęty przed startem: 0,30.

| Miara | Wynik | 95% CI (Wilson) |
| --- | ---: | --- |
| **naruszenie jakiejkolwiek reguły (bramka)** | **3/18 = 0.167** | [0.058; 0.392] |
| naruszenie reguły held-out | 0/18 = 0.000 | [0.000; 0.176] |
| naruszenie reguły, którą naruszył człowiek | 0/18 = 0.000 | [0.000; 0.176] |
| przekroczenie granicy modułu (tam, gdzie granice istnieją) | 1/12 = 0.083 | [0.015; 0.354] |
| regresja funkcjonalna | 7/18 = 0.389 | — |

**Bramka nie przeszła.** Górna granica przedziału (0.392) nie wyklucza progu 0,30, więc jest to wynik „nie wykazano”, a nie „wykazano brak”.

### Przyczyna jest inna niż w pierwszym pilotażu

Warunek konieczny z projektu badania — drzewo rodzica musi mieć możliwe do naruszenia reguły held-out — był tym razem **sprawdzony i spełniony we wszystkich sześciu zadaniach** (11–14 z 18 reguł held-out możliwych do naruszenia na każdym drzewie). W pierwszym pilotażu nie był sprawdzony i był naruszony. Reguły mogły więc zadziałać.

Zawiodło założenie, że commit, w którym **człowiek** przekroczył granicę, skusi do jej przekroczenia **model**. Pomiar mechanizmu to rozstrzyga: w 11 z 12 przebiegów na drzewach mających granice modułów agent **nie dodał ani jednego importu przez granicę** — rozwiązywał zadanie wewnątrz własnego modułu, budując mechanizm lokalny zamiast sięgnąć po istniejący cudzy, po który sięgnął człowiek. Reguła nie może zadziałać na kodzie, który się do niej nie zbliża.

W 0/18 przebiegów agent naruszył regułę naruszoną przez człowieka — **każda hipoteza autora o kuszonej granicy została obalona**. Jedyne zaobserwowane naruszenia to incydentalne zakazy wzorca K8 w dużym nowym pliku frontendowym (wyjście debugowe, lokalne fallbacki), czyli nie przekroczenia architektoniczne.

### Pula warstwy 1 jest mniejsza niż zakładano

Z 7 kandydatów tylko **4** dało się zamienić w zadanie:

| Commit | Użyteczny | Powód odrzucenia |
| --- | :---: | --- |
| `46bbf692` | tak |  |
| `9cf5ac79` | nie | root commit — no parent tree exists, so there is no state on which to pose the task |
| `eab22e21` | nie | parent tree predates modularisation (no app/Modules); every violated rule is scoped to app/Modu |
| `c1d3e424` | tak |  |
| `82915ff2` | tak |  |
| `c32d1719` | nie | root commit — no parent tree exists, so there is no state on which to pose the task |
| `0334bc8f` | tak |  |

Dwa commity są korzeniami historii (brak drzewa rodzica, nie ma na czym postawić zadania), a jeden ma rodzica sprzed modularyzacji — czyli dokładnie tryb porażki pierwszego pilotażu, tym razem wykryty przed uruchomieniem, a nie po.

### Wniosek dla pełnego badania

**Nie uruchamiać pełnego przebiegu 320 komórek na tym projekcie** — wydałby ~19 mln tokenów na pomiar ramienia kontrolnego, które w większości nie może oblać. Jedyną miarą o zdrowej wariancji pozostaje regresja funkcjonalna (0.389 tutaj, 0.32 w pierwszym pilotażu). Bramka kosztowała 18 komórek i oszczędziła 320.

## 7. Odtworzenie całości

```bash
export PYTHONPATH=<repo>/limen/src
python eval/freeze_snapshot.py --check        # snapshot nienaruszony
python -m pytest -q                          # suite limen
python eval/run_legacy_oracle.py
python eval/run_limen_check.py
python eval/study1_equivalence.py            # brama: exit != 0 gdy coś niesklasyfikowane
python eval/study1_table.py
python eval/mutation_sensitivity.py
python eval/validate_shim_against_laravel.py --laravel-root <root>
GITHUB_TOKEN=... python eval/build_corpus.py
python eval/study1b_corpus.py
python eval/study2_history.py
python eval/study2_metrics.py
python eval/build_report.py                  # ten dokument
```

Ścieżka `--spec` musi być absolutna — ścieżki względne są rozwiązywane wobec `--workspace`.

