# Tools and targets

This page covers the `cairn` command line and the targets it builds for. After reading it you can check, format, test, build and run a program, set up an editor, ask where a function pays at run time and how long it will take, search its plans, test a faster implementation against its reference, compare two versions, and build for a GPU or for bare hardware. [agents.md](agents.md) covers the protocol an AI agent edits through.

Every tool here ships with the compiler and needs no Python package outside the standard library. No tool changes what the compiler accepts. `cairn COMMAND --help` lists every option of a command.

| Command | What it does | Described in |
|---|---|---|
| `cairn doctor` | reports the local compilers and tools; downloads nothing | [guide.md](guide.md#install) |
| `cairn new DIR --template T` | writes a project from a template: `default`, `cli`, `lib`, `service` or `parallel`; or, with `--from-sol-execbench`, from a benchmark problem | [guide.md](guide.md#a-project), [--from-sol-execbench](devices.md#benchmark-submissions) |
| `cairn check` | accepts or refuses a program, with every refusal it can judge | [guide.md](guide.md#check-run-test), [every refusal](#every-refusal-in-one-check), [a watched check](#a-watched-check-and-shell-completions), [--generics](#cairn-check---generics) |
| `cairn build`, `cairn run` | builds a native artifact in a fresh directory, with a record; builds and runs it under process limits | [building and running](#building-and-running), [--sanitize](#cairn-run---sanitize), [--incremental](#cairn-build---incremental), [--header](#cairn-build---header), [targets](#the-device-target) |
| `cairn test` | runs test blocks, each in its own process, and task contracts | [cairn test](#cairn-test) |
| `cairn fmt`, `cairn lsp`, `cairn mcp`, `cairn completions` | the formatter; the language server for editors; the Model Context Protocol server for agents; shell completions | [fmt](#cairn-fmt), [lsp](#cairn-lsp), [mcp](#cairn-mcp), [completions](#a-watched-check-and-shell-completions) |
| `cairn rules` | the rule card of a diagnostic code, a card by name, or the cards a program selects | [rules](#cairn-rules) |
| `cairn emit`, `cairn expand`, `cairn doc`, `cairn graph` | prints the C++ the program lowers to; prints the source every `derive` generated; the API reference; the module graph | [--ctypes](#cairn-build---header), [expand and doc](#cairn-doc-and-cairn-expand), [graph](#large-projects-and-bazel) |
| `cairn shot` | runs a program headless and returns every frame `std.draw` captured | [examples.md](examples.md#examplesappspanel), [agents.md](agents.md#requests-beyond-an-edit) |
| `cairn inspect`, `cairn state`, `cairn migrate` | an agent's packet for one symbol; every signature and effect row under a digest, or with `--symbol` one function's investigation; an interface change through every caller | [agents.md](agents.md#packets), [state](agents.md#the-programs-state), [--symbol](agents.md#resuming-an-investigation), [migrate](agents.md#interface-migrations) |
| `cairn explain`, `cairn predict`, `cairn cards`, `cairn tune` | where each function pays at run time; its predicted time; the GPUs `cairn predict` can price; the bounded search over its plans and implementations | [explain](#cairn-explain), [predict](#cairn-predict), [cards](#other-gpus), [tune](#cairn-tune) |
| `cairn validate`, `cairn foreign` | an implementation tested against its reference; what a foreign implementation has | [validate](#cairn-validate), [foreign](#cairn-foreign) |
| `cairn verify`, `cairn diff`, `cairn certificates` | SMT equivalence of two sources; the class of every function between two versions; the collector certificates | [verification.md](verification.md#value-level-source-equivalence), [diff](#cairn-diff), [certificates](verification.md#the-collector-certificates-and-the-loop-model) |
| `cairn export` | the exact program a build compiles, with a record that pins it; with `--harness`, a SOL-ExecBench, GPU MODE or KernelBench submission around it | [export](#cairn-export), [--harness](devices.md#benchmark-submissions) |

## Output for people and for programs

At a terminal, `cairn` prints for a person, as below, and `cairn run` hands the terminal to the program. Piped, every command prints the JSON record that scripts and agents read as one compact line, since an agent pays for every character it reads. `--format human|json` or the `CAIRN_FORMAT` variable chooses between them, a terminal that asks for the record gets it indented, and `NO_COLOR` turns colour off. The exit status and the record are the same either way.

```text
error[E-LEASED]: data is lent to left until wait(left).
  --> src/main.cairn:5:3
  |
5 |   data[0] = 7;
  |   ^^^^
  = help: touch it after the wait, or lend each task a part the other does not touch.
  = note: the tasks card states this rule: cairn rules E-LEASED
```

Every refusal names the rule card that owns its code, and [`cairn rules`](#cairn-rules) prints that card. When the compiler can state the smallest fix without guessing, the refusal carries it as `= help`: a close name for a misspelled one (`did you mean total?`), the conversion between two numeric types, the part `b[0..n]` of an [owner](memory.md#owners-and-moves) passed where `n` elements are expected, the annotation an integer literal's binding needs, the library function an import by name put in place of a builtin, or the change a rule asks for. A code whose message already states the fix gets no help, so nothing is said twice. The JSON record keeps every field it had and adds the card and the fix as `card` and `repair_hint`, from `check`, `build`, `run`, `test`, `validate` and every other command.

A guard is a check the compiler writes wherever it cannot prove an operation safe, such as a bounds or an overflow check. A program stopped by a failed guard is reported as stopped by `SIGABRT`.

## Every refusal in one check

`cairn check` reports every refusal it can judge on its own, so three mistakes in three functions cost one check. The record is the first refusal exactly as a check that stopped there would report it, with the others beside it:

```json
{"protocol": "cairn.diagnostic/2", "status": "rejected", "code": "E-TYPE-MISMATCH",
 "message": "Expected u32, got u64.", "line": 2, "column": 15, "file": "three.cairn", ...,
 "further": [{"code": "E-UNBOUND", "message": "Unbound name missing.", "line": 7, "column": 14, ...},
             {"code": "E-TYPE-MISMATCH", "message": "Expected bool, got u64.", "line": 11, "column": 10, ...}]}
```

Each entry of `further` is a whole diagnostic at its own file, line and column, in source order. A record lists at most twenty, and `further_omitted` counts the rest. `not_judged` counts the functions that got no verdict because of a refusal, and none of them is accepted. `further_stopped` says that the check stopped after the first refusal and what stopped it: a limit's code, such as `E-EFFECT-LIMIT`, or the class of an internal fault, which is a compiler bug to report. Nothing after that point was judged.

The check never reports a refusal that may follow from another. Every function has an effect row, the list of what it may do that a caller can observe (`alloc`, `io`, `trap`, `write:out`, ...), and a signature may cap it with a ceiling. Once a function's body is refused, its row is unknown. A function that reaches it is then not held to its ceiling or to the rule on operand order (`E-EFFECT-ORDER`), and is counted in `not_judged`. So is a function that reaches a reference with [implementations](abstractions.md#implementations), because the reference's row joins theirs only after the rules about implementations run.

A refusal met again through a generic function or a type that two functions use is reported once. A refused type or signature ends the check once every type and signature is checked. A refused constant ends it at once, since whatever names the constant would be judged against half of it. A parse error is reported alone. The rules about [lanes](concurrency.md#parallel-regions), plans, implementations, fusion and layouts run only on a program nothing else refuses.

A script that reads one diagnostic reads what it always read, and the exit status is 1 either way. At a terminal each refusal is shown beside its line, and a last line counts them. `cairn emit`, the language server and the MCP `check` tool report every refusal the same way. `cairn build`, `run` and `test`, and the edit, plan and implementation hosts, stop at the first. `tools/checks/refusal_differential.py` checks every refused program the repository holds both ways, and the first refusal must come out identical.

## cairn rules

```sh
cairn rules E-LEASED          # the card that states the rule behind a code
cairn rules tasks             # a card by name
cairn rules src/main.cairn    # the cards a program selects beyond base, integers and calls
cairn rules --list            # every card, its codes and the words that select it
```

A rule card is a short statement of what one part of the language accepts and refuses, with the diagnostic code of each rule. `cairn rules` prints the compiler's cards, offline and without compiling anything. Every code the compiler, the hosts and `cairn` emit belongs to exactly one card, so a refusal always leads to the rule behind it. One card states each part of the language. The tool cards state what the hosts, the command line and the compiler's own limits refuse: `hosts`, `migrations`, `sketches`, `validation`, `commands`, `harness` and `limits`.

Given a file or a project, `cairn rules` prints the cards the program's words select, which are the cards a host sends an agent that edits it. A code no card states, such as one a recipe's `require` chose, and a word that is no code, card or path are refused with `E-RULE`. The record, `cairn.rules/1`, gives each card's name, kind, codes and text.

## A manifest is named by its path

`check`, `build`, `run`, `test`, `doc` and `expand` take a project directory, a single `.cairn` file, or a manifest of any name, so one directory can hold several configurations:

```sh
cairn run examples/apps/analytics            # cairn.toml, the host engine
cairn run examples/apps/analytics/gpu.toml   # the same sources plus the device entry
```

## A watched check and shell completions

```sh
cairn check demo --watch            # check again each time a file the project reads changes; Ctrl-C ends it
source <(cairn completions bash)    # or zsh; put the script on $fpath as _cairn to keep it
```

`--watch` polls the manifest and its sources four times a second. A manifest that is broken at the start is watched until it is fixed. Piped or with `--format json`, each answer is one JSON Lines record. The completion scripts are generated from the command line's own parser, so they offer every command, option and choice and nothing else.

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

The layout is an indent of two spaces, one statement per line, `key:Type`, and lines wrapped at 100 columns. A block written on one line stays there if it fits, and a block over several lines is never collapsed. Every comment stays where it was, and runs of blank lines collapse to one. Formatting formatted text changes nothing, and `cairn.editor.formatting.format_source(text)` is the same formatter, called from Python.

The formatter lexes its output again and compares the tokens and comments with the input's. If either differs, or the input does not lex, the file is left alone and listed under `not_formatted` with the reason, and the exit code is 1:

```json
{
  "status": "would-change",
  "mode": "check",
  "changed": ["sloppy.cairn"],
  "not_formatted": [{"file": "broken.cairn", "reason": "Unexpected character '\"'."}]
}
```

## cairn test

```sh
cairn test demo                    # every test block and every contract of the manifest
cairn test demo --filter average   # only the tests and contracts whose name contains average
cairn test demo --test app.sums    # the one test block of exactly that name, and no contract
cairn test demo --jobs 4 --timeout 10
```

`cairn test` builds one executable that holds every selected [test block](language.md#tests-and-assert) and runs each test in its own process, several at a time, under the limits `cairn run` applies. Those limits are not a sandbox. A test passes only when its process exits 0, so an assert, a failed guard, a signal or a timeout fails that test alone, whatever it printed. The task contracts the manifest lists under `tests`, JSON files of finite cases, run beside the blocks. A run that finds no test fails.

```text
tests-not-passed: 1 of 3 tests failed, 1 contract, 81 cases
  test wrong: assertion failed at src/main.cairn:13: four is not five
```

Test blocks run only as host processes, so a freestanding project's test blocks are refused with `E-TEST`. `cairn test --contract FILE` still runs a task contract on the host. A device project's tests are built by nvcc for its [device target](#the-device-target) and run on its GPU. `cairn test --emulate --device-target sm_120` runs them on host threads instead, judged against that target ([devices.md](devices.md#emulating-device-code-on-the-host)), and the record's `blocks.emulation` says so.

## Building and running

`cairn build` compiles a program into a fresh directory and prints a record of the build. The record leaves out the checker's receipt, its account of every function. The file `receipt.json` in the build directory keeps that receipt whole, and the record's `receipt` field names the file. `cairn run` builds the same way, then runs the program under process limits (`--timeout`, and `--memory-mib` for heap and thread stacks), or under the target's emulator for a [freestanding image](#the-freestanding-target). Arguments after `--` go to the program. `--keep-guards` writes every guard, including those the checker showed cannot fail, and `--debug` adds debug symbols that point at the CAIRN source.

### cairn run --sanitize

`cairn run . --sanitize address < input.txt` builds the program for the host at `-O1` with frame pointers, checked by AddressSanitizer with leak detection and by UndefinedBehaviorSanitizer, and runs it. `--sanitize thread` checks it with ThreadSanitizer instead. The first report ends the run, and the record names the sanitizer that ran. A sanitizer maps shadow memory many times the program's size, so the run has no memory cap. `cairn build --sanitize` builds the same executable without running it. A device program is checked on host threads with `--emulate`, never on a GPU.

### cairn build --incremental

`--incremental` compiles one object per module against a shared interface header and caches it under `build/objects/`. The key is a hash of the unit, the header, the command line, the runtime headers and the compiler version. It is opt-in because separate objects give up inlining across modules. Device programs and freestanding images are always one unit.

```text
cairn build --incremental examples/apps/analytics    1.34 s   15 compiled   cold cache
cairn build --incremental examples/apps/analytics    0.14 s    0 compiled   nothing changed
cairn build examples/apps/analytics                  1.17 s             whole program, one unit
```

An edit inside one body recompiles one module (0.87 s for one statement added to `analytics.query.above_loop`), and a change of a signature or a layout recompiles all of them. Each object is published by a rename with its sha256 beside it and hashed again before reuse. That catches an interrupted, corrupted or shared cache, but not someone who can write into `build/`. The cache is always safe to delete.

### cairn build --header

`cairn build --kind library --header` writes `NAME.h` beside `libNAME.so`, so a C or C++ program can call the library with nothing from CAIRN in its own build:

```sh
cairn build examples/interop --header       # libstats.so and stats.h under examples/interop/build/stats-*/
c++ -std=c++17 examples/interop/host/main.cpp -I"$DIR" -L"$DIR" -lstats -Wl,-rpath,"$DIR"
```

The header declares the checked entry `cf_NAME` of every function whose types can cross: scalars, copyable records, enums of tags alone, copyable sums, and borrows of them. A [view](memory.md#arrays-views-and-parts) `xs:ro<i64>[n]` becomes `const int64_t *xs` with its length in `n`, and a record becomes a `ct_` struct with CAIRN's field names. Each declaration carries its comment, signature and effect row:

```c
/*
 * One pass over the samples. The total is checked: a sum past i64 aborts rather than wrap.
 * CAIRN: summarize(n:usize, xs:ro<i64>[n]) -> Summary
 * xs: n elements, read.
 * Effects: ffi_precondition, read:xs, trap.
 */
ct_Summary cf_summarize(size_t n, const int64_t *xs);
```

The entry checks what a foreign caller could get wrong, and aborts as a failed guard does. A view may be null only when it is empty, and must be aligned and inside the address space. A written view must overlap no other view. No entry can check that a single borrow points to live storage. Calls between CAIRN functions reach the body `ci_` directly, without the entry checks.

Every layout is asserted in both the header and the library, so a mismatch fails to build instead of corrupting a call. The header names an interface hash that only the matching library defines, so a program built against another version fails to link. The header ends with what cannot cross and why: owners such as `Buf`, linear values, function values, `dyn` references, arrays by value, trait members and device kernels. Private functions, templates, tests, externs, `main` and vendored dependencies are left out. `--header` needs a hosted library built as one unit, so `--kind exe`, a freestanding target and `--incremental` refuse it.

`cairn emit --ctypes` prints a Python module that opens the same library and asserts the same layouts on import. It leaves out, with the reason, what ctypes cannot pass exactly: a record with `align(n)`, and a packed record or a sum that holds a float, by value.

```python
import ctypes

stats = ...  # the module cairn emit examples/interop --ctypes printed
lib = stats.load("examples/interop/build/stats-XXXX/libstats.so")
samples = (ctypes.c_int64 * 6)(4, 8, 15, 16, 23, 42)
assert lib.cf_summarize(6, samples).max == 42
```

A library that runs device work adds two things to its header. `void cairn_NAME_device_stream(void *stream)`, where `NAME` is the library's name, moves the calling thread's device work onto a `cudaStream_t` the caller owns ([devices.md](devices.md#device-execution)). `cq_NAME(void *stream, ...)` sits beside `cf_NAME` for each function whose work the host cannot see before it returns. It queues that work on `stream` and returns without waiting, making no stream, event or allocation, so a caller can capture it in a CUDA graph. The ctypes module takes the stream as a pointer, such as `torch.cuda.current_stream().cuda_stream`. A function that must wait on the host has no `cq_` entry, and the header lists why (`E-ENQUEUE`). [devices.md](devices.md#one-wait-or-none) has the rule, and says where a failed device guard is observed under `cq_`.

`tests/projects/test_interop.py` builds the example under both compilers and runs it under AddressSanitizer and UndefinedBehaviorSanitizer. It requires overlapping, misaligned and null views to abort, and compiles headers of nested, packed and aligned records and of records of storage floats as C11 and C++17.

## cairn doc and cairn expand

`cairn doc [path]` prints a Markdown reference of the checked program's public types, recipes and functions, with their bounds, comments and inferred effect rows. For a cooperative region it adds the shared memory each block holds, per instance of a template. `--module M` documents only the modules named. `cairn doc --std` documents the packaged library, and `cairn doc --std --module text` one module of it. `make docs` writes the library's reference with `--pages docs` as [std_api.md](std_api.md) and one page per module, which the suite holds to the compiler's answer.

    $ cairn doc examples/hello
    # root module

    ```cairn
    // source: src/math.cairn Floor average without overflowing the intermediate sum.
    fn average(x:u64, y:u64) -> u64  // effects: trap

    // source: src/main.cairn
    fn main() -> i32  // effects: trap
    ```

`cairn expand [path]` prints the source the program's `derive` statements generated, with `$name` spliced and static values folded. That is exactly what the checker sees, and where to read the instance a diagnostic inside a recipe is about. An edit belongs in the recipe, so `cairn inspect --symbol` refuses a generated entry (`E-SYMBOL`).

```text
$ cairn expand examples/apps/kvstore
fn encode_Header(out:rw<u8>[16]@host, value:Header) {
  out[0] = u8((shr(value.check, 0) & 255));
  out[1] = u8((shr(value.check, 8) & 255));
  ...
```

## cairn check --generics

`--generics` also checks each generic function once against its bounds, as [abstractions.md](abstractions.md#certifying-a-template) describes:

```json
{"status": "typed", "functions": 172, "library_functions": 84, "formal_status": "not-verified", ...,
 "generics": {"analytics.agg.run_static": "ok", "analytics.agg.bins_new": "ok", ...,
              "analytics.query.map_par": "ok", "analytics.query.map_loop": "ok"}}
```

A template that needs more than its bounds promise is named with what it needed, and the command exits 1. `fn widest[T:affine](a:ro<T>, b:ro<T>) -> bool = less(a, b);` compares values that promised only to be affine:

```json
{"generics": {"ranking.widest": "E-TRAIT-IMPL: ?ranking.widest.T does not implement std.core.Ord."}}
```

## cairn lsp

`cairn lsp` is the language server, the program an editor starts to show the compiler's diagnostics, types and names as you type. It speaks JSON-RPC with Content-Length framing on standard input and output, and answers these requests:

| Request | What it answers |
| --- | --- |
| diagnostics | every refusal of the check, each with its repair hint on its exact token range and its rule card as `codeDescription` |
| hover | the type of the smallest checked expression, the expected type, and a function's signature, effect row and comment |
| documentSymbol, workspace/symbol | the declarations of a document, or of the open projects |
| definition | the name's declaration, in the project or the packaged `std` |
| completion, signatureHelp | fields, functions, variants, module members, locals, keywords; the call being written and its current parameter |
| references, documentHighlight, rename | every place that names the declaration or local, and a rename across the project, checked before it is offered |
| formatting | one edit of the whole document from `cairn fmt` |
| semanticTokens | every name by what it is, including effects and mutable bindings |
| inlayHint | each function's effect row, the extents a call leaves out, and the type of a `let` that writes none |
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

A buffer being typed rarely compiles. Each feature therefore takes its context from the current tokens and its meaning from the last analysis that compiled, so after `let y = p.` the fields of `p` are still offered. A document that a `cairn.toml` lists is analysed with its whole project, so names from sibling files resolve. The project's first refusal shows on every open file of it, and each further refusal only on the file it is in.

A rename is one checked transaction. The edited project must compile again with every function's effect row, callees, guard sites and allocations as they were, under the new name, so a rename that misses an occurrence or catches one too many is refused. References and a rename of a function follow it into `fn g(...) implements f` and `plan f use g;`. Renaming either function gives the implementation a new identity, since the identity digests both declarations as written, and a validation kept for the old one no longer holds.

Fields and variants are renamed from the types the checker gave each expression. Library declarations, foreign functions, `main` and trait members are refused with the reason. Outside a project, references and rename cover only what one document can prove, and answer nothing where that is not enough.

A compiler failure becomes a diagnostic, never an exception. The known limits are these: `definition` picks the first declaration with a matching name, there is no formatting as you type, and no quick fix ever widens an effect ceiling, a borrow mode or a signature.

## The editor extension

`editors/vscode/` is an extension for VS Code and Cursor. It holds a generated TextMate grammar, snippets, the semantic token legend and a client that starts `cairn lsp`, with no build step and no vendored `node_modules`. From a checkout, link it into the editor's extensions and reload the window:

```sh
ln -s "$PWD/editors/vscode" ~/.cursor/extensions/cairn-language.cairn   # Cursor
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-language.cairn   # VS Code
```

Highlighting works at once. The language server needs `npm install --omit=dev` inside `editors/vscode/` once, which is the only step that touches the network. To install the extension as a package instead:

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

`make editors` generates the grammar and the Vim files from the compiler's vocabulary (`src/cairn/editor/grammar.py`), with standard TextMate scope names that every theme colours. `tests/tooling/test_grammar.py` fails when a committed grammar differs from a fresh run or misses a word the parser knows, and `tests/tooling/test_extension.py` compiles every snippet.

## Vim, Neovim and GitHub

`editors/vim/` is a runtime directory with file type detection, syntax from the same vocabulary, and an indent of two spaces. Add it to the runtime path, and in Neovim start the built-in client on `cairn lsp`:

```vim
set runtimepath^=/path/to/cairn/editors/vim
```

```lua
vim.api.nvim_create_autocmd("FileType", { pattern = "cairn", callback = function()
  vim.lsp.start({ name = "cairn", cmd = { "cairn", "lsp" }, root_dir = vim.fs.root(0, { "cairn.toml", ".git" }) })
end })
```

GitHub has no CAIRN grammar, so `.gitattributes` has it highlight `.cairn` files as Rust without counting them as Rust in the language bar.

## cairn mcp

`cairn mcp` serves the compiler's hosts to an agent that speaks the Model Context Protocol, whether or not it has a shell, as one JSON-RPC 2.0 message per line on standard input and output. A host holds a program, shows an agent part of it and decides whether each change the agent sends is kept ([agents.md](agents.md)). The Claude Code plugin starts the server. Another client runs `bin/cairn` of a checkout with the argument `mcp`. The server has eight tools, each a thin call into a host that this page or [agents.md](agents.md) describes:

| Tool | What it calls |
|---|---|
| `check` | `cairn check`: `typed`, or every refusal, each with its code, file, line, card and repair hint |
| `edit_open`, `edit_request` | a [guarded edit session](agents.md#packets) and its `cairn.edit/2` requests |
| `plan_open`, `plan_reply` | a [plan session](agents.md#plan-edits) and its `cairn.plan/1` replies |
| `implementation_open`, `implementation_submit` | an [implementation session](agents.md#implementation-sessions) and its submissions |
| `state` | `cairn state`: every signature and row under a digest, then only what changed since the last one this server sent of that path unless `whole` is true, what changed since any digest it sent (`since`), or with `symbol` one function's investigation |

A tool takes `path` or `source`. `path` is a `.cairn` file, a project directory or a manifest, as the command line takes it, relative to the directory the server started in and inside it. `source` is the text of a program, and a session opened on `source` writes nothing.

A session opened on a path writes each change its host accepts back to the files it came from: an edit into the function's file, a plan after the function's declaration and out of any file that named it elsewhere, and a validated implementation beside its reference. It writes only while every file of the project and the manifest still hold what the host judged the change against. Otherwise the reply is refused as stale with `E-SESSION` and nothing is written, so a file saved in the meantime is never overwritten. It writes only the files the change touches, never a vendored one, each beside itself and then renamed into place, all of them or none. Each file keeps its own form: a byte-order mark stays, a line the change leaves alone keeps its ending, and a line it adds or changes ends as most of the file's lines end, CRLF or LF, with LF on a tie.

```json
{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "edit_request", "arguments": {"request":
  {"protocol": "cairn.edit/2", "handle": "e1", "kind": "body", "replacement": "{ return add_wrap(x, 2); }"}}}}
{"jsonrpc": "2.0", "id": 4, "result": {"isError": false, "content": [{"type": "text", "text":
  "{\"status\":\"typed\",\"symbol\":\"lib.bump\",\"effects\":[],...,\"written\":[\"src/lib.cairn\"]}"}]}}
```

The server compiles each distinct program once, and every tool reads that compile ([internals.md](internals.md#compiler-architecture)). A second `check` of an unchanged project answers from it and says `"cached": true`. A file saved on disk changes the source the server reads, so the next call compiles again, and the compile of the text the files held before is still kept. An accepted edit's compile is the one a following `check` or `state` reads, and an edit session opened after it starts from it too. The server keeps at most 16 programs and 256 MiB of what their compiles made, and drops the one used least recently first, so its memory stays bounded however long it runs. [evidence/v1_1/workspace](../evidence/v1_1/workspace/README.md) has the times before and after.

A result is an error exactly when the command line would exit nonzero: a refused program or request, a stale write, or an environment that cannot answer. Its text is then the CAIRN diagnostic record, with the code, the line and the fix the host can state. A message the server cannot read is a JSON-RPC error: -32700 for text that is not JSON, -32600 for one that is not a request, -32601 for an unknown method, and -32602 for an unknown tool or arguments that are not an object. The server speaks the protocol versions 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25. It answers `initialize` with the client's version when it speaks it and with 2025-11-25 otherwise, and answers requests one at a time. Only the protocol reaches standard output: a build or a test the server starts writes to standard error and reads nothing from standard input.

An implementation session on a project keeps failing cases in `regressions/<reference>.json` and every submission in `.cairn/history`, as [`cairn validate`](#cairn-validate) and [`cairn tune`](#cairn-tune) do. It uses the policy that regressions file pinned, and asking for another is `E-TEST-POLICY`.

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

`sites` counts what needed a guard, `discharged` what the checker proved cannot fail, and `emitted` what the lowering wrote. `emitted` can exceed the difference, because a part's base is written once for its data and once for its size. Here `out[used]` keeps its guard, since nothing bounds `used` by the extent of `out`, and clang names that guard's early exit as the reason the loop stays scalar. Loop verdicts come from clang only; under g++ or with device code the report says why it has none. An agent gets the same report from `cairn inspect --symbol f --explain`, or by sending `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "explain"}` after an edit.

For a [cooperative region](devices.md#cooperative-regions) the function's entry adds `cooperative`: the block's threads, and each shared array and pipeline with its bytes. At their lines it lists every barrier, every warp collective and fragment operation, and every pipeline copy and wait, with the copies the wait leaves in flight (`wait_group`, the checker's count). The same barriers, waits and warp operations appear under `synchronization`.

## cairn predict

`cairn predict [path] [--symbol f] [--at n=1e6] [--against BEFORE] [--inspect]` says how long each function will take without building it, so an agent can price a change before it compiles and times it. The answer is a prediction with a confidence. It is never a measurement.

```text
$ cairn predict bench/suite/kernels/saxpy_f32/kernel.cairn --arch x86-64-v4
predicted, not measured: AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64-v4
saxpy_f32  0.0807 ns*n (memory (l1), up to n=2.05e+03); 0.124 ns*n (memory (l2), ...); 5.66 us + 0.0648 ns*n (pool start, ...); ...; 0.342 ns*n (memory (dram), up to n=1.07e+09)
  n=1000          80.6 ns  memory (l1)          37% of speed of light    high
  n=100000        13.9 us  pool start           22% of speed of light    medium
  n=1e+07          922 us  memory (dram)        99% of speed of light    medium
```

The checker supplies the counts. Trip counts are polynomials in the [extents](memory.md#arrays-views-and-parts), the lengths that views carry in their types. The lane rule makes every write a stream, and the effect row names each allocation, transfer, task and wait. A machine profile prices them by the bandwidth of each cache level and the cost of each operation, of starting the lane pool, a task and an allocation. `bound` names what limits each part, `speed of light` is the same work at that bound's peak, and `--format json` shows the formula term by term.

`--against BEFORE` prints the ratio between two versions, here a loop over `f32` views made `parallel i in n`:

```text
$ cairn predict after.cairn --against before.cairn --arch x86-64-v4 --at n=1000 --at n=1e7
predicted, not measured: AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64-v4
f
  n=1000          54.8 ns -> 54.8 ns    x1.0  memory (l1), high
  n=1e+07          997 us -> 300 us     x0.301  memory (l3), medium
```

Confidence is `high` when every count is a size and every access a stream. It is `medium` when something is approximated (a wide host region, a loop bounded by `min()`, an atomic), and `low` when a number is a guess (a `while` loop, an address from data, a foreign call, recursion). On timings it was not fitted to, the packaged profile came within a quarter at one lane and at a hundred million elements. It predicted wide regions of a hundred thousand to ten million elements badly, hence `medium` ([evidence/v1_0/perf_model](../evidence/v1_0/perf_model/README.md)). `python -m cairn.perf.calibrate --out PROFILE.json` measures another host, and `--profile` or `CAIRN_PROFILE` selects the result.

A [cooperative region](devices.md#cooperative-regions) is priced by its blocks. Each thread's work is counted between its barriers as its warp runs it. Which lanes take part in an access or a branch, and which banks or 32-byte sectors they reach, come from the phase rule's run of one block. The region costs a launch and the largest of four times over the grid: device memory, issue, wavefronts in shared memory, and multiply-adds on tensor cores. An SM holds as many blocks as its threads, registers and shared memory allow, beside the 1 KB each block leaves the system. Registers count only after `--inspect` has compiled the kernels for the device target and ptxas has reported them; nothing runs.

A pipeline's copies go at most as fast as the bytes its stages keep in flight divided by the memory latency. A wait that leaves `N` copies in flight (`cp.async.wait_group N`) keeps `N + 1` stages in flight in each block. A deeper pipeline copies faster until the bandwidth caps it, and holds more shared memory, which can leave fewer blocks on each SM. Each line says what it rests on: `[checked]` for the checker's counts, `[ptxas]` for the compiler's report, `[specification limits]` for NVIDIA's published figures, and `[assumed]` for the profile's assumptions, among them the 500 ns latency. No device run has checked any of it, and the confidence is `low`.

```text
$ cairn predict examples/cooperative/gpu.toml --symbol "row_sums[3]" --at rows=3,cols=1e5 --inspect
row_sums[3]  8.01 us + 8.09 ns*rows (launch, up to rows=512); 8.01 us + 8.11 ns*rows (device memory, ...)
  cooperative region at src/device_kernels.cairn:48: rows blocks of 256 threads (256), on the device
    [checked] shared memory a block: 8192 bytes (partial 2048, tiles 3 stages of 2048)
    [checked] tiles, line 49: depth 3; the wait at line 59 leaves 2 in flight
    [ptxas] registers a thread: 34
    [specification limits] an SM holds 6 blocks, 100% of its threads: by threads 6, registers 6, shared memory 11, ...
  rows=3, cols=100000    73.6 us  device memory        4% of speed of light     low
    [model] 3 blocks in 1 wave, 1% of the device busy; memory 65.6 us, issue 7.23 us, shared 5.17 us beside a 8 us launch [assumed]; tiles keeps 3 stages in flight a block, 18432 bytes on the device: copies at 36.9 GB/s [assumed latency]
```

At depth 2 the same region holds 6144 bytes a block, keeps 2 stages in flight and is predicted at 106 us. `--against` names what a change did to each region: `tiles.depth 2 -> 3; shared_bytes_per_block 6144 -> 8192`.

### Other GPUs

`--card CARD` prices device work on a packaged device card in place of the RTX 5070 Ti, so an agent can ask how a kernel does on a GPU it does not have. `cairn cards` lists the cards. A card is named by its key or by the start of one before a hyphen, so `--card h100` is `h100-sxm5`. Any other name is refused with `E-DEVICE-CARD`.

```text
$ cairn cards
card             cc     SMs    GHz   GB/s    f32 f16 tensor f8 tensor  device
a100-sxm4-80gb   8.0    108   1.41   2039   19.5        312         -  A100 SXM4 80GB
b200-hgx         10.0   148  1.979   7700     75       2250      4500  B200 (HGX B200)
h100-sxm5        9.0    132   1.98   3352   66.9      989.4    1978.9  H100 SXM5 80GB
h200-sxm         9.0    132   1.98   4800     67      989.5      1979  H200 SXM 141GB
l40s             8.9    142   2.52    864   91.6     362.05       733  L40S
rtx-4090         8.9    128   2.52   1008   82.6      165.2     330.3  GeForce RTX 4090
rtx-5070-ti      12.0    70  2.452    896  43.94       87.9     175.8  RTX 5070 Ti
rtx-5090         12.0   170  2.407   1792  104.8      209.5       419  GeForce RTX 5090
```

Every figure on a card is NVIDIA's published specification, and tensor rates are the dense ones. The card's `source` names the document behind each figure: the GPU's datasheet or architecture whitepaper, and the CUDA Programming Guide's tables per compute capability. A figure NVIDIA does not publish for that product is either derived from ones it does, and listed under `derived` with how, or assumed and listed under `assumed`.

The B200's clock is derived: its 75 TFLOPS of FP32 over 148 SMs of 128 cores is 1.979 GHz. The launch cost, the link's fixed cost, the share of peak bandwidth a stream reaches, the occupancy that keeps memory busy and the memory latency are assumed, the same on every card. No card has been measured. The B300 and the GB10 have no card: NVIDIA publishes no clock for either, and its SM counts for the B300 disagree.

The card also gives the device target when neither `--device-target` nor `[build] device_target` names one. That target is the card's own compute capability. From sm_90 on it is the target specific to that architecture (`sm_90a` for the H100), since a card describes exactly one device, and before sm_90 it is the portable one (`sm_80` for the A100). So `--card h100 --inspect` compiles the kernels for sm_90a and reads their registers with ptxas, with no GPU present. A named target is kept, and a card its code does not run on is refused with `E-TARGET-MISMATCH`, as `--device-target sm_120 --card h100` is. A target that a build of the program refuses is refused as the build refuses it, and never priced. That is `E-IMPL-TARGET` when the selected implementation needs a feature the target lacks, as `plan f use g;` with `g` needing `tma` is on the A100's `sm_80`, and `E-TARGET-FEATURE` for the program's own features.

`--card all` prices every card at once. Each function with device work gets a row per card at each size, with its bound, its time and its fraction of speed of light:

```text
$ cairn predict examples/tensor/tile32.cairn --card all --at m=4096,n=4096,k=4096,cn=16777216,an=16777216,bn=16777216
predicted from published specifications, not measured: 8 cards; host work on AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes (measured), x86-64
tile32  m=4096, n=4096, k=4096, cn=1.67772e+07, an=1.67772e+07, bn=1.67772e+07
  a100-sxm4-80gb   sm_80       5.93 ms  shared memory        100% of speed of light   low
  b200-hgx         sm_100a     3.09 ms  shared memory        100% of speed of light   low
  h100-sxm5        sm_90a      3.46 ms  shared memory        100% of speed of light   low
  h200-sxm         sm_90a      3.46 ms  shared memory        100% of speed of light   low
  l40s             sm_89       9.24 ms  device memory        85% of speed of light    low
  rtx-4090         sm_89       7.92 ms  device memory        85% of speed of light    low
  rtx-5070-ti      sm_120a     8.91 ms  device memory        85% of speed of light    low
  rtx-5090         sm_120a     4.46 ms  device memory        85% of speed of light    low
```

The same kernel is bound by shared memory on the data center cards and by device memory on the others, which is the kind of difference the rows are for. Each card is priced for its own target unless one is named. A named target prices only the cards its code runs on and lists the others as refused. A card whose target the program's build refuses is listed as refused too. A function without device work is listed once, since no card changes it. `--inspect` compiles once for each target. [evidence/v1_1/catalog](../evidence/v1_1/catalog/README.md) records these predictions for the suite's kernels and the device examples. They are predictions from specifications, and nothing ran.

## cairn tune

`cairn tune [path] --symbol f --at n=1e7` chooses the [plan](concurrency.md#plans) of `f` by a bounded search. A plan says how a function's parallel regions run, such as how many lanes a host region uses or how a device block is shaped, and it never changes a result. So every candidate the checker accepts is correct, and the search asks only which is fastest. A function is named with its module, as in `--symbol lib.spread`. `--write` puts the chosen plan after the function's declaration and removes any plan that named the function elsewhere. It writes only if the whole project still checks, and keeps each file's byte-order mark and line endings as [`cairn mcp`](#cairn-mcp) does.

### The search

The space is every combination of the items the regions of `f` take: `grain` and `lanes` for host regions; `block`, `per_lane`, `unroll`, `vector` and `stage` (at the radius the staging rule reads) for device regions; and `fuse` where two regions could join. The checker decides what is legal, never the search: each candidate is written into the source and the whole program is checked. `vector` beside `fuse`, or `stage` beside `vector`, is refused with `E-PLAN` and counted under `space.refused` with one example. `cairn predict` prices every legal candidate.

The search generates the space as it goes, never lists it whole, and tries the model's likely winners first. The plan with no items comes first, then each implementation, then each item alone, each checked and priced as it comes. Every combination follows in the order of its estimated time, the product of what each of its items did alone. The estimate only orders the candidates, and each is priced for itself. When the time runs out the search stops generating, and `undone` counts the rest as `not generated: out of time`. Where compiles or timed runs follow, the candidates may take half of `--budget-seconds`.

Each candidate is still checked as a whole program, and lowered as `cairn build` lowers it. The checker reads a plan only after every body and implementation instance, which is more than nine tenths of a check on `examples/cooperative/tuned.toml` ([evidence/v1_1/search](../evidence/v1_1/search/README.md)). That work happens inside one pass whose state cannot be copied, so the search cannot do that work once and then check each plan alone. A candidate whose function lowers to the same canonical code as an earlier candidate's, as `unroll 1` and no plan do, takes that candidate's price, compile and measurement, and its row lists the earlier one under `same_code` (`space.same_code` counts them). Only its check is its own, since the code is known only once it is checked.

`--at n=1e6 --at n=1e8` prices every candidate at both sizes and ranks them by one objective. Each row gives `predicted_ns_at`, its time at each size, beside `predicted_ns`, the objective. The objective is the geometric mean by default, as GPU MODE scores a kernel over its shapes. `--objective mean` takes the arithmetic mean, which the slowest size dominates. `--shapes SHAPES.json` adds sizes with weights, `[{"at": "n=1e6", "weight": 3}, {"at": "n=1e8"}]`.

`fastest` names the best candidate at each size, and that candidate's row says `fastest_at`, so a plan that wins one size and loses the objective shows it. Measured times fold the same way. `chosen_by` says which objective chose what `--write` writes, over predicted or measured times, and the history's record of the search names it too. It is CAIRN's number over its own model and runs, and no leaderboard's score. Below, the model predicts `lanes 4` 13% faster at a hundred thousand elements and 42% slower at a billion, so the geometric mean chooses `grain 65536`.

```text
$ cairn tune bench/suite/kernels/saxpy_f32/kernel.cairn --symbol saxpy_f32 --at n=1e5 --at n=1e9
saxpy_f32: 36 plans, 36 accepted, 0 refused or unchecked; now (no plan for saxpy_f32)
ranked by the geometric mean of the times at n=100000; n=1e+09
   1  plan saxpy_f32 { grain 65536; }                 2.07 ms predicted
      n=100000: 12.7 us; n=1e+09: 339 ms
   ...
chosen: plan saxpy_f32 { grain 65536; }
fastest at n=100000: plan saxpy_f32 { lanes 4; }, 11.1 us predicted; it loses the objective
```

### Implementations

When `f` has [implementations](abstractions.md#implementations), alternative versions of it declared with `implements`, each is a candidate beside the reference, with the reference's plan as it is now. Its row's `plan` reads `plan f use g;`, and it is priced as `g`, the code that runs where the condition holds. A plan's items schedule only the reference's regions, so every other plan beside `g` would be the same candidate to the model, and `undone` counts those apart. A function with implementations and no region is searched over them alone. A candidate whose implementation `needs` a device feature the target lacks is refused as a build for that target refuses it, with `E-IMPL-TARGET`, and counted under `space.refused`. `--compare` refuses it the same way.

Selecting an implementation could change a result, so one is chosen or timed only while the history holds a validation of it as it is now, the record an [implementation session](agents.md#implementation-sessions) or [`cairn validate --history`](#cairn-validate) keeps. Its row says `validated` with the evidence class, or that none holds. Editing the implementation makes its validation stale, and without a history no implementation is chosen. `--write` writes the chosen selection beside the plan, or removes it when the reference was chosen. [demos/implement](../demos/implement/README.md) shows a search over validated implementations.

A validation counts only when its policy is at least as strict as the reference's: the one the project's `regressions/f.json` pinned, or the defaults without one. That means at least as many cases, no looser tolerance, and a domain that admits every input the reference's does. Otherwise the row says `validated only under a weaker policy than the reference's`, with what the policy lacks and the policy itself, and `cairn validate` with no `--policy` validates it under the reference's. A validation also counts only under the same numerical policy, when `cairn tune` builds with the same compiler, and while no input that failed its validation is kept under the same contract. A validation that ran only on a host emulation of the device (`finite-tested-emulated`, from `cairn validate --emulate`) is not enough on its own: the row says it is the only evidence, and `--accept-emulated` lets the search choose the implementation on it, with the row naming the evidence and the target it was judged against.

An implementation with [natural parameters](abstractions.md#implementations) is searched over the values its `tune` clause lists. Each instance is a candidate of its own: checked, priced, compiled for its kernels when it runs device code, and chosen or timed only while the history holds a validation of that instance. Its row reads `plan prefix use prefix_by[16];` with its `parameters`, and `--write` writes that line. The checker has already held every listed instance to the implementation rules, and the budgets bound how many the search compiles and times. `--compare "use prefix_by[8]" --compare "use prefix_by[32]"` compares two instances.

A cooperative region's block shape and a pipeline's depth are such parameters. `threads t in T` and `pipeline tiles:u64[T] depth D;` take a natural, so `fn row_totals_tiled[T:nat, D:nat](...) implements row_totals tune T in [128, 256], D in [2, 3]` ([examples/cooperative/tuned.toml](../examples/cooperative/tuned.toml)) gives the search four instances. Each is priced as above and compiled for its own kernel, whose registers, shared memory and SASS digest its row shows. None is chosen until a validation of it holds. A device implementation gets one on a device only under `make gpu`, or on a host emulation from `cairn validate --emulate`, which the search chooses on only with `--accept-emulated`. `--compare` adds the checker's own difference between two instances: `tiles: stages: 2 -> 3`.

### Device candidates and budgets

`--card CARD` prices the candidates on a [packaged card](#other-gpus) and takes the device target from it when nothing names one, so a search for a GPU this machine lacks compiles for that GPU and prices on its specification. `cairn tune examples/cooperative/tuned.toml --symbol row_totals --at rows=64,cols=1e5 --device-target sm_90a --card h100 --compare "use row_totals_tiled[128, 2]" --compare "use row_totals_tiled[256, 3]"` reads each instance's registers from ptxas for sm_90a and prices both on the H100 card; nothing runs.

Device candidates are then compiled for the [device target](#the-device-target) in predicted order. Among candidates priced alike, one whose kernel items (`unroll`, `vector`, `stage`, `fuse`) no earlier compile covered goes first. Nothing runs: ptxas and cuobjdump report registers, spilled bytes, stack, static shared memory and instructions, and a staged tile's shared memory is computed from the plan. Registers and shared memory enter the price through occupancy, and `chosen` is the candidate ranked best among those a compile read. Without a device target nothing is compiled, and the answer says so.

Each compile is kept by the digest of what it read: the emitted program, the runtime headers, the target, the toolkit and the inspector. A program already compiled costs nothing, and a kept reading for another target is refused (`E-TARGET-MISMATCH`). `resources.sass` digests the SASS. `block` and `per_lane` are arguments the host passes to the launch, so a candidate that differs from one already read only in them, or in the host items, compiles to the same kernels. It takes that reading with its own staged tile, and `resources.same_kernels_as` names where the reading came from.

```text
$ cairn tune blur.cairn --symbol blur --at n=1e7 --budget-compiles 4
"space":  {"configurations": 240, "checked": 240, "legal": 160, "same_code": 0,
           "refused": [{"code": "E-PLAN", "message": "stage loads one region's tiles; fuse and vector reshape the region, and a plan takes one.", "configurations": 80, "example": {"vector": 2, "stage": 1}}]}
"candidates": [{"plan": "(no plan for blur)", "predicted_ns": 113000.0, "predicted_ns_at": [113000.0],
                "resources": {"registers": 12, "instructions": 48, "sass": "3ff854f6b67fbce1", "kept": false, ...}},
               {"plan": "plan blur { block 64; }", "block": 64, "predicted_ns": 113000.0, ...,
                "resources": {"registers": 12, "instructions": 48, "sass": "3ff854f6b67fbce1", "kept": true,
                              "same_kernels_as": "(no plan for blur)", ...}}, ...,
               {"plan": "plan blur { vector 2; }", "vector": 2, "predicted_ns": 113000.0, ...,
                "resources": {"registers": 30, "instructions": 232, "sass": "575ea1ab46835840", ...},
                "applies_to": {"vector": {"blur@9e54491e": {"width": 2, "arrays": ["out"]}}}}, ...]
"budget": {"compiles": {"allowed": 4, "started": 4, "kept": 0}, "seconds": {"allowed": 300.0, "spent": 39.64},
           "runs": {"allowed": null, "started": 0, "kept": 0}, "undone": {"not inspected: compile budget spent": 80}}
```

Four compiles read four kernels, and each reading answers the twenty candidates that differ only in `block` and `per_lane`, so 80 of the 160 have `resources`. `blur@9e54491e` names the region by a digest of its syntax, which survives an edit elsewhere, a comment or a reformat. `applies_to` says which regions a plan item changed and which arrays it chunked or tiled.

The budgets are `--budget-compiles` (4 by default), `--budget-seconds` for the whole search (300 by default) and `--budget-runs`. The seconds hold. A compile or a timed run gets the time left as its limit and is stopped there with every process it started (`stopped at the time budget`), and one is not started when less time is left than the shortest this search has seen. What a spent budget left undone is counted under `undone`, and a candidate no compile read has no `resources`.

### Measuring, comparing and the history

`--measure K` then times the `K` plans ranked best and the current one on this host, halving the field each round and giving the survivors more blocks. It reports how many pairs ran in the predicted order. On a busy machine two close plans are within noise. Device plans are timed only by the make targets the repository's owner runs. `make tune-device FILE=f.cairn SYMBOL=f AT=n=1e8` adds `--device`, holds the device lock, rests after each run and stops after 64 runs. `make calibrate-device` replaces the device profile's assumed figures with measured ones.

`--compare A --compare B` reports how plan `B` differs from plan `A` instead of searching. A plan is written as its items, `grain 1; lanes 8`, or as `none`, and `use g` adds the selection of the implementation `g`, which is then priced and compiled as `g`. A side without `use` is the reference. Each line is labelled as a compiler observation (ptxas, cuobjdump or the model, nothing run), a runtime measurement, a profiler observation, a hypothesis or a suggested experiment.

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

A register, spill or instruction count (in the code, not executed) is never given as the reason one plan is slower. At most it leads to a hypothesis, beside the experiment that would test it, and both go into the history. When a and b compile to the same SASS, the report says so and attributes a measured difference only to the launch or to noise. A measurement or a profile comes only from the history, while it holds for this function, contract, compiler and target, with the procedure or profiling run behind it. Nothing here profiles, and profiling stays apart from timing because a profiler replays kernels. A measured order the model did not predict is reported as such. `--artifacts` adds the path of every file behind the lines: the emitted program, the cubin, ptxas's log, the SASS and the record ids.

The search records into the [candidate history](agents.md#candidate-history) what it tried, what the checker or nvcc refused, what each compile read, and each run with its procedure. The history is `.cairn/history` beside the manifest unless `--history DIR` or `--no-history` says otherwise. A later search reuses what still holds: a kept compile is not repeated, nor a kept measurement of the same candidate, sizes and procedure. A measurement outranks the model. Candidates the history measured at every size are ordered among their own places by what was measured, with `measured_ns` in their rows, and `measured_order` says how many of their pairs ran in the predicted order. `--since TUNE.json` prints only the rows that changed since a saved answer, and counts the rest.

## cairn validate

```sh
cairn validate examples/implementations --symbol prefix_by4          # against the function it implements
cairn validate examples/implementations --symbol "prefix_by[16]"     # one instance of a parameterized one
cairn validate app --symbol total_lanes --policy policy.json         # tolerance, domain, budget, seed, probes
```

An [implementation](abstractions.md#implementations) is an alternative version of a function, `fn g(...) implements f when ...`, that a plan can select in place of `f`, the reference. `cairn validate` tests one implementation against its reference on inputs generated from what the compiler already knows: the signature, which `usize` parameters are extents, the `when`, the literal tiles the body indexes by (`4 * k` gives 4), the implementation's plan items and, for host lanes, the pool's cutoff. Extents come at each tile minus one, the tile, plus one, a partial second tile, zero, one and the domain's largest. Views come zeroed, all ones, ascending, at their type's largest value, alternating, random, and once one element off an aligned allocation. Scalars come at their type's edges, and the same seed gives the same cases.

An implementation with natural parameters is validated one instance at a time, since its condition and its tiles change with the values: `prefix_by[16]` gets tiles of 16, and naming `prefix_by` alone lists its instances. Each case runs the reference, the reference under `plan f use g;` (the dispatch, on every input) and, where the condition holds, the implementation, each call in a process of its own. Each call runs with its standard input and output on the null device, so nothing the code under test prints reaches the validator.

Two calls that both trap agree. Integers and bools agree bit for bit, and floats under the [numerical policy](verification.md#validating-an-implementation): a NaN agrees with any NaN, whatever its sign and payload; `-0.0` agrees with `0.0` only under a tolerance; an infinity agrees only with itself; and otherwise two values agree within `absolute + relative * |reference|`. A pass is [finite testing](verification.md#validating-an-implementation) on the cases that ran, never proof. A run in which no case ran, or no case of the implementation, is `unknown`.

A failing case is shrunk while it still fails: smaller extents with each view cut to its prefix, then aligned views, then each value toward zero. It is reported with what each side did, and kept in `regressions/<reference>.json` or in the file `--regressions` names, written the same on every run. Listed under the manifest's `tests`, that file makes `cairn test` replay every kept case against every implementation of the reference. A file whose reference has no implementation left, or that keeps no case, answers `unknown`.

```text
failed: prefix_blocks against prefix, 17 cases (5 ran it), finite-tested
  fails at n = 16, out = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], xs = [0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]; kept in regressions/prefix.json
  smt: unknown where ((((n % 8) == 0)) && (n >= 0 && n <= 4096)) && n <= 16
```

Apart from the finite result, `smt` asks Z3, as [`cairn verify`](verification.md#value-level-source-equivalence) would, whether the two bodies agree where the condition holds. A loop over an extent the domain lets past 16 is decided only up to 16, and the record says so. Anything outside the modeled fragment, such as a `scan` or a lane region, is `unknown`.

Z3 compares exactly, so its counterexample is replayed natively as one more case, under the policy's domain and tolerance, and `smt.replay` says what it did. `failed` fails the validation, with the shrunk input kept like any other. `within-policy` and `outside-domain` leave the finite result standing, and `unknown` makes the validation `unknown`. After a finite failure there is nothing to replay (`not-run`).

The policy sets `tolerance` (`absolute` and `relative`, 0 by default), `domain` (`largest_extent`, and `extents` or `values` ranges by parameter), `budget` cases, `seed`, shrinking `probes` and `seconds` per call. Without `--policy`, the one the regressions file pinned applies. The record names what it rests on: the implementation's `identity`, the digests of each library's C++ and build under `artifacts`, the `target`, the native `compiler` and its version, the numerical policy under `agreement` with its version and digest, and `coverage`, the cases generated, kept, run and left out with the reason. `--history DIR` keeps the result in that [candidate history](agents.md#candidate-history), as a `validation` record or a `failure` record with the shrunk input, under the implementation's identity, the policy's digests and the native compiler, where [`cairn tune`](#implementations) finds it.

A signature the validator cannot feed (records, owners, views of records) is `unknown`. So is device code without `--emulate`. Only `make gpu` runs device code on a device, under each Compute Sanitizer tool (`memcheck`, `racecheck`, `initcheck`, `synccheck`) as a result of its own. A tool that ran no test is `unknown`, and one that says it cannot instrument the device, as under WSL2 without the WDDM debugger interface, is `unavailable`, never clean. The device tests compare under the same policy and write every input exactly: a NaN, an infinity or `-0.0` by its bits, a view longer than 16 elements from a table of bit patterns, and a view one element off as a part one element into its buffer. A case they cannot write is counted in their coverage with the reason.

`--emulate` runs device code on host threads, judged against the [device target](#the-device-target) (`--device-target`, the manifest, or the GPU nvidia-smi reports), as [devices.md](devices.md#emulating-device-code-on-the-host) describes. A cooperative kernel that forgets its partial last tile fails at its shrunk input, which is kept like any other and which `cairn test --emulate` replays:

```text
failed: scale_tiles against scale, 3 cases (3 ran it), finite-tested on a host emulation of sm_120, not on a device
  fails at n = 1, out = [0], x = [1]; kept in regressions/scale.json
```

The record's `evidence` is then `finite-tested-emulated`, where a host validation's is `finite-tested`, and its `emulation` names the target. That is finite testing of the host emulation, never of the device. [numerics.md](numerics.md#emulated-device-runs) says where an emulated result can differ from a device run, and a `--history` record keeps it under the target `host emulation of sm_120`.

## cairn foreign

A foreign implementation is vendored C++ or CUDA that stands for a CAIRN function ([memory.md](memory.md#foreign-implementations) has the declarations). `cairn foreign` builds it, inspects it, validates it against its reference, and says what it has:

```sh
cairn foreign examples/foreign/host --implementation histogram_interleaved
cairn foreign examples/foreign/device --implementation stencil_tiled --device-target sm_120
```

```text
histogram_interleaved implements histogram_u32 (vendor/histogram.cpp)
  contract        declared, not checked: ffi:histogram_u32_interleaved, ffi_precondition, read:x, trap, write:out
  native build    native-built: clang++, g++
  device          not-applicable: a C++ source has no device code
  finite-tested   clang++: passed, 256 cases
  finite-tested   g++: passed, 256 cases
stencil_tiled implements stencil_1d (vendor/stencil.cu)
  contract        declared, not checked: ffi:stencil_1d_tiled, ffi_precondition, par:device, read:x, trap, write:out
  native build    native-built: clang++, g++
  device          stencil_1d_tiled(unsigned long, float *, const float *): 11 registers, 1032 B shared, 0 B spilled, 0 B stack
  finite-tested   not run: it runs device code, and device code runs only under make gpu; its 12 device tests are native-built
```

Each line is its own claim. The contract is the externs' declared rows, which nothing checks against the source. The build compiles the vendored files unchanged with the program's command line and device target, and fails if a symbol's C++ types differ from what its extern passes. The device line is what ptxas reports when the source is compiled for the device target, and nothing is launched.

The finite tests are [cairn validate](#cairn-validate)'s, with the vendored objects linked into both libraries it builds, under each compiler. They are finite testing, and a failure names the shrunk input. The JSON record, `cairn.foreign/1`, adds the implementation's identity, each source's sha256 and every kernel of every CUDA source. The command exits 1 when a build or a test failed.

## cairn diff

`cairn diff OLD NEW` says what changed between two versions of a program, function by function, and what establishes each answer. A side is a path or a git revision, and `--in PATH` names the project inside the repository. A revision is read into a scratch directory, never checked out over the working tree. [demos/repair](../demos/repair/README.md) shows it reviewing an agent's fix.

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

Beside the class, each function lists how its signature, effect row, guards, allocations, tasks and `unsafe` blocks changed. The semantic version is `major` for a removed, renamed or re-signed public function, a public function that gained an effect or has a witness, or a changed public type. It is `minor` for an added one, and `patch` otherwise. An `unknown` public function makes the level `unknown`, with what is proven under `at_least`, unless the level is already `major`.

`--require equivalent` exits 1 unless every function is identical or `smt-equivalent`, and `--require identical` refuses `smt-equivalent` too. `--markdown FILE` writes a section for a pull request. `--replays N` replays at most `N` witnesses natively (8 by default), `--no-predict` leaves out the predicted change in cost, and `--std` compares the packaged library. The solver has `--timeout-ms` per function (3000 by default) and `--budget-s` for the whole diff (60 s by default). Each function runs in a process that is stopped at its limit, because Z3 cannot be interrupted while it reads a large query. Both versions are lowered by this compiler, so the diff compares two sources, never two compilers. `evidence/v1_0/diff/` records a diff of the library and every example project across two revisions.

## cairn export

`cairn export PATH --out DIR` writes the program `cairn build` would compile into a new directory: the generated C++ (`program.cu` for a device program), exactly the runtime headers it includes, the C header with `--kind library --header`, and `export.json`. That record holds the command line, the device target, each compiler's path and version, a sha256 per file, each function's canonical emission, and one identity over all of it. The device target is judged as `cairn build` judges it, so a target the build refuses (`E-IMPL-TARGET`, `E-TARGET-FEATURE`, `E-ASM-TARGET`) is refused before anything is written.

```sh
cairn export examples/systems --out out/systems     # the program and its record
cairn export out/systems                            # {"status": "export-intact", "identity": "97db...", "files": 2}
cairn build out/systems                             # the recorded command, in a fresh copy under out/systems/build/
cairn run out/systems
```

`build`, `run` and `test` take the export directory itself. Each refuses a file that fails its hash, was added or was removed, and a record whose identity no longer covers it (`E-EXPORT-TAMPERED`). It then builds with exactly the recorded command and compilers, and refuses a compiler here that is another version (`E-EXPORT-TOOLCHAIN`). Every record they write carries the export's identity, so a later rewrite shows. `--tests` exports the test blocks' program, which `cairn test DIR` runs one process per test. `--time f --at n=1e6` exports `f` beside the timing driver of `cairn tune --measure`, so `cairn run DIR` measures exactly the exported code. A device export builds here, and runs only under the make targets the repository's owner runs.

An export is data, never a build script. The identity is a digest anyone can recompute; it is not a signature. A build therefore runs only the command `toolchain.py` gives for the record's kind, architecture, device target and libraries, with clang++ or g++ and nvcc found on this machine's `PATH` by name. A recorded command it would not give is `E-EXPORT-TAMPERED`, and a compiler the record names by a path the `PATH` does not give is `E-EXPORT-TOOLCHAIN`. An export directory cannot bring the program that builds it.

The record names each runtime header's role in a device export. The device implementation is `cairn_kernels.hpp` and the guards a lane calls, whose kernels launch on a stream the caller names, with no execution context. The launch wrappers are `cairn_gpu.hpp`, `cairn_exec.hpp` and `cairn_reuse.hpp`, with `cairn_cub.hpp` in a program with a device `reduce`, `scan` or `compact`. An application can point them at its own stream (`NAME_device_stream`) or replace them with another machine, as the suite's host machine does.

`cairn export DIR --compare OTHER` says whether two exports are the same code: each function's canonical emission, as `cairn diff` compares it, each runtime header, the command, the compilers and the target. It exits 1 when they differ, so a check can hold back a change to a known fast implementation. The same code is not the same speed: compare timings only between exports built alike, with the same `--time` harness, on the same machine.

## Large projects and Bazel

A program may hold 16 MB of source, 32,768 functions and 3,200,000 syntax nodes. Past one of those limits the compiler refuses the program with `E-SOURCE-LIMIT` or `E-EXPANSION-LIMIT` and names what it counted. What code generates stays small: a family makes at most 1,024 copies (`E-FAMILY-LIMIT`), a program's families 2,048 in all, and a recipe 2,048 declarations (`E-EXPANSION-LIMIT`). A manifest may list 1,024 source files and 64 dependencies; past that the manifest is refused with `E-PROJECT-OR-ENVIRONMENT`.

Effect rows are a fixed point over the call graph, so a check, an editor refresh and an incremental build each run the front end over every module, in time about linear in the program's size. On a generated project of 77,000 lines a check took 11 s, an editor refresh 12 s, and an incremental rebuild after an edit inside one body 13 s. A project of 31,802 functions checked in 33 s at 800 MiB (`evidence/v1_0/scale`, on a loaded machine; `make scale` measures it again). An incremental build of 16 or more units precompiles the shared header, which made cold rebuilds and rebuilds after an interface edit about three times faster.

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

`examples/bazel` is that workspace, where `bazel build //...`, `bazel run //:shop` and `bazel test //...` work with nothing fetched. Its `MODULE.bazel` names this checkout with `cairn.local(path = "../..")`; without it the rules run the `cairn` on `PATH`. The rules use the host's Python and C++ compiler and bring no hermetic toolchain. A checkout's `bin/cairn` runs under the `python3` on the `PATH` Bazel was started with, which must be 3.11 or later, because the `PATH` Bazel gives each action may name an older one, as Ubuntu 22.04's does. Each action copies its sources into a fresh directory with a manifest, because a project refuses a source that is a symbolic link, which is how Bazel lays out inputs.

## The device target

A program that indexes `@device` views is built for one device target. The target is spelled as nvcc spells it, and is separate from the CPU architecture that `--arch` names. `sm_120` runs on compute capability 12.0 and every later 12.x device. `sm_120f` adds the features the family shares and runs on its devices from 12.0. `sm_120a` adds every feature of exactly 12.0 and runs only there.

`--device-target` on `build`, `run`, `predict` and `tune` names the target. Without it the target is `[build] device_target` of the manifest; else, for `predict` and `tune`, the target of the [card](#other-gpus) `--card` names; else the one GPU `nvidia-smi` reports, which asks the driver and launches nothing. With none of these a device build is refused with `E-TARGET`. Nothing defaults to `-arch=native`.

```toml
[build]
kind = "exe"
device_target = "sm_120a"
```

The target is resolved once, and every stage receives the same one: nvcc's `-arch`, the kernel reader behind `cairn tune`, the device card `cairn predict` prices on, a device timing and a measured device profile. The build receipt records it under `device_target`: its name, how it was resolved, the features it provides and those the program needs, its resource limits and the nvcc release.

nvcc runs the host half of a device build with the `--cxx` compiler, clang++ by default, and each CUDA release accepts a range of host compilers. CUDA 12.9 takes GCC up to 14 and Clang up to 19, and CUDA 13.2 takes GCC up to 15 and Clang up to 21. Where the default clang++ is newer than the toolkit accepts, name another with `--cxx`. CI builds every device test and every device example with g++ and with clang++ as nvcc's host compiler, under CUDA 13.2 on every pull request and under CUDA 12.9 as well on `main` and weekly ([internals.md](internals.md#continuous-integration)).

| Refused | Code |
|---|---|
| a spelling other than `sm_` and a compute capability with an optional `f` or `a`, `a` below sm_90 or `f` below sm_100, and GPUs of two capabilities with no target named | `E-TARGET` |
| a target the installed nvcc does not compile | `E-TARGET-TOOLKIT` |
| a program needing a feature the target lacks: `bf16` on sm_75, `mma_f8f6f4` on plain sm_120, `tcgen05` on any sm_120 | `E-TARGET-FEATURE` |
| a result recorded for another target: a ptxas report, a timing, a measured device card, or a card for a device the target's code does not run on | `E-TARGET-MISMATCH` |

The features are `FEATURES` in `src/cairn/projects/target.py`. `tests/tooling/test_target.py` assembles one probe instruction per feature for each of sixteen targets the installed nvcc compiles, and holds the table to what ptxas accepts. The limits (registers per thread, shared memory per block and per SM, threads per block, warps per SM) are the CUDA Programming Guide's for 7.5, 8.0, 8.6, 8.7, 8.9, 9.0, 10.0, 10.3, 10.7, 11.0, 12.0 and 12.1. They are a specification that nothing here measured, and every packaged card is held to its row. A target without a row has unknown limits, and its record says so.

`--emulate` on `build`, `run` and `test` judges the program against the target and then builds it for the host, with its device work on host threads ([devices.md](devices.md#emulating-device-code-on-the-host)). nvcc does not run, and what the host cannot run as the device would is refused with `E-EMULATE`.

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

`cairn run` reports the UART output as `stdout` and the emulator's exit status as `exit_code`. `examples/embedded/trap/` reads one element past an array of four:

```json
{"status": "program-exited", "exit_code": 134,
 "stdout": "trap demo: reading window[4] of 4\n",
 "emulator": ["/usr/bin/qemu-system-aarch64", "-M", "virt", "-cpu", "cortex-a72",
              "-nographic", "-semihosting", "-kernel", ".../trapdemo.elf"]}
```

The language works as it does hosted, including guards, `stack` storage, records, sums, generics, traits, closures, `compact`, `reduce`, `derive wire`, `f32` and `f64`. The build refuses, by name and before a compiler runs, any function whose effect row needs a hosted runtime: `alloc`, `free`, `io`, `gpu_*`, `transfer:*`, `par:*` or `ffi:*`. `mmio_read`, `mmio_write` and `asm` stay available inside `unsafe`.

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

The profile has no allocator (storage is `stack` arrays, statics and string views), no concurrency, device, MMU, caches, interrupts or timer, and no vector table, so a hardware exception hangs the machine. It has no unwinding, so `defer` and owner release do not run on a trap, as with a hosted abort. There is no cross compilation: a target is refused on a host of another family. The image runs with the MMU off, so every access is Device-nGnRnE memory, the build passes `-mstrict-align`, and its timings are not comparable to hosted ones. The backend is not verified.

### The aarch64-virt target

QEMU's `virt` machine with a Cortex-A72.

| Address | What |
| --- | --- |
| `0x0900_0000` | PL011 UART0 data register; `0x0900_0018` is the flag register, bit 5 = TX FIFO full |
| `0x4000_0000` | DRAM base, 128 MiB by default |
| `0x4008_0000` | image load address: `.text`, `.rodata`, `.data`, `.bss` |
| `0x4100_0000` | stack top, growing down; 16 MiB clear of the image and of the device tree QEMU writes immediately past it |

`src/cairn/targets/aarch64-virt/start.S` sets `sp`, zeroes `.bss`, enables FP and SIMD, calls `cf_main` and exits through ARM semihosting `SYS_EXIT`, which QEMU turns into its own exit status. PSCI `SYSTEM_OFF` would always exit 0, so a trap could not be told from success. The build's full command line is in its receipt. `cairn run` runs exactly this:

```sh
qemu-system-aarch64 -M virt -cpu cortex-a72 -nographic -semihosting -kernel <image>.elf
echo $?      # fn main()'s return value, or 134 for a failed guard
```

`evidence/v0_8_0/embedded/` holds a captured transcript, `size`, `nm` and the tool versions.

### Adding a target

1. Create `src/cairn/targets/<name>/` with `link.ld` and `start.S`. The start-up code owns the stack, `.bss`, the call to `extern "C" cf_main()`, `memset`, `memcpy`, and `extern "C" [[noreturn]] void cr_exit(int)`, which must carry the status out so a trap (134) can be told from any value `main` returns.
2. Add a row to `TARGETS` in `src/cairn/projects/toolchain.py` with the host `family`, the `-march` `arch`, extra `flags`, and the `run` command.
3. Add the files to `package-data` in `pyproject.toml` if the glob misses them, and extend `tests/projects/test_freestanding.py`.

Nothing else in the compiler knows about targets.
