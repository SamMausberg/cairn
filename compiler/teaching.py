"""Versioned semantic contrast cards. These teach differences, not new dialects."""
CARDS={
'base':'''CAIRN Native 0.4, CPU profile. Use fn, typed parameters, braces, semicolons,
let for immutable values, let mut for mutable locals, and explicit return.
Use for i in 0..n for a sequential half-open loop. Bounds are evaluated once.
if/else and while use braces. No name shadowing or implicit conversions.
reg and each are older accepted spellings; prefer let mut and for.
Unlike Rust, no implicit tail return, methods, match, lifetimes, traits, closures,
modules or allocator exist in this implemented profile. Unlike Python, ranges
are not lists, integers are fixed-width, and indentation is not syntax.
Example: fn inc(x:u64)->u64 { return add_wrap(x,1); }
Parameters never become mutable through an edit. Bind a local instead:
fn twice(x:u64)->u64 { let mut y=x; y=add_wrap(y,x); return y; }
A compiler acceptance is not a behavior proof. Preserve the task contract.''',
'integers':'''u8/u16/u32/u64, usize(64-bit), i32/i64 and bool are scalar types.
Ordinary +,-,* abort on overflow in EVERY build, not wrap or undefined behavior.
Use add_wrap/sub_wrap/mul_wrap for unsigned modular arithmetic. / and % trap
on zero and signed minimum/-1. shl_wrap(x,k) and shr(x,k) require k<bit width;
k is usize. &,|,^,~ are unsigned. min/max are integer-only.
Cast explicitly with u32(x), usize(x), etc.; integer narrowing is range-checked.
Literal types follow context; absent context, integers default to u64.
Counterexample: returning x+1 is not equivalent to add_wrap(x,1) at u64::MAX.
Do not add a trap effect merely to get a candidate accepted.''',
'views':'''Arrays are borrowed function parameters, not owned containers:
n:usize, x:ro<u64>[n]@host, out:rw<u64>[n]@host.
Declare each dynamic extent as an earlier usize parameter. Indexes are usize.
ro permits reads. rw permits reads and writes, but must not alias any other
view in that call. Read-only views may alias. No view local or view return.
Caller supplies live initialized typed memory and prevents concurrent mutation.
Runtime entry guards check null, alignment, interval overflow and mutable alias;
ordinary indexing checks bounds. A view or loop does not allocate or parallelize.
Example: fn copy(n:usize,out:rw<u64>[n]@host,x:ro<u64>[n]@host) {
  for i in 0..n { out[i]=x[i]; }
}''',
'compact':'''let used = compact out for i in n where predicate yield value;
out is a direct rw parameter with capacity exactly n. predicate is bool;
value has the output element type. Neither may read out or call a writing
function. Selected values form a stable prefix; unused tail stays unchanged.
The compiler owns the cursor and emits at most once per visited input. No
allocation, implicit synchronization or automatic parallelism is introduced.
Example: fn even(n:usize,out:rw<u64>[n]@host,x:ro<u64>[n]@host)->usize {
  let used=compact out for i in n where (x[i]&1)==0 yield x[i]; return used;
}''',
'calls':'''Calls name declared functions directly. Bind writing calls at statement
level before using their result. No arbitrary C++ or invented library names.
The checker substitutes callee read/write footprints onto caller parameters,
including recursion. Recursive calls and while loops may diverge; no automatic
termination proof or stack bound exists. Request dependency context before
calling a function outside the packet. Type/effect signatures alone do not
specify what a function computes; read the included implementation/contract.''',
'floats':'''f32/f64 use explicit fixed formats. Use -ffp-contract=off and
-fno-fast-math. No implicit reassociation or FMA. Float-to-integer conversion
is unsupported. Floating min/max have no implicit NaN convention here.
Strict source order is not a proof of IEEE behavior across every target:
the runtime, C++ compiler and hardware remain outside a formal verification.''',
'records':'''struct Pair { x:u64; y:u64; } declares a value record.
Construct with Pair(a,b), read with p.x. Only fields of a mutable local or
writable view can change. Fields are nonrecursive previously declared value
types. Native enum tags use enum Op { read; write; } and Op.read.
No payload variants or match syntax are implemented. Record copies can cost
many instructions; a compact expression is not a constant-cost guarantee.''',
'generators':'''A static function has one [K:nat] parameter. family gain=scale[1..257];
emits named gain_1...gain_256 entries, each checked. It can increase code size.
derive wire for Packet; accepts fixed unsigned fields and generates little-
endian, declaration-order, no-padding codecs. It does not infer validation,
authentication or framing. Generated entries are not directly editable through
the current session API. Read the generator contract before changing its source.'''
}

def select_cards(source: str, has_views: bool=False, has_records: bool=False) -> dict[str,str]:
    selected=['base','integers','calls']
    if has_views:selected.append('views')
    if 'compact ' in source:selected.append('compact')
    if 'f32' in source or 'f64' in source:selected.append('floats')
    if has_records:selected.append('records')
    if 'family ' in source or 'derive wire' in source:selected.append('generators')
    return {name:CARDS[name] for name in selected}
