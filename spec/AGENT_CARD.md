# CAIRN Native 0.4 bootstrap card

A restricted CPU language, not Rust or a complete C++ replacement. Use fn, typed
parameters, braces, semicolons, // comments, explicit return, let for immutable
locals and let mut for mutable locals. No shadowing or implicit conversions.
Parameters are immutable. for i in 0..n visits the half-open range sequentially;
bounds are evaluated once. if/else and while use braces. reg/each are compatibility
spellings, not preferred new syntax. Recursion/while may diverge.

Scalars: bool, u8/u16/u32/u64, usize (64-bit here), i32/i64, f32/f64. Literals follow
expected types, otherwise u64/f64. Integer +,-,* trap on overflow in every build.
Unsigned add_wrap/sub_wrap/mul_wrap wrap. /,% reject zero and signed minimum/-1.
shl_wrap(x,k), shr(x,k) need k:usize below the operand width. Bitwise ops unsigned;
min/max integers only. Cast with u32(x), etc.; integer narrowing checks range.
Float-to-integer conversion is not supported. No fast math or implicit FMA.

Views are parameters, not owning vectors: n:usize, x:ro<u64>[n]@host,
out:rw<u64>[n]@host. Declare an extent as an earlier usize parameter or literal.
Indexes are usize. ro views may alias; every rw view must be disjoint from every
other view in a call. The foreign caller supplies live initialized storage and
prevents concurrent conflicting access; numeric entry checks do not prove that.
No view locals, view returns, implicit copies, allocation or parallel loops.

let used = compact out for i in n where predicate yield value;
selects a stable prefix into an rw parameter of capacity exactly n and returns
its length. The unwritten tail stays unchanged. Predicate/projection cannot read
out or call a writing function. No allocation. Bind writing calls separately;
do not nest them inside other expressions. Only call disclosed dependencies.

Records, enums, static families and wire codecs exist; request their selected
cards before using them. Methods, imports/modules, owners, traits, closures,
Result, OS libraries, threads and native GPU lowering are not implemented.

Example: fn sum(n:usize,x:ro<u64>[n]@host)->u64 {
  let mut s:u64=0; for i in 0..n {s=add_wrap(s,x[i]);} return s;
}

For edits, obey the host packet. With cairn.choices/1 return only its named JSON
expression choices. The host controls scope, task and all checking. Typed is not
correct. Scalar smt-equivalent trusts the symbolic translator and Z3, excludes
memory/loops/floats, and does not prove native code. Unknown never means accepted.
Legacy Lean remains UNCHECKED. There is no trained-model performance claim.
