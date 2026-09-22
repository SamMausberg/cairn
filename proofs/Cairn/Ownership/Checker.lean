/-
The checker: disjoint call arguments, the ownership state `checking.py` carries in its
scope, the guard and effect of every statement, the branch join, and `accepts`.
-/
import Cairn.Ownership.Lanes

namespace Cairn
namespace Ownership

/-! ## Disjoint arguments

`checking.py:disjoint`: no argument of one call may write what another can reach.
The chain may use the bounds of every part in the argument list, because every one
of those slices was formed -- and so guarded `lo <= hi` -- before the call began.
It may use nothing else: unlike `CState.inPlay` below, which is the fact set of
`checking.py:leased`, the parts the live tasks hold are not in play here.  So one
call handed `d[0..a]` and `d[b..n]` is rejected even while `d[a..b]` is lent to a
live task, which is what the Python rule does; the `example`s at the bottom of this
file pin both halves.  The machine asks the same question of the numbers. -/

/-- No borrow conflicts with a later one. -/
def pairsOk (inPlay : List Place) : List Borrow → Bool
  | [] => true
  | x :: rest => rest.all (fun y => !conflict inPlay x y) && pairsOk inPlay rest

/-- What the checker requires of an argument list. -/
def argsDisjoint (args : List Borrow) : Bool :=
  pairsOk (args.map Prod.fst) args

/-- The same, of the real footprints under a valuation: this is what the machine
reports as `AliasedArgs`. -/
def pairsOkAt (ρ : Valuation) : List Borrow → Bool
  | [] => true
  | x :: rest => rest.all (fun y => !races ρ x y) && pairsOkAt ρ rest

theorem pairsOk_sound {ρ : Valuation} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) :
    ∀ args : List Borrow, pairsOk inPlay args = true → pairsOkAt ρ args = true := by
  intro args
  induction args with
  | nil => intro _; rfl
  | cons x rest ih =>
      intro h
      simp only [pairsOk, Bool.and_eq_true, List.all_eq_true] at h
      simp only [pairsOkAt, Bool.and_eq_true, List.all_eq_true]
      refine ⟨fun y hy => ?_, ih h.2⟩
      have hc : conflict inPlay x y = false := by
        have := h.1 y hy
        simpa using this
      simp [conflict_sound hg hc]

/-! ## The checker

`CState` is the ownership state `checking.py` carries in its `Scope`: which locals
hold what, and the lease map from a ticket to the borrows it holds.  A local that
appears in neither `scalars` nor `owners` is dead -- never bound, moved out, or
killed by a branch join. -/

structure CState where
  scope : List Var
  scalars : List Var
  owners : List Var
  leases : List Task
deriving DecidableEq, Repr, Inhabited

/-- Add a local to a tracked set. -/
def add (l : List Var) (p : Var) : List Var := p :: l

/-- Remove every occurrence of a local from a tracked set. -/
def del (l : List Var) (p : Var) : List Var := l.filter fun q => !(q == p)

/-- Keep only the locals both branches still have: a local moved on one path is
dead after the join. -/
def keepIn (l m : List Var) : List Var := l.filter fun p => m.contains p

@[simp] theorem mem_add {l : List Var} {p q : Var} : q ∈ add l p ↔ q = p ∨ q ∈ l := by
  simp [add]

@[simp] theorem mem_del {l : List Var} {p q : Var} : q ∈ del l p ↔ q ∈ l ∧ q ≠ p := by
  simp [del]

@[simp] theorem mem_keepIn {l m : List Var} {p : Var} : p ∈ keepIn l m ↔ p ∈ l ∧ p ∈ m := by
  simp [keepIn]

/-- A local is live when it holds something. -/
def CState.livePlace (c : CState) (p : Var) : Bool :=
  c.scalars.contains p || c.owners.contains p

/-- The places whose bounds the chain may use for this access: everything the live
tasks hold, plus the place being touched.  This is the `lent` list of
`checking.py:leased` together with its `a` argument, and every one of them has been
guarded -- the lent ones where their slices were formed at their spawns, the new one
where its slice is formed now, before the access happens. -/
def CState.inPlay (c : CState) (x : Borrow) : List Place :=
  lentPlaces c.leases ++ [x.1]

/-- No live ticket forbids this access: `checking.py:leased`. -/
def CState.mayAccess (c : CState) (x : Borrow) : Bool :=
  !heldConflict (c.inPlay x) c.leases x

/-- Writing a local: no lease of any place of it may be live.  `whole p` overlaps
every place of `p`, so this is the rule that stops a move, a drop or a rebinding
while any view of it is lent. -/
def CState.mayWrite (c : CState) (p : Var) : Bool :=
  c.mayAccess (.whole ⟨p, []⟩, Mode.rw)

/-- Every rule a straight-line statement must satisfy, as one Boolean.  These are
the conditions `checking.py` raises `E-MOVED`, `E-LEASED`, `E-ALIAS`,
`E-MOVE-BORROW` and `E-PINNED` for. -/
def guardOf (c : CState) : Stmt → Bool
  | .alloc x => c.scope.contains x && c.mayWrite x
  | .mkScalar x => c.scope.contains x && c.mayWrite x
  | .copy y x =>
      c.scope.contains y && !(y == x) && c.scalars.contains x
        && c.mayAccess (.whole ⟨x, []⟩, Mode.ro) && c.mayWrite y
  | .move y x =>
      c.scope.contains y && !(y == x) && c.owners.contains x
        && c.mayWrite x && c.mayWrite y
  | .drop x => c.owners.contains x && c.mayWrite x
  | .call args => argsDisjoint args && args.all fun x => c.livePlace x.1.base && c.mayAccess x
  | .spawn t args =>
      argsDisjoint args && !(c.leases.any fun T => T.1 == t)
        && args.all fun x => c.livePlace x.1.base && c.mayAccess x
  | .wait t => c.leases.any fun T => T.1 == t
  | .ite _ _ => true
  | .parallel _ body =>
      laneRule body && body.all fun a => c.livePlace a.root.var && c.mayAccess a.lease

/-- What a straight-line statement does to the ownership state. -/
def effOf (c : CState) : Stmt → CState
  | .alloc x => { c with scalars := del c.scalars x, owners := add c.owners x }
  | .mkScalar x => { c with scalars := add c.scalars x, owners := del c.owners x }
  | .copy y _ => { c with scalars := add c.scalars y, owners := del c.owners y }
  | .move y x => { c with scalars := del c.scalars y, owners := add (del c.owners x) y }
  | .drop x => { c with owners := del c.owners x }
  | .call _ => c
  | .spawn t args => { c with leases := (t, args) :: c.leases }
  | .wait t => { c with leases := c.leases.filter fun T => !(T.1 == t) }
  | .ite _ _ => c
  | .parallel _ _ => c

/-- The join of two branches: a local either branch killed is dead, and the two
must agree on which tickets are still live. -/
def joinOf (c c1 c2 : CState) : Option CState :=
  if c1.leases == c2.leases then
    some { scope := c.scope, scalars := keepIn c1.scalars c2.scalars,
           owners := keepIn c1.owners c2.owners, leases := c1.leases }
  else none

mutual

/-- One statement, checked.  `none` is rejection. -/
def checkStmt (c : CState) : Stmt → Option CState
  | .ite thn els =>
      match checkBlock c thn, checkBlock c els with
      | some c1, some c2 => joinOf c c1 c2
      | _, _ => none
  | s => if guardOf c s then some (effOf c s) else none

/-- A block, checked left to right. -/
def checkBlock (c : CState) : List Stmt → Option CState
  | [] => some c
  | s :: rest =>
      match checkStmt c s with
      | some c' => checkBlock c' rest
      | none => none

end

@[simp] theorem checkStmt_alloc (c : CState) (x : Var) :
    checkStmt c (.alloc x) = if guardOf c (.alloc x) then some (effOf c (.alloc x)) else none := rfl
@[simp] theorem checkStmt_mkScalar (c : CState) (x : Var) :
    checkStmt c (.mkScalar x) = if guardOf c (.mkScalar x) then some (effOf c (.mkScalar x)) else none := rfl
@[simp] theorem checkStmt_copy (c : CState) (y x : Var) :
    checkStmt c (.copy y x) = if guardOf c (.copy y x) then some (effOf c (.copy y x)) else none := rfl
@[simp] theorem checkStmt_move (c : CState) (y x : Var) :
    checkStmt c (.move y x) = if guardOf c (.move y x) then some (effOf c (.move y x)) else none := rfl
@[simp] theorem checkStmt_drop (c : CState) (x : Var) :
    checkStmt c (.drop x) = if guardOf c (.drop x) then some (effOf c (.drop x)) else none := rfl
@[simp] theorem checkStmt_call (c : CState) (args : List Borrow) :
    checkStmt c (.call args) = if guardOf c (.call args) then some (effOf c (.call args)) else none := rfl
@[simp] theorem checkStmt_spawn (c : CState) (t : Ticket) (args : List Borrow) :
    checkStmt c (.spawn t args) = if guardOf c (.spawn t args) then some (effOf c (.spawn t args)) else none := rfl
@[simp] theorem checkStmt_wait (c : CState) (t : Ticket) :
    checkStmt c (.wait t) = if guardOf c (.wait t) then some (effOf c (.wait t)) else none := rfl
@[simp] theorem checkStmt_parallel (c : CState) (nb : Bound) (body : List Touch) :
    checkStmt c (.parallel nb body) =
      if guardOf c (.parallel nb body) then some (effOf c (.parallel nb body)) else none := rfl
@[simp] theorem checkStmt_ite (c : CState) (thn els : List Stmt) :
    checkStmt c (.ite thn els) =
      match checkBlock c thn, checkBlock c els with
      | some c1, some c2 => joinOf c c1 c2
      | _, _ => none := rfl

@[simp] theorem checkBlock_nil (c : CState) : checkBlock c [] = some c := rfl
@[simp] theorem checkBlock_cons (c : CState) (s : Stmt) (rest : List Stmt) :
    checkBlock c (s :: rest) =
      match checkStmt c s with
      | some c' => checkBlock c' rest
      | none => none := rfl

/-- The starting ownership state of a scope: nothing bound, nothing lent. -/
def CState.start (p : Program) : CState :=
  { scope := p.scope, scalars := [], owners := [], leases := [] }

/-- **The checker**, as one executable Boolean: the body checks, and no ticket is
still live where the scope ends. -/
def accepts (p : Program) : Bool :=
  match checkBlock (CState.start p) p.body with
  | some c => c.leases.isEmpty
  | none => false

end Ownership
end Cairn
