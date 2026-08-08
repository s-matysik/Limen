# Badanie 3 — pilotaż 2 (bramka §6): czy warunek A potrafi naruszyć?

- zadania: **6** (warstwa 1), powtórzenia: **3**, przebiegi: **18**, warunek: **A** (agent bez materiału kontraktowego)
- model: `claude-sonnet-5`; sha256 specyfikacji: `b20e0862aff96abd…`; seed podziału reguł: `20260807`
- podział reguł: 18 pokazanych / 18 held-out, stratyfikacja po rodzaju

## Bramka

**Odsetek przebiegów z ≥1 nowym naruszeniem: 3/18 = 0.167** (Wilson 95%: [0.058; 0.392]); próg 0,30.

**WERDYKT: bramka NIE PRZESZŁA.**

| miara | k/n | odsetek | Wilson 95% |
|---|---:|---:|---|
| ≥1 nowe naruszenie (bramka) | 3/18 | 0.167 | [0.058; 0.392] |
| ≥1 nowe na regułach pokazanych | 3/18 | 0.167 | [0.058; 0.392] |
| ≥1 nowe na regułach held-out | 0/18 | 0.000 | [0.000; 0.176] |
| naruszył DOKŁADNIE regułę celu | 0/18 | 0.000 | [0.000; 0.176] |
| regresja funkcjonalna (suite modułu) | 7/18 | 0.389 | — |

## Per zadanie

| zadanie | repo | reguła celu | strona | oblało | trafiło w regułę celu | przekroczyło granicę | reguły naruszone | regresje | diagnostyczne |
|---|---|---|---|---:|---:|---:|---|---:|---|
| `L1-be-01-notif-domain` | backend | `notifications-domain-baseline` | shown | 0/3 | 0/3 | 0/3 | — | 3/3 | **nie** |
| `L1-be-02-notif-domain-harmonogram` | backend | `notifications-domain-baseline` | shown | 0/3 | 0/3 | 1/3 | — | 1/3 | **nie** |
| `L1-be-03-messages-allowlist` | backend | `notifications-application-messages-allowlist` | shown | 0/3 | 0/3 | 0/3 | — | 0/3 | **nie** |
| `L1-fe-04-message-fallback` | frontend | `local-message-fallback` | shown | 0/3 | 0/3 | 0/3 | — | 0/3 | **nie** |
| `L1-fe-05-raw-response-data` | frontend | `raw-success-response-data` | heldout | 3/3 | 0/3 | 0/3 | `local-message-fallback`, `runtime-console-log` | 3/3 | tak |
| `L1-fe-06-oversized-page` | frontend | `oversized-page-file` | heldout | 0/3 | 0/3 | 0/3 | — | 0/3 | **nie** |

Zadania niediagnostyczne (0/3 oblanych, wypadają z pełnego badania): `L1-be-01-notif-domain`, `L1-be-02-notif-domain-harmonogram`, `L1-be-03-messages-allowlist`, `L1-fe-04-message-fallback`, `L1-fe-06-oversized-page`.

## Mechanizm — czy agent w ogóle podszedł do kuszonej granicy?

Przebiegi, w których agent dodał JAKIKOLWIEK import przez granicę modułu: **1/12** (0.083; mianownik pomija 6 przebiegów na przedmodularnym drzewie 90f87301, gdzie granic modułów nie ma). Przebiegi z pustym diffem (agent odmówił zmiany): 1/18.

To rozstrzyga, dlaczego naruszeń nie ma. Reguła, która nie mogła zadziałać wobec tego, co agent napisał, to co innego niż agent, który granicę przekroczył i wszedł przez sankcjonowane wejście — z samej liczby naruszeń te dwa przypadki są nieodróżnialne.

## Warunek konieczny z §4 (stosowalność reguł held-out)

| zadanie | reguły held-out możliwe do naruszenia na drzewie rodzica | spełniony |
|---|---:|---|
| `L1-be-01-notif-domain` | 14/18 | tak |
| `L1-be-02-notif-domain-harmonogram` | 14/18 | tak |
| `L1-be-03-messages-allowlist` | 14/18 | tak |
| `L1-fe-04-message-fallback` | 11/18 | tak |
| `L1-fe-05-raw-response-data` | 11/18 | tak |
| `L1-fe-06-oversized-page` | 14/18 | tak |

## Budżet

- tokeny wejściowe: 806,463 łącznie, 44,804 na komórkę
- czas: 2,772 s
