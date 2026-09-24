"""The VS Code and Cursor extension: a manifest that points at real files, a configuration that behaves, a legend
the server fills, snippets that compile as written, and no build step."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_program
from cairn.editor.highlighting import KINDS, MODIFIERS
from cairn.version import __semver__

ROOT = Path(__file__).resolve().parents[2]
EDITOR = ROOT / "editors" / "vscode"
STANDARD_KINDS = {"namespace", "type", "class", "enum", "interface", "struct", "typeParameter", "parameter"}
STANDARD_KINDS |= {"variable", "property", "enumMember", "event", "function", "method", "macro", "keyword"}
STANDARD_MODIFIERS = {"declaration", "definition", "readonly", "static", "deprecated", "abstract", "async"}
STANDARD_MODIFIERS |= {"modification", "documentation", "defaultLibrary"}


def manifest() -> dict:
    return json.loads((EDITOR / "package.json").read_text(encoding="utf-8"))


def test_the_manifest_points_at_files_that_exist():
    m = manifest()
    language, contributes = m["contributes"]["languages"][0], m["contributes"]
    assert language["extensions"] == [".cairn"] and m["activationEvents"] == ["onLanguage:cairn"]
    for relative in (
        m["main"],
        language["configuration"],
        contributes["grammars"][0]["path"],
        contributes["snippets"][0]["path"],
    ):
        assert (EDITOR / relative).is_file(), relative
    assert set(m["dependencies"]) == {"vscode-languageclient"}
    assert not (EDITOR / "node_modules").exists(), "node_modules must not be vendored"
    assert (EDITOR / "README.md").is_file() and (EDITOR / ".vscodeignore").is_file()


def test_the_manifest_matches_the_package_and_its_license():
    m = manifest()
    assert m["version"] == __semver__
    assert m["license"] == "(MIT OR Apache-2.0)"
    assert (EDITOR / "LICENSE").read_text(encoding="utf-8").endswith((ROOT / "LICENSE-MIT").read_text(encoding="utf-8"))


def test_the_manifest_declares_every_token_the_server_sends_beyond_the_standard_ones():
    contributes = manifest()["contributes"]
    assert {t["id"] for t in contributes["semanticTokenTypes"]} == set(KINDS) - STANDARD_KINDS
    assert {t["id"] for t in contributes["semanticTokenModifiers"]} == set(MODIFIERS) - STANDARD_MODIFIERS
    scopes = contributes["semanticTokenScopes"][0]
    assert scopes["language"] == "cairn" and scopes["scopes"]["effect"] == ["support.constant.effect.cairn"]


def test_the_language_configuration_is_valid():
    configuration = json.loads((EDITOR / "language-configuration.json").read_text(encoding="utf-8"))
    assert configuration["comments"]["lineComment"] == "//"
    assert [pair[0] for pair in configuration["brackets"]] == ["{", "[", "("]
    assert {pair["open"] for pair in configuration["autoClosingPairs"]} == {"{", "[", "(", '"', "'"}  # never `<`
    comment = re.compile(configuration["onEnterRules"][0]["beforeText"])
    assert comment.search("  // the next line is a comment too") and not comment.search("  let x = 1;")
    assert not comment.search("  //") and configuration["onEnterRules"][0]["action"]["appendText"] == "// "
    increase = re.compile(configuration["indentationRules"]["increaseIndentPattern"])
    assert increase.search("fn f() {") and not increase.search("fn f() { return 1; }") and not increase.search("// {")


def test_the_client_starts_the_server_over_stdio():
    client = (EDITOR / "client.js").read_text(encoding="utf-8")
    assert "vscode-languageclient/node" in client
    assert "TransportKind.stdio" in client
    assert 'settings.get("server.arguments", ["lsp"])' in client


def test_the_commands_a_lens_names_run_in_a_terminal():
    """The client under stand-in `vscode` modules: each command the server's lenses name is registered and declared,
    and types one quoted command line into one reused terminal."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run(
        [node, str(Path(__file__).with_name("client_harness.js")), str(EDITOR / "client.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    said = json.loads(done.stdout)
    assert said["registered"] == ["cairn.run", "cairn.runTest"] and said["subscriptions"] == 2
    assert said["terminals"] == 1 and said["started"][0]["args"] == ["lsp"]
    assert said["sent"] == [
        "'/opt/my cairn' run '/work/my app/cairn.toml'",
        "'/opt/my cairn' test /work/cairn.toml --test 'app.it'\\''s'",
        "'/opt/my cairn' run '/work/open file.cairn'",  # from the palette: the open file
    ]
    declared = {c["command"] for c in manifest()["contributes"]["commands"]}
    assert declared == set(said["registered"])
    hidden = manifest()["contributes"]["menus"]["commandPalette"]
    assert hidden == [{"command": "cairn.runTest", "when": "false"}]  # it needs the name a lens gives it


def expanded(body: list[str]) -> str:
    """A snippet as it reads once every placeholder takes its default and every mirror its placeholder's text."""
    text, values = "\n".join(body).replace("\t", "  "), {}

    def fill(m: re.Match) -> str:
        number, default, choices = m.group(1), m.group(2), m.group(3)
        value = (
            default if default is not None else re.split(r"(?<!\\),", choices)[0].replace("\\|", "|") if choices else ""
        )
        values.setdefault(number, value)
        return value

    text = re.sub(r"\$\{(\d+)(?::([^{}]*)|\|((?:[^|\\]|\\.)*)\|)?\}", fill, text)
    return re.sub(r"\$(\d+)", lambda m: values.get(m.group(1), ""), text)


# Where each snippet goes to be compiled: `@` is the expanded snippet.
CONTEXT = {
    "main": "@", "function": "@", "expression function": "@", "view function": "@", "struct": "@", "enum": "@",
    "match": "import std.core (Option);\nfn f(value:Option[u64]) -> u64 {\n@\nreturn 0;\n}",
    "if": "fn f(condition:bool) {\n@\n}", "for": "fn f(n:usize) {\n@\n}", "while": "fn f(condition:bool) {\n@\n}",
    "let mut": "fn f() {\n@\n}", "parallel": "fn f(n:usize, out:rw<u64>[n]) {\n@\n}",
    "reduce": "fn f(n:usize, xs:ro<u64>[n]) -> u64 {\n@\nreturn total;\n}",
    "spawn and wait": "fn work() {}\nfn f() {\n@\n}",
    "defer": "linear struct Lease { id:u64; }\nfn release(l:Lease) { let Lease(id) = l; }\nfn f(resource:Lease) {\n@\n}",
    "import": "@\nfn main() -> i32 { return 0; }", "trait and impl": "struct Square { side:u64; }\n@",
    "unsafe": "fn f() {\n@\n}",
}  # fmt: skip


def snippets() -> dict:
    return json.loads((EDITOR / "snippets" / "cairn.json").read_text(encoding="utf-8"))


def test_every_snippet_has_a_place_to_be_compiled():
    assert set(snippets()) == set(CONTEXT)
    prefixes = [s["prefix"] for s in snippets().values()]
    assert len(prefixes) == len(set(prefixes))


@pytest.mark.parametrize("name", sorted(CONTEXT))
def test_a_snippet_compiles_as_written(name):
    compile_program(CONTEXT[name].replace("@", expanded(snippets()[name]["body"])))
