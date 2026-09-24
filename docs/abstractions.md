# Generics, traits, closures, modules and recipes

## Generics

A function takes type parameters and natural parameters. They are inferred from the arguments and the expected type, or written out (`largest[u64](3, 9)`, `scale[4](x)`, `Option[u64].None`). `T(x)` converts, or constructs, at the instance's `T`. Every instance is made on demand and checked as ordinary code, and the template's module decides what its own text means.

```cairn
struct Column[T] { values:Buf[T]; used:usize; }

fn mean[T:numeric](n:usize, xs:ro<T>[n]) -> T {
  let mut total:T = 0;
  for x in xs { total += x; }
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

A natural parameter may be a view's static extent, inferred from what is passed. `family` names a bounded range of instances of one natural template.

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

A parameter promises more with a bound: a trait, a kind, or a class of scalars. `[T:copy]` may be used many times and dropped, `[T:affine]` may be dropped and kept in zeroed storage, and no bound admits `linear` values too, which must be consumed exactly once. The classes `integer unsigned signed float numeric scalar` allow the operators and literals of their types. Bounds combine with `+`, and a bound is checked at the call, in the caller's terms (`E-BOUND`).

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

`cairn check --generics` checks each template once, at opaque types that offer only what its bounds promise. `ok` means every instance whose arguments satisfy the bounds will check. Any other verdict names the first thing the body needed beyond its bounds, such as an operator on a bare `T` or dropping one.

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

The verdict covers ceilings, operand order and what a lane may reach too. A trait member without a ceiling may do anything, which shows as `bound:Trait.member` in the row. The command exits 1 unless every template certifies, and every template of `std` does. Without the flag, templates are accepted instance by instance, and one nobody instantiates is listed in the receipt as `uninstantiated_templates`.

## Traits

Dispatch is static, on the type of the `Self` argument. An impl defines exactly its trait's members, with their declared modes and types, in one `impl` block (`E-TRAIT-IMPL`), and a second block for the same `Self` is `E-TRAIT-OVERLAP`.

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

A ceiling on a trait's member (`fn size(self:ro<Self>) -> u64 pure;`) holds every implementation to it (`E-EFFECT-CEILING`), which is what lets a bound promise it. `std.core` declares `less`, `same` and `hash` pure.

A generic impl applies exactly where its bounds hold, which is how `std.core` covers every integer type at once (`impl[T:integer] Ord for T`). One type has one implementation: a generic and a concrete impl that both match one type are refused.

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

Coherence is judged over the whole program, which is always in hand, so any module may implement a trait for a type it can name, and there is no orphan rule.

`value.f(args)` means `f(value, args)`, looked up first in the module that declares the receiver's type, so a type may have its own `len`. When two traits the type implements declare one member name, write the trait (`Frame.size(packet)`), or the call is `E-TRAIT-AMBIGUOUS`.

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

`ro<dyn Frame>` takes any named place whose type implements the trait, as a two-word reference to the object and a static table. A call through it adds `dispatch` and the rows of every implementation. A `dyn` reference is a borrow, so nothing escapes through it. A trait goes behind `dyn` only if no member but its receiver is a borrow or mentions `Self` (`E-DYN`).

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

The owned form is explicit: `Dyn[Frame](Packet(40))` moves the value to the heap (`alloc`, `free`). A `linear` value cannot go in one (`E-LINEAR-STORAGE`). A `Dyn[Frame]` is an ordinary affine value that can live in a `Vec` or a record and lends itself wherever a `dyn Frame` is expected. An empty one is a guard failure when lent, never a null call.

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

`fn(u64) -> u64` is a copyable pointer to a declared function. It can be stored in a record, calling its zero value is a guard failure, and a call through one carries the effects of every function whose address is taken.

```cairn
struct Stage { apply:fn(u64) -> u64; }

fn twice(x:u64) -> u64 = x * 2;
fn drop_low(x:u64) -> u64 = x & 0xfffffff0;

fn run(n:usize, stages:ro<Stage>[n], x:u64) -> u64 {
  let mut value = x;
  for stage in stages { let step = stage.apply; value = step(value); }
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

`ro<fn(u64) -> u64>` is a borrowed callable: pass a declared function, or write a closure in place. A closure exists only as that argument, so it never allocates and never escapes. Its effects belong to the function that wrote it, and the callee shows `indirect_call`. Function types take values and single borrows, not array views (`E-FN-TYPE`).

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

A closure borrows what it captures for that call, `rw` where it writes, so the same call cannot also lend a place the closure touches (`E-ALIAS`).

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

## Implementations

A function can have several implementations. The ordinary `fn` is the reference, which defines what the function computes. An implementation has exactly the reference's signature, names it after `implements`, and may say with `when` on which inputs it applies; `plan f use g;` chooses which one runs.

```cairn
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum += xs[i]; }
  return sum;
}

// Two running sums, for a length the loop divides evenly.
fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 {
  let mut a:u64 = 0;
  let mut b:u64 = 0;
  for k in 0..n / 4 { a += xs[4 * k] + xs[4 * k + 1]; b += xs[4 * k + 2] + xs[4 * k + 3]; }
  return a + b;
}

plan total use total_by4;                         // total tests n % 4 == 0 on entry

fn main() -> i32 {
  let mut xs = Buf[u64](12);
  for i in 0..12 { xs[i] = u64(i); }
  if total(xs) != 66 || total(10, xs[0..10]) != 45 { return 1; }  // total_by4, then the reference
  return 0;
}
```

The selected implementation runs where its condition holds and the reference everywhere else, so every input the reference admits is still admitted; without a plan the reference runs. `when` is a condition over the value parameters that cannot trap: comparisons, `&& || !`, `& | ^ ~`, `min`, `max`, the wrapping forms, `/` or `%` by a nonzero literal or natural parameter, `shr` or `shl_wrap` by one below the width, `len` of a view parameter, literals, constants and natural parameters (`E-IMPL-WHEN`). Without `when` an implementation applies to every input and the dispatch tests nothing. A call whose literal or constant arguments make the condition true, such as `total(12, xs)` above, calls the implementation directly. A condition that computes with a float is always tested on entry, in the machine's own arithmetic.

An implementation keeps its reference's contract. Its parameters, types, extents, placements and result are the reference's (`E-IMPL-SIGNATURE`). Its row stays inside the reference's declared ceiling, or inside the reference's own row when it declares none (`E-IMPL-EFFECT`), and it writes no rounding the reference does not write (`E-IMPL-NUMERICS`). The reference's row joins every implementation's, so choosing one changes no row.

```cairn rejects E-IMPL-EFFECT
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum += xs[i]; }
  return sum;
}
fn total_copy(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut copy = Buf[u64](n);
  for i in 0..n { copy[i] = xs[i]; }
  return copy[0];
}
```

```text
total_copy may alloc, free, local_read, local_write, zero_init, which total does not; total declares no ceiling, so its own row is the ceiling.
```

A reference that states a ceiling admits what the ceiling admits. Here the reference is a sequential loop, and its ceiling lets an implementation run on the lane pool:

```cairn
fn scale(n:usize, xs:rw<u64>[n]) effects(pure, write:xs, par:host) {
  for i in 0..n { xs[i] = 2 * xs[i]; }
}

fn scale_lanes(n:usize, xs:rw<u64>[n]) implements scale when n >= 65536 {
  parallel i in n { xs[i] = 2 * xs[i]; }
}

plan scale use scale_lanes;
```

```cairn rejects E-IMPL-WHEN
fn total(n:usize, xs:ro<u64>[n]) -> u64 = 0;
fn total_padded(n:usize, xs:ro<u64>[n]) -> u64 implements total when (n + 3) / 4 > 2 = 0;
```

```text
A when is a condition over the value parameters that cannot trap: comparisons, && || !, & | ^ ~, min, max, the wrapping forms, / or % by a nonzero literal or natural parameter, shr or shl_wrap by one below the width, len of a view parameter, literals, constants and natural parameters.
```

An implementation lives in its reference's module and is an ordinary function with a body, never generic over types, a kernel or an implementation of an implementation (`E-IMPLEMENTS`). Only a test block calls one by name, and an implementation never reaches its reference, directly or through another function whose implementation calls back, since the dispatch could run them in a cycle (`E-IMPL-CALL`). A plan names one implementation of the function it plans (`E-IMPL-USE`). `needs(cp_async)` after the condition names the [device features](tools.md#the-device-target) an implementation's device code uses, and one without device code may name none (`E-IMPLEMENTS`). A plan that selects it adds them to what the program asks of its device target, and a build for a target that lacks one is refused (`E-IMPL-TARGET`) rather than running the reference in its place.

The receipt lists each implementation under its reference with its condition, whether it is tested at entry, what it requires of the machine (host or device, lanes, tasks, allocation sites, stack bytes) and its identity. The identity is a digest of the two declarations' tokens that no comment changes, and the implementation a plan runs is marked `runs`. Acceptance says nothing about whether an implementation computes what its reference computes; [`cairn validate`](tools.md#cairn-validate) tests that.

An implementation can take natural parameters for [`cairn tune`](tools.md#cairn-tune) to search. `tune K in [2, 4, 8]` after the condition lists the values `K` takes, and each value is an instance, `total_by[4]`, in whose body and condition `K` is a `usize` constant. `plan total use total_by[4];` selects one instance.

```cairn
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum += xs[i]; }
  return sum;
}

// K elements a step, for a length K divides.
fn total_by[K:nat](n:usize, xs:ro<u64>[n]) -> u64 implements total when n % K == 0 tune K in [2, 4, 8] {
  let mut sum:u64 = 0;
  for k in 0..n / K {
    for j in 0..K { sum += xs[K * k + j]; }
  }
  return sum;
}

plan total use total_by[4];                       // total tests n % 4 == 0 on entry

fn main() -> i32 {
  let mut xs = Buf[u64](12);
  for i in 0..12 { xs[i] = u64(i); }
  if total(xs) != 66 || total(10, xs[0..10]) != 45 { return 1; }  // total_by[4], then the reference
  return 0;
}
```

Every instance the list names is made and held to every rule above, whether or not a plan selects it, so each candidate the search may try is one the checker accepted. An instance that breaks a rule is refused with that rule's code, and the message and the diagnostic's `instance` name it: with `tune K in [4, 0]`, `total_by[0]` divides by zero in its condition (`E-IMPL-WHEN`). A list holds distinct naturals, at most 16 instances with every parameter's values combined, as in `tune K in [2, 4], W in [1, 3]`. A natural without a list, a list on an implementation without naturals, a value the list does not name and a selection without values are `E-IMPL-PARAM`.

```cairn rejects E-IMPL-PARAM
fn total(n:usize, xs:ro<u64>[n]) -> u64 = 0;
fn total_by[K:nat](n:usize, xs:ro<u64>[n]) -> u64 implements total when n % K == 0 tune K in [2, 4, 8] = 0;
plan total use total_by[3];
```

```text
K = 3 is not a value total_by lists; K is one of 2, 4, 8.
```

The receipt lists each instance under its reference with its values (`parameters`, `instance_of`) and its condition as the instance reads it, `n % 4 == 0`. An instance's identity is the declarations' identity with its values. The list is not part of it, so listing another value leaves every other instance's validation current. Each instance is built, so each can be validated on its own: the build holds every listed instance's code, and a device implementation compiles every instance's kernels. The reference's row joins every instance's, as it joins every implementation's, so no instance widens it past the ceiling. `cairn check --generics` certifies a parameterized implementation at its listed values.

## Modules

`module net.http;` names the module of the declarations that follow. `pub` exports a declaration, and a private record's fields are private too. `import net.http;` gives `http.get(...)`, `import a.b as c;` renames, and `import std.core (Option, Result);` brings those names in unqualified. An import may not hide a name the module declares (`E-DUPLICATE`).

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
  match packet.accepts(9000) { Some(_) => return 3; None => {} }
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

Only project modules and the packaged `std.*` modules can be imported, and nothing is downloaded.

## Projects

`cairn.toml` lists ordered sources and task files. It is data, never a build script, and a command may name any manifest by path (`cairn run app/gpu.toml`).

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

A name under `libraries` is a row of the closed table in `projects/toolchain.py`, never a flag or a path. A packaged module links its own library (`std.zlib` links zlib), so only a project's own `extern` declarations need the line. An unknown name is refused when the manifest is read.

A [freestanding target](tools.md#the-freestanding-target) refuses any program whose rows need a hosted runtime. Builds use fresh directories, and the generated C++ keeps the C ABI for every function whose signature is C compatible. A library exports every function, and an executable holds what its `main` reaches. `--debug` adds symbols and `#line` maps, and [`--incremental`](tools.md#cairn-build---incremental) compiles one cached object per module.

## Dependencies

`[dependencies] geometry = "deps/geometry"` names a project vendored inside this one's root. Its own dependencies load first, at most 16 per manifest and 4 deep, and a diamond loads once. A dependency contributes only the modules it marks `pub`. Nothing is fetched, no path leaves the root or follows a symbolic link, and the receipt pins each dependency by hash.

A dependency's manifest is checked as strictly as yours. One directory is one project under one name, a module belongs to exactly one project, and no project declares a `std.*` module. A dependency never supplies `main`.

## Recipes

A recipe is a generator written as library code: ordinary declarations over a record schema (`for R`), naturals (`recipe tiles[W:nat, H:nat]`) or function names (`recipe fieldwise[F:fn] for R`). `derive name[arguments] for Type;` applies one.

Inside a recipe, `each f in R { }` iterates statically over a record's fields and `each k in lo..hi { }` over a natural range, in declarations, statements, field lists or call arguments. `fold | each ... { e }` joins the expansions with an operator or a two-argument function. `where t = typeof(f)` names static values, from naturals and the facts `bytes bits offset index count typeof unsigned signed integer float scalar record`; a negative result or a division by zero is `E-RECIPE-STATIC`. `$name` splices a static into an identifier (`encode_$R`, `value.$f`). `require condition, "message";` states the admissible inputs (`E-DERIVE-DOMAIN`).

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

A generated record may declare a field extent as a written one does (`$f:Buf[$t][rows]`), under the same rule (`E-EXTENT`), so a generated column goes to a call whole and pays no part guard.

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
  for m in mass { if m > top { top = m; } }
  return top;
}

pub fn main() -> i32 {
  let mut columns = Particle_columns_new(4);
  columns.mass[2] = 3.0;
  if heaviest(columns.rows, columns.mass) != 3.0 { return 1; }   // whole, by the declared extent
  return 0;
}
```

Recipes take types, naturals and function names, not expressions: behaviour reaches generated code as a `fn` value or a closure. Expansion runs before checking and reads only the recipe and the schema, and `cairn expand` prints the result as source. It is hygienic: a name the recipe uses means what it means in the recipe's module (`helper(x)` becomes `lib.helper(x)`, and a private one is `E-PRIVATE` there), and only `$` splices belong to the deriving module. What a recipe generates is checked like any other code of the deriving module.

A generated name that already exists is `E-DERIVE-COLLISION`, and static iteration is bounded (`E-EXPANSION-LIMIT`). The receipt pins every recipe by the hash of its tokens.

A bare recipe name falls back to the packaged `std.<name>`, then to `std.derived`. `derive wire` emits fixed-width little-endian codecs with no padding. `derive eq`, `derive ord` (lexicographic) and `derive hash` implement `std.core`'s traits for any record whose fields already have them.

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

`derive grad for f;` writes the reverse-mode derivative of `f` as an ordinary function the checker checks; [numerics.md](numerics.md#gradients) has its rules.
