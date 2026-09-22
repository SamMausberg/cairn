# Modules, projects and recipes

## Modules

`module net.http;` names the module of the declarations that follow; a file without one shares the root namespace. `pub` exports a declaration, and impl members are always public. `import net.http;` lets you write `http.get(...)` and `http.Request`, `import a.b as c;` renames, and `import std.core (Option, Result);` also brings those names in unqualified. An import may not hide a name the importing module declares (`E-DUPLICATE`). A private record's fields are as private as the record.

A `family` over another module's template needs that template to be `pub`, and its instances belong to the module that wrote the family (`pub family` exports them). `derive wire for R;` is written in the module that declares `R`.

```cairn
module codec;
import std.core (Option);

struct Limits { largest:u32; }                            // private: so are its fields
pub fn accepts(size:u32) -> Option[u32] {
  let limits = Limits(1480);
  if size > limits.largest { return Option.None; }
  return Option.Some(size + 5);
}

module app;
import codec as packet;
import std.core (Option);

pub fn main() -> i32 {
  match packet.accepts(40) { Option.Some(total) => { if total != 45 { return 1; } } Option.None => { return 2; } }
  match packet.accepts(9000) { Option.Some(total) => { return 3; } Option.None => {} }
  return 0;
}
```

```cairn rejects E-DUPLICATE
module lib;
pub const LIMIT:usize = 99;
module app;
import lib (LIMIT);
const LIMIT:usize = 1;
pub fn main() -> i32 { return i32(LIMIT); }
```

```text
import (LIMIT) collides with app.LIMIT; drop one or use the qualified name.
```

A project's files are compiled together in manifest order. The `std.*` modules ship inside the package and are linked on demand. Only project modules and `std.*` can be imported, and nothing is downloaded.

## Projects

`cairn.toml` lists ordered sources and independent task files. It is data, never a build script. A command takes a source file, a project directory or a manifest by path, so one tree may hold a second configuration (`cairn run app/gpu.toml`).

```toml
[project]
name = "gateway"
sources = ["src/frames.cairn", "src/main.cairn"]

[dependencies]
geometry = "deps/geometry"

[build]
kind = "exe"            # or "library"
arch = "baseline"       # or a named profile of the host family (x86-64, AArch64)
target = "hosted"       # or a board such as "aarch64-virt"
```

A freestanding `target` refuses any program whose effect rows need a hosted runtime ([the freestanding profile](../freestanding.md)). The host chooses trusted compilers (`clang++`, `g++`, and `nvcc` when a program uses the device), and builds use fresh directories. Generated C++ is readable and keeps the C ABI for every function whose signature is C compatible. A library exports every function; an executable contains only what its `main` reaches, and `main` may live in a module, while the receipt still covers everything that was checked.

`--debug` adds symbols and `#line` maps to the authored files. `--incremental` compiles one object per module against a shared interface header and reuses an object only when its unit, that header, the command line, the runtime headers and the compiler version hash to the same key, and the stored object still matches the digest written beside it in `build/objects`. It gives up inlining across modules, and device programs and freestanding images stay one unit either way ([tooling](../tools.md) has the measured sessions).

## Dependencies

`[dependencies] geometry = "deps/geometry"` names a project vendored inside this one's root, with its own `cairn.toml` and its own dependencies loaded first, at most 16 per manifest and 4 deep, a diamond loaded once. A dependency contributes modules only, and only what it marks `pub` is reachable. Nothing is fetched, no path leaves the root, no path has a `.` or `..` segment, no symbolic link is followed, and the receipt pins each dependency's manifest and sources by hash.

A dependency's manifest is read by the same checker as yours, so an unknown table or option is refused there too, and its `[build]`, which the build ignores, must still name a known kind, architecture and target. One directory is one project under one name: a second name for it is an error, not a diamond, and one name is one project of the build, the root's own included.

A module belongs to exactly one project. No project declares a `std.*` module, reopening a module another project declared names both projects and fails, and so does a file with no `module` header that would silently continue a dependency's. An executable's entry point is searched only in the sources this manifest lists, so a dependency neither supplies `main` nor denies you yours.

## Recipes

A recipe is a generator written as library code: ordinary declarations (functions, records, trait `impl`s, `kernel fn`) over a record schema (`for R`), naturals (`recipe tiles[W:nat, H:nat]`) and names of functions (`recipe fieldwise[F:fn] for R`). `derive name[arguments] for Type;` applies one.

Inside a recipe, `each f in R { }` iterates statically over a record's fields and `each k in lo..hi { }` over a natural range, at declaration level, at statement level, in a record's field list, or among the arguments of a call, where it splices one or several expressions per step (`each f in R { lo.$f, hi.$f }`). `fold | each ... { e }` joins the expansions with one operator, or with any function of two operands (`fold add_wrap each ...`, `fold lib.chain each ...`).

`where a = offset(f), t = typeof(f)` names static values. They are computed from naturals, comparisons, `min`, `max`, a static `fold` over a static `each` (`where width = fold + each f in R { bytes(f) }`) and the facts `bytes bits offset index count typeof unsigned signed integer float scalar record`. Static values are naturals and booleans; a negative result or a division by zero is `E-RECIPE-STATIC`.

`$name` splices one into an identifier (`encode_$R`, `value.$f`, `shift_$k`), and a whole `$name` is that natural or that type. The longest static name wins, so `$R_columns` is `$R` then `_columns`. Only the bare `for` parameter `R` is the type itself: every other static needs its `$`, so an ordinary identifier that happens to share a `where` name is left alone. `require condition, "message";` states the admissible inputs (`E-DERIVE-DOMAIN`, or the code the message opens with).

```cairn
module layout;

pub recipe columns for R {                                  // R: the record it is derived for
  each f in R { require scalar(f), "columns holds scalar fields."; }
  pub struct $R_columns { each f in R where t = typeof(f) { $f:Buf[$t]; } }
  pub fn $R_columns_new(rows:usize) -> $R_columns = $R_columns(each f in R where t = typeof(f) { Buf[$t](rows) });
  pub fn $R_get(c:ro<$R_columns>, i:usize) -> R = R(each f in R { c.$f[i] });
  pub fn $R_set(c:rw<$R_columns>, i:usize, row:R) { each f in R { c.$f[i] = row.$f; } }
}

module app;
import layout;

struct Particle { x:f32; mass:f64; }
derive layout.columns for Particle;                         // Particle_columns, _new, _get, _set

pub fn main() -> i32 {
  let mut columns = Particle_columns_new(4);
  Particle_set(columns, 2, Particle(1.5, 3.0));
  let row = Particle_get(columns, 2);
  if row.mass != 3.0 || row.x != 1.5 { return 1; }
  return 0;
}
```

```cairn rejects E-DERIVE-DOMAIN
module layout;
pub recipe columns for R {
  each f in R { require scalar(f), "columns holds scalar fields."; }
  pub struct $R_columns { each f in R where t = typeof(f) { $f:Buf[$t]; } }
}
module app;
import layout;
struct Frame { body:Buf[u8]; }
derive layout.columns for Frame;
```

```text
columns holds scalar fields.
```

A function name is spliced as the deriving module wrote it (`$F(v.$f)` calls it; inside a longer identifier, `$F_$R`, it gives its last segment) and means what it means there, so one generic function serves fields of different types and privacy is judged from the deriving module. Recipes take types and naturals, not expressions: behavior reaches a generated function as a `fn` value or a closure. A recipe over a natural range generates one function per step, as `family` does for one template.

Expansion happens before checking and reads nothing but the recipe and the schema, so it is a function of its inputs; `cairn expand` prints what it produced, as source. Expansion is hygienic: a function, type, trait or constant the recipe names means what it means in the recipe's own module and is spelled out in full where the code lands (`helper(x)` becomes `lib.helper(x)`, so the deriving module's own `helper` cannot capture it, and a private one is `E-PRIVATE` from there). Only `$` splices, and the names they build, belong to the deriving module.

What a recipe generates is ordinary code of the deriving module, checked like any other. Its signatures and effect ceilings are its contract, and privacy is judged where `derive` is written, though a recipe reaches its own module's helpers by their public path.

A generated name that already exists is `E-DERIVE-COLLISION`, and static iteration is bounded per level and in total (`E-EXPANSION-LIMIT`). A derivation for a record that another derivation generates waits for it, whatever order they are written in. The receipt pins every recipe by the hash of its tokens (`recipes`) beside the list of `derivations`.

A bare recipe name the program does not declare falls back to the packaged `std.<name>`, then to `std.derived`. `derive wire` emits fixed-width unsigned little-endian codecs in declaration order with no padding; it is no longer compiler code but the packaged recipe `std.wire`. `std.derived` holds `derive eq`, `derive ord` (lexicographic) and `derive hash`: impls of `std.core`'s traits for any record whose fields already have them, checked like impls written by hand.

```cairn
import std.core (Eq, Ord);

struct Header { kind:u8; size:u32; }
derive wire for Header;
derive eq for Header;
derive ord for Header;

fn main() -> i32 {
  stack bytes:u8[5] = zeroed;
  let head = Header(7, 1480);
  encode_Header(bytes, head);
  if !same(decode_Header(bytes), head) || wire_size_Header() != 5 { return 1; }
  if !less(Header(7, 1), head) { return 2; }
  return 0;
}
```
