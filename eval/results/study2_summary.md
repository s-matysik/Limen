# Badanie 2 — historia repozytorium

Wygenerowane przez `eval/study2_history.py` (pomiar) i `eval/study2_metrics.py`
(metryki i rysunek). Żadna liczba w tym dokumencie nie jest wpisana ręcznie —
wszystkie pochodzą z `eval/results/study2_metrics.json`,
`eval/results/study2_timeline.csv` i `eval/results/study2/index.json`.

## Odtworzenie

```bash
export PYTHONPATH=/Users/sebastianmatysik/'!!!!!Limen'/limen/src
python eval/study2_history.py            # 276 punktów pomiarowych
python eval/study2_metrics.py            # metryki, CSV, dane do rysunku
```

## 1. Zakres i gęstość próbkowania

| | |
| --- | ---: |
| repozytoria architektoniczne | 6 |
| commity first-parent | 69 |
| okno czasowe | 2026-03-22 .. 2026-08-03 |
| punkty pomiarowe (2 osie × 2 warianty) | 276 |
| moduły w `units.declared` dzisiejszej spec | 32 |

**Decyzja o gęstości.** Protokół dopuszcza „co 10. commit albo wszystkie z 12
miesięcy". Historia LitRev ma 69 commitów first-parent
w ~4,5 miesiąca; co 10. dałoby ~7 punktów pomiarowych, czyli za mało, by zobaczyć
przebieg — zwłaszcza że zmiany są skupione w kilku dużych commitach
refaktoryzacyjnych. Wzięto **wszystkie** commity. Parametr `--every N` pozostaje
w CLI dla większych repozytoriów, domyślnie 1.

## 2. Wybrana strategia osi czasu

Specyfikacja opisuje workspace pięciu rootów (`roots[].repo`), a historia jest
per repozytorium i commity nie są zsynchronizowane w czasie. Policzono **obie**
strategie:

**(b) `workspace` — oś główna.** Stan w czasie T = dla każdego repozytorium jego
ostatni commit first-parent z datą ≤ T. To jedyna oś, na której reguły
cross-repo (K4 `public-api-only`, K7 `baseline-snapshot`) mają czego pilnować, i
jest bliższa pytaniu badawczemu „jak zmieniała się zgodność systemu w czasie".
**To jest rekonstrukcja, nie stan historyczny**: kombinacja commitów w danym
punkcie nie musiała nigdy istnieć jednocześnie na żadnym dysku.

**(a) `per-repo` — kontrola odporności.** Każde repozytorium na własnej osi,
pozostałe rooty puste. Zależności cross-repo degradują się wtedy do ostrzeżeń
`unresolved-dependency`, więc ta oś nie może oceniać reguł cross-repo — służy
sprawdzeniu, czy wnioski przeżywają bez rekonstrukcji.

**Wnioski się nie zmieniają.** Rozkład epizodów per rodzaj reguły jest na obu
osiach identyczny poza K4, którego oś `per-repo` z definicji nie widzi
(3 epizodów na osi `workspace`,
0 na `per-repo`); kształt przebiegu (szczyt
w kwietniu–maju, zapaść po wydzieleniu biblioteki, płaski ogon do sierpnia) jest
ten sam.

## 3. Ograniczenia metodologiczne

- **L1** one specification (today's) for the whole history; rules are NOT reconstructed per commit, so findings dated before the rule existed are anachronistic — they state that the commit would violate today's contract, not that it violated a contract in force at the time.
- **L2** --today only drives exemption expiry; all debt exemptions expire 2026-12-31, after the end of this window, so within this history it is a verified no-op (see --verify-today).
- **L3** axis 'workspace' RECONSTRUCTS the workspace at time T from each repository's last commit <= T; such a combination need not have existed simultaneously.
- **L4** before a repository's first commit its root is materialised empty; K9 structure rules still fire on it — findings carry repo_present.
- **L5** axis 'per-repo' evaluates one repository with the others empty, so cross-repository dependencies degrade to unresolved-dependency.
- **L6** units.registry_policy is relaxed from 'exact' to 'open' for the history run: 'exact' is an assertion about TODAY's module registry (the declared list is recorded verbatim in index.json as units_declared_today) and aborts every historical tree with exit 3 before any rule runs. The relaxation is verified inert on today's tree (--verify-registry) and the registry delta is recorded per commit instead.

### 3.1 Weryfikacja `--today` (L2)

`--today` wstrzykuje datę wyłącznie do oceny wygasania wyjątków
(`spec/model.py::Exemption.status`); w silniku nie ma innego zegara. Sprawdzone
w kodzie **i empirycznie**: to samo drzewo (`2026-08-03`) uruchomione
z datą commita daje **11** findings, a z datą po
wygaśnięciu (`2027-01-01`) — **16**
(+5, dokładnie tyle, ile jest wpisów długu; nowe rule_id:
`host-into-core-baseline`, `shared-not-into-host-modules`).
Ponieważ wszystkie wyjątki wygasają 2026-12-31, czyli **po** końcu okna, w tej
historii `--today` jest zweryfikowanym no-opem.
Dane: `eval/results/study2/today_verification.json`.

### 3.2 Relaksacja `registry_policy` (L6)

Dzisiejsza spec ma `units.registry_policy: exact` z 32-elementową listą
`declared:` (liczba wyprowadzona z `index.json` → `units_declared_today`, nie
przepisana). To asercja o **dzisiejszym** rejestrze modułów (`validate.py` S6):
na każdym drzewie historycznym zbiór wykrytych jednostek jest inny i limen kończy
się kodem 3 **zanim uruchomi jakąkolwiek regułę** — bez relaksacji całe badanie
zwróciłoby zero findings dla wszystkich 69 commitów.
Historia biegnie więc na specyfikacji identycznej z dzisiejszą poza
`registry_policy: open`. Zmiana jest **zweryfikowanie obojętna dla wyników**: na
zamrożonym workspace obie polityki dają
11/11 findings i **bajtowo identyczne
raporty** (`reports_identical: true`;
`eval/results/study2/registry_verification.json`). Delta rejestru nie jest
wyrzucana — jest zapisywana per commit jako `units_declared_missing` /
`units_undeclared`, co daje datowanie pojawiania się każdego modułu.

### 3.3 Trzy klasy findings

limen emituje trzy klasy; tylko pierwsza to naruszenie kodu:

| klasa | co to jest | traktowanie |
| --- | --- | --- |
| `rule` (K1–K11) | naruszenie zadeklarowanej reguły przez kod | **podstawa wszystkich metryk czasowych** |
| `spec-integrity` (`spec-dead-entry`) | wpis `allow`/`exempt`/`expected`/`owners`, którego żaden plik w drzewie nie deklaruje | liczone osobno — to anachronizm z L1 uczyniony widzialnym: dzisiejsza spec nazywa symbole, które wtedy nie istniały |
| `unresolved` | import nierozwiązany do pliku w workspace | liczone osobno; na osi `per-repo` zawyżone z konstrukcji (L5) |

Na osi `workspace/as-is` `spec-dead-entry` daje 26
epizodów, wszystkie zamknięte przed końcem okna — dokładnie tak, jak należy
oczekiwać: w miarę powstawania kodu dzisiejsze wpisy przestają być martwe.

## 4. Przebieg — kiedy cokolwiek się dzieje

Poniżej wszystkie punkty osi `workspace/as-is`, w których liczba naruszeń się
zmienia. Pozostałe 54 z 69 commitów nie ruszają
żadnego findingu — to też wynik.

| data | commit | repo punktu | plików | naruszeń | wprow. | napr. | temat |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 2026-03-22 | `c32d171` | litrev-backend | 28 | 3 | +2 | −0 | Initial commit |
| 2026-03-22 | `9cf5ac7` | litrev-frontend | 69 | 24 | +15 | −0 | Initial commit |
| 2026-03-22 | `90f8730` | litrev-frontend | 63 | 22 | +0 | −2 | Delete Kopiuj files |
| 2026-03-26 | `c1d3e42` | litrev-frontend | 99 | 47 | +11 | −1 | Zarządzanie użytkownikami |
| 2026-04-20 | `eab22e2` | litrev-backend | 281 | 59 | +12 | −0 | Obsługa folderów oraz zmiana projektu na modularny m |
| 2026-04-20 | `82915ff` | litrev-frontend | 415 | 20 | +3 | −22 | Obsługa folderów oraz zmiana projektu na modularny m |
| 2026-04-26 | `feed536` | litrev-frontend | 454 | 19 | +0 | −1 | Dodanie opcji w menu Projekty |
| 2026-05-07 | `46bbf69` | litrev-backend | 545 | 60 | +41 | −0 | Komentarze |
| 2026-05-10 | `8e005bf` | litrev-backend | 589 | 7 | +0 | −53 | Wydzielenie biblioteki |
| 2026-05-13 | `c20944b` | litrev-frontend | 486 | 2 | +0 | −3 | Wydzielenie biblioteki |
| 2026-05-18 | `b03bd4f` | litrev-core-backend | 517 | 3 | +1 | −0 | Dodanie ekstrakcji pdf-ow w przegladzie literatury. |
| 2026-05-18 | `6deb785` | litrev-backend | 519 | 1 | +0 | −2 | Dodanie ekstrakcji pdf-ow w przegladzie literatury. |
| 2026-05-22 | `0334bc8` | litrev-frontend | 531 | 3 | +2 | −0 | Merge branch 'feat/projects-view' into 'main' |
| 2026-05-22 | `9a1d547` | litrev-backend | 532 | 5 | +2 | −0 | Merge branch 'feat/migrate-to-mariadb' into 'main' |
| 2026-06-05 | `4022b67` | litrev-frontend | 538 | 3 | +0 | −2 | Merge branch 'feat/projects-view' into 'main' |

Szczyt: **60** naruszeń w `46bbf69`
(2026-05-07). Koniec okna: **3**
naruszenia (`1677857`), co zgadza się z zamrożonym snapshotem
Badania 1 (3 błędy, 8 ostrzeżeń `unresolved`).

Dwa commity strukturalne dominują przebieg: „Obsługa folderów oraz zmiana
projektu na modularny monolit" (2026-04-20) — powstaje struktura, której spec
oczekuje, i pojawia się 12 naruszeń K5; oraz „Wydzielenie biblioteki"
(2026-05-10) — 53 naruszenia znikają w jednym commicie, bo kod przenosi się do
`backend-shared`, gdzie dzisiejsze reguły go nie ścigają.

## 5. Reguła tożsamości findingu i analiza wrażliwości

**Klucz podstawowy: `(rule_id, file, target)`**, linia jako atrybut pomocniczy.
Uzasadnienie: numery linii przesuwają się przy każdej edycji powyżej naruszenia,
więc klucz z linią raportowałby „naprawę" i „nowe naruszenie" przy każdej
niezwiązanej zmianie. `file` jest ścieżką względem snapshotu
(`litrev-arch-snapshot/<repo>/...`), stabilną między commitami. Tam, gdzie limen
nie emituje `target` (K9, K10), klucz degeneruje się do `(rule_id, file)` — co
jest dokładnie poprawne dla tych rodzajów: plik jest albo za duży, albo nie.
Findings klasy `spec-integrity` nie mają `file`/`target`; ich tożsamością jest
`spec_path`.

**Wrażliwość** (oś `workspace/as-is`):

| wariant klucza | epizodów | naprawionych | mediana KM |
| --- | ---: | ---: | ---: |
| `(rule_id, file, target)` | 89 | 86 | 14.55 d |
| `(rule_id, file, target, line)` | 126 | 123 | 19.68 d |

Dodanie linii do klucza tworzy **+37**
epizodów (42%
więcej) i przesuwa medianę z 14.55 d
na 19.68 d. To jest miara błędu,
który klucz z linią by tu wprowadził, i uzasadnia wybór klucza podstawowego.
Pełne dane: `eval/results/study2_sensitivity.json`.

## 6. Naruszenia wprowadzone, naprawione, czas do naprawy

Oś `workspace/as-is`, klasa `rule`, tylko repozytoria obecne w danym punkcie:

| | |
| --- | ---: |
| epizodów naruszeń (wprowadzonych) | **89** |
| naprawionych w oknie | **86** |
| otwartych na końcu okna (**cenzurowanych z prawej**) | **3** |
| mediana czasu do naprawy (Kaplan–Meier) | **14.55 dnia** |
| 95% CI (bootstrap percentylowy, B=10000, seed 20260804) | **[2.46, 19.68] dni** |
| mediana naiwna (tylko naprawione, bez cenzurowania) | 3.71 dnia |

**Cenzurowanie jest istotne i nie zostało zignorowane.** Trzy epizody są otwarte
na końcu okna — wszystkie to `host-cross-module-public-surface` (K4), czyli
dokładnie te naruszenia, które Badanie 1 raportuje jako pozostałe 3 błędy. Ich
obserwowany wiek to 72,9–76,3 dnia i **rośnie dalej**; mediana naiwna
(3.71 d) je pomija i przez to zaniża wynik.

**Uczciwe zastrzeżenie do mediany KM.** Krzywa przeżycia jest silnie schodkowa,
bo czasy życia są związane z kilkoma datami commitów: 41 z
89 epizodów zamyka się w jednym commicie. Po t = 2.46 d
przeżycie wynosi 0.5169 — tuż **powyżej** 0,5 — i spada
poniżej dopiero przy t = 14.55 d. Mediana KM jest więc tutaj
wielkością niestabilną i to jest właśnie treść szerokiego przedziału ufności
[2.46, 19.68].
Krzywa jest wyeksportowana w całości (`km_curve` w `study2_metrics.json`), żeby
recenzent to widział, a nie zgadywał:

| t (dni) | S(t) | w ryzyku | zdarzeń |
| ---: | ---: | ---: | ---: |
| 0.02 | 0.9775 | 89 | 2 |
| 2.46 | 0.5169 | 87 | 41 |
| 4.96 | 0.5056 | 46 | 1 |
| 14.55 | 0.4831 | 45 | 2 |
| 19.68 | 0.3483 | 43 | 12 |
| 22.60 | 0.3146 | 31 | 3 |
| 24.39 | 0.1910 | 28 | 11 |
| 29.35 | 0.0674 | 17 | 11 |
| 35.73 | 0.0562 | 6 | 1 |
| 57.70 | 0.0337 | 5 | 2 |

Oś kontrolna `per-repo/as-is` daje 86 epizodów i medianę KM
2.46 d — niższą, bo bez rekonstrukcji nie widać epizodów
cross-repo, które żyją najdłużej. Wariant `workspace/no-exemptions`:
92 epizodów, 8 cenzurowanych,
mediana 14.55 d.

## 7. Rozkład per rodzaj reguły

| rodzaj | epizodów (`workspace`) | epizodów (`per-repo`) | szczyt jednoczesnych |
| --- | ---: | ---: | ---: |
| K1 `layer-dependency` | 0 | 0 | 0 |
| K2 `ownership` | 0 | 0 | 0 |
| K3 `consumer-allowlist` | 0 | 0 | 0 |
| K4 `public-api-only` | 3 | 0 | 3 |
| K5 `scoped-allowlist` | 13 | 13 | 13 |
| K6 `forbidden-target` | 0 | 0 | 0 |
| K7 `baseline-snapshot` | 42 | 42 | 42 |
| K8 `pattern-ban` | 22 | 22 | 39 |
| K9 `structure` | 2 | 2 | 1 |
| K10 `metric-threshold` | 7 | 7 | 5 |
| K11 `reachability` | 0 | 0 | 0 |

Pięć z jedenastu rodzajów nie odpala się **ani razu** w całej historii:
K1 `layer-dependency`, K2 `ownership`, K3 `consumer-allowlist`,
K6 `forbidden-target` (odpala się tylko w wariancie bez wyjątków) i
K11 `reachability` (nieużyty w spec — 0 reguł). **To jest wynik, nie brak
danych**: reguły warstwowe i własnościowe nie zostały złamane w tej historii ani
razu, a rozkład naruszeń jest zdominowany przez K7 (42
epizodów) i K8 (22) — czyli przez baseline snapshotu
i bany tekstowe, nie przez granice modułów.

## 8. Czas życia wyjątków

`limen spec stats` na dzisiejszym drzewie:

```
rules per kind:
  baseline-snapshot: 3
  consumer-allowlist: 1
  forbidden-target: 6
  layer-dependency: 4
  metric-threshold: 4
  ownership: 1
  pattern-ban: 4
  public-api-only: 3
  scoped-allowlist: 5
  structure: 5
exemptions: 4 total, 4 active, 0 expired
  median age: 0 days
  median declared lifetime (expires - since): 149 days
```

Wszystkie cztery wyjątki mają `since: 2026-08-04`, czyli powstały w dniu
retrofitu specyfikacji i **nie mają własnej historii** — `spec stats` na każdym
drzewie historycznym zwróci te same cztery wpisy. Historię ma natomiast **dług,
który te wyjątki legalizują**, i to jest wielkość mierzalna: finding obecny
w wariancie `no-exemptions`, a nieobecny w `as-is` w tym samym punkcie, to
naruszenie, które dzisiejsza spec wybacza.

| reguła | rodzaj | pierwszy raz w kodzie | faktyczny wiek (dni) | zadeklarowane życie (dni) | stan na koniec |
| --- | --- | --- | ---: | ---: | --- |
| `shared-not-into-host-modules` | K6 | 2026-05-10 | 85.0 | 149 | open |
| `host-cross-module-public-surface` | K4 | 2026-05-18 | 76.3 | 149 | open |
| `host-cross-module-public-surface` | K4 | 2026-05-18 | 76.3 | 149 | open |
| `host-into-core-baseline` | K7 | 2026-05-18 | 76.3 | 149 | open |
| `host-into-core-baseline` | K7 | 2026-05-18 | 76.3 | 149 | open |

Mediana **faktycznego** wieku długu: **76.32 dnia**,
przy medianie **zadeklarowanego** życia 149 dni
i medianie wieku wyjątku raportowanej przez `spec stats` równej 0 dni. Innymi
słowy: wyjątki są młode (mediana wieku 0–2 dni), ale dług, który opisują, ma
około 76 dni i wszystkie
5 naruszeń jest wciąż otwartych na końcu okna. Ta
różnica jest głównym argumentem za mierzeniem długu z historii, a nie
z metadanych specyfikacji.

## 9. Rysunek

![naruszenia w czasie](study2_violations_over_time.png)

Górny panel: skumulowana liczba naruszeń w czasie z podziałem na rodzaj reguły
(stan trzyma się od commita do następnego — stąd schodki, nie interpolacja).
Dolne panele: małe wielokrotności per rodzaj, **każdy z własną skalą Y**, bo
szczyty różnią się o rząd wielkości. Dane źródłowe: `study2_timeline.csv`
(kolumny `n_K1`..`n_K11`, `introduced`, `repaired`), `study2_per_kind.csv`.

## 10. Artefakty

| plik | zawartość |
| --- | --- |
| `eval/study2_history.py` | pomiar per commit; nagłówek zawiera pełną listę ograniczeń |
| `eval/study2_metrics.py` | metryki czasowe, KM z cenzurowaniem, bootstrap, analiza wrażliwości |
| `eval/results/study2/**/<sha>.json` | 276 punktów pomiarowych (przyrostowo, `--resume`) |
| `eval/results/study2/index.json` | manifest przebiegu, `limitations`, `spec_stats`, `units_declared_today` |
| `eval/results/study2_timeline.csv` | jeden wiersz na punkt pomiarowy |
| `eval/results/study2_per_kind.csv` | naruszenia per rodzaj reguły per punkt |
| `eval/results/study2_findings.csv` | ziarno surowe: jeden wiersz na (punkt, finding) |
| `eval/results/study2_lifetimes.csv` | jeden wiersz na epizod, z flagą cenzurowania |
| `eval/results/study2_exemption_lifetimes.csv` | faktyczny wiek długu wybaczanego przez wyjątki |
| `eval/results/study2_sensitivity.json` | wrażliwość na regułę tożsamości |
| `eval/results/study2_metrics.json` | wszystkie liczby cytowane wyżej |
| `eval/results/study2_violations_over_time.png` | rysunek, 300 dpi |
