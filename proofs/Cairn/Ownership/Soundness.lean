/-
Soundness and progress: no execution of an accepted program reaches a fault under any
interleaving and any valuation, every allocation is released exactly once, and no
reachable configuration is stuck.
-/
import Cairn.Ownership.Preservation

namespace Cairn
namespace Ownership

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

end Ownership
end Cairn
