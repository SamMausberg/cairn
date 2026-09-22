/-
A mechanized core of CAIRN's ownership and lease discipline.

This file is a self-contained calculus: a first-order statement language over
named locals and the places borrowed out of them, an executable checker that
mirrors the rules `src/cairn/compiler/checking.py` enforces (moved set, lease map, visibly
disjoint array parts, disjoint call arguments, branch joins, no live ticket at
scope exit), and an interleaving small-step machine with explicit error states.
The theorems at the bottom say that the checker's acceptance rules out every one of
those error states -- under every valuation of the immutable part bounds -- and that
every allocation is released exactly once on normal termination.

The model is written by hand.  Nothing here is extracted from, or mechanically
connected to, the Python compiler; the verification section of `docs/MANUAL.md` states exactly
what is and is not covered.

Design notes that matter for reading the theorems:

* A *local* (`Var`) is the unit of ownership: it is allocated, moved, dropped and
  released.  A *place* (`Place`, in `Places.lean`) is what one borrow names: the
  whole owner, its header alone (`len(d)`), all its elements (`d[]`), or a part
  `d[lo..hi]` whose bounds are literals or immutable names.  Two parts of one local
  are disjoint only when one visibly ends at or before the other begins, and the
  order of two bounds may be chained through the `lo <= hi` facts of the other parts
  in play -- which is sound only because forming a slice runs that guard first.  The
  machine therefore performs the guard and TRAPS when it fails; a trap is a defined
  abort of the whole configuration, live tasks included, which is what `cr::trap()`
  does to the process.  It is neither a fault nor a race, and `Ok` holds of it.
* A task's body is abstracted to its *footprint*: while its ticket is live it may
  touch any place it was lent, in the mode it was lent, at any point between any
  two steps of the spawner, any number of times, and through the *whole owner* held
  `rw` it may replace the cell there, which is what `swap` through a lent owner
  does.  A view of the elements cannot: that is why `len(d)` stays readable while
  `d[]` is lent, and why it does not while `d` itself is.  That is the worst case
  the lease rule has to survive, so the race theorem quantifies over real
  interleavings rather than over a sequential approximation.  What a task's write
  does to a *value* is not modelled.
* The dynamic semantics is deliberately undefensive: `copy` duplicates whatever
  bits a local holds, assignment releases what it held before, and a move leaves a
  ghost mark that exists only so that a later use can be *named* an error.  Nothing
  in the machine prevents a fault; only the checker does.
-/
import Cairn.Ownership.Syntax
import Cairn.Ownership.Lanes
import Cairn.Ownership.Checker
import Cairn.Ownership.Weakening
import Cairn.Ownership.Machine
import Cairn.Ownership.Heap
import Cairn.Ownership.Invariant
import Cairn.Ownership.Preservation
import Cairn.Ownership.Soundness
import Cairn.Ownership.Regress
