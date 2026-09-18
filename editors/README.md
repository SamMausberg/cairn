# Editor support

`editors/vscode/` is a VS Code / Cursor extension: a TextMate grammar, bracket and
comment configuration, and a client that starts `cairn lsp` over stdio. It has no
build step. `node_modules/` is not vendored; `vscode-languageclient` is declared in
`package.json` and resolved when the extension is packaged or installed.

## Install from this checkout (Cursor or VS Code)

Symlink the directory into the editor's extension folder and reload the window:

```sh
ln -s "$PWD/editors/vscode" ~/.cursor/extensions/cairn-1.0.0          # Cursor
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-1.0.0          # VS Code
```

Syntax highlighting works immediately. The language server needs its dependency
present, so run `npm install --omit=dev` inside `editors/vscode/` once (this is the
only step that touches the network). Without it the grammar still loads and the
client reports a missing module.

## Install as a package

```sh
cd editors/vscode
npm install
npx --yes @vscode/vsce package          # writes cairn-1.0.0.vsix
code --install-extension cairn-1.0.0.vsix
```

## Server executable

The client runs `cairn lsp`. If `cairn` is not on `PATH` — a source checkout, for
instance — point the setting at the checkout's entry script:

```json
{
  "cairn.server.command": "/path/to/cairn/bin/cairn",
  "cairn.server.arguments": ["lsp"]
}
```

`bin/cairn` needs a Python 3.11+ interpreter on `PATH`; to pin one, set the command
to that interpreter and the arguments to `["/path/to/cairn/bin/cairn", "lsp"]`.

## What the extension gives you

Diagnostics as you type, hover types, a document outline, go to definition inside
the open file, and `Format Document` (the same formatter as `cairn fmt`). See
`docs/tooling.md` for what each of those covers and what it does not.

The grammar's keyword lists are checked against the compiler's `RESERVED` set by
`tests/test_lsp.py`, so a new keyword fails the suite until the grammar learns it.
