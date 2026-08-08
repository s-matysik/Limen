# Badanie 3 — agent z kontraktem (PILOTAŻ, PRZEBIEG NIEPEŁNY)

Wygenerowane przez `eval/study3_agent/analyze.py`. Wszystkie liczby pochodzą z
`eval/results/study3_results.csv` i `eval/results/study3/**`.

## Status: 25 z 120 komórek

Zaplanowano K=10 zadań x 4 warunki x R=3 = **120 przebiegów**. Wykonano **25**.
Przebieg zatrzymał się na twardym limicie tokenów LLM sesji (2,0 mln): 25 komórek
zużyło **1,507,477 tokenów wejściowych** (średnio
60299 na komórkę). Pełny pilotaż wymaga
**7,235,890** tokenów wejściowych — czyli
3.6x limitu jednej sesji. **Design nie mieści się
w jednej sesji i nie jest to kwestia optymalizacji.** Plan jest zamrożony
(`eval/results/study3_plan.json`), przebiegi są przyrostowe, więc wznowienie
dokłada brakujące komórki bez powtarzania wykonanych.

**Żadne zadanie nie ma wszystkich czterech warunków** (`tasks_with_all_conditions
= 0`), a maksymalne pokrycie pary zadanie-warunek to R=2. Test permutacyjny w
parach w obrębie zadania miał do dyspozycji 2-4 zadania na kontrast zamiast 10.
Poniższe liczby są **opisem 25 przebiegów, nie wynikiem pilotażu**.

## Kontrola eksperymentu (zapisane w każdym przebiegu)

| | |
|---|---|
| model | `claude-sonnet-5` (wszystkie warunki) |
| temperatura | brak — API odrzuca `temperature` dla tego modelu (`deprecated for this model`, zweryfikowane); w każdym warunku obowiązuje ten sam domyślny sampler |
| seed kolejności | 20260807 |
| sha256 architecture.yaml | `b20e0862aff96abd6cb7b941a4713c868745bb2f12c39a75f6de38ebdcb04fc6` |
| K zaplanowane / R | 10 / 3 |
| reguły held-out | 7 z 36 |
| rozkład zadań | {'litrev-backend': 6, 'litrev-frontend': 4} |

Zadania: 6 backend + 4 frontend (pula 34 kandydatów -> 13 kwalifikujących się
przez filtry C1-C3 -> 10 wybranych; `eval/study3_agent/tasks_audit.json` zawiera
zmierzone wartości progów dla każdego kandydata i przyczynę odrzucenia).

## Miara pierwotna nie ma wariancji

`delta_errors` (błędy `limen check` na drzewie agenta minus na drzewie rodzica)
wynosi **0 w 23 z 25** przebiegów.
Odsetek przebiegów wprowadzających nowy błąd:
**0.08** (95% CI Wilsona
[0.022, 0.250]).

Wariancja międzyzadaniowa różnic jest **zerowa dla każdego z sześciu kontrastów**,
więc test permutacyjny wchodzi w tryb `all-zero` i zwraca p=1,0 — nie jako wynik
negatywny, ale jako degenerację: **przy tej miarze żadne K ani R nie wykryje
różnicy średnich, bo nie ma czego różnicować.** To jest główna obserwacja
metodologiczna pilotażu.

Dlaczego podłoga: wszystkie 10 drzewa rodzica mają
**0 błędów na regułach pokazywanych** agentowi (cały dług historyczny leży na
regułach held-out). Zadania to małe, dobrze zakresowane zmiany w jednym module,
a reguły egzekwują granice **między** modułami. Agent, który poprawnie
implementuje zlecenie w jednym module, po prostu nie ma jak naruszyć tych reguł.

## Miara współpierwotna: regresja funkcjonalna

Kryterium: żaden test nie zawodzi, którego nie zawodziło drzewo rodzica, ORAZ
liczba przechodzących testów nie spadła. Drugi warunek jest niezbędny —
zweryfikowano, że błąd fatalny PHP przerywa runner przed wypisaniem
pojedynczych testów (exit 255, pusty wiersz podsumowania, 41 utraconych
przechodzących testów).

| warunek | n | przebiegi z regresją | odsetek | 95% CI |
|---|---|---|---|---|
| A — no rules | 6 | 1 | 0.17 | [0.030, 0.564] |
| B — full prose (AGENTS/CLAUDE.md) | 5 | 1 | 0.20 | [0.036, 0.624] |
| C — scoped contract (limen context) | 5 | 2 | 0.40 | [0.118, 0.769] |
| D — contract + repair loop | 9 | 4 | 0.44 | [0.189, 0.733] |

Przedziały pokrywają się w każdej parze; przy n=5-9 na warunek nie da się
odróżnić 0.17 od 0.44. Kierunek surowych liczb (więcej regresji w C i D)
jest **nieinterpretowalny** przy tym n i tej nierównowadze pokrycia.

## Redukcja kontekstu B->C (wynik wtórny, zmierzony)

Proza: **48451 B** (33 plików AGENTS.md/CLAUDE.md).
Kontrakt generowany: **9023 B** średnio.
Redukcja **81.4%** (współczynnik 5.37x).
Tokeny wejściowe: A 55717, B 75935,
C 40521, D 65655.
To jedyna liczba w tym raporcie, która nie zależy od mocy statystycznej.

## Kontrola cyrkularności

**1. Ręczna weryfikacja próbki findings.** Wylosowano **50** findings (seed
20260807, warstwowo po `rule_id`, **13 reguł**) z findings Badania 2 o severity
error w obecnych repozytoriach, bez `spec-dead-entry` i `unresolved-dependency`
(rama próbkowania: 125 unikalnych findings w miejscach kodu).
Rater mechaniczny: cytowany plik istnieje **50/50**, cytowany target występuje
w pliku **49/50**, target na cytowanej linii **46/50**. Wśród findings, których
reguła w ogóle cytuje linię i których dokładne drzewo historyczne dało się
odtworzyć: **32/32** (95% CI [0,893; 1,000]). Cztery odstępstwa to 3 reguły
niecytujące linii z konstrukcji (structure, baseline-snapshot) i 1 przypadek,
gdzie drzewo historyczne było niedostępne i użyto zamrożonego workspace o innej
numeracji linii.
**Wymóg protokołu o ZGODNOŚCI dwóch raterów NIE jest spełniony** — drugi rater
(LLM) jest zaimplementowany w `eval/study3_agent/verify_findings.py` i
zaseedowany, ale nie został uruchomiony, bo limit tokenów sesji był już
wyczerpany. Zgodność i kappa Cohena pozostają nieobliczone.

**2. Niezależna miara funkcjonalna** — wyżej; nie pochodzi od limen.

**3. Held-out rules.** 7 z 36 reguł usunięto ze specyfikacji, z której
generowany jest kontrakt dla warunków C i D, oraz z findings zwracanych agentowi
w pętli D; punktacja zawsze na pełnej specyfikacji. Reguły wybrano warstwowo po
rodzajach tak, by w obu połowach były reguły faktycznie odpalające w tym
kodzie: `host-cross-module-public-surface`, `host-into-core-baseline`, `runtime-console-log`, `modules-have-no-local-deployment`, `support-and-models-module-import-allowlist`, `shared-not-into-host-modules`, `oversized-page-file`.
**Wynik: 0 nowych błędów na regułach held-out w każdym warunku** (i 0 na
regułach pokazywanych poza 2 przebiegami). Przy 25 przebiegach ten podział nie
rozstrzyga niczego — mierzy tę samą podłogę.

## Pętla D

Iteracji średnio 1.22; rozkład {'1': 8, '3': 1}.
8 z 9 przebiegów D nie miało co naprawiać (0 błędów na regułach pokazywanych już
w iteracji 0), 1 wykorzystał wszystkie iteracje. Mechanizm pętli zweryfikowano
osobno wstrzykniętym findingiem na regule pokazywanej: pętla poprawnie wykonała
drugą iterację i finding zniknął.

## Analiza mocy — co potrzeba do pełnego badania

Dla miary pierwotnej pytanie o moc jest źle postawione (brak wariancji). Wersje
odpowiadalne:

**Zdarzenie „przebieg wprowadził jakikolwiek nowy błąd"** przy zaobserwowanym
odsetku 0.08:
553 przebiegów na warunek, by wykryć
spadek o połowę; 76 by wykryć 0,20 vs 0,05.

**Regresja funkcjonalna** przy zaobserwowanym łącznym odsetku 0.32:
49 przebiegów na warunek dla 0,40 vs 0,15 (np. K=20, R=3),
82 dla 0,40 vs 0,20 (np. K=20, R=5),
76 dla 0,20 vs 0,05 (np. K=20, R=4).
Moc 0,80, alfa 0,05 dwustronne, przybliżenie normalne dla dwóch proporcji.

Wszystkie trzy szacunki mieszczą się w przedziale **K=20, R=3-5**, czyli
dokładnie w pierwotnie odrzuconym wariancie K=20/R=5 (400 przebiegów). Przy
zmierzonym koszcie 60299 tokenów wejściowych na przebieg to
~24.1 mln tokenów wejściowych — wykonalne tylko rozłożone na wiele
sesji albo z budżetem podniesionym.

## Zalecenia projektowe przed pełnym badaniem

1. **Miara pierwotna musi mieć wariancję.** Obecne zadania (mała zmiana w jednym
   module) nie mogą naruszyć reguł, które egzekwują granice między modułami.
   Trzeba albo zadań, których zakres z natury przekracza granicę modułu (a wtedy
   prompt musi to opisać bez zdradzania architektury — filtr C3 to utrudnia),
   albo miary czulszej niż liczba errorów (np. odległość od granicy: czy import
   trafił w Contracts czy w konkretną klasę Application).
2. **Podłogę raportować jako wynik.** „Agent nie łamie tych reguł, bo zadania nie
   dają mu okazji" to informacja o zakresie stosowalności kontraktu, nie porażka
   pomiaru.
3. **Zadanie fe-08 do wymiany.** W 5 z 5 przebiegów agent odmówił implementacji,
   trafnie zauważając, że modułu przeglądu literatury nie ma w pokazanym
   zakresie plików (prompt pochodzi z commita, który dotyka `projects`, ale
   opisuje funkcję z `reviews`). To defekt selekcji zadania, nie zachowania
   agenta — filtr C1 patrzy na moduły dotknięte commitem, nie na moduły, których
   wymaga treść zlecenia.
4. **Budżet planować per sesja.** 60299 tokenów wejściowych na przebieg x liczba
   komórek; 2,0 mln na sesję to ~33 komórki.

## Ograniczenia

- **L1 Przebieg niepełny** (25/120), pokrycie niezbalansowane, żadne zadanie nie
  ma pełnego zestawu warunków. Nie należy z tych liczb wnioskować o różnicach
  między warunkami.
- **L2 Anachronizm specyfikacji** (jak w Badaniu 2): jedna, dzisiejsza
  specyfikacja dla drzew historycznych; `units.registry_policy` zrelaksowane do
  `open`, bo `exact` przerywa kodem 3 przed uruchomieniem reguł.
- **L3 Miara funkcjonalna to kryterium REGRESJI, nie akceptacji.** Suite modułu
  z drzewa rodzica nie wie nic o zleconej funkcji, więc „agent nie zaimplementował
  zadania" przechodzi jako sukces. Miara wykrywa tylko „agent zepsuł to, co
  działało".
- **L4 Zgodność dwóch raterów nieobliczona** (patrz kontrola 1).
- **L5 Jedno drzewo rodzica na zadanie**, złożone z commitów współczesnych w
  pozostałych repozytoriach — rekonstrukcja, która nie musiała nigdy istnieć
  jednocześnie (jak oś `workspace` w Badaniu 2).
- **L6 Prompty przepisane przez LLM.** Filtr leksykalny C3 gwarantuje brak
  terminów architektonicznych, ale nie gwarantuje, że przepisane zlecenie jest
  wiernym opisem commita; oryginalny komunikat jest zapisany w polu `source`
  każdego zadania do weryfikacji.
