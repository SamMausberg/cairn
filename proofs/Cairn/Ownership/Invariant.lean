/-
Where the checker meets the machine: the bookkeeping that turns a checker permission
into a fault-free access, the split of the thread list, and the invariant `Ok` of a
configuration of an accepted program.
-/
import Cairn.Ownership.Heap
import Cairn.Ownership.Weakening

namespace Cairn
namespace Ownership

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

end Ownership
end Cairn
