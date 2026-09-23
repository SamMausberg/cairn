"use strict";
// Runs editors/vscode/client.js against stand-ins for `vscode` and `vscode-languageclient/node`, calls the commands
// it registered as the server's code lenses would, and prints what it registered and what each command typed into
// its terminal, as JSON. `tests/tooling/test_extension.py` reads the answer.

const Module = require("module");

const sent = [];
const registered = {};
const started = [];
const terminals = [];
const fakes = {
  vscode: {
    workspace: {
      getConfiguration: () => ({ get: (key, fallback) => (key === "server.command" ? "/opt/my cairn" : fallback) })
    },
    commands: {
      registerCommand: (name, run) => {
        registered[name] = run;
        return { dispose() {} };
      }
    },
    window: {
      terminals,
      activeTextEditor: { document: { uri: { fsPath: "/work/open file.cairn" } } },
      createTerminal: (name) => {
        const terminal = { name, show() {}, sendText: (text) => sent.push(text) };
        terminals.push(terminal);
        return terminal;
      }
    }
  },
  "vscode-languageclient/node": {
    TransportKind: { stdio: "stdio" },
    LanguageClient: class {
      constructor(id, name, server) {
        started.push({ id, command: server.run.command, args: server.run.args, transport: server.run.transport });
      }
      start() {
        return Promise.resolve();
      }
      stop() {}
    }
  }
};
const load = Module._load;
Module._load = function (request, ...rest) {
  return Object.prototype.hasOwnProperty.call(fakes, request) ? fakes[request] : load.call(this, request, ...rest);
};

const client = require(process.argv[2]);
const context = { subscriptions: [] };
client.activate(context);
registered["cairn.run"]("/work/my app/cairn.toml");
registered["cairn.runTest"]("/work/cairn.toml", "app.it's");
registered["cairn.run"]();
console.log(
  JSON.stringify({
    registered: Object.keys(registered).sort(),
    subscriptions: context.subscriptions.length,
    terminals: terminals.length,
    sent,
    started
  })
);
