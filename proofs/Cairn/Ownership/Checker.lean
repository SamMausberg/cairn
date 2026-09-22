/-
The checker: disjoint call arguments, the ownership state `checking.py` carries in its
scope, the guard and effect of every statement, the branch join, and `accepts`.
-/
import Cairn.Ownership.Lanes

namespace Cairn
namespace Ownership

/-! ## Disjoint arguments

`checking.py:disjoint`: no argument of one call may write what another can reach.  The chain
may use the bounds of every part in the argument list, because every one of those slices was
formed, and so guarded `lo <= hi`, before the call began.  It may use nothing else.  Unlike
`CState.inPlay` below, which is the fact set of `checking.py:leased`, the parts the live tasks
hold are not in play here, so one call handed `d[0..a]` and `d[b..n]` is rejected even while
`d[a..b]` is lent to a live task.  That is what the Python rule does, and the `example`s at the
end of `Regress.lean` pin both halves.  The machine asks the same question of the numbers. -/

/-- No borrow conflicts with a later one. -/
def pairsOk (inPlay : List Place) : List Borrow → Bool
  | [] => true
  | x :: rest => rest.all (fun y => !conflict inPlay x y) && pairsOk inPlay rest

/-- What the checker requires of an argument list. -/
def argsDisjoint (args : List Borrow) : Bool :=
  pairsOk (args.map Prod.fst) args

/-- The same, of the real footprints under a valuation: what the machine reports as
`AliasedArgs`. -/
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
      have hc : conflict inPlay x y = false := by simpa using h.1 y hy
      simp [conflict_sound hg hc]

/-! ## The checker

`CState` is the ownership state `checking.py` carries in its `Scope`: which locals hold what,
which groups are live, and the lease map from a ticket or a group to the borrows it holds.  A
local in neither `scalars` nor `owners` is dead: never bound, moved out, or killed by a branch
join.  The locals the scope declares are a parameter of every rule, since they never change. -/

structure CState where
  scalars : List Var
  owners : List Var
  groups : List Ticket
  leases : List Task
deriving DecidableEq, Repr, Inhabited

/-- Membership, decided without going through `LawfulBEq`. -/
def has {α : Type} [DecidableEq α] (l : List α) (a : α) : Bool := l.any fun b => decide (b = a)

@[simp] theorem has_iff {α : Type} [DecidableEq α] {l : List α} {a : α} : has l a = true ↔ a ∈ l := by
  simp [has]

/-- Add a local to a tracked set. -/
def add (l : List Var) (p : Var) : List Var := p :: l

/-- Remove every occurrence of a local from a tracked set. -/
def del (l : List Var) (p : Var) : List Var := l.filter fun q => !(q == p)

/-- Keep only the locals both branches still have: a local moved on one path is dead after the
join. -/
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

/-- A live ticket or group goes by this name. -/
def CState.named (c : CState) (t : Ticket) : Bool :=
  has c.groups t || c.leases.any fun T => decide (T.1 = t)

/-- The places whose bounds the chain may use for this access: everything the live tasks hold,
and the place being touched.  This is the `lent` list of `checking.py:leased` with its `a`
argument, and every one of them has been guarded: the lent ones where their slices were formed
at their spawns, the new one where its slice is formed now, before the access happens. -/
def CState.inPlay (c : CState) (x : Borrow) : List Place :=
  lentPlaces c.leases ++ [x.1]

/-- No live ticket or group forbids this access: `checking.py:leased`. -/
def CState.mayAccess (c : CState) (x : Borrow) : Bool :=
  !heldConflict (c.inPlay x) c.leases x

/-- Writing a local: no lease of any place of it may be live.  `whole p` overlaps every place of
`p`, so this is the rule that stops a move, a drop or a rebinding while any view of it is lent. -/
def CState.mayWrite (c : CState) (p : Var) : Bool :=
  c.mayAccess (.whole ⟨p, []⟩, Mode.rw)

/-- Every argument names a live local, and no lease forbids it. -/
def CState.mayLend (c : CState) (args : List Borrow) : Bool :=
  args.all fun x => c.livePlace x.1.base && c.mayAccess x

/-- Every rule a straight-line statement must satisfy, as one Boolean.  These are the conditions
`checking.py` raises `E-MOVED`, `E-LEASED`, `E-ALIAS`, `E-MOVE-BORROW`, `E-PINNED` and
`E-PARALLEL-*` for. -/
def guardOf (scope : List Var) (c : CState) : Stmt → Bool
  | .alloc x => scope.contains x && c.mayWrite x
  | .mkScalar x => scope.contains x && c.mayWrite x
  | .copy y x =>
      scope.contains y && !(y == x) && c.scalars.contains x
        && c.mayAccess (.whole ⟨x, []⟩, Mode.ro) && c.mayWrite y
  | .move y x =>
      scope.contains y && !(y == x) && c.owners.contains x && c.mayWrite x && c.mayWrite y
  | .drop x => c.owners.contains x && c.mayWrite x
  | .call args => argsDisjoint args && c.mayLend args
  | .spawn t args => argsDisjoint args && !c.named t && c.mayLend args
  | .wait t => c.named t
  | .ite _ _ => true
  | .parallel _ body => laneRule body && body.all fun a => c.livePlace a.root.var && c.mayAccess a.lease
  | .group g _ => !c.named g
  | .submit g args => has c.groups g && argsDisjoint args && c.mayLend args
  | .collect g => has c.groups g

/-- What a straight-line statement does to the ownership state. -/
def effOf (c : CState) : Stmt → CState
  | .alloc x => { c with scalars := del c.scalars x, owners := add c.owners x }
  | .mkScalar x => { c with scalars := add c.scalars x, owners := del c.owners x }
  | .copy y _ => { c with scalars := add c.scalars y, owners := del c.owners y }
  | .move y x => { c with scalars := del c.scalars y, owners := add (del c.owners x) y }
  | .drop x => { c with owners := del c.owners x }
  | .spawn t args => { c with leases := (t, args) :: c.leases }
  | .wait t => { c with leases := c.leases.filter (fun T => !decide (T.1 = t)), groups := del c.groups t }
  | .group g _ => { c with groups := add c.groups g }
  | .submit g args => { c with leases := (g, args) :: c.leases }
  | _ => c

/-! ### The branch join

`checking.py:branches` checks every path from one state and joins what the paths leave.  A local
either path moved is dead.  Tickets and groups are linear, so the paths must agree on which are
live (`E-LINEAR-BRANCH`).  A group may have been lent different places on different paths, and
after the join it holds what any path lent it.  A part lent on some paths only keeps its root and
its elements and loses its bounds, since its `lo <= hi` guard ran only where it was formed:
`checking.py:settle` writes it `d[?..?]`, which is `elems` here. -/

/-- A part lent on some paths only, without its bounds. -/
def Borrow.vague : Borrow → Borrow
  | (.part r _ _, m) => (.elems r, m)
  | y => y

/-- Some task named `t` in `L` holds `y`. -/
def lentIn (L : List Task) (t : Ticket) (y : Borrow) : Bool :=
  L.any fun T => decide (T.1 = t) && has T.2 y

/-- The leases after two paths join: every lease of either, each borrow exact only when both
paths lent it under that name. -/
def settle (L1 L2 : List Task) : List Task :=
  (L1 ++ L2.filter fun T => !has L1 T).map fun T =>
    (T.1, T.2.map fun y => if lentIn L1 T.1 y && lentIn L2 T.1 y then y else y.vague)

/-- The leases of tickets, as opposed to groups. -/
def CState.tickets (c : CState) : List Task := c.leases.filter fun T => !has c.groups T.1

/-- The join of two paths: a local either path killed is dead, the paths must agree on the live
groups and on the tickets, and the groups hold what either path lent them. -/
def joinOf (c1 c2 : CState) : Option CState :=
  if c1.groups.all (has c2.groups) && c2.groups.all (has c1.groups) && decide (c1.tickets = c2.tickets)
  then some { scalars := keepIn c1.scalars c2.scalars, owners := keepIn c1.owners c2.owners,
              groups := c1.groups, leases := settle c1.leases c2.leases }
  else none

mutual

/-- One statement, checked.  `none` is rejection. -/
def checkStmt (scope : List Var) (c : CState) : Stmt → Option CState
  | .ite thn els =>
      match checkBlock scope c thn, checkBlock scope c els with
      | some c1, some c2 => joinOf c1 c2
      | _, _ => none
  | s => if guardOf scope c s then some (effOf c s) else none

/-- A block, checked left to right. -/
def checkBlock (scope : List Var) (c : CState) : List Stmt → Option CState
  | [] => some c
  | s :: rest =>
      match checkStmt scope c s with
      | some c' => checkBlock scope c' rest
      | none => none

end

/-- What a checked straight-line statement says: its guard passed, and the state after it is its
effect. -/
theorem checkStmt_straight {scope : List Var} {c c1 : CState} {s : Stmt}
    (h : checkStmt scope c s = some c1) (hne : s.isIte = false) :
    guardOf scope c s = true ∧ c1 = effOf c s := by
  have hs : checkStmt scope c s = if guardOf scope c s then some (effOf c s) else none := by
    cases s <;> first | rfl | exact absurd hne nofun
  rw [hs] at h
  split at h
  · next hg => exact ⟨hg, (Option.some.inj h).symm⟩
  · exact absurd h nofun

@[simp] theorem checkStmt_ite (scope : List Var) (c : CState) (thn els : List Stmt) :
    checkStmt scope c (.ite thn els) =
      match checkBlock scope c thn, checkBlock scope c els with
      | some c1, some c2 => joinOf c1 c2
      | _, _ => none := rfl

@[simp] theorem checkBlock_nil (scope : List Var) (c : CState) : checkBlock scope c [] = some c := rfl
@[simp] theorem checkBlock_cons (scope : List Var) (c : CState) (s : Stmt) (rest : List Stmt) :
    checkBlock scope c (s :: rest) =
      match checkStmt scope c s with
      | some c' => checkBlock scope c' rest
      | none => none := rfl

/-- The ownership state a scope starts in: nothing bound, nothing lent. -/
def CState.start : CState := { scalars := [], owners := [], groups := [], leases := [] }

/-- **The checker**, as one executable Boolean: the body checks, and no ticket or group is still
live where the scope ends. -/
def accepts (p : Program) : Bool :=
  match checkBlock p.scope CState.start p.body with
  | some c => c.leases.isEmpty && c.groups.isEmpty
  | none => false

end Ownership
end Cairn
