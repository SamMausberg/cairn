/-
The statement language of the ownership calculus: heap cells, what one lane of a
`parallel` region does to the places of its enclosing scope, the region rule, the
statements and the program.
-/
import Cairn.Places

namespace Cairn
namespace Ownership

/-! ## Syntax -/

/-- The identity of one heap cell. -/
abbrev AllocId := Nat

/-! ### What one lane does

`parallel i in n { body }` runs one lane per index `i < n`.  `checking.py:region`
abstracts the body to one list, `Lanes.accesses`: for every place of the enclosing
scope the body touches, the root local, whether the index written there is the binder
itself, and whether it writes.  `Touch` is that list, with the place kept, so that the
leases of the enclosing scope can still be asked about each access.

The things a lane cannot do are not rules here but absences: the body is a list of
accesses, so a region cannot nest, `return`, move an outer owner or run a collector,
and a local the body declares is not a place of the enclosing scope and simply does
not appear. -/

/-- One access a lane makes to a place of the enclosing scope.

* `elem r m` is `x[i]`, or `r.xs[i]`, at the lane's own index -- the only shape
  `checking.py` allows on anything lanes write (`i.tag == "name" and i.val == binder`,
  exactly that name as the whole index);
* `other r m` is every other way of naming elements: `x[j]`, `x[i + 1]`, `x[0]`, or a
  part or whole view lent on to a call.  Its footprint is modelled as EVERY element,
  the worst case, since nothing here bounds where the index lands;
* `whole r m` is the place itself -- a shared scalar read or assigned, or a single
  borrow lent on;
* `len r` is `len(x)`: the header alone, which `check_len` checks with
  `leased(..., elements = False)` and which `region` does not record at all. -/
inductive Touch where
  | elem (r : Root) (m : Mode)
  | other (r : Root) (m : Mode)
  | whole (r : Root) (m : Mode)
  | len (r : Root)
deriving DecidableEq, Repr, Inhabited

/-- The storage the access names. -/
def Touch.root : Touch → Root
  | .elem r _ => r
  | .other r _ => r
  | .whole r _ => r
  | .len r => r

/-- The mode it is touched in.  A `len` read is a read. -/
def Touch.mode : Touch → Mode
  | .elem _ m => m
  | .other _ m => m
  | .whole _ m => m
  | .len _ => Mode.ro

/-- Is the index the lane's own binder?  This is the `at_binder` flag
`checking.py:e_index` records. -/
def Touch.atBinder : Touch → Bool
  | .elem _ _ => true
  | _ => false

/-- Does `checking.py:region` see this access at all?  `e_index` and `lend` record;
`len` does not, and needs no rule, since the header is not an element. -/
def Touch.recorded : Touch → Bool
  | .len _ => false
  | _ => true

/-- The place the region's lease check names: what `checking.py:where` writes, where
any index of `x` is `x[]`, whatever the index is. -/
def Touch.lease : Touch → Borrow
  | .elem r m => (.elems r, m)
  | .other r m => (.elems r, m)
  | .whole r m => (.whole r, m)
  | .len r => (.hdr r, Mode.ro)

/-- What the lane with index `k` really touches: its own element where the binder is
the index, and the worst case everywhere else. -/
def Touch.borrow (k : Nat) : Touch → Borrow
  | .elem r m => (.part r (.lit k) (.lit (k + 1)), m)
  | .other r m => (.elems r, m)
  | .whole r m => (.whole r, m)
  | .len r => (.hdr r, Mode.ro)

/-- Some access of the body writes this local.  `checking.py` calls this set
`written`, and keys it on the root local name, not on the field path. -/
def writesVar (body : List Touch) (x : Var) : Bool :=
  body.any fun c => c.mode == Mode.rw && c.root.var == x

/-- **The region rule.**  Whatever any lane writes may be touched, by any lane, only
at the lane's own index: `checking.py:region` computes `written` and raises
`E-PARALLEL-RACE` for every recorded access to a written local that is not `x[i]`.
Assigning a shared scalar of the enclosing scope is the case reported as
`E-PARALLEL-WRITE`; it fails here too, because such an access writes its local and is
not at the binder. -/
def laneRule (body : List Touch) : Bool :=
  body.all fun a => !a.recorded || !writesVar body a.root.var || a.atBinder

/-- Statements.  `call`/`spawn` take a borrow list rather than a callee: the
callee's body is abstracted to the footprint it was handed, which is all the
ownership and lease rules ever look at.  A read of a scalar is
`call [(whole x, ro)]`, a write of an array part is `call [(part x lo hi, rw)]`,
and `let k = len(x);` is `call [(hdr x, ro)]`. -/
inductive Stmt where
  /-- `let x = Buf[T](n);` -- a fresh heap cell lands in `x`. -/
  | alloc (x : Var)
  /-- `let x = 0;` -- a copyable scalar lands in `x`. -/
  | mkScalar (x : Var)
  /-- `let y = x;` for a copyable `x`: the bits are duplicated. -/
  | copy (y x : Var)
  /-- `let y = x;` for an owner `x`: the cell moves and `x` is dead afterwards. -/
  | move (y x : Var)
  /-- The implicit release of the owner in `x` at the exit of an inner scope. -/
  | drop (x : Var)
  /-- `f(borrows...)`: the places are lent for the duration of the call. -/
  | call (args : List Borrow)
  /-- `let t = spawn f(borrows...);`: the places stay lent until `wait t`. -/
  | spawn (t : Ticket) (args : List Borrow)
  /-- `wait(t)` or `wait(g)`: the only thing that returns a task's borrows.  On a group it
  joins every task still running and consumes the group. -/
  | wait (t : Ticket)
  /-- `if c { thn } else { els }`: the condition is opaque, so both branches are
  always reachable and the join is what makes a one-sided move dead. -/
  | ite (thn els : List Stmt)
  /-- `parallel i in n { body }`: one lane per index below `n`, all of them at once,
  and the spawner blocked until every one is done.  `n` is read from the valuation,
  exactly as a part bound is. -/
  | parallel (n : Bound) (body : List Touch)
  /-- `let g = Group[T](n);`: an empty group of at most `n` tasks in flight.  Like a ticket
  it is linear, and only `wait(g)` consumes it. -/
  | group (g : Ticket) (n : Bound)
  /-- `spawn f(borrows...) into g;`: a task of the group `g`.  The places stay lent to `g`
  until `wait(g)`, whichever task finishes first. -/
  | submit (g : Ticket) (args : List Borrow)
  /-- `collect(g)`: joins one finished task of `g`.  The checker cannot know which one, so
  its leases stay with the group. -/
  | collect (g : Ticket)
deriving Repr, Inhabited

/-- Everything but `if` is checked by its guard and continues in its effect. -/
def Stmt.isIte : Stmt → Bool
  | .ite _ _ => true
  | _ => false

/-- A scope: the locals it declares and the statements it runs.  Every owner still
held by one of `scope`'s locals is released when the body falls off the end. -/
structure Program where
  scope : List Var
  body : List Stmt
deriving Repr, Inhabited

end Ownership
end Cairn
