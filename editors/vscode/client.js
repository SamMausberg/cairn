"use strict";
// The whole client: start `cairn lsp` over stdio and let the server do the work.
// No build step and no dependency beyond vscode-languageclient.

const { workspace } = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

let client;

function activate() {
  const settings = workspace.getConfiguration("cairn");
  const server = {
    command: settings.get("server.command", "cairn"),
    args: settings.get("server.arguments", ["lsp"]),
    transport: TransportKind.stdio
  };
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
