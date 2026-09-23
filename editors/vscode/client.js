"use strict";
// The whole client: start `cairn lsp` over stdio and let the server do the work, and run what the server's
// code lenses name (`cairn run`, `cairn test --filter`) in a terminal. No build step and no dependency beyond
// vscode-languageclient.

const { commands, window, workspace } = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

// One word for a POSIX shell: a path or a test name is passed as written, whatever it holds.
function quoted(word) {
  return /^[A-Za-z0-9_./:=-]+$/.test(word) ? word : "'" + word.replace(/'/g, "'\\''") + "'";
}

function inTerminal(words) {
  const terminal = window.terminals.find((t) => t.name === "CAIRN") || window.createTerminal("CAIRN");
  terminal.show();
  terminal.sendText(words.map(quoted).join(" "));
}

function activate(context) {
  const settings = workspace.getConfiguration("cairn");
  const executable = settings.get("server.command", "cairn");
  const server = {
    command: executable,
    args: settings.get("server.arguments", ["lsp"]),
    transport: TransportKind.stdio
  };
  // From a lens, the command gets the project the server named; from the palette, it runs the open file.
  const open = () => (window.activeTextEditor ? window.activeTextEditor.document.uri.fsPath : ".");
  context.subscriptions.push(
    commands.registerCommand("cairn.run", (target) => inTerminal([executable, "run", target || open()])),
    commands.registerCommand("cairn.runTest", (target, name) =>
      inTerminal([executable, "test", target, "--test", name])
    )
  );
  client = new LanguageClient(
    "cairn",
    "CAIRN Language Server",
    { run: server, debug: server },
    { documentSelector: [{ scheme: "file", language: "cairn" }] }
  );
  return client.start();
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
