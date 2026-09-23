# CAIRN for VS Code and Cursor

Highlighting, snippets and a client for `cairn lsp`, the language server that ships with the CAIRN compiler. The server does the work, so what the editor shows is what the compiler checks.

## What you get

- Diagnostics as you type, with the compiler's code (`E-LEASED`, `E-MOVED`) and its repair hint.
- Colors by meaning once a buffer compiles: parameters, mutable and immutable locals, fields, methods, records, enums and their variants, traits, constants, library names and effect rows. Mutable names are underlined.
- Inlay hints for what the source leaves unwritten: each function's inferred effect row after its signature, the extents a call leaves out (`dot(v, v)` passes `n = len(v)`), and the type of a `let` that writes none.
- Hover with the type, the binding, the signature, the effect row and the comment above a declaration.
- Completion, signature help, go to definition (into the packaged `std` too), the document outline, workspace symbols and `Format Document`, which is `cairn fmt`. A file of a project is analysed with the whole project, so what it imports from a sibling file resolves.
- References, highlights and rename across every file of the project, for functions, types, constants, locals, fields and variants. A rename is refused unless the whole project still compiles with every function's effect row, callees and guards unchanged but for the name.
- Run and Run test lenses above `main` and each `test` block, which run `cairn run` and `cairn test --test NAME` in a terminal.
- Quick fixes where the repair is one edit nobody has to choose: the missing arms of a `match`, `let mut` for an assigned local, and the `import` of a library module.
- Snippets for the common forms: `main`, `fn`, `fnv`, `struct`, `enum`, `match`, `for`, `parallel`, `reduce`, `spawn`, `defer` and more. Each one compiles as written.

## Setup

The extension runs `cairn lsp`. Install the compiler (`pip install -e .` in a checkout) so `cairn` is on `PATH`, or point the setting at the checkout's entry script:

```json
{
  "cairn.server.command": "/path/to/cairn/bin/cairn",
  "cairn.server.arguments": ["lsp"]
}
```

Highlighting and snippets work without the server. The grammar is generated from the compiler's vocabulary by `make editors`, so it knows every keyword the compiler does. [docs/tools.md](https://github.com/SamMausberg/cairn/blob/main/docs/tools.md#cairn-lsp) describes every request the server answers and its limits.

CAIRN and this extension are licensed under either the MIT license or the Apache License, Version 2.0, at your option.
