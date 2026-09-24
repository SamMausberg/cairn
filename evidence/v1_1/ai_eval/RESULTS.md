# The 1.1 evaluation: results as it stands

The counted run of [bench/ai/PREREGISTRATION_V1_1.md](../../../bench/ai/PREREGISTRATION_V1_1.md) stopped early, at the owner's request to end the session, with 54 of its 156 subjects counted: replicate 1 in full but for the `block_scan` plugin cell, and three subjects of replicate 2's `histogram`. Everything below describes those 54. The preregistered claims are for the full run, so this record makes none of them; it reports the figures and their intervals as they stand. [RUN_NOTES.md](RUN_NOTES.md) lists every event that was not a subject's own result, and the deviations are below.

## What the 54 subjects did

Every counted subject solved its task: 13 of 13 with the plugin, 14 of 14 with the documentation, 14 of 14 in C++ and 13 of 13 in Rust, each program passing its hidden check under the sanitizers or Rust's debug checks. No cell is discordant. No final program failed a sanitizer, a guard or a Rust check, and none used `unsafe`.

| arm | subjects | USD per solved task [95%] | tokens per solved task [95%] | median turns [quartiles] |
|---|---|---|---|---|
| CAIRN, plugin | 13 | 0.57 [0.34, 0.83] | 1,337,478 [783,635, 1,987,261] | 32 [16, 34] |
| CAIRN, documentation | 14 | 0.85 [0.51, 1.19] | 2,016,668 [1,197,048, 2,860,443] | 34 [15.5, 44.5] |
| C++ | 14 | 0.15 [0.09, 0.25] | 245,348 [160,385, 372,882] | 10 [8, 13.75] |
| Rust | 13 | 0.14 [0.06, 0.28] | 219,794 [114,243, 419,818] | 7 [6, 9] |

| ratio of cost per solved task | dollars [95%] | tokens [95%] |
|---|---|---|
| plugin / documentation | 0.68 [0.49, 0.89] | 0.66 [0.47, 0.90] |
| plugin / C++ | 3.7 [2.0, 6.3] | 5.5 [2.9, 9.1] |
| plugin / Rust | 4.0 [1.8, 9.2] | 6.1 [2.6, 14.0] |
| documentation / C++ | 5.5 [3.4, 8.8] | 8.2 [5.1, 12.6] |
| documentation / Rust | 6.0 [3.0, 12.9] | 9.2 [4.7, 19.6] |
| C++ / Rust | 1.08 [0.87, 1.54] | 1.12 [0.86, 1.65] |

Intervals are the 2.5th to 97.5th percentiles of 10,000 resamples of the 14 cells, seed 20260924, as `bench/ai/analysis.py` computes them. A cell is one task in one replicate; the `block_scan` cell of replicate 1 has no plugin subject and replicate 2's `histogram` has no Rust subject. `tables_counted.md` has every subject's row, and `results_counted.json` the same data.

In these subjects the plugin arm cost about two thirds of what the documentation arm cost per solved task, and both CAIRN arms cost several times what C++ and Rust cost. These are descriptions of 54 subjects, most tasks seen once, not the preregistered result: with one replicate, three sessions at a time on a loaded machine, and eleven plugin cells run a second time after a harness failure, they can change when the run is completed.

## What differs from 1.0

The 1.0 benchmark, before the plugin existed, found CAIRN documentation subjects at 11.6 times the total tokens of C++ subjects over twenty paired cells. Here the documentation arm is at 8.2 times C++'s tokens per solved task over thirteen tasks seen mostly once, three of them new. The model, effort and limits are the same; Claude Code is 2.1.281 rather than 2.1.280, and the compiler, documentation and skill are main's at dd3f75e, after the skill was trimmed and every refusal came to name its rule card and fix. Subjects ran three at a time rather than one, from the resumption on each in its own user, mount and process namespace. These differences are why the two runs are compared only in words.

## Deviations from the preregistration

- The run stopped at 54 of 156 subjects, for the session's time, not for its cost (31.29 of the 200 dollars) or for any failure of the platform. `harness.py run --phase counted --replicate 1` continues it from the replicate 1 `block_scan` plugin cell, and replicates 2 and 3 follow, under the same toolchain in `~/cairn-aieval/toolchain`, once the PAUSE file in `results/ai_eval/` is removed.
- The harness changed three times during the run, each time between subjects and with the toolchain unchanged: isolation of each subject's files (cdaf81e), a process namespace (405377c), and a plugin copy per plugin subject (546f182). The first twelve subjects ran without the isolation, the first fifty without the process namespace.
- Eleven replicate 1 plugin subjects lost their plugin copy mid-session through the harness bug that 546f182 fixes. They are kept on file, reported apart in `tables_counted.md` and under `subjects/counted/r1/*/plugin/set_aside/`, and counted nowhere; their cells ran again with fresh subjects, which the preregistration allows only for a platform failure. They cost 7.98 dollars, which the ceiling counts. All eleven had solved their tasks, and the criterion for the rerun was the timestamps alone.
- The reruns ran after three subjects of replicate 2 had started, so not every cell of replicate 1 started before replicate 2.
- One documentation subject killed two background `find` processes of the concurrent plugin subject (`audit_decisions.json`); that plugin subject was among the eleven set aside.
- The audit gained two flags during the run. The report audits every transcript under the final rule; every flag was decided and none found a subject reading what it was not given, so no subject is contaminated.

## Limits

54 subjects of small single-file tasks, most tasks seen once, with finite hidden tests. One model family, `claude-sonnet-5`, which also wrote the language, its documentation, the plugin, the tasks, their oracles and this report; other model families are not measured. The three new tasks were chosen because CAIRN's checks bear on them. The CAIRN arms' `block_scan` ran on an emulation of the device, never on a GPU. The machine was heavily loaded, with load averages of 78 to 251 on 16 threads, so wall time is the least comparable measure; tokens, turns and cost do not depend on load. Every subject solved its task, so this run, like 1.0's, cannot tell the arms apart by tasks solved.
