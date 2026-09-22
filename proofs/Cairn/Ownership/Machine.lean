/-
The dynamic semantics: values, faults, the heap, the interleaving small-step machine
over the spawner, its tasks and the lanes of a running region, and reachability.
-/
import Cairn.Ownership.Checker

namespace Cairn
namespace Ownership

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

end Ownership
end Cairn
