/-
Preservation: every successor of a configuration that satisfies `Ok` satisfies it too.  Since
`Ok` is `False` on a fault, this is also what rules every fault out.
-/
import Cairn.Ownership.Invariant

namespace Cairn
namespace Ownership

/-! ## Preservation

Each straight-line statement has one lemma of one shape: under the checker's guard, every step of
the spawner `Lands` in a trap, or in the rest of the code with a state that the checker's effect
is in step with.  `Ok_succ` assembles them with the branch step and the steps of live threads. -/

/-- A spawner step under `c`'s guard: a trap, or the rest of the code in step with `c'`. -/
def Lands (ρ : Valuation) (scope : List Var) (c' : CState) (rest : List Stmt) (cfg : Cfg) : Prop :=
  cfg = .trap ∨ ∃ tasks lanes st, cfg = .run rest tasks lanes st ∧ Sync ρ scope c' tasks (tasks ++ lanes) st

theorem Lands.run {ρ : Valuation} {scope : List Var} {c' : CState} {rest : List Stmt}
    {tasks : List Task} {st : State} (h : Sync ρ scope c' tasks tasks st) :
    Lands ρ scope c' rest (.run rest tasks [] st) :=
  .inr ⟨tasks, [], st, rfl, by rwa [List.append_nil]⟩

variable {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task} {st : State}
  {rest : List Stmt}

/-! ### Binding a local -/

/-- `let x = Buf[T](n);`: a fresh cell lands in `x`, which moves from the scalars to the owners. -/
theorem alloc_sync {x : Var} (h : Sync ρ scope c tasks tasks st) (hg : guardOf scope c (.alloc x) = true) :
    ∀ cfg ∈ stepStmt ρ (.alloc x) rest tasks st, Lands ρ scope (effOf c (.alloc x)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, List.contains_iff_mem] at hg
  obtain ⟨hw, hu⟩ := write_ok h.facts h.agree hg.2
  obtain ⟨st', hre⟩ := reallocAt_ok h.mem x
  have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun _ => reallocAt_env_ne hre
  intro cfg hcfg
  simp only [stepStmt, raceErr_none hw, hre, List.mem_singleton] at hcfg
  subst hcfg
  exact .run ⟨memOk_reallocAt h.mem hg.1 hre, h.compat, h.live.rebind hkeep hu, h.guarded, h.facts,
    h.agree.rebind hkeep (reallocAt_groups hre) ⟨rfl, rfl⟩ (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hx => absurd rfl (mem_del.mp hx).2) (fun p hp => mem_add.mp hp)
      (fun _ => reallocAt_env_self hre)⟩

/-- `let x = 0;`: a scalar lands in `x`. -/
theorem mkScalar_sync {x : Var} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf scope c (.mkScalar x) = true) :
    ∀ cfg ∈ stepStmt ρ (.mkScalar x) rest tasks st, Lands ρ scope (effOf c (.mkScalar x)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, List.contains_iff_mem] at hg
  obtain ⟨hw, hu⟩ := write_ok h.facts h.agree hg.2
  obtain ⟨st', hbe⟩ := bindAt_ok h.mem x .scalar
  have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun _ => bindAt_env_ne hbe
  intro cfg hcfg
  simp only [stepStmt, raceErr_none hw, hbe, List.mem_singleton] at hcfg
  subst hcfg
  exact .run ⟨memOk_bindAt h.mem (by intro _ h; cases h) hbe, h.compat, h.live.rebind hkeep hu,
    h.guarded, h.facts,
    h.agree.rebind hkeep (bindAt_groups hbe) ⟨rfl, rfl⟩ (fun p hp => mem_add.mp hp)
      (fun _ => bindAt_env_self hbe) (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hx => absurd rfl (mem_del.mp hx).2)⟩

/-- `let y = x;` for a scalar `x`: the bits are duplicated, and `y` is a scalar. -/
theorem copy_sync {y x : Var} (h : Sync ρ scope c tasks tasks st) (hg : guardOf scope c (.copy y x) = true) :
    ∀ cfg ∈ stepStmt ρ (.copy y x) rest tasks st, Lands ρ scope (effOf c (.copy y x)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, List.contains_iff_mem] at hg
  have hxs : st.env x = .scalar := h.agree.scalars_ok x hg.1.1.2
  have hro : heldRace ρ tasks (.whole ⟨x, []⟩, Mode.ro) = false := heldRace_none h.facts h.agree rfl hg.1.2
  obtain ⟨hwy, hu⟩ := write_ok h.facts h.agree hg.2
  obtain ⟨st', hbe⟩ := bindAt_ok h.mem y (st.env x)
  have hkeep : ∀ p, p ≠ y → st'.env p = st.env p := fun _ => bindAt_env_ne hbe
  intro cfg hcfg
  simp only [stepStmt, accessErr_none hro (memErr_none h.mem hxs nofun nofun), raceErr_none hwy,
    hbe, List.mem_singleton] at hcfg
  subst hcfg
  exact .run ⟨memOk_bindAt h.mem (by simp [hxs]) hbe, h.compat, h.live.rebind hkeep hu, h.guarded,
    h.facts,
    h.agree.rebind hkeep (bindAt_groups hbe) ⟨rfl, rfl⟩ (fun p hp => mem_add.mp hp)
      (fun _ => (bindAt_env_self hbe).trans hxs) (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hy => absurd rfl (mem_del.mp hy).2)⟩

/-- `let y = x;` for an owner `x`: the cell moves to `y` and `x` is dead.  Agreement is carried
across the two rebindings one at a time; the heap invariant, which the intermediate state
breaks, is `memOk_move`'s business. -/
theorem move_sync {y x : Var} (h : Sync ρ scope c tasks tasks st) (hg : guardOf scope c (.move y x) = true) :
    ∀ cfg ∈ stepStmt ρ (.move y x) rest tasks st, Lands ρ scope (effOf c (.move y x)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, List.contains_iff_mem, Bool.not_eq_true',
    beq_eq_false_iff_ne] at hg
  have hyx : y ≠ x := hg.1.1.1.2
  obtain ⟨a, hxa⟩ := h.agree.owners_ok x hg.1.1.2
  obtain ⟨hwx, hux⟩ := write_ok h.facts h.agree hg.1.2
  obtain ⟨hwy, huy⟩ := write_ok h.facts h.agree hg.2
  obtain ⟨st1, hbe⟩ := bindAt_ok h.mem y (st.env x)
  have hkeep1 : ∀ p, p ≠ y → st1.env p = st.env p := fun _ => bindAt_env_ne hbe
  have hkeep2 : ∀ p, p ≠ x → upd st1.env x .moved p = st1.env p := fun _ => upd_other
  intro cfg hcfg
  simp only [stepStmt, accessErr_none hwx (memErr_none h.mem hxa nofun nofun), raceErr_none hwy,
    hbe, List.mem_singleton] at hcfg
  subst hcfg
  have hmid : Agree { c with scalars := del c.scalars y, owners := add c.owners y } st1 tasks :=
    h.agree.rebind hkeep1 (bindAt_groups hbe) ⟨rfl, rfl⟩ (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hy => absurd rfl (mem_del.mp hy).2) (fun p hp => mem_add.mp hp)
      (fun _ => ⟨a, (bindAt_env_self hbe).trans hxa⟩)
  refine .run ⟨memOk_move h.mem hg.1.1.1.1 hyx hxa hbe, h.compat,
    (h.live.rebind hkeep1 huy).rebind hkeep2 hux, h.guarded, h.facts,
    hmid.rebind hkeep2 rfl ⟨rfl, rfl⟩ (fun p hp => Or.inr hp) ?_ ?_ ?_⟩
  · intro hx
    have := h.agree.scalars_ok x (mem_del.mp hx).1
    rw [hxa] at this; exact nomatch this
  · intro p hp
    rcases mem_add.mp hp with rfl | hp
    · exact Or.inr (mem_add.mpr (Or.inl rfl))
    · exact Or.inr (mem_add.mpr (Or.inr (mem_del.mp hp).1))
  · intro hx
    rcases mem_add.mp hx with e | hx
    · exact absurd e.symm hyx
    · exact absurd rfl (mem_del.mp hx).2

/-- The implicit release of `x` at the exit of an inner scope. -/
theorem drop_sync {x : Var} (h : Sync ρ scope c tasks tasks st) (hg : guardOf scope c (.drop x) = true) :
    ∀ cfg ∈ stepStmt ρ (.drop x) rest tasks st, Lands ρ scope (effOf c (.drop x)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, List.contains_iff_mem] at hg
  obtain ⟨a, hxa⟩ := h.agree.owners_ok x hg.1
  obtain ⟨hw, hu⟩ := write_ok h.facts h.agree hg.2
  obtain ⟨st', hre⟩ := release_ok h.mem x
  have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun _ => release_env_ne hre
  intro cfg hcfg
  simp only [stepStmt, accessErr_none hw (memErr_none h.mem hxa nofun nofun), hre,
    List.mem_singleton] at hcfg
  subst hcfg
  refine .run ⟨memOk_release h.mem hre, h.compat, h.live.rebind hkeep hu, h.guarded, h.facts,
    h.agree.rebind hkeep (release_groups hre) ⟨rfl, rfl⟩ (fun p hp => Or.inr hp) ?_
      (fun p hp => Or.inr (mem_del.mp hp).1) (fun hx => absurd rfl (mem_del.mp hx).2)⟩
  intro hx
  have := h.agree.scalars_ok x hx
  rw [hxa] at this; exact nomatch this

/-! ### Lending -/

/-- A disjoint argument list whose slices were guarded is disjoint under the valuation. -/
theorem pairsOkAt_of_args {args : List Borrow} (hd : argsDisjoint args = true)
    (hga : argsGuarded ρ args = true) : pairsOkAt ρ args = true :=
  pairsOk_sound (fun p hp => by
    obtain ⟨z, hz, rfl⟩ := List.mem_map.mp hp
    exact List.all_eq_true.mp hga z hz) args hd

/-- Lending under the checker's permission: once every slice is guarded, no argument races with a
live task, and every argument names a bound local. -/
theorem lend_free {args : List Borrow} (h : Sync ρ scope c tasks tasks st)
    (hl : c.mayLend args = true) (hga : argsGuarded ρ args = true) :
    (∀ z ∈ args, heldRace ρ tasks z = false) ∧ accessAll ρ tasks st args = none := by
  simp only [CState.mayLend, List.all_eq_true, Bool.and_eq_true] at hl
  have hfree : ∀ z ∈ args, heldRace ρ tasks z = false := fun z hz =>
    heldRace_none h.facts h.agree (List.all_eq_true.mp hga z hz) (hl z hz).2
  refine ⟨hfree, accessAll_none args fun z hz => ?_⟩
  obtain ⟨h1, h2⟩ := h.agree.live_of_livePlace (hl z hz).1
  exact accessErr_none (hfree z hz) (memErr_none h.mem rfl h1 h2)

/-- The machine's `lend`: a trap, or the continuation, reached only with every slice guarded. -/
theorem lend_cases {args : List Borrow} {full : Bool} {k : List Cfg} {P : Cfg → Prop} (hP : P .trap)
    (hacc : argsGuarded ρ args = true → accessAll ρ tasks st args = none)
    (hpairs : argsGuarded ρ args = true → pairsOkAt ρ args = true)
    (hk : argsGuarded ρ args = true → ∀ cfg ∈ k, P cfg) :
    ∀ cfg ∈ lend ρ tasks st args full k, P cfg := by
  intro cfg hcfg
  unfold lend at hcfg
  cases hga : argsGuarded ρ args <;> cases full <;>
    simp only [hga, Bool.not_false, Bool.not_true, Bool.or_true, Bool.or_false, Bool.false_eq_true,
      ↓reduceIte, List.mem_singleton] at hcfg
  · exact hcfg ▸ hP
  · exact hcfg ▸ hP
  · simp only [hpairs hga, hacc hga, Bool.not_true, Bool.false_eq_true, ↓reduceIte] at hcfg
    exact hk hga cfg hcfg
  · exact hcfg ▸ hP

/-- A new task that holds `args` under the name `t`: in step on both sides. -/
theorem sync_lend {args : List Borrow} {t : Ticket} (h : Sync ρ scope c tasks tasks st)
    (hl : c.mayLend args = true) (hga : argsGuarded ρ args = true) :
    Sync ρ scope { c with leases := (t, args) :: c.leases } ((t, args) :: tasks) ((t, args) :: tasks) st := by
  have hfree := (lend_free h hl hga).1
  simp only [CState.mayLend, List.all_eq_true, Bool.and_eq_true] at hl
  have hgz := List.all_eq_true.mp hga
  refine ⟨h.mem, List.pairwise_cons.mpr ⟨fun U hU z hz w hw => heldRace_eq_false_iff.mp (hfree z hz) U hU w hw,
    h.compat⟩, ?_, ?_, ?_, ⟨h.agree.scalars_ok, h.agree.owners_ok, h.agree.groups_ok, h.agree.covers.cons _⟩⟩
  · intro T hT z hz
    rcases List.mem_cons.mp hT with rfl | hT
    · exact h.agree.live_of_livePlace (hl z hz).1
    · exact h.live T hT z hz
  · intro T hT z hz
    rcases List.mem_cons.mp hT with rfl | hT
    · exact hgz z hz
    · exact h.guarded T hT z hz
  · intro T hT z hz
    rcases List.mem_cons.mp hT with rfl | hT
    · exact hgz z hz
    · exact h.facts T hT z hz

/-- `f(borrows...)`: the places are lent for the call and returned. -/
theorem call_sync {args : List Borrow} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf scope c (.call args) = true) :
    ∀ cfg ∈ stepStmt ρ (.call args) rest tasks st, Lands ρ scope (effOf c (.call args)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true] at hg
  exact lend_cases (.inl rfl) (fun hga => (lend_free h hg.2 hga).2) (pairsOkAt_of_args hg.1)
    fun _ cfg hcfg => (List.mem_singleton.mp hcfg) ▸ .run h

/-- `let t = spawn f(borrows...);`: the places stay lent, and the new task is compatible with
every live one because the lease check admitted each of its borrows. -/
theorem spawn_sync {t : Ticket} {args : List Borrow} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf scope c (.spawn t args) = true) :
    ∀ cfg ∈ stepStmt ρ (.spawn t args) rest tasks st, Lands ρ scope (effOf c (.spawn t args)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true] at hg
  exact lend_cases (.inl rfl) (fun hga => (lend_free h hg.2 hga).2) (pairsOkAt_of_args hg.1.1)
    fun hga cfg hcfg => (List.mem_singleton.mp hcfg) ▸ .run (sync_lend h hg.2 hga)

/-- A live group is still known to the machine. -/
theorem capOf_live {g : Ticket} {l : List (Ticket × Nat)} (h : g ∈ l.map Prod.fst) : capOf g l ≠ none := by
  induction l with
  | nil => exact absurd h List.not_mem_nil
  | cons G rest ih =>
      unfold capOf
      split
      · exact nofun
      · next hne =>
          rcases List.mem_cons.mp h with e | h
          · exact absurd e.symm hne
          · exact ih h

/-- `spawn f(borrows...) into g;`: a spawn under the group's name, or a trap if it is full. -/
theorem submit_sync {g : Ticket} {args : List Borrow} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf scope c (.submit g args) = true) :
    ∀ cfg ∈ stepStmt ρ (.submit g args) rest tasks st, Lands ρ scope (effOf c (.submit g args)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, has_iff] at hg
  have hcap := capOf_live ((h.agree.groups_ok g).mp hg.1.1)
  intro cfg hcfg
  simp only [stepStmt] at hcfg
  split at hcfg
  · next hn => exact absurd hn hcap
  · exact lend_cases (.inl rfl) (fun hga => (lend_free h hg.2 hga).2) (pairsOkAt_of_args hg.1.2)
      (fun hga cfg hcfg => (List.mem_singleton.mp hcfg) ▸ .run (sync_lend h hg.2 hga)) cfg hcfg

theorem finishOne_sublist {g : Ticket} : ∀ {tasks ts : List Task}, ts ∈ finishOne g tasks → ts.Sublist tasks
  | [], _, h => absurd h List.not_mem_nil
  | T :: rest, ts, h => by
      simp only [finishOne, List.mem_append, List.mem_map] at h
      rcases h with h | ⟨ts', h', rfl⟩
      · split at h
        · exact (List.mem_singleton.mp h) ▸ List.sublist_cons_self T rest
        · exact absurd h List.not_mem_nil
      · exact (finishOne_sublist h').cons_cons T

/-- Fewer live tasks, under the same checker state. -/
theorem Sync.sublist {ts : List Task} (h : Sync ρ scope c tasks tasks st) (hs : ts.Sublist tasks) :
    Sync ρ scope c ts ts st :=
  ⟨h.mem, h.compat.sublist hs, fun T hT => h.live T (hs.subset hT),
    fun T hT => h.guarded T (hs.subset hT), h.facts,
    ⟨h.agree.scalars_ok, h.agree.owners_ok, h.agree.groups_ok, h.agree.covers.sub fun _ hT => hs.subset hT⟩⟩

/-- `collect(g)`: one task of the group finished and is joined.  The checker keeps its leases, so
it claims more than the machine holds, which `Covers` allows. -/
theorem collect_sync {g : Ticket} (h : Sync ρ scope c tasks tasks st) (hg : guardOf scope c (.collect g) = true) :
    ∀ cfg ∈ stepStmt ρ (.collect g) rest tasks st, Lands ρ scope (effOf c (.collect g)) rest cfg := by
  simp only [guardOf, has_iff] at hg
  have hcap := capOf_live ((h.agree.groups_ok g).mp hg)
  intro cfg hcfg
  simp only [stepStmt] at hcfg
  split at hcfg
  · next hn => exact absurd hn hcap
  · split at hcfg
    · exact .inl (List.mem_singleton.mp hcfg)
    · next ts more hts =>
        obtain ⟨ts', hts', rfl⟩ := List.mem_map.mp hcfg
        have hin : ts' ∈ finishOne g tasks := by rw [hts]; exact hts'
        exact .run (h.sublist (finishOne_sublist hin))

/-- `wait(t)`: the task or the group is gone and its borrows are returned. -/
theorem wait_sync {t : Ticket} (h : Sync ρ scope c tasks tasks st) :
    ∀ cfg ∈ stepStmt ρ (.wait t) rest tasks st, Lands ρ scope (effOf c (.wait t)) rest cfg := by
  intro cfg hcfg
  simp only [stepStmt, List.mem_singleton] at hcfg
  subst hcfg
  have hsub : ∀ {l : List Task}, (l.filter fun T => !decide (T.1 = t)).Sublist l := List.filter_sublist
  refine .run ⟨h.mem.regroup _, h.compat.sublist hsub, fun T hT => h.live T (hsub.subset hT),
    fun T hT => h.guarded T (hsub.subset hT), fun T hT => h.facts T (hsub.subset hT),
    ⟨h.agree.scalars_ok, h.agree.owners_ok, fun g => ?_, h.agree.covers.filter t⟩⟩
  simp only [effOf, mem_del, List.mem_map, List.mem_filter, h.agree.groups_ok g]
  constructor
  · rintro ⟨⟨G, hG, rfl⟩, hne⟩
    exact ⟨G, ⟨hG, by simpa using hne⟩, rfl⟩
  · rintro ⟨G, ⟨hG, hne⟩, rfl⟩
    exact ⟨⟨G, hG, rfl⟩, by simpa using hne⟩

/-- `let g = Group[T](n);`: an empty group, live on both sides. -/
theorem group_sync {g : Ticket} {nb : Bound} (h : Sync ρ scope c tasks tasks st) :
    ∀ cfg ∈ stepStmt ρ (.group g nb) rest tasks st, Lands ρ scope (effOf c (.group g nb)) rest cfg := by
  intro cfg hcfg
  simp only [stepStmt, List.mem_singleton] at hcfg
  subst hcfg
  refine .run ⟨h.mem.regroup _, h.compat, h.live, h.guarded, h.facts,
    ⟨h.agree.scalars_ok, h.agree.owners_ok, fun g' => ?_, h.agree.covers⟩⟩
  simp only [effOf, mem_add, List.map_cons, List.mem_cons, h.agree.groups_ok g']

/-! ### Regions and threads -/

/-- `parallel i in n { body }`: one lane per index is forked, each compatible with every other lane
by the region rule and with every live task by the lease check. -/
theorem parallel_sync {nb : Bound} {body : List Touch} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf scope c (.parallel nb body) = true) :
    ∀ cfg ∈ stepStmt ρ (.parallel nb body) rest tasks st,
      Lands ρ scope (effOf c (.parallel nb body)) rest cfg := by
  simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
  have hfree : ∀ a ∈ body, heldRace ρ tasks a.lease = false := fun a ha =>
    heldRace_none h.facts h.agree (Touch.lease_guard ρ a) (hg.2 a ha).2
  intro cfg hcfg
  simp only [stepStmt, List.mem_singleton] at hcfg
  subst hcfg
  refine .inr ⟨tasks, _, st, rfl, h.mem, List.pairwise_append.mpr ⟨h.compat, lanesOf_pairwise ρ _ hg.1, ?_⟩,
    ?_, ?_, h.facts, h.agree⟩
  · intro T hT L hL x hx w hw
    obtain ⟨a, ha, rfl⟩ := mem_lanesOf _ hL hw
    rw [races_symm]
    exact races_borrow_of_lease (heldRace_eq_false_iff.mp (hfree a ha) T hT x hx)
  · intro T hT w hw
    rcases List.mem_append.mp hT with h1 | h2
    · exact h.live T h1 w hw
    · obtain ⟨a, ha, rfl⟩ := mem_lanesOf _ h2 hw
      rw [Touch.borrow_base]
      exact h.agree.live_of_livePlace (hg.2 a ha).1
  · intro T hT w hw
    rcases List.mem_append.mp hT with h1 | h2
    · exact h.guarded T h1 w hw
    · obtain ⟨a, ha, rfl⟩ := mem_lanesOf _ h2 hw
      exact Touch.borrow_guard ρ a T.1

/-- **Every straight-line statement lands in step with its effect.** -/
theorem straight_sync {s : Stmt} (h : Sync ρ scope c tasks tasks st) (hg : guardOf scope c s = true)
    (hne : s.isIte = false) : ∀ cfg ∈ stepStmt ρ s rest tasks st, Lands ρ scope (effOf c s) rest cfg := by
  cases s with
  | alloc x => exact alloc_sync h hg
  | mkScalar x => exact mkScalar_sync h hg
  | copy y x => exact copy_sync h hg
  | move y x => exact move_sync h hg
  | drop x => exact drop_sync h hg
  | call args => exact call_sync h hg
  | spawn t args => exact spawn_sync h hg
  | wait t => exact wait_sync h
  | ite thn els => exact absurd hne nofun
  | parallel nb body => exact parallel_sync h hg
  | group g nb => exact group_sync h
  | submit g args => exact submit_sync h hg
  | collect g => exact collect_sync h hg

/-- **One step of a live thread.**  Any access of its footprint is race free and touches a bound
local, and the only visible write, replacing a cell through a whole owner, keeps the state in
step with the same checker state. -/
theorem thread_sync {code : List Stmt} {lanes : List Task} (h : Sync ρ scope c tasks (tasks ++ lanes) st)
    {T : Task} {others : List Task} (hsplit : (T, others) ∈ splits (tasks ++ lanes)) :
    ∀ cfg ∈ stepThread ρ code tasks lanes T others st,
      ∃ st', cfg = .run code tasks lanes st' ∧ Sync ρ scope c tasks (tasks ++ lanes) st' := by
  have hT := (splits_mem hsplit).1
  intro cfg hcfg
  simp only [stepThread, List.mem_flatMap] at hcfg
  obtain ⟨z, hz, hcfg⟩ := hcfg
  have hnc : heldRace ρ others z = false := heldRace_eq_false_iff.mpr fun U hU w hw =>
    pairwise_splits (noRacePair_symm ρ) h.compat hsplit U hU z hz w hw
  obtain ⟨hl1, hl2⟩ := h.live T hT z hz
  simp only [accessErr_none hnc (memErr_none h.mem rfl hl1 hl2)] at hcfg
  cases hzm : z.2 with
  | ro => simp only [hzm, List.mem_singleton] at hcfg; exact ⟨st, hcfg, h⟩
  | rw =>
      simp only [hzm] at hcfg
      unfold taskWrite at hcfg
      split at hcfg
      · next p hp =>
          -- the borrow is the whole owner: the thread may replace the cell
          rw [hp] at hl1 hl2
          split at hcfg
          · next a hea =>
              obtain ⟨st', hre⟩ := reallocAt_ok h.mem p
              simp only [hre, List.mem_cons, List.not_mem_nil, or_false] at hcfg
              rcases hcfg with rfl | rfl
              · exact ⟨st, rfl, h⟩
              have hkeep : ∀ q, q ≠ p → st'.env q = st.env q := fun _ => reallocAt_env_ne hre
              refine ⟨st', rfl, memOk_reallocAt h.mem (mem_scope_of_owner h.mem hea) hre, h.compat,
                ?_, h.guarded, h.facts,
                h.agree.rebind hkeep (reallocAt_groups hre) ⟨rfl, rfl⟩ (fun q hq => Or.inr hq) ?_
                  (fun q hq => Or.inr hq) (fun _ => reallocAt_env_self hre)⟩
              · intro U hU w hw
                rcases dec_eq_or_ne w.1.base p with hwp | hwp
                · obtain ⟨b, hb⟩ := reallocAt_env_self hre
                  rw [hwp, hb]; exact ⟨nofun, nofun⟩
                · rw [hkeep _ hwp]; exact h.live U hU w hw
              · intro hq
                have := h.agree.scalars_ok p hq
                rw [hea] at this; exact nomatch this
          · next hea => exact absurd (show st.env (Place.whole ⟨p, []⟩).base = .nil from hea) hl1
          · next hea => exact absurd (show st.env (Place.whole ⟨p, []⟩).base = .moved from hea) hl2
          · simp only [List.mem_singleton] at hcfg; exact ⟨st, hcfg, h⟩
      all_goals (simp only [List.mem_singleton] at hcfg; exact ⟨st, hcfg, h⟩)

/-! ### Assembling the steps -/

/-- A step of the spawner, under a derivation that accepts the code: an `if` enters a branch, whose
code is followed by what the join accepts; anything else lands in step with its effect; and
forgetting is `Sync.weaken`. -/
theorem main_step {c : CState} {code : List Stmt} (hchk : Checks scope c code) :
    ∀ {s : Stmt} {rest : List Stmt} {tasks : List Task} {st : State}, code = s :: rest →
      Sync ρ scope c tasks tasks st → ∀ cfg ∈ stepStmt ρ s rest tasks st, Ok ρ scope cfg := by
  induction hchk with
  | nil => intro _ _ _ _ he; exact absurd he nofun
  | weaken hle _ ih => intro _ _ _ _ he hsync; exact ih he (hsync.weaken hle)
  | @cons c c' s rest hc hrest _ =>
      intro s' rest' tasks st he hsync cfg hcfg
      injection he with h1 h2
      subst h1 h2
      cases hs : s.isIte
      · obtain ⟨hg, rfl⟩ := checkStmt_straight hc hs
        rcases straight_sync hsync hg hs cfg hcfg with rfl | ⟨tasks', lanes, st', rfl, hs'⟩
        · trivial
        · exact ⟨_, hs', hrest⟩
      · cases s <;> simp only [Stmt.isIte, Bool.false_eq_true] at hs
        rename_i thn els
        rw [checkStmt_ite] at hc
        split at hc
        · next a1 a2 h1 h2 =>
            obtain ⟨hle1, hle2⟩ := joinOf_le hc
            have hsync' : Sync ρ scope c tasks (tasks ++ []) st := by rwa [List.append_nil]
            simp only [stepStmt, List.mem_cons, List.not_mem_nil, or_false] at hcfg
            rcases hcfg with rfl | rfl
            · exact ⟨c, hsync', checks_append hrest thn c a1 h1 hle1⟩
            · exact ⟨c, hsync', checks_append hrest els c a2 h2 hle2⟩
        · exact absurd hc nofun

theorem Ok_succ {cfg cfg' : Cfg} (h : Ok ρ scope cfg) (hs : cfg' ∈ succ ρ scope cfg) : Ok ρ scope cfg' := by
  cases cfg with
  | done st => exact absurd hs (by simp [succ])
  | trap => exact absurd hs (by simp [succ])
  | err e => exact absurd hs (by simp [succ])
  | run code tasks lanes st =>
  obtain ⟨c, hsync, hchk⟩ := h
  rcases List.mem_append.mp hs with hmain | hthread
  · -- the main thread steps, which a running region blocks
    cases lanes with
    | cons L others =>
        -- the region completes; the lanes are gone and the spawner may go on
        simp only [stepHost, List.mem_singleton] at hmain
        subst hmain
        have hs' : Sync ρ scope c tasks tasks st :=
          ⟨hsync.mem, (List.pairwise_append.mp hsync.compat).1,
            fun T hT => hsync.live T (List.mem_append_left _ hT),
            fun T hT => hsync.guarded T (List.mem_append_left _ hT), hsync.facts, hsync.agree⟩
        exact ⟨c, by rwa [List.append_nil], hchk⟩
    | nil =>
    simp only [stepHost] at hmain
    rw [List.append_nil] at hsync
    cases code with
    | nil =>
        obtain ⟨hl, hgr⟩ := hchk.done rfl
        have htasks : tasks = [] := nil_of_forall fun T hT => by
          obtain ⟨U, hU, _⟩ := hsync.agree.covers T hT
          rw [hl] at hU; exact List.not_mem_nil hU
        have hgroups : st.groups = [] := nil_of_forall fun G hG => by
          have := (hsync.agree.groups_ok G.1).mpr (List.mem_map_of_mem hG)
          rw [hgr] at this; exact List.not_mem_nil this
        subst htasks
        obtain ⟨st', hrel, _, _⟩ := releaseAll_sound scope st hsync.mem
        simp only [stepMain, hgroups, hrel, List.mem_singleton] at hmain
        subst hmain
        exact releaseAll_final hsync.mem hrel
    | cons s rest => exact main_step hchk rfl hsync cfg' hmain
  · -- one live thread steps: a task, or a lane of the region that is running
    simp only [List.mem_flatMap] at hthread
    obtain ⟨y, hy, hcfg⟩ := hthread
    obtain ⟨_, rfl, hs'⟩ := thread_sync hsync hy cfg' hcfg
    exact ⟨c, hs', hchk⟩

end Ownership
end Cairn
