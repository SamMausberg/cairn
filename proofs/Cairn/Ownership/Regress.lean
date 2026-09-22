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

/-! ### Task groups

`let g = Group[T](k);` is `group`, `spawn f(...) into g;` is `submit`, `collect(g)` is `collect`
and `wait(g)` is `wait`.  `fill` and `sum` are the helpers of `tests/soundness/test_concurrency.py`,
and each program's `len(d)` extent is the header read written out in front of its submission. -/

/-- The group. -/
def g : Ticket := 7

/-- Three visibly disjoint parts to one group, one collect, one wait: accepted. -/
def groupSplit : Program :=
  ⟨[0], [alloc 0, group g (.lit 3), submit g [(.part data (.lit 0) a, Mode.rw)],
         submit g [(.part data a b, Mode.rw)], submit g [(.part data b n, Mode.rw)], collect g, wait g]⟩

/-- `let g = Group[u64](2); return 0;`: E-LINEAR-LEAK. -/
def groupNeverWaited : Program := ⟨[], [group g (.lit 2)]⟩

/-- `if flag { wait(g); }`: E-LINEAR-BRANCH. -/
def groupWaitedOnOnePath : Program := ⟨[], [group g (.lit 2), ite [wait g] [], wait g]⟩

/-- `wait(g); let r = collect(g);`: E-MOVED. -/
def collectAfterWait : Program := ⟨[], [group g (.lit 2), wait g, collect g]⟩

/-- `wait(g); spawn tag(1) into g;`: E-MOVED. -/
def submitAfterWait : Program := ⟨[], [group g (.lit 2), wait g, submit g []]⟩

/-- `spawn fill(len(d), d, 0) into g; d[0] = 1; wait(g);`: E-LEASED. -/
def groupLeased : Program :=
  ⟨[0], [alloc 0, group g (.lit 2), call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.rw)],
         call [(.elems data, Mode.rw)], wait g]⟩

/-- The same write after a `collect(g)`: E-LEASED, since a collect returns no lease. -/
def groupWriteAfterCollect : Program :=
  ⟨[0], [alloc 0, group g (.lit 2), call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.rw)],
         collect g, call [(.elems data, Mode.rw)], wait g]⟩

/-- Two readers of one buffer, and a read beside them: accepted. -/
def groupReaders : Program :=
  ⟨[0], [alloc 0, group g (.lit 4), call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.ro)],
         call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.ro)], call [(.elems data, Mode.ro)],
         collect g, wait g]⟩

/-- `if flag { spawn fill(len(a), a, 1) into g; } else { spawn fill(len(b), b, 2) into g; }
a[0] = 7; wait(g);`: E-LEASED.  After the join the group holds what either path lent it. -/
def groupLeaseFromOnePath : Program :=
  ⟨[0, 1], [alloc 0, alloc 1, group g (.lit 1),
            ite [call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.rw)]]
                [call [(.hdr snd, Mode.ro)], submit g [(.elems snd, Mode.rw)]],
            call [(.elems data, Mode.rw)], wait g]⟩

/-- The same program with the write after `wait(g)`: accepted. -/
def groupWriteAfterWait : Program :=
  ⟨[0, 1], [alloc 0, alloc 1, group g (.lit 1),
            ite [call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.rw)]]
                [call [(.hdr snd, Mode.ro)], submit g [(.elems snd, Mode.rw)]],
            wait g, call [(.elems data, Mode.rw)]]⟩

/-- `if flag { spawn fill(b - a, d[a..b], 1) into g; } spawn fill(a, d[0..a], 2) into g;
spawn fill(n - b, d[b..n], 3) into g;`: E-LEASED.  `d[a..b]` orders the other two parts only
where it was formed, and so guarded. -/
def groupFactFromOnePath : Program :=
  ⟨[0], [alloc 0, group g (.lit 3), ite [submit g [(.part data a b, Mode.rw)]] [],
         submit g [(.part data (.lit 0) a, Mode.rw)], submit g [(.part data b n, Mode.rw)], wait g]⟩

/-- The same part lent on both paths orders the other two: accepted. -/
def groupFactFromBothPaths : Program :=
  ⟨[0], [alloc 0, group g (.lit 3),
         ite [submit g [(.part data a b, Mode.rw)]] [submit g [(.part data a b, Mode.rw)]],
         submit g [(.part data (.lit 0) a, Mode.rw)], submit g [(.part data b n, Mode.rw)], wait g]⟩

/-- A region over what a group holds: E-LEASED, like any other access. -/
def laneUnderGroup : Program :=
  ⟨[0], [alloc 0, group g (.lit 1), call [(.hdr data, Mode.ro)], submit g [(.elems data, Mode.rw)],
         parallel n [.elem data Mode.ro], wait g]⟩

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
   ("twoRegions", true, twoRegions),
   ("groupSplit", true, groupSplit),
   ("groupNeverWaited", false, groupNeverWaited),
   ("groupWaitedOnOnePath", false, groupWaitedOnOnePath),
   ("collectAfterWait", false, collectAfterWait),
   ("submitAfterWait", false, submitAfterWait),
   ("groupLeased", false, groupLeased),
   ("groupWriteAfterCollect", false, groupWriteAfterCollect),
   ("groupReaders", true, groupReaders),
   ("groupLeaseFromOnePath", false, groupLeaseFromOnePath),
   ("groupWriteAfterWait", true, groupWriteAfterWait),
   ("groupFactFromOnePath", false, groupFactFromOnePath),
   ("groupFactFromBothPaths", true, groupFactFromBothPaths),
   ("laneUnderGroup", false, laneUnderGroup)]

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

Every theorem above says that an accepted program reaches no fault, which would be vacuous if
the machine could never fault. `report` rules out a checker that rejects everything; the
witnesses below rule out a machine that never faults. Each takes a program the checker rejects
and shows an interleaving that drives the machine into the fault its rule exists to prevent.

`found` searches the successors of a configuration to a small depth and `found_reach` turns a
successful search back into a `Reach` derivation, so each witness is one `decide` over the
machine itself. -/

/-- Some run of at most `n` steps from `c` ends in a configuration `goal` accepts. -/
def found (ρ : Valuation) (scope : List Var) (goal : Cfg → Bool) : Nat → Cfg → Bool
  | 0, c => goal c
  | n + 1, c => goal c || (succ ρ scope c).any (found ρ scope goal n)

theorem found_reach {ρ : Valuation} {scope : List Var} {goal : Cfg → Bool} :
    ∀ (n : Nat) (c : Cfg), found ρ scope goal n c = true → ∃ d, Reach ρ scope c d ∧ goal d = true := by
  intro n
  induction n with
  | zero => exact fun c h => ⟨c, .refl c, h⟩
  | succ n ih =>
      intro c h
      rcases Bool.or_eq_true_iff.mp h with h | h
      · exact ⟨c, .refl c, h⟩
      · obtain ⟨b, hb, hf⟩ := List.any_eq_true.mp h
        obtain ⟨d, hr, hd⟩ := ih b hf
        exact ⟨d, .step hb hr, hd⟩

/-- The configuration is the fault `e`. -/
def faulted (e : Err) : Cfg → Bool
  | .err e' => decide (e' = e)
  | _ => false

/-- The configuration is the defined abort of a failed guard. -/
def trapped : Cfg → Bool
  | .trap => true
  | _ => false

theorem reach_fault {ρ : Valuation} {scope : List Var} {c : Cfg} {e : Err} (n : Nat)
    (h : found ρ scope (faulted e) n c = true) : Reach ρ scope c (.err e) := by
  obtain ⟨d, hr, hd⟩ := found_reach n c h
  cases d <;> simp only [faulted, Bool.false_eq_true, decide_eq_true_eq] at hd
  exact hd ▸ hr

theorem reach_trap {ρ : Valuation} {scope : List Var} {c : Cfg} (n : Nat)
    (h : found ρ scope trapped n c = true) : Reach ρ scope c .trap := by
  obtain ⟨d, hr, hd⟩ := found_reach n c h
  cases d <;> simp only [trapped, Bool.false_eq_true] at hd
  exact hr

/-- Any valuation will do where the bounds are literals. -/
def anyVal : Valuation := fun _ => 0

/-- `a = 6`, `b = 3`, `n = 9`: the valuation `backwardsPart` is written for, under which `d[a..b]`
is backwards and `d[0..a]` and `d[b..n]` overlap. -/
def badVal : Valuation := fun v => if v = 0 then 6 else if v = 1 then 3 else 9

/-- `n = 2`: two lanes, the smallest region in which lanes can race at all. -/
def twoLanes : Valuation := fun _ => 2

/-- **A data race is reachable.** The spawner reads a place a live task writes:
`let t = spawn bump(c, 4); let seen = c;` in `tests/soundness/test_soundness.py`. -/
theorem leasedRead_races :
    Reach anyVal leasedRead.scope (Cfg.start leasedRead) (Cfg.err (Err.race 0)) :=
  reach_fault 3 (by decide)

/-- A second task would write what a live task already writes, so spawning it is the race. -/
theorem overlappingTasks_races :
    Reach anyVal overlappingTasks.scope (Cfg.start overlappingTasks) (Cfg.err (Err.race 0)) :=
  reach_fault 3 (by decide)

/-- `d[0..6]` and `d[3..9]` share the indices 3, 4 and 5, so the second spawn is a race under
every valuation. -/
theorem overlappingParts_races :
    Reach anyVal overlappingParts.scope (Cfg.start overlappingParts) (Cfg.err (Err.race 0)) :=
  reach_fault 3 (by decide)

/-- Two fields of one record are disjoint, but the same field lent twice is one piece of storage
lent twice. The header read that reaching `box.a` performs steps through; the fault arrives at
the second spawn. -/
theorem sameFieldToTwoTasks_races :
    Reach anyVal sameFieldToTwoTasks.scope (Cfg.start sameFieldToTwoTasks) (Cfg.err (Err.race 4)) :=
  reach_fault 5 (by decide)

/-- `box.xs[0..6]` and `box.xs[3..9]` handed to one call reach `AliasedArgs`. -/
theorem fieldPartsOverlapInOneCall_aliases :
    Reach anyVal fieldPartsOverlapInOneCall.scope (Cfg.start fieldPartsOverlapInOneCall)
      (Cfg.err Err.aliasedArgs) :=
  reach_fault 3 (by decide)

/-- **The backwards part traps and does not race.** `backwardsPart` is accepted, and under the
valuation it is written for the machine aborts at the guard of `d[6..3]`, on the spawner's
thread, before either of the two overlapping tasks has started. `tests/soundness/test_soundness.py`
observes this execution as SIGABRT under ThreadSanitizer. -/
theorem backwardsPart_traps :
    Reach badVal backwardsPart.scope (Cfg.start backwardsPart) Cfg.trap :=
  reach_trap 2 (by decide)

/-- `parallel i in n { out[0] = 1; }` forks two lanes that both hold every element of `out` for
writing; the first lane to step finds the other one there. -/
theorem laneWritesFixedIndex_races :
    Reach twoLanes laneWritesFixedIndex.scope (Cfg.start laneWritesFixedIndex)
      (Cfg.err (Err.race 0)) :=
  reach_fault 3 (by decide)

/-- Every lane of `parallel i in n { total = total + out[i]; }` holds `total` for writing. -/
theorem laneWritesShared_races :
    Reach twoLanes laneWritesShared.scope (Cfg.start laneWritesShared) (Cfg.err (Err.race 2)) :=
  reach_fault 4 (by decide)

/-- Lane 0 holds every element of `out` to read, and lane 1 holds element 1 to write. -/
theorem laneReadsOther_races :
    Reach twoLanes laneReadsOther.scope (Cfg.start laneReadsOther) (Cfg.err (Err.race 0)) :=
  reach_fault 3 (by decide)

/-- **A double free is reachable.** Copying an owner duplicates the cell, and the implicit
release at scope exit frees it twice. -/
theorem copyAnOwner_doubleFrees :
    Reach anyVal copyAnOwner.scope (Cfg.start copyAnOwner) (Cfg.err (Err.doubleFree 0)) :=
  reach_fault 3 (by decide)

/-- **A use of a released cell is reachable.** Touching a place after its implicit release is
what the moved set forbids. -/
theorem useAfterDrop_usesDeadPlace :
    Reach anyVal useAfterDrop.scope (Cfg.start useAfterDrop) (Cfg.err (Err.useAfterMove 0)) :=
  reach_fault 3 (by decide)

/-- **A leaked ticket is reachable.** The scope ends while a task still runs:
`let t = spawn sum(len(data), data); return 0;`. -/
theorem unawaitedTicket_leaks :
    Reach anyVal unawaitedTicket.scope (Cfg.start unawaitedTicket) (Cfg.err (Err.leak 0)) :=
  reach_fault 3 (by decide)

/-- **A group never waited leaks.** -/
theorem groupNeverWaited_leaks :
    Reach anyVal groupNeverWaited.scope (Cfg.start groupNeverWaited) (Cfg.err (Err.leak g)) :=
  reach_fault 2 (by decide)

/-- **A group used after `wait(g)` is gone.** -/
theorem collectAfterWait_deadGroup :
    Reach anyVal collectAfterWait.scope (Cfg.start collectAfterWait) (Cfg.err (Err.deadGroup g)) :=
  reach_fault 3 (by decide)

/-- **A lease lent on one path only still races.** The machine takes the first branch, lends `a`
to the group, and the write after the join finds the task there. -/
theorem groupLeaseFromOnePath_races :
    Reach anyVal groupLeaseFromOnePath.scope (Cfg.start groupLeaseFromOnePath) (Cfg.err (Err.race 0)) :=
  reach_fault 7 (by decide)

/-- **A fact from one path only is not a fact.** Under `a = 6`, `b = 3`, `n = 9` the machine takes
the empty branch, so `d[6..3]` is never formed, and `d[0..6]` and `d[3..9]` overlap. -/
theorem groupFactFromOnePath_races :
    Reach badVal groupFactFromOnePath.scope (Cfg.start groupFactFromOnePath) (Cfg.err (Err.race 0)) :=
  reach_fault 5 (by decide)

/-- The group witnesses are rejected too. -/
theorem group_witnesses_are_rejected :
    (accepts groupNeverWaited || accepts collectAfterWait || accepts groupLeaseFromOnePath
      || accepts groupFactFromOnePath) = false := by decide

/-- Each fault witness above is about a program the checker rejects, which is what makes them
consistent with the soundness theorems. `backwardsPart` is accepted, and it reaches a trap. -/
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
