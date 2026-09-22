# Working on CAIRN

Read README.md, then [docs/language/](docs/language/README.md) and [docs/architecture.md](docs/architecture.md). Start with `python3 bin/cairn doctor`, `make lint` and `python3 -m pytest -q tests -n auto`. Use the existing Python API and real CAIRN source; do not invent unsupported libraries or syntax.

**Ownership of the code.** Keep every change small and in the file that owns the rule. Paths are under `src/cairn/`.

| File | Owns |
|---|---|
| `compiler/syntax.py` | syntax and source ranges |
| `compiler/modules.py` | linking the packaged `std` modules |
| `compiler/expansion.py` | library recipes applied by `derive`, and static families |
| `compiler/checking.py` | names, types, generic instances, places, ownership, leases, lanes, placement |
| `compiler/effects.py` | the effect vocabulary, the fixed point, the operand-order audit |
| `compiler/traits.py` | who implements what, what a bound promises, the one place an instance is made |
| `compiler/constants.py` | constant folding |
| `compiler/builtins.py` | every primitive's rule, beside its lowering |
| `compiler/codegen.py` | lowering the typed tree; nothing else produces text |
| `runtime/*.hpp` | guards, owners, threads, device calls |
| `projects/toolchain.py` | every native flag |
| `agent/teaching.py` | the rule cards, selected from lexical tokens |

Project manifests are data, never build scripts. The formatter owns layout: `ruff format` at 120 columns, `cairn fmt` for `.cairn`. The smallest clear program wins, never by hiding a cost or deleting a check. No source file is longer than 800 lines; split by responsibility, under a name that says what the piece owns.

**Do not weaken** a task, reference, input domain, numerical policy, alias rule, lease, lane rule, effect ceiling or test to make a candidate pass. Checked and wrapping arithmetic differ. Borrows are second class: a design that needs a stored or returned borrow needs an owner, an index or a handle. An owner moves; `take` and `swap` are the only ways out of a place. A linear value is consumed exactly once on every path. A lane touches only element `[i]` of what any lane writes. While a ticket is live its borrows are leased. `unsafe` is for the foreign boundary and the machine, never for silencing the checker.

**Keep claims distinct**: accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are different statements, and unknown is never success. The Lean result covers the certificate checker, the seventeen collector certificates, the collector loop model and a core ownership/lease calculus over locals, record field paths, whole owners, headers, elements, array parts with visible bounds and parallel regions (`proofs/Cairn/Places.lean` and `proofs/Cairn/Ownership/`, hand-written, not extracted from `checking.py`); the receipt turns it off when the bundle changes, so regenerate with `tools/checks/export_lean_certificates.py` and rebuild `proofs/` when you touch a rule. Do not claim a speedup, GPU advantage or AI proficiency without executed evidence recorded under `evidence/`.

**Every language change needs** all of these before it lands:

- a precise elaboration and a failure policy;
- a cost boundary, meaning which effects it adds;
- a rejection test naming its diagnostic code;
- an independent behavior test run natively under both compilers, with the sanitizer that bites (address and leak for ownership, thread for concurrency, the device death tests for guards in lanes);
- a rule card in `agent/teaching.py`, selected from lexical tokens;
- an entry in the chapter of `docs/language/` that owns it and in `docs/project/capabilities.json`;
- the canonical projection still round-tripping to identical native code.

Count whole compiler dependencies in density measurements, not a facade alone.

**Never** commit credentials, binaries, build trees or unrelated user files. Never change repository visibility, force-push, delete remote resources, install a token or publish from a test. Publication tests use fakes; the private publisher is opt-in and creates only a new personal repository. A task whose child exits abnormally has failed, even if it printed a pass.

## Development

Work on a branch, one focused change at a time. Run the fast suite before and after you touch code, and both native compilers with the relevant sanitizers when you touch the runtime or the lowering. Accepted examples are not evidence on their own: every rule needs a rejection test naming its diagnostic code and an independent behaviour oracle.

Source belongs in `src/cairn`, tests in `tests`, real programs in `examples`, and generated results under `results/`, which is not tracked. Do not reimplement a compiler rule in a script. `implementation_hash()` in `src/cairn/verify/scalar_semantics.py` lists the files a semantic receipt is pinned to; add a new parser, checker or emitter file to that list. Prefer removing repeated boilerplate to adding opaque punctuation, and do not shrink a source-token measurement by excluding semantics the program imports.

Commit messages are one plain sentence saying what is now true, with no attribution trailer. The project is licensed MIT or Apache-2.0 at the recipient's option, and every contribution is accepted under the same terms. Publication to a package index or a change of hosting is an explicit owner action, never a side effect of a test or a script.

## Writing documentation

The documentation is [docs/](docs/README.md): one file per subject, and one chapter per subject of the language reference under `docs/language/`. Add to the file that owns the subject rather than starting one, keep every file under 800 lines, and update the index when a file is added. README.md says what CAIRN is and how to start it, and nothing that a docs file already says. `docs/std_api.md` is generated by `make docs` and is never edited by hand.

Write the way a careful engineer explains something at a whiteboard: plain present-tense sentences, paragraphs of two to four, one idea per sentence. Lead with what the reader can do, then the rule, then the reason. Cut words, never facts, limits or diagnostic codes, and keep `accepted`, `typed`, `native-built`, `finite-tested`, `sanitizer-clean`, `SMT-equivalent`, `Lean-checked` and `benchmarked` apart, since unknown is never success. Show a short real example instead of describing syntax. No emojis, no em dashes, no filler openers, no title-case headings, no bullet list of paragraphs; a table is for tabular data. One paragraph per line, never hard-wrapped.

`tests/language/test_docs_examples.py` compiles every fenced example in README.md and under `docs/`. A block tagged `cairn` must be accepted; `cairn rejects E-CODE` must be refused with exactly that code; `cairn fragment` is not compiled, so use it rarely. Shell sessions are `sh`, compiler output is `text` or `json`. Check an example with `python bin/cairn check FILE` before you keep it. The twelve programs of `docs/tour.md` are also built and run by `tests/language/test_tour.py`.
