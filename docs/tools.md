# Tools and targets

Every tool here ships with the compiler, needs no Python package outside the standard library, and changes nothing the compiler accepts. `cairn COMMAND --help` lists every option.

## Output for people and for programs

At a terminal, `cairn` prints for a person, as below, and `run` hands the terminal to the program. Piped, every command prints the JSON record scripts and agents read. `--format human|json` or `CAIRN_FORMAT` chooses, `NO_COLOR` turns colour off, and the exit status and the record do not change.

```text
error[E-LEASED]: data is lent to left until wait(left).
  --> src/main.cairn:5:3
  |
5 |   data[0] = 7;
  |   ^^^^
```

A misspelled name gets the nearest name in scope (`= help: did you mean total?`), and a program stopped by a failed guard is reported as stopped by `SIGABRT`.

## A watched check and shell completions

```sh
cairn check demo --watch            # check again each time a file the project reads changes; Ctrl-C ends it
source <(cairn completions bash)    # or zsh; put the script on $fpath as _cairn to keep it
```

`--watch` polls the manifest and its sources four times a second, and watches a manifest that is broken at the start until it is fixed. Piped or with `--format json`, each answer is one JSON Lines record. The completion scripts are generated from the command line's own parser, so they offer every command, option and choice and nothing else.

## cairn fmt

```sh
cairn fmt src/                 # rewrite every *.cairn under src/ in place
cairn fmt --check src/ tests/  # write nothing; exit 1 if anything would change
cairn fmt --diff src/a.cairn   # write nothing; print a unified diff
```

```text
$ cairn fmt --diff sloppy.cairn
--- sloppy.cairn
+++ sloppy.cairn (formatted)
@@ -1,10 +1,7 @@
 // Rolling checksum over a frame.
-fn checksum( n : usize , frame : ro < u8 > [ n ] ) -> u32
-{
-    let mut sum : u32 = 0 ;
+fn checksum(n:usize, frame:ro<u8>[n]) -> u32 {
+  let mut sum:u32 = 0;
 
-
-
-    for i in 0 .. n { sum = add_wrap ( sum , u32 ( frame [ i ] ) ) ; }
-    return sum ;
+  for i in 0..n { sum = add_wrap(sum, u32(frame[i])); }
+  return sum;
 }
```

The layout is two-space indentation, one statement per line, `key:Type`, and lines wrapped at 100 columns. A one-line block stays on one line if it fits, a block over several lines is never collapsed, every comment stays where it was, and runs of blank lines collapse to one.

The formatter re-lexes its output and compares tokens and comments with the input. If either differs, or the input does not lex, the file is left alone and listed under `not_formatted` with the reason, and the exit code is 1:

```json
{
  "status": "would-change",
  "mode": "check",
  "changed": ["sloppy.cairn"],
  "not_formatted": [{"file": "broken.cairn", "reason": "Unexpected character '\"'."}]
}
```

Formatting is a fixed point, and `cairn.editor.formatting.format_source(text)` is the same formatter from Python.

## cairn check --generics

`--generics` also checks each generic function once against its bounds, as [abstractions.md](abstractions.md#certifying-a-template) describes:

```json
{"status": "typed", "functions": 159, "formal_status": "not-verified",
 "generics": {"analytics.agg.run_static": "ok", "analytics.agg.bins_new": "ok",
              "analytics.query.map_par": "ok", "analytics.query.map_loop": "ok"}}
```

A template that needs more than its bounds promise is named with what it needed, and the command exits 1. `fn widest[T:affine](a:ro<T>, b:ro<T>) -> bool = less(a, b);` compares values that promised only to be affine:

```json
{"generics": {"ranking.widest": "E-TRAIT-IMPL: ?ranking.widest.T does not implement std.core.Ord."}}
```

## cairn test

```sh
cairn test demo                    # every test block and every contract of the manifest
cairn test demo --filter average   # only the tests and contracts whose name contains average
cairn test demo --test app.sums    # the one test block of exactly that name, and no contract
cairn test demo --jobs 4 --timeout 10
```

`cairn test` builds one executable holding every selected [test block](language.md#tests-and-assert) and runs each test in its own process, several at a time, under the limits `cairn run` applies, which are not a sandbox. A test passes only when its process exits 0, so an assert, a guard, a signal or a timeout fails that test alone, whatever it printed. The manifest's JSON contracts run beside the blocks, and a run that finds no test fails.

```text
tests-not-passed: 1 of 3 tests failed, 1 contract, 81 cases
  test wrong: assertion failed at src/main.cairn:13: four is not five
```

Test blocks run only as host processes, so a freestanding project's are refused (`E-TEST`); `cairn test --contract FILE` still runs a task contract on the host.

## cairn validate

```sh
cairn validate examples/implementations --symbol prefix_by4          # against the function it implements
cairn validate app --symbol total_lanes --policy policy.json         # tolerance, domain, budget, seed, probes
```

`cairn validate` tests one [implementation](abstractions.md#implementations) against its reference on inputs generated from what the compiler already knows: the signature, which `usize` parameters are extents, the `when`, the literal tiles the body indexes by (`4 * k` gives 4), the implementation's plan items and, for host lanes, the pool's cutoff. Extents come at each tile minus one, the tile, plus one, a partial second tile, zero, one and the domain's largest; views come zeroed, all ones, ascending, at their type's largest value, alternating, random, and once one element off an aligned allocation; scalars come at their type's edges. The same seed gives the same cases.

Each case runs the reference, the reference under `plan f use g;` (the dispatch, on every input) and, where the condition holds, the implementation, each call in a process of its own. Two traps agree, and results agree bit for bit, or within the policy's tolerance for floats. A pass is [finite testing](verification.md#validating-an-implementation) on the cases that ran, never proof, and an implementation no case ran is `unknown`.

A failing case is shrunk while it still fails (smaller extents with each view cut to its prefix, aligned views, then each value toward zero), reported with what each side did, and kept in `regressions/<reference>.json`, written the same on every run. Listed under the manifest's `tests`, that file makes `cairn test` replay every kept case against every implementation of the reference.

```text
failed: prefix_blocks against prefix, 17 cases (5 ran it), finite-tested
  fails at n = 16, out = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], xs = [0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]; kept in regressions/prefix.json
  smt: unknown where ((((n % 8) == 0)) && (n >= 0 && n <= 4096)) && n <= 16
```

Apart from the finite result, `smt` asks Z3, as [`cairn verify`](verification.md#value-level-source-equivalence) would, whether the two bodies agree where the condition holds. A loop over an extent the domain lets past 16 is decided only up to 16, and the record says so; anything outside the modeled fragment, such as a `scan` or a lane region, is `unknown`.

The policy sets `tolerance` (`absolute` and `relative`, 0 by default), `domain` (`largest_extent`, and `extents` or `values` ranges by parameter), `budget` cases, `seed`, shrinking `probes` and `seconds` per call; without `--policy`, the one the regressions file pinned applies. `--history DIR` keeps the result in that [candidate history](agents.md#candidate-history), a `validation` record or a `failure` record with the shrunk input, under the implementation's identity and the policy's digests. A signature the validator cannot feed (records, owners, views of records) is `unknown`, and so is device code, which only `make gpu` runs, under each Compute Sanitizer tool (`memcheck`, `racecheck`, `initcheck`, `synccheck`) as a result of its own.

## cairn doc and cairn expand

`cairn doc [path]` prints a Markdown reference of the checked program's public types, recipes and functions, with bounds, comments and inferred effect rows. `cairn doc --std` documents the packaged library, and `make docs` writes it as [std_api.md](std_api.md) and one page per module, which the suite holds to the compiler's answer.

    $ cairn doc examples/hello
    # root module

    ```cairn
    // source: src/math.cairn Floor average without overflowing the intermediate sum.
    fn average(x:u64, y:u64) -> u64  // effects: trap

    // source: src/main.cairn
    fn main() -> i32  // effects: trap
    ```

`cairn expand [path]` prints the source the program's `derive` statements generated, `$name` spliced and static values folded: exactly what the checker sees, and where to read the instance a diagnostic inside a recipe is about. An edit belongs in the recipe, so `cairn inspect --symbol` refuses a generated entry (`E-SYMBOL`).

```text
$ cairn expand examples/apps/kvstore
fn encode_Header(out:rw<u8>[16]@host, value:Header) {
  out[0] = u8((shr(value.check, 0) & 255));
  out[1] = u8((shr(value.check, 8) & 255));
  ...
```

## cairn explain

`cairn explain [path] [--symbol f]` shows where each function pays at run time, at the `.cairn` line of each cost: the guards the C++ still checks, the owners it allocates, the calls that allocate, spawn, join, lock or do I/O, the points where it waits, and clang's verdict on every loop. It reads the emitted C++ and clang's optimization record, and runs nothing.

```text
$ cairn explain examples/apps/analytics --symbol analytics.query.above_loop
"guards": {
  "sites":      {"bounds": 3, "overflow": 1, "view_entry": 2},
  "emitted":    {"bounds": 1, "disjointness": 1, "overflow": 1, "view_entry": 2},
  "discharged": {"bounds": 2},
  "by_line":    {"src/query.cairn:14": {"view_entry": 2, "disjointness": 1}, "src/query.cairn:18": {"bounds": 1}, ...},
  "discharged_by_line": {"src/query.cairn:17": {"bounds": 1}, "src/query.cairn:18": {"bounds": 1}}
},
"loops": [{"at": "src/query.cairn:16:5", "verdict": "not vectorized",
           "reasons": ["Cannot vectorize early exit loop", ...]}, ...]
```

`sites` counts what needed a guard, `discharged` what the checker proved cannot fail, and `emitted` what the lowering wrote, which can exceed the difference because a part's base is written once for its data and once for its size. Here `out[used]` keeps its guard, since nothing bounds `used` by `out`'s extent, and clang names that guard's early exit as why the loop stays scalar. `--keep-guards` on `emit`, `build` and `run` writes every guard. Loop verdicts come from clang only; under g++ or with device code the report says why it has none.

An agent gets the same report from `cairn inspect --symbol f --explain`, or by sending `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "explain"}` after an edit.

## cairn predict

`cairn predict [path] [--symbol f] [--at n=1e6] [--against BEFORE]` says how long each function will take without building it, so an agent can price a change before compiling and timing it. The answer is a prediction, never a measurement, with a confidence.

```text
$ cairn predict bench/suite/kernels/saxpy_f32/kernel.cairn --arch x86-64-v4
predicted, not measured: AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64-v4
saxpy_f32  0.0807 ns*n (memory (l1), up to n=2.05e+03); 0.124 ns*n (memory (l2), ...); 5.66 us + 0.0648 ns*n (pool start, ...); ...; 0.342 ns*n (memory (dram), up to n=1.07e+09)
  n=1000          80.6 ns  memory (l1)          37% of speed of light    high
  n=100000        13.9 us  pool start           22% of speed of light    medium
  n=1e+07          922 us  memory (dram)        99% of speed of light    medium
```

The checker supplies the counts: trip counts are polynomials in the extents, the lane rule makes every write a stream, and the effect row names each allocation, transfer, task and wait. A machine profile prices them by bandwidth per cache level and the cost of each operation and of starting the lane pool, a task and an allocation. `bound` names what limits each part, `speed of light` is the same work at that bound's peak, and `--format json` shows the formula term by term.

`--against BEFORE` prints the ratio between two versions, here a loop over `f32` views made `parallel i in n`:

```text
$ cairn predict after.cairn --against before.cairn --arch x86-64-v4 --at n=1000 --at n=1e7
predicted, not measured: AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64-v4
f
  n=1000          54.8 ns -> 54.8 ns    x1.0  memory (l1), high
  n=1e+07          997 us -> 300 us     x0.301  memory (l3), medium
```

Confidence is `high` when every count is a size and every access a stream, `medium` when something is approximated (a wide host region, a loop bounded by `min()`, an atomic), and `low` when a number is a guess (a `while` loop, an address from data, a foreign call, recursion). On timings it was not fitted to, the packaged profile came within a quarter at one lane and at a hundred million elements, and predicted wide regions of a hundred thousand to ten million elements badly, hence `medium` ([evidence/v1_4/perf_model](../evidence/v1_4/perf_model/README.md)). `python -m cairn.perf.calibrate --out PROFILE.json` measures another host, and `--profile` or `CAIRN_PROFILE` selects it.

## cairn tune

`cairn tune [path] --symbol f --at n=1e7` chooses `f`'s [plan](concurrency.md#plans) by a bounded search. A plan changes no result, so every candidate the checker accepts is correct and the search only asks which is fastest. A function is named with its module, as in `--symbol lib.spread`. `--write` puts the chosen plan after the function's declaration and removes any plan that named it elsewhere, only if the whole project still checks.

The space is every combination of the items `f`'s regions take: `grain` and `lanes` for host regions; `block`, `per_lane`, `unroll`, `vector` and `stage` (at the radius the staging rule reads) for device regions; `fuse` where two regions could join. The checker, not the search, decides what is legal: each candidate is written into the source and the whole program checked, so `vector` beside `fuse`, or `stage` beside `vector`, is refused with `E-PLAN` and counted under `space.refused` with one example. `cairn predict` prices every legal candidate.

When `f` has [implementations](abstractions.md#implementations), each is a candidate beside the reference, with every plan of the reference's regions: its row's `plan` reads `plan f use g;` and it is priced as `g`, the code that runs where the condition holds. A function with implementations and no region is searched over them alone. Because selecting an implementation could change a result, one is chosen or timed only while the history holds a validation of it as it is now, the record an [implementation session](agents.md#implementation-sessions) keeps; its row says `validated` with the evidence class, or that none holds. Editing the implementation makes its validation stale, and without a history no implementation is chosen. `--write` writes the chosen selection beside the plan, or removes it when the reference was chosen.

Device candidates are then compiled for the [device target](#the-device-target) in predicted order; among candidates priced alike, one whose kernel items (`unroll`, `vector`, `stage`, `fuse`) no earlier compile covered goes first. Nothing runs: ptxas and cuobjdump report registers, spilled bytes, stack, static shared memory and instructions, and a staged tile's shared memory is computed from the plan. Registers and shared memory enter the price through occupancy, and `chosen` is the best-ranked candidate a compile read. Each compile is kept by the digest of what it read (the emitted program, the runtime headers, the target, the toolkit and the inspector), so a program already compiled costs nothing, and a kept reading for another target is refused (`E-TARGET-MISMATCH`). `resources.sass` digests the SASS, so candidates with the same device code show one digest, as `blur` with and without `block 128` does. Without a device target nothing is compiled, and the answer says so.

```text
$ cairn tune blur.cairn --symbol blur --at n=1e7 --budget-compiles 4
"space":  {"configurations": 240, "checked": 240, "legal": 160,
           "refused": [{"code": "E-PLAN", "message": "stage loads one region's tiles; fuse and vector reshape the region, and a plan takes one.", "configurations": 80, "example": {"vector": 2, "stage": 1}}]}
"candidates": [{"plan": "plan blur { stage 1; }", "stage": 1, "predicted_ns": 113000.0,
                "resources": {"registers": 24, "dynamic_shared_bytes": 1040, "instructions": 88, ...},
                "applies_to": {"stage": {"blur@9e54491e": {"radius": 1, "arrays": ["x"]}}}}, ...]
"budget": {"compiles": {"allowed": 4, "started": 4, "kept": 0}, "seconds": {"allowed": 300.0, "spent": 18.29},
           "runs": {"allowed": null, "started": 0, "kept": 0}, "undone": {"not inspected: compile budget spent": 156}}
```

`blur@9e54491e` names the region by a digest of its syntax, which survives an edit elsewhere, a comment or a reformat; `applies_to` says which regions a plan item changed and which arrays it chunked or tiled. The budgets are `--budget-compiles` (4 by default), `--budget-seconds` for the whole search (300) and `--budget-runs`. What a spent budget left undone is counted under `undone`, and a candidate no compile read has no `resources`.

`--measure K` then times the best-ranked few and the current plan on this host, halving the field each round with more blocks for the survivors, and reports how many pairs ran in the predicted order; on a busy machine two close plans are within noise. Device plans are timed only by the owner's targets: `make tune-device FILE=f.cairn SYMBOL=f AT=n=1e8`, which holds the device lock, rests after each run and stops after 64, and `make calibrate-device`, which replaces the device profile's assumed figures with measured ones.

`--compare A --compare B` reports how plan `B` differs from plan `A` instead of searching. A plan is written as its items, `grain 1; lanes 8`, or as `none`, and `use g` adds the selection of the implementation `g`, which is then priced and compiled as `g`; a side without `use` is the reference. Each line is labelled a compiler observation (ptxas, cuobjdump or the model, nothing run), a runtime measurement, a profiler observation, a hypothesis or a suggested experiment.

```text
$ cairn tune blur.cairn --symbol blur --at n=1e7 --compare none --compare "stage 1; block 128"
blur: (no plan for blur)  ->  plan blur { block 128; stage 1; }
  [compiler observation] cairn predict (the model, not a run): at n=1e+07: predicted 1.13e+05 ns -> 1.13e+05 ns; the model's bound device memory -> device memory; confidence low
  [compiler observation] ptxas and cuobjdump: registers per thread: 12 -> 24
  [compiler observation] the plan: staged tile bytes per block, computed from the plan: 0 -> 528
  [compiler observation] ptxas and cuobjdump: SASS instructions in the code: 48 -> 88
  [compiler observation] cuobjdump: global load instructions in the code: 3 -> 1
  [compiler observation] cuobjdump: global store instructions in the code: 1 -> 2
  [compiler observation] cuobjdump: shared load instructions in the code: 0 -> 4
  [compiler observation] cuobjdump: shared store instructions in the code: 0 -> 1
  [hypothesis] derived from the SASS counts: b reads through shared memory (global load instructions in the code: 1 in b, 3 in a); b may move fewer bytes from device memory, unless the caches already served the neighbours' repeated reads
  [suggested experiment] suggested, not run: time a and b at the same sizes, interleaved: make tune-device FILE=... SYMBOL=blur AT=...  (the owner's target; nothing here runs the device)
  [suggested experiment] suggested, not run: profile a and b in an explicit profiling run (Nsight Compute's occupancy and memory sections), apart from timing, which only the owner runs
```

A register, spill or instruction count (in the code, not executed) is never given as the reason one plan is slower: at most it leads to a hypothesis, beside the experiment that would test it, and both go into the history. When a and b compile to the same SASS, the report says so and attributes a measured difference only to the launch or noise. A measurement or a profile comes only from the history, while it holds for this function, contract, compiler and target, with the procedure or profiling run behind it. Nothing here profiles; profiling stays apart from timing because a profiler replays kernels. A measured order the model did not predict is reported as such, and `--artifacts` adds the path of every file behind the lines: the emitted program, the cubin, ptxas's log, the SASS and the record ids.

The search records what it tried, what the checker or nvcc refused, what each compile read and each run with its procedure into the [candidate history](agents.md#candidate-history), `.cairn/history` beside the manifest unless `--history DIR` or `--no-history` says otherwise. A later search reuses what still holds: a kept compile is not repeated, nor a kept measurement of the same candidate, sizes and procedure. `--since TUNE.json` prints only the rows that changed since a saved answer, and counts the rest.

## cairn diff

`cairn diff OLD NEW` says what changed between two versions of a program, function by function, and what establishes each answer. A side is a path or a git revision (`--in PATH` names the project inside the repository). A revision is read into a scratch directory, never checked out over the working tree. [demos/repair](../demos/repair/README.md) shows it reviewing an agent's fix.

```text
$ cairn diff before.cairn after.cairn
before.cairn -> after.cairn: 1 identical-code, 1 smt-equivalent, 1 behavior-changed
  behavior-changed  scale  at x = 63: before returns 126, after returns 189 (native: clang++ and g++ agree)
  smt-equivalent    clamp  Z3 found no input on which they differ
predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): clamp x0.471, scale x0.889
semver: major: scale behaves differently at x = 63
```

| Class | Meaning |
| --- | --- |
| `identical-code` | Its emitted C++, and that of everything it calls, is the same up to renaming, so the same native code runs. Renamed parameters and the short forms land here. |
| `smt-equivalent` | Z3 found no admitted input on which the two differ in result, in what they leave in an `rw` borrow, or in whether they abort, within the [modeled fragment](verification.md#value-level-source-equivalence). |
| `behavior-changed` | It comes with a witness input, what each version does on it, and the same run natively under each compiler present. A native run that disagrees with the model makes the answer `unknown`. |
| `identical-source` | A generic nothing instantiates has no code, so its tokens are compared; otherwise it is `unknown`. |
| `signature-changed`, `added`, `removed`, `renamed` | What the names say. A changed signature is not compared value for value. |
| `unknown` | It gives its reason, and is never counted as unchanged. |

Beside the class, each function lists how its signature, effect row, guards, allocations, tasks and `unsafe` blocks changed. The semantic version is `major` for a removed, renamed or re-signed public function, a public function that gained an effect or has a witness, or a changed public type; `minor` for an added one; `patch` otherwise. An `unknown` public function makes the level `unknown`, with what is proven under `at_least`, unless it is already `major`.

`--require equivalent` exits 1 unless every function is identical or `smt-equivalent`, and `--require identical` refuses `smt-equivalent` too. `--markdown FILE` writes a section for a pull request. The solver has `--timeout-ms` per query and `--budget-s` for the whole diff (60 s by default), and each function runs in a process stopped at its limit, because Z3 cannot be interrupted while it reads a large query. Both versions are lowered by this compiler, so the diff compares two sources, never two compilers. `evidence/v1_4/diff/` records a diff of the library and every example project across two revisions.

## cairn export

`cairn export PATH --out DIR` writes the program `cairn build` would compile into a new directory: the generated C++ (`program.cu` for a device program), exactly the runtime headers it includes, the C header with `--kind library --header`, and `export.json`. That record holds the command line, the device target, each compiler's path and version, a sha256 per file, each function's canonical emission, and one identity over all of it.

```sh
cairn export examples/systems --out out/systems     # the program and its record
cairn export out/systems                            # {"status": "export-intact", "identity": "596d...", "files": 2}
cairn build out/systems                             # the recorded command, in a fresh copy under out/systems/build/
cairn run out/systems
```

`build`, `run` and `test` take the export directory itself. Each refuses a file that fails its hash, was added or was removed, and a record whose identity no longer covers it (`E-EXPORT-TAMPERED`), then builds with exactly the recorded command and compilers (`E-EXPORT-TOOLCHAIN` when a compiler here is another version). Every record they write carries the export's identity, so a later rewrite shows. `--tests` exports the test blocks' program, which `cairn test DIR` runs one process per test, and `--time f --at n=1e6` exports `f` beside the timing driver of `cairn tune --measure`, so `cairn run DIR` measures exactly the exported code. A device export builds here and runs only under the owner's make targets.

The record names each runtime header's role in a device export: the device implementation (`cairn_kernels.hpp` and the guards a lane calls, whose kernels launch on a stream the caller names, with no execution context) or the launch wrappers (`cairn_gpu.hpp`, `cairn_exec.hpp`, `cairn_reuse.hpp`), which an application can point at its own stream (`NAME_device_stream`) or replace with another machine, as the suite's host machine does.

`cairn export DIR --compare OTHER` says whether two exports are the same code (each function's canonical emission, as `cairn diff` compares it, each runtime header, the command, the compilers and the target), and exits 1 when they differ, which holds a change to a known-fast implementation. Same code is not the same speed: compare timings only between exports built alike, with the same `--time` harness, on the same machine.

## cairn build --incremental

`--incremental` compiles one object per module against a shared interface header and caches it under `build/objects/`, keyed by a hash of the unit, the header, the command line, the runtime headers and the compiler version. It is opt-in because separate objects give up inlining across modules, and device programs and freestanding images are always one unit.

```text
cairn build --incremental examples/apps/analytics    1.34 s   15 compiled   cold cache
cairn build --incremental examples/apps/analytics    0.14 s    0 compiled   nothing changed
cairn build examples/apps/analytics                  1.17 s             whole program, one unit
```

A body-only edit recompiles one module (0.87 s for one statement added to `analytics.query.above_loop`), and a signature or layout change recompiles all of them. Each object is published by a rename with its sha256 beside it and hashed again before reuse, which catches an interrupted, corrupted or shared cache but not someone who can write into `build/`. The cache is always safe to delete.

## cairn build --header

`cairn build --kind library --header` writes `NAME.h` beside `libNAME.so`, so a C or C++ program can call the library with nothing from CAIRN in its own build:

```sh
cairn build examples/interop --header       # libstats.so and stats.h under examples/interop/build/stats-*/
c++ -std=c++17 examples/interop/host/main.cpp -I"$DIR" -L"$DIR" -lstats -Wl,-rpath,"$DIR"
```

The header declares the checked entry `cf_NAME` of every function whose types can cross: scalars, copyable records, tag-only enums, copyable sums, and borrows of them. A view `xs:ro<i64>[n]` becomes `const int64_t *xs` with its length in `n`, a record a `ct_` struct with CAIRN's field names, and each declaration carries its comment, signature and effect row:

```c
/*
 * One pass over the samples. The total is checked: a sum past i64 aborts rather than wrap.
 * CAIRN: summarize(n:usize, xs:ro<i64>[n]) -> Summary
 * xs: n elements, read.
 * Effects: ffi_precondition, read:xs, trap.
 */
ct_Summary cf_summarize(size_t n, const int64_t *xs);
```

The entry checks what a foreign caller could get wrong, and aborts as a failed guard does: a view must be null only when empty, aligned and inside the address space, and a written view must overlap no other. No entry can check that a single borrow points to live storage. Calls between CAIRN functions reach the lean body `ci_` directly.

Every layout is asserted in both the header and the library, so a mismatch fails to build instead of corrupting a call, and the header names an interface hash only the matching library defines, so a program built against another version fails to link. The header ends with what cannot cross and why: owners such as `Buf`, linear values, function values, `dyn` references, arrays by value, trait members and device kernels. Private functions, templates, tests, externs, `main` and vendored dependencies are left out. `--header` needs a hosted library built as one unit, so `--kind exe`, a freestanding target and `--incremental` refuse it.

`cairn emit --ctypes` prints a Python module that opens the same library, asserts the same layouts on import, and leaves out, with the reason, what ctypes cannot pass exactly (a record with `align(n)`, and a packed record or a float-holding sum by value).

```python
import ctypes

stats = ...  # the module cairn emit examples/interop --ctypes printed
lib = stats.load("examples/interop/build/stats-XXXX/libstats.so")
samples = (ctypes.c_int64 * 6)(4, 8, 15, 16, 23, 42)
assert lib.cf_summarize(6, samples).max == 42
```

`tests/projects/test_interop.py` builds the example under both compilers, runs it under AddressSanitizer and UndefinedBehaviorSanitizer, requires overlapping, misaligned and null views to abort, and compiles headers of nested, packed, aligned and storage-float records as C11 and C++17.

A library that runs device work also declares `void NAME_device_stream(void *stream)`, which puts the calling thread's device work on a `cudaStream_t` the caller owns ([concurrency.md](concurrency.md#device-execution)).

## A manifest is named by its path

`check`, `build`, `run`, `test`, `doc` and `expand` take a project directory, a single `.cairn` file, or a manifest with any name, so one directory can hold several configurations:

```sh
cairn run examples/apps/analytics            # cairn.toml, the host engine
cairn run examples/apps/analytics/gpu.toml   # the same sources plus the device entry
```

## Large projects and Bazel

A program may hold 16 MB of source, 32,768 functions and 3,200,000 syntax nodes, and a manifest may list 1,024 files and 64 dependencies. Past a limit the compiler refuses the program with `E-EXPANSION-LIMIT` or `E-SOURCE-LIMIT` and names what it counted. What code generates stays small: 1,024 copies per family, 2,048 across a program's families, 2,048 declarations per recipe.

Effect rows are a fixed point over the call graph, so a check, an editor refresh and an incremental build each run the front end over every module, in time about linear in the program's size. On a generated project of 77,000 lines a check took 11 s, an editor refresh 12 s, and an incremental rebuild after a body edit 13 s; one of 31,802 functions checked in 33 s at 800 MiB (`evidence/v0_9/scale`, on a loaded machine; `make scale` measures it again). An incremental build of 16 or more units precompiles the shared header, which made cold and interface-edit rebuilds about three times faster.

`cairn graph` prints the module graph a build system or CI job plans with: each file's modules, each module's imports, exports and dependents, a topological order and a source hash. `--interfaces` checks the program and adds each module's interface hash, which changes only when a public signature or effect row does. Every build writes `compile_commands.json` beside the C++ it generated, for clangd and other C++ tools.

```sh
cairn graph examples/apps/analytics --format human
cairn graph examples/apps/analytics --interfaces --format json
```

`bazel/` is a Bazel module, `rules_cairn`. A library is checked on its own as a validation action, so `bazel build` refuses what `cairn check` refuses, and a binary or test builds its sources and its dependencies' as one program:

```starlark
load("@rules_cairn//:defs.bzl", "cairn_binary", "cairn_library", "cairn_test")

cairn_library(name = "geometry", srcs = ["geometry/geometry.cairn"])
cairn_library(name = "pricing", srcs = ["pricing/pricing.cairn"], deps = [":geometry"])
cairn_binary(name = "shop", srcs = ["shop.cairn"], deps = [":pricing"])
cairn_test(name = "pricing_test", size = "small", srcs = ["pricing/pricing_test.cairn"], deps = [":pricing"])
```

`examples/bazel` is that workspace, where `bazel build //...`, `bazel run //:shop` and `bazel test //...` work with nothing fetched. Its `MODULE.bazel` names this checkout with `cairn.local(path = "../..")`; without it the rules run the `cairn` on `PATH`. The rules use the host's Python and C++ compiler, not a hermetic toolchain. Each action copies its sources into a fresh directory with a manifest, because a project refuses a source that is a symbolic link, which is how Bazel lays out inputs.

## cairn lsp

```sh
cairn lsp      # speaks JSON-RPC with Content-Length framing on stdin/stdout
```

| Request | What it answers |
| --- | --- |
| diagnostics | the compiler's diagnostic, its repair hint and the exact token range |
| hover | the type of the smallest checked expression, the expected type, and a function's signature, effect row and comment |
| documentSymbol, workspace/symbol | the declarations of a document, or of the open projects |
| definition | the name's declaration, in the project or the packaged `std` |
| completion, signatureHelp | fields, functions, variants, module members, locals, keywords; the call being written and its current parameter |
| references, documentHighlight, rename | every place that names the declaration or local, and a project-wide rename checked before it is offered |
| formatting | one whole-document edit from `cairn fmt` |
| semanticTokens | every name by what it is, including effects and mutable bindings |
| inlayHint | each function's effect row, the extents a call leaves out, and the type of an unannotated `let` |
| codeLens | Run above `main` and Run test above each `test` block |
| codeAction | quick fixes for a non-exhaustive `match` (`E-MATCH-COVERAGE`), an assigned `let` (`E-IMMUTABLE`) and a missing `import` (`E-CALLEE`, `E-TYPE`) |

```json
{"id": 2, "result": {"contents": {"kind": "markdown",
  "value": "\n```cairn\nfn average(x:u64, y:u64) -> u64\n```\n\nEffects: `trap`."},
  "range": {"start": {"line": 1, "character": 3}, "end": {"line": 1, "character": 10}}}}
{"id": 3, "result": {"signatures": [{"label": "fn average(x:u64, y:u64) -> u64",
  "parameters": [{"label": "x:u64"}, {"label": "y:u64"}]}],
  "activeSignature": 0, "activeParameter": 1}}
```

A buffer being typed rarely compiles, so each feature takes its context from the current tokens and its meaning from the last analysis that compiled: after `let y = p.` the fields of `p` are still offered. A document a `cairn.toml` lists is analysed with its whole project, so names from sibling files resolve and a refusal in one file shows on every open file of the project.

A rename is one checked transaction: the edited project must compile again with every function's effect row, callees, guard sites and allocations as they were under the new name, so a rename that misses an occurrence or catches one too many is refused. Fields and variants are renamed from the types the checker gave each expression. Library declarations, foreign functions, `main` and trait members are refused with the reason. Outside a project, references and rename cover only what one document can prove, and answer nothing where that is not enough.

A compiler failure becomes a diagnostic, never an exception. Known limits: diagnostics stop at the first compiler error, as the compiler does; `definition` picks the first declaration with a matching name; there is no format-on-type; and no quick fix ever widens an effect ceiling, a borrow mode or a signature.

## The editor extension

`editors/vscode/` is a VS Code and Cursor extension: a generated TextMate grammar, snippets, the semantic token legend and a client that starts `cairn lsp`, with no build step and no vendored `node_modules`. From a checkout, symlink it and reload the window:

```sh
ln -s "$PWD/editors/vscode" ~/.cursor/extensions/cairn-language.cairn   # Cursor
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-language.cairn   # VS Code
```

Highlighting works at once. The language server needs `npm install --omit=dev` inside `editors/vscode/` once, the only step that touches the network. To install it as a package instead:

```sh
cd editors/vscode
npm install
npx --yes @vscode/vsce package          # writes cairn-<version>.vsix
code --install-extension cairn-*.vsix
```

When `cairn` is not on `PATH`, point the client at the checkout's entry script:

```json
{
  "cairn.server.command": "/path/to/cairn/bin/cairn",
  "cairn.server.arguments": ["lsp"]
}
```

`make editors` generates the grammar and the Vim files from the compiler's vocabulary (`src/cairn/editor/grammar.py`), with standard TextMate scope names every theme colours. `tests/tooling/test_grammar.py` fails when a committed grammar differs from a fresh run or misses a word the parser knows, and `tests/tooling/test_extension.py` compiles every snippet.

## Vim, Neovim and GitHub

`editors/vim/` is a runtime directory with file type detection, syntax from the same vocabulary, and two-space indentation. Add it to the runtime path, and in Neovim start the built-in client on `cairn lsp`:

```vim
set runtimepath^=/path/to/cairn/editors/vim
```

```lua
vim.api.nvim_create_autocmd("FileType", { pattern = "cairn", callback = function()
  vim.lsp.start({ name = "cairn", cmd = { "cairn", "lsp" }, root_dir = vim.fs.root(0, { "cairn.toml", ".git" }) })
end })
```

GitHub has no CAIRN grammar, so `.gitattributes` has it highlight `.cairn` files as Rust without counting them as Rust in the language bar.

## The device target

A program that indexes `@device` views is built for one device target, spelled as nvcc spells it and apart from the CPU architecture `--arch` names. `sm_120` runs on compute capability 12.0 and every later 12.x device, `sm_120f` adds the features the family shares and runs on its devices from 12.0, and `sm_120a` adds every feature of exactly 12.0 and runs only there.

`--device-target` on `build`, `run`, `predict` and `tune` names the target, else `[build] device_target`, else the one GPU `nvidia-smi` reports, which asks the driver and launches nothing. With none of these a device build is refused with `E-TARGET`; nothing defaults to `-arch=native`.

```toml
[build]
kind = "exe"
device_target = "sm_120a"
```

The target is resolved once and every stage receives the same one: nvcc's `-arch`, the kernel reader behind `cairn tune`, the device card `cairn predict` prices on, a device timing and a measured device profile. The build receipt records it under `device_target`: its name, how it was resolved, the features it provides and those the program needs, its resource limits and the nvcc release.

| Refused | Code |
|---|---|
| a spelling other than `sm_` and a compute capability with an optional `f` or `a`, `a` below sm_90 or `f` below sm_100, and GPUs of two capabilities with no target named | `E-TARGET` |
| a target the installed nvcc does not compile | `E-TARGET-TOOLKIT` |
| a program needing a feature the target lacks: `bf16` on sm_75, `mma_f8f6f4` on plain sm_120, `tcgen05` on any sm_120 | `E-TARGET-FEATURE` |
| a result recorded for another target: a ptxas report, a timing, a measured device card, or a card for a device the target's code does not run on | `E-TARGET-MISMATCH` |

The features are `FEATURES` in `src/cairn/projects/target.py`. `tests/tooling/test_target.py` assembles one probe instruction per feature for eleven targets and holds the table to what ptxas accepts. The limits (registers per thread, shared memory per block and per SM, threads per block, warps per SM) are the CUDA programming guide's for 7.5 through 12.0, a specification rather than a measurement; a target without a row has unknown limits, and its record says so.

## The freestanding target

A freestanding build produces one ELF image that runs on bare hardware, with no operating system, C library, C++ runtime, loader or unwinder. `[build] target` selects it, and `--target` on `build` and `run` overrides it.

```toml
[project]
name = "embedded"
sources = ["src/uart.cairn", "src/parse.cairn", "src/main.cairn"]

[build]
kind = "exe"
target = "aarch64-virt"
```

```sh
cairn build examples/embedded   # the ELF image plus a receipt, in a fresh directory
cairn run   examples/embedded   # the same image, under the target's emulator
```

`cairn run` reports the UART output as `stdout` and the emulator's exit status as `exit_code`. `examples/embedded/trap/` reads one element past a four-element array:

```json
{"status": "program-exited", "exit_code": 134,
 "stdout": "trap demo: reading window[4] of 4\n",
 "emulator": ["/usr/bin/qemu-system-aarch64", "-M", "virt", "-cpu", "cortex-a72",
              "-nographic", "-semihosting", "-kernel", ".../trapdemo.elf"]}
```

The language works as it does hosted, guards, `stack` storage, records, sums, generics, traits, closures, `compact`, `reduce`, `derive wire`, `f32` and `f64` included. The build refuses, by name and before a compiler runs, any function whose effect row needs a hosted runtime: `alloc`, `free`, `io`, `gpu_*`, `transfer:*`, `par:*` or `ffi:*`. `mmio_read`, `mmio_write` and `asm` stay available inside `unsafe`.

```json
{"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT",
 "message": "A freestanding target has no hosted runtime: main has effect 'alloc'."}
```

No symbol is undefined: the target's `start.S` defines `memset`, `memcpy` and the exit path. `main`'s return value is the emulator's exit status, and a failed guard exits with 134, as a hosted `std::abort` does.

```text
$ nm -u examples/embedded/build/embedded-*/embedded.elf     # nothing is undefined
$ size examples/embedded/build/embedded-*/embedded.elf
   text	   data	    bss	    dec	    hex	filename
   5411	      0	      0	   5411	   1523	embedded.elf
```

What the profile does not give: an allocator (storage is `stack` arrays, statics and string views), concurrency, a device, an MMU, caches, interrupts, a timer, a vector table (a hardware exception hangs the machine), unwinding (`defer` and owner release do not run on a trap, as with a hosted abort), or cross compilation (a target is refused on another host family). The image runs with the MMU off, so every access is Device-nGnRnE memory, the build passes `-mstrict-align`, and its timings are not comparable to hosted ones. The backend is not verified.

### The aarch64-virt target

QEMU's `virt` machine with a Cortex-A72.

| Address | What |
| --- | --- |
| `0x0900_0000` | PL011 UART0 data register; `0x0900_0018` is the flag register, bit 5 = TX FIFO full |
| `0x4000_0000` | DRAM base, 128 MiB by default |
| `0x4008_0000` | image load address: `.text`, `.rodata`, `.data`, `.bss` |
| `0x4100_0000` | stack top, growing down; 16 MiB clear of the image and of the device tree QEMU writes just past it |

`src/cairn/targets/aarch64-virt/start.S` sets `sp`, zeroes `.bss`, enables FP and SIMD, calls `cf_main` and exits through ARM semihosting `SYS_EXIT`, which QEMU turns into its own exit status. PSCI `SYSTEM_OFF` would always exit 0, so a trap could not be told from success. The build's full command line is in its receipt. `cairn run` runs exactly this:

```sh
qemu-system-aarch64 -M virt -cpu cortex-a72 -nographic -semihosting -kernel <image>.elf
echo $?      # fn main()'s return value, or 134 for a failed guard
```

`evidence/v1_0/embedded/` holds a captured transcript, `size`, `nm` and the tool versions.

### Adding a target

1. Create `src/cairn/targets/<name>/` with `link.ld` and `start.S`. The start-up code owns the stack, `.bss`, the call to `extern "C" cf_main()`, `memset`, `memcpy`, and `extern "C" [[noreturn]] void cr_exit(int)`, which must carry the status out so a trap (134) is distinguishable from any value `main` returns.
2. Add a row to `TARGETS` in `src/cairn/projects/toolchain.py` with the host `family`, the `-march` `arch`, extra `flags`, and the `run` command.
3. Add the files to `package-data` in `pyproject.toml` if the glob misses them, and extend `tests/projects/test_freestanding.py`.

Nothing else in the compiler knows about targets.
