# Editor support

This folder holds what an editor needs to work with CAIRN. Every editor here gets the same two things: highlighting generated from the compiler's own vocabulary, and `cairn lsp`, the language server that ships with the compiler. [docs/tools.md](../docs/tools.md#cairn-lsp) describes what the server answers.

| Directory | Editor | What it holds | Setup |
|---|---|---|---|
| `vscode/` | VS Code, Cursor | the extension: TextMate grammar, language configuration, snippets, semantic token legend, the LSP client | [the editor extension](../docs/tools.md#the-editor-extension); [vscode/README.md](vscode/README.md) lists its features and settings |
| `vim/` | Vim, Neovim | `ftdetect`, `syntax` and `ftplugin`, as a runtime directory | [Vim and Neovim](../docs/tools.md#vim-neovim-and-github) |

The TextMate grammar and the Vim files are generated. Run `make editors` after the compiler learns a word, and never edit them by hand. `tests/tooling/test_grammar.py` fails while a committed file differs from a fresh run, and the suite checks the extension's manifest, commands and client against stand-in modules. No `.vsix` is built here, because packaging needs `@vscode/vsce` and nothing is downloaded.

Any other editor with an LSP client can run `cairn lsp` over standard input and output. Shell completions, and a watched check that prints JSON Lines, are in [docs/tools.md](../docs/tools.md#a-watched-check-and-shell-completions).
