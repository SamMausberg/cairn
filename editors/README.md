# Editor support

`editors/vscode/` is a VS Code and Cursor extension, version 1.2.0: a TextMate grammar, bracket and comment configuration, and a client that starts `cairn lsp` over stdio. It has no build step. `node_modules/` is not vendored; `vscode-languageclient` is declared in `package.json` and resolved when the extension is packaged or installed.

## Install from this checkout

Symlink the directory into the editor's extension folder and reload the window:

```sh
ln -s "$PWD/editors/vscode" ~/.cursor/extensions/cairn-1.2.0          # Cursor
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-1.2.0          # VS Code
```

Syntax highlighting works immediately. The language server needs its dependency present, so run `npm install --omit=dev` inside `editors/vscode/` once. That is the only step that touches the network; without it the grammar still loads and the client reports a missing module.

## Install as a package

```sh
cd editors/vscode
npm install
npx --yes @vscode/vsce package          # writes cairn-1.2.0.vsix
code --install-extension cairn-1.2.0.vsix
```

## Server executable

The client runs `cairn`, with `lsp` as its argument. When `cairn` is not on `PATH`, a source checkout for instance, point the setting at the checkout's entry script:

```json
{
  "cairn.server.command": "/path/to/cairn/bin/cairn",
  "cairn.server.arguments": ["lsp"]
}
```

`bin/cairn` needs a Python 3.11 or later interpreter on `PATH`. To pin one, set the command to that interpreter and the arguments to `["/path/to/cairn/bin/cairn", "lsp"]`.

## What the extension gives you

Diagnostics as you type, hover types and effect rows, a document outline, completion (fields, methods, module members, variants, locals and words), signature help, go to definition inside the open file and into the packaged `std`, references and rename within the file, and `Format Document`, which is the same formatter as `cairn fmt`. The client negotiates all of it from the server, so nothing here changes when the server learns something new. [docs/guide/tooling.md](../docs/guide/tooling.md) says what each of those covers and what it does not.

The grammar's keyword list is checked against the compiler's `RESERVED` set by `tests/tooling/test_lsp.py`, so a new keyword fails the suite until the grammar learns it.
