# Documentation

`guide/` is for people writing CAIRN.

- [tour.md](guide/tour.md): twelve complete programs, each compiled and run by the test suite.
- [language.md](guide/language.md): the reference.
- [std.md](guide/std.md) and [std_api.md](guide/std_api.md): the standard library, and its generated API reference.
- [tooling.md](guide/tooling.md): `cairn fmt`, `doc`, `expand`, `lsp`, incremental builds, the editor extension.
- [freestanding.md](guide/freestanding.md): the bare-metal AArch64 target.

`internals/` is for people changing the compiler.

- [architecture.md](internals/architecture.md): the pipeline, and where each rule lives by file and function.
- [testing.md](internals/testing.md): what each test folder establishes, and how to run it.
- [verification.md](internals/verification.md): what is proved, by what, and what is not.
- [security.md](internals/security.md): what the tools read, write and run.

`project/` states where the project stands.

- [roadmap.md](project/roadmap.md): what is still missing, stated as gates.
- [capabilities.json](project/capabilities.json): implemented and not implemented, as data.
- [private-publication.md](project/private-publication.md): the opt-in private publisher.
- `BASELINE.json` and `UPSTREAM.json`: the archives this source was built from.

The programs themselves are indexed in [examples/README.md](../examples/README.md). `cards/` holds the rule cards given to AI agents. `history/` holds the 0.2, 0.3 and 0.4 specifications.
