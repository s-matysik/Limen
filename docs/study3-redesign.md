# Badanie 3 — jak przetestować hipotezę, której pilotaż nie przetestował

Dokument projektowy. Wszystkie liczby diagnostyczne pochodzą z `eval/results/`
(`study3_summary.json`, `study2_lifetimes.csv`) i są odtwarzalne komendami z `research-report.md`.

## 1. Hipoteza, dosłownie

Protokół (krok 17c): *„held-out rules: podzbiór zadań oceniasz regułami nieobecnymi w kontekście
podanym agentowi — jeśli poprawa przenosi się na reguły niepodane, to najmocniejszy wynik
artykułu."*

Rozbicie na dwie hipotezy, bo mają różny status dowodowy i różny koszt:

- **H1 (zgodność):** agent z kontraktem wprowadza mniej naruszeń reguł, **które mu pokazano**,
  niż agent z pełną prozą. Słabsza — mierzy, czy agent wykonuje instrukcję.
- **H2 (transfer):** poprawa przenosi się na reguły, **których mu nie pokazano**. Mocniejsza —
  mierzy, czy kontrakt uczy zasady, a nie tylko listy zakazów. To jest teza artykułu.

Pilotaż nie obalił żadnej z nich. Nie zdołał ich przetestować.

## 2. Dlaczego pilotaż nie zadziałał — diagnoza, nie wymówka

Δ liczby błędów = 0 w 23 z 25 przebiegów; wariancja międzyzadaniowa = 0 we wszystkich sześciu
kontrastach; test permutacyjny w trybie all-zero (p = 1). Trzy przyczyny, wszystkie zmierzone:

1. **Zadania nie mogły naruszyć reguł.** Reguły egzekwują granice *między* modułami; zadania
   były zmianami *wewnątrz* jednego modułu.
2. **Drzewa rodzica były czyste na regułach pokazywanych.** Cały dług historyczny leżał na
   regułach held-out, więc nie było czego naprawiać ani pogarszać.
3. **Naruszenia są w tym projekcie rzadkim zdarzeniem.** W całej historii **9 commitów**
   wprowadziło jakiekolwiek naruszenie (89 epizodów), z czego **7 zostało później naprawionych**.
   Losowo wybrane zadanie ma niską szansę trafić w sytuację, w której reguła w ogóle działa.

Punkt 3 jest najważniejszy: **LitRev nie ma dość materiału na K=20 zadań podatnych na
naruszenie.** Zwiększanie K w obrębie tego repozytorium nie pomoże.

## 3. Dobór zadań — trzy warstwy zamiast jednej

Zamiast „commity dodające use-case" (dobór, który dał podłogę), trzy warstwy o rosnącej
podatności na naruszenie. Każde zadanie dostaje etykietę warstwy i **jest raportowane osobno**.

### Warstwa 1 — zadania z udokumentowanym naruszeniem (n ≈ 7)

Siedem commitów, które historycznie wprowadziły naruszenie **później naprawione przez człowieka**:
`46bbf692` (41 naruszeń), `9cf5ac79` (15), `eab22e21` (12), `c1d3e424` (11), `82915ff2` (3),
`c32d1719` (2), `0334bc8f` (2). Wartość: wiadomo, że pokusa jest realna (człowiek jej uległ)
**i** że istnieje poprawne rozwiązanie (człowiek je potem napisał). To jest ground truth,
którego nie da się wymyślić.

Ograniczenie do zapisania: to są w większości duże commity, więc zadanie trzeba wyciąć do
podzadania obejmującego jedno naruszenie.

### Warstwa 2 — zadania międzymodułowe z historii (n ≈ 8)

Commity dotykające ≥ 2 modułów: backend `8e005bf0` (16 modułów), `eab22e21` (12), `46bbf692` (7),
`bf64942d` (5), `67cef387` (3), `6deb7853` (2); frontend 4 dalsze. Wymagają przekroczenia
granicy modułu, więc reguła ma szansę zadziałać. Duże commity tnie się na podzadania.

### Warstwa 3 — zadania syntetyczne, preregistrowane (n ≈ 10)

Warstwy 1–2 nie dadzą K=20 z samego LitRev. Brakujące zadania układa się syntetycznie —
**ale z zabezpieczeniem przeciw dopasowaniu ich pod reguły**:

- zadania pisze się **przed** wylosowaniem podziału na reguły pokazane i held-out;
- treść zadania opisuje **cel biznesowy** („dodaj powiadomienie e-mail przy zmianie właściciela
  projektu"), nigdy mechanizm („użyj Contracts");
- każde zadanie ma zapisane, **która granica architektoniczna jest w nim naturalnie kuszona**
  i dlaczego — to jest hipoteza autora, weryfikowana potem wynikiem warunku A;
- zadania, w których **warunek A nie naruszył niczego w żadnym powtórzeniu**, są odrzucane
  jako niediagnostyczne — z podaniem liczby odrzuconych. Zadanie, którego nie da się oblać,
  nie mierzy niczego.

Warstwa 3 jest oznaczona w `tasks.jsonl` i w każdej tabeli. Jeśli wynik na warstwach 1–2
różni się od wyniku na warstwie 3, to jest to informacja o generalizacji, nie szum do uśrednienia.

## 4. Podział reguł — jądro projektu H2

Podział na pokazane i held-out musi być **losowy i wykonany przed pierwszym przebiegiem**,
inaczej H2 jest niefalsyfikowalna.

- **Stratyfikacja po rodzaju reguły** (K1–K10). Bez tego wszystkie reguły jednego rodzaju mogą
  trafić do jednej połowy i „transfer" oznaczałby tylko podobieństwo w obrębie rodzaju.
- **Dwa niezależne podziały** (seed A i seed B), każde zadanie przechodzi oba. Transfer
  utrzymujący się przy obu jest odporny na przypadek doboru; transfer widoczny tylko przy jednym
  jest artefaktem.
- **Warunek konieczny, sprawdzany przed startem:** drzewo rodzica każdego zadania musi mieć
  ≥ 1 możliwe naruszenie reguły held-out. W pilotażu ten warunek był naruszony i to była
  bezpośrednia przyczyna Δ_heldout = 0 wszędzie.
- Sam podział zapisany w wynikach razem z sha256 specyfikacji.

## 5. Miary — zamiana pierwotnej na współpierwotną

| Miara | Rola | Uzasadnienie |
|---|---|---|
| regresja funkcjonalna (istniejący suite modułu pada) | **pierwotna** | jedyna zmierzona z niezerową wariancją: 0,32 [0,17; 0,52]; niezależna od limen |
| naruszenia reguł **held-out** | **pierwotna dla H2** | to jest teza artykułu |
| naruszenia reguł pokazanych | wtórna | zdegenerowana w pilotażu; raportowana, nie testowana jako główna |
| tokeny kontekstu | wtórna | zmierzona: redukcja 81,4% (5,37×) |
| iteracje do czystego D | wtórna | |

Zamiana miary pierwotnej to najważniejsza zmiana wobec protokołu i wynika wprost z pomiaru,
nie z wygody. Uzasadnienie idzie do artykułu razem z liczbą, która ją wymusiła.

**Miara zdarzeniowa zamiast liczbowej.** Zamiast średniej liczby naruszeń (zdominowanej przez
zera) — odsetek przebiegów z co najmniej jednym naruszeniem, z przedziałami Wilsona i modelem
logistycznym mieszanym (zadanie jako efekt losowy). Przy zdarzeniach rzadkich to jest właściwy
model, a nie test t na zerach.

## 6. Liczebność

Z pilotażu: regresja funkcjonalna 0,32 [0,17; 0,52], częstość zdarzeń 0,08 [0,02; 0,25].

| Cel | Przebiegi na ramię |
|---|---:|
| regresja 0,40 vs 0,15 | 49 |
| regresja 0,40 vs 0,20 | 82 |
| zdarzenia 0,30 vs 0,10 | 62 |
| zdarzenia 0,20 vs 0,05 | 76 |

To jest **K = 20, R = 4** (80 przebiegów na ramię, 320 łącznie) — z zastrzeżeniem, że liczby
te zakładają, iż nowy dobór zadań podniesie częstość zdarzeń z 0,08 do rzędu 0,20–0,30.
**Ten warunek trzeba sprawdzić drugim pilotażem**, zanim uruchomi się pełny przebieg:
K = 6 zadań z warstwy 1, tylko warunki A i C, R = 3 (36 przebiegów, ~2,2 mln tokenów wejściowych
przy zmierzonych 60 299 na komórkę — patrz tabela w §7).
Jeśli warunek A nie oblewa ≥ 30% przebiegów, projekt zadań nadal jest zły i pełny przebieg
byłby stratą.

## 7. Budżet i wykonanie

Zmierzone: 60 299 tokenów wejściowych na komórkę, 1 507 477 na 25 komórek.

| Etap | Komórki | Tokeny wejścia | Sesje (limit 2 mln) |
|---|---:|---:|---:|
| pilotaż 2 (walidacja doboru) | 36 | ~2,2 mln | 2 |
| pełne badanie K=20, R=4, 4 warunki | 320 | ~19,3 mln | 10–11 |
| dwa podziały reguł (§4) | ×2 dla H2 | ~+9,6 mln | +5 |

Wykonanie musi być **przyrostowe i wznawialne** — plan zamrożony przed startem
(`study3_plan.json` już to robi), każdy przebieg zapisywany osobno, wznowienie dokłada brakujące
komórki. To działało w pilotażu i jest jedynym powodem, dla którego przerwanie na 25/120 nie
skasowało pracy.

## 8. Kontrola cyrkularności — czego zabrakło

Protokół wymaga trzech środków; pilotaż dostarczył półtora.

1. **Ręczna weryfikacja ≥ 50 findings ze zgodnością dwóch raterów** — *niespełnione*.
   Rater mechaniczny sprawdził 50 pozycji z 13 reguł (50/50 realne, cel na wskazanej linii
   w 46/50), drugi rater jest zaimplementowany i zaseedowany, ale nie został uruchomiony.
   Bez drugiego ratera nie ma zgodności ani kappy — to musi być zrobione albo jawnie
   zaraportowane jako brak.
2. **Niezależna miara funkcjonalna** — *spełnione*, i awansuje na miarę pierwotną.
3. **Held-out rules** — *zaimplementowane, ale niediagnostyczne*, bo warunek z §4 nie był
   sprawdzony. Naprawa opisana wyżej.

## 9. Co uznać za wynik

- **H2 potwierdzona:** istotnie niższa częstość naruszeń reguł held-out w C/D niż w A/B,
  przy obu podziałach reguł, bez pogorszenia regresji funkcjonalnej. To jest wynik, o który
  chodzi w artykule.
- **H1 potwierdzona, H2 nie:** kontrakt działa jak lista zakazów — użyteczne narzędzie,
  słabsza teza. Też publikowalne, ale zmienia tytuł pracy.
- **Żadna nie potwierdzona przy zdrowej częstości zdarzeń** (A oblewa ≥ 30%): uczciwy wynik
  negatywny, mówiący że przy tym modelu i tych zadaniach kontrakt nie poprawia architektury.
  Wymaga zaraportowania z przedziałami ufności i mocą, nie samym p.
- **Częstość zdarzeń nadal przy podłodze:** projekt zadań jest zły, wynik nie istnieje.
  Nie wolno wtedy raportować „braku różnicy" jako wyniku — to jest błąd, którego pilotaż
  o mało nie popełnił.
