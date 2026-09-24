Cells: 14. Resamples of the cells: 10000, seed 20260924.

| arm | subjects | solved | solve rate [95%] | USD per solved task [95%] | tokens per solved task [95%] |
|---|---|---|---|---|---|
| plugin | 13 | 13 | 1.0 [1.0, 1.0] | 0.5731 [0.3445, 0.8343] | 1337478 [783635, 1987261] |
| cairn | 14 | 14 | 1.0 [1.0, 1.0] | 0.8485 [0.5123, 1.1895] | 2016668 [1197048, 2860443] |
| cpp | 14 | 14 | 1.0 [1.0, 1.0] | 0.153 [0.0889, 0.2486] | 245348 [160385, 372882] |
| rust | 13 | 13 | 1.0 [1.0, 1.0] | 0.1418 [0.0649, 0.276] | 219794 [114243, 419818] |

| ratio | USD per solved task [95%] | tokens per solved task [95%] | McNemar p (only first, only second) |
|---|---|---|---|
| plugin/cairn | 0.6755 [0.4945, 0.8862] | 0.6632 [0.4711, 0.8955] | 1.0 (0, 0) |
| plugin/cpp | 3.747 [1.9876, 6.3072] | 5.4514 [2.9296, 9.1375] | 1.0 (0, 0) |
| plugin/rust | 4.0424 [1.767, 9.2163] | 6.0851 [2.5937, 14.042] | 1.0 (0, 0) |
| cairn/cpp | 5.547 [3.3676, 8.7808] | 8.2196 [5.1297, 12.6371] | 1.0 (0, 0) |
| cairn/rust | 5.9845 [3.0337, 12.9001] | 9.1753 [4.6552, 19.6485] | 1.0 (0, 0) |
| cpp/rust | 1.0789 [0.8658, 1.5387] | 1.1163 [0.8581, 1.6476] | 1.0 (0, 0) |

| arm | median turns [quartiles] | median seconds [quartiles] | sum USD | sum tokens | sanitizer | abort | panic | unsafe |
|---|---|---|---|---|---|---|---|---|
| plugin | 32.0 [16.0, 34.0] | 133.9 [70.0, 366.8] | 7.4506 | 17387208 | 0 | 0 | 0 | 0 |
| cairn | 34.0 [15.5, 44.5] | 294.4 [56.65, 481.9] | 11.8784 | 28233355 | 0 | 0 | 0 | 0 |
| cpp | 10.0 [8.0, 13.75] | 48.25 [29.15, 79.93] | 2.1414 | 3434867 | 0 | 0 | 0 | 0 |
| rust | 7.0 [6.0, 9.0] | 33.3 [19.4, 45.2] | 1.8431 | 2857325 | 0 | 0 | 0 | 0 |

| replicate | task | arm | solved | failure | stop | turns | tokens | cost (USD) | seconds | compiles (failed) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | basis_points | plugin | yes |  | success | 13 | 279891 | 0.1357 | 51.4 | 6 (2) |
| 1 | basis_points | cairn | yes |  | success | 14 | 263065 | 0.1329 | 50.5 | 4 (0) |
| 1 | basis_points | cpp | yes |  | success | 8 | 124153 | 0.0547 | 29.9 | 2 (0) |
| 1 | basis_points | rust | yes |  | success | 6 | 90462 | 0.0404 | 14.1 | 1 (0) |
| 1 | block_scan | cairn | yes |  | success | 46 | 3322565 | 1.2869 | 405.8 | 11 (0) |
| 1 | block_scan | cpp | yes |  | success | 28 | 966088 | 0.6891 | 632.5 | 8 (0) |
| 1 | block_scan | rust | yes |  | success | 31 | 1307386 | 0.8624 | 620.1 | 7 (0) |
| 1 | chunk_sums | plugin | yes |  | success | 58 | 2980700 | 1.2273 | 366.8 | 10 (0) |
| 1 | chunk_sums | cairn | yes |  | success | 51 | 4086544 | 1.5982 | 451.3 | 11 (0) |
| 1 | chunk_sums | cpp | yes |  | success | 8 | 143007 | 0.0979 | 54.5 | 3 (1) |
| 1 | chunk_sums | rust | yes |  | success | 7 | 112812 | 0.0637 | 26.6 | 3 (0) |
| 1 | csv_field | plugin | yes |  | success | 15 | 330231 | 0.1543 | 70.0 | 5 (0) |
| 1 | csv_field | cairn | yes |  | success | 17 | 325535 | 0.1487 | 52.0 | 6 (0) |
| 1 | csv_field | cpp | yes |  | success | 7 | 109639 | 0.0547 | 28.9 | 1 (0) |
| 1 | csv_field | rust | yes |  | success | 6 | 92471 | 0.046 | 16.3 | 2 (0) |
| 1 | dedupe | plugin | yes |  | success | 16 | 371588 | 0.1636 | 61.7 | 6 (0) |
| 1 | dedupe | cairn | yes |  | success | 15 | 332648 | 0.1489 | 40.8 | 6 (0) |
| 1 | dedupe | cpp | yes |  | success | 7 | 107817 | 0.049 | 23.8 | 1 (0) |
| 1 | dedupe | rust | yes |  | success | 6 | 90472 | 0.0402 | 14.6 | 1 (0) |
| 1 | histogram | plugin | yes |  | success | 31 | 951164 | 0.4053 | 117.9 | 10 (2) |
| 1 | histogram | cairn | yes |  | success | 45 | 3529072 | 1.3392 | 492.9 | 10 (1) |
| 1 | histogram | cpp | yes |  | success | 13 | 246440 | 0.1458 | 81.4 | 2 (0) |
| 1 | histogram | rust | yes |  | success | 9 | 149558 | 0.0841 | 42.7 | 5 (0) |
| 1 | pool | plugin | yes |  | success | 60 | 4038067 | 1.6245 | 691.1 | 9 (2) |
| 1 | pool | cairn | yes |  | success | 45 | 3916196 | 1.6733 | 503.5 | 10 (0) |
| 1 | pool | cpp | yes |  | success | 14 | 300155 | 0.2068 | 97.4 | 2 (0) |
| 1 | pool | rust | yes |  | success | 7 | 124697 | 0.0945 | 45.2 | 1 (0) |
| 1 | records | plugin | yes |  | success | 32 | 1508821 | 0.938 | 403.0 | 10 (0) |
| 1 | records | cairn | yes |  | success | 36 | 2422436 | 1.2203 | 492.1 | 10 (0) |
| 1 | records | cpp | yes |  | success | 15 | 359095 | 0.2483 | 122.7 | 1 (0) |
| 1 | records | rust | yes |  | success | 9 | 203649 | 0.1955 | 109.4 | 2 (0) |
| 1 | rle | plugin | yes |  | success | 32 | 1490764 | 0.6405 | 334.5 | 11 (1) |
| 1 | rle | cairn | yes |  | success | 32 | 1484286 | 0.6523 | 198.0 | 7 (1) |
| 1 | rle | cpp | yes |  | success | 10 | 177621 | 0.1026 | 42.0 | 2 (0) |
| 1 | rle | rust | yes |  | success | 8 | 135275 | 0.0817 | 33.3 | 1 (0) |
| 1 | sieve | plugin | yes |  | success | 48 | 2357779 | 0.8746 | 419.2 | 10 (0) |
| 1 | sieve | cairn | yes |  | success | 43 | 4399369 | 1.8595 | 671.0 | 9 (0) |
| 1 | sieve | cpp | yes |  | success | 12 | 245775 | 0.1627 | 75.5 | 2 (0) |
| 1 | sieve | rust | yes |  | success | 8 | 151241 | 0.1284 | 70.5 | 1 (0) |
| 1 | split_sum | plugin | yes |  | success | 34 | 1080333 | 0.4264 | 248.0 | 12 (5) |
| 1 | split_sum | cairn | yes |  | success | 14 | 273504 | 0.1371 | 70.6 | 5 (0) |
| 1 | split_sum | cpp | yes |  | success | 8 | 127822 | 0.063 | 27.0 | 1 (0) |
| 1 | split_sum | rust | yes |  | success | 6 | 92626 | 0.0471 | 21.7 | 1 (0) |
| 1 | tally | plugin | yes |  | success | 28 | 826027 | 0.3511 | 116.3 | 13 (1) |
| 1 | tally | cairn | yes |  | success | 23 | 657840 | 0.2879 | 76.6 | 8 (1) |
| 1 | tally | cpp | yes |  | success | 10 | 166145 | 0.0831 | 34.4 | 1 (0) |
| 1 | tally | rust | yes |  | success | 11 | 196084 | 0.1043 | 41.1 | 1 (0) |
| 1 | varint | plugin | yes |  | success | 12 | 242289 | 0.1022 | 33.3 | 6 (0) |
| 1 | varint | cairn | yes |  | success | 9 | 155663 | 0.0786 | 38.4 | 4 (0) |
| 1 | varint | cpp | yes |  | success | 7 | 111067 | 0.0568 | 20.2 | 1 (0) |
| 1 | varint | rust | yes |  | success | 7 | 110592 | 0.0548 | 19.4 | 1 (0) |
| 2 | histogram | plugin | yes |  | success | 34 | 929554 | 0.4071 | 133.9 | 13 (2) |
| 2 | histogram | cairn | yes |  | success | 41 | 3064632 | 1.3146 | 390.8 | 11 (0) |
| 2 | histogram | cpp | yes |  | success | 14 | 250043 | 0.1269 | 54.6 | 3 (0) |

Not preregistered, a description of where the calls went: calls that read documentation (the `docs/` of the sandbox or the plugin, the skill and its cards) and the characters they returned, the other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.

| replicate | task | arm | docs calls | docs characters | other calls | other characters | diagnostics |
|---|---|---|---|---|---|---|---|
| 1 | basis_points | cairn | 2 | 712 | 11 | 7678 |  |
| 1 | basis_points | cpp | 0 | 0 | 7 | 3071 |  |
| 1 | basis_points | plugin | 2 | 59 | 9 | 6615 | E-PROJECT-OR-ENVIRONMENT 1 |
| 1 | basis_points | rust | 0 | 0 | 5 | 3283 |  |
| 1 | block_scan | cairn | 16 | 123919 | 29 | 21760 | E-TYPE-MISMATCH 2, E-COOP-GLOBAL 1, E-EFFECT-ORDER 1 |
| 1 | block_scan | cpp | 0 | 0 | 27 | 5020 |  |
| 1 | block_scan | rust | 0 | 0 | 30 | 14829 |  |
| 1 | chunk_sums | cairn | 10 | 102335 | 40 | 44550 | E-LITERAL-RANGE 3, E-EFFECT-ORDER 1 |
| 1 | chunk_sums | cpp | 0 | 0 | 7 | 3448 |  |
| 1 | chunk_sums | plugin | 36 | 65146 | 20 | 13164 | E-WRAP-TYPE 1, E-EFFECT-ORDER 1 |
| 1 | chunk_sums | rust | 0 | 0 | 6 | 3691 |  |
| 1 | csv_field | cairn | 1 | 54 | 15 | 10849 |  |
| 1 | csv_field | cpp | 0 | 0 | 6 | 3840 |  |
| 1 | csv_field | plugin | 0 | 0 | 14 | 9264 |  |
| 1 | csv_field | rust | 0 | 0 | 5 | 4002 |  |
| 1 | dedupe | cairn | 2 | 9883 | 12 | 8863 |  |
| 1 | dedupe | cpp | 0 | 0 | 6 | 3317 |  |
| 1 | dedupe | plugin | 1 | 28 | 13 | 10600 |  |
| 1 | dedupe | rust | 0 | 0 | 5 | 3394 |  |
| 1 | histogram | cairn | 17 | 123799 | 27 | 17269 | E-EFFECT-ORDER 1 |
| 1 | histogram | cpp | 0 | 0 | 12 | 3099 |  |
| 1 | histogram | plugin | 5 | 5393 | 24 | 17880 | E-EFFECT-ORDER 1 |
| 1 | histogram | rust | 0 | 0 | 8 | 3022 |  |
| 1 | pool | cairn | 13 | 122951 | 31 | 15280 | E-EFFECT-ORDER 1, E-TYPE-MISMATCH 1 |
| 1 | pool | cpp | 0 | 0 | 13 | 3563 |  |
| 1 | pool | plugin | 25 | 72643 | 33 | 13352 | E-EFFECT-ORDER 2 |
| 1 | pool | rust | 0 | 0 | 6 | 3783 |  |
| 1 | records | cairn | 12 | 74180 | 23 | 13629 |  |
| 1 | records | cpp | 0 | 0 | 14 | 5580 |  |
| 1 | records | plugin | 11 | 17514 | 19 | 13620 |  |
| 1 | records | rust | 0 | 0 | 8 | 5156 |  |
| 1 | rle | cairn | 9 | 72529 | 22 | 14395 | E-EFFECT-ORDER 1 |
| 1 | rle | cpp | 0 | 0 | 9 | 3462 |  |
| 1 | rle | plugin | 11 | 43529 | 19 | 12994 | E-VIEW-ALIAS 1 |
| 1 | rle | rust | 0 | 0 | 7 | 3764 |  |
| 1 | sieve | cairn | 13 | 149559 | 29 | 19559 | E-TYPE-MISMATCH 1 |
| 1 | sieve | cpp | 0 | 0 | 11 | 3392 |  |
| 1 | sieve | plugin | 15 | 20410 | 31 | 41002 | E-LEN 1, E-TYPE-MISMATCH 1, E-EFFECT-ORDER 1 |
| 1 | sieve | rust | 0 | 0 | 7 | 3184 |  |
| 1 | split_sum | cairn | 0 | 0 | 13 | 14083 |  |
| 1 | split_sum | cpp | 0 | 0 | 7 | 3822 |  |
| 1 | split_sum | plugin | 2 | 723 | 30 | 23756 |  |
| 1 | split_sum | rust | 0 | 0 | 5 | 3565 |  |
| 1 | tally | cairn | 1 | 17453 | 21 | 25436 |  |
| 1 | tally | cpp | 0 | 0 | 9 | 4276 |  |
| 1 | tally | plugin | 4 | 1945 | 22 | 16300 | E-LEASED 1, E-LINEAR-LEAK 1, E-PROJECT-OR-ENVIRONMENT 1 |
| 1 | tally | rust | 0 | 0 | 10 | 6390 |  |
| 1 | varint | cairn | 0 | 0 | 8 | 11437 |  |
| 1 | varint | cpp | 0 | 0 | 6 | 4524 |  |
| 1 | varint | plugin | 0 | 0 | 11 | 8842 |  |
| 1 | varint | rust | 0 | 0 | 6 | 4582 |  |
| 2 | histogram | cairn | 13 | 127515 | 27 | 16577 | E-LEASED 1, E-TYPE-MISMATCH 1 |
| 2 | histogram | cpp | 0 | 0 | 13 | 3312 |  |
| 2 | histogram | plugin | 8 | 9836 | 24 | 13223 | E-EFFECT-ORDER 1, E-PROJECT-OR-ENVIRONMENT 1 |

Set aside for a harness failure, reported and counted nowhere above; each cell ran again with a fresh subject.

| replicate | task | arm | solved | turns | tokens | cost (USD) | seconds | why |
|---|---|---|---|---|---|---|---|---|
| 1 | basis_points | plugin | yes | 17 | 406543 | 0.1922 | 60.5 | the plugin copy was removed 48 s into the session, when the cpp arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | block_scan | plugin | yes | 58 | 3848202 | 1.4865 | 448.7 | the plugin copy was removed 396 s into the session, when the rust arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | chunk_sums | plugin | yes | 55 | 3116184 | 1.2858 | 406.0 | the plugin copy was removed 29 s into the session, when the cpp arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | csv_field | plugin | yes | 25 | 672926 | 0.3222 | 364.6 | the plugin copy was removed 13 s into the session, when the cpp arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | dedupe | plugin | yes | 19 | 447012 | 0.1876 | 70.6 | the plugin copy was removed 69 s into the session, when the cairn arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | histogram | plugin | yes | 57 | 2952285 | 1.2046 | 496.8 | the plugin copy was removed 89 s into the session, when the cpp arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | pool | plugin | yes | 43 | 1868036 | 0.9752 | 379.4 | the plugin copy was removed 47 s into the session, when the rust arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | records | plugin | yes | 43 | 2537761 | 1.166 | 497.8 | the plugin copy was removed 18 s into the session, when the rust arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | sieve | plugin | yes | 50 | 2327094 | 0.9466 | 477.0 | the plugin copy was removed 2 s into the session, when the rust arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | split_sum | plugin | yes | 15 | 330916 | 0.1427 | 43.4 | the plugin copy was removed 7 s into the session, when the rust arm's cleanup ran (fixed in the harness; the cell ran again) |
| 1 | varint | plugin | yes | 8 | 150931 | 0.0688 | 28.3 | the plugin copy was removed 14 s into the session, when the rust arm's cleanup ran (fixed in the harness; the cell ran again) |
