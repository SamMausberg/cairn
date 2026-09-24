# Every refusal in one check

What was run to support three claims about commit 91e2eb0, which makes `cairn check` report every independent refusal: the first refusal is unchanged, accepted programs emit the same C++, and checking takes no longer. Everything ran on 2026-09-24 on one machine: an AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18), Python 3.12.3, shared with four other agents running test suites.

## The first refusal is unchanged

`tools/checks/refusal_differential.py` takes every program `tools/checks/emission_identity.py` takes: each example, one program importing every std module, every CAIRN program written into a test and every `cairn` block of the docs. It checks each program as a check that stops would. Each refused program is checked again with every refusal reported, and the record must be the same record with at most `further`, `further_omitted` and `not_judged` added. It also requires each further refusal to be a whole diagnostic, in source order, never the first again, at most twenty, and it requires that no check ended early on a fault.

`refusal_differential.json`: of 1,938 programs, 1,089 are refused, and all 1,089 keep their first refusal exactly. 14 of them have further refusals (15 in all), 32 count functions that were not judged, and nothing broke. Most refused programs in the suite are written to have one mistake, so few have further refusals. The suite runs the same differential in `tests/language/test_refusals.py`, in eight parts.

## Accepted programs emit the same C++

`emission_identity.json`: the same programs were compiled at 2b1658f, the parent commit, and at 91e2eb0, and matched by program text, since inserting a test moves the line that names a program. All 1,801 programs of the parent are present after, and none differs. The 788 accepted ones emit the same C++ with the same effect rows, and the 1,013 refused ones are refused with the same code.

## Checking takes no longer

`check_time.json`: `compile_program` in a cold process, eleven runs of each arm interleaved, on `examples/apps/analytics` (637 lines, 172 functions) and on a generated project of 370 modules (`bench/scale/generate.py --modules 370`, 19,327 lines, 2,223 functions). The arms are the parent commit, 91e2eb0 as `build` checks, and 91e2eb0 as `cairn check` checks, with every refusal reported. The medians are 0.162, 0.159 and 0.158 seconds on analytics, and 1.49, 1.45 and 1.45 seconds on the generated project. The differences are smaller than the spread between runs on a shared machine.

## What did not run

`make scale`, which times `cairn check`, builds and incremental rebuilds up to 1,480 modules, did not run; only the check was timed, on the two programs above. No refused program was timed, and a refused program now costs about a full check, where it used to stop at its first refusal. Nothing here runs device code.
