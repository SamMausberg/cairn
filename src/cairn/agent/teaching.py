"""Feature-selected semantic cards. Shorter wording never grants edit authority."""

from ..compiler.syntax import lex

CARDS = {
    "base": """CAIRN 1.2 is a checked systems language, not Rust/Python. Use fn, typed interfaces,
braces, semicolons, explicit return; let is immutable, let mut is mutable.
Parameters are immutable; shadowing and implicit conversions are forbidden.
fn inc(x:u64)->u64 = add_wrap(x,1); is one return, not a closure.
for i in lo..hi is sequential, half-open; evaluate bounds once, lower first;
only compact/parallel/reduce write for i in n. let x:u32 = 7; annotates. Precedence
rises || && | ^ & (== != < <= > >=) (+ -) (* / %); && and || stop early.
if/else if/else and while use braces. break/continue target the nearest loop,
including from match arms. while/recursion may diverge; no stack bound is proved.
reg/each remain aliases; prefer let mut/for. Blocks have no implicit tail return.
No inheritance, overloading, exceptions or hidden allocation.
Other features arrive as their own cards only when the source uses them.
Ranges are not lists; indentation is insignificant. Preserve the fixed task.
Typed, tested, SMT-equivalent and Lean-verified are different claims.""",
    "integers": """Types: bool, u8/u16/u32/u64, usize(64-bit), i32/i64. +,-,* trap on overflow in
every build. Unsigned add_wrap/sub_wrap/mul_wrap are modular. /,% trap on zero
or signed min/-1; signed remainder truncates toward zero, unlike Python.
shl_wrap(x,k),shr(x,k) require usize k below width. &,|,^,~ are unsigned;
min/max are integer-only. Conversions are calls, u64(x); explicit, range checked: narrowing
traps outside the target; float-to-integer truncates toward zero and traps on NaN
or out of range. Literals use expected type, else u64/f64.
x+1 and add_wrap(x,1) differ at u64 maximum. Never weaken arithmetic or the
allowed trap/domain policy merely to pass a check.""",
    "views": """ro<T>[n] and rw<T>[n] borrow host storage; @host is optional, not a transfer.
An interface extent is a literal or earlier immutable usize parameter. Indexes
are usize; indexing checks bounds. len(view) reads extent metadata, not elements.
Read-only views may alias. Each rw view must be disjoint from every other view
in a call. Entry checks cover numerical null/alignment/overflow/overlap only;
caller supplies live initialized typed storage without conflicting external access.
No arbitrary view aliases, view returns, resizing or implicit copies/parallelism.""",
    "compact": """let used = compact out for i in n where predicate yield value;
out is an rw borrow or scoped buffer of capacity exactly n (len(out) also works).
Predicate is bool; projection has the element type. Neither may read out or
call externally-writing/allocating functions. Selected projections fill a stable prefix;
unused tail is unchanged. No temporary allocation or synchronization. The private
cursor emits at most once per input. Its arithmetic certificates do not prove
the entire compiler, lifetime system, or native backend.""",
    "calls": """Call declared functions, not invented libraries. Calls with external writes or
allocation must be whole expressions at statement/condition roots, not nested operands; a call
that only releases may be nested, since a drop runs where C++ ends the scope.
Read the included callee implementation/contract: its name, types and effect row
do not specify its behavior. Effects substitute caller buffers, including recursive
calls. Request context before calling an undisclosed dependency. Source/model
acceptance does not establish the reference's intent or native performance.""",
    "floats": """f32/f64 use -ffp-contract=off -fno-fast-math. No implicit reassociation or FMA;
floating min/max are unsupported; u64(x) of a float truncates, trapping out of range.
Strict flags are not
a mechanized IEEE proof. Floating exception flags and NaN payloads are outside
the current observation model; compiler/runtime/hardware remain trusted.""",
    "records": """struct Pair {x:u64; y:u64;} is a value record. Construct Pair(a,b), access p.x.
Only mutable locals/rw elements can change. Fields are previously declared,
nonrecursive value types, not views, enums or sums. Copying a record costs work.
Tag-only enum Op {Read; Write;} uses Op.Read; equality is allowed. Tagged sums
have a separate card. Neither construct is a dynamic object or allocation.""",
    "generators": """A function may declare one [K:nat] parameter. family gain=scale[1..257];
instantiates gain_1..gain_256 with bounded expansion, no runtime dispatcher.
Do not append a semicolon after a function block.
derive wire for Packet; accepts fixed-width unsigned fields and creates little-
endian, declaration-order, no-padding codecs. No framing/authentication/validation
is inferred. Generated entries are not direct edit targets. Read generator
contracts; large expansion is not a measured advantage over compact C++ templates.
wire is a library recipe. recipe name[K:nat, F:fn] for R { ... } (F: a function's name,
called as $F(x)) holds ordinary fn and
struct declarations plus static forms: each f in R { } (fields) or each k in lo..hi { }
at declaration, statement, field-list or call-argument level (there it splices a
list, { a.$f, b.$f } gives two per step); fold | each ... { e } joins the expansions with
an operator or a two-operand function (fold add_wrap each ..); where a = offset(f),
w = fold + each f in R { bytes(f) } names static values (naturals, min, max; facts take a
field or a type alike, unsigned(f) or bytes(R): bytes bits offset index count typeof
unsigned signed integer float scalar record); $name splices into identifiers (encode_$R, value.$f) or stands for the
natural/type; bare R is the type; require cond, "message"; states the domain.
A recipe may also hold impl Trait for R { ... }. derive name[naturals] for Type; expands
before checking into code of the deriving module, checked like any other; std offers
derive eq|ord|hash for P; (impls of std.core's Eq, Ord, Hash).""",
    "memory": """buffer scratch:u64[n] = zeroed; explicitly allocates and zero-initializes heap
storage. stack scratch:u64[32] = zeroed; explicitly reserves initialized stack
storage. Elements are scalar; stack capacity is a literal, declarations total at
most 65536 bytes per function (not a bound on recursion/spills/native stack).
Bind computed heap capacity to an immutable usize first. The owner is neither
copyable nor returnable; pass its borrow to helpers. len(scratch) is metadata.
Elements are writable without mut. Buf[u64](n) (owners) is the same array as a movable value.
Storage is released on normal scope exit, return, break and continue. Allocation
failure/guards abort; abort does not promise cleanup. No manual free or escaping
borrow exists. Receipts expose alloc/free/zero_init and private reads/writes.""",
    "sums": """enum Result {Value(u64); Error(u32); Empty;} defines a monomorphic tagged sum.
Use Result.Value(x), Result.Error(code), Result.Empty. Payloads are scalar values,
not borrows/owners/records; no generics or unchecked payload projection.
match result { Result.Value(v)=>{return v;} Result.Error(code)=>{return 0;}
               Result.Empty=>{return 0;} }
Every variant has exactly one arm; payload binders are fresh, immutable and local
to that arm. Match evaluates its subject once. Result values can be copied or
returned with no heap allocation. No implicit propagation/unwrapping. Foreign
callers must supply the valid tag and its initialized active payload. Scalar SMT
verification currently rejects tagged sums, even when their wrappers return integers.
Payloads may be any value type. try x on a two-variant sum (success first) yields the
success payload or returns the failure from the enclosing function, which must return
the same sum family with the same failure payload. It is the only propagation form.""",
    "generics": """fn largest[T](a:T, b:T) -> T and struct Pair[T] {a:T; b:T;} take type parameters;
[K:nat] is a static natural. Instances are monomorphized on demand and each instance is
checked as ordinary code; arguments are inferred from values, literals and the expected
type, or written f[u64](x), Pair[u8](1, 2), Option[u64].None. trait Shape {fn area(self:
ro<Self>) -> u64;} with impl Shape for Square {...} dispatches statically on the Self
argument; [S:Shape] is checked when the instance is made. Bounds join with +: a trait,
a kind (copy: reusable; affine: droppable, storable; none: may be linear) or a scalar class
(integer unsigned signed float numeric scalar: operators and literals allowed). value.f(a) is f(value, a),
found first in the module of the receiver's type. A ro<T> or rw<T> parameter borrows the
named place you pass (a local, a field, another borrow): write area(sq), never &sq; rw
needs a mutable place. No inheritance, no implicit boxing.""",
    "owners": """let mut b = Buf[u64](n); is a first-class zeroed heap array; Array[u64, 4]() is inline.
Owners are affine: binding, passing by value or returning one moves it and the old name
is dead. An owner never moves out of a place: take(place) moves it out leaving zero,
swap(a, b) exchanges places; let Conn(sock, sent) = c; consumes a whole record and binds
every field (the way out for a linear field). An outer owner cannot be moved inside a loop, closure or
lane. linear struct values must be consumed exactly once on every path; defer call(x);
schedules that one visible call for every normal exit of its block. ro<T> and rw<T>
borrow one value and read/assign like the value; x[lo..hi] passes a part of an array with
one dynamic guard; two parts are disjoint only if they visibly share a boundary. Letting an
owner go charges free where the release is: the end of the block or match arm holding it, a
return that leaves while it is still held, a function handed one that passes it on to nobody,
and the place a new value is assigned over. A linear value need not own storage, so consuming
one charges nothing on its own.""",
    "effects": """Every function has an inferred effect row; pure and effects(read:x, trap) after the
return type are checked ceilings, never wishes. extern fn write(fd:i32, data:ro<u8>[n],
n:usize) -> i64 effects(io); declares a C symbol whose effects are mandatory because its
body is invisible; ffi:write then appears in every transitive caller. alloc is charged where
storage is taken and free where it goes back, so fn sink(b:Buf[u64]) {} has the row free and
a ceiling that leaves free out is rejected as E-EFFECT-CEILING. Foreign calls,
mmio_read[u32](addr), mmio_write[u32](addr, v) and asm("wfi") are legal only inside
unsafe { }. Do not widen a ceiling or add unsafe to make an edit pass.""",
    "parallel": """parallel i in n { out[i] = a * x[i] + y[i]; } runs one lane per index and finishes before
the next statement. Placement is part of a view type: @host (default), @pinned, @unified,
@device. Indexing a @device view makes the region CUDA lanes, otherwise host threads; host
code cannot index @device memory and lanes cannot index the other side; a @pinned or
@unified view may be passed where @host is asked (@unified also as @device). The first host
region of a process creates the lane pool and later ones reuse it; a small one is just the loop.
Whatever any lane writes may be touched only at [i]; shared scalars cannot be assigned (use let s = reduce
add_wrap for i in n yield x[i];, which is also the ordinary fold outside any region: i
runs over 0..n and the operator is one of add_wrap mul_wrap min max & | ^, or + * on floats). Lanes cannot return, nest or move outer owners, and
neither a lane nor anything it calls may do I/O, spawn, touch the machine or (on the
device) allocate or use strings and host owners. A lane may call a fn parameter f of its
function (effect lane:f): whoever finally passes a closure must not write what it
captures. kernel fn at(...) -> f32 is a
device helper: it may index @device views, is callable only from device lanes and other
kernels, and cannot allocate, do I/O or start regions. buffer d:f32[n]@device = zeroed;
is a scoped device owner; transfer(dst, src) is the only way across placements. reduce on
the device combines in an unspecified order: exact for add_wrap mul_wrap & | ^ min max,
not for float + and *; reduce + is checked on unsigned integers, not offered on signed.""",
    "tasks": """let t = spawn f(args); runs a declared function on its own thread and gives a linear
ticket that must be consumed by wait(t) in the same function; let r = wait(t); is f's
result (wait(t); alone when f returns nothing). Until then every place
lent to the task is leased: nobody writes what it reads or touches what it writes;
visibly disjoint parts (d[0..mid], d[mid..n]) may be lent mutably to different tasks.
Atomic[u64] and Mutex[T] are declared in place and shared by ro borrow: a.fetch_add(1,
Order.relaxed) always names its memory order; m.with(|s:rw<T>| { ... }) is the only way
into a mutex. Tickets, atomics and mutexes are never stored, passed by value or returned.
Device work can be queued: let up = spawn transfer(x, a); let k = spawn parallel i in n
after up { y[i] = x[i]; }; each returns at once with a linear ticket that leases the
views it touches until wait; after orders it behind live tickets on the device and lets
it share what they hold. Only device regions and transfers are queued.""",
    "closures": """fn(u64) -> u64 is a copyable code pointer to a plain declared function of values.
ro<fn(u64) -> u64> is a borrowed callable: pass a declared function or write the closure
in place, apply(n, xs, |x:u64| -> u64 { return x + bias; }). A closure captures its scope
by reference, exists only as that argument, never allocates, and its effects belong to
the function that wrote it. What it captures it borrows for that call (rw where it
writes), so the same call cannot lend, move or write those places. ro<dyn Shape> / rw<dyn Shape> parameters take any named place
whose type implements the trait; calls through them add the dispatch effect and the
effects of every implementation. Dynamic references are never values; Dyn[Shape](value)
is the owned form: an affine heap value (alloc, free) that dispatches and lends itself as dyn.""",
    "modules": """module net.http; names the module of what follows; pub exports. import net.http; allows
http.get(...); import a.b as c; renames; import std.core (Option, Result); also brings
those names in unqualified. std.* ships with the compiler; nothing is downloaded. A
private name of another module is not callable; request context instead of guessing.""",
}


def select_cards(
    source: str, has_views: bool = False, has_records: bool = False, has_sums: bool = False
) -> dict[str, str]:
    # Actual tokens prevent comments/spacing from silently choosing the curriculum.
    words = {token.s for token in lex(source)}
    wanted = {
        "views": has_views or words & {"buffer", "stack", "len"},
        "compact": "compact" in words,
        "floats": words & {"f32", "f64"},
        "records": has_records,
        "generators": words & {"family", "derive", "recipe"},
        "memory": words & {"buffer", "stack"},
        "sums": has_sums or words & {"match", "try"},
        "generics": words & {"trait", "impl"} or has_generic_brackets(source),
        "owners": words & {"Buf", "Array", "take", "swap", "defer", "linear"},
        "effects": words & {"extern", "unsafe", "pure", "effects"},
        "parallel": words & {"parallel", "reduce", "transfer", "device", "pinned", "unified"},
        "tasks": words & {"spawn", "wait", "Atomic", "Mutex"},
        "closures": words & {"|", "||", "dyn"} and ("dyn" in words or "fn" in words),
        "modules": words & {"module", "import", "pub"},
    }
    return {name: CARDS[name] for name in ["base", "integers", "calls", *(n for n, on in wanted.items() if on)]}


def has_generic_brackets(source: str) -> bool:
    """`fn f[T]`, `struct S[T]` or `enum E[T]`: a declaration keyword, a name, then `[`."""
    tokens = [t.s for t in lex(source)]
    return any(a in {"fn", "struct", "enum"} and c == "[" for a, c in zip(tokens, tokens[2:], strict=False))
