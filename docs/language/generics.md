# Generics, traits and closures

## Generics

A function takes type parameters and natural parameters. Arguments for them are inferred from the arguments, the literals and the expected type, or written out where nothing else says them (`largest[u64](3, 9)`, `scale[4](x)`, `Option[u64].None`). A literal argument takes its type from the other arguments and then from the expected result. `T(x)` converts, or constructs, at the instance's `T`.

Every instance is monomorphized on demand and checked as ordinary code, so an instance, not its template, is what typechecks. Arguments are typed where they are written: the template's module decides what its own text means, never what the caller's expressions mean.

```cairn
struct Column[T] { values:Buf[T]; used:usize; }

fn mean[T:numeric](n:usize, xs:ro<T>[n]) -> T {
  let mut total:T = 0;
  for i in 0..n { total = total + xs[i]; }
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

A natural parameter may be a view's static extent, and is then inferred from a literal, a fixed array or a literal extent passed there. `family` names a bounded range of instances of one natural template.

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

A parameter promises more with a bound: a trait, a kind, or a closed class of scalars. `[T:copy]` may be used many times and dropped. `[T:affine]` may be dropped and kept in zeroed storage. Saying nothing admits `linear` values too, which must be consumed exactly once. The classes are `integer unsigned signed float numeric scalar`, and a class licenses the operators and literals of its types. Bounds combine with `+`, and generic records take them too.

A bound is checked where the instance is requested, so misuse is reported at the call, in the caller's terms.

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

`cairn check --generics` checks each template once, at opaque witness types that offer only what its bounds promise and must be consumed exactly once (the strictest kind); a parameter bounded by a scalar class is checked at every type of that class.

`ok` means every instance whose arguments satisfy the bounds will check. Any other verdict names the first thing the body needed beyond its bounds: an operator on a bare `T`, a second use of a `T`, dropping one, zeroed storage of one. Where a parameter promised no kind, the verdict says so.

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

The command exits 1 unless every template certifies, so a project may hold itself to that, and every template of `std` certifies. Without the flag, a program's own unbounded templates are still accepted per instance, and a template nobody instantiates is listed in the receipt as `uninstantiated_templates`, never silently trusted.

## Traits

Dispatch is static, on the type of the `Self` argument, and a bound such as `[K:Hash + Eq]` is checked when the instance is made. An impl defines exactly its trait's members, with exactly their declared modes and types and `Self` replaced by the implementing type (`E-TRAIT-IMPL`), whether or not they are ever used, in one `impl` block. Two blocks for one `Self` type are `E-TRAIT-OVERLAP`, however they split the members. There is no inheritance and no implicit boxing.

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

Every generic parameter of an impl appears in its `Self` type, so an instance is made from the type alone, and a generic impl applies exactly where its bounds hold. That is how `std.core` covers every scalar class at once (`impl[T:integer] Ord for T`).

One type has one implementation. A generic impl and a concrete impl that both match one type are refused, and so is a generic impl whose bound asks the question it answers (`impl[T:Frame] Frame for T`). Static calls, `dyn` borrows and `Dyn` values all resolve through that one answer.

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
pub fn add(r:rw<Ring>, byte:u8) { r.slots[r.used] = byte; r.used = r.used + 1; }

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

A member is dyn-compatible when only its receiver is a borrow or mentions `Self`, which excludes returning `Self`. A trait goes behind `dyn` only if every member is, because the table has a slot for each.

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

The owned form is explicit. `Dyn[Frame](Packet(40))` moves a value of any implementing type that is not `linear` to the heap (`alloc`, `free`). The box drops what it holds, so a `linear` value may not go in one (`E-LINEAR-STORAGE`); it may only be consumed.

A `Dyn[Frame]` is an ordinary affine value that can live in a `Vec` or a record, its members dispatch, and it lends itself wherever a `dyn Frame` reference is expected. An empty one, moved from or out of zeroed storage, is a guard failure when it is lent, never a null call.

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
  if run(len(pipeline), pipeline, 21) != 32 { return 1; }
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
  scale(len(samples), samples, |x:u64| -> u64 { calls = calls + 1; return x * gain; });
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
