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

/-- A local that holds something -- a scalar or a live cell -- is safe to touch. -/
theorem memErr_none {st : State} {scope} {p : Var} {v : Val} (hm : MemOk st scope)
    (hp : st.env p = v) (h1 : v ≠ .nil) (h2 : v ≠ .moved) : memErr st p = none := by
  unfold memErr
  split
  · next heq => exact absurd (hp.symm.trans heq) h1
  · next heq => exact absurd (hp.symm.trans heq) h2
  · rfl
  · next a heq => rw [hm.liveOfEnv p a heq]; rfl

theorem accessErr_none {ρ : Valuation} {tasks : List Task} {st : State} {x : Borrow}
    (hr : heldRace ρ tasks x = false) (hm : memErr st x.1.base = none) :
    accessErr ρ tasks st x = none := by simp [accessErr, raceErr, hr, hm]

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
  | nil => simp [splits]
  | cons a0 rest ih =>
      intro a r h
      simp only [splits, List.mem_cons, List.mem_map, Prod.mk.injEq] at h
      rcases h with ⟨rfl, rfl⟩ | ⟨⟨y, ys⟩, hy, rfl, rfl⟩
      · exact ⟨List.mem_cons_self .., fun b hb => List.mem_cons_of_mem _ hb⟩
      · obtain ⟨h1, h2⟩ := ih hy
        refine ⟨List.mem_cons_of_mem _ h1, fun b hb => ?_⟩
        rcases List.mem_cons.mp hb with rfl | hb
        · exact List.mem_cons_self ..
        · exact List.mem_cons_of_mem _ (h2 b hb)

theorem pairwise_splits {α : Type} {R : α → α → Prop} (hsym : ∀ a b, R a b → R b a) :
    ∀ {l : List α}, l.Pairwise R → ∀ {a : α} {r : List α}, (a, r) ∈ splits l → ∀ b ∈ r, R a b := by
  intro l
  induction l with
  | nil => simp [splits]
  | cons a0 rest ih =>
      intro hp a r h
      have hph := List.pairwise_cons.mp hp
      simp only [splits, List.mem_cons, List.mem_map, Prod.mk.injEq] at h
      rcases h with ⟨rfl, rfl⟩ | ⟨⟨y, ys⟩, hy, rfl, rfl⟩
      · exact hph.1
      · intro b hb
        rcases List.mem_cons.mp hb with rfl | hb
        · exact hsym _ _ (hph.1 y (splits_mem hy).1)
        · exact ih hph.2 hy b hb

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
  · rw [h.scalars_ok p hs]; exact ⟨nofun, nofun⟩
  · obtain ⟨a, ha⟩ := h.owners_ok p ho
    rw [ha]; exact ⟨nofun, nofun⟩

/-- **Agreement survives rebinding one local.**  The checker's new sets may mention `x`
only as what the state now holds there, and every other local is as it was. -/
theorem Agree.rebind {c c' : CState} {st st' : State} {tasks : List Task} {x : Var}
    (h : Agree c st tasks) (hkeep : ∀ p, p ≠ x → st'.env p = st.env p) (hl : c'.leases = c.leases)
    (hs : ∀ p ∈ c'.scalars, p = x ∨ p ∈ c.scalars) (hsx : x ∈ c'.scalars → st'.env x = .scalar)
    (ho : ∀ p ∈ c'.owners, p = x ∨ p ∈ c.owners) (hox : x ∈ c'.owners → ∃ a, st'.env x = .owner a) :
    Agree c' st' tasks where
  scalars_ok p hp := (dec_eq_or_ne p x).elim (fun e => e ▸ hsx (e ▸ hp))
    fun ne => hkeep p ne ▸ h.scalars_ok p ((hs p hp).resolve_left ne)
  owners_ok p hp := (dec_eq_or_ne p x).elim (fun e => e ▸ hox (e ▸ hp))
    fun ne => hkeep p ne ▸ h.owners_ok p ((ho p hp).resolve_left ne)
  leases_ok := hl ▸ h.leases_ok

/-- The local under every place a live task holds still holds something. -/
def TasksLive (tasks : List Task) (st : State) : Prop :=
  ∀ T ∈ tasks, ∀ y ∈ T.2, st.env y.1.base ≠ .nil ∧ st.env y.1.base ≠ .moved

/-- Rebinding a local no live task holds a place of keeps every leased base bound. -/
theorem TasksLive.rebind {tasks : List Task} {st st' : State} {x : Var} (h : TasksLive tasks st)
    (hkeep : ∀ p, p ≠ x → st'.env p = st.env p) (hu : ∀ T ∈ tasks, ∀ y ∈ T.2, y.1.base ≠ x) :
    TasksLive tasks st' :=
  fun T hT y hy => hkeep _ (hu T hT y hy) ▸ h T hT y hy

/-- **Every part a live task holds was guarded.**  This is the invariant the
chaining rule needs: a lease on `d[lo..hi]` exists only because the spawner formed
that slice, and forming it traps unless `lo <= hi`.  It is what makes the facts the
checker chains through true of the numbers -- and it is the whole reason a spawn
performs the guard before it starts the task. -/
def TasksGuarded (ρ : Valuation) (tasks : List Task) : Prop :=
  ∀ T ∈ tasks, ∀ y ∈ T.2, y.1.guard ρ = true

/-- No two live threads race with each other. -/
def TasksOk (ρ : Valuation) (tasks : List Task) : Prop := tasks.Pairwise (NoRacePair ρ)

/-- **The checker and the machine in step.**  The heap invariant holds, the live
threads -- `threads` is the tasks and the lanes of a running region together -- are
pairwise compatible, every base they hold is bound and every part they hold was
guarded, and the checker state `c` describes the state and the tasks. -/
structure Sync (ρ : Valuation) (scope : List Var) (c : CState) (tasks threads : List Task)
    (st : State) : Prop where
  mem : MemOk st scope
  compat : TasksOk ρ threads
  live : TasksLive threads st
  guarded : TasksGuarded ρ threads
  scope_eq : c.scope = scope
  agree : Agree c st tasks

/-- The invariant an accepted program keeps: some checker state is in step with the
machine and checks the code that remains, leaving no ticket live at the end.  The
three clauses about live threads range over the tasks AND the lanes together, which is
the single statement that no two of them conflict -- two tasks, a task and a lane, or
two lanes.  A trap satisfies the invariant vacuously: it is a defined abort, so nothing
is claimed about the cells it leaves behind. -/
def Ok (ρ : Valuation) (scope : List Var) : Cfg → Prop
  | .run code tasks lanes st =>
      ∃ c, Sync ρ scope c tasks (tasks ++ lanes) st ∧ ∃ d, checkBlock c code = some d ∧ d.leases = []
  | .done st => (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1)
  | .trap => True
  | .err _ => False

/-- The invariant of a configuration with no region running, spelled without the
empty tail: this is the shape every straight-line step produces. -/
theorem Ok_run_nil {ρ : Valuation} {scope : List Var} {code : List Stmt} {tasks : List Task}
    {st : State} :
    Ok ρ scope (.run code tasks [] st) ↔
      ∃ c, Sync ρ scope c tasks tasks st ∧ ∃ d, checkBlock c code = some d ∧ d.leases = [] := by
  simp only [Ok, List.append_nil]

theorem Ok_of_sync {ρ : Valuation} {scope : List Var} {c d : CState} {code : List Stmt}
    {tasks : List Task} {st : State} (hs : Sync ρ scope c tasks tasks st)
    (hchk : checkBlock c code = some d) (hdl : d.leases = []) :
    Ok ρ scope (.run code tasks [] st) :=
  Ok_run_nil.mpr ⟨c, hs, d, hchk, hdl⟩

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

theorem checkBlock_scope {c c' : CState} {ss : List Stmt} (h : checkBlock c ss = some c') :
    c'.scope = c.scope := by
  obtain ⟨_, _, _, hsc⟩ := checkBlock_mono (blockSize ss + 1) ss (Nat.lt_succ_self _) (Le.refl c) h
  exact hsc

theorem checkBlock_weaken {c d c' : CState} {ss : List Stmt} (hle : Le c d)
    (h : checkBlock c ss = some c') : ∃ d', checkBlock d ss = some d' ∧ Le c' d' := by
  obtain ⟨d', h1, h2, _⟩ := checkBlock_mono (blockSize ss + 1) ss (Nat.lt_succ_self _) hle h
  exact ⟨d', h1, h2⟩

end Ownership
end Cairn
