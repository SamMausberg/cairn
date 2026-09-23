# How the tools scale with a project

Taken on 2026-09-23 on one AMD Ryzen 7 7800X3D (8 cores, 16 threads, 70 GB) under WSL2, with clang++ 21.1.8 and Python 3.12.3. Other agents were building and testing on the machine the whole time: the load average was 10 to 16 on 16 threads at the end of the runs. Every number is one cold process, measured once, so read the ratios below as large effects only; nothing here is a quiet machine's figure.

`bench/scale/generate.py` writes a project of N modules over 64 files. Each module has a record, a sum and seven functions (a loop, a fold, a host `parallel` region, two `match`es, two tasks over disjoint parts) and imports three earlier modules. `bench/scale/measure.py` times each step in a fresh process through `bench/scale/driver.py`, with peak resident memory, and `make scale` runs it. `before.json` is the compiler of main at ff79337, with the old limits lifted where it refused a size. `after.json` is this change on 673d693, under its own default limits; it lacks only ff79337's cached compiler version, which costs it one `--version` call a build.

## Before and after

Seconds for each step, before and after. A body edit changes one function of the leaf module or of `big.core`, which every module imports; an interface edit changes a public signature of `big.core`, so every unit recompiles. The editor refresh is the language server's analysis after one change to a leaf file.

| modules | lines | check | editor refresh | whole build | incremental, cold | again, no change | leaf body | core body | core interface |
|---|---|---|---|---|---|---|---|---|---|
| 185 | 9,643 | 1.1 / 1.2 | 1.4 / 1.3 | 13.1 / 11.8 | 31.9 / 11.6 | 3.2 / 1.4 | 3.6 / 2.1 | 3.6 / 2.0 | 25.7 / 11.4 |
| 370 | 19,263 | 2.1 / 1.9 | 3.4 / 2.5 | 24.0 / 23.3 | 54.2 / 20.5 | 5.3 / 2.6 | 6.2 / 3.7 | 6.4 / 3.0 | 48.9 / 22.3 |
| 740 | 38,503 | 4.6 / 4.5 | 7.1 / 5.8 | 48.8 / 43.9 | 116.4 / 40.5 | 16.6 / 5.0 | 17.0 / 6.7 | 14.2 / 5.6 | 120.1 / 38.9 |
| 1,480 | 76,983 | 10.6 / 10.8 | 16.8 / 12.2 | 90.2 / 91.7 | 264.5 / 81.7 | 27.6 / 13.4 | 27.2 / 12.7 | 24.0 / 12.9 | 283.1 / 90.2 |

Before this change, the 370-module project (19,263 lines, about 2,300 functions) and everything larger was refused: 2,048 functions and 200,000 syntax nodes were the limits. After it, all four sizes pass the defaults.

Peak memory did not change: a check took 50, 76, 129 and 236 MiB before and 49, 76, 130 and 238 MiB after, and the editor 116 to 771 MiB. The largest native compiler process took 120 to 260 MiB. An editor process holds about three times what a check does, because it keeps the last good analysis beside the new one.

## What changed, and why it helped

- An incremental build ran the front end twice, once for the whole program and once for its units. It runs once, and a rebuild with nothing to compile went from 27.6 s to 13.4 s at 1,480 modules.
- Each unit parsed the whole shared header, which is 16,000 lines at 740 modules. With 16 or more units to compile, the header is precompiled once. On 12 units of that project one unit took 0.66 s under clang without it and 0.27 s with it, after a 0.86 s header build; under GCC, 1.0 s and 0.39 s. A cold incremental build and an interface edit both went about three times faster.
- An editor refresh lexed every file of the project to find its `module` lines. Those are now kept by each file's text, so a refresh lexes only what changed.
- The CLI tells the garbage collector to look at old objects rarely. Interleaved in-process checks ran 10 to 20 percent faster with it (740 modules: 4.8 and 5.1 s against 3.9 and 4.1 s; 1,480 modules: 10.1 and 11.1 s against 9.0 and 9.8 s), at the same peak memory. The loaded runs above do not separate that from their noise.
- The language server analyses a run of changes to one file once, at its last text.
- A whole-program build did not change: the native compiler takes most of it.

## The limits

A program may now hold 16 MB of source, 32,768 functions and 3,200,000 syntax nodes, and a manifest may list 1,024 files and 64 dependencies. What code generates stays bounded as before: 1,024 copies per family, 2,048 across a program's families and 2,048 declarations per recipe. A 5,300-module project of 7.1 MB, 31,802 functions and 757,915 nodes, just under the function limit, checked in 33.0 s at 797 MiB. The source and node limits admit a program about twice that heavy, and none was measured. An editor on such a project would hold about 2.5 GB, by the ratio above, and none was measured either.

A generic instance whose type grows at each recursive call, such as `grow[Box[T]]` inside `grow[T]`, still ends with Python's recursion limit and `E-PROJECT-OR-ENVIRONMENT`, not a diagnostic of its own, both before and after this change.

## A compiled front end

mypyc, already in the development environment, compiled the lexer and the parser. On the 740-module project, lexing took 0.50 s in Python and 0.45 s compiled, and lexing plus parsing 1.5 to 1.6 s and 1.1 to 1.2 s. The rest of the compiler does not compile: mypyc requires strict optional types, and the compiler's type-check turns them off (`strict_optional = false` in pyproject.toml, since the checker fills tree fields after parsing). With them on, mypy reports 115 errors in 15 files of `src/cairn/compiler`, 39 in `codegen.py`. So no compiled build is offered. Lexing and parsing are about a fifth of a check, so compiling only them would save well under a tenth of it.

## What did not run

No measurement past 77,000 lines built natively or ran the editor. Nothing ran on a GPU. The Bazel rules were measured only by `tests/projects/test_large_projects.py`, which builds, runs and tests `examples/bazel`.
