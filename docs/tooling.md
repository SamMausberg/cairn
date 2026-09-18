# CAIRN developer tooling

Two commands ship with the compiler and depend on nothing outside the standard
library: `cairn fmt` lays out sources, `cairn lsp` answers an editor. Neither
changes what the compiler accepts; both are layout and presentation only.

## `cairn fmt`

```sh
cairn fmt src/                 # rewrite every *.cairn under src/ in place
cairn fmt --check src/ tests/  # write nothing; exit 1 if anything would change
cairn fmt --diff src/a.cairn   # write nothing; print a unified diff
```

Paths may be files or directories; a directory is searched for `*.cairn`. In place
is the default; `--check` and `--diff` never write. The exit code is 1 when a file
fails to lex, or when `--check`/`--diff` found something to change; otherwise 0.
Everything but a `--diff` patch is printed as a JSON report naming the files that
changed and the files that were left alone.

The layout it produces:

* two-space indentation, one statement per line, one trailing newline;
* canonical spacing — `fn f(n:usize, x:ro<u8>[n]) -> u64`, `a + b`, `x[i]`,
  `f(a, b)`, `key:Type` with no space after the colon, no space before `;` `,`
  `[`, and `@device` attached to its extent;
* a block the author wrote on one line stays on one line if it still fits in 100
  columns (`if x { return 1; }`); otherwise it is broken over lines. A block the
  author wrote over several lines is never collapsed;
* long parameter lists and call arguments wrap inside their brackets, long binary
  chains wrap before an operator, both greedily filled to 100 columns;
* every comment is kept, at the end of its line or on its own line;
* one blank line between declarations or statements is kept; a run of blank lines
  collapses to one; blank lines next to a brace are dropped.

**It refuses rather than risk a change of meaning.** Before writing anything the
formatter re-lexes its own output and compares the token stream and the comment
list with the input. If either differs — or if the input does not lex at all —
the file is left exactly as it was and listed under `not_formatted`. Formatting is
a fixed point: `format_source(format_source(x)) == format_source(x)`.

The API is `cairn.formatting.format_source(text) -> str`, which returns `text`
unchanged when it refuses, and `format_report(text) -> (text, reason)` when the
reason matters.

## `cairn build --incremental`

One object per module, compiled against a shared interface header (`program.hpp`: types, tables and prototypes) and cached under `build/objects/` by the hash of everything that went into it: the unit, the header, the command line, the runtime headers and the compiler version. Nothing stale can be linked, because a change to any of those is a different key; a body-only edit recompiles one module, a signature or layout change recompiles all. Missing objects compile concurrently. The build receipt lists every unit and whether it was reused. It is opt-in because separate objects give up inlining across modules; device programs and freestanding images are always one unit. The cache is safe to delete.

## `cairn lsp`

```sh
cairn lsp      # speaks JSON-RPC with Content-Length framing on stdin/stdout
```

Supported: `initialize`, `initialized`, `shutdown`, `exit`; full-text
`textDocument/didOpen`, `didChange`, `didClose`; and

| Request | What it answers |
| --- | --- |
| `publishDiagnostics` | the compiler's diagnostic for the buffer: its code, its message, the `repair_hint` from `agent_tools.explain`, and the exact token range |
| `textDocument/hover` | the smallest checked expression covering the position — its type, the type expected of it, and for a name whether the binding is mutable |
| `textDocument/documentSymbol` | functions, structs, enums, traits, consts and impls with ranges; trait and impl members nest as children |
| `textDocument/definition` | a declaration of the identifier under the cursor, in the same document |
| `textDocument/formatting` | one whole-document edit from `cairn fmt`, or no edit when the buffer is already formatted |

Positions are UTF-16 code units, as the protocol requires, so non-ASCII comments
and astral characters do not shift a range.

The server analyses the open buffer; `std.*` imports are linked by the compiler
itself, and sites from a linked module are not offered as hovers of the file you
are editing. A buffer that does not compile still gets its outline, and any
compiler failure becomes a diagnostic rather than an exception: the server
answers every request it accepted and keeps running.

Known limits: only one document is analysed at a time, so a name declared in a
sibling file of the same project is neither hovered nor jumped to; there is no
completion, rename, references or workspace symbol support; the first declaration
with a matching name wins in `definition`, and `Enum.Variant` resolves to the
enum; diagnostics stop at the first compiler error, because the compiler does.

## Editor extension

`editors/vscode/` is a VS Code / Cursor extension: a TextMate grammar, bracket and
comment configuration, and a client that launches `cairn lsp`. It has no build
step and vendors nothing. See `editors/README.md` for installation, and set
`cairn.server.command` when `cairn` is not on `PATH`.
