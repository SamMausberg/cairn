# Editor support

Every editor here gets the same two things: highlighting generated from the compiler's own vocabulary, and `cairn lsp`, the language server that ships with the compiler. [docs/tools.md](../docs/tools.md#cairn-lsp) describes what the server answers.

| Directory | Editor | What it holds |
|---|---|---|
| `vscode/` | VS Code, Cursor | the extension: TextMate grammar, language configuration, snippets, semantic token legend, the LSP client |
| `vim/` | Vim, Neovim | `ftdetect`, `syntax` and `ftplugin`, as a runtime directory |

GitHub highlights `.cairn` files as Rust through the repository's `.gitattributes`, because it has no CAIRN grammar of its own.

The TextMate grammar and the Vim files are generated: run `make editors` after the compiler learns a word, and never edit them by hand. `tests/tooling/test_grammar.py` fails while a committed file differs from a fresh run.

## VS Code and Cursor

Symlink the directory into the editor's extensions folder and reload the window, then run `npm install --omit=dev` inside it once for the client's one dependency. [vscode/README.md](vscode/README.md) lists what the extension does and its settings.

```sh
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-language.cairn
```

No `.vsix` is built here, because packaging needs `@vscode/vsce` and nothing is downloaded; with it installed, `npx vsce package` inside `vscode/` packages the extension. The suite checks the manifest, the commands and the client against stand-in modules.

## Vim and Neovim

Put the runtime directory on the path. Neovim's built-in client then starts the server for each CAIRN buffer:

```vim
set runtimepath^=/path/to/cairn/editors/vim
```

```lua
vim.api.nvim_create_autocmd("FileType", { pattern = "cairn", callback = function()
  vim.lsp.start({ name = "cairn", cmd = { "cairn", "lsp" }, root_dir = vim.fs.root(0, { "cairn.toml", ".git" }) })
end })
```

Any other editor with an LSP client can run `cairn lsp` over stdio the same way. Shell completions and a watched check that prints JSON Lines are in [docs/tools.md](../docs/tools.md#a-watched-check-and-shell-completions).
