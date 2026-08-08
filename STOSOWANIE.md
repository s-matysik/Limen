# limen w praktyce badawczej — wnioski z czterech badań

Dokument towarzyszący `research-report.md`. Tam są liczby i komendy, które je odtwarzają;
tutaj — co z nich wynika dla kogoś, kto rozważa użycie limen we własnym projekcie.
Wszystkie przywołane wielkości pochodzą z `eval/results/`.

## 1. Do czego limen nadaje się udowodnienie

**Zastąpienie rozproszonych checkerów architektury jedną specyfikacją.**
Retrofit zamienił 47 reguł rozsianych po 1246 liniach kodu w trzech językach (PHP config,
klasy PHPUnit, skrypt Node) na 36 deklaracji w 526 liniach YAML. Semantyka została zachowana
w sposób sprawdzalny: 0 różnic niesklasyfikowanych, a oracle uruchamiał **niezmieniony** kod
legacy zwalidowany prawdziwym Laravelem (26/26 testów, 20/20 mutantów z identycznymi zbiorami
failures).

**Bramka CI wymagająca powtarzalności.**
Determinizm bajtowy potwierdzony na 539 plikach i 2274 zależnościach, w trzech wariantach
uruchomienia (zimny cache, ciepły cache, losowy PYTHONHASHSEED). Dla bramki, która blokuje merge,
to jest funkcja krytyczna i jedyna, której nie wolno przyjmować na wiarę.

**Generowanie zwięzłego kontekstu dla agenta kodującego.**
`limen context` produkuje 9,0 kB tam, gdzie pełna proza architektoniczna zajmuje 48,5 kB —
redukcja 81,4% (5,37×). Ta liczba jest własnością generatora, nie próby, więc nie zależy od
liczebności eksperymentu. **To jedyny zmierzony efekt Badania 3.**

**Wykrywanie luk w checkerach, które już masz.**
Trzy błędy `host-cross-module-public-surface` znalezione przez limen były niewidoczne dla legacy,
bo jego regexy były zakotwiczone w `App\Modules\`, więc importy z `LitRev\BackendShared\`
przechodziły bokiem. Retrofit ma wartość diagnostyczną niezależnie od tego, czy zostaniesz
przy nowym narzędziu.

## 2. Czego badania NIE wykazały

**Że kontrakt poprawia jakość pracy agenta.** Badanie 3 tego nie zmierzyło — miara pierwotna
(liczba nowych naruszeń) okazała się zdegenerowana: zero w 23 z 25 przebiegów, zerowa wariancja
międzyzadaniowa we wszystkich sześciu kontrastach. Nie jest to wynik „brak efektu", tylko
„pomiar nie mógł efektu wykryć". Kto chce tę tezę obronić, musi zaprojektować zadania inaczej
(patrz §4).

**Że narzędzie obsłuży dowolny istniejący config.** Z 60 repozytoriów korpusu porównanie doszło
do skutku w **12**. Reszta odpadła głównie na nietłumaczalności configu. Jeśli masz działający
deptrac albo import-linter, zakładaj, że migracja będzie ręczna.

**Że reguły warstwowe i własnościowe działają w praktyce produkcyjnej.** W historii 69 commitów
**pięć z jedenastu rodzajów reguł nie odpaliło się ani razu** (K1 layer-dependency, K2 ownership,
K3 consumer-allowlist, K6 forbidden-target poza wariantem bez wyjątków, K11 reachability).
Że działają, wiadomo z testów mutacyjnych; że łapią realny dług w typowym projekcie — nie.

## 3. Kiedy to się opłaca, a kiedy nie

**Opłaca się, gdy:**
- masz **więcej niż jeden** checker architektury i zaczynają się rozjeżdżać;
- pracujesz w wielorepo i potrzebujesz reguł **przez granice repozytoriów** — legacy per-repo
  ich z definicji nie widzi (w Badaniu 2 tylko oś zrekonstruowanego workspace pokazywała K4);
- karmisz agenta kodującego prozą architektoniczną i płacisz za to kontekstem;
- reguły muszą przetrwać zmianę osoby, która je napisała — YAML z `reason`/`owner`/`expires`
  przy każdym wyjątku jest czytelny dla następcy, regex w klasie testowej nie.

**Nie opłaca się, gdy:**
- masz jeden, działający checker i nikt się na niego nie skarży;
- projekt jest jednomodułowy — reguły egzekwują granice **między** modułami, więc nie mają
  czego pilnować;
- liczysz na automatyczną migrację z istniejącego narzędzia.

## 4. Jak wdrażać — kolejność wynikająca z badań

1. **Zacznij od inwentarza, nie od pisania YAML-a.** Wypisz każdą regułę, którą realnie
   egzekwujesz: plik, linie, semantyka, wyjątki. Bez tego nie odróżnisz retrofitu od
   przepisywania architektury przy okazji.
2. **Nie rozluźniaj reguły, żeby kod przeszedł.** Regułę odwzorowujesz wiernie; naruszenie
   zostaje naruszeniem. To jest różnica między migracją a ukryciem długu.
3. **Zmierz równoważność, zanim wyłączysz stary checker.** Uruchom oba na tym samym drzewie
   i sklasyfikuj każdą różnicę. Kryterium odbioru: zero różnic bez kategorii, wymuszone
   niezerowym kodem wyjścia — brama, nie raport.
4. **Dodaj test czułości.** Dwa narzędzia milczące na czystym drzewie „zgadzają się" pozornie.
   Wstrzyknij po jednym syntetycznym naruszeniu na regułę i sprawdź, czy **oba** je łapią.
   U nas 35 mutacji, 35/35 wykrytych — dopiero to jest dowód.
5. **Wyjątki od razu z datą ważności.** Zmierzona mediana wieku długu legalizowanego wyjątkami
   to 76,3 dnia przy deklarowanym życiu 149 dni, i wszystkie 4 wyjątki były wciąż otwarte na
   końcu okna. Bez `expires` wyjątek jest wieczny.
6. **`limen context` wdrażaj niezależnie od bramki CI.** Redukcja kontekstu jest zmierzona
   i nie wymaga, żeby cała specyfikacja była kompletna.

## 5. Pułapki, które kosztowały nas czas

**Ścieżka `--spec` musi być absolutna.** Względne są rozwiązywane wobec `--workspace`, co daje
mylące „specification file not found".

**`units.registry_policy: exact` przerywa drzewa historyczne kodem 3** zanim uruchomi jakąkolwiek
regułę. Analiza historyczna bez relaksacji do `open` zwróci zero findings dla każdego commita
i będzie wyglądać na czysty projekt.

**Cache faktów jest kluczowany treścią plików, nie wersją ekstraktora.** Po zmianie kodu
ekstraktora trzeba go wyczyścić ręcznie, inaczej dostaniesz stare wyniki.

**Przy walidacji z Composerem nie symlinkuj `vendor/`.** Autoloader wyprowadza baseDir
z *rozwiązanej* ścieżki, więc klasy ładują się z drzewa pierwotnego i każdy mutant „przechodzi" —
fałszywa zgodność wyglądająca jak sukces. Kopiuj naprawdę (`cp -Rc`).

**Sprawdź, czy oracle porównawczy w ogóle żyje.** dependency-cruiser bez `node_modules`
nie rozwiązuje większości zależności i raportuje zero, nic nie rozstrzygnąwszy. Bez progu
rozwiązywalności takie repo policzy się jako pełna zgodność.

## 6. Jeśli chcesz powtórzyć Badanie 3

Pilotaż wykrył defekt konstrukcji za 21% budżetu i to jest jego główna wartość. Warunki
sensownego powtórzenia:

- **Zadania muszą dotykać więcej niż jednego modułu.** Reguły egzekwują granice międzymodułowe;
  zmiana wewnątrz jednego modułu nie ma jak ich naruszyć, niezależnie od liczebności próby.
- **Miarą pierwotną powinna być regresja funkcjonalna**, nie liczba naruszeń — to ona ma
  wariancję (0,32 [0,17; 0,52]).
- **Liczebność:** 49 przebiegów na ramię dla efektu 0,40 vs 0,15; 82 dla 0,40 vs 0,20.
  To K=20 przy R=3–5.
- **Budżet:** 60 299 tokenów wejściowych na komórkę. 120 komórek to 7,2 mln — nie mieści się
  w jednej sesji, planuj podział z przyrostowym zapisem przebiegów.
- **Kontrola cyrkularności wymaga drugiego ratera.** U nas zgodność dwóch raterów nie została
  obliczona i jest to zapisane jako niespełniony wymóg, nie przemilczane.

## 7. Jednozdaniowo

limen jest udowodniony jako **deterministyczna bramka CI i konsolidator rozproszonych reguł
architektury**, z realną i zmierzoną korzyścią w postaci 5,4× mniejszego kontekstu dla agenta;
teza o poprawie jakości pracy agenta pozostaje nierozstrzygnięta, a migracja z istniejących
narzędzi jest w praktyce robotą ręczną.
