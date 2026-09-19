# CAIRN 1.1 language profile

This document specifies implemented behavior. `docs/history/` records earlier proposals and must not be used to infer accepted features. Grammar lives in `syntax.py`; types, ownership and effects in `checking.py`; lowering in `codegen.py`; guards in `runtime/*.hpp`. Every construct below is executed natively by the test suite; none of it is a whole-compiler proof (see verification.md).

Three rules explain most of the language. **Costs are visible**: nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row. **Borrows are second class**: a borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references. **Short forms are contracts**: `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

## Source and scalars

ASCII identifiers, UTF-8 comments, braces, semicolons, `//` comments. No shadowing, no implicit conversion, no operator overloading, no implicit block-tail return. Literals: decimal and `0x` integers, floats with a point or exponent, `true`/`false`, `'c'` (one byte, a `u8`-compatible integer literal) and `"text"` (a static `ro<u8>[n]` view; escapes `\n \t \r \0 \\ \" \' \xNN`). Parameters and `let` locals are immutable; `let mut` is mutable. `reg` and `each i in n` remain aliases.

Scalars: `bool`, `u8 u16 u32 u64 usize` (64-bit), `i8 i16 i32 i64`, `f32 f64`. `const N:usize = 256;` declares one scalar literal. `fn inc(x:u64) -> u64 = add_wrap(x,1);` is one return; blocks need explicit returns and every nonvoid path must return.

Ordinary integer `+ - *` abort on overflow in every build; `/ %` reject zero and signed minimum over -1; signed remainder truncates toward zero. `add_wrap sub_wrap mul_wrap` are modular; `shl_wrap shr` need a `usize` count below the width; `& | ^ ~` are unsigned; `min max` are integer-only. Conversions are explicit type calls and integer targets are range checked: narrowing traps outside the target, and float-to-integer truncates toward zero and traps on NaN or an out-of-range value. A literal takes its expected type, else `u64`/`f64`. Floats compile with `-ffp-contract=off -fno-fast-math` (and `--fmad=false` on the device): no contraction or reassociation is ever authorized. A failed guard aborts; it does not unwind or roll back.

## Records, sums, generics

```cairn
struct Pair[T] { a:T; b:T; }                 // copyable when its fields are
enum Option[T] { Some(T); None; }            // a tagged sum; zero or one payload per variant
enum Op { Read; Write; }                     // tag-only enum: equality allowed
struct Header packed { kind:u8; size:u32; }  // or align(64): a power of two from 8 to 65536
```

Fields and payloads are any value type (scalars, records, sums, owners), never a borrow or `void`, and never their own type by value, directly or in an inline `Array` (reach it through `Buf`). Construct `Pair(1, 2)`, `Option.Some(x)`, `Option[u64].None`; type arguments are inferred from arguments, literals and the expected type, or written explicitly. `match` evaluates its subject once and needs exactly one arm per variant with no wildcard; a payload arm binds one fresh immutable value, and matching an owner consumes it. `try e` takes a two-variant sum (success first, failure second), yields the success payload, and otherwise returns the failure from the enclosing function (or closure), whose return type must be a two-variant sum with the same failure payload; the families may differ, so a `Done[E]` failure propagates out of a function returning `Result[T, E]`. Written inside a larger expression, a `try` may not sit beside an operand that already owns something (`E-EFFECT-ORDER`: bind the `try` first), because leaving from there would abandon it. It is the only propagation form and it is always written out.

Functions take type and natural parameters: `fn largest[T](a:T, b:T) -> T`, `fn scale[K:nat](...)`, called as `largest(3, 9)` or `scale[4](...)`; a natural may be a view's static extent (`out:rw<u64>[K]`), and a literal argument takes its type from the other arguments, then from the expected result (`let y:u32 = conv(3);`). Every instance is monomorphized on demand and checked as ordinary code, so an instance, not its template, is what typechecks. `cairn check --generics` additionally checks each template once at opaque witness types that offer only what its bounds promise and must be consumed exactly once (the strictest kind): `ok` means every instance whose arguments satisfy the bounds will check, and anything else names the first thing the body needed beyond its bounds (an operator on a bare `T`, a second use of a `T`, dropping one, zeroed storage of one). A parameter promises more with a bound: a trait, a kind bound (`[T: copy]` may be used many times and dropped, `[T: affine]` may be dropped and kept in zeroed storage; saying nothing admits `linear` values too, which must be consumed exactly once) or a closed scalar class (`integer unsigned signed float numeric scalar`, which licenses operators and literals and is certified by checking every type of the class), combined with `+` and also allowed on generic records (`struct Vec[T: affine]`). A bound is checked where the instance is requested, so misuse is reported at the call in the caller's terms (`E-BOUND`: `Token is linear, not affine; std.vec.push needs [T: affine]`) rather than from inside the template. Every template of `std` certifies; a program's own unbounded templates are still accepted per instance as before; never-instantiated templates are listed in the receipt (`uninstantiated_templates`) rather than silently trusted. `family gain = scale[1..257];` still names a bounded range of instances.

## Traits and methods

```cairn
trait Shape { fn area(self:ro<Self>) -> u64; }
impl Shape for Square { fn area(self:ro<Square>) -> u64 = self.side * self.side; }
fn total[S: Shape](x:ro<S>, y:ro<S>) -> u64 = area(x) + area(y);
```

Dispatch is static, on the type of the `Self` argument; a bound (`[K: Hash + Eq]`) is checked when the instance is made. An impl defines exactly its trait's members with exactly their declared modes and types, `Self` replaced by the implementing type (`E-TRAIT-IMPL`), whether or not it is ever used. Every generic parameter of an impl appears in its `Self` type, so instances are made from the type alone. One type has one implementation: a generic `impl[T] Tag for T` and a concrete `impl Tag for S` that both match `S` are rejected (`E-TRAIT-OVERLAP`), and static calls, `dyn` borrows and `Dyn` values all resolve through that one answer. When two traits the type implements declare the same member name, write the trait: `B.go(s)` (`E-TRAIT-AMBIGUOUS`). `value.f(args)` is `f(value, args)`, looked up first in the module that declares the receiver's type and before the builtins, so a type may have its own `len` (an unqualified `len(x)` is still the builtin; only the names that became builtins in 1.0, `take swap transfer wait mmio_read mmio_write asm`, yield to a function the calling module can name); privacy is still judged from where the call is written. There is no inheritance and no implicit boxing.

## Borrows

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements; `ro<T>` and `rw<T>` borrow one value. Arguments are passed by naming a place (`buf`, `v.data`, `grid`); an `ro<T>` parameter also accepts a temporary. Inside the callee a single borrow reads and assigns like the value itself. An array extent is a literal or an earlier immutable `usize` parameter (extern declarations may name a later one); `len(view)` reads that metadata; extents must agree by name/literal identity, and `len(v)` supplies the identity of `v`. A part `x[lo..hi]` (or a part of a part, `x[a..b][c..d]`, one guard per level) may be passed wherever an array borrow is expected and carries one dynamic guard (`lo <= hi <= len` and `hi - lo` equals the callee's extent, which for a part may be any `usize` expression: `take(n - 1, text[1..n])`). Read-only borrows may alias. A mutable borrow must not overlap any other argument of the same call: distinct fields of one record are disjoint, and two parts of one array are disjoint only when they visibly share a boundary (`b[0..mid]`, `b[mid..n]`). Entry guards still check null, alignment, length and overlap numerically. Borrows cannot be stored, returned or bound to a local; the one exception is `let s = "text";`, whose storage is static.

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

`fn(u64) -> u64` is a copyable code pointer to a plain declared function of values (an instantiated generic such as `ascending[u64]` qualifies); it can be stored in records, its zero value is legal and calling it is a guard failure, and whoever calls through one inherits the effects of every function whose address is taken. `ro<fn(u64) -> u64>` is a borrowed callable: pass a declared function or write a closure in place, `apply(n, xs, |x:u64| -> u64 { return x + bias; })`. A closure captures its enclosing scope by reference, exists only as that argument, and therefore never allocates or escapes; its effects belong to the function that wrote it, and the callee shows `indirect_call`. What a closure captures it borrows for that call, `rw` where it writes: the same call cannot also lend, move or write a place the closure touches (`E-ALIAS`), so a callee never sees a closure reach anything it was given. Function types carry values and single borrows, not array views.

## Effects and the foreign boundary

Each function's row is the least fixed point of its local effects and its callees' rows with borrowed footprints renamed to the caller's arguments: `read:x`, `write:x`, `local_read`, `local_write`, `alloc`, `free`, `zero_init`, `stack_storage`, `gpu_alloc`, `gpu_free`, `transfer:h2d|d2h|d2d|h2h`, `par:host`, `par:device`, `spawn`, `join`, `atomic`, `lock`, `indirect_call`, `dispatch`, `lane:f`, `ffi:symbol`, `io` (or any label an extern declares), `mmio`, `asm`, `trap`, `diverge`, `ffi_precondition`. A row says what may happen, never what is computed. `fn f(...) -> T pure { ... }` and `effects(read:x, trap)` are checked ceilings (`E-EFFECT-CEILING`).

```cairn
extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);
fn say(n:usize, text:ro<u8>[n]) { unsafe { let sent = write(1, text, n); } }
```

An `extern` declares its C symbol (`extern "close" fn close_fd(fd:i32) -> i32 effects(io);` binds it under another name), signature and effects; its body is invisible, so its effects are mandatory and `ffi:write` propagates to every transitive caller. Foreign calls, `mmio_read[u32](addr)`, `mmio_write[u32](addr, v)` and `asm("wfi")` are legal only inside `unsafe { }`, which is counted per function in the receipt. A caller must supply live, initialized, correctly typed storage for each borrow; numerical guards cannot establish provenance.

## Parallel regions and placement

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
```

`parallel i in n { body }` runs one lane per index and completes before the next statement. A view's placement is part of its type: `@host` (default), `@pinned`, `@unified`, `@device`. If the body indexes a `@device` view it runs as CUDA lanes, otherwise on host threads created for that statement; the emitted lane body is the same lambda either way. Host code cannot index `@device` memory and device lanes cannot index host memory (`E-PLACEMENT`); `@unified` is visible to both. Lanes are race free by construction: whatever any lane writes may be touched only at element `[i]` (`E-PARALLEL-RACE`), shared scalars cannot be assigned (`E-PARALLEL-WRITE`, use `reduce`), lanes cannot return, nest or move outer owners, and a lane's own row and the row of everything it calls must be pure-like (`E-PARALLEL-CALL`; host lanes may also allocate, use atomics and lock). A lane may call, or hand on to a helper, a `fn` parameter of its function: that leaves the footprint `lane:f` in the row (and in a declared ceiling), renamed up the call graph like `read:x`, and whatever is finally passed is judged where it is written, so `map(n, out, |x:u64| -> u64 { return x * k; })` is accepted, a closure that writes what it captured is not, and a stored `fn` value counts as any function of its type whose address was taken. Dynamic dispatch from a host lane, from a function it calls, or from such a closure is judged against every implementation. Whatever a device lane reaches is device code: a helper with a host view parameter or a host-only construct in its body is refused there (`E-PLACEMENT`), while plain pure helpers run on either side. String literals and host owners are host memory and stay out of device code (`E-PLACEMENT`); a device `compact` checks its predicate and projection as device lanes. `kernel fn at(w:usize, n:usize, grid:ro<f32>[n]@device, x:usize, y:usize) -> f32 = grid[y * w + x];` is a device helper: its body is device code, it may be called only from device lanes and other kernels (`E-PLACEMENT` elsewhere), and it obeys the device lane rules. `buffer d:f32[n]@device = zeroed;` is a scoped device owner; `transfer(dst, src)` is the only way elements cross a placement boundary; extents agree by identity, parts such as `transfer(a[0..k], b[2..6])` by one length guard, and the two sides may not overlap.

`let s = reduce add_wrap for i in n yield x[i];` combines with one of `add_wrap mul_wrap & | ^ min max` (integers) or `+ *` (floats). On the host it is an in-order fold; over device views it is a tree whose association order is unspecified, which is exact for the integer operators and explicitly not for floats. Checked `+` is offered on unsigned integers, where no partial sum can overflow unless the total does, so the trap cannot depend on the order (on the device the sum carries an overflow flag through the reduction and the host traps); signed `+` and integer `*` are not, because a partial result can overflow alone. The bounded collector over a `@device` output is stable stream compaction with the same contract. Thread start-up makes host regions pay off only for large `n`; evidence/v1_0/gpu/benchmark.json records measured break-even points.

## Tasks and shared state

```cairn
let left  = spawn fill(mid, data[0..mid], 0);
let right = spawn fill(n - mid, data[mid..n], 500);
wait(left);
wait(right);
```

`let t = spawn f(args);` runs a declared function on its own thread. Arguments are evaluated at the spawn and carried by value, so the task never reads the spawner's locals. `t` is a linear ticket bound to its scope: it must be consumed by `wait(t)`, which returns `f`'s result, on every path of the same function, and it cannot be stored, passed or returned. Until then every place lent to the task is **leased**: nobody may write what the task reads or touch what it writes (`E-LEASED`), including by moving the owner. Read-only lending is shared freely, and visibly disjoint parts of one array may be lent mutably to different tasks: a part ends where the next begins (`d[0..a]`, `d[a..b]`, `d[b..n]`), bounds are literals or names that cannot change, and because every lent part was guarded `lo <= hi` the order of the bounds chains through the parts in between, so a K-way split works. An array view lends the elements, so `len(d)` stays readable; an owner lent whole (`rw<Buf[T]>`) does not. A temporary given to a task's single borrow rides along by value. A closure cannot follow a task to another thread.

Device work can be queued instead of awaited: `let up = spawn transfer(x, a);` and `let k = spawn parallel i in n after up { y[i] = 2.0 * x[i]; };` put a transfer or a device region on a stream of its own and return at once, so the host and other queued work go on. The ticket is the same linear, scope-bound value (its type is `Ticket[void]@device`): it holds a lease on every view the work touches, `rw` where it writes, until `wait`, and only completion restores ordinary access. `after a, b` orders the new work behind live tickets by device events, never by stopping the host; work queued after a ticket may touch what that ticket (and whatever it was itself queued after) holds, and nothing else may. Only device work is queued this way (`E-SPAWN`); host work and blocking I/O become asynchronous by spawning the function that does them, which leases their buffers the same way. A failed enqueue or a lost device traps; there is no cancellation.

`Atomic[T]` (integers and `bool`) and `Mutex[T]` are declared in place, `let hits = Atomic[u64](0);`, and shared by `ro` borrow; they are the only interior mutability in the language and are never stored, passed by value or returned (`E-PINNED`). Every atomic access names its memory order: `hits.fetch_add(1, Order.relaxed)`, `load`, `store`, `swap`, `fetch_sub/and/or/xor`, `compare_exchange(expected, desired, Order.seq_cst, Order.seq_cst)`. A mutex has one operation, `m.with(|state:rw<T>| { ... })`, which may return a value; no guard object exists to escape, the closure cannot name the mutex it holds (`E-ALIAS`), and a thread that reaches the same mutex again through another borrow traps instead of relocking. Lanes may use both. The effects are `spawn`, `join`, `atomic` and `lock`.

## Dynamic interfaces

`fn measure(s:ro<dyn Shape>) -> u64 = area(s) + 1;` takes any named place whose type implements the trait. The call site builds a two-word reference (object, static table); members called on it go through the table, add the `dispatch` effect, and contribute the effect rows of every implementation. A member is dyn-compatible when only its receiver is a borrow or mentions `Self`, which excludes returning `Self`; a trait goes behind `dyn` only if every member is (`E-DYN`), since the static table has a slot for each. By-value owners pass through the table by move. Dynamic references are borrows, so they are never values and nothing escapes through them. The owned form is explicit: `let d = Dyn[Shape](Square(3));` moves a value of any implementing type to the heap (`alloc`, `free`), `Dyn[Shape]` is an ordinary affine value that can live in a `Vec` or a record, members called on it dispatch, and it lends itself wherever a `dyn Shape` reference is expected. An empty one (moved from, or from zeroed storage) is a guard failure when lent, never a null call.

## Modules

`module net.http;` names the module of the declarations that follow; files without it share the root namespace. `pub` exports a declaration (impl members are always public). `import net.http;` lets you write `http.get(...)` and `http.Request`; `import a.b as c;` renames; `import std.core (Option, Result);` also brings those names in unqualified, and may not hide a name the importing module declares (`E-DUPLICATE`). A private record's fields are as private as the record. `family gain = lib.scale[1..3];` needs `lib.scale` to be `pub`, and its instances belong to the module that wrote the family (`pub family` exports them); `derive wire for R;` is written in the module that declares `R`. A project's files are compiled together in manifest order; `std.*` modules ship inside the package and are linked on demand. Only project modules and `std.*` can be imported; nothing is downloaded.

## Contracted forms

`let used = compact out for i in n where predicate yield value;` writes the stable selected prefix into existing storage of capacity exactly `n`, evaluates the predicate once per input and the projection only when selected, never reads its output, leaves the tail unchanged and allocates nothing on the host (on a `@device` output it is a stable stream compaction whose scan needs device scratch, shown as `gpu_alloc`, `gpu_free`; device `reduce` likewise). Its one unchecked store is justified by seventeen affine certificates that are checked before every emission and proved sound in Lean, together with in-bounds stores and stable selection for the loop model (verification.md). `derive wire for Packet;` emits fixed-width unsigned little-endian codecs in declaration order with no padding; it is no longer compiler code but the packaged recipe `std.wire`.

## Recipes

```cairn
pub recipe columns for R {                                  // R: the record it is derived for
  each f in R { require scalar(f), "columns holds scalar fields."; }
  pub struct $R_columns { each f in R where t = typeof(f) { $f:Buf[$t]; } }
  pub fn $R_get(c:ro<$R_columns>, i:usize) -> R = R(each f in R { c.$f[i] });
  pub fn $R_set(c:rw<$R_columns>, i:usize, row:R) { each f in R { c.$f[i] = row.$f; } }
}
derive columns for Particle;                                // Particle_columns, Particle_get, Particle_set
```

A recipe is a generator written as library code: ordinary declarations (functions and records) over a record schema (`for R`) and naturals (`recipe tiles[W:nat, H:nat]`), applied by `derive name[naturals] for Type;`. Inside it, `each x in R { ... }` iterates statically over a record's fields and `each k in lo..hi { ... }` over a natural range, at declaration level, statement level, in a record's field list, or among the arguments of a call (where it splices a list); `fold | each ... { e }` joins the expansions with one operator. `where a = offset(f), t = typeof(f)` names static values, computed from naturals, comparisons and the facts `bytes bits offset index count typeof unsigned signed integer float scalar record`; `$name` splices one into an identifier (`encode_$R`, `value.$f`, `shift_$k`; the longest static name wins, so `$R_columns` is `$R` then `_columns`), and a whole `$name` is that natural or that type. Only the bare `for` parameter `R` is the type itself; every other static needs its `$`, so an ordinary identifier that happens to share a `where` name is left alone. Static values are naturals and booleans: a negative result or a division by zero is `E-RECIPE-STATIC`. `require condition, "message";` states the admissible inputs (`E-DERIVE-DOMAIN`, or the code the message opens with).

Expansion happens before checking and reads nothing but the recipe and the schema, so it is a function of its inputs; what it generates is ordinary code of the deriving module, checked like any other: a recipe's signatures and effect ceilings are its contract, privacy is judged where `derive` is written (a recipe reaches its own module's helpers by their public path), a generated name that already exists is `E-DERIVE-COLLISION`, static iteration is bounded per level and in total (`E-EXPANSION-LIMIT`), a derivation for a record that another derivation generates waits for it whatever the order they are written in, and the receipt pins every recipe by the hash of its tokens (`recipes`) beside the list of `derivations`. A bare recipe name that the program does not declare falls back to the packaged `std.<name>`. Recipes take types and naturals, not expressions: behavior is passed to generated functions as `fn` values or closures.

## Projects

`cairn.toml` lists ordered sources and independent task files; it is data, never a build script. `[build] kind = "exe" | "library"`, `arch = "baseline"` or a named profile of the host family (x86-64, AArch64), `target = "hosted"` or a freestanding board such as `"aarch64-virt"`, which refuses any program whose effect rows need a hosted runtime (docs/freestanding.md). The host chooses trusted compilers (`clang++`, `g++`, and `nvcc` when a program uses the device); builds use fresh directories. Generated C++ is readable and keeps the C ABI for every function whose signature is C compatible. A library exports every function; an executable contains only what its `main` reaches (`main` may live in a module), while the receipt still covers everything that was checked. `--debug` adds symbols and `#line` maps to the authored files. `--incremental` compiles one object per module against a shared interface header (types, tables, prototypes) and reuses an object only when its unit, that header, the command line, the runtime headers and the compiler version hash to the same key, so a body-only edit recompiles one module and a signature change recompiles all; it gives up inlining across modules, and device programs and freestanding images stay one unit.

## Scope of proof

Typed, native-built, finite-tested, SMT-equivalent and Lean-checked are distinct claims; see verification.md. Generic instances, owners, lanes and the foreign boundary are native-implemented and tested, not mechanized.
