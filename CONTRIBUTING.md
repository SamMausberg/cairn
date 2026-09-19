# Development

Work on a branch, one focused change at a time. Run the fast suite before and after you touch code, and both native compilers with the relevant sanitizers when you touch the runtime or the lowering. Accepted examples are not evidence on their own: every rule needs a rejection test naming its diagnostic code and an independent behaviour oracle.

Source belongs in `src/cairn`, tests in `tests`, real programs in `examples`, and generated results outside tracked source. Do not reimplement a compiler rule in a script. `implementation_hash()` in `src/cairn/verify/scalar_semantics.py` lists the files a semantic receipt is pinned to; add a new parser, checker or emitter file to that list. Every added language construct needs a precise elaboration, a failure policy, a cost boundary and an entry in `docs/project/capabilities.json`. Prefer removing repeated boilerplate to adding opaque punctuation, and do not shrink a source-token measurement by excluding semantics the program imports.

This development scaffold authorizes no license grant, public hosting, package release or dependency auto-update. Publication stays an explicit owner action through the private-only path.

## Writing documentation

Write the way a careful engineer explains something at a whiteboard: plain present-tense sentences, paragraphs of two to four, one idea per sentence. Lead with what the reader can do, then the rule, then the reason. Cut words, never facts, limits or diagnostic codes, and keep `accepted`, `typed`, `native-built`, `finite-tested`, `sanitizer-clean`, `SMT-equivalent`, `Lean-checked` and `benchmarked` apart, since unknown is never success. Show a short real example instead of describing syntax. No emojis, no em dashes, no filler openers, no title-case headings, no bullet list of paragraphs; a table is for tabular data. One paragraph per line, never hard-wrapped.

`tests/language/test_docs_examples.py` compiles every fenced example in README.md, `docs/guide/`, `docs/internals/` and `examples/**/README.md`. A block tagged `cairn` must be accepted; `cairn rejects E-CODE` must be refused with exactly that code; `cairn fragment` is not compiled, so use it rarely. Shell sessions are `sh`, compiler output is `text` or `json`. Check an example with `python bin/cairn check FILE` before you keep it.
