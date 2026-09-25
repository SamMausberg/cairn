"""The editor grammars: generated from the compiler's vocabulary, in sync, and assigning the scopes they claim.

Scopes are pinned through a small TextMate engine written here over Python's `re`, which the grammar's
patterns are restricted to. Where node and an installed editor's vscode-textmate are present, the real
engine tokenizes every `.cairn` file in the repository and must agree with this one character by character.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.check.effects import EFFECTS
from cairn.compiler.primitives.builtins import TABLE
from cairn.compiler.syntax.lexing import RESERVED
from cairn.compiler.syntax.tree import PLACES
from cairn.editor import grammar

ROOT = Path(__file__).resolve().parents[2]
GRAMMAR = ROOT / "editors" / "vscode" / "syntaxes" / "cairn.tmLanguage.json"


class Engine:
    """TextMate tokenization, as vscode-textmate does it: at each position the earliest match wins, ties go to
    the enclosing rule's end pattern and then to the first pattern listed, and each line is matched with a
    newline after it."""

    def __init__(self, raw: dict):
        self.repository, self.top = raw["repository"], raw

    def patterns(self, rule: dict) -> list[dict]:
        out = []
        for p in rule.get("patterns", []):
            if "include" in p:
                target = self.top if p["include"] == "$self" else self.repository[p["include"][1:]]
                out += self.patterns(target) if "match" not in target and "begin" not in target else [target]
            elif "match" in p or "begin" in p:
                out.append(p)
            else:
                out += self.patterns(p)
        return out

    @staticmethod
    def paint(scopes: list, m: re.Match, base: list[str], captures: dict) -> None:
        for i in range(m.start(), m.end()):
            scopes[i] = list(base)
        for group, capture in sorted(captures.items(), key=lambda kv: int(kv[0])):
            if m.group(int(group)) is not None and "name" in capture:
                for i in range(m.start(int(group)), m.end(int(group))):
                    scopes[i] = [*base, capture["name"]]

    def lines(self, text: str) -> list[list[list[str]]]:
        """Per line, the scopes of every character (the root scope left out)."""
        stack: list[tuple[dict, list[str]]] = [(self.top, [])]
        out = []
        for line in text.split("\n"):
            subject, pos = line + "\n", 0
            scopes: list = [None] * len(subject)
            while pos < len(subject):
                rule, base = stack[-1]
                candidates = ([("end", re.compile(rule["end"]), rule)] if "end" in rule else []) + [
                    ("rule", re.compile(p.get("match") or p["begin"]), p) for p in self.patterns(rule)
                ]
                best = None
                for kind, regex, p in candidates:
                    m = regex.search(subject, pos)
                    if m and (best is None or m.start() < best[1].start()):
                        best = (kind, m, p)
                if best is None:
                    for i in range(pos, len(subject)):
                        scopes[i] = list(base)
                    break
                kind, m, p = best
                for i in range(pos, m.start()):
                    scopes[i] = list(base)
                if kind == "end":
                    self.paint(scopes, m, base, rule.get("endCaptures", {}))
                    stack.pop()
                elif "begin" in p:
                    inner = [*base, p["name"]] if "name" in p else list(base)
                    self.paint(scopes, m, inner, p.get("beginCaptures", {}))
                    stack.append((p, inner))
                else:
                    self.paint(scopes, m, [*base, p["name"]] if "name" in p else list(base), p.get("captures", {}))
                if m.end() == pos and kind != "end" and "begin" not in p:
                    scopes[pos], pos = list(base), pos + 1  # an empty match never stalls the line
                else:
                    pos = m.end()
            out.append(scopes[: len(line)])
        return out


def engine() -> Engine:
    return Engine(json.loads(GRAMMAR.read_text(encoding="utf-8")))


def scope_of(source: str, token: str, nth: int = 0) -> str:
    """The innermost scope of the `nth` occurrence of `token` in a one-line `source`, if the whole token has one."""
    word = re.escape(token) if not re.fullmatch(r"[\w$]+", token) else rf"(?<![\w$]){re.escape(token)}(?!\w)"
    at = [m.start() for m in re.finditer(word, source)][nth]
    scopes = engine().lines(source)[0][at : at + len(token)]
    innermost = {(s[-1] if s else "") for s in scopes}
    assert len(innermost) == 1, f"{token!r} in {source!r} is split across {innermost}"
    return innermost.pop()


def test_the_committed_grammars_are_what_the_generator_writes():
    assert grammar.main(["--check"]) == 0, "regenerate with: make editors"


def test_every_reserved_word_has_exactly_one_class():
    assert not grammar.unclassified()
    seen: dict[str, str] = {}
    for name, (_, _, words) in grammar.WORDS.items():
        for word in words:
            assert word not in seen, f"{word} is in both {seen.get(word)} and {name}"
            seen[word] = name
    assert set(seen) == RESERVED, "a class lists a word the lexer does not reserve"


def test_every_word_the_parser_reads_in_one_position_is_highlighted_there():
    """A word the parser compares a token against, and the lexer does not reserve, is contextual and needs a rule."""
    compiler = sorted((ROOT / "src" / "cairn" / "compiler").rglob("*.py"))
    parser = "\n".join(p.read_text(encoding="utf-8") for p in compiler)
    compared = set(re.findall(r'(?:\.s == |eat\(|need\(|ahead\(\d\) == )"([a-z_]+)"', parser))
    compared |= {
        w for group in re.findall(r"\.s (?:not )?in \{([^}]*)\}", parser) for w in re.findall(r'"([a-z_]+)"', group)
    }
    contextual = compared - RESERVED - set(TABLE)
    assert contextual <= grammar.CONTEXTUAL, (
        f"highlight these where the parser reads them: {contextual - grammar.CONTEXTUAL}"
    )
    assert set(grammar.CONTEXT) == grammar.CONTEXTUAL and not grammar.CONTEXTUAL & RESERVED


def test_the_vocabulary_comes_from_the_compiler():
    assert set(grammar.BUILTINS) | set(grammar.TYPES) >= set(TABLE)
    assert {"Some", "None", "Ok", "Err"} <= set(grammar.library_variants())
    raw = GRAMMAR.read_text(encoding="utf-8")
    for word in [*EFFECTS, *PLACES, *grammar.BUILTINS, *grammar.TYPES]:
        assert re.search(rf"\b{re.escape(word)}\b", raw), word


PINNED = [
    ("fn fill(n:usize, out:rw<u64>[n]) {", "fn", "keyword.declaration.function.cairn"),
    ("fn fill(n:usize, out:rw<u64>[n]) {", "fill", "entity.name.function.cairn"),
    ("fn fill(n:usize, out:rw<u64>[n]) {", "out", "variable.parameter.cairn"),
    ("fn fill(n:usize, out:rw<u64>[n]) {", "rw", "storage.modifier.borrow.cairn"),
    ("fn fill(n:usize, out:rw<u64>[n]) {", "<", "punctuation.definition.typeparameters.begin.cairn"),
    ("fn fill(n:usize, out:rw<u64>[n]) {", "usize", "support.type.cairn"),
    ("fn sort[T:Ord + affine, N:nat](v:rw<Vec[T]>) {}", "T", "entity.name.type.parameter.cairn"),
    ("fn sort[T:Ord + affine, N:nat](v:rw<Vec[T]>) {}", "affine", "support.type.bound.cairn"),
    ("fn sort[T:Ord + affine, N:nat](v:rw<Vec[T]>) {}", "nat", "support.type.bound.cairn"),
    ("fn sort[T:Ord + affine, N:nat](v:rw<Vec[T]>) {}", "Vec", "entity.name.type.cairn"),
    ("fn saxpy(x:ro<f32>[n]@device) {", "device", "storage.modifier.placement.cairn"),
    ("fn say(n:usize) effects(io, ffi:write, read:text) {", "io", "support.constant.effect.cairn"),
    ("fn say(n:usize) effects(io, ffi:write, read:text) {", "ffi", "support.constant.effect.cairn"),
    ("fn say(n:usize) effects(io, ffi:write, read:text) {", "text", "variable.other.effect.cairn"),
    ("fn checksum(n:usize) -> u32 pure {", "pure", "storage.modifier.cairn"),
    ("struct Chart packed { rows:usize; price:Buf[f64][rows]; }", "Chart", "entity.name.type.cairn"),
    ("struct Chart packed { rows:usize; price:Buf[f64][rows]; }", "packed", "storage.modifier.layout.cairn"),
    ("struct Text { data:Buf[u8]; len:usize; lends data[0..len]; }", "lends", "storage.modifier.cairn"),
    ("struct Odd { lends:usize; }", "lends", "variable.other.property.cairn"),
    ("struct Chart packed { rows:usize; price:Buf[f64][rows]; }", "price", "variable.other.property.cairn"),
    ("struct Slot align(64) { hits:u64; }", "align", "storage.modifier.layout.cairn"),
    ("enum Op { Read; Write; }", "Op", "entity.name.type.cairn"),
    ("const LIMIT:usize = 4096;", "LIMIT", "variable.other.constant.cairn"),
    ("  if n > LIMIT { return LIMIT; }", "LIMIT", "variable.other.constant.cairn"),
    ("  let mut total:u64 = 0;", "let", "storage.type.binding.cairn"),
    ("  let mut total:u64 = 0;", "mut", "storage.modifier.cairn"),
    ("  let mut total:u64 = 0;", "total", "variable.other.declaration.cairn"),
    ("  total += 3;", "+=", "keyword.operator.assignment.compound.cairn"),
    ("  mask ^= 0xff;", "^=", "keyword.operator.assignment.compound.cairn"),
    ("  mask ^= 0xff;", "0xff", "constant.numeric.integer.hexadecimal.cairn"),
    ("  if a != b && c <= d { }", "!=", "keyword.operator.comparison.cairn"),
    ("  if a != b && c <= d { }", "&&", "keyword.operator.logical.cairn"),
    ("  let ratio = 1.5e3 * 2.0;", "1.5e3", "constant.numeric.float.cairn"),
    ("  match f() { Some(v) => return v; None => { return 0; } }", "Some", "variable.other.enummember.cairn"),
    ("  match f() { Some(v) => return v; None => { return 0; } }", "None", "variable.other.enummember.cairn"),
    ("  match f() { Some(v) => return v; None => { return 0; } }", "=>", "keyword.operator.arrow.cairn"),
    ("  match f() { Some(v) => return v; None => { return 0; } }", "match", "keyword.control.conditional.cairn"),
    ("  return Result.Ok(0);", "Ok", "variable.other.enummember.cairn"),
    ("  return Result.Ok(0);", "Result", "entity.name.type.cairn"),
    ("  let left = spawn fill(data[0..mid], 0);", "spawn", "keyword.control.concurrency.cairn"),
    ("  let left = spawn fill(data[0..mid], 0);", "fill", "entity.name.function.call.cairn"),
    ("  let left = spawn fill(data[0..mid], 0);", "..", "keyword.operator.range.cairn"),
    ("  spawn fetch(url) into g;", "into", "keyword.control.concurrency.cairn"),
    ("  let into = 3;", "into", "variable.other.declaration.cairn"),
    ("  wait(left);", "wait", "support.function.builtin.cairn"),
    ("  parallel i in n { out[i] = x[i]; }", "parallel", "keyword.control.concurrency.cairn"),
    ("  parallel i in n { out[i] = x[i]; }", "i", "variable.other.declaration.cairn"),
    ("plan fill { grain 64; lanes 4; }", "plan", "keyword.declaration.cairn"),
    ("plan fill { grain 64; lanes 4; }", "grain", "keyword.other.plan.cairn"),
    ("test sums { assert(total == 6); }", "test", "keyword.declaration.cairn"),
    ("test sums { assert(total == 6); }", "sums", "entity.name.function.cairn"),
    ("test sums { assert(total == 6); }", "assert", "support.function.builtin.cairn"),
    ("  let test = 3;", "test", "variable.other.declaration.cairn"),
    ("  out.push('=');", "push", "entity.name.function.member.cairn"),
    ("  out.push('=');", "=", "string.quoted.single.cairn"),
    ('  let s = "a\\n";', "\\n", "constant.character.escape.cairn"),
    ("  let v = vec.new[u64]();", "new", "entity.name.function.member.cairn"),
    ("  unsafe { asm(\"wfi\"); }", "unsafe", "keyword.other.unsafe.cairn"),
    ("  unsafe { asm(\"wfi\"); }", "asm", "support.function.builtin.cairn"),
    ('  asm volatile x86_64 "rdtsc" (out t:u64) clobbers(rax, rdx);', "volatile", "storage.modifier.cairn"),
    ('  asm volatile x86_64 "rdtsc" (out t:u64) clobbers(rax, rdx);', "out", "storage.modifier.cairn"),
    ('  asm volatile x86_64 "rdtsc" (out t:u64) clobbers(rax, rdx);', "clobbers", "storage.modifier.cairn"),
    ("  fill(out, 3);", "out", ""),  # an ordinary name everywhere else
    ("// E-LEASED: data is lent to left until wait(left).", "E-LEASED", "constant.other.diagnostic-code.cairn"),
    ("// E-LEASED: data is lent to left until wait(left).", "wait", "comment.line.double-slash.cairn"),
    ("import std.vec (Vec);", "std.vec", "entity.name.namespace.cairn"),
    ("import std.map as m;", "m", "entity.name.namespace.cairn"),
    ("fn area(self:ro<Self>) -> u64 = self.a;", "Self", "variable.language.self.cairn"),
    ("  impl std.core.Eq for R { fn same(a:ro<R>, b:ro<R>) -> bool = a.$f == b.$f; }", "$f", "variable.other.metavariable.cairn"),
]  # fmt: skip


@pytest.mark.parametrize(("source", "token", "scope"), PINNED, ids=[f"{t}:{s}" for _, t, s in PINNED])
def test_a_token_gets_its_scope(source, token, scope):
    assert scope_of(source, token) == scope


def test_an_unsafe_block_ends_at_its_own_brace():
    rows = engine().lines("unsafe { if a { b(); } c(); }\nd();")
    assert "meta.block.unsafe.cairn" in rows[0][len("unsafe { if a { b(); } ")]
    assert all("meta.block.unsafe.cairn" not in s for s in rows[1])


def textmate_modules() -> Path | None:
    """vscode-textmate and vscode-oniguruma from an editor already on this machine; never downloaded."""
    candidates = [os.environ.get("CAIRN_TEXTMATE_MODULES", "")]
    home = Path.home()
    candidates += [str(p) for pattern in (".cursor-server/bin/*/node_modules", ".vscode-server/bin/*/node_modules")
                   for p in sorted(home.glob(pattern))]  # fmt: skip
    return next((Path(c) for c in candidates if c and (Path(c) / "vscode-textmate").is_dir()
                 and (Path(c) / "vscode-oniguruma").is_dir()), None)  # fmt: skip


def test_the_real_engine_agrees_on_every_cairn_file():
    modules = textmate_modules()
    if shutil.which("node") is None or modules is None:
        pytest.skip("needs node and an installed vscode-textmate (set CAIRN_TEXTMATE_MODULES)")
    files = sorted(p for p in ROOT.rglob("*.cairn") if not {".claude", "results"} & set(p.relative_to(ROOT).parts))
    script = Path(__file__).with_name("textmate.js")
    run = subprocess.run(["node", str(script), str(modules), str(GRAMMAR), *map(str, files)], capture_output=True,
                         text=True, timeout=300, check=True)  # fmt: skip
    real, ours, disagree = json.loads(run.stdout), engine(), []
    for name, lines in real.items():
        text = Path(name).read_text(encoding="utf-8")
        for number, (line, tokens, mine) in enumerate(zip(text.split("\n"), lines, ours.lines(text), strict=True), 1):
            theirs = [None] * len(line)
            for start, end, scopes in tokens:
                theirs[start:end] = [scopes] * (end - start)
            wrong = [i for i, c in enumerate(line) if not c.isspace() and theirs[i] != mine[i]]
            disagree += [f"{Path(name).relative_to(ROOT)}:{number}:{wrong[0] + 1}"] if wrong else []
    assert len(files) > 40 and not disagree, disagree[:20]


def test_the_vim_syntax_loads_without_errors(tmp_path):
    if shutil.which("vim") is None:
        pytest.skip("vim is not installed")
    sample = tmp_path / "sample.cairn"
    sample.write_text("fn main() -> i32 { let mut x:u64 = 0; x += 1; return 0; }\n// E-LEASED\n", encoding="utf-8")
    probe, script = tmp_path / "probe.txt", tmp_path / "probe.vim"
    script.write_text("\n".join([
        f"set rtp^={ROOT / 'editors' / 'vim'}", "filetype on", "syntax on", f"edit {sample}",
        "redir! > " + str(probe), "echo &filetype", "echo synIDattr(synIDtrans(synID(1, 1, 1)), 'name')",
        "echo synIDattr(synID(1, 4, 1), 'name')", "echo synIDattr(synID(2, 4, 1), 'name')", "messages",
        "redir END", "qall!",
    ]), encoding="utf-8")  # fmt: skip
    run = subprocess.run(["vim", "-N", "-u", "NONE", "-i", "NONE", "-Es", "-S", str(script)], capture_output=True,
                         text=True, timeout=60)  # fmt: skip
    said = probe.read_text(encoding="utf-8").split()
    assert run.returncode == 0 and said[:4] == ["cairn", "Statement", "cairnFunction", "cairnDiagnostic"], (
        said,
        run.stderr,
    )
    assert not any(word.startswith("E") and word[1:4].isdigit() for word in said), said
