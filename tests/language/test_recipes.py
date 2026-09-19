"""Typed recipes: generators written as library code, applied by `derive`, checked as ordinary code.

The packaged `std.wire` recipe replaced a closed Python generator; its output is pinned against the
bytes that generator produced. Everything else here is the general mechanism: static iteration over
record schemas and natural ranges, `where` values, `$` splices, generated records, admissible
domains, budgets, hygiene, privacy and the content hash a receipt pins.
"""

import hashlib
import shutil
import subprocess

import pytest

from cairn.agent.agent_tools import canonical_source, expanded_source
from cairn.compiler.cairnc import RUNTIME_FILES, Diagnostic, compile_source
from cairn.editor.formatting import format_source

LAYOUT = """
module layout;

// Structure of arrays: one column per field, with row access. Written once, derived per record.
pub recipe columns for R {
  each f in R { require scalar(f), "columns holds scalar fields."; }
  pub struct $R_columns { each f in R where t = typeof(f) { $f:Buf[$t]; } }
  pub fn $R_columns_new(rows:usize) -> $R_columns = $R_columns(each f in R where t = typeof(f) { Buf[$t](rows) });
  pub fn $R_get(c:ro<$R_columns>, i:usize) -> R = R(each f in R { c.$f[i] });
  pub fn $R_set(c:rw<$R_columns>, i:usize, row:R) { each f in R { c.$f[i] = row.$f; } }
  each f in R {
    pub fn $R_sum_$f(c:ro<$R_columns>) -> f64 {
      let mut total:f64 = 0.0;
      for i in 0..len(c.$f) { total = total + f64(c.$f[i]); }
      return total;
    }
  }
}

// Static folds compute over the schema; a run-time fold takes an operator or any function of two operands;
// an `each` may splice several expressions per step; a recipe may generate a device helper.
pub recipe stats for R where width = fold + each f in R { bytes(f) }, widest = fold max each f in R { bytes(f) } {
  each f in R { require unsigned(f), "stats folds unsigned fields."; }
  pub struct $R_range { each f in R where t = typeof(f) { lo_$f:$t; hi_$f:$t; } }
  pub fn $R_point(v:R) -> $R_range = $R_range(each f in R { v.$f, v.$f });
  pub fn $R_width() -> usize = $width + min($widest, 0);
  pub fn $R_widest() -> usize = $widest;
  pub fn $R_checksum(v:R) -> u64 = fold add_wrap each f in R { u64(v.$f) };
  kernel fn $R_scaled(x:u64) -> u64 = mul_wrap(x, $width);
}

// A family by another name: one function per natural in a range.
pub recipe powers[LO:nat, HI:nat] {
  each k in LO..HI { pub fn shift_$k(x:u64) -> u64 pure = shl_wrap(x, $k); }
}
"""

APP = (
    LAYOUT
    + """
module app;
import layout;
struct Particle { x:f32; y:f32; mass:f64; id:u32; }
struct Packet { kind:u8; size:u32; tag:u16; }
derive layout.columns for Particle;
derive layout.powers[1, 4];
derive wire for Packet;
derive layout.stats for Packet;
pub fn main() -> i32 {
  let mut cols = Particle_columns_new(4);
  for i in 0..4 { Particle_set(cols, i, Particle(f32(i), 2.0, 1.5, u32(i))); }
  let row = Particle_get(cols, 3);
  if row.id != 3 || Particle_sum_mass(cols) != 6.0 || Particle_sum_x(cols) != 6.0 { return 1; }
  if shift_1(1) + shift_2(1) + shift_3(1) != 14 { return 2; }
  stack bytes:u8[7] = zeroed;
  encode_Packet(bytes, Packet(9, 0x01020304, 7));
  let back = decode_Packet(bytes);
  if bytes[1] != 4 || bytes[4] != 1 || back.size != 0x01020304 || back.tag != 7 || wire_size_Packet() != 7 { return 3; }
  let at = Packet_point(Packet(9, 5, 7));
  if Packet_width() != 7 || Packet_widest() != 4 || Packet_checksum(back) != 0x01020304 + 16 { return 4; }
  if at.lo_kind != 9 || at.hi_size != 5 || at.hi_tag != 7 { return 5; }
  return 0;
}
"""
)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_recipes_generate_records_functions_and_ranges_that_run(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    cpp, receipt = compile_source(APP, roots=("app.main",))
    (tmp_path / "p.cpp").write_text(cpp + "int main() { return static_cast<int>(cf_app_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]
    flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"] if cxx == "clang++" else []
    subprocess.run([cxx, *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=180)
    assert subprocess.run([tmp_path / "p"], timeout=60).returncode == 0
    rows = receipt["functions"]
    assert rows["app.shift_2"]["effects"] == ["trap"]  # The recipe's `pure` ceiling held for the instance.
    assert {"alloc", "free"} <= set(rows["app.Particle_columns_new"]["effects"])
    assert receipt["derivations"][0] == {"module": "app", "recipe": "layout.columns", "naturals": [], "for": "Particle"}
    assert receipt["wire_derivations"] == ["app.Packet"]


def test_a_receipt_pins_each_recipe_by_the_hash_of_its_tokens():
    first = compile_source(APP)[1]["recipes"]
    assert set(first) == {"layout.columns", "layout.powers", "layout.stats", "std.wire.wire"}
    assert all(len(digest) == 64 for digest in first.values())
    respaced = APP.replace("pub recipe powers[LO:nat, HI:nat] {", "pub recipe powers[LO:nat,HI:nat]\n{  // same tokens")
    assert compile_source(respaced)[1]["recipes"] == first  # Layout and comments are not the recipe.
    changed = APP.replace("shl_wrap(x, $k)", "shl_wrap(x, $k + 1)")
    assert compile_source(changed)[1]["recipes"]["layout.powers"] != first["layout.powers"]


def test_expansion_is_a_function_of_its_inputs():
    assert compile_source(APP)[0] == compile_source(APP)[0]
    digest = hashlib.sha256(compile_source(APP)[0].encode()).hexdigest()
    assert digest == hashlib.sha256(compile_source(APP)[0].encode()).hexdigest()


REJECTED = {
    "a field outside the recipe's admissible domain, with the recipe's own message": (
        "E-DERIVE-DOMAIN",
        LAYOUT + "module app;\nimport layout;\nstruct Bag { items:Buf[u64]; }\nderive layout.columns for Bag;",
    ),
    "wire keeps the diagnostic code of the generator it replaced": (
        "E-DERIVE-FIELD",
        "struct P { x:f32; }\nderive wire for P;",
    ),
    "a recipe that does not exist": ("E-DERIVE-RECIPE", "struct P { x:u32; }\nderive nothing for P;"),
    "arguments the recipe does not declare": (
        "E-DERIVE-RECIPE",
        LAYOUT + "module app;\nimport layout;\nderive layout.powers[1];",
    ),
    "a target that is not a record": ("E-DERIVE-TYPE", "enum E { A; B; }\nderive wire for E;"),
    "a generated name that already exists": (
        "E-DERIVE-COLLISION",
        "struct P { x:u32; }\nfn encode_P() {}\nderive wire for P;",
    ),
    "the same recipe derived twice for one type": (
        "E-DERIVE-COLLISION",
        "struct P { x:u32; }\nderive wire for P;\nderive wire for P;",
    ),
    "another module's private recipe": (
        "E-PRIVATE",
        "module lib;\nrecipe secret for R { pub fn id_$R() -> usize = 1; }\n"
        "module app;\nimport lib;\nstruct P { x:u32; }\nderive lib.secret for P;",
    ),
    "another module's private record": (
        "E-PRIVATE",
        "module lib;\nstruct Secret { code:u32; }\nmodule app;\nimport lib;\nderive wire for lib.Secret;",
    ),
    "a static iteration past its bound": (
        "E-EXPANSION-LIMIT",
        "recipe many[N:nat] { each k in 0..N { pub fn f_$k() -> usize = $k; } }\nderive many[5000];",
    ),
    "a static name the recipe never bound": (
        "E-RECIPE-STATIC",
        "recipe bad for R { pub fn size_$R() -> usize = $missing; }\nstruct P { x:u32; }\nderive bad for P;",
    ),
    "a runtime value where a static one is needed": (
        "E-RECIPE-STATIC",
        "recipe bad for R { pub fn f_$R(n:usize) -> usize { each k in 0..n { } return 0; } }\n"
        "struct P { x:u32; }\nderive bad for P;",
    ),
    "generated code is checked like any other: a recipe cannot smuggle an effect past its ceiling": (
        "E-EFFECT-CEILING",
        "recipe leaky for R where n = bytes(R) { pub fn make_$R() -> usize pure { let b = Buf[u8]($n); return len(b); } }\n"
        "struct P { x:u32; }\nderive leaky for P;",
    ),
    "a type parameter other than the one after `for`": ("E-RECIPE", "recipe r[T] { }"),
    # Found by the fourth review ------------------------------------------------------------------
    "a static division by zero (was a Python traceback)": (
        "E-RECIPE-STATIC",
        "recipe d[N:nat] where q = 8 / N { pub fn g() -> usize = $q; }\nderive d[0];",
    ),
    "static arithmetic on a type (was a Python traceback)": (
        "E-RECIPE-STATIC",
        "recipe c for R where n = 0 - typeof(R) { pub fn g_$R() -> usize = $n; }\nstruct P { a:u32; }\nderive c for P;",
    ),
    "a static range bounded by a type (was a Python traceback)": (
        "E-RECIPE-STATIC",
        "recipe c for R { each k in 0..typeof(R) { pub fn g_$k() -> usize = 1; } }\nstruct P { a:u32; }\nderive c for P;",
    ),
    "a negative static, which would have become a huge unsigned literal": (
        "E-RECIPE-STATIC",
        "recipe d[N:nat] where q = N - 5 { pub fn g() -> u64 = u64($q); }\nderive d[1];",
    ),
    "nested iteration over nothing (the compiler used to hang)": (
        "E-EXPANSION-LIMIT",
        "recipe bomb { pub fn f() -> usize { each a in 0..1024 { each b in 0..1024 { each c in 0..1024 { } } }\n"
        "  return 0; } }\nderive bomb;",
    ),
    "splices outside a recipe are not syntax": ("E-LEX", "fn f() -> usize = $n;"),
    "a generated record cannot repeat a field": (
        "E-DERIVE-COLLISION",
        "recipe t for R { struct $R_table { each f in R { $f:u64; } rows:usize; } }\nstruct P { rows:u32; }\nderive t for P;",
    ),
    "fold says what it takes": ("E-PARSE", "recipe w for R { fn f() -> usize = fold add_wrap 3; }"),
    "a static fold combines naturals": (
        "E-RECIPE-STATIC",
        "recipe w for R where n = fold + each f in R { typeof(f) } { fn f() -> usize = $n; }\nstruct P { a:u8; }\n"
        "derive w for P;",
    ),
    "a static fold of nothing has no value": (
        "E-RECIPE-STATIC",
        "recipe w where n = fold + each i in 0..0 { i } { fn f() -> usize = $n; }\nderive w;",
    ),
    "a generated kernel is still a kernel": (
        "E-PLACEMENT",
        "recipe k for R { kernel fn twice_$R(x:u64) -> u64 = x * 2; }\nstruct P { a:u8; }\nderive k for P;\n"
        "fn host() -> u64 = twice_P(2);",
    ),
}


@pytest.mark.parametrize("name", REJECTED)
def test_rejections(name):
    code, source = REJECTED[name]
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code, e.value.data["message"]


def test_the_projection_and_the_formatter_keep_recipes_and_derivations():
    projected = canonical_source(APP)
    assert "pub recipe columns for R {" in projected and "derive layout.powers[1, 4];" in projected
    assert compile_source(projected)[0] == compile_source(APP)[0]
    formatted = format_source(APP)
    assert format_source(formatted) == formatted and compile_source(formatted)[0] == compile_source(APP)[0]


CAPTURE = """
module lib;
pub trait Same { fn same(a:ro<Self>, b:ro<Self>) -> bool; }
pub fn helper(x:u64) -> u64 = 1;
pub recipe go for R {
  pub fn $R_go(v:ro<R>) -> u64 = helper(v.x);
  impl Same for R { fn same(a:ro<R>, b:ro<R>) -> bool = a.x == b.x; }
}
pub fn alike[T:Same](a:ro<T>, b:ro<T>) -> bool = same(a, b);
module app;
import lib;
trait Same { fn same(a:ro<Self>, b:ro<Self>) -> bool; }
struct P { x:u64; }
fn helper(x:u64) -> u64 = 99;
derive lib.go for P;
pub fn main() -> i32 { let p = P(1); if !lib.alike(p, p) { return 7; } return i32(P_go(p)); }
"""


def test_a_name_a_recipe_writes_means_what_it_means_in_the_recipes_module(tmp_path):
    """Hygiene: the deriving module's `helper` and `Same` do not capture the recipe's. Only `$` splices (and the
    names they build) belong to the deriving module; everything else is spelled out in full when it is expanded."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    shown = expanded_source(CAPTURE)
    assert "return lib.helper(v.x);" in shown and "impl lib.Same for app.P {" in shown
    (tmp_path / "p.cpp").write_text(
        compile_source(CAPTURE, roots=("app.main",))[0] + "int main() { return cf_app_main(); }\n"
    )
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    subprocess.run(
        ["clang++", "-std=c++20", "-O1", str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120
    )
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == 1  # lib.helper ran, and P implements lib.Same.
    with pytest.raises(Diagnostic) as e:  # A private helper is private from where the code lands, and says so.
        compile_source(CAPTURE.replace("pub fn helper(x:u64) -> u64 = 1;", "fn helper(x:u64) -> u64 = 1;"))
    assert e.value.data["code"] == "E-PRIVATE"


NAMED = """
module m;
pub fn twice[T:numeric](x:T) -> T = x + x;
fn hidden(x:u64) -> u64 = x;
pub recipe fieldwise[F:fn] for R { pub fn $F_$R(v:R) -> R = R(each f in R { $F(v.$f) }); }
pub fn chain(seed:u64, value:u64) -> u64 pure = mul_wrap(seed ^ value, 1099511628211);
pub recipe scaled[K:nat, F:fn] for R {
  pub fn $F_$K_$R(v:R) -> u64 = fold add_wrap each f in R { mul_wrap(u64($F(v.$f)), $K) };
  pub fn key_$K_$R(v:R) -> u64 = fold m.chain each f in R { u64(v.$f) };
}
module app;
import m;
struct P { a:u32; b:f64; }
struct Q { a:u32; b:u64; }
fn half[T:numeric](x:T) -> T = x / 2;
derive m.fieldwise[half] for P;
derive m.fieldwise[m.twice] for P;
derive m.scaled[3, m.twice] for Q;
pub fn main() -> i32 {
  let low = half_P(P(4, 3.0));
  let high = twice_P(P(4, 3.0));
  if low.a != 2 || low.b != 1.5 || high.a != 8 || high.b != 6.0 || twice_3_Q(Q(1, 2)) != 18 { return 1; }
  if key_3_Q(Q(1, 2)) != m.chain(1, 2) { return 2; }
  return 0;
}
"""


def test_a_recipe_takes_the_name_of_a_function(tmp_path):
    """`[F:fn]`: the name is spliced as the deriving module wrote it and means what it means there, so a generic
    function serves fields of different types and another module's private function stays private."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    cpp, receipt = compile_source(NAMED, roots=("app.main",))
    assert receipt["derivations"][2] == {"module": "app", "recipe": "m.scaled", "naturals": [3, "m.twice"], "for": "Q"}
    (tmp_path / "p.cpp").write_text(cpp + "int main() { return static_cast<int>(cf_app_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-Werror"]
    subprocess.run(["clang++", *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=180)
    assert subprocess.run([tmp_path / "p"], timeout=60).returncode == 0
    assert "derive m.scaled[3, m.twice] for Q;" in canonical_source(NAMED)
    for code, more in [
        ("E-PRIVATE", "derive m.fieldwise[m.hidden] for Q;"),
        ("E-CALLEE", "derive m.fieldwise[nothing] for Q;"),
        ("E-DERIVE-RECIPE", "derive m.fieldwise[3] for Q;"),
        ("E-DERIVE-RECIPE", "derive m.scaled[half, half] for Q;"),
    ]:
        with pytest.raises(Diagnostic) as e:
            compile_source(NAMED + more)
        assert e.value.data["code"] == code, e.value.data["message"]


def test_a_diagnostic_inside_generated_code_names_its_derivation():
    source = (
        "recipe bad for R {\n  fn total_$R(v:R) -> u64 = fold + each f in R { v.$f };\n}\nstruct P { a:u32; b:u64; }\n"
    )
    with pytest.raises(Diagnostic) as e:
        compile_source(source + "struct Q { a:u64; }\nderive bad for Q;\nderive bad for P;\n")
    assert (e.value.data["line"], e.value.data["derived"]) == (2, "derive bad for P")  # The recipe's line, P's copy.


def test_expand_shows_what_the_derivations_generated_as_source():
    shown = expanded_source(APP)
    assert (
        shown.startswith("module app;\n") and "module layout;" not in shown
    )  # Only what was generated, where it lives.
    assert "pub struct Particle_columns { x:Buf[f32]; y:Buf[f32]; mass:Buf[f64]; id:Buf[u32]; }" in shown
    assert "pub fn shift_2(x:u64) -> u64 pure {\n  return shl_wrap(x, 2);\n}" in shown
    assert "fn Packet_width() -> usize {\n  return (7 + min(4, 0));\n}" in shown
    assert "impl std.core.Eq for Key {\n  fn same(a:ro<Key>, b:ro<Key>) -> bool {" in expanded_source(DERIVED)


HYGIENE = """
module lib;
recipe wire for R { pub fn zz_$R() -> usize = 1; }                                        // private and unrelated
module app;
fn t(x:u32) -> u32 = x + 100;
recipe cap for R { each f in R where t = typeof(f) { pub fn g_$f(y:$t) -> $t = t(y); } }   // t the function, $t the type
recipe mk for R { pub struct $R_box { v:u32; n:u16; } }
struct P { a:u32; }
derive wire for P_box;                                                                     // generated two lines below
derive cap for P;
derive mk for P;
pub fn main() -> i32 {
  if g_a(1) != 101 { return 1; }
  if wire_size_P_box() != 6 { return 2; }
  return 0;
}
"""


def test_only_dollar_splices_are_rewritten_and_derivations_wait_for_what_they_need(tmp_path):
    """A `where` name once rewrote the ordinary identifier `t`; a private `wire` elsewhere once hid std.wire."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    cpp = compile_source(HYGIENE, roots=("app.main",))[0]
    assert "cf_app_t(" in cpp
    (tmp_path / "p.cpp").write_text(cpp + "int main() { return static_cast<int>(cf_app_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O1", "-fno-exceptions", "-fsanitize=address,undefined"]
    subprocess.run(["clang++", *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=180)
    assert subprocess.run([tmp_path / "p"], timeout=60).returncode == 0


DERIVED = """
import std.core (Ord, Eq, Hash, Option);
import std.map as map;
import std.sort as sort;

struct Key { shard:u16; id:i64; }
struct Tagged { key:Key; tag:u8; live:bool; }                 // a derived impl serves the next one
derive eq for Key;
derive ord for Key;
derive eq for Tagged;
struct Name { a:u32; b:u64; }
derive eq for Name;
derive hash for Name;

fn main() -> i32 {
  let a = Key(1, -9);
  let b = Key(1, 10);
  if same(a, b) || !same(a, a) || !less(a, b) || less(b, a) { return 1; }
  if !same(Tagged(a, 1, true), Tagged(a, 1, true)) || same(Tagged(a, 1, true), Tagged(a, 1, false)) { return 2; }
  let mut keys = Buf[Key](3);
  keys[0] = Key(2, 1);
  keys[1] = Key(1, 7);
  keys[2] = Key(1, -3);
  sort.sort(len(keys), keys);                      // std.sort asks for [T: Ord]
  if keys[0].id != -3 || keys[2].shard != 2 { return 3; }
  let mut seen = map.new[Name, u64]();             // std.map asks for [K: Hash + Eq + affine]
  map.insert(seen, Name(1, 7), 70);
  match map.find(seen, Name(1, 7)) {
    Option.Some(slot) => { if seen.vals[slot] != 70 { return 4; } }
    Option.None => { return 5; }
  }
  if hash(Name(1, 7)) == hash(Name(7, 1)) { return 6; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_derived_implementations_serve_generic_library_code(tmp_path, cxx):
    """`derive eq|ord|hash` are recipes in std.derived that generate impls; std.core's own impls are one bounded
    blanket impl per class of scalars, which applies exactly where its bound holds."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    cpp = compile_source(DERIVED, roots=("main",))[0]
    (tmp_path / "p.cpp").write_text(cpp + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = [
        "-std=c++20",
        "-O1",
        "-fno-exceptions",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-parameter",
        "-Wno-unused-variable",
    ]
    flags += ["-fsanitize=address,undefined"] if cxx == "clang++" else []
    subprocess.run([cxx, *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=180)
    assert subprocess.run([tmp_path / "p"], timeout=60).returncode == 0


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("E-TRAIT-IMPL", "struct P { x:f32; }\nderive eq for P;"),  # No Eq for floats: NaN is not equal to itself.
        ("E-TRAIT-IMPL", "struct P { x:i64; }\nderive hash for P;"),  # Hash is offered for unsigned scalars.
        ("E-TRAIT-OVERLAP", "import std.core (Eq);\nstruct P { x:u32; }\nderive eq for P;\n"
         "impl Eq for P { fn same(a:ro<P>, b:ro<P>) -> bool = true; }\nfn main() -> i32 { if same(P(1), P(1)) { return 0; } return 1; }"),
        ("E-TRAIT-IMPL", "import std.core (Ord);\nstruct P { x:u32; }\nfn main() -> i32 { if less(P(1), P(2)) { return 0; } return 1; }"),
    ],
)  # fmt: skip
def test_a_derived_impl_is_checked_like_a_written_one(code, source):
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code, e.value.data["message"]
