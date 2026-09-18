"""Feature-selected semantic cards. Shorter wording never grants edit authority."""

from .syntax import lex

CARDS = {
    "base": """CAIRN 0.6 is a checked CPU prototype, not Rust/Python. Use fn, typed interfaces,
braces, semicolons, explicit return; let is immutable, let mut is mutable.
Parameters are immutable; shadowing and implicit conversions are forbidden.
fn inc(x:u64)->u64 = add_wrap(x,1); is one return, not a closure.
for i in lo..hi is sequential, half-open; evaluate bounds once, lower first.
if/else if/else and while use braces. break/continue target the nearest loop,
including from match arms. while/recursion may diverge; no stack bound is proved.
reg/each remain aliases; prefer let mut/for. Blocks have no implicit tail return.
No methods, traits, closures, general modules, GPU execution or concurrency.
Ranges are not lists; indentation is insignificant. Preserve the fixed task.
Typed, tested, SMT-equivalent and Lean-verified are different claims.""",
    "integers": """Types: bool, u8/u16/u32/u64, usize(64-bit), i32/i64. +,-,* trap on overflow in
every build. Unsigned add_wrap/sub_wrap/mul_wrap are modular. /,% trap on zero
or signed min/-1; signed remainder truncates toward zero, unlike Python.
shl_wrap(x,k),shr(x,k) require usize k below width. &,|,^,~ are unsigned;
min/max are integer-only. Conversions are explicit, integer narrowing checked;
float-to-integer is unsupported. Literals use expected type, else u64/f64.
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
allocation/free must be whole expressions at statement/condition roots, not nested operands.
Read the included callee implementation/contract: its name, types and effect row
do not specify its behavior. Effects substitute caller buffers, including recursive
calls. Request context before calling an undisclosed dependency. Source/model
acceptance does not establish the reference's intent or native performance.""",
    "floats": """f32/f64 use -ffp-contract=off -fno-fast-math. No implicit reassociation or FMA;
floating min/max and float-to-integer casts are unsupported. Strict flags are not
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
contracts; large expansion is not a measured advantage over compact C++ templates.""",
    "memory": """buffer scratch:u64[n] = zeroed; explicitly allocates and zero-initializes heap
storage. stack scratch:u64[32] = zeroed; explicitly reserves initialized stack
storage. Elements are scalar; stack capacity is a literal, declarations total at
most 65536 bytes per function (not a bound on recursion/spills/native stack).
Bind computed heap capacity to an immutable usize first. The owner is neither
copyable nor returnable; pass its borrow to helpers. len(scratch) is metadata.
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
verification currently rejects tagged sums, even when their wrappers return integers.""",
}


def select_cards(
    source: str, has_views: bool = False, has_records: bool = False, has_sums: bool = False
) -> dict[str, str]:
    # Actual tokens prevent comments/spacing from silently choosing the curriculum.
    words = {token.s for token in lex(source)}
    selected = ["base", "integers", "calls"]
    if has_views or words & {"buffer", "stack", "len"}:
        selected.append("views")
    if "compact" in words:
        selected.append("compact")
    if words & {"f32", "f64"}:
        selected.append("floats")
    if has_records:
        selected.append("records")
    if words & {"family", "derive"}:
        selected.append("generators")
    if words & {"buffer", "stack"}:
        selected.append("memory")
    if has_sums or "match" in words:
        selected.append("sums")
    return {name: CARDS[name] for name in selected}
