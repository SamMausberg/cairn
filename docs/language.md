# CAIRN 1.0 language profile

This document specifies implemented behavior. `docs/history/` records earlier proposals and must not be used to infer accepted features. Grammar lives in `syntax.py`; types, ownership and effects in `checking.py`; lowering in `codegen.py`; guards in `runtime/*.hpp`. Every construct below is executed natively by the test suite; none of it is a whole-compiler proof (see verification.md).

Three rules explain most of the language. **Costs are visible**: nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row. **Borrows are second class**: a borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references. **Short forms are contracts**: `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

## Source and scalars

ASCII identifiers, UTF-8 comments, braces, semicolons, `//` comments. No shadowing, no implicit conversion, no operator overloading, no implicit block-tail return. Literals: decimal and `0x` integers, floats with a point or exponent, `true`/`false`, `'c'` (one byte, a `u8`-compatible integer literal) and `"text"` (a static `ro<u8>[n]` view; escapes `\n \t \r \0 \\ \" \' \xNN`). Parameters and `let` locals are immutable; `let mut` is mutable. `reg` and `each i in n` remain aliases.

Scalars: `bool`, `u8 u16 u32 u64 usize` (64-bit), `i8 i16 i32 i64`, `f32 f64`. `const N:usize = 256;` declares one scalar literal. `fn inc(x:u64) -> u64 = add_wrap(x,1);` is one return; blocks need explicit returns and every nonvoid path must return.

Ordinary integer `+ - *` abort on overflow in every build; `/ %` reject zero and signed minimum over -1; signed remainder truncates toward zero. `add_wrap sub_wrap mul_wrap` are modular; `shl_wrap shr` need a `usize` count below the width; `& | ^ ~` are unsigned; `min max` are integer-only. Conversions are explicit type calls, integer narrowing is range checked, float-to-integer is unsupported. A literal takes its expected type, else `u64`/`f64`. Floats compile with `-ffp-contract=off -fno-fast-math` (and `--fmad=false` on the device): no contraction or reassociation is ever authorized. A failed guard aborts; it does not unwind or roll back.

## Records, sums, generics

```cairn
struct Pair[T] { a:T; b:T; }                 // copyable when its fields are
enum Option[T] { Some(T); None; }            // a tagged sum; zero or one payload per variant
enum Op { Read; Write; }                     // tag-only enum: equality allowed
struct Header packed { kind:u8; size:u32; }  // or align(64)
```

Fields and payloads are any value type (scalars, records, sums, owners), never a borrow or `void`, and never their own type by value (reach it through `Buf`). Construct `Pair(1, 2)`, `Option.Some(x)`, `Option[u64].None`; type arguments are inferred from arguments, literals and the expected type, or written explicitly. `match` evaluates its subject once and needs exactly one arm per variant with no wildcard; a payload arm binds one fresh immutable value, and matching an owner consumes it. `try e` takes a two-variant sum (success first, failure second), yields the success payload, and otherwise returns the failure from the enclosing function, whose return type must be the same sum family with the same failure payload. It is the only propagation form and it is always written out.

Functions take type and natural parameters: `fn largest[T](a:T, b:T) -> T`, `fn scale[K:nat](...)`, called as `largest(3, 9)` or `scale[4](...)`. Every instance is monomorphized on demand and checked as ordinary code, so an instance, not its template, is what typechecks; never-instantiated templates are listed in the receipt (`uninstantiated_templates`) rather than silently trusted. `family gain = scale[1..257];` still names a bounded range of instances.

## Traits and methods

```cairn
trait Shape { fn area(self:ro<Self>) -> u64; }
impl Shape for Square { fn area(self:ro<Square>) -> u64 = self.side * self.side; }
fn total[S: Shape](x:ro<S>, y:ro<S>) -> u64 = area(x) + area(y);
```

Dispatch is static, on the type of the `Self` argument; a bound is checked when the instance is made. `value.f(args)` is `f(value, args)`, looked up first in the module that declares the receiver's type. There is no inheritance and no implicit boxing.

## Borrows

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements; `ro<T>` and `rw<T>` borrow one value. Arguments are passed by naming a place (`buf`, `v.data`, `grid`); an `ro<T>` parameter also accepts a temporary. Inside the callee a single borrow reads and assigns like the value itself. An array extent is a literal or an earlier immutable `usize` parameter (extern declarations may name a later one); `len(view)` reads that metadata; extents must agree by name/literal identity, and `len(v)` supplies the identity of `v`. A part `x[lo..hi]` may be passed wherever an array borrow is expected and carries one dynamic guard (`lo <= hi <= len` and `hi - lo` equals the callee's extent, which for a part may be any `usize` expression: `take(n - 1, text[1..n])`). Read-only borrows may alias. A mutable borrow must not overlap any other argument of the same call: distinct fields of one record are disjoint, and two parts of one array are disjoint only when they visibly share a boundary (`b[0..mid]`, `b[mid..n]`). Entry guards still check null, alignment, length and overlap numerically. Borrows cannot be stored, returned or bound to a local; the one exception is `let s = "text";`, whose storage is static.

## Owners

```cairn
buffer scratch:u64[n] = zeroed;     // lexical heap array, extent identity n
stack counts:usize[256] = zeroed;   // fixed local storage, 65536 bytes per function at most
let mut b = Buf[u64](n);            // first-class, movable, zeroed heap array
let mut grid = Array[u64, 4]();     // inline fixed array value
```

Every type has an all-zero value, so storage of any element type is zero-initialized. Owners are **affine**: using one as a value (binding, by-value argument, return, field initializer) moves it and its name is dead afterwards (`E-MOVED`); release at scope exit is implicit and visible as the `free` effect. An owner cannot be moved out of a place: `take(place)` moves the value out and leaves zero, `swap(a, b)` exchanges two places. An outer owner cannot be moved inside a loop, closure or lane. `linear struct Token { ... }` values must be consumed exactly once on every path (`E-LINEAR-LEAK`, `E-LINEAR-BRANCH`); `defer call(...);` schedules one visible call for every normal exit of its block and counts as that consumption. Aborts do not promise cleanup. Growth is library code: `std.vec` reallocates with `Buf`, `swap` and an assignment, so its allocation appears in every caller's effect row.

## Control

`for i in lo..hi` evaluates `lo` then `hi` once; empty and reversed ranges do nothing. `if / else if / else`, `while`, `break`, `continue` (nearest loop, also from a match arm), nested `{ }` blocks. Logical operators short-circuit. A call that writes through a borrow or allocates cannot be a nested operand (`E-EFFECT-ORDER`); bind it first. No loop implies parallelism.

## Function values and closures

`fn(u64) -> u64` is a copyable code pointer to a plain declared function of values; it can be stored in records. `ro<fn(u64) -> u64>` is a borrowed callable: pass a declared function or write a closure in place, `apply(n, xs, |x:u64| -> u64 { return x + bias; })`. A closure captures its enclosing scope by reference, exists only as that argument, and therefore never allocates or escapes; its effects belong to the function that wrote it, and the callee shows `indirect_call`. Function types carry values and single borrows, not array views.

## Effects and the foreign boundary

Each function's row is the least fixed point of its local effects and its callees' rows with borrowed footprints renamed to the caller's arguments: `read:x`, `write:x`, `local_read`, `local_write`, `alloc`, `free`, `zero_init`, `stack_storage`, `gpu_alloc`, `gpu_free`, `transfer:h2d|d2h|d2d|h2h`, `par:host`, `par:device`, `indirect_call`, `ffi:symbol`, `io` (or any label an extern declares), `mmio`, `asm`, `trap`, `diverge`, `ffi_precondition`. A row says what may happen, never what is computed. `fn f(...) -> T pure { ... }` and `effects(read:x, trap)` are checked ceilings (`E-EFFECT-CEILING`).

```cairn
extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);
fn say(n:usize, text:ro<u8>[n]) { unsafe { let sent = write(1, text, n); } }
```

An `extern` declares its C symbol, signature and effects; its body is invisible, so its effects are mandatory and `ffi:write` propagates to every transitive caller. Foreign calls, `mmio_read[u32](addr)`, `mmio_write[u32](addr, v)` and `asm("wfi")` are legal only inside `unsafe { }`, which is counted per function in the receipt. A caller must supply live, initialized, correctly typed storage for each borrow; numerical guards cannot establish provenance.

## Parallel regions and placement

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
```

`parallel i in n { body }` runs one lane per index and completes before the next statement. A view's placement is part of its type: `@host` (default), `@pinned`, `@unified`, `@device`. If the body indexes a `@device` view it runs as CUDA lanes, otherwise on host threads created for that statement; the emitted lane body is the same lambda either way. Host code cannot index `@device` memory and device lanes cannot index host memory (`E-PLACEMENT`); `@unified` is visible to both. Lanes are race free by construction: whatever any lane writes may be touched only at element `[i]` (`E-PARALLEL-RACE`), shared scalars cannot be assigned (`E-PARALLEL-WRITE`, use `reduce`), lanes cannot return, nest or move outer owners, and a lane may call only functions whose rows are pure-like (host lanes may also allocate). `buffer d:f32[n]@device = zeroed;` is a scoped device owner; `transfer(dst, src)` is the only way elements cross a placement boundary.

`let s = reduce add_wrap for i in n yield x[i];` combines with one of `add_wrap mul_wrap & | ^ min max` (integers) or `+ *` (floats). On the host it is an in-order fold; over device views it is a tree whose association order is unspecified, which is exact for the integer operators and explicitly not for floats. Checked integer `+` is not offered because its trap would depend on that order. The bounded collector over a `@device` output is stable stream compaction with the same contract. Thread start-up makes host regions pay off only for large `n`; evidence/v1_0/gpu/benchmark.json records measured break-even points.

## Modules

`module net.http;` names the module of the declarations that follow; files without it share the root namespace. `pub` exports a declaration (impl members are always public). `import net.http;` lets you write `http.get(...)` and `http.Request`; `import a.b as c;` renames; `import std.core (Option, Result);` also brings those names in unqualified. A project's files are compiled together in manifest order; `std.*` modules ship inside the package and are linked on demand. Only project modules and `std.*` can be imported; nothing is downloaded.

## Contracted forms

`let used = compact out for i in n where predicate yield value;` writes the stable selected prefix into existing storage of capacity exactly `n`, evaluates the predicate once per input and the projection only when selected, never reads its output, leaves the tail unchanged and allocates nothing. Its one unchecked store is justified by seventeen affine certificates that are checked before every emission and proved sound in Lean, together with in-bounds stores and stable selection for the loop model (verification.md). `derive wire for Packet;` emits fixed-width unsigned little-endian codecs in declaration order with no padding.

## Projects

`cairn.toml` lists ordered sources and independent task files; it is data, never a build script. `[build] kind = "exe" | "library"`, `arch = "baseline"` or a named profile of the host family (x86-64, AArch64), `target = "hosted"` or a freestanding board such as `"aarch64-virt"`, which refuses any program whose effect rows need a hosted runtime (docs/freestanding.md). The host chooses trusted compilers (`clang++`, `g++`, and `nvcc` when a program uses the device); builds use fresh directories. Generated C++ is readable and keeps the C ABI for every function whose signature is C compatible.

## Scope of proof

Typed, native-built, finite-tested, SMT-equivalent and Lean-checked are distinct claims; see verification.md. Generic instances, owners, lanes and the foreign boundary are native-implemented and tested, not mechanized.
