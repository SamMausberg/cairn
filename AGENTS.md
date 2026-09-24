# Working on CAIRN

Read README.md, then [docs/language.md](docs/language.md) and the architecture section of [docs/internals.md](docs/internals.md). Start with `python3 bin/cairn doctor`, `make lint` and `python3 -m pytest -q tests -n 4`. Use the existing Python API and real CAIRN source; do not invent unsupported libraries or syntax.

**Ownership of the code.** Keep every change small and in the file that owns the rule. Paths are under `src/cairn/`.

| File | Owns |
|---|---|
| `compiler/lexing.py` | tokens and reserved words |
| `compiler/tree.py` | the syntax tree, the scalar vocabulary, `Diagnostic` |
| `compiler/syntax.py` | the parser's declarations and entry point, and source ranges |
| `compiler/syntax_expressions.py`, `compiler/syntax_statements.py` | the parser's token cursor, types and expressions; its statements |
| `compiler/modules.py` | linking the packaged `std` modules |
| `compiler/expansion.py` | library recipes applied by `derive`, and static families |
| `compiler/gradients.py` | reverse-mode differentiation applied by `derive grad`, generated as source |
| `compiler/checking.py` | names, types, generic instances, the whole-program judge, the walk over bodies |
| `compiler/scope.py` | what the checker knows inside one function and one region |
| `compiler/statements.py` | statement rules: declarations, control flow, `match`, loops, `defer`, collectors |
| `compiler/expressions.py` | expression rules: literals, names, indexing, fields, variants, closures, `try`, operators |
| `compiler/calls.py` | calls, generic instances at the call, function values, arguments, record construction and declared extents |
| `compiler/places.py` | places, ownership, leases, aliasing |
| `compiler/concurrency.py` | tasks and tickets, lanes and regions, atomics and mutexes, placement |
| `compiler/fusion.py` | which adjacent regions a plan's `fuse` may run as one, and the scratch they need not keep |
| `compiler/chunks.py` | which arrays a plan's `vector` moves a chunk at a time in a device region, and that lowering |
| `compiler/staging.py` | which arrays a plan's `stage` loads into a device block's shared tile, and that lowering |
| `compiler/cooperative.py` | cooperative regions (`blocks ... threads ...`): their shape, shared arrays, barriers and warp operations, who reaches a statement together, and their lowering |
| `compiler/phases.py` | the phase rule: between two barriers no two threads of a block touch one shared element where either writes |
| `compiler/footprints.py` | index polynomials, and the rule that each element of an array from outside a cooperative region has one writer |
| `compiler/pipelines.py` | pipeline stages in a cooperative region: their declaration, the states fill, wait and release move them through, their lowering |
| `compiler/tensor.py` | the tensor-core multiply `mma_unordered`: its rule, its numerical contract and its lowering |
| `compiler/fragments.py` | tensor-core fragments: their types, the warp operations on them, the layouts each family reads, their lowering |
| `compiler/layouts.py` | `layout` declarations: their evaluation and rules, their receipt, and `L.at(...)` in code with its lowering |
| `compiler/layout_algebra.py` | storage layouts and spreads as values: offsets, owners, coverage, distinct offsets, runs, bank conflicts, conversions |
| `compiler/rings.py` | I/O rings: their declaration, the operations that move owners in and out, their lowering |
| `compiler/implementations.py` | alternative implementations of a function: their declaration, condition, contract, selection by a plan and the dispatch that lowers it |
| `compiler/effects.py` | the effect vocabulary, the fixed point, the operand-order audit |
| `compiler/traits.py` | who implements what, what a bound promises, the one place an instance is made |
| `compiler/constants.py` | constant folding |
| `compiler/facts.py` | what the checker established about `usize` values, which lowering uses to drop a guard |
| `compiler/builtins.py` | every primitive's rule, beside its lowering |
| `compiler/machine.py` | the machine: `mmio_read`, `mmio_write`, `asm` and typed assembly, their rules and target requirements beside their lowering |
| `compiler/launches.py` | an `extern` CUDA kernel's `launch(threads, block)`: its rule beside its lowering |
| `compiler/printing.py` | `print`, `println`, `eprint`, `eprintln` and `format`: what each argument writes, and their lowering |
| `compiler/codegen.py` | lowering the typed tree; nothing else produces C++ |
| `compiler/region_lowering.py` | the lowering of `parallel`, `reduce`, `scan` and `compact` on host or device lanes, and of a fused chain as one region |
| `compiler/execution.py` | which runtime operation each piece of device work lowers to, on the calling thread's execution context |
| `compiler/header.py` | the C header of a library build: its declarations, the layouts it states, what cannot cross |
| `runtime/*.hpp` | guards, owners, threads, rings, storage floats, the tensor-core multiply, device calls, execution contexts |
| `projects/project.py` | manifests, vendored dependencies, the line-to-file map |
| `projects/toolchain.py` | every native flag, and the closed table of system libraries |
| `projects/target.py` | the device target: its spelling, how it is resolved, the features and limits it has, and the results it refuses |
| `projects/export.py` | an export: the program a build compiles and the record pinning it, and the builds, runs, tests and comparisons that take it |
| `projects/foreign.py` | vendored C++ and CUDA a manifest's `[foreign]` names: built by the project's command line, held to each extern's types, inspected |
| `projects/revision.py` | a program as a path or a git revision holds it |
| `projects/new.py` | what `cairn new` writes: the default project or a packaged template, and the AGENTS.md each gets |
| `cli.py`, `commands.py` | the command line: `main` runs each command; `commands.py` declares every command and option it parses |
| `agent/agent_tools.py` | edit sessions, packets and the host that names them by handle |
| `agent/evidence.py` | what a packet may say is established about a function |
| `agent/history.py` | what was tried, failed, measured or hypothesized for each candidate, under its identity, and analyses kept by key |
| `agent/investigation.py` | one function's investigation as a compact packet, from the history records that still hold |
| `agent/migration.py`, `agent/plans.py` | the two wider edit classes: interface migrations across files, and plan-only edits |
| `agent/implementations.py` | implementation sessions: a pinned reference, tolerance, test policy and inputs, and new implementations admitted only when they validate |
| `agent/teaching.py` | the rule cards, selected from lexical tokens |
| `agent/skill.py` | the agent skill under `skills/cairn/`, written from the cards, the fixes and the command line |
| `agent/mcp.py`, `agent/mcp_tools.py` | `cairn mcp`: the Model Context Protocol over stdio, and the tools it serves from the hosts |
| `agent/write_back.py` | a change a host admitted written back to the files a session read, only while they hold what it was judged against |
| `editor/grammar.py` | the editor grammars, generated from the compiler's vocabulary |
| `editor/terminal.py` | what a person at a terminal reads, beside the JSON record |
| `perf/work.py` | what a function does each time it runs, counted from the typed tree, which a prediction prices |
| `perf/counts.py` | what a count is: polynomials in a function's extents, and the work, region and cost records the counting fills |
| `perf/cooperative_work.py`, `perf/cooperative_model.py` | what a cooperative region's threads do, counted as their warps run it from the phase rule's run of one block; and its price: the blocks an SM holds, a pipeline's copies in flight, a launch and four rates |
| `perf/model.py`, `perf/profile.py` | a predicted time, and what one machine can do |
| `perf/on_device.py` | the only device timing, under the owner's make targets |
| `perf/plan_source.py` | a function's plan as source text: the plans the checker resolves to it, and where a new one is written |
| `perf/regions.py` | names for a function's parallel regions that survive edits which do not touch them |
| `perf/search.py`, `perf/tune.py` | the bounded search over a function's plans: the space, what the checker accepts, the budgets, measurement |
| `perf/resources.py` | what a compiled candidate uses on the device, for one target, kept by what was compiled |
| `perf/feedback.py` | the difference report between two candidates, each line labelled by the kind of evidence it is |
| `verify/elision.py` | the independent check of every guard lowering leaves out |
| `verify/diff.py`, `verify/emission.py` | the class each function of two versions gets, and when two emissions are the same code |
| `verify/foreign.py` | what a foreign implementation has: its declared contract, build, device inspection and validation |
| `verify/runner.py` | test blocks, each run in a process of its own |
| `verify/boundaries.py`, `verify/validation.py`, `verify/isolated_calls.py` | contract-driven validation: boundary inputs from an implementation's contract, each call in a process of its own against the reference, shrinking and kept regressions |
| `verify/device_validation.py` | the device side of validation: generated device tests under each Compute Sanitizer tool, only in `make gpu` |
| `verify/scalar_values.py` | the value model: leaves, what the guard admits, what a caller observes, reading a model back |
| `verify/scalar_symbolic.py` | the SMT translator |
| `verify/scalar_concrete.py` | the concrete replay |
| `verify/scalar_semantics.py` | the query, the counterexample check and the receipt |

Project manifests are data, never build scripts. The formatter owns layout: `ruff format` at 120 columns, `cairn fmt` for `.cairn`. The smallest clear program wins, never by hiding a cost or deleting a check. No source file is longer than 800 lines; split by responsibility, under a name that says what the piece owns.

**Do not weaken** a task, reference, input domain, numerical policy, alias rule, lease, lane rule, effect ceiling or test to make a candidate pass. Checked and wrapping arithmetic differ. Borrows are second class: a design that needs a stored or returned borrow needs an owner, an index or a handle. An owner moves; `take` and `swap` are the only ways out of a place. A linear value is consumed exactly once on every path. A lane touches only element `[i]` of what any lane writes. While a ticket is live its borrows are leased. `unsafe` is for the foreign boundary and the machine, never for silencing the checker.

**Keep claims distinct**: accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are different statements, and unknown is never success. The Lean result covers the hand-written models [docs/verification.md](docs/verification.md#the-lean-project) lists, none extracted from `compiler/`; the receipt turns it off when the bundle changes, so regenerate with `tools/checks/export_lean_certificates.py` and rebuild `proofs/` when you touch a rule. Do not claim a speedup, GPU advantage or AI proficiency without executed evidence recorded under `evidence/`.

**Every language change needs** all of these before it lands:

- a precise elaboration and a failure policy;
- a cost boundary, meaning which effects it adds;
- a rejection test naming its diagnostic code;
- an independent behavior test run natively under both compilers, with the sanitizer that bites (address and leak for ownership, thread for concurrency, the device death tests for guards in lanes);
- a rule card in `agent/teaching.py`, selected from lexical tokens;
- an entry in the reference file that owns it (`docs/language.md`, `docs/memory.md`, `docs/abstractions.md`, `docs/concurrency.md`, `docs/devices.md` or `docs/numerics.md`) and in `docs/project/capabilities.json`;
- the canonical projection still round-tripping to identical native code.

Count whole compiler dependencies in density measurements, not a facade alone.

**Never** commit credentials, binaries, build trees or unrelated user files. Never change repository visibility, force-push, delete remote resources, install a token or publish from a test. Publication tests use fakes; the private publisher is opt-in and creates only a new personal repository. A task whose child exits abnormally has failed, even if it printed a pass.

## Development

Work on a branch, one focused change at a time. Run the fast suite before and after you touch code, and both native compilers with the relevant sanitizers when you touch the runtime or the lowering. Never run code on the GPU outside `make gpu`, never set `CAIRN_GPU_TESTS` yourself, and never start `make gpu` while another agent may: repeated device runs have crashed the host. Several agents on one machine share its cores, so give pytest at most four workers each.

Source belongs in `src/cairn`, tests in `tests`, real programs in `examples`, and generated results under `results/`, which is not tracked. Do not reimplement a compiler rule in a script. `implementation_hash()` in `src/cairn/verify/scalar_semantics.py` lists the files a semantic receipt is pinned to; add a new parser, checker or emitter file to that list. Prefer removing repeated boilerplate to adding opaque punctuation, and do not shrink a source-token measurement by excluding semantics the program imports.

Commit messages are one short, plain sentence saying what is now true, with a body only when a reviewer needs it, and no attribution trailer. The project is licensed MIT or Apache-2.0 at the recipient's option, and every contribution is accepted under the same terms. Publication to a package index or a change of hosting is an explicit owner action, never a side effect of a test or a script.

## Writing documentation

The documentation is [docs/](docs/README.md): fourteen files, one per subject, and the language reference is six of them. Add to the file that owns the subject rather than starting one, keep every file under 800 lines, and update the index when a file is added. README.md says what CAIRN is and how to start it, and nothing that a docs file already says. `docs/std_api.md` and the module pages under `docs/std/` are generated by `make docs` and are never edited by hand.

Write the way a careful engineer explains something at a whiteboard: plain present-tense sentences, paragraphs of two to four, one idea per sentence. Lead with what the reader can do, then the rule, then the reason. Cut words, never facts, limits or diagnostic codes, and keep `accepted`, `typed`, `native-built`, `finite-tested`, `sanitizer-clean`, `SMT-equivalent`, `Lean-checked` and `benchmarked` apart, since unknown is never success. Show a short real example instead of describing syntax. No emojis, no em dashes, no filler openers, no title-case headings, no bullet list of paragraphs; a table is for tabular data. One paragraph per line, never hard-wrapped.

`tests/language/test_docs_examples.py` compiles every fenced example in README.md and under `docs/`. A block tagged `cairn` must be accepted; `cairn rejects E-CODE` must be refused with exactly that code; `cairn fragment` is not compiled, so use it rarely. Shell sessions are `sh`, compiler output is `text` or `json`. Check an example with `python bin/cairn check FILE` before you keep it. The twelve programs of the tour in `docs/guide.md` are also built and run by `tests/language/test_tour.py`.
