# Tools and targets

Every tool here ships with the compiler and needs no Python package outside the standard library. None of them changes what the compiler accepts: `fmt` and `lsp` are layout and presentation, `doc` and `expand` print what the checker already saw, and `--incremental` changes only how the same program reaches the linker. The sessions below were run in this checkout on `examples/hello` and `examples/apps/analytics`.

## Output for people and for programs

At a terminal, `cairn` prints for a person. A refusal names its code and position and underlines the token. `check`, `build`, `test` and `new` answer in one line. `run` hands the terminal to the program, so its output streams and it can read standard input. Piped, every command prints the JSON record that scripts, tests and agents read. `--format human` or `--format json` chooses explicitly, `CAIRN_FORMAT` sets the default, and `NO_COLOR` turns colour off.

```text
error[E-LEASED]: data is lent to left until wait(left).
  --> src/main.cairn:5:3
  |
5 |   data[0] = 7;
  |   ^^^^
```

A misspelled name gets the nearest name in scope (`= help: did you mean total?`), and a program stopped by a failed guard is reported as stopped by `SIGABRT`. The rendering changes nothing else: the exit status and the JSON record are the same either way.

## A watched check and shell completions

```sh
cairn check demo --watch            # check again each time a file the project reads changes; Ctrl-C ends it
source <(cairn completions bash)    # or zsh; put the script on $fpath as _cairn to keep it
```

`--watch` runs the same check, then runs it again whenever the manifest or a source it lists changes size or modification time. It polls four times a second and needs nothing installed. Each answer is printed under a line with the time. With `--format json`, or piped, each answer is its whole JSON record on one line (JSON Lines) with no time line, so an editor or an agent reads one line per check. A manifest that is broken when the watch starts is watched until it is fixed, and the interrupt that ends a watch exits 0.

`cairn completions bash` and `cairn completions zsh` print a completion script generated from the command line's own parser. It offers every command, option and choice (`--format`, `--template`, `--arch`) and nothing else. The suite checks that each script names everything the parser declares, that its shell accepts it, and that the bash script completes what a person types.

## cairn fmt

```sh
cairn fmt src/                 # rewrite every *.cairn under src/ in place
cairn fmt --check src/ tests/  # write nothing; exit 1 if anything would change
cairn fmt --diff src/a.cairn   # write nothing; print a unified diff
```

Paths may be files or directories, and a directory is searched for `*.cairn`. The exit code is 1 when a file fails to lex, or when `--check` or `--diff` finds something to change.

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

The layout:

- two-space indentation, one statement per line, one trailing newline;
- canonical spacing: `fn f(n:usize, x:ro<u8>[n]) -> u64`, `a + b`, `x[i]`, `f(a, b)`, `key:Type` with no space after the colon, no space before `;`, `,` or `[`, and `@device` attached to its extent;
- a block written on one line stays on one line if it fits in 100 columns and breaks otherwise, and a block written over several lines is never collapsed;
- long parameter lists, call arguments and binary chains wrap greedily at 100 columns;
- every comment stays where it was;
- one blank line between declarations is kept, a run of blank lines collapses to one, and blank lines next to a brace are dropped.

The formatter never risks a change of meaning. Before writing, it re-lexes its own output and compares the tokens and the comments with the input. If either differs, or the input does not lex, the file is left as it was and listed under `not_formatted` with the reason. Every mode except `--diff` prints a JSON report:

```json
{
  "status": "would-change",
  "mode": "check",
  "changed": ["sloppy.cairn"],
  "not_formatted": [{"file": "broken.cairn", "reason": "Unexpected character '\"'."}]
}
```

Formatting is a fixed point: `format_source(format_source(x)) == format_source(x)`. From Python, `cairn.editor.formatting.format_source(text) -> str` returns `text` unchanged when it refuses, and `format_report(text) -> (text, reason)` also gives the reason.

## cairn check --generics

`cairn check` types the program. `--generics` also checks each generic function once against its bounds and fails if one needs more than they promise, so misuse is reported at the call rather than at the instance. The answer has one entry per template of the project's own modules:

```json
{"status": "typed", "functions": 159, "formal_status": "not-verified",
 "generics": {"analytics.agg.run_static": "ok", "analytics.agg.bins_new": "ok",
              "analytics.query.map_par": "ok", "analytics.query.map_loop": "ok"}}
```

A template that reaches past its bounds is named with what it needed, and the command exits 1. Here `fn widest[T:affine](a:ro<T>, b:ro<T>) -> bool = less(a, b);` compares two values of a type that promised only to be affine:

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

`cairn test` builds one executable holding every selected test block ([language.md](language.md#tests-and-assert)) and runs each test in its own process, several at a time, in source order. A test passes only when its process exits 0. A failed `assert` or guard, a signal, any other status or a timeout fails that test alone, whatever it printed first. The processes run in the project's directory under the limits `cairn run` applies, which stop runaway programs and are not a sandbox.

The manifest's JSON contracts run beside the blocks, and `--contract FILE` runs one contract alone. A run that finds no test at all fails. `--test NAME` runs the one block of exactly that name as `cairn test` labels it (`sums`, or `store.sums` in module `store`), which is what the editor's Run test lens sends.

```text
tests-not-passed: 1 of 3 tests failed, 1 contract, 81 cases
  test wrong: assertion failed at src/main.cairn:13: four is not five
```

Piped, the record gives each test its file and line, its status, why it failed and what it printed. Tests run only as host processes, so a freestanding project's tests are refused (`E-TEST`), and its image holds none of them.

## cairn doc

`cairn doc [path] [--module m]` prints a Markdown reference taken from the checked program: every public type, recipe and function of the project's modules, with its bounds, the `//` comment above it and the effect row the checker inferred.

    $ cairn doc examples/hello
    # root module

    ```cairn
    // source: src/math.cairn Floor average without overflowing the intermediate sum.
    fn average(x:u64, y:u64) -> u64  // effects: trap

    // source: src/main.cairn
    fn main() -> i32  // effects: trap
    ```

Each module gets a heading, its own comment, and one block where each declaration sits under its comment. The effect row ends the declaration's line, or goes above it when the line would pass 120 columns. A template's row is what it may do for any arguments within its bounds, apart from what their own trait members do. `cairn doc --std` documents the packaged library. `make docs` writes the same reference as [std_api.md](std_api.md), an index, and one page per module under `docs/std/`, and the suite compares every page with the compiler's answer, so none can drift.

## cairn expand

`cairn expand [path]` prints, module by module, the CAIRN source that the program's `derive` statements generated: the records, functions and `impl` blocks each recipe made, with every `$name` spliced and every static value folded.

```text
$ cairn expand examples/apps/kvstore
fn encode_Header(out:rw<u8>[16]@host, value:Header) {
  out[0] = u8((shr(value.check, 0) & 255));
  out[1] = u8((shr(value.check, 8) & 255));
  ...
```

Generated code is ordinary code of the deriving module, so this is exactly what the checker sees. When a diagnostic points into a recipe, this is where to read the instance it is about. `cairn inspect --symbol` refuses a generated entry, because the edit belongs in the recipe (`E-SYMBOL`, "Edit an authored function, not a generated entry.").

## cairn explain

`cairn explain [path] [--symbol f]` shows where each function pays at run time, at the `.cairn` line of each cost:

- the guards the emitted C++ still checks;
- the owners it allocates;
- the calls whose effect row allocates, spawns, joins, locks or does I/O;
- the points where it waits for a task, a lock or a region;
- clang's verdict on every loop.

It reads all of this from the emitted C++ and from clang's optimization record for the build's own flags. Nothing is run or timed.

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

`sites` counts what the checker saw as needing a guard, by kind. `discharged` is the part of those the checker proved cannot fail, which the lowering leaves out, and `discharged_by_line` says where each one was. `emitted` is what the lowering wrote. It is usually `sites` minus `discharged`, and higher where the lowering writes one expression twice: a part's base is written once for its data and once for its size, so a guard inside it runs twice. `--keep-guards` on `emit`, `build` and `run` writes every guard, discharged or not, so a program can be run against its conservative build.

In the example, `out[used]` on line 18 keeps its guard, because nothing bounds `used` by the extent of `out`, and so does the `+` on line 19. Clang lists an early exit, which is what a guard's trap is, among its reasons for leaving the loop scalar. A loop is reported where clang placed it, so a loop whose first instruction was inlined from a guard shows the runtime header's line. Verdicts come from clang only: under `--cxx g++`, and for any program with device code, `vectorization` says why it was not read, and nothing is compiled with nvcc.

An agent gets the same report from `cairn inspect --symbol f --explain`, or by sending `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "explain"}` after an edit, which explains the candidate the host last admitted in that session.

## cairn predict

`cairn predict [path] [--symbol f] [--at n=1e6] [--against BEFORE]` says how long each function will take, before anything is built. An agent can try a change and see its cost in time instead of compiling and timing every candidate. The answer is a prediction, never a measurement, and each one says how sure it is and why.

```text
$ cairn predict bench/suite/kernels/saxpy_f32/kernel.cairn --arch x86-64-v4
predicted, not measured: AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64-v4
saxpy_f32  0.0807 ns*n (memory (l1), up to n=2.05e+03); 0.124 ns*n (memory (l2), ...); 5.66 us + 0.0648 ns*n (pool start, ...); ...; 0.342 ns*n (memory (dram), up to n=1.07e+09)
  n=1000          80.6 ns  memory (l1)          37% of speed of light    high
  n=100000        13.9 us  pool start           22% of speed of light    medium
  n=1e+07          922 us  memory (dram)        99% of speed of light    medium
```

The prediction starts from what the checker already knows. A region's count and a loop's trip count are polynomials in the extents, the lane rule makes every write a stream, and the effect row names each allocation, transfer, task and wait. `--format json` prints those counts beside the time: operations by kind, bytes read and written per stream, accesses whose address comes from data, and the formula term by term.

A machine profile prices the counts: bandwidth per cache level on one lane and on all of them, the cost of each kind of operation with and without vectors, and the cost of starting the lane pool, a task and an allocation. `bound` names what limits each part. `speed of light` is the same work at the whole machine's peak for that bound, so a sequential loop reads low where parallel lanes would help.

`--against BEFORE` prices two versions and prints the ratio at each size. Turning `for i in 0..n { out[i] = x[i] * 2.0; }` over `f32` views into `parallel i in n` is predicted to change nothing at a thousand elements and to take under a third of the time at ten million:

```text
$ cairn predict after.cairn --against before.cairn --arch x86-64-v4 --at n=1000 --at n=1e7
predicted, not measured: AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64-v4
f
  n=1000          54.8 ns -> 54.8 ns    x1.0  memory (l1), high
  n=1e+07          997 us -> 300 us     x0.301  memory (l3), medium
```

Confidence is `high` when every count is a size and every access a stream. It is `medium` when something is approximated: a wide host region, a loop bounded by `min()` or by an outer index, an atomic, or a profile measured for another `-march`. It is `low`, with `measure` set, when a number is a guess: a `while` loop, an address from data, a foreign call, a function value, recursion, or a size that was not given.

`evidence/v1_4/perf_model/` records how the packaged profile was measured and how well it predicted timings it was never fitted to. It was within a quarter at one lane and at a hundred million elements, and bad for wide regions between a hundred thousand and ten million elements, which is why those are `medium`. The model prices a fold by the larger of its dependent chain and the rest of its body, a vector loop's compute on physical cores rather than lanes, and a wide region's streams mostly from the shared cache, since the pool's on-demand claims move chunks between cores. The same record says what each of those corrected and where the model got worse.

## cairn tune

`cairn tune [path] --symbol f --at n=1e7` chooses `f`'s [plan](concurrency.md#plans) by prediction. A plan changes how a function's host regions are scheduled and nothing they compute, so every candidate is correct by construction and the search only asks which is fastest. Every legal `grain` and `lanes` pair is priced, with `fuse` on and off where the function has regions to join, and the answer ranks them.

`--measure K` then times the best-ranked plans with distinct lane caps, and the plan the function has now, on this host. Each round halves the field and gives the survivors more runs, and the answer says how many pairs the measurement ordered as predicted. On a busy machine two close plans are within noise of each other, so a close measured order says little. `--write` puts the chosen plan into the file that holds `f`, under its short name, replacing any plan it had there, and writes nothing unless the whole project still checks with it.

A function with a device region is tuned over `block`, `per_lane` and `unroll`. The CUDA compiler builds the kernel once per unroll for `sm_120`, and ptxas reports its registers, which set how many threads an SM holds. A block the registers cannot fill, and a grid too small to keep the device busy, are priced as such. Nothing runs to rank them.

Timing device plans runs device code, so `--measure` on a device function only ranks them and says so. `make tune-device FILE=f.cairn SYMBOL=f AT=n=1e8` is the owner's target that times them: it holds the device lock, rests after each run and stops after 64. `make calibrate-device` replaces the device profile's assumed figures (bandwidth, launch and transfer cost) with measured ones in `results/perf_model/device.json`. No agent runs either target.

`python -m cairn.perf.calibrate --out PROFILE.json` measures another host, and `--profile PROFILE.json` or `CAIRN_PROFILE` selects it. Calibration runs host kernels only. Device code is read at compile time and never run: `cairn.perf.device` compiles for `sm_120` and reads each kernel's registers, spills, shared memory and instruction mix from ptxas and cuobjdump, and `cairn.perf.native` reads each host loop's cycles from llvm-mca.

## cairn diff

`cairn diff OLD NEW` says what changed between two versions of a program, function by function, and what establishes each answer. Each side is a path (a file, a project directory or a manifest) or a revision of the git repository around the current directory, with `--in PATH` naming the project inside it. A revision is read object by object into a scratch directory, and nothing is checked out over the working tree. `--std` compares the packaged library under `src/cairn/std` as each side holds it.

```text
$ cairn diff before.cairn after.cairn
before.cairn -> after.cairn: 1 identical-code, 1 smt-equivalent, 1 behavior-changed
  behavior-changed  scale  at x = 63: before returns 126, after returns 189 (native: clang++ and g++ agree)
  smt-equivalent    clamp  Z3 found no input on which they differ
predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): clamp x0.471, scale x0.889
semver: major: scale behaves differently at x = 63
```

Here `clamp` became `min(x, hi)`, `scale` multiplies by 3 instead of 2, and `label` did not change. Each function gets one class:

| Class | Meaning |
| --- | --- |
| `identical-code` | Its emitted C++, and that of everything it calls, is the same up to a consistent renaming of locals, parameters and compiler temporaries, so the same native code runs. Bare variants, one-statement arms, `+=` on a local and renamed parameters land here. |
| `smt-equivalent` | Z3 found no admitted input on which the two differ in their result, in what they leave in an `rw` borrow, or in whether they abort, within the fragment [verification.md](verification.md#value-level-source-equivalence) describes. |
| `behavior-changed` | It comes with a witness: the input Z3 found, what each version does on it in the value model, and the same run natively under each compiler present when the task runner takes the signature. A native run that disagrees with the model makes the answer `unknown` instead. |
| `identical-source` | A generic function that nothing instantiates has no code to compare, so its tokens are compared with those of every function it names. It is `identical-source` when all are the same and `unknown` otherwise, and it is never left out. |
| `signature-changed` | Its parameter or result types changed, so it is not compared value for value. |
| `added`, `removed`, `renamed` | One version alone holds it, or its code is the other's under a new name. |
| `unknown` | It gives its reason, and is never counted as unchanged. |

A loop the solver cannot bound is tried again with every unsigned parameter at 16 or below. A difference found there is a `behavior-changed` witness, and an equivalence found there is reported as holding only there.

Beside the class, each function lists what the compiler established differently on the two sides, whatever the solver decided: its signature, effect row, guard sites written and discharged, the guards its lowered code holds, heap allocations, local storage, tasks started and `unsafe` blocks. Test blocks are compared by their code. The cost change comes from [cairn predict](#cairn-predict) and is labelled predicted.

The semantic version verdict reads the public interface: the functions and types a program's own modules export, or everything in a program that declares no module. A removed or renamed public function, a changed signature, a public function whose effect row gained an effect or that has a witness, and a changed public type are `major`. An added public function or type is `minor`, and any other change is `patch`. A public function whose class is `unknown` is listed under `unproven`. The level is then `unknown`, with what the proven facts give under `at_least`, because an unproven function may hide a change of any size. Only a `major` verdict stands beside unproven functions, since nothing can raise it further.

`--require equivalent` exits 1 unless every function is `identical-code`, `identical-source` or `smt-equivalent` and no type or test changed, and `--require identical` also refuses `smt-equivalent`. That is how a refactoring's pull request can require proof that it only refactored. `--markdown FILE` also writes a section for a pull request description, and nothing is posted anywhere.

`--timeout-ms` is the solver's time for one query, `--budget-s` its time for the whole diff (60 seconds by default), and `--replays` the number of witnesses built natively (8). Each function is compared in its own process, stopped after twenty solver timeouts or the rest of the budget, whichever is less, because Z3 cannot be interrupted while it reads a large query. A check that runs past twice its timeout is interrupted. Either way the function is `unknown`, with the reason. Both versions are checked and lowered by this compiler, so the diff compares two sources, never two compilers. `evidence/v1_4/diff/` records `cairn diff v1.3.0` against a 1.4 commit, for the library and every example project.

## cairn build --incremental

`--incremental` builds one object per module, compiled against a shared interface header (`program.hpp`: types, tables and prototypes) and cached under `build/objects/`. It is opt-in because separate objects give up inlining across modules. Device programs and freestanding images are always one unit. The cache is safe to delete.

An object's key hashes everything that went into it: the unit, the header, the command line (which carries the compiler, the architecture, the build kind and `--debug`), the runtime headers and the compiler version. A change to any of them gives a different key, so nothing stale can be linked. Missing objects compile concurrently, and the build receipt lists every unit and whether it was reused. On one machine, for the fifteen units of `examples/apps/analytics` (its own eight modules and the seven `std` modules it links):

```text
cairn build --incremental examples/apps/analytics    1.34 s   15 compiled   cold cache
cairn build --incremental examples/apps/analytics    0.14 s    0 compiled   nothing changed
cairn build examples/apps/analytics                  1.17 s             whole program, one unit
```

A body-only edit recompiles one module: adding one statement to `analytics.query.above_loop` took 0.87 s and rebuilt only `analytics_query.cpp`. A signature or layout change recompiles everything, because the shared header is in every key.

A key names an object, and a digest identifies it. `<key>.o` is published by a rename, then the sha256 of its bytes is written beside it as `<key>.sha256` by a second rename, so an interrupted compile leaves at worst an object with no digest. Before an object is reused, its bytes are hashed and compared with the digest: truncate a cached object and the next build compiles that unit again. This catches interrupted, corrupted and shared caches. It does not stop anyone who can write into `build/`, since they can rewrite the digest too.

`build/objects`, each object and each digest must be plain entries of the project's own build output. A symbolic link, or a file where the directory belongs, is refused under the same fail-closed rule as the build directory itself. A unit whose compile times out or is killed leaves no object under its key, and the build still writes the `cairn.build/1` record and the `receipt.json` a whole-program build writes.

## cairn build --header

A C or C++ program can call a CAIRN library with nothing from CAIRN in its own build. `cairn build --kind library --header` writes `NAME.h` beside `libNAME.so`, and `cairn emit --header` prints the same header. `examples/interop` is a statistics library and a C++ program that calls it:

```sh
cairn build examples/interop --header       # libstats.so and stats.h under examples/interop/build/stats-*/
c++ -std=c++17 examples/interop/host/main.cpp -I"$DIR" -L"$DIR" -lstats -Wl,-rpath,"$DIR"
```

The header declares the checked entry `cf_NAME` of every function whose types can all cross the boundary: scalars, copyable records, tag-only enums, copyable sums, and borrows of any of them.

- A view `xs:ro<i64>[n]` becomes `const int64_t *xs`, with its length in the parameter its extent names, in CAIRN's order.
- A single borrow `p:rw<Point>` becomes `ct_Point *p`.
- A record becomes a `ct_` struct with CAIRN's field names, a tag-only enum a `uint32_t` with a constant per variant, and a sum its tag and a union of its payloads.

Each declaration carries the comment above the function, its CAIRN signature and its effect row:

```c
/*
 * One pass over the samples. The total is checked: a sum past i64 aborts rather than wrap.
 * CAIRN: summarize(n:usize, xs:ro<i64>[n]) -> Summary
 * xs: n elements, read.
 * Effects: ffi_precondition, read:xs, trap.
 */
ct_Summary cf_summarize(size_t n, const int64_t *xs);
```

A foreign caller goes through the checked entry. Each view must be null only when it is empty, aligned for its element and inside the address space, and a view the function writes must not overlap any other view argument. Otherwise the process aborts, as it does when any guard of the body fails. A single borrow must point to live storage, which no entry can check. Calls between CAIRN functions go to the lean body `ci_` instead, because the checker has already shown what the entry would check (`checked-entries/1` in the manifest's trusted lowering rules).

Every layout is stated once and checked on both sides. `_Static_assert` lines hold the C or C++ compiler that includes the header to each size, alignment and offset, and the same numbers go into the library's own C++ as `static_assert`, so a library and a header that disagree fail to build instead of corrupting a call. The header also names the library's interface identity: a symbol named by a hash of everything the header declares, which only the library built with that header defines. A program built with a header from another version of the library fails to link, and a generated ctypes binding refuses to load it. Including the header therefore means linking the library.

The end of the header lists what cannot cross, with the reason: owners such as `Buf`, linear values, function values, `dyn` references, an array passed by value, trait members and device kernels. A module's private functions, templates, tests, externs, `main` and vendored dependencies are left out. `--header` needs a hosted library built as one unit, so `--kind exe`, a freestanding target and `--incremental` refuse it.

Python reaches the same library through `cairn emit --ctypes`, which prints a module whose `load(path)` opens it with every bound entry declared. The module states each layout the header states and checks it against ctypes on import, so a layout ctypes would compute differently fails the import instead of a call. It leaves out, with the reason, what ctypes would not pass exactly: a record with `align(n)`, which ctypes cannot state, and, by value, a packed record or a sum whose union holds a float, which libffi may classify differently from the C compiler.

```python
import ctypes

stats = ...  # the module cairn emit examples/interop --ctypes printed
lib = stats.load("examples/interop/build/stats-XXXX/libstats.so")
samples = (ctypes.c_int64 * 6)(4, 8, 15, 16, 23, 42)
assert lib.cf_summarize(6, samples).max == 42
```

The two directions compose. `extern` brings a C function into CAIRN with its effects declared and every call inside `unsafe`, and the header takes a CAIRN function out to C with its checks at the entry. The `ffi:` effects in a library's rows name the foreign code it reaches, and its entries state what it requires of callers. `tests/projects/test_interop.py` builds the example under both compilers and runs the host under AddressSanitizer and UndefinedBehaviorSanitizer. It has a foreign caller pass overlapping, misaligned and null views and a zero divisor, and requires each to abort. It also compiles a header of nested, packed, aligned, array-holding and storage-float records as C11 and C++17 against a library that states the same layouts.

## A manifest is named by its path

`check`, `build`, `run`, `test`, `doc` and `expand` take a directory holding `cairn.toml`, a single `.cairn` file, or the path of a manifest with any name. One project directory can therefore hold several configurations:

```sh
cairn run examples/apps/analytics            # cairn.toml, the host engine
cairn run examples/apps/analytics/gpu.toml   # the same sources plus the device entry
```

[examples/apps/analytics](examples.md#examplesappsanalytics) says what its second manifest changes, and why a machine without CUDA must still be able to build the host engine.

## cairn lsp

```sh
cairn lsp      # speaks JSON-RPC with Content-Length framing on stdin/stdout
```

The server handles `initialize`, `initialized`, `shutdown` and `exit`; full-text `textDocument/didOpen`, `didChange` and `didClose`; and these:

| Request | What it answers |
| --- | --- |
| `publishDiagnostics` | the compiler's diagnostic for the buffer: its code, its message, the `repair_hint` from `agent_tools.explain`, and the exact token range |
| `textDocument/hover` | the smallest checked expression covering the position: its type, the type expected of it, and for a name whether the binding is mutable; on a function name, its signature and its inferred effect row; on any declared name, the `//` comment written directly above its declaration, in this file or the packaged `std` |
| `textDocument/documentSymbol` | functions, structs, enums, traits, consts and impls with ranges; trait and impl members nest as children |
| `textDocument/definition` | a declaration of the name under the cursor: this document, or the packaged `src/cairn/std/*.cairn` file the name comes from |
| `textDocument/completion` | see below; the trigger character is `.` and the client filters by the prefix already typed |
| `textDocument/signatureHelp` | the innermost call still open before the cursor: its signature, its parameters and the index of the one being written; triggers are `(` and `,` |
| `textDocument/references` | every place that names what the cursor stands on: in every file of the document's project, or in this document when it belongs to none, under the rules below |
| `textDocument/prepareRename`, `textDocument/rename` | the same set as one `WorkspaceEdit` over every file it touches, the task contracts that name a renamed function included, admitted only when the whole project rechecks unchanged but for the name, or a refusal that names its reason |
| `textDocument/formatting` | one whole-document edit from `cairn fmt`, or no edit when the buffer is already formatted |
| `textDocument/semanticTokens/full` | every name by what it is, with the legend in `initialize`: namespace, type, struct, enum, interface (a trait), type parameter, parameter, variable, property (a field), enum member, function, method, macro (a recipe) and `effect`; modifiers `declaration`, `readonly`, `defaultLibrary` and `mutable` (a `let mut` local or an `rw` parameter) |
| `textDocument/inlayHint` | each function's inferred effect row before its body, the extents a call leaves out (`n = len(v),` before the first argument of `dot(v, v)`, `n = 4 - 0,` for a part `v[0..4]`), and the type of a `let` that writes none |
| `textDocument/codeLens` | Run test above each `test` block and Run above `main`, for the editor's `cairn.runTest` and `cairn.run` commands, which pass the project's manifest, or the file when it belongs to none, to `cairn test --test NAME` and `cairn run`; none for a buffer that is not a file on disk |
| `textDocument/documentHighlight` | the places in this document that references answers, each a write where the name is declared, bound or assigned and a read elsewhere |
| `workspace/symbol` | every declaration whose name holds the query, case aside, in the open documents, their projects and the project at each workspace folder `initialize` names, with its module as its container |
| `textDocument/codeAction` | a quick fix where one edit repairs the diagnostic and nothing is chosen: the missing arms of a `match` (`E-MATCH-COVERAGE`), spelled as its other arms spell theirs, with a fresh binder for a payload; `let mut` for an assigned `let` local (`E-IMMUTABLE`); the `import` of a packaged module a name uses (`E-CALLEE`, `E-TYPE`) |

Positions are UTF-16 code units, as the protocol requires. Hovering `average` in `examples/hello/src/math.cairn`, then asking for help inside a call to it, answers:

```json
{"id": 2, "result": {"contents": {"kind": "markdown",
  "value": "\n```cairn\nfn average(x:u64, y:u64) -> u64\n```\n\nEffects: `trap`."},
  "range": {"start": {"line": 1, "character": 3}, "end": {"line": 1, "character": 10}}}}
{"id": 3, "result": {"signatures": [{"label": "fn average(x:u64, y:u64) -> u64",
  "parameters": [{"label": "x:u64"}, {"label": "y:u64"}]}],
  "activeSignature": 0, "activeParameter": 1}}
```

A buffer being typed usually does not compile. Each feature therefore takes its context from the current tokens, which always exist because the scanner never fails, and its meaning from the last analysis that did compile, matched by name rather than position. After `let y = p.` the fields of `p` are still offered. The locals on offer are those the last good analysis saw in this function, plus the names the current tokens bind before the cursor (`let`, `let mut`, `reg`, `for`, `each`, `parallel`, `buffer`, `stack`, parameters, closure parameters and `match` payload binders). A buffer that has never compiled still completes its own keywords, builtins, types, declarations and token-visible locals, and it is never renamed.

Semantic tokens and inlay hints follow the same rule. Declarations, parameters and each function's binders come from the current tokens, so a buffer that does not compile is still coloured by what it declares. Rows, inferred types and what a name elsewhere means come from the last good analysis. A generic function's row is the join of its instances' rows, so a template nothing instantiates shows none.

After `name.`, completion offers the fields of the record `name` holds (through `ro<>` and `rw<>`, with a generic container's arguments substituted), and every function `name.f(...)` resolves to: those of the receiver type's module, and the members of visible traits it implements. After an enum or a sum it offers the variants, and after an import alias or a module path that module's public declarations. Elsewhere it offers the locals with their types, the declarations visible in the cursor's module, the builtins, the intrinsic and scalar types, and the reserved words. After `import ` it offers the packaged modules and the document's own, after `derive ` the recipes in reach, and after `:` in a generic bound the traits in scope, the kinds and the scalar classes. A private name of another module is never offered.

A document belongs to a project when it is on disk under a `cairn.toml` that lists it. The server then reads every file of that project, using each open buffer in place of its file, analyses once the combined source the build compiles, and answers for the whole project. A name declared in a sibling file is hovered, completed and jumped to, and a refusal in one file is published on every open file of the project, naming the file and line it is in. A file that opens no module of its own continues the module the file before it opened, because the compiler reads it that way, and the server takes that answer from the compiler's own parse.

In a project, references and rename resolve a declaration as the compiler does: a bare name in its own module, a qualified name (`geo.scale`), a name brought in by `import geo (area);`, and a method call (`pair.scale(1)`) that reaches it. For a local, they take the identifiers from its binder to the end of its declaration, when it is bound exactly once there.

A rename is one checked transaction. The new name must be an identifier no file of the project writes, and not a reserved word, a builtin or a type. The edit is applied to the combined source, which must compile again, and every function's receipt entry (its effect row, callees, guard sites and allocations) must be what it was, under the new name where the name changed. A rename that misses one occurrence, or takes one too many, therefore fails the recheck, even where the program would still compile and a call would silently reach another function of the old name.

A field or a variant is renamed the same way, from the types the checker gave each expression. A field is renamed where its record declares it, in a declared extent or a `lends` clause, and after the `.` of every access to that record. A variant is renamed where its sum declares it, in `Sum.Variant`, in each bare `Variant` the checker typed as that sum, and in match arms over it. A receipt names no field or variant, so every entry must be exactly what it was, and a field that a recipe writes into a generated name (`get_$f`) is refused when a caller of the old name no longer resolves. Declarations of the packaged library, foreign functions, `main` and trait members are refused with the reason. Nothing is ever applied in part.

In a document outside any project, references and rename answer only what one document can prove. For a top-level declaration, that is every identifier token equal to its name, unless the name is ever written after a `.` or after `import`, a local of that name is bound somewhere, or two declarations share it. For a local, it is the identifiers from its binder to the end of its declaration, provided it is bound exactly once there and is not also a declaration, an import alias or an imported name. Nothing can shadow it in that range, because CAIRN rejects shadowing (`E-SHADOW`). Where the rule does not hold, `references` answers nothing and `prepareRename` answers null. A rename is also refused for a reserved word, a builtin, a name from another module, a buffer with no good analysis, and a new name that is not a free identifier of the document. The suite applies each returned edit and recompiles, so an edit that would break a program that compiled is a test failure.

The server analyses the open buffer. A compiler failure becomes a diagnostic, never an exception, and every handler answers an empty list or null for any position in any buffer. Known limits:

- a document outside any project is analysed alone, so a name it imports from a sibling file is not hovered, completed or jumped to, and its references and rename stop at the edge of the buffer;
- `definition` picks the first declaration with a matching name, and `Enum.Variant` resolves to the enum;
- there is no format-on-type, and no quick fix ever widens an effect ceiling, a borrow mode or a signature;
- diagnostics stop at the first compiler error, because the compiler does.

## The editor extension

`editors/vscode/` is a VS Code and Cursor extension whose version is the package's. It holds a generated TextMate grammar, bracket, folding, indentation and comment-continuation rules, snippets, the semantic token legend with a scope for each CAIRN token, and a client that starts `cairn lsp` over stdio. It has no build step, and its `README.md` is what a marketplace page would show. `node_modules/` is not vendored: `vscode-languageclient` is declared in `package.json` and resolved when the extension is packaged or installed.

To install from a checkout, symlink the directory into the editor's extension folder and reload the window:

```sh
ln -s "$PWD/editors/vscode" ~/.cursor/extensions/cairn-language.cairn   # Cursor
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-language.cairn   # VS Code
```

Highlighting works at once. The language server needs its dependency, so run `npm install --omit=dev` inside `editors/vscode/` once; it is the only step that touches the network. To install it as a package:

```sh
cd editors/vscode
npm install
npx --yes @vscode/vsce package          # writes cairn-<version>.vsix
code --install-extension cairn-*.vsix
```

The client runs `cairn lsp`. When `cairn` is not on `PATH`, point the setting at the checkout's entry script, which needs Python 3.11 or later on `PATH`:

```json
{
  "cairn.server.command": "/path/to/cairn/bin/cairn",
  "cairn.server.arguments": ["lsp"]
}
```

The extension shows diagnostics as you type, colours by meaning, and gives inlay hints for rows, extents and types; hover with types, effect rows and comments; a document outline; completion; signature help; go to definition in the open file and into the packaged `std`; references and a checked rename across every file of the project; quick fixes; and `Format Document`, which is `cairn fmt`. Mutable names are underlined, as rust-analyzer does. `tests/tooling/test_extension.py` compiles every snippet with its placeholders at their defaults, and requires the manifest to declare every token type and modifier the server sends beyond the standard ones.

The grammar is generated, never edited. `make editors` writes it and the Vim files from the compiler's own vocabulary (`src/cairn/editor/grammar.py`): the reserved words sorted into classes, the words the parser reads in one position only (`plan`, `into`, `after`, `packed` and the rest), the builtins, scalar and intrinsic types, placements, effects and the library's variants. Scope names are the standard TextMate ones, so every theme colours them. `tests/tooling/test_grammar.py` fails when a committed grammar differs from a fresh run, when a reserved word has no class, or when the parser compares a token against a word the grammar does not know, and it pins the scope of each construct. Where node and an installed editor's `vscode-textmate` are present, the same test tokenizes every `.cairn` file in the repository with that engine and requires it to agree with the suite's own engine character by character.

## Vim, Neovim and GitHub

`editors/vim/` is a runtime directory: `ftdetect` sets the file type for `.cairn`, `syntax` highlights with Vim's standard groups from the same vocabulary as the TextMate grammar, and `ftplugin` sets two-space indentation and `//` comments. Add it to the runtime path, and in Neovim start the built-in client on `cairn lsp`:

```vim
set runtimepath^=/path/to/cairn/editors/vim
```

```lua
vim.api.nvim_create_autocmd("FileType", { pattern = "cairn", callback = function()
  vim.lsp.start({ name = "cairn", cmd = { "cairn", "lsp" }, root_dir = vim.fs.root(0, { "cairn.toml", ".git" }) })
end })
```

GitHub has no CAIRN grammar, so `.gitattributes` asks it to highlight `.cairn` files as Rust, whose keywords and punctuation are the closest, without counting them as Rust in the language bar. It also marks the generated grammars, the API reference and the teaching corpus as generated.

## The freestanding target

A freestanding build produces one ELF image that runs on bare hardware, with no operating system, C library, C++ runtime, dynamic loader, start files or unwinder. `[build] target` in `cairn.toml` selects it, `--target` on `cairn build` and `cairn run` overrides it, and the default is `"hosted"`.

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

`cairn run` reports the UART output as `stdout` and the emulator's exit status as `exit_code`. The deliberate guard violation in `examples/embedded/trap/` reads one element past a four-element array:

```json
{"status": "program-exited", "exit_code": 134,
 "stdout": "trap demo: reading window[4] of 4\n",
 "emulator": ["/usr/bin/qemu-system-aarch64", "-M", "virt", "-cpu", "cortex-a72",
              "-nographic", "-semihosting", "-kernel", ".../trapdemo.elf"]}
```

The profile keeps the whole language except what a host provides. Checked `+ - * / %`, the bounds, extent, null, alignment, overlap and tag guards, `stack` storage, records, sums, `match`, generics, traits, closures, `compact`, `reduce` and `derive wire` behave as they do hosted. `f32` and `f64` work, because the start-up code enables FP and SIMD at EL1.

The build refuses what it could not link, before a compiler runs. It reads each function's effect row from the frontend receipt and rejects the program by name if any row holds an effect only a hosted runtime supplies: `alloc`, `free`, `io`, `gpu_*`, `transfer:*`, `par:*` or `ffi:*`. That covers every `buffer`, `Buf`, `std.vec`, `parallel`, device region and `extern` call. `mmio_read`, `mmio_write` and `asm` remain available inside `unsafe`.

```json
{"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT",
 "message": "A freestanding target has no hosted runtime: main has effect 'alloc'."}
```

No symbol is undefined: `memset`, `memcpy` and the exit path are defined in the target's `start.S`, and `cr::trap()` does not call `std::abort`. A trap is distinguishable from success. `fn main() -> i32` returns its value as the emulator's exit status, and a failed guard leaves by the same path with status 134, the status a hosted shell reports for `std::abort`.

```text
$ nm -u examples/embedded/build/embedded-*/embedded.elf     # nothing is undefined
$ size examples/embedded/build/embedded-*/embedded.elf
   text	   data	    bss	    dec	    hex	filename
   5411	      0	      0	   5411	   1523	embedded.elf
```

What the profile does not give:

- an allocator: `buffer`, `Buf` and `std.vec` are rejected, not emulated, and storage is `stack` arrays, statics and `ro<u8>[n]` string views;
- concurrency or a device;
- an MMU, caches, interrupts or a timer. The image runs in the state QEMU hands it, flat physical memory at EL1 with the MMU off, so every access is Device-nGnRnE memory. The build passes `-mstrict-align`, and performance numbers from this profile are not comparable to hosted ones;
- a vector table, so a hardware exception hangs the machine instead of reporting; the language's own guards are what stop a bad index or an overflow;
- unwinding: a trap does not log or roll back, and `defer` and owner release do not run, just as a hosted abort does not run them;
- cross compilation: a target names a host family and is refused on any other host.

`cairn test` still runs on the host, since a task contract builds a hosted shared library whatever `target` says. The backend is not verified, and `formal_status` stays `not-verified`.

### The aarch64-virt target

QEMU's `virt` machine with a Cortex-A72, the board CAIRN's evidence is captured on.

| Address | What |
| --- | --- |
| `0x0900_0000` | PL011 UART0 data register; `0x0900_0018` is the flag register, bit 5 = TX FIFO full |
| `0x4000_0000` | DRAM base, 128 MiB by default |
| `0x4008_0000` | image load address: `.text`, `.rodata`, `.data`, `.bss` |
| `0x4100_0000` | stack top, growing down; 16 MiB clear of the image and of the device tree QEMU writes just past it |

`src/cairn/targets/aarch64-virt/start.S` sets `sp`, zeroes `.bss`, enables FP and SIMD through `CPACR_EL1`, calls `cf_main` and branches to `cr_exit`. `cr_exit` issues the ARM semihosting call `SYS_EXIT` (`0x18`) with `ADP_Stopped_ApplicationExit` and the status, which QEMU turns into its own exit status; that is why the emulator runs with `-semihosting`. PSCI `SYSTEM_OFF` would also stop the machine, but it always exits 0, so a trap could not be told from success. `start.S` also defines `memset` and `memcpy`, a byte at a time, because the compiler lowers aggregate copies to those names whatever `-ffreestanding` says.

The build is one command line, from the receipt of `cairn build examples/embedded`:

```text
clang++ -std=c++20 -O3 -ffp-contract=off -fno-fast-math -fno-exceptions -fno-rtti
  -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-variable -Wno-unused-but-set-variable
  -DCAIRN_FREESTANDING=1 -ffreestanding -nostdlib -static -fno-stack-protector
  -fno-threadsafe-statics -fno-PIC -fno-PIE -fno-unwind-tables -fno-asynchronous-unwind-tables
  -Wl,--build-id=none -Wno-unused-command-line-argument -mstrict-align -march=armv8-a
  -Wl,-T,<target>/link.ld <build>/program.cpp <target>/start.S -o <build>/embedded.elf
```

`-Wno-unused-command-line-argument` is there because the C++ options go unused on the `start.S` job, and `-Werror` would otherwise reject them. `cairn run --target aarch64-virt <project>` runs exactly this:

```sh
qemu-system-aarch64 -M virt -cpu cortex-a72 -nographic -semihosting -kernel <image>.elf
echo $?      # fn main()'s return value, or 134 for a failed guard
```

QEMU loads the ELF by its program headers and enters at `_start`, so no raw binary and no bootloader is needed. `examples/embedded/` is the worked application and `examples/embedded/trap/` the guard violation, and `evidence/v1_0/embedded/` holds a captured transcript, `size`, `nm` and the tool versions.

### Adding a target

1. Create `src/cairn/targets/<name>/` with `link.ld` and `start.S`. The start-up code owns the stack, `.bss`, the call to `extern "C" cf_main()`, `memset`, `memcpy`, and `extern "C" [[noreturn]] void cr_exit(int)`. That one exit path must carry a status out, so a trap (134) is distinguishable from any value `fn main() -> i32` can return.
2. Add one row to `TARGETS` in `src/cairn/projects/toolchain.py` naming the host `family`, the `-march` `arch`, any extra `flags`, and the `run` command that executes an image, ending in the option that takes the image path.
3. Add the directory's `*.S` and `*.ld` to `package-data` in `pyproject.toml` if the glob does not already cover it, and extend `tests/projects/test_freestanding.py`.

Nothing else in the compiler knows about targets. A profile is a flag set, a linker script, a start-up file and an effect refusal, and every target shares one code generator.
