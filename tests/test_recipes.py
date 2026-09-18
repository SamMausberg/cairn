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

from cairn.agent_tools import canonical_source
from cairn.cairnc import RUNTIME_FILES, Diagnostic, compile_source
from cairn.formatting import format_source

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
    assert set(first) == {"layout.columns", "layout.powers", "std.wire.wire"}
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
