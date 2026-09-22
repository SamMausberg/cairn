# Generics, traits, closures, modules and recipes

The ways a program abstracts: type and natural parameters with bounds, traits with static and dynamic dispatch, function values and closures, modules and vendored projects, and recipes that generate code as library code. [language.md](language.md) covers values, memory and effects; [concurrency.md](concurrency.md) covers tasks, lanes and devices.

## Generics

A function takes type parameters and natural parameters. Arguments for them are inferred from the arguments, the literals and the expected type, or written out where nothing else says them (`largest[u64](3, 9)`, `scale[4](x)`, `Option[u64].None`). A literal argument takes its type from the other arguments and then from the expected result. `T(x)` converts, or constructs, at the instance's `T`.

Every instance is monomorphized on demand and checked as ordinary code, so an instance, not its template, is what typechecks. Arguments are typed where they are written: the template's module decides what its own text means, never what the caller's expressions mean.

```cairn
struct Column[T] { values:Buf[T]; used:usize; }

fn mean[T:numeric](n:usize, xs:ro<T>[n]) -> T {
  let mut total:T = 0;
  for i in 0..n { total += xs[i]; }
  return total / T(n);                          // T(n) converts at the instance's T
}
fn take_first[T:copy](c:ro<Column[T]>) -> T = c.values[0];

fn main() -> i32 {
  stack cents:u64[4] = zeroed;
  stack load:f64[2] = zeroed;
  for i in 0..4 { cents[i] = u64(4 + i * 4); }  // 4, 8, 12, 16
  load[0] = 1.5;
  load[1] = 2.5;
  let mut column = Column(Buf[u32](1), 1);
  column.values[0] = 7;
  let started:u32 = take_first(column);         // the literal 7 took u32 from the expected type
  if mean(4, cents) != 10 || mean(2, load) != 2.0 || started != 7 { return 1; }
  return 0;
}
```

A natural parameter may be a view's static extent, inferred from a literal, a fixed array or a literal extent passed there. `family` names a bounded range of instances of one natural template.

```cairn
fn label[N:nat](text:ro<u8>[N]) -> usize = N;         // N comes from the literal
fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);
family gain = scale[1..4];                            // gain_1, gain_2, gain_3

fn main() -> i32 {
  stack row:u8[3] = zeroed;
  if label("retry after") != 11 || label(row) != 3 { return 1; }
  if scale[4](2) != 8 || gain_3(7) != 21 { return 2; }
  return 0;
}
```

## Bounds

A parameter promises more with a bound: a trait, a kind, or a closed class of scalars. `[T:copy]` may be used many times and dropped. `[T:affine]` may be dropped and kept in zeroed storage. Saying nothing admits `linear` values too, which must be consumed exactly once. The classes are `integer unsigned signed float numeric scalar`, and a class licenses the operators and literals of its types. Bounds combine with `+`, and generic records take them too. A bound is checked where the instance is requested, so misuse is reported at the call, in the caller's terms (`E-BOUND`).

```cairn
import std.core (Ord);

struct Span[T:copy] { low:T; high:T; }

fn span[T:Ord + copy](n:usize, xs:ro<T>[n]) -> Span[T] {
  let mut least:usize = 0;
  let mut most:usize = 0;
  for i in 1..n {
    if less(xs[i], xs[least]) { least = i; }
    if less(xs[most], xs[i]) { most = i; }
  }
  return Span(xs[least], xs[most]);
}

fn main() -> i32 {
  stack sizes:u32[4] = zeroed;
  for i in 0..4 { sizes[i] = u32(40 + i * 20); }
  sizes[2] = 12;
  let seen = span(4, sizes);
  if seen.low != 12 || seen.high != 100 { return 1; }
  return 0;
}
```

```cairn rejects E-BOUND
struct Window[T:copy] { first:T; last:T; }
fn widest[T:copy](x:T) -> Window[T] = Window(x, x);
fn main() -> i32 { let body = Buf[u8](2); let w = widest(body); return 0; }
```

```text
Buf[u8] is affine, not copy; widest needs [T:copy].
```

## Certifying a template

`cairn check --generics` checks each template once, at opaque witness types that offer only what its bounds promise and must be consumed exactly once; a parameter bounded by a scalar class is checked at every type of that class. `ok` means every instance whose arguments satisfy the bounds will check. Any other verdict names the first thing the body needed beyond its bounds: an operator on a bare `T`, a second use of a `T`, dropping one, zeroed storage of one.

```cairn
fn smaller[T:numeric](a:T, b:T) -> T { if a < b { return a; } return b; }
fn count[T](x:T) -> u64 = 1;                          // drops x, which may be linear

fn main() -> i32 {
  if smaller(3, 9) != 3 || count(7) != 1 { return 1; }
  return 0;
}
```

```sh
cairn check --generics frame.cairn
```

```json
"generics": {
  "smaller": "ok",
  "count": "E-LINEAR-LEAK: x is linear: consume it, or defer its consumer, on every path. T may be linear here; [T:affine] or [T:copy] promises more."
}
```

The verdict covers the rules that need every row as well: ceilings, operand order and what a lane may reach. At a witness, a bounded member may do what its trait's ceiling allows; a member without a ceiling may do anything, which shows as `bound:Trait.member` in the row and which no `pure` template and no lane can absorb. A program that does not check leaves its templates `unknown`, never `ok`.

The command exits 1 unless every template certifies, and every template of `std` certifies. Without the flag, a program's own unbounded templates are still accepted per instance, and a template nobody instantiates is listed in the receipt as `uninstantiated_templates`, never silently trusted.

## Traits

Dispatch is static, on the type of the `Self` argument, and a bound such as `[K:Hash + Eq]` is checked when the instance is made. An impl defines exactly its trait's members, with exactly their declared modes and types and `Self` replaced by the implementing type (`E-TRAIT-IMPL`), whether or not they are used, in one `impl` block. Two blocks for one `Self` type are `E-TRAIT-OVERLAP`, however they split the members. There is no inheritance and no implicit boxing.

```cairn
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
struct Beacon { seq:u16; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u64 = u64(self.payload) + 5; }
impl Frame for Beacon { fn size(self:ro<Beacon>) -> u64 = 8; }

fn bandwidth[A:Frame, B:Frame](a:ro<A>, b:ro<B>) -> u64 = size(a) + size(b);

fn main() -> i32 {
  let packet = Packet(40);
  let beacon = Beacon(3);
  if bandwidth(packet, beacon) != 53 || packet.size() != 45 { return 1; }
  return 0;
}
```

```cairn rejects E-TRAIT-IMPL
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u32 = self.payload; }
```

```text
Frame.Packet.size does not match Frame.size(ro<Packet>).
```

A ceiling on a trait's member (`fn size(self:ro<Self>) -> u64 pure;`) is part of the contract: every implementation is held to it (`E-EFFECT-CEILING`), and one that declares more is `E-TRAIT-IMPL`. That is what lets a bound promise it. `std.core` declares `less`, `same` and `hash` pure.

Every generic parameter of an impl appears in its `Self` type, so an instance is made from the type alone, and a generic impl applies exactly where its bounds hold. That is how `std.core` covers every scalar class at once (`impl[T:integer] Ord for T`). One type has one implementation: a generic impl and a concrete impl that both match one type are refused, and so is a generic impl whose bound asks the question it answers (`impl[T:Frame] Frame for T`). Static calls, `dyn` borrows and `Dyn` values all resolve through that one answer.

```cairn rejects E-TRAIT-OVERLAP
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u64 = u64(self.payload); }
impl[T] Frame for T { fn size(self:ro<T>) -> u64 = 0; }
fn main() -> i32 { let packet = Packet(1); return i32(size(packet)); }
```

```text
Two impls of Frame match Packet: one Self type means one implementation.
```

Coherence is judged over the whole program, which is always in hand, since dependencies are vendored sources. Any module may implement a trait it can name for a type it can name, by hand or by `derive`, and two modules that both do are told so (`E-TRAIT-OVERLAP`). There is no orphan rule.

`value.f(args)` means `f(value, args)`, looked up first in the module that declares the receiver's type and before the builtins, so a type may have its own `len`. An unqualified `len(x)` is still the builtin; only the names that became builtins in 1.0 (`take swap transfer wait mmio_read mmio_write asm`) yield to a function the calling module can name. Privacy is judged from where the call is written. When two traits the type implements declare one member name, write the trait (`Frame.size(packet)`) or the call is `E-TRAIT-AMBIGUOUS`.

```cairn
module frames;
pub struct Ring { slots:Buf[u8]; used:usize; }
pub fn ring(room:usize) -> Ring = Ring(Buf[u8](room), 0);
pub fn len(r:ro<Ring>) -> usize = r.used;                 // a type may have its own len
pub fn add(r:rw<Ring>, byte:u8) { r.slots[r.used] = byte; r.used += 1; }

module app;
import frames;

pub fn main() -> i32 {
  let mut ring = frames.ring(4);
  ring.add(9);
  if ring.len() != 1 || len(ring.slots) != 4 { return 1; }  // the method, then the builtin
  return 0;
}
```

## dyn and Dyn

`ro<dyn Frame>` takes any named place whose type implements the trait. The call site builds a two-word reference (the object and a static table); members called on it go through the table, add the `dispatch` effect, and contribute the effect rows of every implementation. Dynamic references are borrows, so they are never values and nothing escapes through them. By-value owners pass through the table by move.

A member is dyn-compatible when only its receiver is a borrow or mentions `Self`, which excludes returning `Self`. A trait goes behind `dyn` only if every member is (`E-DYN`), because the table has a slot for each.

```cairn rejects E-DYN
trait Frame { fn size(self:ro<Self>) -> u64; fn dup(self:ro<Self>) -> Self; }
struct Packet { payload:u32; }
impl Frame for Packet {
  fn size(self:ro<Packet>) -> u64 = u64(self.payload);
  fn dup(self:ro<Packet>) -> Packet = Packet(self.payload);
}
fn total(f:ro<dyn Frame>) -> u64 = size(f);
fn main() -> i32 { let packet = Packet(4); return i32(total(packet)); }
```

```text
Frame.dup is not dyn-compatible: only its receiver may be a borrow or mention Self.
```

The owned form is explicit. `Dyn[Frame](Packet(40))` moves a value of any implementing type that is not `linear` to the heap (`alloc`, `free`). The box drops what it holds, so a `linear` value may not go in one (`E-LINEAR-STORAGE`). A `Dyn[Frame]` is an ordinary affine value that can live in a `Vec` or a record, its members dispatch, and it lends itself wherever a `dyn Frame` reference is expected. An empty one, moved from or out of zeroed storage, is a guard failure when it is lent, never a null call.

```cairn
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
struct Beacon { seq:u16; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u64 = u64(self.payload) + 5; }
impl Frame for Beacon { fn size(self:ro<Beacon>) -> u64 = 8; }

fn total(f:ro<dyn Frame>) -> u64 = size(f);              // a borrowed two-word reference

fn main() -> i32 {
  let mut queue = Buf[Dyn[Frame]](2);
  queue[0] = Dyn[Frame](Packet(40));                     // on the heap, released at scope exit
  queue[1] = Dyn[Frame](Beacon(3));
  if total(queue[0]) + total(queue[1]) != 53 { return 1; }
  if queue[1].size() != 8 { return 2; }
  return 0;
}
```

## Function values and closures

`fn(u64) -> u64` is a copyable code pointer to a plain declared function of values; an instantiated generic such as `ascending[u64]` qualifies. It can be stored in a record, its zero value is legal and calling it is a guard failure, and whoever calls through one inherits the effects of every function whose address is taken.

```cairn
struct Stage { apply:fn(u64) -> u64; }

fn twice(x:u64) -> u64 = x * 2;
fn drop_low(x:u64) -> u64 = x & 0xfffffff0;

fn run(n:usize, stages:ro<Stage>[n], x:u64) -> u64 {
  let mut value = x;
  for i in 0..n { let step = stages[i].apply; value = step(value); }
  return value;
}

fn main() -> i32 {
  let mut pipeline = Buf[Stage](2);
  pipeline[0] = Stage(twice);
  pipeline[1] = Stage(drop_low);
  if run(pipeline, 21) != 32 { return 1; }
  return 0;
}
```

`ro<fn(u64) -> u64>` is a borrowed callable: pass a declared function, or write a closure in place. A closure captures its enclosing scope by reference, exists only as that argument, and therefore never allocates and never escapes. Its effects belong to the function that wrote it, and the callee shows `indirect_call`. Function types carry values and single borrows, not array views (`E-FN-TYPE`).

```cairn
fn scale(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }

fn main() -> i32 {
  let mut samples = Buf[u64](4);
  let gain:u64 = 3;
  let mut calls:u64 = 0;
  for i in 0..len(samples) { samples[i] = u64(i); }
  scale(samples, |x:u64| -> u64 { calls += 1; return x * gain; });
  if samples[3] != 9 || calls != 4 { return 1; }
  return 0;
}
```

What a closure captures it borrows for that call, `rw` where it writes. The same call cannot also lend, move or write a place the closure touches (`E-ALIAS`), so a callee never sees a closure reach anything it was given.

```cairn rejects E-ALIAS
fn scale(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }
fn main() -> i32 {
  let mut samples = Buf[u64](4);
  scale(len(samples), samples, |x:u64| -> u64 { return x + samples[0]; });
  return 0;
}
```

```text
A mutable view cannot be passed to overlapping call arguments.
```

## Modules

`module net.http;` names the module of the declarations that follow; a file without one shares the root namespace. `pub` exports a declaration, and impl members are always public. `import net.http;` lets you write `http.get(...)` and `http.Request`, `import a.b as c;` renames, and `import std.core (Option, Result);` also brings those names in unqualified. An import may not hide a name the importing module declares (`E-DUPLICATE`). A private record's fields are as private as the record.

A `family` over another module's template needs that template to be `pub`, and its instances belong to the module that wrote the family (`pub family` exports them). `derive wire for R;` is written in the module that declares `R`.

```cairn
module codec;
import std.core (Option);

struct Limits { largest:u32; }                            // private: so are its fields
pub fn accepts(size:u32) -> Option[u32] {
  let limits = Limits(1480);
  if size > limits.largest { return None; }
  return Some(size + 5);
}

module app;
import codec as packet;
import std.core (Option);

pub fn main() -> i32 {
  match packet.accepts(40) { Some(total) => { if total != 45 { return 1; } } None => return 2; }
  match packet.accepts(9000) { Some(total) => return 3; None => {} }
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
libraries = ["z"]       # system libraries this project's own externs call, by name
```

A name under `libraries` is a row of the toolchain's closed table in `projects/toolchain.py`, never a flag or a path, and the library is found where the C compiler finds it. A packaged module that binds a library links it wherever it is imported (`std.zlib` links zlib), so only a project's own `extern` declarations need the line. An unknown or repeated name, and a library on a freestanding target, are refused when the manifest is read; a missing name fails at the link, naming the symbol, and the build record lists what was linked.

A freestanding `target` refuses any program whose effect rows need a hosted runtime ([the freestanding target](tools.md#the-freestanding-target)). The host chooses trusted compilers (`clang++`, `g++`, and `nvcc` when a program uses the device), and builds use fresh directories. Generated C++ is readable and keeps the C ABI for every function whose signature is C compatible. A library exports every function; an executable contains only what its `main` reaches, and `main` may live in a module, while the receipt still covers everything that was checked.

`--debug` adds symbols and `#line` maps to the authored files. `--incremental` compiles one object per module against a shared interface header and reuses an object only when its unit, that header, the command line, the runtime headers and the compiler version hash to the same key, and the stored object still matches the digest written beside it in `build/objects`. It gives up inlining across modules, and device programs and freestanding images stay one unit either way ([tools.md](tools.md#cairn-build---incremental) has the measured sessions).

## Dependencies

`[dependencies] geometry = "deps/geometry"` names a project vendored inside this one's root, with its own `cairn.toml` and its own dependencies loaded first, at most 16 per manifest and 4 deep, a diamond loaded once. A dependency contributes modules only, and only what it marks `pub` is reachable. Nothing is fetched, no path leaves the root, no path has a `.` or `..` segment, no symbolic link is followed, and the receipt pins each dependency's manifest and sources by hash.

A dependency's manifest is read by the same checker as yours, so an unknown table or option is refused there too, and its `[build]`, which the build ignores, must still name a known kind, architecture and target. One directory is one project under one name: a second name for it is an error, not a diamond, and one name is one project of the build, the root's own included.

A module belongs to exactly one project. No project declares a `std.*` module, reopening a module another project declared names both projects and fails, and so does a file with no `module` header that would silently continue a dependency's. An executable's entry point is searched only in the sources this manifest lists, so a dependency neither supplies `main` nor denies you yours.

## Recipes

A recipe is a generator written as library code: ordinary declarations (functions, records, trait `impl`s, `kernel fn`) over a record schema (`for R`), naturals (`recipe tiles[W:nat, H:nat]`) and names of functions (`recipe fieldwise[F:fn] for R`). `derive name[arguments] for Type;` applies one.

Inside a recipe, `each f in R { }` iterates statically over a record's fields and `each k in lo..hi { }` over a natural range, at declaration level, at statement level, in a record's field list, or among the arguments of a call, where it splices one or several expressions per step (`each f in R { lo.$f, hi.$f }`). `fold | each ... { e }` joins the expansions with one operator, or with any function of two operands (`fold add_wrap each ...`, `fold lib.chain each ...`).

`where a = offset(f), t = typeof(f)` names static values, computed from naturals, comparisons, `min`, `max`, a static `fold` over a static `each` (`where width = fold + each f in R { bytes(f) }`) and the facts `bytes bits offset index count typeof unsigned signed integer float scalar record`. Static values are naturals and booleans; a negative result or a division by zero is `E-RECIPE-STATIC`.

`$name` splices one into an identifier (`encode_$R`, `value.$f`, `shift_$k`), and a whole `$name` is that natural or that type. The longest static name wins, so `$R_columns` is `$R` then `_columns`. Only the bare `for` parameter `R` is the type itself: every other static needs its `$`, so an ordinary identifier that shares a `where` name is left alone. `require condition, "message";` states the admissible inputs (`E-DERIVE-DOMAIN`, or the code the message opens with).

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

A generated record may declare a field extent as a written one does: `$f:Buf[$t][rows]` says the generated column holds `rows` elements, where `rows` is an earlier `usize` field of the same generated record. The rule is the written record's rule (`E-EXTENT` names the generated record), so a column goes to a call whole and pays no part guard, and the constructor writes each carrier inline as `Buf[$t](rows)` on the same `rows`.

```cairn
module layout;

pub recipe columns for R {
  pub struct $R_columns {
    rows:usize;
    each f in R where t = typeof(f) { $f:Buf[$t][rows]; }
  }
  pub fn $R_columns_new(rows:usize) -> $R_columns = $R_columns(
    rows, each f in R where t = typeof(f) { Buf[$t](rows) }
  );
}

module app;
import layout;

struct Particle { x:f32; mass:f64; }
derive layout.columns for Particle;

fn heaviest(n:usize, mass:ro<f64>[n]) -> f64 {
  let mut top:f64 = 0.0;
  for i in 0..n { if mass[i] > top { top = mass[i]; } }
  return top;
}

pub fn main() -> i32 {
  let mut columns = Particle_columns_new(4);
  columns.mass[2] = 3.0;
  if heaviest(columns.rows, columns.mass) != 3.0 { return 1; }   // whole, by the declared extent
  return 0;
}
```

A function name is spliced as the deriving module wrote it (`$F(v.$f)` calls it; inside a longer identifier, `$F_$R`, it gives its last segment) and means what it means there, so one generic function serves fields of different types and privacy is judged from the deriving module. Recipes take types and naturals, not expressions: behavior reaches a generated function as a `fn` value or a closure. A recipe over a natural range generates one function per step, as `family` does for one template.

Expansion happens before checking and reads nothing but the recipe and the schema, so it is a function of its inputs; `cairn expand` prints what it produced, as source. Expansion is hygienic: a function, type, trait or constant the recipe names means what it means in the recipe's own module and is spelled out in full where the code lands (`helper(x)` becomes `lib.helper(x)`, so the deriving module's own `helper` cannot capture it, and a private one is `E-PRIVATE` from there). Only `$` splices, and the names they build, belong to the deriving module. What a recipe generates is ordinary code of the deriving module, checked like any other; its signatures and effect ceilings are its contract.

A generated name that already exists is `E-DERIVE-COLLISION`, and static iteration is bounded per level and in total (`E-EXPANSION-LIMIT`). A derivation for a record that another derivation generates waits for it, whatever order they are written in. The receipt pins every recipe by the hash of its tokens (`recipes`) beside the list of `derivations`.

A bare recipe name the program does not declare falls back to the packaged `std.<name>`, then to `std.derived`. `derive wire` emits fixed-width unsigned little-endian codecs in declaration order with no padding, as the packaged recipe `std.wire`. `std.derived` holds `derive eq`, `derive ord` (lexicographic) and `derive hash`: impls of `std.core`'s traits for any record whose fields already have them, checked like impls written by hand.

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

## Gradients

`derive grad for f;` generates `f_grad`, the reverse-mode derivative of `f`, as an ordinary function: `cairn expand` prints it and the checker checks it like any other. `derive grad[w, b] for f;` differentiates with respect to the named parameters only. Without a list, every float parameter and every `ro` float view is differentiated.

`f_grad` takes `f`'s parameters, then `seed` when `f` returns a float, then one adjoint for each parameter it differentiates: `d_x:rw<T>` or `d_x:rw<T>[n]`, into which it adds, and `d_out:ro<T>[n]` for each float view `f` writes, which it reads. It runs `f`, adds `seed` times each partial derivative into the adjoints, and returns `f`'s result. Adding rather than assigning is what lets gradients compose: a gradient that calls `g` hands `g_grad` its own adjoints.

```cairn
fn square(x:f64) -> f64 = x * x;

fn loss(n:usize, w:ro<f64>[n], x:ro<f64>[n], bias:f64, target:f64) -> f64 {
  let guess = reduce + for i in n yield w[i] * x[i];
  return square(guess + bias - target);
}

derive grad for square;
derive grad[w, bias] for loss;                     // x and target are data: no adjoint for them

fn main() -> i32 {
  let n:usize = 2;
  buffer w:f64[n] = zeroed;
  buffer x:f64[n] = zeroed;
  buffer dw:f64[n] = zeroed;
  x[0] = 1.0;
  x[1] = 2.0;
  let mut bias:f64 = 0.0;
  for step in 0..100 {
    for i in 0..n { dw[i] = 0.0; }
    let mut dbias:f64 = 0.0;
    loss_grad(n, w, x, bias, 5.0, 1.0, dw, dbias);           // the loss, dropped; seed times its gradient added
    for i in 0..n { w[i] -= 0.05 * dw[i]; }
    bias -= 0.05 * dbias;
  }
  if loss(n, w, x, bias, 5.0) > 0.000000001 { return 1; }
  return 0;
}
```

The differentiated fragment is one whose adjoint lies in the same fragment: immutable `let`s, `if` whose paths return, sums, loops and regions that write each output element once at their binder, `+ - * /`, `sqrt`, `abs` (whose derivative at zero is taken as 1), `floor ceil trunc` (whose derivative is 0), conversions between `f32` and `f64`, `std.math.exp` and `std.math.log`, and calls of functions that derive their own gradient. A sum is a `reduce +`, or a float `let mut` that one sequential loop adds into (`acc += e`, with `e` not reading `acc`) and that nothing reads before that loop ends: each step's `e` then takes the sum's own adjoint. A function that reaches libm cannot run inside a `reduce`, so its sums are loops. Any other `let mut`, `while`, a `reduce` other than `+`, and an output read or written twice are `E-GRAD-FORM`. A call of a function with no derived gradient, or one whose gradient leaves out a parameter the caller differentiates, is `E-GRAD-CALL`. A target that is not a plain function of this module, or a list naming something other than its float parameters, is `E-GRAD`.

Nothing is taped. Each `return`, and the end of a function that writes views, carries its own backward sweep, which recomputes the `let`s on its path. A region's backward sweep is a region of its own, in which each lane adds into its own element of each adjoint. Whatever the lanes would add into a shared scalar is gathered by a sequential loop, so no lane writes a value another lane writes. A lane that reads a differentiated input at another lane's element would scatter its adjoint across lanes, so that is `E-GRAD-RACE`: read it at `[i]`, differentiate a sequential loop, or leave the input out of the list.

```cairn rejects E-GRAD-RACE
fn smooth(n:usize, x:ro<f64>[n], out:rw<f64>[n]) {
  parallel i in n { out[i] = x[i] + x[(i + 1) % n]; }
}
derive grad for smooth;
```

```text
The adjoint of x[(i + 1) % n] adds into d_x at another lane's element; read x at [i] in the region, or differentiate a sequential loop.
```

The derivative is the derivative of the formulas, not of their rounding: the float operations round as written, and the adjoint's own operations round too. The suite holds gradients to central differences of the compiled function and, where the system Python has torch, to torch's autograd over the same formulas, under both compilers, the sanitizers and ThreadSanitizer for a region. That is finite testing, not a proof, and the SMT model does not describe generated gradients any more than it describes the regions they contain.
