"""What the standard library promises as a whole, beside the programs of test_std.py that use it module by module.

The rejection table records the API guarantees that the type system, not a convention, is responsible for:
overlapping views cannot be handed to one call, an unhashable key cannot key a map. Each module's effect rows are
part of its contract; every generic of the library is instantiated once and built, and checked against its bounds;
no packaged source is hidden from git; and the generated reference under docs/ is what the compiler says today.
"""

import pathlib
import subprocess

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import native, refused

# The API guarantees that are types, not documentation ------------------------------------------

UPDATE_REACHES_THE_MAP = """
import std.map (Map);
fn main() -> i32 {
  let mut m = map.new[u64, u64]();
  m.insert(1, 10);
  let found = m.update(1, |v:rw<u64>| { m.insert(2, 20); v = 0; });
  return 0;
}
"""

OVERLAPPING_COPY = """
import std.mem;
fn main() -> i32 {
  buffer b:u8[8] = zeroed;
  let mid:usize = 3;
  mem.copy(3, b[0..mid], b[1..4]);
  return 0;
}
"""

UNHASHABLE_KEY = """
import std.map (Map);
struct Point { x:u64; y:u64; }
fn main() -> i32 {
  let mut m = map.new[Point, u64]();
  m.insert(Point(1, 2), 3);
  return 0;
}
"""

WRITE_THROUGH_READONLY = """
import std.mem;
fn wrong(n:usize, xs:ro<u8>[n]) { xs[0] = 1; }
fn main() -> i32 = 0;
"""

FILL_A_READONLY_VIEW = """
import std.mem;
fn wrong(n:usize, xs:ro<u8>[n]) { mem.fill(n, xs, 0); }
fn main() -> i32 = 0;
"""

CLOSURE_MOVES_OWNER = """
import std.sort;
fn main() -> i32 {
  buffer xs:u64[4] = zeroed;
  let held = Buf[u64](1);
  sort.sort_by(len(xs), xs, |a:ro<u64>, b:ro<u64>| -> bool { let stolen = held; return a < b; });
  return 0;
}
"""

MISMATCHED_EXTENT = """
import std.mem;
fn main() -> i32 {
  buffer a:u8[4] = zeroed;
  buffer b:u8[8] = zeroed;
  mem.copy(len(a), a, b);
  return 0;
}
"""


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-ALIAS", OVERLAPPING_COPY),
        ("E-ALIAS", UPDATE_REACHES_THE_MAP),  # the closure is lent the value while the call holds the map
        ("E-TRAIT-IMPL", UNHASHABLE_KEY),
        ("E-WRITE-LEASE", WRITE_THROUGH_READONLY),
        ("E-TYPE-MISMATCH", FILL_A_READONLY_VIEW),
        ("E-MOVE-IN-LOOP", CLOSURE_MOVES_OWNER),
        ("E-TYPE-MISMATCH", MISMATCHED_EXTENT),
    ],
)
def test_the_type_system_protects_the_api(code, source):
    refused(code, source)


# Effect rows are part of each module's contract ------------------------------------------------


def rows(source):
    return compile_source(source)[1]["functions"]


def test_sort_allocates_nothing_and_does_not_recurse():
    functions = rows("""
        import std.sort;
        fn main() -> i32 { buffer xs:u64[4] = zeroed; sort.sort(len(xs), xs); return 0; }
        """)
    row = set(functions["std.sort.sort[u64]"]["effects"])
    assert not {"alloc", "free", "io"} & row  # `diverge` is there: a while loop may not end
    assert {"read:xs", "write:xs", "indirect_call"} <= row


def test_map_growth_is_visible_in_the_callers_row():
    functions = rows("""
        import std.map (Map);
        fn main() -> i32 { let mut m = map.new[u64, u64](); m.insert(1, 2); return 0; }
        """)
    assert {"alloc", "free", "zero_init"} <= set(functions["std.map.insert[u64, u64]"]["effects"])
    assert {"alloc", "free"} <= set(functions["main"]["effects"])


def test_a_release_charges_free_without_charging_alloc():
    """A map of owning values releases the value it replaces, which `place` says and never allocates for."""
    functions = rows("""
        import std.map (Map);
        import std.vec (Vec);
        fn drops(v:Vec[u8]) {}
        fn main() -> i32 {
          let mut m = map.new[u64, Vec[u8]]();
          let mut first = vec.new[u8]();
          first.push(1);
          m.insert(7, first);
          let mut second = vec.new[u8]();
          m.insert(7, second);
          let mut third = vec.new[u8]();
          drops(third);
          return 0;
        }
        """)
    place = set(functions["std.map.place[u64, std.vec.Vec[u8]]"]["effects"])
    assert "free" in place and "alloc" not in place
    assert functions["drops"]["effects"] == ["free"]  # A function that only drops what it was given.
    assert {"alloc", "free"} <= set(functions["std.map.insert[u64, std.vec.Vec[u8]]"]["effects"])


def test_parallel_and_device_placement_are_separate_effects():
    functions = rows("""
        fn on_threads(n:usize, out:rw<u64>[n], src:ro<u64>[n]) { parallel i in n { out[i] = src[i] + 1; } }
        fn on_lanes(n:usize, out:rw<u64>[n]@device, src:ro<u64>[n]@device) { parallel i in n { out[i] = src[i] + 1; } }
        fn main() -> i32 = 0;
        """)
    assert "par:host" in functions["on_threads"]["effects"]
    assert "par:device" in functions["on_lanes"]["effects"]


COVERAGE = """
import std.core (Option, Eq, Ord, Hash);
import std.arena (Arena, Handle);
import std.map (Map);
import std.mem;
import std.sort;
import std.vec (Vec);

// Touch every generic the library declares: an instance, not its template, is what typechecks,
// so anything left uninstantiated here would ship unchecked.
fn main() -> i32 {
  let mut v = vec.with_capacity[u8](4);
  v.push('a');
  v.reserve(16);
  if v.capacity() < 16 { return 1; }
  v.set(0, 'z');
  if v.get(0) != 'z' { return 2; }
  v.extend_from(3, "abc");
  if v.len != 4 { return 3; }
  v.truncate(2);
  match v.pop() {
    Option.Some(b) => { if b != 'a' { return 4; } }
    Option.None => { return 5; }
  }
  v.clear();
  if v.len != 0 { return 6; }
  v.insert(0, 'q');
  v.insert(1, 'r');
  let q:u8 = 'q';
  match v.find(q) {
    Option.Some(at) => { if at != 0 { return 22; } }
    Option.None => { return 23; }
  }
  match v.remove(0) {
    Option.Some(b) => { if b != 'q' { return 24; } }
    Option.None => { return 25; }
  }
  match v.swap_remove(0) {
    Option.Some(b) => { if b != 'r' { return 26; } }
    Option.None => { return 27; }
  }

  buffer xs:u64[4] = zeroed;
  buffer ys:u64[4] = zeroed;
  mem.fill(len(xs), xs, 3);
  mem.copy(len(ys), ys, xs);
  if !mem.equal(len(xs), xs, len(ys), ys) { return 7; }
  sort.sort(len(xs), xs);
  sort.sort_by(len(xs), xs, |a:ro<u64>, b:ro<u64>| -> bool { return a < b; });
  sort.radix_sort(len(xs), xs, ys);
  let three:u64 = 3;
  match sort.search(len(xs), xs, three) {
    Option.Some(at) => {}
    Option.None => { return 8; }
  }

  let mut m = map.new[u64, u64]();
  for i in 0..32 { m.insert(u64(i), u64(i)); }
  if m.count() != 32 || m.slots() < 32 { return 9; }
  match m.find(three) {
    Option.Some(slot) => { if !m.live(slot) { return 10; } }
    Option.None => { return 11; }
  }
  match m.remove(three) {
    Option.Some(value) => { if value != 3 { return 12; } }
    Option.None => { return 13; }
  }
  if m.contains(three) { return 28; }
  match m.slot(4) {
    Option.Some(s) => { match m.resolve(s) { Option.Some(at) => {} Option.None => { return 29; } } }
    Option.None => { return 30; }
  }
  let bumped = m.update(4, |x:rw<u64>| { x = x + 1; });
  match m.get(4) {
    Option.Some(x) => { if !bumped || x != 5 { return 31; } }
    Option.None => { return 32; }
  }
  let at = m.entry(4, 0);
  m.vals[at] += 1;
  let mut order = vec.new[usize]();
  m.sorted(order);
  if order.len != m.count() || m.keys[order.data[3]] != 4 || m.vals[order.data[3]] != 6 { return 33; }

  let word = vec.from(3, "abc");
  let other = vec.from(3, "abd");
  if !less(word, other) || same(word, other) || hash(word) != vec.hash_view(3, "abc") { return 34; }
  if mem.compare(3, "abc", 2, "ab") != 1 { return 35; }
  let mut words = map.new[Vec[u8], u64]();
  let first = map.entry_view(words, 3, "abc", 1);
  words.vals[first] += 1;
  match map.find_view(words, 3, "abc") {
    Option.Some(slot) => { if words.vals[slot] != 2 { return 36; } }
    Option.None => { return 37; }
  }

  let mut a = arena.new[u64]();
  let mut last = a.insert(0);
  for i in 0..32 { last = a.insert(u64(i)); }
  if a.count() != 33 || a.slots() < 33 { return 14; }
  if !a.alive(last.slot) { return 15; }
  let same = a.handle(last.slot);
  if same.generation != last.generation { return 16; }
  match a.find(last) {
    Option.Some(slot) => { if a.items[slot] != 31 { return 17; } }
    Option.None => { return 18; }
  }
  match a.remove(last) {
    Option.Some(value) => { if value != 31 { return 19; } }
    Option.None => { return 20; }
  }
  match a.remove(last) {
    Option.Some(value) => { return 21; }
    Option.None => {}
  }
  let recycled = a.insert(7);
  if recycled.slot != last.slot { return 22; }
  return 0;
}
"""


def test_every_generic_in_the_library_is_instantiated_and_runs(tmp_path):
    receipt = compile_source(COVERAGE)[1]
    assert receipt["uninstantiated_templates"] == []
    native(tmp_path, COVERAGE)


def test_no_packaged_source_is_hidden_from_version_control():
    """`core.*` once ignored std/core.cairn: the suite was green here and broken in every fresh checkout."""
    root = pathlib.Path(__file__).resolve().parents[2]
    files = [str(p) for p in (root / "src" / "cairn").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    done = subprocess.run(["git", "check-ignore", *files], cwd=root, capture_output=True, text=True)
    if done.returncode == 128:
        pytest.skip("not a git checkout")
    assert done.stdout == ""


def test_every_packaged_template_needs_only_what_its_bounds_promise():
    """The library's generics are checked once against their bounds, so a misuse is reported at the call
    (`Token is linear, not affine; std.vec.push needs [T:affine]`), never from inside the library."""
    from cairn.compiler.cairnc import certify_templates

    modules = sorted(p.stem for p in (pathlib.Path(__file__).resolve().parents[2] / "src/cairn/std").glob("*.cairn"))
    source = "".join(f"import std.{m};\n" for m in modules) + "fn main() -> i32 { return 0; }\n"
    verdicts = certify_templates(source)
    assert len(verdicts) >= 40 and {n: v for n, v in verdicts.items() if v != "ok"} == {}
    token = "import std.vec as vec;\nlinear struct Token { id:u64; }\n"
    said = refused("E-BOUND", token + "fn main() -> i32 { let mut v = vec.new[Token](); return 0; }")["message"]
    assert "std.vec.new needs [T:affine]" in said


def test_the_api_reference_is_what_the_compiler_says_today():
    """docs/std_api.md and docs/std/ are generated (`make docs`): signatures, bounds, comments and effect rows."""
    from cairn.editor.docs import GENERATED, NOTE, document, standard_library, standard_library_pages

    root = pathlib.Path(__file__).resolve().parents[2]
    pages = standard_library_pages()
    assert {f"std/{p.name}" for p in (root / "docs/std").glob("*.md")} | {"std_api.md"} == set(pages), "run `make docs`"
    for name, text in pages.items():
        assert (root / "docs" / name).read_text(encoding="utf-8") == text, f"run `make docs`: docs/{name} drifted"
    whole = standard_library()
    for name in set(pages) - {"std_api.md"}:  # each page is its module's section of the one document, word for word
        assert pages[name].removeprefix(GENERATED).removesuffix("\n\n" + NOTE + "\n") in whole, name
    alone = standard_library(["std.text"]).removeprefix(GENERATED + NOTE + "\n\n")
    assert alone.startswith("# std.text") and alone.removesuffix("\n") in whole and "# std.io" not in alone
    own = document("module m;\n// Doubles.\npub fn twice[T: integer](x:T) -> T = x + x;\nfn hidden() {}\n")
    assert "pub fn twice[T:integer](x:T) -> T" in own and "Doubles." in own and "hidden" not in own
    assert "pub fn twice[T:integer](x:T) -> T  // effects: trap" in own
    assert own.startswith("A generic function's effects are what it may do for any arguments within its bounds")


def examples() -> dict[str, list[str]]:
    """Every fenced `cairn` example in a std module's own comment, by module, as `cairn doc` prints it."""
    import re

    from cairn.editor.docs import introduction

    found = {}
    for path in sorted((pathlib.Path(__file__).resolve().parents[2] / "src/cairn/std").glob("*.cairn")):
        text = path.read_text(encoding="utf-8")
        told = "\n".join(introduction(text, re.search(r"^module ", text, re.M).start()))
        found[path.stem] = re.findall(r"^```cairn\n(.*?)^```", told, flags=re.S | re.M)
    return found


def test_an_example_in_a_module_comment_is_a_program_the_compiler_accepts():
    """`cairn doc --std --module std.map` shows a word count; like every example in docs/, it compiles."""
    found = examples()
    assert len(found["map"]) == 1 and "map.entry_view" in found["map"][0]
    for module, sources in found.items():
        for source in sources:
            assert compile_source(source)[0], module


def test_doc_std_prints_only_the_modules_named(capsys):
    """`cairn doc --std --module text` is one module's signatures, not the whole library an agent then pages through."""
    from cairn.cli import main

    assert main(["doc", "--std", "--module", "text", "--module", "std.vec"]) == 0
    printed = capsys.readouterr().out
    assert "# std.text" in printed and "# std.vec" in printed and "# std.io" not in printed
    assert main(["doc", "--std", "--module", "texts", "--format", "json"]) == 2
    assert "No packaged module std.texts: the library has std.arena" in capsys.readouterr().out
    assert main(["doc", "--std", "--module", "text", "--pages", "unused", "--format", "json"]) == 2
