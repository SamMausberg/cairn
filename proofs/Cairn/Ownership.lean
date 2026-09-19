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
connected to, the Python compiler; `docs/internals/verification.md` states exactly what is
and is not covered.

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
  /-- `wait(t)`: the only thing that returns a task's borrows. -/
  | wait (t : Ticket)
  /-- `if c { thn } else { els }`: the condition is opaque, so both branches are
  always reachable and the join is what makes a one-sided move dead. -/
  | ite (thn els : List Stmt)
  /-- `parallel i in n { body }`: one lane per index below `n`, all of them at once,
  and the spawner blocked until every one is done.  `n` is read from the valuation,
  exactly as a part bound is. -/
  | parallel (n : Bound) (body : List Touch)
deriving Repr, Inhabited

/-- A scope: the locals it declares and the statements it runs.  Every owner still
held by one of `scope`'s locals is released when the body falls off the end. -/
structure Program where
  scope : List Var
  body : List Stmt
deriving Repr, Inhabited

/-! ### The lanes a region forks

A lane has exactly the shape of a task -- a name and a footprint -- so the machine
races them against each other with the same machinery.  What differs is only where
the footprint comes from: a task is handed one at its spawn, a lane computes one from
its own index. -/

/-- One lane per index below `n`, holding what the body touches at that index. -/
def lanesOf (n : Nat) (body : List Touch) : List Task :=
  match n with
  | 0 => []
  | k + 1 => (k, body.map (Touch.borrow k)) :: lanesOf k body

@[simp] theorem Touch.borrow_root (a : Touch) (k : Nat) : (a.borrow k).1.root = a.root := by
  cases a <;> rfl

@[simp] theorem Touch.borrow_mode (a : Touch) (k : Nat) : (a.borrow k).2 = a.mode := by
  cases a <;> rfl

@[simp] theorem Touch.lease_root (a : Touch) : a.lease.1.root = a.root := by cases a <;> rfl

@[simp] theorem Touch.lease_base (a : Touch) : a.lease.1.base = a.root.var := by cases a <;> rfl

@[simp] theorem Touch.borrow_base (a : Touch) (k : Nat) : (a.borrow k).1.base = a.root.var := by
  cases a <;> rfl

/-- Every place a region names is guarded: a lane's own element is `[k, k+1)`, and
nothing else carries bounds at all.  So a region never traps. -/
@[simp] theorem Touch.lease_guard (ρ : Valuation) (a : Touch) : a.lease.1.guard ρ = true := by
  cases a <;> rfl

@[simp] theorem Touch.borrow_guard (ρ : Valuation) (a : Touch) (k : Nat) :
    (a.borrow k).1.guard ρ = true := by
  cases a
  · exact decide_eq_true (Nat.le_succ k)
  · rfl
  · rfl
  · rfl

/-- The lease check a region runs names `x[]`; a lane holds only its own element, so
it inherits the answer. -/
theorem races_borrow_of_lease {ρ : Valuation} {a : Touch} {k : Nat} {y : Borrow}
    (h : races ρ a.lease y = false) : races ρ (a.borrow k) y = false := by
  cases a with
  | other r m => exact h
  | whole r m => exact h
  | len r => exact h
  | elem r m =>
      show (meets ρ (.part r (.lit k) (.lit (k + 1))) y.1 && ((m == Mode.rw) || (y.2 == Mode.rw)))
        = false
      cases hm : meets ρ (Place.part r (.lit k) (.lit (k + 1))) y.1 with
      | false => rfl
      | true =>
          have hlease : (meets ρ (Place.elems r) y.1 && ((m == Mode.rw) || (y.2 == Mode.rw)))
              = false := h
          rw [meets_elems_of_part hm, Bool.true_and] at hlease
          rw [Bool.true_and]
          exact hlease

/-- An access that writes puts its local in `written`. -/
theorem writesVar_of_mem {body : List Touch} {a : Touch} (ha : a ∈ body) (hm : a.mode = Mode.rw) :
    writesVar body a.root.var = true := by
  simp only [writesVar, List.any_eq_true]
  refine ⟨a, ha, ?_⟩
  have h1 : (a.mode == Mode.rw) = true := by rw [hm]; rfl
  rw [h1, beq_place_self, Bool.and_self]

/-- **Under the region rule a lane holds only its own element, or the header.**  A
write is always the lane's own element; a `len` read is the header; nothing else
survives on a local the lanes write. -/
theorem borrow_shape {body : List Touch} (h : laneRule body = true) {a : Touch} (ha : a ∈ body)
    (hw : writesVar body a.root.var = true) (k : Nat) :
    a.borrow k = (.part a.root (.lit k) (.lit (k + 1)), a.mode) ∨
      a.borrow k = (.hdr a.root, Mode.ro) := by
  have hall := (List.all_eq_true.mp h) a ha
  cases a with
  | elem r m => exact Or.inl rfl
  | len r => exact Or.inr rfl
  | other r m =>
      have h2 : (!writesVar body r.var || false) = true := hall
      rw [show writesVar body r.var = true from hw] at h2
      exact Bool.noConfusion h2
  | whole r m =>
      have h2 : (!writesVar body r.var || false) = true := hall
      rw [show writesVar body r.var = true from hw] at h2
      exact Bool.noConfusion h2

/-- **Two lanes of an accepted region never race.**  If their borrows met with a write
among them they would be two writes, or a write and a read, of one local; the rule
then makes both of them the lane's own element, and the indices differ. -/
theorem lane_borrows_dont_race {ρ : Valuation} {body : List Touch} (h : laneRule body = true)
    {k l : Nat} (hkl : k ≠ l) {a b : Touch} (ha : a ∈ body) (hb : b ∈ body) :
    races ρ (a.borrow k) (b.borrow l) = false := by
  cases hr : races ρ (a.borrow k) (b.borrow l) with
  | false => rfl
  | true =>
      exfalso
      obtain ⟨hmeet, hmode⟩ := and_parts hr
      have hvar : a.root.var = b.root.var := by
        have ht : (a.borrow k).1.root.touches (b.borrow l).1.root = true :=
          (and_parts hmeet).1
        rw [Touch.borrow_root, Touch.borrow_root] at ht
        exact eq_of_beq (and_parts ht).1
      have hw : writesVar body a.root.var = true := by
        rcases Bool.or_eq_true_iff.mp hmode with hm | hm
        · rw [Touch.borrow_mode] at hm; exact writesVar_of_mem ha (eq_of_beq hm)
        · rw [Touch.borrow_mode] at hm
          rw [hvar]; exact writesVar_of_mem hb (eq_of_beq hm)
      have hwb : writesVar body b.root.var = true := by rw [← hvar]; exact hw
      rcases borrow_shape h ha hw k with hab | hab <;>
        rcases borrow_shape h hb hwb l with hbb | hbb <;>
        rw [hab, hbb] at hmeet hmode
      · rw [meets_own_element hkl] at hmeet; exact Bool.noConfusion hmeet
      · rw [meets_part_hdr] at hmeet; exact Bool.noConfusion hmeet
      · rw [meets_symm, meets_part_hdr] at hmeet; exact Bool.noConfusion hmeet
      · exact Bool.noConfusion hmode

/-- Every lane carries an index below `n`, so two of them never share one. -/
theorem lanesOf_index_lt : ∀ (n : Nat) {body : List Touch} {T : Task},
    T ∈ lanesOf n body → T.1 < n := by
  intro n
  induction n with
  | zero => intro body T hT; exact absurd hT List.not_mem_nil
  | succ k ih =>
      intro body T hT
      rcases List.mem_cons.mp hT with h1 | h2
      · rw [h1]; exact Nat.lt_succ_self k
      · exact Nat.lt_succ_of_lt (ih h2)

/-- Every borrow of a lane comes from an access of the body, taken at that lane's own
index. -/
theorem mem_lanesOf : ∀ (n : Nat) {body : List Touch} {T : Task}, T ∈ lanesOf n body →
    ∀ {y : Borrow}, y ∈ T.2 → ∃ a ∈ body, y = a.borrow T.1 := by
  intro n
  induction n with
  | zero => intro body T hT; exact absurd hT List.not_mem_nil
  | succ k ih =>
      intro body T hT y hy
      rcases List.mem_cons.mp hT with h1 | h2
      · subst h1
        obtain ⟨a, haa, hae⟩ := List.mem_map.mp hy
        exact ⟨a, haa, hae.symm⟩
      · exact ih h2 hy

/-- **The lanes of an accepted region are pairwise compatible.** -/
theorem lanesOf_pairwise (ρ : Valuation) : ∀ (n : Nat) {body : List Touch},
    laneRule body = true → (lanesOf n body).Pairwise (NoRacePair ρ) := by
  intro n
  induction n with
  | zero => intro body _; exact List.Pairwise.nil
  | succ k ih =>
      intro body h
      refine List.pairwise_cons.mpr ⟨?_, ih h⟩
      intro U hU x hx w hw
      have hlt : U.1 < k := lanesOf_index_lt k hU
      have hne : k ≠ U.1 := by
        intro hc
        rw [hc] at hlt
        exact Nat.lt_irrefl U.1 hlt
      obtain ⟨a, ha, hae⟩ := List.mem_map.mp hx
      obtain ⟨b, hb, hbe⟩ := mem_lanesOf k hU hw
      rw [← hae, hbe]
      exact lane_borrows_dont_race h hne ha hb

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

/-! ## Weakening

A branch join throws locals away, so after `if` the checker holds a *weaker* state
than either branch produced.  Stepping into a branch therefore has to know that
whatever checks from the join also checks from the branch. -/

/-- `c` claims no more than `d` does. -/
structure Le (c d : CState) : Prop where
  scope_eq : c.scope = d.scope
  scalars : ∀ p, p ∈ c.scalars → p ∈ d.scalars
  owners : ∀ p, p ∈ c.owners → p ∈ d.owners
  leases_eq : c.leases = d.leases

theorem Le.refl (c : CState) : Le c c := ⟨rfl, fun _ h => h, fun _ h => h, rfl⟩

@[simp] theorem contains_iff_mem {l : List Var} {p : Var} : l.contains p = true ↔ p ∈ l := by
  simp

theorem mayAccess_congr {c d : CState} (h : Le c d) (x : Borrow) :
    d.mayAccess x = c.mayAccess x := by
  simp [CState.mayAccess, CState.inPlay, h.leases_eq]

theorem livePlace_mono {c d : CState} (h : Le c d) {p : Var} (hp : c.livePlace p = true) :
    d.livePlace p = true := by
  simp only [CState.livePlace, Bool.or_eq_true, contains_iff_mem] at hp ⊢
  exact hp.imp (h.scalars p) (h.owners p)

theorem guard_mono {c d : CState} (h : Le c d) {s : Stmt} (hs : guardOf c s = true) :
    guardOf d s = true := by
  have hsc : d.scope = c.scope := h.scope_eq.symm
  have hlc : d.leases = c.leases := h.leases_eq.symm
  have hmw : ∀ p, d.mayWrite p = c.mayWrite p := fun p => mayAccess_congr h _
  have hma : ∀ x, d.mayAccess x = c.mayAccess x := mayAccess_congr h
  have hargs : ∀ args : List Borrow,
      (args.all fun x => c.livePlace x.1.base && c.mayAccess x) = true →
      (args.all fun x => d.livePlace x.1.base && d.mayAccess x) = true := by
    intro args ha
    simp only [List.all_eq_true, Bool.and_eq_true] at ha ⊢
    intro x hx
    exact ⟨livePlace_mono h (ha x hx).1, by rw [hma]; exact (ha x hx).2⟩
  cases s with
  | alloc x => simpa only [guardOf, hsc, hmw] using hs
  | mkScalar x => simpa only [guardOf, hsc, hmw] using hs
  | copy y x =>
      simp only [guardOf, Bool.and_eq_true, contains_iff_mem, hsc, hmw, hma] at hs ⊢
      exact ⟨⟨⟨⟨hs.1.1.1.1, hs.1.1.1.2⟩, h.scalars x hs.1.1.2⟩, hs.1.2⟩, hs.2⟩
  | move y x =>
      simp only [guardOf, Bool.and_eq_true, contains_iff_mem, hsc, hmw] at hs ⊢
      exact ⟨⟨⟨⟨hs.1.1.1.1, hs.1.1.1.2⟩, h.owners x hs.1.1.2⟩, hs.1.2⟩, hs.2⟩
  | drop x =>
      simp only [guardOf, Bool.and_eq_true, contains_iff_mem, hmw] at hs ⊢
      exact ⟨h.owners x hs.1, hs.2⟩
  | call args =>
      simp only [guardOf, Bool.and_eq_true] at hs ⊢
      exact ⟨hs.1, hargs args hs.2⟩
  | spawn t args =>
      simp only [guardOf, Bool.and_eq_true, hlc] at hs ⊢
      exact ⟨⟨hs.1.1, hs.1.2⟩, hargs args hs.2⟩
  | wait t => simpa only [guardOf, hlc] using hs
  | ite thn els => rfl
  | parallel nb body =>
      simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hs ⊢
      refine ⟨hs.1, fun a ha => ?_⟩
      exact ⟨livePlace_mono h (hs.2 a ha).1, by rw [hma]; exact (hs.2 a ha).2⟩

theorem eff_mono {c d : CState} (h : Le c d) (s : Stmt) : Le (effOf c s) (effOf d s) := by
  have hs := h.scalars
  have ho := h.owners
  cases s <;> refine ⟨h.scope_eq, ?_, ?_, by simp [effOf, h.leases_eq]⟩ <;> intro p hp <;>
    (try simp only [effOf, mem_del, mem_add] at hp ⊢) <;>
    first
      | exact hs p hp
      | exact ho p hp
      | exact ⟨hs p hp.1, hp.2⟩
      | exact ⟨ho p hp.1, hp.2⟩
      | exact hp.imp id (hs p)
      | exact hp.imp id (ho p)
      | exact hp.imp id (fun hq => ⟨ho p hq.1, hq.2⟩)

mutual
/-- A structural measure that does not depend on `sizeOf`'s exact shape. -/
def stmtSize : Stmt → Nat
  | .ite thn els => blockSize thn + blockSize els + 1
  | _ => 1
def blockSize : List Stmt → Nat
  | [] => 0
  | s :: rest => stmtSize s + blockSize rest + 1
end

@[simp] theorem blockSize_nil : blockSize [] = 0 := rfl
@[simp] theorem blockSize_cons (s : Stmt) (rest : List Stmt) :
    blockSize (s :: rest) = stmtSize s + blockSize rest + 1 := rfl
@[simp] theorem stmtSize_ite (thn els : List Stmt) :
    stmtSize (.ite thn els) = blockSize thn + blockSize els + 1 := rfl

theorem joinOf_mono {c d c1 d1 c2 d2 j : CState} (h : Le c d) (h1 : Le c1 d1) (h2 : Le c2 d2)
    (hj : joinOf c c1 c2 = some j) : ∃ k, joinOf d d1 d2 = some k ∧ Le j k := by
  unfold joinOf at hj ⊢
  split at hj
  · next heq =>
      have hd : d1.leases = d2.leases := by
        rw [← h1.leases_eq, ← h2.leases_eq]
        exact of_decide_eq_true (by simpa using heq)
      rw [show (d1.leases == d2.leases) = true by simpa using hd]
      refine ⟨_, rfl, ?_⟩
      have hjv := Option.some.inj hj
      rw [← hjv]
      refine ⟨h.scope_eq, ?_, ?_, h1.leases_eq⟩
      · intro p hp
        simp only [mem_keepIn] at hp ⊢
        exact ⟨h1.scalars p hp.1, h2.scalars p hp.2⟩
      · intro p hp
        simp only [mem_keepIn] at hp ⊢
        exact ⟨h1.owners p hp.1, h2.owners p hp.2⟩
  · exact absurd hj (by simp)

/-- The straight-line half of weakening: a guard that passes in `c` passes in `d`. -/
theorem guardAux {c d c1 : CState} {s : Stmt} (hle : Le c d)
    (hc1 : (if guardOf c s then some (effOf c s) else none) = some c1) :
    ∃ d1, (if guardOf d s then some (effOf d s) else none) = some d1 ∧ Le c1 d1
      ∧ c1.scope = c.scope := by
  split at hc1
  · next hg =>
      rw [guard_mono hle hg]
      refine ⟨_, rfl, ?_, ?_⟩
      · rw [← Option.some.inj hc1]; exact eff_mono hle s
      · rw [← Option.some.inj hc1]; cases s <;> rfl
  · exact absurd hc1 (by simp)

/-- **Weakening.**  What checks from a weaker ownership state checks from a stronger
one, and the result stays weaker. -/
theorem checkBlock_mono : ∀ (n : Nat) (ss : List Stmt), blockSize ss < n →
    ∀ {c d c' : CState}, Le c d → checkBlock c ss = some c' →
      ∃ d', checkBlock d ss = some d' ∧ Le c' d' ∧ c'.scope = c.scope := by
  intro n
  induction n with
  | zero => intro ss hss; exact absurd hss (Nat.not_lt_zero _)
  | succ n ih =>
      intro ss hss c d c' hle hs
      cases ss with
      | nil => exact ⟨d, rfl, by rw [← Option.some.inj hs]; exact hle, by rw [← Option.some.inj hs]⟩
      | cons s rest =>
          have hrest : blockSize rest < n := by simp only [blockSize_cons] at hss; omega
          rw [checkBlock_cons] at hs
          cases hc1 : checkStmt c s with
          | none => rw [hc1] at hs; exact absurd hs (by simp)
          | some c1 =>
              rw [hc1] at hs
              have hstep : ∃ d1, checkStmt d s = some d1 ∧ Le c1 d1 ∧ c1.scope = c.scope := by
                cases s with
                | ite thn els =>
                    have hthn : blockSize thn < n := by
                      simp only [blockSize_cons, stmtSize_ite] at hss; omega
                    have hels : blockSize els < n := by
                      simp only [blockSize_cons, stmtSize_ite] at hss; omega
                    rw [checkStmt_ite] at hc1
                    split at hc1
                    · next a1 a2 h1 h2 =>
                        obtain ⟨d1, hd1, hle1, _⟩ := ih thn hthn hle h1
                        obtain ⟨d2, hd2, hle2, _⟩ := ih els hels hle h2
                        obtain ⟨k, hk, hlek⟩ := joinOf_mono hle hle1 hle2 hc1
                        refine ⟨k, by rw [checkStmt_ite, hd1, hd2]; exact hk, hlek, ?_⟩
                        unfold joinOf at hc1
                        split at hc1
                        · rw [← Option.some.inj hc1]
                        · exact absurd hc1 (by simp)
                    · exact absurd hc1 (by simp)
                | alloc x => exact guardAux hle hc1
                | mkScalar x => exact guardAux hle hc1
                | copy y x => exact guardAux hle hc1
                | move y x => exact guardAux hle hc1
                | drop x => exact guardAux hle hc1
                | call args => exact guardAux hle hc1
                | spawn t args => exact guardAux hle hc1
                | wait t => exact guardAux hle hc1
                | parallel nb body => exact guardAux hle hc1
              obtain ⟨d1, hd1, hle1, hsc1⟩ := hstep
              obtain ⟨d', hd', hled, hscd⟩ := ih rest hrest hle1 hs
              exact ⟨d', by rw [checkBlock_cons, hd1]; exact hd', hled, by rw [hscd, hsc1]⟩

theorem checkBlock_append (c : CState) : ∀ (l₁ l₂ : List Stmt),
    checkBlock c (l₁ ++ l₂) =
      match checkBlock c l₁ with
      | some c' => checkBlock c' l₂
      | none => none := by
  intro l₁
  induction l₁ generalizing c with
  | nil => intro l₂; rfl
  | cons s rest ih =>
      intro l₂
      rw [List.cons_append, checkBlock_cons, checkBlock_cons]
      cases h : checkStmt c s with
      | none => rfl
      | some c' => exact ih c' l₂

/-! ## Dynamic semantics

The machine is undefensive on purpose.  `moved` is a ghost mark: it exists only so
that a use of a moved local can be *named* `useAfterMove`, never to stop one.
`copy` duplicates whatever the source local holds, which is how a mis-accepted
copy of an owner would reach a double free.  Binding a local releases what it held
before, which is what an assignment lowers to. -/

/-- What a local holds.  `nil` is never bound, `moved` is the ghost left by a move
or a release. -/
inductive Val where
  | nil
  | moved
  | scalar
  | owner (a : AllocId)
deriving DecidableEq, Repr, Inhabited

/-- The faults the machine can reach. -/
inductive Err where
  | useAfterMove (p : Var)
  | useAfterFree (a : AllocId)
  | doubleFree (a : AllocId)
  | leak (t : Ticket)
  | race (p : Var)
  | aliasedArgs
deriving DecidableEq, Repr, Inhabited

/-- The heap and the locals.  `frees a` counts how often `a` has been released, so
"released exactly once" is a statement about numbers rather than about a log. -/
structure State where
  env : Var → Val
  live : AllocId → Bool
  next : AllocId
  frees : AllocId → Nat

/-- Point update of the local map. -/
def upd (f : Var → Val) (x : Var) (v : Val) : Var → Val :=
  fun p => if p = x then v else f p

/-- Point update of the liveness map. -/
def updL (f : AllocId → Bool) (a : AllocId) (v : Bool) : AllocId → Bool :=
  fun b => if b = a then v else f b

/-- Point update of the release counter. -/
def updN (f : AllocId → Nat) (a : AllocId) (v : Nat) : AllocId → Nat :=
  fun b => if b = a then v else f b

@[simp] theorem upd_same (f : Var → Val) (x : Var) (v : Val) : upd f x v x = v := by
  simp [upd]

@[simp] theorem upd_other {f : Var → Val} {x p : Var} {v : Val} (h : p ≠ x) :
    upd f x v p = f p := by simp [upd, h]

@[simp] theorem updL_same (f : AllocId → Bool) (a : AllocId) (v : Bool) : updL f a v a = v := by
  simp [updL]

@[simp] theorem updL_other {f : AllocId → Bool} {a b : AllocId} {v : Bool} (h : b ≠ a) :
    updL f a v b = f b := by simp [updL, h]

@[simp] theorem updN_same (f : AllocId → Nat) (a : AllocId) (v : Nat) : updN f a v a = v := by
  simp [updN]

@[simp] theorem updN_other {f : AllocId → Nat} {a b : AllocId} {v : Nat} (h : b ≠ a) :
    updN f a v b = f b := by simp [updN, h]

/-- Release whatever a local holds and leave the ghost mark.  Releasing a cell that
is already gone is the double free the affine rule has to rule out. -/
def release (x : Var) (st : State) : Except Err State :=
  match st.env x with
  | .owner a =>
      if st.live a then
        .ok { env := upd st.env x .moved, live := updL st.live a false,
              next := st.next, frees := updN st.frees a (st.frees a + 1) }
      else .error (.doubleFree a)
  | .nil => .ok st
  | .moved => .ok st
  | .scalar => .ok st

/-- Put a fresh cell in `x`, after releasing what `x` held: `let x = Buf[T](n);`
and the replacement a task performs through an `rw` lease both do this. -/
def reallocAt (x : Var) (st : State) : Except Err State :=
  match release x st with
  | .error e => .error e
  | .ok st' =>
      .ok { env := upd st'.env x (.owner st'.next), live := updL st'.live st'.next true,
            next := st'.next + 1, frees := st'.frees }

/-- Put `v` in `y`, after releasing what `y` held. -/
def bindAt (y : Var) (v : Val) (st : State) : Except Err State :=
  match release y st with
  | .error e => .error e
  | .ok st' => .ok { st' with env := upd st'.env y v }

/-- Reading or writing a local that holds nothing, or a cell that is gone. -/
def memErr (st : State) (p : Var) : Option Err :=
  match st.env p with
  | .nil => some (.useAfterMove p)
  | .moved => some (.useAfterMove p)
  | .scalar => none
  | .owner a => if st.live a then none else some (.useAfterFree a)

/-- Touching a place that some other thread really holds in a conflicting mode.
Unlike the checker's test, this one asks the numbers: two parts race only when
their index ranges meet under the valuation. -/
def raceErr (ρ : Valuation) (tasks : List Task) (x : Borrow) : Option Err :=
  if heldRace ρ tasks x then some (.race x.1.base) else none

/-- One access: the race check first, then the memory check. -/
def accessErr (ρ : Valuation) (tasks : List Task) (st : State) (x : Borrow) : Option Err :=
  match raceErr ρ tasks x with
  | some e => some e
  | none => memErr st x.1.base

/-- Every access of one call, left to right. -/
def accessAll (ρ : Valuation) (tasks : List Task) (st : State) : List Borrow → Option Err
  | [] => none
  | x :: rest =>
      match accessErr ρ tasks st x with
      | some e => some e
      | none => accessAll ρ tasks st rest

/-- A machine configuration: the spawner's remaining statements, the tasks that are
still live, the lanes of the region that is running -- empty unless one is -- and the
state.  `done` is normal termination, `err` is a fault, and `trap` is the defined
abort a failed `lo <= hi` guard performs. -/
inductive Cfg where
  | run (code : List Stmt) (tasks lanes : List Task) (st : State)
  | done (st : State)
  | trap
  | err (e : Err)

/-- The implicit release of a scope's locals, in order. -/
def releaseAll : List Var → State → Except Err State
  | [], st => .ok st
  | p :: rest, st =>
      match release p st with
      | .ok st' => releaseAll rest st'
      | .error e => .error e

/-- Every element of a list paired with the other elements, so that a task step can
name the threads it is racing against without naming itself. -/
def splits {α : Type} : List α → List (α × List α)
  | [] => []
  | a :: rest => (a, rest) :: (splits rest).map (fun x => (x.1, a :: x.2))

/-- The `cr::part` guard of every slice the statement forms.  It runs on this
thread, before the call is made or the task is started. -/
def argsGuarded (ρ : Valuation) (args : List Borrow) : Bool :=
  args.all fun x => x.1.guard ρ

/-- One step of the spawner.  It runs only while no region is live, so every
configuration it produces has no lanes -- except the one a region starts, which forks
one lane per index below `n` and leaves the spawner blocked until they are done. -/
def stepStmt (ρ : Valuation) (s : Stmt) (rest : List Stmt) (tasks : List Task)
    (st : State) : List Cfg :=
  match s with
  | .alloc x =>
      match raceErr ρ tasks (.whole ⟨x, []⟩, .rw) with
      | some e => [.err e]
      | none =>
          match reallocAt x st with
          | .error e => [.err e]
          | .ok st' => [.run rest tasks [] st']
  | .mkScalar x =>
      match raceErr ρ tasks (.whole ⟨x, []⟩, .rw) with
      | some e => [.err e]
      | none =>
          match bindAt x .scalar st with
          | .error e => [.err e]
          | .ok st' => [.run rest tasks [] st']
  | .copy y x =>
      match accessErr ρ tasks st (.whole ⟨x, []⟩, .ro) with
      | some e => [.err e]
      | none =>
          match raceErr ρ tasks (.whole ⟨y, []⟩, .rw) with
          | some e => [.err e]
          | none =>
              match bindAt y (st.env x) st with
              | .error e => [.err e]
              | .ok st' => [.run rest tasks [] st']
  | .move y x =>
      match accessErr ρ tasks st (.whole ⟨x, []⟩, .rw) with
      | some e => [.err e]
      | none =>
          match raceErr ρ tasks (.whole ⟨y, []⟩, .rw) with
          | some e => [.err e]
          | none =>
              match bindAt y (st.env x) st with
              | .error e => [.err e]
              | .ok st' => [.run rest tasks [] { st' with env := upd st'.env x .moved }]
  | .drop x =>
      match accessErr ρ tasks st (.whole ⟨x, []⟩, .rw) with
      | some e => [.err e]
      | none =>
          match release x st with
          | .error e => [.err e]
          | .ok st' => [.run rest tasks [] st']
  | .call args =>
      if !argsGuarded ρ args then [.trap]
      else if !pairsOkAt ρ args then [.err .aliasedArgs]
      else
        match accessAll ρ tasks st args with
        | some e => [.err e]
        | none => [.run rest tasks [] st]
  | .spawn t args =>
      if !argsGuarded ρ args then [.trap]
      else if !pairsOkAt ρ args then [.err .aliasedArgs]
      else
        match accessAll ρ tasks st args with
        | some e => [.err e]
        | none => [.run rest ((t, args) :: tasks) [] st]
  | .wait t => [.run rest (tasks.filter fun T => !(T.1 == t)) [] st]
  | .ite thn els => [.run (thn ++ rest) tasks [] st, .run (els ++ rest) tasks [] st]
  | .parallel nb body => [.run rest tasks (lanesOf (nb.eval ρ) body) st]

/-- What a thread may do through a borrow it holds `rw`: replace the cell, if what it
holds is the whole owner and the owner holds a cell -- that is what `swap` through a
lent owner does -- and otherwise nothing this model can see, since a view cannot
replace the storage it views and values are not modelled. -/
def taskWrite (code : List Stmt) (tasks lanes : List Task) (r : Place)
    (st : State) : List Cfg :=
  match r with
  | .whole ⟨p, []⟩ =>
      match st.env p with
      | .owner _ =>
          match reallocAt p st with
          | .error e => [.err e]
          | .ok st' => [.run code tasks lanes st, .run code tasks lanes st']
      | .nil => [.run code tasks lanes st]
      | .moved => [.run code tasks lanes st]
      | .scalar => [.run code tasks lanes st]
  | .whole ⟨_, _ :: _⟩ => [.run code tasks lanes st]
  | .hdr _ => [.run code tasks lanes st]
  | .elems _ => [.run code tasks lanes st]
  | .part _ _ _ => [.run code tasks lanes st]

/-- One step of one live thread -- a task or a lane, which the machine treats alike:
any borrow of its footprint, at any time, racing against every other live thread. -/
def stepThread (ρ : Valuation) (code : List Stmt) (tasks lanes : List Task) (T : Task)
    (others : List Task) (st : State) : List Cfg :=
  T.2.flatMap fun x =>
    match accessErr ρ others st x with
    | some e => [.err e]
    | none =>
        match x.2 with
        | .rw => taskWrite code tasks lanes x.1 st
        | .ro => [.run code tasks lanes st]

/-- What the spawner does next: the next statement, or -- at the end of the body --
the leak check and the implicit release of the scope. -/
def stepMain (ρ : Valuation) (scope : List Var) (code : List Stmt) (tasks : List Task)
    (st : State) : List Cfg :=
  match code with
  | [] =>
      match tasks with
      | T :: _ => [.err (.leak T.1)]
      | [] =>
          match releaseAll scope st with
          | .ok st' => [.done st']
          | .error e => [.err e]
  | s :: rest => stepStmt ρ s rest tasks st

/-- The main thread's own step, which a running region BLOCKS: `parallel` completes
before the next statement, so while any lane is live the only thing the main thread
can do is end the region.  Tasks spawned earlier keep running throughout. -/
def stepHost (ρ : Valuation) (scope : List Var) (code : List Stmt) (tasks lanes : List Task)
    (st : State) : List Cfg :=
  match lanes with
  | [] => stepMain ρ scope code tasks st
  | _ :: _ => [.run code tasks [] st]

/-- Every successor of a configuration: one main-thread step interleaved with every
access every live thread -- task or lane -- might make.  `done`, `trap` and `err` are
final. -/
def succ (ρ : Valuation) (scope : List Var) : Cfg → List Cfg
  | .done _ => []
  | .trap => []
  | .err _ => []
  | .run code tasks lanes st =>
      stepHost ρ scope code tasks lanes st
      ++ (splits (tasks ++ lanes)).flatMap fun x => stepThread ρ code tasks lanes x.1 x.2 st

/-- The state a scope starts in: nothing bound, nothing allocated. -/
def State.start : State :=
  { env := fun _ => .nil, live := fun _ => false, next := 0, frees := fun _ => 0 }

/-- The configuration a program starts in. -/
def Cfg.start (p : Program) : Cfg := .run p.body [] [] State.start

/-- Reachability under the interleaving semantics, at one valuation of the
immutable bounds. -/
inductive Reach (ρ : Valuation) (scope : List Var) : Cfg → Cfg → Prop where
  | refl (c : Cfg) : Reach ρ scope c c
  | step {a b c : Cfg} : b ∈ succ ρ scope a → Reach ρ scope b c → Reach ρ scope a c

/-! ## The heap invariant

None of this mentions the checker: it is what the machine keeps true by itself
once no fault has happened. -/

structure MemOk (st : State) (scope : List Var) : Prop where
  /-- A local never holds a cell that has been released: no dangling pointer. -/
  liveOfEnv : ∀ p a, st.env p = .owner a → st.live a = true
  /-- Two locals never hold the same cell: the affine core. -/
  uniq : ∀ p q a, st.env p = .owner a → st.env q = .owner a → p = q
  /-- Every live cell sits in a local the scope will release. -/
  covered : ∀ a, st.live a = true → ∃ p, p ∈ scope ∧ st.env p = .owner a
  /-- A live cell has not been released. -/
  liveUnfreed : ∀ a, st.live a = true → st.frees a = 0
  /-- Every allocated cell is live or has been released exactly once. -/
  freedOnce : ∀ a, a < st.next → st.live a = true ∨ st.frees a = 1
  /-- Nothing beyond the allocation frontier exists. -/
  beyond : ∀ a, st.next ≤ a → st.live a = false ∧ st.frees a = 0

theorem MemOk.lt_next {st : State} {scope} (h : MemOk st scope) {a : AllocId}
    (ha : st.live a = true) : a < st.next := by
  rcases Nat.lt_or_ge a st.next with hlt | hge
  · exact hlt
  · rw [(h.beyond a hge).1] at ha; exact Bool.noConfusion ha

/-! ### Releasing -/

/-- A successful release either found nothing to free, or freed exactly the cell the
place held. -/
theorem release_cases {st st' : State} {x : Var} (hr : release x st = .ok st') :
    (st' = st ∧ ∀ a, st.env x ≠ .owner a) ∨
    (∃ a, st.env x = .owner a ∧ st.live a = true ∧
      st' = ⟨upd st.env x .moved, updL st.live a false, st.next,
             updN st.frees a (st.frees a + 1)⟩) := by
  revert hr
  unfold release
  split
  · next a heq =>
      split
      · next hl => exact fun hr => Or.inr ⟨a, heq, hl, (Except.ok.inj hr).symm⟩
      · exact fun hr => absurd hr (by simp)
  · next heq =>
      exact fun hr => Or.inl ⟨(Except.ok.inj hr).symm,
        fun b hb => by rw [heq] at hb; exact Val.noConfusion hb⟩
  · next heq =>
      exact fun hr => Or.inl ⟨(Except.ok.inj hr).symm,
        fun b hb => by rw [heq] at hb; exact Val.noConfusion hb⟩
  · next heq =>
      exact fun hr => Or.inl ⟨(Except.ok.inj hr).symm,
        fun b hb => by rw [heq] at hb; exact Val.noConfusion hb⟩

theorem release_ok {st : State} {scope} (h : MemOk st scope) (x : Var) :
    ∃ st', release x st = .ok st' := by
  unfold release
  split
  · next a heq => rw [h.liveOfEnv x a heq]; exact ⟨_, rfl⟩
  · exact ⟨st, rfl⟩
  · exact ⟨st, rfl⟩
  · exact ⟨st, rfl⟩

theorem release_next {st st' : State} {x : Var} (hr : release x st = .ok st') :
    st'.next = st.next := by
  rcases release_cases hr with ⟨he, _⟩ | ⟨a, _, _, he⟩ <;> rw [he]

theorem release_env_ne {st st' : State} {x p : Var} (hr : release x st = .ok st')
    (hne : p ≠ x) : st'.env p = st.env p := by
  rcases release_cases hr with ⟨he, _⟩ | ⟨a, _, _, he⟩
  · rw [he]
  · rw [he]; exact upd_other hne

theorem release_env_self {st st' : State} {x : Var} (hr : release x st = .ok st') :
    ∀ a, st'.env x ≠ .owner a := by
  rcases release_cases hr with ⟨he, hn⟩ | ⟨a, _, _, he⟩
  · rw [he]; exact hn
  · rw [he]; intro b; show upd st.env x .moved x ≠ _
    rw [upd_same]; exact fun h => Val.noConfusion h

theorem release_live_of {st st' : State} {x : Var} (hr : release x st = .ok st')
    {a : AllocId} (ha : st'.live a = true) : st.live a = true := by
  rcases release_cases hr with ⟨he, _⟩ | ⟨b, _, _, he⟩
  · rw [he] at ha; exact ha
  · rw [he] at ha
    have ha' : updL st.live b false a = true := ha
    rcases nat_eq_or_ne a b with hab | hab
    · rw [hab, updL_same] at ha'; exact Bool.noConfusion ha'
    · rw [updL_other hab] at ha'; exact ha'

theorem memOk_release {st st' : State} {scope} {x : Var} (h : MemOk st scope)
    (hr : release x st = .ok st') : MemOk st' scope := by
  rcases release_cases hr with ⟨he, _⟩ | ⟨a, hx, ha, he⟩
  · rw [he]; exact h
  subst he
  have henv : ∀ p, p ≠ x → (upd st.env x Val.moved) p = st.env p := fun p hp => upd_other hp
  have hlive : ∀ b, b ≠ a → (updL st.live a false) b = st.live b := fun b hb => updL_other hb
  have hfree : ∀ b, b ≠ a → (updN st.frees a (st.frees a + 1)) b = st.frees b :=
    fun b hb => updN_other hb
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p b hp0
    have hp : (upd st.env x Val.moved) p = .owner b := hp0
    rcases nat_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp; exact Val.noConfusion hp
    rw [henv p hpx] at hp
    have hne : b ≠ a := fun hba => hpx (h.uniq p x b hp (hba ▸ hx))
    show (updL st.live a false) b = true
    rw [hlive b hne]; exact h.liveOfEnv p b hp
  · intro p q b hp0 hq0
    have hp : (upd st.env x Val.moved) p = .owner b := hp0
    have hq : (upd st.env x Val.moved) q = .owner b := hq0
    rcases nat_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp; exact Val.noConfusion hp
    rcases nat_eq_or_ne q x with hqx | hqx
    · rw [hqx, upd_same] at hq; exact Val.noConfusion hq
    rw [henv p hpx] at hp; rw [henv q hqx] at hq
    exact h.uniq p q b hp hq
  · intro b hb0
    have hb : (updL st.live a false) b = true := hb0
    have hne : b ≠ a := by
      intro hba; rw [hba, updL_same] at hb; exact Bool.noConfusion hb
    rw [hlive b hne] at hb
    obtain ⟨p, hp, hpe⟩ := h.covered b hb
    have hpx : p ≠ x := by
      intro hc
      refine hne (Val.owner.inj ?_)
      rw [← hpe, hc, hx]
    have hfin : (upd st.env x Val.moved) p = Val.owner b := by rw [henv p hpx]; exact hpe
    exact ⟨p, hp, hfin⟩
  · intro b hb0
    have hb : (updL st.live a false) b = true := hb0
    have hne : b ≠ a := by
      intro hba; rw [hba, updL_same] at hb; exact Bool.noConfusion hb
    rw [hlive b hne] at hb
    show (updN st.frees a (st.frees a + 1)) b = 0
    rw [hfree b hne]; exact h.liveUnfreed b hb
  · intro b hb
    rcases nat_eq_or_ne b a with hba | hba
    · right
      show (updN st.frees a (st.frees a + 1)) b = 1
      rw [hba, updN_same, h.liveUnfreed a ha]
    rcases h.freedOnce b hb with hl | hf
    · left; show (updL st.live a false) b = true
      rw [hlive b hba]; exact hl
    · right; show (updN st.frees a (st.frees a + 1)) b = 1
      rw [hfree b hba]; exact hf
  · intro b hb
    have hne : b ≠ a := fun hba =>
      absurd (h.lt_next ha) (Nat.not_lt.mpr (hba ▸ hb))
    exact ⟨by show (updL st.live a false) b = false
              rw [hlive b hne]; exact (h.beyond b hb).1,
           by show (updN st.frees a (st.frees a + 1)) b = 0
              rw [hfree b hne]; exact (h.beyond b hb).2⟩

theorem release_live_keep {st st' : State} {x : Var} (hr : release x st = .ok st')
    {a : AllocId} (hne : st.env x ≠ .owner a) (ha : st.live a = true) : st'.live a = true := by
  rcases release_cases hr with ⟨he, _⟩ | ⟨b, hx, _, he⟩
  · rw [he]; exact ha
  · rw [he]
    have hab : a ≠ b := fun hc => hne (hc ▸ hx)
    show updL st.live b false a = true
    rw [updL_other hab]; exact ha

/-- A local that holds no cell can be dropped from the coverage list. -/
theorem memOk_tail {st : State} {p : Var} {rest : List Var}
    (h : MemOk st (p :: rest)) (hp : ∀ a, st.env p ≠ .owner a) : MemOk st rest := by
  refine ⟨h.liveOfEnv, h.uniq, ?_, h.liveUnfreed, h.freedOnce, h.beyond⟩
  intro a ha
  obtain ⟨q, hq, hqe⟩ := h.covered a ha
  rcases List.mem_cons.mp hq with hqp | hqr
  · exact absurd hqe (hqp ▸ hp a)
  · exact ⟨q, hqr, hqe⟩

/-! ### Fresh cells -/

/-- Putting a brand new cell in a local that holds none. -/
theorem memOk_fresh {st : State} {scope} {x : Var} (h : MemOk st scope) (hx : x ∈ scope)
    (hnx : ∀ a, st.env x ≠ .owner a) :
    MemOk ⟨upd st.env x (.owner st.next), updL st.live st.next true, st.next + 1, st.frees⟩
      scope := by
  have hnl : st.live st.next = false := (h.beyond st.next (Nat.le_refl _)).1
  have hnf : st.frees st.next = 0 := (h.beyond st.next (Nat.le_refl _)).2
  have hnotnext : ∀ b, st.live b = true → b ≠ st.next := by
    intro b hb hc; rw [hc, hnl] at hb; exact Bool.noConfusion hb
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p b hp0
    have hp : (upd st.env x (Val.owner st.next)) p = .owner b := hp0
    rcases nat_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp
      have : st.next = b := Val.owner.inj hp
      show updL st.live st.next true b = true
      rw [← this, updL_same]
    · rw [upd_other hpx] at hp
      have hb := h.liveOfEnv p b hp
      show updL st.live st.next true b = true
      rw [updL_other (hnotnext b hb)]; exact hb
  · intro p q b hp0 hq0
    have hp : (upd st.env x (Val.owner st.next)) p = .owner b := hp0
    have hq : (upd st.env x (Val.owner st.next)) q = .owner b := hq0
    have key : ∀ r, (upd st.env x (Val.owner st.next)) r = .owner b → r = x ∨ st.env r = .owner b := by
      intro r hr
      rcases nat_eq_or_ne r x with hrx | hrx
      · exact Or.inl hrx
      · exact Or.inr (by rw [← upd_other (f := st.env) (v := Val.owner st.next) hrx]; exact hr)
    have hnb : ∀ r, st.env r = .owner b → b ≠ st.next := by
      intro r hr; exact hnotnext b (h.liveOfEnv r b hr)
    rcases key p hp with hpx | hpe
    · rcases key q hq with hqx | hqe
      · rw [hpx, hqx]
      · rw [hpx, upd_same] at hp
        exact absurd (Val.owner.inj hp).symm (hnb q hqe)
    · rcases key q hq with hqx | hqe
      · rw [hqx, upd_same] at hq
        exact absurd (Val.owner.inj hq).symm (hnb p hpe)
      · exact h.uniq p q b hpe hqe
  · intro b hb0
    have hb : updL st.live st.next true b = true := hb0
    rcases nat_eq_or_ne b st.next with hbn | hbn
    · refine ⟨x, hx, ?_⟩
      show upd st.env x (Val.owner st.next) x = Val.owner b
      rw [hbn]; exact upd_same _ _ _
    · rw [updL_other hbn] at hb
      obtain ⟨p, hp, hpe⟩ := h.covered b hb
      have hpx : p ≠ x := fun hc => hnx b (hc ▸ hpe)
      refine ⟨p, hp, ?_⟩
      show upd st.env x (Val.owner st.next) p = Val.owner b
      rw [upd_other hpx]; exact hpe
  · intro b hb0
    have hb : updL st.live st.next true b = true := hb0
    rcases nat_eq_or_ne b st.next with hbn | hbn
    · show st.frees b = 0; rw [hbn]; exact hnf
    · rw [updL_other hbn] at hb; exact h.liveUnfreed b hb
  · intro b hb
    rcases nat_eq_or_ne b st.next with hbn | hbn
    · left; show updL st.live st.next true b = true; rw [hbn, updL_same]
    · have hlt : b < st.next := by
        rcases Nat.lt_or_ge b st.next with hh | hh
        · exact hh
        · exact absurd (Nat.le_antisymm (Nat.lt_succ_iff.mp hb) hh) hbn
      rcases h.freedOnce b hlt with hl | hf
      · left; show updL st.live st.next true b = true
        rw [updL_other hbn]; exact hl
      · exact Or.inr hf
  · intro b hb
    have hbn : b ≠ st.next := fun hc => by
      rw [hc] at hb; exact absurd hb (Nat.not_le.mpr (Nat.lt_succ_self _))
    have hge : st.next ≤ b := Nat.le_of_succ_le hb
    exact ⟨by show updL st.live st.next true b = false
              rw [updL_other hbn]; exact (h.beyond b hge).1,
           (h.beyond b hge).2⟩

theorem reallocAt_ok {st : State} {scope} (h : MemOk st scope) (x : Var) :
    ∃ st', reallocAt x st = .ok st' := by
  obtain ⟨st1, h1⟩ := release_ok h x
  exact ⟨_, by unfold reallocAt; rw [h1]⟩

theorem reallocAt_spec {st st' : State} {x : Var} (hr : reallocAt x st = .ok st') :
    ∃ st1, release x st = .ok st1 ∧
      st' = ⟨upd st1.env x (.owner st1.next), updL st1.live st1.next true,
             st1.next + 1, st1.frees⟩ := by
  unfold reallocAt at hr
  split at hr
  · exact absurd hr (by simp)
  · next st1 h1 => exact ⟨st1, h1, (Except.ok.inj hr).symm⟩

theorem memOk_reallocAt {st st' : State} {scope} {x : Var} (h : MemOk st scope)
    (hx : x ∈ scope) (hr : reallocAt x st = .ok st') : MemOk st' scope := by
  obtain ⟨st1, h1, he⟩ := reallocAt_spec hr
  rw [he]
  exact memOk_fresh (memOk_release h h1) hx (release_env_self h1)

theorem reallocAt_env_ne {st st' : State} {x p : Var} (hr : reallocAt x st = .ok st')
    (hne : p ≠ x) : st'.env p = st.env p := by
  obtain ⟨st1, h1, he⟩ := reallocAt_spec hr
  rw [he]
  show upd st1.env x _ p = _
  rw [upd_other hne]; exact release_env_ne h1 hne

theorem reallocAt_env_self {st st' : State} {x : Var} (hr : reallocAt x st = .ok st') :
    ∃ a, st'.env x = .owner a := by
  obtain ⟨st1, h1, he⟩ := reallocAt_spec hr
  exact ⟨st1.next, by rw [he]; exact upd_same _ _ _⟩

/-! ### Binding a value -/

theorem bindAt_ok {st : State} {scope} (h : MemOk st scope) (y : Var) (v : Val) :
    ∃ st', bindAt y v st = .ok st' := by
  obtain ⟨st1, h1⟩ := release_ok h y
  exact ⟨_, by unfold bindAt; rw [h1]⟩

theorem bindAt_spec {st st' : State} {y : Var} {v : Val} (hb : bindAt y v st = .ok st') :
    ∃ st1, release y st = .ok st1 ∧ st' = ⟨upd st1.env y v, st1.live, st1.next, st1.frees⟩ := by
  unfold bindAt at hb
  split at hb
  · exact absurd hb (by simp)
  · next st1 h1 => exact ⟨st1, h1, (Except.ok.inj hb).symm⟩

theorem bindAt_env_ne {st st' : State} {y p : Var} {v : Val} (hb : bindAt y v st = .ok st')
    (hne : p ≠ y) : st'.env p = st.env p := by
  obtain ⟨st1, h1, he⟩ := bindAt_spec hb
  rw [he]
  show upd st1.env y v p = _
  rw [upd_other hne]; exact release_env_ne h1 hne

theorem bindAt_env_self {st st' : State} {y : Var} {v : Val} (hb : bindAt y v st = .ok st') :
    st'.env y = v := by
  obtain ⟨st1, h1, he⟩ := bindAt_spec hb
  rw [he]; exact upd_same _ _ _

/-- Binding a value that is not a cell keeps the heap invariant. -/
theorem memOk_bindAt {st st' : State} {scope} {y : Var} {v : Val} (h : MemOk st scope)
    (hv : ∀ a, v ≠ .owner a) (hb : bindAt y v st = .ok st') : MemOk st' scope := by
  obtain ⟨st1, h1, he⟩ := bindAt_spec hb
  have h1' : MemOk st1 scope := memOk_release h h1
  have hy : ∀ a, (upd st1.env y v) y ≠ .owner a := by
    intro a; rw [upd_same]; exact hv a
  have hother : ∀ p, p ≠ y → (upd st1.env y v) p = st1.env p := fun p hp => upd_other hp
  rw [he]
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p b hp0
    have hp : (upd st1.env y v) p = .owner b := hp0
    rcases nat_eq_or_ne p y with hpy | hpy
    · exact absurd hp (hpy ▸ hy b)
    · rw [hother p hpy] at hp; exact h1'.liveOfEnv p b hp
  · intro p q b hp0 hq0
    have hp : (upd st1.env y v) p = .owner b := hp0
    have hq : (upd st1.env y v) q = .owner b := hq0
    rcases nat_eq_or_ne p y with hpy | hpy
    · exact absurd hp (hpy ▸ hy b)
    rcases nat_eq_or_ne q y with hqy | hqy
    · exact absurd hq (hqy ▸ hy b)
    rw [hother p hpy] at hp; rw [hother q hqy] at hq
    exact h1'.uniq p q b hp hq
  · intro b hb0
    obtain ⟨p, hp, hpe⟩ := h1'.covered b hb0
    have hpy : p ≠ y := fun hc => (release_env_self h1) b (hc ▸ hpe)
    refine ⟨p, hp, ?_⟩
    show upd st1.env y v p = Val.owner b
    rw [hother p hpy]; exact hpe
  · exact h1'.liveUnfreed
  · exact h1'.freedOnce
  · exact h1'.beyond

/-- Moving a cell from `x` to `y`: the cell changes place, so it stays covered
exactly once.  This is the one step where the intermediate state would break the
invariant, which is why it is proved in one piece. -/
theorem memOk_move {st st1 : State} {scope} {y x : Var} {a : AllocId} (h : MemOk st scope)
    (hy : y ∈ scope) (hxy : y ≠ x) (hx : st.env x = .owner a)
    (hb : bindAt y (st.env x) st = .ok st1) :
    MemOk ⟨upd st1.env x .moved, st1.live, st1.next, st1.frees⟩ scope := by
  obtain ⟨st2, h2r, he⟩ := bindAt_spec hb
  have hxny : x ≠ y := fun hc => hxy hc.symm
  have h2 : MemOk st2 scope := memOk_release h h2r
  have h2x : st2.env x = .owner a := by rw [release_env_ne h2r hxny]; exact hx
  have h2y : ∀ b, st2.env y ≠ .owner b := release_env_self h2r
  have hex : ∀ p, p ≠ x → (upd st1.env x Val.moved) p = st1.env p := fun p hp => upd_other hp
  have h1y : st1.env y = .owner a := by rw [he]; show upd st2.env y (st.env x) y = _
                                        rw [upd_same]; exact hx
  have h1o : ∀ p, p ≠ y → st1.env p = st2.env p := by
    intro p hp; rw [he]; show upd st2.env y (st.env x) p = _; rw [upd_other hp]
  have hey : (upd st1.env x Val.moved) y = .owner a := by rw [hex y hxy]; exact h1y
  have hkey : ∀ p b, (upd st1.env x Val.moved) p = .owner b → p = y ∧ b = a ∨
      (p ≠ x ∧ p ≠ y ∧ st2.env p = .owner b) := by
    intro p b hp
    rcases nat_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp; exact absurd hp (fun hc => Val.noConfusion hc)
    rcases nat_eq_or_ne p y with hpy | hpy
    · rw [hpy] at hp; rw [hey] at hp; exact Or.inl ⟨hpy, (Val.owner.inj hp).symm⟩
    · rw [hex p hpx, h1o p hpy] at hp; exact Or.inr ⟨hpx, hpy, hp⟩
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p b hp0
    have hlive1 : st1.live = st2.live := by rw [he]
    rcases hkey p b hp0 with ⟨_, hba⟩ | ⟨_, _, hpe⟩
    · show st1.live b = true
      rw [hlive1, hba]; exact h2.liveOfEnv x a h2x
    · show st1.live b = true
      rw [hlive1]; exact h2.liveOfEnv p b hpe
  · intro p q b hp0 hq0
    rcases hkey p b hp0 with ⟨hpy, hba⟩ | ⟨hpx, hpy, hpe⟩
    · rcases hkey q b hq0 with ⟨hqy, _⟩ | ⟨hqx, _, hqe⟩
      · rw [hpy, hqy]
      · exact absurd (h2.uniq q x b hqe (hba ▸ h2x)) hqx
    · rcases hkey q b hq0 with ⟨_, hba⟩ | ⟨hqx, _, hqe⟩
      · exact absurd (h2.uniq p x b hpe (hba ▸ h2x)) hpx
      · exact h2.uniq p q b hpe hqe
  · intro b hb0
    have hb2 : st2.live b = true := by rw [he] at hb0; exact hb0
    obtain ⟨p, hp, hpe⟩ := h2.covered b hb2
    rcases nat_eq_or_ne p y with hpy | hpy
    · exact absurd hpe (hpy ▸ h2y b)
    rcases nat_eq_or_ne p x with hpx | hpx
    · refine ⟨y, hy, ?_⟩
      have hba : b = a := by
        refine Val.owner.inj ?_
        rw [← hpe, hpx, h2x]
      show (upd st1.env x Val.moved) y = Val.owner b
      rw [hba]; exact hey
    · refine ⟨p, hp, ?_⟩
      show (upd st1.env x Val.moved) p = Val.owner b
      rw [hex p hpx, h1o p hpy]; exact hpe
  · intro b hb0
    have : st2.live b = true := by rw [he] at hb0; exact hb0
    show st1.frees b = 0
    rw [he]; exact h2.liveUnfreed b this
  · intro b hb0
    have hlt : b < st2.next := by rw [he] at hb0; exact hb0
    rcases h2.freedOnce b hlt with hl | hf
    · left; show st1.live b = true; rw [he]; exact hl
    · right; show st1.frees b = 1; rw [he]; exact hf
  · intro b hb0
    have hge : st2.next ≤ b := by rw [he] at hb0; exact hb0
    exact ⟨by show st1.live b = false; rw [he]; exact (h2.beyond b hge).1,
           by show st1.frees b = 0; rw [he]; exact (h2.beyond b hge).2⟩

/-! ### The implicit release at scope exit -/

theorem releaseAll_sound : ∀ (scope : List Var) (st : State), MemOk st scope →
    ∃ st', releaseAll scope st = .ok st' ∧ MemOk st' [] ∧ st'.next = st.next := by
  intro scope
  induction scope with
  | nil => intro st h; exact ⟨st, rfl, h, rfl⟩
  | cons p rest ih =>
      intro st h
      obtain ⟨st1, h1⟩ := release_ok h p
      have h1' : MemOk st1 rest :=
        memOk_tail (memOk_release h h1) (release_env_self h1)
      obtain ⟨st', hr, hfin, hnext⟩ := ih st1 h1'
      refine ⟨st', ?_, hfin, ?_⟩
      · show (match release p st with
              | .ok s => releaseAll rest s
              | .error e => .error e) = .ok st'
        rw [h1]; exact hr
      · rw [hnext]; exact release_next h1

theorem mem_scope_of_owner {st : State} {scope} (h : MemOk st scope) {p : Var} {a : AllocId}
    (hp : st.env p = .owner a) : p ∈ scope := by
  obtain ⟨q, hq, hqe⟩ := h.covered a (h.liveOfEnv p a hp)
  rw [← h.uniq q p a hqe hp]; exact hq

/-- After the implicit release nothing is live, and every cell the scope ever
allocated has been released exactly once. -/
theorem releaseAll_final {scope : List Var} {st st' : State} (h : MemOk st scope)
    (hr : releaseAll scope st = .ok st') :
    (∀ a, st'.live a = false) ∧ (∀ a, a < st'.next → st'.frees a = 1) := by
  obtain ⟨st'', hr'', hfin, _⟩ := releaseAll_sound scope st h
  have hst : st' = st'' := Except.ok.inj (hr.symm.trans hr'')
  subst hst
  have hdead : ∀ a, st'.live a = false := by
    intro a
    cases ha : st'.live a with
    | false => rfl
    | true =>
        obtain ⟨p, hp, _⟩ := hfin.covered a ha
        exact absurd hp (List.not_mem_nil)
  refine ⟨hdead, fun a hlt => ?_⟩
  rcases hfin.freedOnce a hlt with hl | hf
  · rw [hdead a] at hl; exact Bool.noConfusion hl
  · exact hf

/-! ## Conflict bookkeeping -/

theorem raceErr_none {ρ : Valuation} {tasks : List Task} {x : Borrow}
    (h : heldRace ρ tasks x = false) : raceErr ρ tasks x = none := by simp [raceErr, h]

theorem memErr_none {st : State} {scope} {p : Var} (hm : MemOk st scope)
    (h1 : st.env p ≠ .nil) (h2 : st.env p ≠ .moved) : memErr st p = none := by
  unfold memErr
  split
  · next heq => exact absurd heq h1
  · next heq => exact absurd heq h2
  · rfl
  · next a heq => rw [hm.liveOfEnv p a heq]; rfl

theorem accessErr_none {ρ : Valuation} {tasks : List Task} {st : State} {x : Borrow}
    (hr : raceErr ρ tasks x = none) (hm : memErr st x.1.base = none) :
    accessErr ρ tasks st x = none := by simp [accessErr, hr, hm]

theorem accessAll_none {ρ : Valuation} {tasks : List Task} {st : State} :
    ∀ args : List Borrow, (∀ x ∈ args, accessErr ρ tasks st x = none) →
      accessAll ρ tasks st args = none := by
  intro args
  induction args with
  | nil => intro _; rfl
  | cons x rest ih =>
      intro h
      rw [show accessAll ρ tasks st (x :: rest) =
            match accessErr ρ tasks st x with
            | some e => some e
            | none => accessAll ρ tasks st rest from rfl,
          h x (List.mem_cons_self ..)]
      exact ih fun y hy => h y (List.mem_cons_of_mem _ hy)

/-! ## Splitting the task list -/

theorem splits_mem {α : Type} : ∀ {l : List α} {a : α} {r : List α},
    (a, r) ∈ splits l → a ∈ l ∧ ∀ b ∈ r, b ∈ l := by
  intro l
  induction l with
  | nil => intro a r h; exact absurd h (by simp [splits])
  | cons a0 rest ih =>
      intro a r h
      rw [show splits (a0 :: rest) = (a0, rest) :: (splits rest).map
            (fun x => (x.1, a0 :: x.2)) from rfl] at h
      rcases List.mem_cons.mp h with h1 | h2
      · have ha : a = a0 := congrArg Prod.fst h1
        have hr : r = rest := congrArg Prod.snd h1
        exact ⟨by rw [ha]; exact List.mem_cons_self .., by
          intro b hb; rw [hr] at hb; exact List.mem_cons_of_mem _ hb⟩
      · obtain ⟨y, hy, hyeq⟩ := List.mem_map.mp h2
        have ha : a = y.1 := (congrArg Prod.fst hyeq).symm
        have hr : r = a0 :: y.2 := (congrArg Prod.snd hyeq).symm
        obtain ⟨hy1, hy2⟩ := ih hy
        refine ⟨by rw [ha]; exact List.mem_cons_of_mem _ hy1, ?_⟩
        intro b hb
        rw [hr] at hb
        rcases List.mem_cons.mp hb with hb1 | hb2
        · rw [hb1]; exact List.mem_cons_self ..
        · exact List.mem_cons_of_mem _ (hy2 b hb2)

theorem pairwise_splits {α : Type} {R : α → α → Prop} (hsym : ∀ a b, R a b → R b a) :
    ∀ {l : List α}, l.Pairwise R → ∀ {a : α} {r : List α}, (a, r) ∈ splits l → ∀ b ∈ r, R a b := by
  intro l
  induction l with
  | nil => intro _ a r h; exact absurd h (by simp [splits])
  | cons a0 rest ih =>
      intro hp a r h
      rw [show splits (a0 :: rest) = (a0, rest) :: (splits rest).map
            (fun x => (x.1, a0 :: x.2)) from rfl] at h
      have hph := List.pairwise_cons.mp hp
      rcases List.mem_cons.mp h with h1 | h2
      · have ha : a = a0 := congrArg Prod.fst h1
        have hr : r = rest := congrArg Prod.snd h1
        intro b hb
        rw [ha]; exact hph.1 b (hr ▸ hb)
      · obtain ⟨y, hy, hyeq⟩ := List.mem_map.mp h2
        have ha : a = y.1 := (congrArg Prod.fst hyeq).symm
        have hr : r = a0 :: y.2 := (congrArg Prod.snd hyeq).symm
        intro b hb
        rw [hr] at hb
        rcases List.mem_cons.mp hb with hb1 | hb2
        · rw [ha, hb1]
          exact hsym a0 y.1 (hph.1 y.1 (splits_mem hy).1)
        · rw [ha]; exact ih hph.2 hy b hb2

/-! ## The invariant

`Agree` is where the checker meets the machine: everything the checker claims
about a local is true of the state, and the lease map is exactly the list of live
tasks. -/

structure Agree (c : CState) (st : State) (tasks : List Task) : Prop where
  scalars_ok : ∀ p ∈ c.scalars, st.env p = .scalar
  owners_ok : ∀ p ∈ c.owners, ∃ a, st.env p = .owner a
  leases_ok : c.leases = tasks

theorem Agree.live_of_livePlace {c : CState} {st : State} {tasks} (h : Agree c st tasks)
    {p : Var} (hp : c.livePlace p = true) : st.env p ≠ .nil ∧ st.env p ≠ .moved := by
  simp only [CState.livePlace, Bool.or_eq_true, contains_iff_mem] at hp
  rcases hp with hs | ho
  · rw [h.scalars_ok p hs]; exact ⟨fun hc => Val.noConfusion hc, fun hc => Val.noConfusion hc⟩
  · obtain ⟨a, ha⟩ := h.owners_ok p ho
    rw [ha]; exact ⟨fun hc => Val.noConfusion hc, fun hc => Val.noConfusion hc⟩

/-- The local under every place a live task holds still holds something. -/
def TasksLive (tasks : List Task) (st : State) : Prop :=
  ∀ T ∈ tasks, ∀ y ∈ T.2, st.env y.1.base ≠ .nil ∧ st.env y.1.base ≠ .moved

/-- **Every part a live task holds was guarded.**  This is the invariant the
chaining rule needs: a lease on `d[lo..hi]` exists only because the spawner formed
that slice, and forming it traps unless `lo <= hi`.  It is what makes the facts the
checker chains through true of the numbers -- and it is the whole reason a spawn
performs the guard before it starts the task. -/
def TasksGuarded (ρ : Valuation) (tasks : List Task) : Prop :=
  ∀ T ∈ tasks, ∀ y ∈ T.2, y.1.guard ρ = true

/-- No two live threads race with each other. -/
def TasksOk (ρ : Valuation) (tasks : List Task) : Prop := tasks.Pairwise (NoRacePair ρ)

/-- The invariant an accepted program keeps.  The three clauses about live threads
range over the tasks AND the lanes together, which is the single statement that no
two of them conflict -- two tasks, a task and a lane, or two lanes.  A trap satisfies
the invariant vacuously: it is a defined abort, so nothing is claimed about the cells
it leaves behind. -/
def Ok (ρ : Valuation) (scope : List Var) : Cfg → Prop
  | .run code tasks lanes st =>
      MemOk st scope ∧ TasksOk ρ (tasks ++ lanes) ∧ TasksLive (tasks ++ lanes) st ∧
      TasksGuarded ρ (tasks ++ lanes) ∧
      ∃ c, c.scope = scope ∧ Agree c st tasks ∧
        ∃ d, checkBlock c code = some d ∧ d.leases = []
  | .done st => (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1)
  | .trap => True
  | .err _ => False

/-- The invariant of a configuration with no region running, spelled without the
empty tail: this is the shape every straight-line step produces. -/
theorem Ok_run_nil {ρ : Valuation} {scope : List Var} {code : List Stmt} {tasks : List Task}
    {st : State} :
    Ok ρ scope (.run code tasks [] st) ↔
      (MemOk st scope ∧ TasksOk ρ tasks ∧ TasksLive tasks st ∧ TasksGuarded ρ tasks ∧
        ∃ c, c.scope = scope ∧ Agree c st tasks ∧
          ∃ d, checkBlock c code = some d ∧ d.leases = []) := by
  simp only [Ok, List.append_nil]

/-- The guards of everything in play for one access: the lent parts by the
invariant, and the accessed place itself by the guard the machine just ran. -/
theorem inPlay_guarded {ρ : Valuation} {c : CState} {tasks : List Task} {x : Borrow}
    (hleases : c.leases = tasks) (hg : TasksGuarded ρ tasks) (hx : x.1.guard ρ = true) :
    ∀ p ∈ c.inPlay x, p.guard ρ = true := by
  intro p hp
  simp only [CState.inPlay, List.mem_append, List.mem_singleton] at hp
  rcases hp with hl | hs
  · rw [hleases] at hl
    simp only [lentPlaces, List.mem_flatMap] at hl
    obtain ⟨T, hT, hmem⟩ := hl
    obtain ⟨y, hy, hye⟩ := List.mem_map.mp hmem
    rw [← hye]; exact hg T hT y hy
  · rw [hs]; exact hx

/-- The lease rule, transported to the numbers: what the checker allows really does
not race. -/
theorem heldRace_none {ρ : Valuation} {c : CState} {tasks : List Task} {x : Borrow}
    (hleases : c.leases = tasks) (hg : TasksGuarded ρ tasks) (hx : x.1.guard ρ = true)
    (h : c.mayAccess x = true) : heldRace ρ tasks x = false := by
  have hfalse : heldConflict (c.inPlay x) c.leases x = false := by
    simpa [CState.mayAccess] using h
  rw [hleases] at hfalse
  exact heldRace_of_heldConflict (inPlay_guarded hleases hg hx) hfalse

theorem checkBlock_scope {c c' : CState} {ss : List Stmt} (h : checkBlock c ss = some c') :
    c'.scope = c.scope := by
  obtain ⟨_, _, _, hsc⟩ := checkBlock_mono (blockSize ss + 1) ss (Nat.lt_succ_self _) (Le.refl c) h
  exact hsc

theorem checkBlock_weaken {c d c' : CState} {ss : List Stmt} (hle : Le c d)
    (h : checkBlock c ss = some c') : ∃ d', checkBlock d ss = some d' ∧ Le c' d' := by
  obtain ⟨d', h1, h2, _⟩ := checkBlock_mono (blockSize ss + 1) ss (Nat.lt_succ_self _) hle h
  exact ⟨d', h1, h2⟩

theorem effOf_scope (c : CState) (s : Stmt) : (effOf c s).scope = c.scope := by
  cases s <;> rfl

theorem checkStmt_scope {c c1 : CState} {s : Stmt} (h : checkStmt c s = some c1) :
    c1.scope = c.scope := by
  refine checkBlock_scope (ss := [s]) ?_
  rw [checkBlock_cons, h]; rfl

/-! ## Preservation

Every successor of a configuration that satisfies the invariant satisfies it too.
Since `Ok` is `False` on error configurations, this is also what rules the faults
out. -/

/-- Writing a local: the checker's permission gives both halves at once -- no live
task holds any place of that local, so nothing races with the write and nothing a
task holds moves under it. -/
theorem write_ok {ρ : Valuation} {c : CState} {tasks : List Task} {x : Var}
    (hleases : c.leases = tasks) (hg : TasksGuarded ρ tasks) (h : c.mayWrite x = true) :
    heldRace ρ tasks (.whole ⟨x, []⟩, Mode.rw) = false ∧
      ∀ T ∈ tasks, ∀ y ∈ T.2, y.1.base ≠ x := by
  have hfalse : heldConflict (c.inPlay (.whole ⟨x, []⟩, Mode.rw)) c.leases
      (.whole ⟨x, []⟩, Mode.rw) = false := by
    simpa [CState.mayWrite, CState.mayAccess] using h
  have hguards := inPlay_guarded (x := (Place.whole ⟨x, []⟩, Mode.rw)) hleases hg rfl
  rw [hleases] at hfalse
  exact ⟨heldRace_of_heldConflict hguards hfalse, untouched_of_write_ok hfalse⟩

theorem Ok_succ {ρ : Valuation} {scope : List Var} {cfg cfg' : Cfg} (h : Ok ρ scope cfg)
    (hs : cfg' ∈ succ ρ scope cfg) : Ok ρ scope cfg' := by
  cases cfg with
  | done st => exact absurd hs (by simp [succ])
  | trap => exact absurd hs (by simp [succ])
  | err e => exact absurd hs (by simp [succ])
  | run code tasks lanes st =>
  simp only [Ok] at h
  obtain ⟨hmem, htok, htlive, htg, c, hscope, hag, d, hchk, hdl⟩ := h
  rcases List.mem_append.mp hs with hmain | htask
  · -- the main thread steps, which a running region blocks
    cases lanes with
    | cons L others =>
        -- the region completes; the lanes are gone and the spawner may go on
        simp only [stepHost, List.mem_singleton] at hmain
        rw [hmain]
        refine Ok_run_nil.mpr ⟨hmem, (List.pairwise_append.mp htok).1, ?_, ?_,
          c, hscope, hag, d, hchk, hdl⟩
        · intro T hT z hz; exact htlive T (List.mem_append_left _ hT) z hz
        · intro T hT z hz; exact htg T (List.mem_append_left _ hT) z hz
    | nil =>
    simp only [stepHost] at hmain
    rw [List.append_nil] at htok htlive htg
    cases code with
    | nil =>
        have hcd : c = d := Option.some.inj hchk
        have htasks : tasks = [] := by rw [← hag.leases_ok, hcd, hdl]
        subst htasks
        obtain ⟨st', hrel, _, _⟩ := releaseAll_sound scope st hmem
        simp only [stepMain, hrel, List.mem_singleton] at hmain
        rw [hmain]
        exact releaseAll_final hmem hrel
    | cons s rest =>
    rw [checkBlock_cons] at hchk
    cases hc1 : checkStmt c s with
    | none => rw [hc1] at hchk; exact absurd hchk (by simp)
    | some c1 =>
    rw [hc1] at hchk
    have hsc1 : c1.scope = scope := by rw [checkStmt_scope hc1]; exact hscope
    simp only [stepMain] at hmain
    -- the common shape of every straight-line step
    cases s with
    | alloc x =>
        rw [checkStmt_alloc] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
            have hx : x ∈ scope := hscope ▸ hg.1
            obtain ⟨hw, huntouched⟩ := write_ok hag.leases_ok htg hg.2
            obtain ⟨st', hre⟩ := reallocAt_ok hmem x
            simp only [stepStmt, raceErr_none hw, hre, List.mem_singleton] at hmain
            have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun p hp => reallocAt_env_ne hre hp
            rw [hmain]
            refine Ok_run_nil.mpr ⟨memOk_reallocAt hmem hx hre, htok, ?_, htg, _, hsc1,
              ⟨?_, ?_, hag.leases_ok⟩, d, hchk, hdl⟩
            · intro T hT y hy
              rw [hkeep y.1.base (huntouched T hT y hy)]; exact htlive T hT y hy
            · intro p hp
              simp only [effOf, mem_del] at hp
              rw [hkeep p hp.2]; exact hag.scalars_ok p hp.1
            · intro p hp
              simp only [effOf, mem_add] at hp
              rcases nat_eq_or_ne p x with hpx | hpx
              · rw [hpx]; exact reallocAt_env_self hre
              · rw [hkeep p hpx]
                exact hag.owners_ok p (hp.resolve_left hpx)
        · exact absurd hc1 (by simp)
    | mkScalar x =>
        rw [checkStmt_mkScalar] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
            obtain ⟨hw, huntouched⟩ := write_ok hag.leases_ok htg hg.2
            obtain ⟨st', hbe⟩ := bindAt_ok hmem x Val.scalar
            simp only [stepStmt, raceErr_none hw, hbe, List.mem_singleton] at hmain
            have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun p hp => bindAt_env_ne hbe hp
            rw [hmain]
            refine Ok_run_nil.mpr ⟨memOk_bindAt hmem (fun a hc => Val.noConfusion hc) hbe, htok,
              ?_, htg, _, hsc1, ⟨?_, ?_, hag.leases_ok⟩, d, hchk, hdl⟩
            · intro T hT y hy
              rw [hkeep y.1.base (huntouched T hT y hy)]; exact htlive T hT y hy
            · intro p hp
              simp only [effOf, mem_add] at hp
              rcases nat_eq_or_ne p x with hpx | hpx
              · rw [hpx]; exact bindAt_env_self hbe
              · rw [hkeep p hpx]; exact hag.scalars_ok p (hp.resolve_left hpx)
            · intro p hp
              simp only [effOf, mem_del] at hp
              rw [hkeep p hp.2]; exact hag.owners_ok p hp.1
        · exact absurd hc1 (by simp)
    | copy y x =>
        rw [checkStmt_copy] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, contains_iff_mem, Bool.not_eq_true',
              beq_eq_false_iff_ne] at hg
            have hyx : y ≠ x := hg.1.1.1.2
            have hxs : st.env x = Val.scalar := hag.scalars_ok x hg.1.1.2
            have hro : heldRace ρ tasks (.whole ⟨x, []⟩, Mode.ro) = false :=
              heldRace_none hag.leases_ok htg rfl hg.1.2
            obtain ⟨hwy, huntouched⟩ := write_ok hag.leases_ok htg hg.2
            have haccx : accessErr ρ tasks st (.whole ⟨x, []⟩, Mode.ro) = none :=
              accessErr_none (raceErr_none hro)
                (memErr_none hmem
                  (by show st.env x ≠ Val.nil; rw [hxs]; exact fun hc => Val.noConfusion hc)
                  (by show st.env x ≠ Val.moved; rw [hxs]; exact fun hc => Val.noConfusion hc))
            obtain ⟨st', hbe⟩ := bindAt_ok hmem y (st.env x)
            simp only [stepStmt, haccx, raceErr_none hwy, hbe, List.mem_singleton] at hmain
            have hkeep : ∀ p, p ≠ y → st'.env p = st.env p := fun p hp => bindAt_env_ne hbe hp
            rw [hmain]
            refine Ok_run_nil.mpr
              ⟨memOk_bindAt hmem (fun a hc => by rw [hxs] at hc; exact Val.noConfusion hc) hbe,
               htok, ?_, htg, _, hsc1, ⟨?_, ?_, hag.leases_ok⟩, d, hchk, hdl⟩
            · intro T hT z hz
              rw [hkeep z.1.base (huntouched T hT z hz)]; exact htlive T hT z hz
            · intro p hp
              simp only [effOf, mem_add] at hp
              rcases nat_eq_or_ne p y with hpy | hpy
              · rw [hpy, bindAt_env_self hbe]; exact hxs
              · rw [hkeep p hpy]; exact hag.scalars_ok p (hp.resolve_left hpy)
            · intro p hp
              simp only [effOf, mem_del] at hp
              rw [hkeep p hp.2]; exact hag.owners_ok p hp.1
        · exact absurd hc1 (by simp)
    | move y x =>
        rw [checkStmt_move] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, contains_iff_mem, Bool.not_eq_true',
              beq_eq_false_iff_ne] at hg
            have hy : y ∈ scope := hscope ▸ hg.1.1.1.1
            have hyx : y ≠ x := hg.1.1.1.2
            obtain ⟨a, hxa⟩ := hag.owners_ok x hg.1.1.2
            obtain ⟨hwx, hux⟩ := write_ok hag.leases_ok htg hg.1.2
            obtain ⟨hwy, huy⟩ := write_ok hag.leases_ok htg hg.2
            have haccx : accessErr ρ tasks st (.whole ⟨x, []⟩, Mode.rw) = none :=
              accessErr_none (raceErr_none hwx)
                (memErr_none hmem
                  (by show st.env x ≠ Val.nil; rw [hxa]; exact fun hc => Val.noConfusion hc)
                  (by show st.env x ≠ Val.moved; rw [hxa]; exact fun hc => Val.noConfusion hc))
            obtain ⟨st1, hbe⟩ := bindAt_ok hmem y (st.env x)
            simp only [stepStmt, haccx, raceErr_none hwy, hbe, List.mem_singleton] at hmain
            have h1y : st1.env y = Val.owner a := by rw [bindAt_env_self hbe]; exact hxa
            have hey : (upd st1.env x Val.moved) y = Val.owner a := by
              rw [upd_other hyx]; exact h1y
            have hkeep : ∀ p, p ≠ x → p ≠ y → (upd st1.env x Val.moved) p = st.env p := by
              intro p hpx hpy
              rw [upd_other hpx, bindAt_env_ne hbe hpy]
            rw [hmain]
            refine Ok_run_nil.mpr ⟨memOk_move hmem hy hyx hxa hbe, htok, ?_, htg, _, hsc1,
              ⟨?_, ?_, hag.leases_ok⟩, d, hchk, hdl⟩
            · intro T hT z hz
              have hzz : (upd st1.env x Val.moved) z.1.base = st.env z.1.base :=
                hkeep z.1.base (hux T hT z hz) (huy T hT z hz)
              show (upd st1.env x Val.moved) z.1.base ≠ Val.nil ∧
                   (upd st1.env x Val.moved) z.1.base ≠ Val.moved
              rw [hzz]; exact htlive T hT z hz
            · intro p hp
              simp only [effOf, mem_del] at hp
              have hps := hag.scalars_ok p hp.1
              have hpx : p ≠ x := by
                intro hc; rw [hc, hxa] at hps; exact Val.noConfusion hps
              show (upd st1.env x Val.moved) p = Val.scalar
              rw [hkeep p hpx hp.2]; exact hps
            · intro p hp
              simp only [effOf, mem_add, mem_del] at hp
              rcases nat_eq_or_ne p y with hpy | hpy
              · exact ⟨a, by rw [hpy]; exact hey⟩
              · have hp' := hp.resolve_left hpy
                obtain ⟨b, hb⟩ := hag.owners_ok p hp'.1
                refine ⟨b, ?_⟩
                show (upd st1.env x Val.moved) p = Val.owner b
                rw [hkeep p hp'.2 hpy]; exact hb
        · exact absurd hc1 (by simp)
    | drop x =>
        rw [checkStmt_drop] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
            obtain ⟨a, hxa⟩ := hag.owners_ok x hg.1
            obtain ⟨hw, huntouched⟩ := write_ok hag.leases_ok htg hg.2
            have haccx : accessErr ρ tasks st (.whole ⟨x, []⟩, Mode.rw) = none :=
              accessErr_none (raceErr_none hw)
                (memErr_none hmem
                  (by show st.env x ≠ Val.nil; rw [hxa]; exact fun hc => Val.noConfusion hc)
                  (by show st.env x ≠ Val.moved; rw [hxa]; exact fun hc => Val.noConfusion hc))
            obtain ⟨st', hre⟩ := release_ok hmem x
            simp only [stepStmt, haccx, hre, List.mem_singleton] at hmain
            have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun p hp => release_env_ne hre hp
            rw [hmain]
            refine Ok_run_nil.mpr ⟨memOk_release hmem hre, htok, ?_, htg, _, hsc1,
              ⟨?_, ?_, hag.leases_ok⟩, d, hchk, hdl⟩
            · intro T hT z hz
              rw [hkeep z.1.base (huntouched T hT z hz)]; exact htlive T hT z hz
            · intro p hp
              have hps := hag.scalars_ok p hp
              have hpx : p ≠ x := by
                intro hcc; rw [hcc, hxa] at hps; exact Val.noConfusion hps
              rw [hkeep p hpx]; exact hps
            · intro p hp
              simp only [effOf, mem_del] at hp
              rw [hkeep p hp.2]; exact hag.owners_ok p hp.1
        · exact absurd hc1 (by simp)
    | call args =>
        rw [checkStmt_call] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
            cases hguard : argsGuarded ρ args with
            | false =>
                simp only [stepStmt, hguard] at hmain
                simp at hmain
                rw [hmain]; trivial
            | true =>
                have hga : ∀ z ∈ args, z.1.guard ρ = true := by
                  intro z hz
                  exact (List.all_eq_true.mp hguard) z hz
                have hpairs : pairsOkAt ρ args = true :=
                  pairsOk_sound (ρ := ρ)
                    (fun p hp => by
                      obtain ⟨z, hz, hze⟩ := List.mem_map.mp hp
                      rw [← hze]; exact hga z hz)
                    args hg.1
                have hacc : ∀ z ∈ args, accessErr ρ tasks st z = none := by
                  intro z hz
                  have hz' := hg.2 z hz
                  refine accessErr_none
                    (raceErr_none (heldRace_none hag.leases_ok htg (hga z hz) hz'.2)) ?_
                  obtain ⟨h1, h2⟩ := hag.live_of_livePlace hz'.1
                  exact memErr_none hmem h1 h2
                simp only [stepStmt, hguard, hpairs, accessAll_none args hacc] at hmain
                simp at hmain
                rw [hmain]
                exact Ok_run_nil.mpr ⟨hmem, htok, htlive, htg, _, hsc1,
                  ⟨hag.scalars_ok, hag.owners_ok, hag.leases_ok⟩, d, hchk, hdl⟩
        · exact absurd hc1 (by simp)
    | spawn t args =>
        rw [checkStmt_spawn] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
            cases hguard : argsGuarded ρ args with
            | false =>
                simp only [stepStmt, hguard] at hmain
                simp at hmain
                rw [hmain]; trivial
            | true =>
                have hga : ∀ z ∈ args, z.1.guard ρ = true := by
                  intro z hz
                  exact (List.all_eq_true.mp hguard) z hz
                have hpairs : pairsOkAt ρ args = true :=
                  pairsOk_sound (ρ := ρ)
                    (fun p hp => by
                      obtain ⟨z, hz, hze⟩ := List.mem_map.mp hp
                      rw [← hze]; exact hga z hz)
                    args hg.1.1
                have hfree : ∀ z ∈ args, heldRace ρ tasks z = false := by
                  intro z hz
                  exact heldRace_none hag.leases_ok htg (hga z hz) (hg.2 z hz).2
                have hacc : ∀ z ∈ args, accessErr ρ tasks st z = none := by
                  intro z hz
                  refine accessErr_none (raceErr_none (hfree z hz)) ?_
                  obtain ⟨h1, h2⟩ := hag.live_of_livePlace (hg.2 z hz).1
                  exact memErr_none hmem h1 h2
                simp only [stepStmt, hguard, hpairs, accessAll_none args hacc] at hmain
                simp at hmain
                rw [hmain]
                refine Ok_run_nil.mpr ⟨hmem, ?_, ?_, ?_, _, hsc1,
                  ⟨hag.scalars_ok, hag.owners_ok, ?_⟩, d, hchk, hdl⟩
                · refine List.pairwise_cons.mpr ⟨?_, htok⟩
                  intro U hU z hz w hw
                  exact heldRace_eq_false_iff.mp (hfree z hz) U hU w hw
                · intro T hT z hz
                  rcases List.mem_cons.mp hT with hT1 | hT2
                  · have hz' := hg.2 z (by rw [hT1] at hz; exact hz)
                    exact hag.live_of_livePlace hz'.1
                  · exact htlive T hT2 z hz
                · intro T hT z hz
                  rcases List.mem_cons.mp hT with hT1 | hT2
                  · exact hga z (by rw [hT1] at hz; exact hz)
                  · exact htg T hT2 z hz
                · show (t, args) :: c.leases = (t, args) :: tasks
                  rw [hag.leases_ok]
        · exact absurd hc1 (by simp)
    | wait t =>
        rw [checkStmt_wait] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [stepStmt, List.mem_singleton] at hmain
            rw [hmain]
            refine Ok_run_nil.mpr ⟨hmem, List.Pairwise.sublist List.filter_sublist htok, ?_, ?_,
              _, hsc1, ⟨hag.scalars_ok, hag.owners_ok, ?_⟩, d, hchk, hdl⟩
            · intro T hT z hz
              exact htlive T (List.mem_filter.mp hT).1 z hz
            · intro T hT z hz
              exact htg T (List.mem_filter.mp hT).1 z hz
            · show c.leases.filter _ = tasks.filter _
              rw [hag.leases_ok]
        · exact absurd hc1 (by simp)
    | ite thn els =>
        rw [checkStmt_ite] at hc1
        split at hc1
        · next a1 a2 h1 h2 =>
            unfold joinOf at hc1
            split at hc1
            · next hleq =>
                have hc1v := Option.some.inj hc1
                have hle1 : Le c1 a1 := by
                  refine ⟨?_, ?_, ?_, ?_⟩
                  · rw [← hc1v]; exact (checkBlock_scope h1).symm
                  · intro p hp; rw [← hc1v] at hp; exact (mem_keepIn.mp hp).1
                  · intro p hp; rw [← hc1v] at hp; exact (mem_keepIn.mp hp).1
                  · rw [← hc1v]
                have hleases : a1.leases = a2.leases := of_decide_eq_true (by simpa using hleq)
                have hle2 : Le c1 a2 := by
                  refine ⟨?_, ?_, ?_, ?_⟩
                  · rw [← hc1v]; exact (checkBlock_scope h2).symm
                  · intro p hp; rw [← hc1v] at hp; exact (mem_keepIn.mp hp).2
                  · intro p hp; rw [← hc1v] at hp; exact (mem_keepIn.mp hp).2
                  · rw [← hc1v]; exact hleases
                simp only [stepStmt, List.mem_cons, List.not_mem_nil, or_false] at hmain
                have hbranch : ∀ (a : CState) (br : List Stmt), Le c1 a →
                    checkBlock c br = some a →
                    Ok ρ scope (.run (br ++ rest) tasks [] st) := by
                  intro a br hle hbr
                  obtain ⟨d', hd', hled⟩ := checkBlock_weaken hle hchk
                  exact Ok_run_nil.mpr ⟨hmem, htok, htlive, htg, c, hscope, hag, d',
                    by rw [checkBlock_append, hbr]; exact hd',
                    by rw [← hled.leases_eq]; exact hdl⟩
                rcases hmain with hm | hm
                · rw [hm]; exact hbranch a1 thn hle1 h1
                · rw [hm]; exact hbranch a2 els hle2 h2
            · exact absurd hc1 (by simp)
        · exact absurd hc1 (by simp)
    | parallel nb body =>
        rw [checkStmt_parallel] at hc1
        split at hc1
        · next hg =>
            have hc1v := Option.some.inj hc1
            subst hc1v
            simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
            simp only [stepStmt, List.mem_singleton] at hmain
            have hbody : ∀ a ∈ body, c.livePlace a.root.var = true ∧ c.mayAccess a.lease = true :=
              hg.2
            have hfree : ∀ a ∈ body, heldRace ρ tasks a.lease = false := fun a ha =>
              heldRace_none hag.leases_ok htg (Touch.lease_guard ρ a) (hbody a ha).2
            rw [hmain]
            refine ⟨hmem, ?_, ?_, ?_, _, hsc1,
              ⟨hag.scalars_ok, hag.owners_ok, hag.leases_ok⟩, d, hchk, hdl⟩
            · refine List.pairwise_append.mpr ⟨htok, lanesOf_pairwise ρ _ hg.1, ?_⟩
              intro T hT L hL x hx w hw
              obtain ⟨a, ha, hae⟩ := mem_lanesOf _ hL hw
              rw [hae, races_symm]
              exact races_borrow_of_lease
                (heldRace_eq_false_iff.mp (hfree a ha) T hT x hx)
            · intro T hT w hw
              rcases List.mem_append.mp hT with h1 | h2
              · exact htlive T h1 w hw
              · obtain ⟨a, ha, hae⟩ := mem_lanesOf _ h2 hw
                rw [hae, Touch.borrow_base]
                exact hag.live_of_livePlace (hbody a ha).1
            · intro T hT w hw
              rcases List.mem_append.mp hT with h1 | h2
              · exact htg T h1 w hw
              · obtain ⟨a, ha, hae⟩ := mem_lanesOf _ h2 hw
                rw [hae]
                exact Touch.borrow_guard ρ a T.1
        · exact absurd hc1 (by simp)
  · -- one live thread steps: a task, or a lane of the region that is running
    simp only [List.mem_flatMap] at htask
    obtain ⟨y, hy, hcfg⟩ := htask
    obtain ⟨hT, hothers⟩ := splits_mem hy
    simp only [stepThread, List.mem_flatMap] at hcfg
    obtain ⟨z, hz, hcfg2⟩ := hcfg
    have hnc : heldRace ρ y.2 z = false := by
      rw [heldRace_eq_false_iff]
      intro U hU w hw
      exact pairwise_splits (noRacePair_symm ρ) htok hy U hU z hz w hw
    obtain ⟨hl1, hl2⟩ := htlive y.1 hT z hz
    have hacc : accessErr ρ y.2 st z = none :=
      accessErr_none (raceErr_none hnc) (memErr_none hmem hl1 hl2)
    simp only [hacc] at hcfg2
    have hsame : Ok ρ scope (Cfg.run code tasks lanes st) :=
      ⟨hmem, htok, htlive, htg, c, hscope, hag, d, hchk, hdl⟩
    cases hzm : z.2 with
    | ro =>
        simp only [hzm, List.mem_singleton] at hcfg2
        rw [hcfg2]; exact hsame
    | rw =>
        simp only [hzm] at hcfg2
        unfold taskWrite at hcfg2
        split at hcfg2
        · next p hp =>
            -- the borrow is the whole owner: the thread may replace the cell
            rw [hp] at hl1 hl2
            split at hcfg2
            · next a hea =>
                have hsp : p ∈ scope := mem_scope_of_owner hmem hea
                obtain ⟨st', hre⟩ := reallocAt_ok hmem p
                simp only [hre, List.mem_cons, List.not_mem_nil, or_false] at hcfg2
                rcases hcfg2 with hm | hm
                · rw [hm]; exact hsame
                · rw [hm]
                  have hkeep : ∀ q, q ≠ p → st'.env q = st.env q :=
                    fun q hq => reallocAt_env_ne hre hq
                  refine ⟨memOk_reallocAt hmem hsp hre, htok, ?_, htg, c, hscope,
                    ⟨?_, ?_, hag.leases_ok⟩, d, hchk, hdl⟩
                  · intro U hU w hw
                    rcases nat_eq_or_ne w.1.base p with hwp | hwp
                    · obtain ⟨b, hb⟩ := reallocAt_env_self hre
                      rw [hwp, hb]
                      exact ⟨fun hc => Val.noConfusion hc, fun hc => Val.noConfusion hc⟩
                    · rw [hkeep w.1.base hwp]; exact htlive U hU w hw
                  · intro q hq
                    have hqs := hag.scalars_ok q hq
                    have hqp : q ≠ p := by
                      intro hcc; rw [hcc, hea] at hqs; exact Val.noConfusion hqs
                    rw [hkeep q hqp]; exact hqs
                  · intro q hq
                    rcases nat_eq_or_ne q p with hqp | hqp
                    · rw [hqp]; exact reallocAt_env_self hre
                    · rw [hkeep q hqp]; exact hag.owners_ok q hq
            · next hea =>
                exact absurd (show st.env (Place.whole ⟨p, []⟩).base = Val.nil from hea) hl1
            · next hea =>
                exact absurd (show st.env (Place.whole ⟨p, []⟩).base = Val.moved from hea) hl2
            · next hea =>
                simp only [List.mem_singleton] at hcfg2
                rw [hcfg2]; exact hsame
        · simp only [List.mem_singleton] at hcfg2
          rw [hcfg2]; exact hsame
        · simp only [List.mem_singleton] at hcfg2
          rw [hcfg2]; exact hsame
        · simp only [List.mem_singleton] at hcfg2
          rw [hcfg2]; exact hsame
        · simp only [List.mem_singleton] at hcfg2
          rw [hcfg2]; exact hsame

/-! ## Soundness -/

theorem memOk_start (scope : List Var) : MemOk State.start scope := by
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p a hp; exact Val.noConfusion hp
  · intro p q a hp _; exact Val.noConfusion hp
  · intro a ha; exact Bool.noConfusion ha
  · intro a ha; exact Bool.noConfusion ha
  · intro a ha; exact absurd ha (Nat.not_lt_zero _)
  · intro a _; exact ⟨rfl, rfl⟩

theorem Ok_start {p : Program} {ρ : Valuation} (h : accepts p = true) :
    Ok ρ p.scope (Cfg.start p) := by
  unfold accepts at h
  split at h
  · next c hc =>
      refine ⟨memOk_start p.scope, List.Pairwise.nil, fun T hT => absurd hT List.not_mem_nil,
        fun T hT => absurd hT List.not_mem_nil,
        CState.start p, rfl, ⟨fun q hq => absurd hq List.not_mem_nil,
          fun q hq => absurd hq List.not_mem_nil, rfl⟩, c, hc, ?_⟩
      exact List.eq_nil_of_length_eq_zero (by simpa using h)
  · exact Bool.noConfusion h

theorem Ok_reach {ρ : Valuation} {scope : List Var} : ∀ {a b : Cfg},
    Reach ρ scope a b → Ok ρ scope a → Ok ρ scope b := by
  intro a b hr
  induction hr with
  | refl c => exact fun h => h
  | step hm _ ih => exact fun h => ih (Ok_succ h hm)

/-- **Soundness.**  No execution of an accepted program, under any interleaving of
the spawner with its tasks and under EVERY valuation of the immutable part bounds,
reaches any fault: no use of a moved place, no use of a released cell, no double
free, no leaked ticket, no data race and no aliased call arguments.  A trap -- the
defined abort of a slice whose bounds are backwards -- is not a fault and is not
excluded. -/
theorem accepted_no_fault {p : Program} (hp : accepts p = true) {ρ : Valuation} {cfg : Cfg}
    (hr : Reach ρ p.scope (Cfg.start p) cfg) (e : Err) : cfg ≠ Cfg.err e := by
  intro hc
  have hok := Ok_reach hr (Ok_start hp)
  rw [hc] at hok
  exact hok

/-- No reachable state uses a local whose owner has moved away. -/
theorem accepted_no_use_after_move {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (q : Var) :
    cfg ≠ Cfg.err (.useAfterMove q) :=
  accepted_no_fault hp hr _

/-- No reachable state touches a cell that has been released. -/
theorem accepted_no_use_after_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (a : AllocId) :
    cfg ≠ Cfg.err (.useAfterFree a) :=
  accepted_no_fault hp hr _

/-- No cell is released twice. -/
theorem accepted_no_double_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (a : AllocId) :
    cfg ≠ Cfg.err (.doubleFree a) :=
  accepted_no_fault hp hr _

/-- **Race freedom**, over the full interleaving and for every valuation: no
reachable state has the spawner and a task, or two tasks, touching a common
location of one local with a write among them.  For two parts of one array that is
"their index ranges meet under the valuation", which is the question the checker
answers syntactically by chaining the guarded bounds. -/
theorem accepted_race_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (q : Var) :
    cfg ≠ Cfg.err (.race q) :=
  accepted_no_fault hp hr _

/-- No ticket is still live where the scope ends. -/
theorem accepted_no_leaked_ticket {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (t : Ticket) :
    cfg ≠ Cfg.err (.leak t) :=
  accepted_no_fault hp hr _

/-- No call ever receives two overlapping borrows with a write among them -- where
"overlapping" is again the real footprints under the valuation. -/
theorem accepted_no_aliased_args {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) : cfg ≠ Cfg.err .aliasedArgs :=
  accepted_no_fault hp hr _

/-- **The live threads never conflict.**  In every reachable configuration of an
accepted program, the tasks and the lanes of a running region are pairwise
compatible: no two of them -- two tasks, a task and a lane, or two lanes -- hold
borrows that touch a common location with a write among them, under every
interleaving and every valuation.  `accepted_race_free` is what happens when one of
them tries; this is the standing property that makes it impossible. -/
theorem accepted_threads_disjoint {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {code : List Stmt} {tasks lanes : List Task} {st : State}
    (hr : Reach ρ p.scope (Cfg.start p) (.run code tasks lanes st)) :
    (tasks ++ lanes).Pairwise (NoRacePair ρ) :=
  (Ok_reach hr (Ok_start hp)).2.1

/-- **Every allocation is released exactly once.**  On normal termination nothing
is still live and every cell the run ever allocated has been released once.  A run
that traps makes no such claim: the process aborted. -/
theorem accepted_frees_each_allocation_once {p : Program} (hp : accepts p = true)
    {ρ : Valuation} {st : State} (hr : Reach ρ p.scope (Cfg.start p) (Cfg.done st)) :
    (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1) :=
  Ok_reach hr (Ok_start hp)

/-! ## Progress

The machine is total by construction: every statement has at least one successor,
and so does the end of the body.  What progress adds to that is the other half --
that a reachable configuration of an accepted program is never an error -- which is
`accepted_no_fault` above. -/

theorem stepStmt_ne_nil (ρ : Valuation) (s : Stmt) (rest : List Stmt) (tasks : List Task)
    (st : State) : stepStmt ρ s rest tasks st ≠ [] := by
  cases s <;> simp only [stepStmt] <;> (repeat' split) <;> exact List.cons_ne_nil _ _

theorem stepMain_ne_nil (ρ : Valuation) (scope : List Var) (code : List Stmt)
    (tasks : List Task) (st : State) : stepMain ρ scope code tasks st ≠ [] := by
  unfold stepMain
  split
  · split
    · exact List.cons_ne_nil _ _
    · split <;> exact List.cons_ne_nil _ _
  · exact stepStmt_ne_nil ρ _ _ tasks st

theorem stepHost_ne_nil (ρ : Valuation) (scope : List Var) (code : List Stmt)
    (tasks lanes : List Task) (st : State) : stepHost ρ scope code tasks lanes st ≠ [] := by
  unfold stepHost
  split
  · exact stepMain_ne_nil ρ scope code tasks st
  · exact List.cons_ne_nil _ _

theorem succ_run_ne_nil (ρ : Valuation) (scope : List Var) (code : List Stmt)
    (tasks lanes : List Task) (st : State) : succ ρ scope (.run code tasks lanes st) ≠ [] := by
  show (stepHost ρ scope code tasks lanes st ++ _) ≠ []
  cases hm : stepHost ρ scope code tasks lanes st with
  | nil => exact absurd hm (stepHost_ne_nil ρ scope code tasks lanes st)
  | cons a l => rw [List.cons_append]; exact List.cons_ne_nil _ _

/-- Progress: an accepted program never gets stuck.  Every reachable configuration
has finished -- normal termination, or the defined abort of a failed bounds guard --
or has a successor. -/
def Progress : Prop :=
  ∀ (p : Program) (ρ : Valuation), accepts p = true →
    ∀ cfg, Reach ρ p.scope (Cfg.start p) cfg →
      (∃ st, cfg = Cfg.done st) ∨ cfg = Cfg.trap ∨ succ ρ p.scope cfg ≠ []

/-- **Progress**, proved.  The error configurations are the only stuck ones, and
`accepted_no_fault` says an accepted program never reaches one. -/
theorem accepted_progress : Progress := by
  intro p ρ hp cfg hr
  cases cfg with
  | done st => exact Or.inl ⟨st, rfl⟩
  | trap => exact Or.inr (Or.inl rfl)
  | err e => exact absurd rfl (accepted_no_fault hp hr e)
  | run code tasks lanes st =>
      exact Or.inr (Or.inr (succ_run_ne_nil ρ p.scope code tasks lanes st))

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
the elements, and the owner's length is not one of them. -/
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

/-! ### Fields

`checking.py:path` writes a field path `r.a`, and `overlaps` compares two bases by
dotted prefix: two fields of one record are apart, a field and its record are not,
and a part of an array held in a field chains exactly as one of a plain local.

Reaching a field, though, READS the record: `checking.py:e_name` runs
`leased(box, "ro")` on the base local before `overlaps` ever sees `box.a`.  So the
disjointness of two fields only ever bites inside ONE argument list, where no base
read intervenes; across a live task lease, naming any field of a record one field of
which is lent is `E-LEASED`.  `readBase` below is that read, written out, and it is
why `fieldsToTwoTasks` is rejected although `box.a` and `box.b` are disjoint. -/

/-- The read of the base local that naming any field of `box` performs. -/
def readBase : Stmt := call [(.whole box, Mode.ro)]

/-- `let t = spawn fill(len(box.a), box.a, 0); wait(t);` -- accepted. -/
def oneFieldToATask : Program :=
  ⟨[4], [alloc 4, readBase, spawn 0 [(.elems boxA, Mode.rw)], wait 0]⟩

/-- `let l = spawn fill(..., box.a, 0); let r = spawn fill(..., box.b, 1);` --
E-LEASED, and NOT because the two fields overlap: naming `box.b` reads `box`, which
is lent. -/
def fieldsToTwoTasks : Program :=
  ⟨[4], [alloc 4, readBase, spawn 0 [(.elems boxA, Mode.rw)],
         readBase, spawn 1 [(.elems boxB, Mode.rw)], wait 0, wait 1]⟩

/-- `both(6, box.xs[0..6], box.a[3..9])` -- accepted: inside one argument list two
fields really are disjoint, whatever their bounds. -/
def partsOfTwoFieldsInOneCall : Program :=
  ⟨[4], [alloc 4, readBase, call [(.part boxXs (.lit 0) (.lit 6), Mode.rw),
                                  (.part boxA (.lit 3) (.lit 9), Mode.ro)]]⟩

/-- `both(4, box.xs[0..4], box.xs[4..8])` -- accepted: the part chain works inside a
field exactly as it does on a local. -/
def fieldPartsSplitInOneCall : Program :=
  ⟨[4], [alloc 4, readBase, call [(.part boxXs (.lit 0) (.lit 4), Mode.rw),
                                  (.part boxXs (.lit 4) (.lit 8), Mode.ro)]]⟩

/-- `both(6, box.xs[0..6], box.xs[3..9])` -- E-ALIAS: they really overlap. -/
def fieldPartsOverlapInOneCall : Program :=
  ⟨[4], [alloc 4, readBase, call [(.part boxXs (.lit 0) (.lit 6), Mode.rw),
                                  (.part boxXs (.lit 3) (.lit 9), Mode.ro)]]⟩

/-- `both(len(box.a), box.a, box.a)` -- E-ALIAS: one field is not two. -/
def sameFieldTwiceInOneCall : Program :=
  ⟨[4], [alloc 4, readBase, call [(.elems boxA, Mode.rw), (.elems boxA, Mode.ro)]]⟩

/-- `let t = spawn fill(..., box.a, 0); let moved = box;` -- E-LEASED: a field is
inside its record, so a lease on the field pins the record. -/
def fieldMoveUnderLease : Program :=
  ⟨[1, 4], [alloc 4, readBase, spawn 0 [(.elems boxA, Mode.rw)], move 1 4, wait 0]⟩

/-- `let t = spawn bump(box); let s = sum(len(box.a), box.a);` -- E-LEASED the other
way: the record is lent whole, so every field of it is. -/
def fieldReadUnderRecordLease : Program :=
  ⟨[4], [alloc 4, spawn 0 [(.whole box, Mode.rw)], readBase,
         call [(.elems boxA, Mode.ro)], wait 0]⟩

/-- `let t = spawn bump(box); let k = len(box.xs);` -- E-LEASED. -/
def lenOfFieldUnderRecordLease : Program :=
  ⟨[4], [alloc 4, spawn 0 [(.whole box, Mode.rw)], readBase,
         call [(.hdr boxXs, Mode.ro)], wait 0]⟩

/-- `let t = spawn fill(len(box.xs), box.xs, 1); let k = len(box.xs);` -- E-LEASED,
again by the base read: the header of a field is not one of its elements, but
naming the field reads the record the task's view lives in. -/
def lenOfFieldUnderElementLease : Program :=
  ⟨[4], [alloc 4, readBase, spawn 0 [(.elems boxXs, Mode.rw)], readBase,
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

/-- Every line of the regression, as one Boolean the build can print. -/
def report : Bool :=
  accepts moveOnce && !accepts doubleMove && !accepts leasedRead && accepts sharedRead
    && !accepts moveBesideView && !accepts unawaitedTicket && !accepts aliasedCall
    && !accepts movedOnOnePath && accepts movedOnBothPaths && !accepts ticketsDisagree
    && accepts twoOwners && accepts disjointSplit && accepts kwaySplit
    && !accepts partsWithoutMiddle && !accepts overlappingParts && !accepts overlappingArgs
    && accepts backwardsPart && accepts lenUnderElementLease && !accepts lenUnderOwnerLease
    && !accepts overlappingTasks && !accepts copyAnOwner
    && accepts copyAScalar && !accepts useAfterDrop && !accepts outOfScope
    && accepts oneFieldToATask && !accepts fieldsToTwoTasks
    && accepts partsOfTwoFieldsInOneCall && accepts fieldPartsSplitInOneCall
    && !accepts fieldPartsOverlapInOneCall && !accepts sameFieldTwiceInOneCall
    && !accepts fieldMoveUnderLease && !accepts fieldReadUnderRecordLease
    && !accepts lenOfFieldUnderElementLease && !accepts lenOfFieldUnderRecordLease
    && accepts laneMap && accepts laneReadsShared && accepts laneLenAndWrite
    && !accepts laneWritesFixedIndex && !accepts laneWritesShared && !accepts laneReadsOther
    && accepts laneBesideTask && !accepts laneUnderLease && !accepts laneUnderPartLease
    && accepts twoRegions

/-- What the build prints, so the Python gate can assert on it. -/
def line : String :=
  if report then "ownership-regression: pass" else "ownership-regression: FAIL"

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

/-- **Naming a second field under a lease really races.**  `box.a` is lent to a
task; the read of `box` that reaching `box.b` performs touches the same storage the
task writes.  That is exactly what `checking.py` reports as `E-LEASED`, and it is why
the field rule alone would not have been enough. -/
theorem fieldsToTwoTasks_races :
    Reach anyVal fieldsToTwoTasks.scope (Cfg.start fieldsToTwoTasks) (Cfg.err (Err.race 4)) :=
  Reach.step (List.Mem.head _)
    (Reach.step (List.Mem.head _)
      (Reach.step (List.Mem.head _)
        (Reach.step (List.Mem.head _) (Reach.refl _))))

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
      || accepts fieldsToTwoTasks || accepts fieldPartsOverlapInOneCall
      || accepts laneWritesFixedIndex
      || accepts laneWritesShared || accepts laneReadsOther) = false
      ∧ accepts backwardsPart = true := by
  constructor <;> decide

end Regress

/-- The Lean encodings of the pinned CAIRN programs are classified exactly as the
Python checker classifies them. -/
theorem ownership_regression : Regress.report = true := by decide

/-! ### The individual pinned programs -/

example : accepts Regress.moveBesideView = false := by decide
example : accepts Regress.leasedRead = false := by decide
example : accepts Regress.doubleMove = false := by decide
example : accepts Regress.unawaitedTicket = false := by decide
example : accepts Regress.aliasedCall = false := by decide
example : accepts Regress.movedOnOnePath = false := by decide
example : accepts Regress.ticketsDisagree = false := by decide
example : accepts Regress.overlappingTasks = false := by decide
example : accepts Regress.copyAnOwner = false := by decide
example : accepts Regress.useAfterDrop = false := by decide
example : accepts Regress.partsWithoutMiddle = false := by decide
example : accepts Regress.overlappingParts = false := by decide
example : accepts Regress.overlappingArgs = false := by decide
example : accepts Regress.lenUnderOwnerLease = false := by decide
example : accepts Regress.fieldsToTwoTasks = false := by decide
example : accepts Regress.fieldMoveUnderLease = false := by decide
example : accepts Regress.fieldReadUnderRecordLease = false := by decide
example : accepts Regress.fieldPartsOverlapInOneCall = false := by decide
example : accepts Regress.sameFieldTwiceInOneCall = false := by decide
example : accepts Regress.lenOfFieldUnderElementLease = false := by decide
example : accepts Regress.lenOfFieldUnderRecordLease = false := by decide
example : accepts Regress.laneWritesFixedIndex = false := by decide
example : accepts Regress.laneWritesShared = false := by decide
example : accepts Regress.laneReadsOther = false := by decide
example : accepts Regress.laneUnderLease = false := by decide
example : accepts Regress.laneUnderPartLease = false := by decide
example : accepts Regress.twoOwners = true := by decide
example : accepts Regress.disjointSplit = true := by decide
example : accepts Regress.kwaySplit = true := by decide
example : accepts Regress.backwardsPart = true := by decide
example : accepts Regress.lenUnderElementLease = true := by decide
example : accepts Regress.sharedRead = true := by decide
example : accepts Regress.movedOnBothPaths = true := by decide
example : accepts Regress.copyAScalar = true := by decide
example : accepts Regress.oneFieldToATask = true := by decide
example : accepts Regress.fieldPartsSplitInOneCall = true := by decide
example : accepts Regress.partsOfTwoFieldsInOneCall = true := by decide
example : accepts Regress.laneMap = true := by decide
example : accepts Regress.laneReadsShared = true := by decide
example : accepts Regress.laneLenAndWrite = true := by decide
example : accepts Regress.laneBesideTask = true := by decide
example : accepts Regress.twoRegions = true := by decide

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
