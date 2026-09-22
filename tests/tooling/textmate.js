"use strict";
// Tokenize files with the real TextMate engine VS Code uses (vscode-textmate over vscode-oniguruma).
// node textmate.js <node_modules> <grammar.json> <file>...   prints, per file, per line, [start, end, scopes].
// Nothing is downloaded: the modules come from an editor already installed on the machine.

const fs = require("fs");
const path = require("path");
const [modules, grammarPath, ...files] = process.argv.slice(2);
const textmate = require(path.join(modules, "vscode-textmate"));
const oniguruma = require(path.join(modules, "vscode-oniguruma"));

async function main() {
  const wasm = fs.readFileSync(path.join(modules, "vscode-oniguruma", "release", "onig.wasm")).buffer;
  await oniguruma.loadWASM(wasm);
  const registry = new textmate.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (patterns) => new oniguruma.OnigScanner(patterns),
      createOnigString: (s) => new oniguruma.OnigString(s)
    }),
    loadGrammar: async () => textmate.parseRawGrammar(fs.readFileSync(grammarPath, "utf8"), grammarPath)
  });
  const grammar = await registry.loadGrammar("source.cairn");
  const out = {};
  for (const file of files) {
    let stack = textmate.INITIAL;
    out[file] = fs.readFileSync(file, "utf8").split("\n").map((line) => {
      const result = grammar.tokenizeLine(line, stack);
      stack = result.ruleStack;
      return result.tokens.map((t) => [t.startIndex, t.endIndex, t.scopes.slice(1)]);
    });
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
});
