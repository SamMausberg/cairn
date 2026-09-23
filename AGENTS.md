# Working on CAIRN

Read README.md, then [docs/language.md](docs/language.md) and the architecture section of [docs/internals.md](docs/internals.md). Start with `python3 bin/cairn doctor`, `make lint` and `python3 -m pytest -q tests -n auto`. Use the existing Python API and real CAIRN source; do not invent unsupported libraries or syntax.

**Ownership of the code.** Keep every change small and in the file that owns the rule. Paths are under `src/cairn/`.

| File | Owns |
|---|---|
| `compiler/lexing.py` | tokens and reserved words |
| `compiler/tree.py` | the syntax tree, the scalar vocabulary, `Diagnostic` |
| `compiler/syntax.py` | the parser and source ranges |
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
| `compiler/tensor.py` | the tensor-core multiply `mma_unordered`: its rule, its numerical contract and its lowering |
| `compiler/rings.py` | I/O rings: their declaration, the operations that move owners in and out, their lowering |
| `compiler/effects.py` | the effect vocabulary, the fixed point, the operand-order audit |
| `compiler/traits.py` | who implements what, what a bound promises, the one place an instance is made |
| `compiler/constants.py` | constant folding |
| `compiler/facts.py` | what the checker established about `usize` values, which lowering uses to drop a guard |
| `compiler/builtins.py` | every primitive's rule, beside its lowering |
| `compiler/printing.py` | `print`, `println`, `eprint`, `eprintln` and `format`: what each argument writes, and their lowering |
| `compiler/codegen.py` | lowering the typed tree; nothing else produces C++ |
| `compiler/header.py` | the C header of a library build: its declarations, the layouts it states, what cannot cross |
| `runtime/*.hpp` | guards, owners, threads, rings, storage floats, the tensor-core multiply, device calls, execution contexts |
| `projects/project.py` | manifests, vendored dependencies, the line-to-file map |
| `projects/toolchain.py` | every native flag, and the closed table of system libraries |
| `projects/revision.py` | a program as a path or a git revision holds it |
| `agent/agent_tools.py` | edit sessions, packets and the host that names them by handle |
| `agent/evidence.py` | what a packet may say is established about a function |
| `agent/migration.py`, `agent/plans.py` | the two wider edit classes: interface migrations across files, and plan-only edits |
| `agent/teaching.py` | the rule cards, selected from lexical tokens |
| `editor/grammar.py` | the editor grammars, generated from the compiler's vocabulary |
| `editor/terminal.py` | what a person at a terminal reads, beside the JSON record |
| `perf/work.py` | what a function does each time it runs, counted from the typed tree, which a prediction prices |
| `perf/model.py`, `perf/profile.py` | a predicted time, and what one machine can do |
| `perf/on_device.py` | the only device timing, under the owner's make targets |
| `verify/elision.py` | the independent check of every guard lowering leaves out |
| `verify/diff.py`, `verify/emission.py` | the class each function of two versions gets, and when two emissions are the same code |
| `verify/runner.py` | test blocks, each run in a process of its own |
| `verify/scalar_values.py` | the value model: leaves, what the guard admits, what a caller observes, reading a model back |
| `verify/scalar_symbolic.py` | the SMT translator |
| `verify/scalar_concrete.py` | the concrete replay |
| `verify/scalar_semantics.py` | the query, the counterexample check and the receipt |

Project manifests are data, never build scripts. The formatter owns layout: `ruff format` at 120 columns, `cairn fmt` for `.cairn`. The smallest clear program wins, never by hiding a cost or deleting a check. No source file is longer than 800 lines; split by responsibility, under a name that says what the piece owns.

**Do not weaken** a task, reference, input domain, numerical policy, alias rule, lease, lane rule, effect ceiling or test to make a candidate pass. Checked and wrapping arithmetic differ. Borrows are second class: a design that needs a stored or returned borrow needs an owner, an index or a handle. An owner moves; `take` and `swap` are the only ways out of a place. A linear value is consumed exactly once on every path. A lane touches only element `[i]` of what any lane writes. While a ticket is live its borrows are leased. `unsafe` is for the foreign boundary and the machine, never for silencing the checker.

**Keep claims distinct**: accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are different statements, and unknown is never success. The Lean result covers the certificate checker, the seventeen collector certificates, the collector loop model and a core ownership/lease calculus over locals, record field paths, whole owners, headers, elements, array parts with visible bounds and parallel regions (`proofs/Cairn/Places.lean` and `proofs/Cairn/Ownership/`, hand-written, not extracted from `compiler/places.py` and its siblings); the receipt turns it off when the bundle changes, so regenerate with `tools/checks/export_lean_certificates.py` and rebuild `proofs/` when you touch a rule. Do not claim a speedup, GPU advantage or AI proficiency without executed evidence recorded under `evidence/`.

**Every language change needs** all of these before it lands:

- a precise elaboration and a failure policy;
- a cost boundary, meaning which effects it adds;
- a rejection test naming its diagnostic code;
- an independent behavior test run natively under both compilers, with the sanitizer that bites (address and leak for ownership, thread for concurrency, the device death tests for guards in lanes);
- a rule card in `agent/teaching.py`, selected from lexical tokens;
- an entry in the reference file that owns it (`docs/language.md`, `docs/memory.md`, `docs/abstractions.md`, `docs/concurrency.md` or `docs/numerics.md`) and in `docs/project/capabilities.json`;
- the canonical projection still round-tripping to identical native code.

Count whole compiler dependencies in density measurements, not a facade alone.

**Never** commit credentials, binaries, build trees or unrelated user files. Never change repository visibility, force-push, delete remote resources, install a token or publish from a test. Publication tests use fakes; the private publisher is opt-in and creates only a new personal repository. A task whose child exits abnormally has failed, even if it printed a pass.

## Development

Work on a branch, one focused change at a time. Run the fast suite before and after you touch code, and both native compilers with the relevant sanitizers when you touch the runtime or the lowering. Never run code on the GPU outside `make gpu`, never set `CAIRN_GPU_TESTS` yourself, and never start `make gpu` while another agent may: repeated device runs have crashed the host. Several agents on one machine share its cores, so give pytest at most four workers each. Accepted examples are not evidence on their own: every rule needs a rejection test naming its diagnostic code and an independent behaviour oracle.

Source belongs in `src/cairn`, tests in `tests`, real programs in `examples`, and generated results under `results/`, which is not tracked. Do not reimplement a compiler rule in a script. `implementation_hash()` in `src/cairn/verify/scalar_semantics.py` lists the files a semantic receipt is pinned to; add a new parser, checker or emitter file to that list. Prefer removing repeated boilerplate to adding opaque punctuation, and do not shrink a source-token measurement by excluding semantics the program imports.

Commit messages are one plain sentence saying what is now true, with no attribution trailer. The project is licensed MIT or Apache-2.0 at the recipient's option, and every contribution is accepted under the same terms. Publication to a package index or a change of hosting is an explicit owner action, never a side effect of a test or a script.

## Writing documentation

The documentation is [docs/](docs/README.md): thirteen files, one per subject, and the language reference is five of them. Add to the file that owns the subject rather than starting one, keep every file under 800 lines, and update the index when a file is added. README.md says what CAIRN is and how to start it, and nothing that a docs file already says. `docs/std_api.md` and the module pages under `docs/std/` are generated by `make docs` and are never edited by hand.

Write the way a careful engineer explains something at a whiteboard: plain present-tense sentences, paragraphs of two to four, one idea per sentence. Lead with what the reader can do, then the rule, then the reason. Cut words, never facts, limits or diagnostic codes, and keep `accepted`, `typed`, `native-built`, `finite-tested`, `sanitizer-clean`, `SMT-equivalent`, `Lean-checked` and `benchmarked` apart, since unknown is never success. Show a short real example instead of describing syntax. No emojis, no em dashes, no filler openers, no title-case headings, no bullet list of paragraphs; a table is for tabular data. One paragraph per line, never hard-wrapped.

`tests/language/test_docs_examples.py` compiles every fenced example in README.md and under `docs/`. A block tagged `cairn` must be accepted; `cairn rejects E-CODE` must be refused with exactly that code; `cairn fragment` is not compiled, so use it rarely. Shell sessions are `sh`, compiler output is `text` or `json`. Check an example with `python bin/cairn check FILE` before you keep it. The twelve programs of the tour in `docs/guide.md` are also built and run by `tests/language/test_tour.py`.
