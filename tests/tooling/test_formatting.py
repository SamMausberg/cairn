"""`cairn fmt`: meaning is preserved, comments survive, and formatting is a fixed point.

The corpus is every CAIRN source in the checkout plus every CAIRN program embedded
in this test suite, so a new example is covered the moment it is added.
"""

import ast
import json
import random
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.syntax.lexing import TOKEN, lex
from cairn.compiler.syntax.tree import Diagnostic
from cairn.editor.formatting import comments, format_report, format_source
from sources import cairn_sources

ROOT = Path(__file__).resolve().parents[2]


def tokens(text):
    return [t.s for t in lex(text)]


def lexable(text):
    try:
        lex(text)
    except Diagnostic:
        return False
    return True


def repository_sources():
    return cairn_sources(ROOT)


def embedded_programs():
    """CAIRN programs written inline in the test suite, recognised by shape and lexed."""
    out = []
    for path in sorted(ROOT.joinpath("tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 40:
                text = node.value
                if any(k in text for k in ("fn ", "struct ", "enum ")) and "{" in text and lexable(text):
                    out.append(pytest.param(text, id=f"{path.name}:{node.lineno}"))
    return out


CORPUS = repository_sources()
EMBEDDED = embedded_programs()


def test_the_corpus_is_not_empty():
    assert len(CORPUS) >= 10
    assert len(EMBEDDED) >= 10


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: str(p.relative_to(ROOT)))
def test_repository_sources_keep_their_tokens_and_comments(path):
    text = path.read_text(encoding="utf-8")
    out, error = format_report(text)
    assert not error, error
    assert tokens(out) == tokens(text)
    assert comments(out) == comments(text)
    assert format_source(out) == out


@pytest.mark.parametrize("text", EMBEDDED)
def test_embedded_programs_keep_their_tokens_and_comments(text):
    out, error = format_report(text)
    assert not error, error
    assert tokens(out) == tokens(text)
    assert comments(out) == comments(text)
    assert format_source(out) == out


def test_repository_sources_still_compile_after_formatting():
    """Formatting a whole project source must not change what the compiler accepts."""
    from cairn.compiler.cairnc import compile_source

    checked = 0
    for path in CORPUS:
        text = path.read_text(encoding="utf-8")
        try:
            before = compile_source(text)[1]
        except Diagnostic:
            continue  # a fragment or a deliberate rejection; the token check already covers it
        checked += 1
        formatted = format_source(text)
        after = before if formatted == text else compile_source(formatted)[1]  # the same text compiles as it did
        assert after["function_count"] == before["function_count"]
    assert checked >= 5


# Spacing, blocks and blank lines ------------------------------------------------------------------

GOLDEN = {
    "a task group": (
        "fn tag(x:u64)->u64=x;\nfn main()->i32{let g=Group[u64](2);spawn tag(1)into g;let r=collect(g);wait(g);return 0;}\n",
        "fn tag(x:u64) -> u64 = x;\n"
        "fn main() -> i32 {\n  let g = Group[u64](2);\n  spawn tag(1) into g;\n  let r = collect(g);\n  wait(g);\n"
        "  return 0;\n}\n",
    ),
    "canonical spacing": (
        "fn  f( a : u64 ,b:u64 )->u64{return a+b;}\n",
        "fn f(a:u64, b:u64) -> u64 { return a + b; }\n",
    ),
    "borrows and placement": (
        "fn saxpy(n:usize,out:rw<f32>[n]@device,x:ro<f32>[n]@device,a:f32){\n"
        "parallel i in n{out[i]=a*x[i]+out[i];}\n}\n",
        "fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) {\n"
        "  parallel i in n { out[i] = a * x[i] + out[i]; }\n"
        "}\n",
    ),
    "declarations without space after colon": (
        "struct Header packed{kind:u8;size:u32;}\nconst N : usize = 256;\n",
        "struct Header packed { kind:u8; size:u32; }\nconst N:usize = 256;\n",
    ),
    "else joins the closing brace": (
        "fn f(x:u64) -> u64 {\n  if x > 1 {\n    return 1;\n  }\n  else {\n    return 0;\n  }\n}\n",
        "fn f(x:u64) -> u64 {\n  if x > 1 {\n    return 1;\n  } else {\n    return 0;\n  }\n}\n",
    ),
    "one statement per line": (
        "fn f() -> u64 {\n  let a = 1; let b = 2;\n  return a + b;\n}\n",
        "fn f() -> u64 {\n  let a = 1;\n  let b = 2;\n  return a + b;\n}\n",
    ),
    "unary minus is not a binary operator": (
        "fn f(a:i64) -> i64 { let b = -1; return a-b; }\n",
        "fn f(a:i64) -> i64 { let b = -1; return a - b; }\n",
    ),
    "blank runs collapse to one": (
        "fn a() -> u64 { return 1; }\n\n\n\nfn b() -> u64 { return 2; }\n",
        "fn a() -> u64 { return 1; }\n\nfn b() -> u64 { return 2; }\n",
    ),
    "single blank lines survive": (
        "fn f() -> u64 {\n  let a = 1;\n\n  return a;\n}\n",
        "fn f() -> u64 {\n  let a = 1;\n\n  return a;\n}\n",
    ),
    "import parentheses keep their space": (
        "import std.core (Option, Result);\n",
        "import std.core (Option, Result);\n",
    ),
    "closures stay inline": (
        "fn f(n:usize, xs:ro<u64>[n], bias:u64) -> u64 {\n  return apply(n,xs,|x:u64|->u64{return x+bias;});\n}\n",
        "fn f(n:usize, xs:ro<u64>[n], bias:u64) -> u64 {\n"
        "  return apply(n, xs, |x:u64| -> u64 { return x + bias; });\n}\n",
    ),
    "generic brackets never take a leading space": (
        "fn f(n:usize) -> Vec[u64] { let mut b = Buf [u64] (n); return b; }\n",
        "fn f(n:usize) -> Vec[u64] { let mut b = Buf[u64](n); return b; }\n",
    ),
    "a declared field extent hugs its field type": (
        "struct Chart{rows:usize;price : Buf[f64] [rows] ;qty:Buf[f64][rows];}\n",
        "struct Chart { rows:usize; price:Buf[f64][rows]; qty:Buf[f64][rows]; }\n",
    ),
    "effect rows and externs": (
        "extern fn write(fd:i32,data:ro<u8>[n],n:usize)->i64 effects(io);\n",
        "extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);\n",
    ),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_cases(name):
    source, expected = GOLDEN[name]
    assert format_source(source) == expected
    assert format_source(expected) == expected


def test_a_multiline_block_is_not_collapsed():
    source = "fn f() -> u64 {\n  return 1;\n}\n"
    assert format_source(source) == source


def test_a_block_that_no_longer_fits_is_broken():
    long_name = "a_very_long_identifier_that_will_not_fit_on_one_hundred_columns"
    source = f"fn f({long_name}:u64) -> u64 {{ return {long_name} + {long_name}; }}\n"
    out = format_source(source)
    assert out.splitlines()[0].endswith("{")
    assert all(len(line) <= 100 for line in out.splitlines())
    assert format_source(out) == out


def test_long_parameter_lists_wrap_at_a_hundred_columns():
    source = (
        "fn wide(alpha:usize, beta:rw<u64>[alpha]@device, gamma:ro<u64>[alpha]@device, "
        "delta:f64, epsilon:f64) -> u64 { return 0; }\n"
    )
    out = format_source(source)
    assert all(len(line) <= 100 for line in out.splitlines())
    assert out.startswith("fn wide(\n")
    assert tokens(out) == tokens(source)


def test_long_binary_chains_wrap_at_a_hundred_columns():
    source = "fn f() -> u64 {\n  return " + " + ".join(f"value_number_{i}" for i in range(12)) + ";\n}\n"
    out = format_source(source)
    assert all(len(line) <= 100 for line in out.splitlines())
    assert out.splitlines()[2].startswith("    + ")
    assert format_source(out) == out


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_scattered_whitespace_comments_and_truncation_change_nothing(seed):
    """Layout the formatter must survive: any whitespace, comments anywhere, a cut-off file."""
    rng = random.Random(seed)
    noise = [" // note\n", "\n// own line\n", " // héllo \U0001f600\n", "\n\n// spaced\n"]
    gaps = [" ", "\n", "\n\n\n", "  ", "\t", " \n  "]
    checked = 0
    for path in CORPUS:
        pieces, text, at = [], path.read_text(encoding="utf-8"), 0
        while at < len(text):
            m = TOKEN.match(text, at)
            pieces.append(m.group())
            at = m.end()
        mangled = "".join(
            rng.choice(gaps)
            if piece.isspace()
            else piece + (rng.choice(noise) if not piece.startswith("//") and rng.random() < 0.05 else "")
            for piece in pieces
        )
        if seed % 2:
            mangled = mangled[: rng.randrange(1, len(mangled) + 1)]  # unbalanced braces on purpose
        if not lexable(mangled):
            continue
        checked += 1
        out, error = format_report(mangled)
        assert not error, error
        assert tokens(out) == tokens(mangled)
        assert comments(out) == comments(mangled)
        assert format_source(out) == out
    assert checked >= 5


# Comments -----------------------------------------------------------------------------------------


COMMENTS = {
    "own line and trailing": (
        "// header\nfn f() -> u64 { // after the signature\n"
        "  // an own line\n  return 1; // after a statement\n}\n// trailer\n"
    ),
    "comment inside a parameter list": "fn f(a:u64, // the first\n     b:u64) -> u64 { return a + b; }\n",
    "comment before a closing brace": "fn f() -> u64 {\n  return 1;\n  // last word\n}\n",
    "blank line before a comment": "fn a() -> u64 { return 1; }\n\n// about b\nfn b() -> u64 { return 2; }\n",
    "unicode comment": "// héllo wörld — \U0001f600 astral\nfn f() -> u64 { return 1; }\n",
}


@pytest.mark.parametrize("name", sorted(COMMENTS))
def test_every_comment_survives(name):
    source = COMMENTS[name]
    out, error = format_report(source)
    assert not error, error
    assert comments(out) == comments(source)
    assert tokens(out) == tokens(source)
    assert format_source(out) == out


def test_a_trailing_comment_stays_on_its_line():
    out = format_source("fn f() -> u64 {\n  return 1; // why\n}\n")
    assert out == "fn f() -> u64 {\n  return 1; // why\n}\n"


def test_a_comment_keeps_the_declaration_it_documents():
    source = "// what f does\nfn f() -> u64 { return 1; }\n"
    assert format_source(source) == source


def test_a_splice_is_a_bracket_and_a_fold_operator_is_not_a_break():
    """`each f in R { a, b }` stays whole however long its line is; the line breaks around it, never after `fold`."""
    source = (
        "pub recipe stats for R where width = fold + each f in R { bytes(f) }, widest = fold max each f in R { bytes(f) } {\n"
        "  pub fn $R_all(c:ro<$R_table>) -> $R_summary = $R_summary(each f in R { $R_min_$f(c) }, "
        "each f in R { $R_max_$f(c), $R_sum_$f(c) });\n"
        "  pub fn $R_key(v:R) -> u64 = fold ^ each f in R where o = offset(f) { shl_wrap(u64(v.$f), $o) } "
        "+ fold add_wrap each f in R { u64(v.$f) };\n}\n"
    )
    assert format_source(source) == (
        "pub recipe stats for R where width = fold + each f in R { bytes(f) },\n"
        "  widest = fold max each f in R { bytes(f) } {\n"
        "  pub fn $R_all(c:ro<$R_table>) -> $R_summary = $R_summary(\n"
        "    each f in R { $R_min_$f(c) }, each f in R { $R_max_$f(c), $R_sum_$f(c) }\n"
        "  );\n"
        "  pub fn $R_key(v:R) -> u64 = fold ^ each f in R where o = offset(f) { shl_wrap(u64(v.$f), $o) }\n"
        "    + fold add_wrap each f in R { u64(v.$f) };\n}\n"
    )
    assert format_source(format_source(source)) == format_source(source)


def test_trailing_whitespace_and_carriage_returns_are_normalised():
    assert format_source("// spaced   \nfn f() -> u64 { return 1; }  \n") == (
        "// spaced\nfn f() -> u64 { return 1; }\n"
    )
    assert format_source("fn f() -> u64 {\r\n  return 1; // note\r\n}\r\n") == (
        "fn f() -> u64 {\n  return 1; // note\n}\n"
    )


# Refusals and the command line --------------------------------------------------------------------


def test_unlexable_input_is_returned_unchanged():
    source = "fn f() { let x = #broken; }\n"
    out, error = format_report(source)
    assert out == source
    assert "Unexpected character" in error
    assert format_source(source) == source


def test_an_empty_file_formats_to_itself():
    assert format_source("") == ""
    assert format_source("\n\n") == ""


def test_unbalanced_braces_do_not_crash():
    for source in ("fn f() {\n  return 1;\n", "}\n}\nfn f() -> u64 { return 1; }\n", "{{{{\n"):
        out, error = format_report(source)
        assert not error, error
        assert tokens(out) == tokens(source)


def write(directory, name, text):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_fmt_rewrites_in_place(tmp_path, capsys):
    path = write(tmp_path, "src/a.cairn", "fn f(a:u64,b:u64)->u64{return a+b;}\n")
    clean = write(tmp_path, "src/b.cairn", "fn g() -> u64 { return 1; }\n")
    write(tmp_path, ".cairn/history/records.jsonl", "")  # the history cairn tune keeps beside a manifest
    assert main(["fmt", str(tmp_path)]) == 0
    assert path.read_text() == "fn f(a:u64, b:u64) -> u64 { return a + b; }\n"
    assert clean.read_text() == "fn g() -> u64 { return 1; }\n"
    result = json.loads(capsys.readouterr().out)
    assert result["changed"] == [str(path)]
    assert result["not_formatted"] == []


def test_fmt_check_exits_one_and_writes_nothing(tmp_path, capsys):
    path = write(tmp_path, "a.cairn", "fn f(a:u64,b:u64)->u64{return a+b;}\n")
    assert main(["fmt", "--check", str(path)]) == 1
    assert path.read_text() == "fn f(a:u64,b:u64)->u64{return a+b;}\n"
    assert json.loads(capsys.readouterr().out)["status"] == "would-change"
    assert main(["fmt", str(path)]) == 0
    capsys.readouterr()
    assert main(["fmt", "--check", str(path)]) == 0
    assert capsys.readouterr().out == '{"status":"clean","mode":"check","changed":[],"not_formatted":[]}\n'  # piped


def test_fmt_diff_prints_a_patch_and_writes_nothing(tmp_path, capsys):
    path = write(tmp_path, "a.cairn", "fn f(a:u64,b:u64)->u64{return a+b;}\n")
    assert main(["fmt", "--diff", str(path)]) == 1
    assert path.read_text() == "fn f(a:u64,b:u64)->u64{return a+b;}\n"
    out = capsys.readouterr().out
    assert out.startswith("--- " + str(path))
    assert "+fn f(a:u64, b:u64) -> u64 { return a + b; }" in out


def test_fmt_never_touches_a_file_that_does_not_lex(tmp_path, capsys):
    broken = write(tmp_path, "broken.cairn", "fn f() { let x = #; }\n")
    good = write(tmp_path, "good.cairn", "fn g(a:u64)->u64{return a;}\n")
    assert main(["fmt", str(tmp_path)]) == 1
    assert broken.read_text() == "fn f() { let x = #; }\n"
    assert good.read_text() == "fn g(a:u64) -> u64 { return a; }\n"
    result = json.loads(capsys.readouterr().out)
    assert [x["file"] for x in result["not_formatted"]] == [str(broken)]
