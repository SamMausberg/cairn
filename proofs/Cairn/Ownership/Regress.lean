/-
The regression: the Lean encodings of the programs the Python tests pin, classified as
the Python checker classifies them, and the fault witnesses that show the machine can
reach every fault the rules exist to prevent.
-/
import Cairn.Ownership.Soundness

namespace Cairn
namespace Ownership

/-! ## Regression: the checker on the programs the Python tests pin

Local 0 is `data`, 1 and 2 are further locals, 3 is a second buffer, 4 is a record
with the fields 0, 1 and 2 (`box.a`, `box.b`, `box.xs`); tickets are 0, 1 and 2; the
bound names 0, 1 and 2 are the immutable `a`, `b` and `n` of the source programs.
Each line names the CAIRN program in `tests/soundness/test_soundness.py` or
`tests/soundness/test_concurrency.py` that it encodes, and every classification below was
re-checked against the Python checker on that source. -/

namespace Regress

open Stmt

/-- The immutable `a`, `b` and `n` of the part programs. -/
def a : Bound := .nm 0
def b : Bound := .nm 1
def n : Bound := .nm 2

/-- The roots the programs name: three plain locals, a second buffer, and a record
local with three fields. -/
def data : Root := ⟨0, []⟩
def snd : Root := ⟨1, []⟩
def thd : Root := ⟨2, []⟩
def other : Root := ⟨3, []⟩
def box : Root := ⟨4, []⟩
def boxA : Root := ⟨4, [0]⟩
def boxB : Root := ⟨4, [1]⟩
def boxXs : Root := ⟨4, [2]⟩

/-- `let mut b = Buf[u64](n); let y = b;` -- accepted. -/
def moveOnce : Program := ⟨[0, 1], [alloc 0, move 1 0]⟩

/-- `let x = eat(a); let y = eat(a);` -- E-MOVED, "used twice as if it were a copy". -/
def doubleMove : Program := ⟨[0, 1, 2], [alloc 0, move 1 0, move 2 0]⟩

/-- `let t = spawn bump(c, 4); let seen = c;` -- E-LEASED. -/
def leasedRead : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.whole data, Mode.rw)], call [(.whole data, Mode.ro)], wait 0]⟩

/-- `let t = spawn sum(len(data), data); let x = data[0]; ...` -- accepted:
read-only lending is shared freely. -/
def sharedRead : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.elems data, Mode.ro)], call [(.elems data, Mode.ro)], wait 0]⟩

/-- `let t = spawn sum(len(data), data); let moved = data;` -- E-LEASED:
a move beside a view the task still holds. -/
def moveBesideView : Program :=
  ⟨[0, 1], [alloc 0, spawn 0 [(.elems data, Mode.ro)], move 1 0, wait 0]⟩

/-- `let t = spawn sum(len(data), data); return 0;` -- E-LINEAR-LEAK. -/
def unawaitedTicket : Program := ⟨[0], [alloc 0, spawn 0 [(.elems data, Mode.rw)]]⟩

/-- `f(len(b), b, b)` -- E-ALIAS: one call writing and reading the same place. -/
def aliasedCall : Program :=
  ⟨[0], [alloc 0, call [(.elems data, Mode.rw), (.elems data, Mode.ro)]]⟩

/-- `if ... { let y = b; } use(b);` -- the join kills a place moved on one path. -/
def movedOnOnePath : Program :=
  ⟨[0, 1], [alloc 0, ite [move 1 0] [], call [(.whole data, Mode.ro)]]⟩

/-- Moving on both paths is fine, and the target is live after the join. -/
def movedOnBothPaths : Program :=
  ⟨[0, 1], [alloc 0, ite [move 1 0] [move 1 0], call [(.whole snd, Mode.ro)]]⟩

/-- `let t = spawn sum(...); if n == 8 { wait(t); }` -- E-LINEAR-BRANCH:
the branches disagree about which tickets are live. -/
def ticketsDisagree : Program :=
  ⟨[0], [alloc 0, ite [spawn 0 [(.elems data, Mode.ro)]] [], wait 0]⟩

/-- Two tasks writing two whole buffers -- accepted. -/
def twoOwners : Program :=
  ⟨[0, 3], [alloc 0, alloc 3, spawn 0 [(.elems data, Mode.rw)],
            spawn 1 [(.elems other, Mode.rw)], wait 0, wait 1]⟩

/-- `let a = spawn fill(mid, data[0..mid], 0); let b = spawn fill(n - mid, data[mid..n], 7);`
-- accepted: two parts of one buffer that visibly meet at `mid`
(`test_visibly_disjoint_parts_with_stable_bounds_are_still_lent_together`). -/
def disjointSplit : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data (.lit 0) a, Mode.rw)],
         spawn 1 [(.part data a n, Mode.rw)], wait 0, wait 1]⟩

/-- The K-way split `checking.py:overlaps` licenses: `d[0..a]`, `d[a..b]`, `d[b..n]`
are pairwise disjoint, the last pair only by chaining `a <= b` through the middle
part.  Accepted. -/
def kwaySplit : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data (.lit 0) a, Mode.rw)],
         spawn 1 [(.part data a b, Mode.rw)], spawn 2 [(.part data b n, Mode.rw)],
         wait 0, wait 1, wait 2]⟩

/-- The same two ends with nothing lent between them to order their bounds -- E-LEASED
(`tests/soundness/test_soundness.py`: "two parts with nothing lent between them to order their
bounds"). -/
def partsWithoutMiddle : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data (.lit 0) a, Mode.rw)],
         spawn 1 [(.part data b n, Mode.rw)], wait 0, wait 1]⟩

/-- Two parts of one array that really overlap: `d[0..6]` and `d[3..9]` -- E-LEASED. -/
def overlappingParts : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data (.lit 0) (.lit 6), Mode.rw)],
         spawn 1 [(.part data (.lit 3) (.lit 9), Mode.rw)], wait 0, wait 1]⟩

/-- One call handed two parts that really overlap -- E-ALIAS
(`transfer(a[0..4], a[2..6])`). -/
def overlappingArgs : Program :=
  ⟨[0], [alloc 0, call [(.part data (.lit 0) (.lit 4), Mode.rw),
                        (.part data (.lit 2) (.lit 6), Mode.ro)]]⟩

/-- **The backwards part.**  `d[a..b]` is lent first and orders `d[0..a]` before
`d[b..n]`, which overlap whenever `a > b`.  The checker ACCEPTS this, exactly as
`checking.py` does, and it is safe only because forming `d[a..b]` runs the
`lo <= hi` guard on this thread before either overlapping task exists
(`test_a_backwards_part_used_to_order_two_others_aborts_at_its_spawn`). -/
def backwardsPart : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data a b, Mode.rw)],
         spawn 1 [(.part data (.lit 0) a, Mode.rw)], spawn 2 [(.part data b n, Mode.rw)],
         wait 0, wait 1, wait 2]⟩

/-- `let t = spawn fill(len(d), d, 1); let k = len(d);` -- accepted: the task holds
the elements, and the owner's length is not one of them.  The same program encodes
`let mut m:usize = 4; let t = spawn fill(m, d[0..m], 1); let k = len(d);`, whose part carries a
mutable bound and is therefore mapped to `elems` too; `checking.py` accepts that source, because
`leased(d, "ro", elements = False)` skips every held place whose string carries a `[`. -/
def lenUnderElementLease : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.elems data, Mode.rw)], call [(.hdr data, Mode.ro)], wait 0]⟩

/-- `let t = spawn repl(b, 10); let k = len(b);` -- E-LEASED: the task holds the
owner itself and may `swap` the cell, so the length can change under the read. -/
def lenUnderOwnerLease : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.whole data, Mode.rw)], call [(.hdr data, Mode.ro)], wait 0]⟩

/-- Two tasks writing one whole place -- E-LEASED. -/
def overlappingTasks : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.whole data, Mode.rw)], spawn 1 [(.whole data, Mode.rw)],
         wait 0, wait 1]⟩

/-- Copying an owner is not a copy: rejected, because `copy` needs a scalar. -/
def copyAnOwner : Program := ⟨[0, 1], [alloc 0, copy 1 0]⟩

/-- Copying a scalar is fine. -/
def copyAScalar : Program := ⟨[0, 1], [mkScalar 0, copy 1 0]⟩

/-- Using a place after its implicit release -- E-MOVED. -/
def useAfterDrop : Program := ⟨[0], [alloc 0, drop 0, call [(.whole data, Mode.ro)]]⟩

/-- A local the scope never declared. -/
def outOfScope : Program := ⟨[1], [alloc 0]⟩

/-! ### The shapes `path` writes with a `?`

A single element `a[i]`, a part of a part `a[lo..hi][j..k]` and a part one of whose bounds can
change are `"a[]"`, `"a[?..?]"` and `"a[?..3]"` to `checking.py:path`, and every one of them is
modelled as `elems` (`Places.lean`, "The `?` shapes, case by case").  These programs pin that
the classification the model gives is the one `checking.py` gives the CAIRN source beside it.
Each was run through `python bin/cairn check`, and each is pinned on the Python side in
`tests/soundness/test_soundness.py`.  `fill` is
`fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }` and
`two` is `fn two(n:usize, a:rw<u64>[n], m:usize, b:rw<u64>[m]) { a[0] = 1; b[0] = 2; }`. -/

/-- `let a:usize = 4; let t = spawn fill(a, d[0..a], 1); let v = d[6];` -- E-LEASED.  The read is
`d[]`, which overlaps every part of `d`, so it is refused even though index 6 lies outside the
part the task holds.  Conservative, and what `checking.py` does. -/
def elemUnderPartLease : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data (.lit 0) a, Mode.rw)], call [(.elems data, Mode.ro)], wait 0]⟩

/-- `let t = spawn fill(a, e[0..a], 1); let v = d[6];` -- accepted.  The same element read
while the task holds a part of ANOTHER buffer: two locals never touch, whatever the index
would be. -/
def elemBesideOtherPart : Program :=
  ⟨[0, 3], [alloc 0, alloc 3, spawn 0 [(.part other (.lit 0) a, Mode.rw)],
            call [(.elems data, Mode.ro)], wait 0]⟩

/-- `let mut m:usize = 4; let t1 = spawn fill(m, d[0..m], 1); let t2 = spawn fill(n - m, d[m..n], 7);`
-- E-LEASED.  `m` is mutable, so `path` writes `d[0..?]` and `d[?..n]`, neither part orders the
other, and both become `elems` here.  With an immutable `m` the same source is `disjointSplit`,
which both checkers accept: the rejection is the mutability, not the arithmetic. -/
def mutableBoundParts : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.elems data, Mode.rw)], spawn 1 [(.elems data, Mode.rw)], wait 0, wait 1]⟩

/-- `two(2, d[0..8][0..2], 8, d[8..16])` -- E-ALIAS.  A part of a part sits somewhere inside the
outer part and `path` writes `d[?..?]`, so it overlaps the part beside it although the two
really are disjoint. -/
def partOfPartBesidePart : Program :=
  ⟨[0], [alloc 0, call [(.elems data, Mode.rw), (.part data (.lit 8) (.lit 16), Mode.rw)]]⟩

/-! ### Fields

`checking.py:path` writes a field path `r.a`, and `overlaps` compares two bases by
dotted prefix: two fields of one record are apart, a field and its record are not,
and a part of an array held in a field chains exactly as one of a plain local.

Reaching a field reads THAT FIELD's header, not the record it sits in:
`checking.py:e_field` suppresses the whole-local read its base would perform and runs
`leased(box.a, "ro", elements = False)` at the outermost field of the path.  So two
tasks may hold two different fields of one record, and the header of a field stays
readable while a task holds that field's elements.  A lease of the record itself still
covers every field inside it, because `whole box` overlaps every place rooted in `box`,
and a lease of one field still blocks a move of the record for the same reason.
`reachField` below is that read, written out. -/

/-- The header read that reaching `box.a` performs: the field's own, not the record's. -/
def reachField (r : Root) : Stmt := call [(.hdr r, Mode.ro)]

/-- `let t = spawn fill(len(box.a), box.a, 0); wait(t);` -- accepted. -/
def oneFieldToATask : Program :=
  ⟨[4], [alloc 4, reachField boxA, spawn 0 [(.elems boxA, Mode.rw)], wait 0]⟩

/-- `let l = spawn fill(..., box.a, 0); let r = spawn fill(..., box.b, 1);` --
accepted: two fields of one record are disjoint storage, and reaching `box.b` reads
the header of `box.b` alone, which the lease of `box.a`'s elements does not cover. -/
def fieldsToTwoTasks : Program :=
  ⟨[4], [alloc 4, reachField boxA, spawn 0 [(.elems boxA, Mode.rw)],
         reachField boxB, spawn 1 [(.elems boxB, Mode.rw)], wait 0, wait 1]⟩

/-- `let l = spawn fill(..., box.a, 0); let r = spawn fill(..., box.a, 1);` --
E-LEASED: one field lent twice is one piece of storage lent twice. -/
def sameFieldToTwoTasks : Program :=
  ⟨[4], [alloc 4, reachField boxA, spawn 0 [(.elems boxA, Mode.rw)],
         reachField boxA, spawn 1 [(.elems boxA, Mode.rw)], wait 0, wait 1]⟩

/-- `let t = spawn fill(..., box.a, 0); box.a = Buf[u64](2);` -- E-LEASED: assignment
replaces the cell the task's view lives in, so the place it names is `whole box.a`,
which overlaps the elements the task holds. -/
def fieldAssignUnderElementLease : Program :=
  ⟨[4], [alloc 4, reachField boxA, spawn 0 [(.elems boxA, Mode.rw)],
         call [(.whole boxA, Mode.rw)], wait 0]⟩

/-- `both(6, box.xs[0..6], box.a[3..9])` -- accepted: two fields really are disjoint,
whatever their bounds. -/
def partsOfTwoFieldsInOneCall : Program :=
  ⟨[4], [alloc 4, reachField boxXs, reachField boxA,
         call [(.part boxXs (.lit 0) (.lit 6), Mode.rw),
               (.part boxA (.lit 3) (.lit 9), Mode.ro)]]⟩

/-- `both(4, box.xs[0..4], box.xs[4..8])` -- accepted: the part chain works inside a
field exactly as it does on a local. -/
def fieldPartsSplitInOneCall : Program :=
  ⟨[4], [alloc 4, reachField boxXs, call [(.part boxXs (.lit 0) (.lit 4), Mode.rw),
                                          (.part boxXs (.lit 4) (.lit 8), Mode.ro)]]⟩

/-- `both(6, box.xs[0..6], box.xs[3..9])` -- E-ALIAS: they really overlap. -/
def fieldPartsOverlapInOneCall : Program :=
  ⟨[4], [alloc 4, reachField boxXs, call [(.part boxXs (.lit 0) (.lit 6), Mode.rw),
                                          (.part boxXs (.lit 3) (.lit 9), Mode.ro)]]⟩

/-- `both(len(box.a), box.a, box.a)` -- E-ALIAS: one field is not two. -/
def sameFieldTwiceInOneCall : Program :=
  ⟨[4], [alloc 4, reachField boxA, call [(.elems boxA, Mode.rw), (.elems boxA, Mode.ro)]]⟩

/-- `let t = spawn fill(..., box.a, 0); let moved = box;` -- E-LEASED: a field is
inside its record, so a lease on the field pins the record. -/
def fieldMoveUnderLease : Program :=
  ⟨[1, 4], [alloc 4, reachField boxA, spawn 0 [(.elems boxA, Mode.rw)], move 1 4, wait 0]⟩

/-- `let t = spawn bump(box); let s = sum(len(box.a), box.a);` -- E-LEASED the other
way: the record is lent whole, so every field of it is. -/
def fieldReadUnderRecordLease : Program :=
  ⟨[4], [alloc 4, spawn 0 [(.whole box, Mode.rw)], reachField boxA,
         call [(.elems boxA, Mode.ro)], wait 0]⟩

/-- `let t = spawn bump(box); let k = len(box.xs);` -- E-LEASED: reaching the field
reads its header, and the task may replace the whole record under it. -/
def lenOfFieldUnderRecordLease : Program :=
  ⟨[4], [alloc 4, spawn 0 [(.whole box, Mode.rw)], reachField boxXs,
         call [(.hdr boxXs, Mode.ro)], wait 0]⟩

/-- `let t = spawn fill(len(box.xs), box.xs, 1); let k = len(box.xs);` -- accepted:
the header of a field is not one of its elements, and reaching the field no longer
reads the record. -/
def lenOfFieldUnderElementLease : Program :=
  ⟨[4], [alloc 4, reachField boxXs, spawn 0 [(.elems boxXs, Mode.rw)], reachField boxXs,
         call [(.hdr boxXs, Mode.ro)], wait 0]⟩

/-! ### Parallel regions

`checking.py:region` records one triple per access -- root local, at the binder,
writes -- and refuses any recorded access to a written local that is not at the
binder.  The bodies below are the lane bodies of `tests/soundness/test_concurrency.py`. -/

/-- `parallel i in n { out[i] = x[i]; }` -- accepted: the saxpy shape. -/
def laneMap : Program :=
  ⟨[0, 1], [alloc 0, alloc 1, parallel n [.elem data Mode.rw, .elem snd Mode.ro]]⟩

/-- `parallel i in n { out[i] = k; }` for a shared scalar `k` -- accepted: lanes read
what the enclosing scope holds. -/
def laneReadsShared : Program :=
  ⟨[0, 2], [alloc 0, mkScalar 2, parallel n [.elem data Mode.rw, .whole thd Mode.ro]]⟩

/-- `parallel i in n { let m = len(out); out[i] = m; }` -- accepted: `len` is the
header, which no lane's element touches, and `region` does not record it. -/
def laneLenAndWrite : Program :=
  ⟨[0], [alloc 0, parallel n [.len data, .elem data Mode.rw]]⟩

/-- `parallel i in n { out[0] = 1; }`, and equally `parallel i in n { poke(n, out); }`
-- E-PARALLEL-RACE: every lane writes the same element. -/
def laneWritesFixedIndex : Program :=
  ⟨[0], [alloc 0, parallel n [.other data Mode.rw]]⟩

/-- `parallel i in n { total = total + out[i]; }` -- E-PARALLEL-WRITE: a shared
scalar of the enclosing scope cannot be assigned. -/
def laneWritesShared : Program :=
  ⟨[0, 2], [alloc 0, mkScalar 2, parallel n [.whole thd Mode.rw, .elem data Mode.ro]]⟩

/-- `parallel i in n { out[i] = out[0]; }` -- E-PARALLEL-RACE: a lane reads at an
index that is not its own, of an array the lanes write. -/
def laneReadsOther : Program :=
  ⟨[0], [alloc 0, parallel n [.elem data Mode.rw, .other data Mode.ro]]⟩

/-- A region beside a live task that holds another buffer -- accepted. -/
def laneBesideTask : Program :=
  ⟨[0, 3], [alloc 0, alloc 3, spawn 0 [(.elems other, Mode.rw)],
            parallel n [.elem data Mode.rw], wait 0]⟩

/-- A region touching what a live task writes -- E-LEASED: a lane is checked against
the leases of the enclosing scope like any other access. -/
def laneUnderLease : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.elems data, Mode.rw)],
         parallel n [.elem data Mode.ro], wait 0]⟩

/-- The same against a *part* a live task holds -- also E-LEASED, because the lease
check names `x[]` for any index, whatever the lane's own index would be.  That is
conservative, and it is what `checking.py:where` does. -/
def laneUnderPartLease : Program :=
  ⟨[0], [alloc 0, spawn 0 [(.part data (.lit 0) a, Mode.rw)],
         parallel n [.elem data Mode.rw], wait 0]⟩

/-- Two regions, one after the other, over one buffer -- accepted: a region completes
before the next statement. -/
def twoRegions : Program :=
  ⟨[0], [alloc 0, parallel n [.elem data Mode.rw], parallel n [.elem data Mode.rw]]⟩

/-- Every line of the regression: the program, and whether the Python checker accepts
the CAIRN source it encodes. -/
def lines : List (String × Bool × Program) :=
  [("moveOnce", true, moveOnce),
   ("doubleMove", false, doubleMove),
   ("leasedRead", false, leasedRead),
   ("sharedRead", true, sharedRead),
   ("moveBesideView", false, moveBesideView),
   ("unawaitedTicket", false, unawaitedTicket),
   ("aliasedCall", false, aliasedCall),
   ("movedOnOnePath", false, movedOnOnePath),
   ("movedOnBothPaths", true, movedOnBothPaths),
   ("ticketsDisagree", false, ticketsDisagree),
   ("twoOwners", true, twoOwners),
   ("disjointSplit", true, disjointSplit),
   ("kwaySplit", true, kwaySplit),
   ("partsWithoutMiddle", false, partsWithoutMiddle),
   ("overlappingParts", false, overlappingParts),
   ("overlappingArgs", false, overlappingArgs),
   ("backwardsPart", true, backwardsPart),
   ("lenUnderElementLease", true, lenUnderElementLease),
   ("lenUnderOwnerLease", false, lenUnderOwnerLease),
   ("overlappingTasks", false, overlappingTasks),
   ("copyAnOwner", false, copyAnOwner),
   ("copyAScalar", true, copyAScalar),
   ("useAfterDrop", false, useAfterDrop),
   ("outOfScope", false, outOfScope),
   ("elemUnderPartLease", false, elemUnderPartLease),
   ("elemBesideOtherPart", true, elemBesideOtherPart),
   ("mutableBoundParts", false, mutableBoundParts),
   ("partOfPartBesidePart", false, partOfPartBesidePart),
   ("oneFieldToATask", true, oneFieldToATask),
   ("fieldsToTwoTasks", true, fieldsToTwoTasks),
   ("sameFieldToTwoTasks", false, sameFieldToTwoTasks),
   ("fieldAssignUnderElementLease", false, fieldAssignUnderElementLease),
   ("partsOfTwoFieldsInOneCall", true, partsOfTwoFieldsInOneCall),
   ("fieldPartsSplitInOneCall", true, fieldPartsSplitInOneCall),
   ("fieldPartsOverlapInOneCall", false, fieldPartsOverlapInOneCall),
   ("sameFieldTwiceInOneCall", false, sameFieldTwiceInOneCall),
   ("fieldMoveUnderLease", false, fieldMoveUnderLease),
   ("fieldReadUnderRecordLease", false, fieldReadUnderRecordLease),
   ("lenOfFieldUnderElementLease", true, lenOfFieldUnderElementLease),
   ("lenOfFieldUnderRecordLease", false, lenOfFieldUnderRecordLease),
   ("laneMap", true, laneMap),
   ("laneReadsShared", true, laneReadsShared),
   ("laneLenAndWrite", true, laneLenAndWrite),
   ("laneWritesFixedIndex", false, laneWritesFixedIndex),
   ("laneWritesShared", false, laneWritesShared),
   ("laneReadsOther", false, laneReadsOther),
   ("laneBesideTask", true, laneBesideTask),
   ("laneUnderLease", false, laneUnderLease),
   ("laneUnderPartLease", false, laneUnderPartLease),
   ("twoRegions", true, twoRegions)]

/-- The lines this checker classifies differently from the Python checker. -/
def failures : List String :=
  lines.filterMap fun (name, expected, p) => if accepts p == expected then none else some name

/-- Every line of the regression, as one Boolean the build can print. -/
def report : Bool := failures.isEmpty

/-- What the build prints, so the Python gate can assert on it, naming any line that
fails. -/
def line : String :=
  if report then "ownership-regression: pass"
  else "ownership-regression: FAIL " ++ String.intercalate ", " failures

/-! ### The faults are reachable

Every theorem above has the shape "accepted implies no reachable fault", which
would be vacuous if the machine could never fault at all.  `report` rules out the
other vacuity -- the checker is not simply always `false`.  These witnesses rule
out this one: each names a program the checker REJECTS and exhibits the step
sequence that drives the machine into the fault the rule exists to prevent.  The
proofs are the raw `Reach` derivations; `List.Mem.head`/`.tail` pick which
successor of the computed `succ` list the execution takes, so nothing is hidden
behind a tactic. -/

/-- Any valuation will do where the bounds are literals. -/
def anyVal : Valuation := fun _ => 0

/-- `a = 6`, `b = 3`, `n = 9`: the valuation `backwardsPart` is written for, under
which `d[a..b]` is backwards and `d[0..a]` and `d[b..n]` overlap. -/
def badVal : Valuation := fun v => if v = 0 then 6 else if v = 1 then 3 else 9

/-- `n = 2`: two lanes, the smallest region in which lanes can race at all. -/
def twoLanes : Valuation := fun _ => 2

/-- **A data race is reachable.**  The spawner reads a place a live task writes:
`let t = spawn bump(c, 4); let seen = c;` in `tests/soundness/test_soundness.py`. -/
theorem leasedRead_races :
    Reach anyVal leasedRead.scope (Cfg.start leasedRead) (Cfg.err (Err.race 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- **A data race is reachable** the other way: a second task would write what a
live task already writes, so spawning it is the race. -/
theorem overlappingTasks_races :
    Reach anyVal overlappingTasks.scope (Cfg.start overlappingTasks) (Cfg.err (Err.race 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- **Two overlapping parts really race.**  `d[0..6]` and `d[3..9]` share the
indices 3, 4 and 5, so the second spawn is a race under every valuation -- the
checker rejects the program, and this is what it would cost to accept it. -/
theorem overlappingParts_races :
    Reach anyVal overlappingParts.scope (Cfg.start overlappingParts) (Cfg.err (Err.race 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- **Lending one field to two tasks really races.**  Two fields of one record are
disjoint, so the checker accepts `box.a` and `box.b` going to two tasks; the SAME
field twice is one piece of storage twice, and the second spawn is the race.  The
header read that reaching `box.a` performs is not the race -- it steps through -- and
the fault arrives at the spawn itself. -/
theorem sameFieldToTwoTasks_races :
    Reach anyVal sameFieldToTwoTasks.scope (Cfg.start sameFieldToTwoTasks) (Cfg.err (Err.race 4)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _)
        (Reach.step (List.Mem.head _)
          (Reach.step (List.Mem.head _) (Reach.refl _)))))

/-- **Two overlapping parts of one field alias.**  `box.xs[0..6]` and `box.xs[3..9]`
handed to one call reach the `AliasedArgs` configuration. -/
theorem fieldPartsOverlapInOneCall_aliases :
    Reach anyVal fieldPartsOverlapInOneCall.scope (Cfg.start fieldPartsOverlapInOneCall)
      (Cfg.err Err.aliasedArgs) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- **The backwards part traps, and does not race.**  `backwardsPart` is ACCEPTED,
and under the valuation it is written for the machine aborts at the guard of
`d[6..3]` -- on the spawner's thread, before either of the two tasks that overlap
has been started.  This is the execution `tests/soundness/test_soundness.py` observes as
SIGABRT under ThreadSanitizer. -/
theorem backwardsPart_traps :
    Reach badVal backwardsPart.scope (Cfg.start backwardsPart) Cfg.trap :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _) (Reach.refl _))

/-- **Two lanes writing one element really race.**  `parallel i in n { out[0] = 1; }`
forks two lanes that both hold every element of `out` for writing; the first lane to
step finds the other one there. -/
theorem laneWritesFixedIndex_races :
    Reach twoLanes laneWritesFixedIndex.scope (Cfg.start laneWritesFixedIndex)
      (Cfg.err (Err.race 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.tail _ (List.Mem.head _)) (Reach.refl _)))

/-- **A lane writing a shared scalar really races.**  Every lane of
`parallel i in n { total = total + out[i]; }` holds `total` for writing. -/
theorem laneWritesShared_races :
    Reach twoLanes laneWritesShared.scope (Cfg.start laneWritesShared) (Cfg.err (Err.race 2)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _)
        (Reach.step (List.Mem.tail _ (List.Mem.head _)) (Reach.refl _))))

/-- **A lane reading at another index really races** with the lane that writes there:
lane 0 holds every element of `out` to read, lane 1 holds element 1 to write. -/
theorem laneReadsOther_races :
    Reach twoLanes laneReadsOther.scope (Cfg.start laneReadsOther) (Cfg.err (Err.race 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.tail _ (List.Mem.head _)) (Reach.refl _)))

/-- **A double free is reachable.**  Copying an owner duplicates the cell, and the
implicit release at scope exit then frees it twice. -/
theorem copyAnOwner_doubleFrees :
    Reach anyVal copyAnOwner.scope (Cfg.start copyAnOwner) (Cfg.err (Err.doubleFree 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- **A use after the cell is gone is reachable.**  Touching a place after its
implicit release is exactly what the moved set forbids. -/
theorem useAfterDrop_usesDeadPlace :
    Reach anyVal useAfterDrop.scope (Cfg.start useAfterDrop) (Cfg.err (Err.useAfterMove 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- **A leaked ticket is reachable.**  The scope ends while a task is still
running: `let t = spawn sum(len(data), data); return 0;`. -/
theorem unawaitedTicket_leaks :
    Reach anyVal unawaitedTicket.scope (Cfg.start unawaitedTicket) (Cfg.err (Err.leak 0)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _) (Reach.refl _)))

/-- None of the fault witnesses above is about an accepted program: each is
rejected, which is what makes them consistent with the soundness theorems.  The one
exception, `backwardsPart`, IS accepted -- and it reaches a trap, not a fault. -/
theorem witnesses_are_rejected :
    (accepts leasedRead || accepts overlappingTasks || accepts overlappingParts
      || accepts copyAnOwner || accepts useAfterDrop || accepts unawaitedTicket
      || accepts sameFieldToTwoTasks || accepts fieldPartsOverlapInOneCall
      || accepts laneWritesFixedIndex
      || accepts laneWritesShared || accepts laneReadsOther) = false
      ∧ accepts backwardsPart = true := by
  constructor <;> decide

end Regress

/-- The Lean encodings of the pinned CAIRN programs are classified exactly as the
Python checker classifies them: no line of the regression fails. -/
theorem ownership_regression : Regress.report = true := by decide

/-! ### Two rules, two chains -/

/-- The two rules chain through different facts, exactly as `checking.py` does.  The
lease check (`leased`) may use the bounds of every part the live tasks hold; the
argument check (`disjoint`) may use only the bounds of the argument list it is
looking at.  So handing ONE call the two ends of a split is rejected even while the
middle part is lent to a live task -- there is no fact in that argument list to
order `a` before `b` -- although spawning the same two ends one at a time is
accepted.  This is conservative, not unsound, and it is what the Python rule does. -/
example : accepts ⟨[0], [Stmt.alloc 0,
    Stmt.spawn 0 [(.part Regress.data Regress.a Regress.b, Mode.rw)],
    Stmt.call [(.part Regress.data (.lit 0) Regress.a, Mode.rw),
               (.part Regress.data Regress.b Regress.n, Mode.rw)],
    Stmt.wait 0]⟩ = false := by decide

example : accepts ⟨[0], [Stmt.alloc 0,
    Stmt.spawn 0 [(.part Regress.data Regress.a Regress.b, Mode.rw)],
    Stmt.spawn 1 [(.part Regress.data (.lit 0) Regress.a, Mode.rw)],
    Stmt.spawn 2 [(.part Regress.data Regress.b Regress.n, Mode.rw)],
    Stmt.wait 0, Stmt.wait 1, Stmt.wait 2]⟩ = true := by decide

end Ownership
end Cairn
