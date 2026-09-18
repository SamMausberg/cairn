# Working on CAIRN

Read README.md and docs/language.md. Start with `python3 bin/cairn doctor` and `python3 -m pytest -q tests`. Use the existing Python API and conventional CAIRN source; do not invent unsupported libraries or syntax.

Keep changes small and local. The parser owns syntax and source ranges; checking owns types/effects; expansion owns closed generators; codegen owns C++; the runtime header owns explicit guards. Project manifests are data, never executable build scripts. Preserve source comments and the unchanged parts of edits.

Do not weaken a task, reference, input domain, numerical policy, alias rule, or test to make a candidate pass. Checked arithmetic and wrapping arithmetic are different. `ro<T>[n]` and `rw<T>[n]` default to host only; they are borrows, not owning vectors. `fn f(...) -> T = expr;` means one return; blocks still need explicit returns. The collector does not allocate.

Keep accepted, typed, native-built, tested, and SMT-equivalent outcomes distinct. Unknown is not success. Do not claim Lean verification, a new native speedup, or AI proficiency without the corresponding executed evidence.

Add rejection tests, independent behavior tests, and any applicable scalar comparison. Run both compilers and sanitizers for runtime/lowering changes. Count whole compiler dependencies in source-audit density measurements, not just the API facade.

Never commit credentials, binaries, generated build trees, or an unrelated user file. Never change repository visibility, force-push, delete remote resources, install a token, or run a publish step from a test. Publication tests use fakes. The private publisher is opt-in and creates only a new personal repository.
