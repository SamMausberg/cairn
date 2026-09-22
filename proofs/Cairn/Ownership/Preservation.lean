/-
Preservation: every successor of a configuration that satisfies `Ok` satisfies it too.
Since `Ok` is `False` on a fault, this is also what rules every fault out.
-/
import Cairn.Ownership.Invariant

namespace Cairn
namespace Ownership

/-! ## Preservation

Every successor of a configuration that satisfies the invariant satisfies it too.
Since `Ok` is `False` on error configurations, this is also what rules the faults
out.  Each statement has one lemma of one shape: under the checker's guard, the
spawner's step lands in the rest of the code with a state the checker's effect is in
step with, or traps.  `Ok_succ` assembles them, and the live threads' steps. -/

/-- `let x = Buf[T](n);`: a fresh cell lands in `x`, which moves from the scalars to the
owners. -/
theorem alloc_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {x : Var} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf c (.alloc x) = true) :
    ∀ cfg ∈ stepStmt ρ (.alloc x) rest tasks st,
      ∃ st', cfg = .run rest tasks [] st' ∧ Sync ρ scope (effOf c (.alloc x)) tasks tasks st' := by
  simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
  obtain ⟨hw, hu⟩ := write_ok h.agree.leases_ok h.guarded hg.2
  obtain ⟨st', hre⟩ := reallocAt_ok h.mem x
  have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun _ => reallocAt_env_ne hre
  intro cfg hcfg
  simp only [stepStmt, raceErr_none hw, hre, List.mem_singleton] at hcfg
  exact ⟨st', hcfg, memOk_reallocAt h.mem (h.scope_eq ▸ hg.1) hre, h.compat,
    h.live.rebind hkeep hu, h.guarded, h.scope_eq,
    h.agree.rebind hkeep rfl (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hx => absurd rfl (mem_del.mp hx).2) (fun p hp => mem_add.mp hp)
      (fun _ => reallocAt_env_self hre)⟩

/-- `let x = 0;`: a scalar lands in `x`. -/
theorem mkScalar_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {x : Var} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf c (.mkScalar x) = true) :
    ∀ cfg ∈ stepStmt ρ (.mkScalar x) rest tasks st,
      ∃ st', cfg = .run rest tasks [] st' ∧ Sync ρ scope (effOf c (.mkScalar x)) tasks tasks st' := by
  simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
  obtain ⟨hw, hu⟩ := write_ok h.agree.leases_ok h.guarded hg.2
  obtain ⟨st', hbe⟩ := bindAt_ok h.mem x .scalar
  have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun _ => bindAt_env_ne hbe
  intro cfg hcfg
  simp only [stepStmt, raceErr_none hw, hbe, List.mem_singleton] at hcfg
  exact ⟨st', hcfg, memOk_bindAt h.mem (by intro _ h; cases h) hbe, h.compat,
    h.live.rebind hkeep hu, h.guarded, h.scope_eq,
    h.agree.rebind hkeep rfl (fun p hp => mem_add.mp hp) (fun _ => bindAt_env_self hbe)
      (fun p hp => Or.inr (mem_del.mp hp).1) (fun hx => absurd rfl (mem_del.mp hx).2)⟩

/-- `let y = x;` for a scalar `x`: the bits are duplicated, and `y` is a scalar. -/
theorem copy_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {y x : Var} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf c (.copy y x) = true) :
    ∀ cfg ∈ stepStmt ρ (.copy y x) rest tasks st,
      ∃ st', cfg = .run rest tasks [] st' ∧ Sync ρ scope (effOf c (.copy y x)) tasks tasks st' := by
  simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
  have hxs : st.env x = .scalar := h.agree.scalars_ok x hg.1.1.2
  have hro : heldRace ρ tasks (.whole ⟨x, []⟩, Mode.ro) = false :=
    heldRace_none h.agree.leases_ok h.guarded rfl hg.1.2
  obtain ⟨hwy, hu⟩ := write_ok h.agree.leases_ok h.guarded hg.2
  obtain ⟨st', hbe⟩ := bindAt_ok h.mem y (st.env x)
  have hkeep : ∀ p, p ≠ y → st'.env p = st.env p := fun _ => bindAt_env_ne hbe
  intro cfg hcfg
  simp only [stepStmt, accessErr_none hro (memErr_none h.mem hxs nofun nofun), raceErr_none hwy,
    hbe, List.mem_singleton] at hcfg
  exact ⟨st', hcfg, memOk_bindAt h.mem (by simp [hxs]) hbe, h.compat, h.live.rebind hkeep hu,
    h.guarded, h.scope_eq,
    h.agree.rebind hkeep rfl (fun p hp => mem_add.mp hp)
      (fun _ => (bindAt_env_self hbe).trans hxs) (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hy => absurd rfl (mem_del.mp hy).2)⟩

/-- `let y = x;` for an owner `x`: the cell moves to `y` and `x` is dead.  Agreement is
carried across the two rebindings one at a time; the heap invariant, which the
intermediate state breaks, is `memOk_move`'s business. -/
theorem move_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {y x : Var} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf c (.move y x) = true) :
    ∀ cfg ∈ stepStmt ρ (.move y x) rest tasks st,
      ∃ st', cfg = .run rest tasks [] st' ∧ Sync ρ scope (effOf c (.move y x)) tasks tasks st' := by
  simp only [guardOf, Bool.and_eq_true, contains_iff_mem, Bool.not_eq_true',
    beq_eq_false_iff_ne] at hg
  have hyx : y ≠ x := hg.1.1.1.2
  obtain ⟨a, hxa⟩ := h.agree.owners_ok x hg.1.1.2
  obtain ⟨hwx, hux⟩ := write_ok h.agree.leases_ok h.guarded hg.1.2
  obtain ⟨hwy, huy⟩ := write_ok h.agree.leases_ok h.guarded hg.2
  obtain ⟨st1, hbe⟩ := bindAt_ok h.mem y (st.env x)
  have hkeep1 : ∀ p, p ≠ y → st1.env p = st.env p := fun _ => bindAt_env_ne hbe
  have hkeep2 : ∀ p, p ≠ x → upd st1.env x .moved p = st1.env p := fun _ => upd_other
  intro cfg hcfg
  simp only [stepStmt, accessErr_none hwx (memErr_none h.mem hxa nofun nofun), raceErr_none hwy,
    hbe, List.mem_singleton] at hcfg
  refine ⟨_, hcfg, memOk_move h.mem (h.scope_eq ▸ hg.1.1.1.1) hyx hxa hbe, h.compat,
    (h.live.rebind hkeep1 huy).rebind hkeep2 hux, h.guarded, h.scope_eq, ?_⟩
  have hmid : Agree { c with scalars := del c.scalars y, owners := add c.owners y } st1 tasks :=
    h.agree.rebind hkeep1 rfl (fun p hp => Or.inr (mem_del.mp hp).1)
      (fun hy => absurd rfl (mem_del.mp hy).2) (fun p hp => mem_add.mp hp)
      (fun _ => ⟨a, (bindAt_env_self hbe).trans hxa⟩)
  refine hmid.rebind hkeep2 rfl (fun p hp => Or.inr hp) ?_ ?_ ?_
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
theorem drop_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {x : Var} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf c (.drop x) = true) :
    ∀ cfg ∈ stepStmt ρ (.drop x) rest tasks st,
      ∃ st', cfg = .run rest tasks [] st' ∧ Sync ρ scope (effOf c (.drop x)) tasks tasks st' := by
  simp only [guardOf, Bool.and_eq_true, contains_iff_mem] at hg
  obtain ⟨a, hxa⟩ := h.agree.owners_ok x hg.1
  obtain ⟨hw, hu⟩ := write_ok h.agree.leases_ok h.guarded hg.2
  obtain ⟨st', hre⟩ := release_ok h.mem x
  have hkeep : ∀ p, p ≠ x → st'.env p = st.env p := fun _ => release_env_ne hre
  intro cfg hcfg
  simp only [stepStmt, accessErr_none hw (memErr_none h.mem hxa nofun nofun), hre,
    List.mem_singleton] at hcfg
  refine ⟨st', hcfg, memOk_release h.mem hre, h.compat, h.live.rebind hkeep hu, h.guarded,
    h.scope_eq, h.agree.rebind hkeep rfl (fun p hp => Or.inr hp) ?_
      (fun p hp => Or.inr (mem_del.mp hp).1) (fun hx => absurd rfl (mem_del.mp hx).2)⟩
  intro hx
  have := h.agree.scalars_ok x hx
  rw [hxa] at this; exact nomatch this

/-- The guard of every slice an argument list forms, when the machine has run it. -/
theorem argsGuarded_of {ρ : Valuation} {args : List Borrow} (h : argsGuarded ρ args = true) :
    ∀ z ∈ args, z.1.guard ρ = true :=
  List.all_eq_true.mp h

/-- A disjoint argument list whose slices were guarded is disjoint under the valuation. -/
theorem pairsOkAt_of_args {ρ : Valuation} {args : List Borrow} (hd : argsDisjoint args = true)
    (hga : ∀ z ∈ args, z.1.guard ρ = true) : pairsOkAt ρ args = true :=
  pairsOk_sound (fun p hp => by obtain ⟨z, hz, rfl⟩ := List.mem_map.mp hp; exact hga z hz) args hd

/-- `f(borrows...)`: the places are lent for the call and returned; the state is as it
was, or the process trapped at a guard. -/
theorem call_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {args : List Borrow} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st)
    (hg : guardOf c (.call args) = true) :
    ∀ cfg ∈ stepStmt ρ (.call args) rest tasks st, cfg = .trap ∨ cfg = .run rest tasks [] st := by
  simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
  intro cfg hcfg
  cases hguard : argsGuarded ρ args with
  | false => simp only [stepStmt, hguard] at hcfg; simp at hcfg; exact Or.inl hcfg
  | true =>
      have hga := argsGuarded_of hguard
      have hacc : accessAll ρ tasks st args = none := accessAll_none args fun z hz => by
        obtain ⟨h1, h2⟩ := h.agree.live_of_livePlace (hg.2 z hz).1
        exact accessErr_none (heldRace_none h.agree.leases_ok h.guarded (hga z hz) (hg.2 z hz).2)
          (memErr_none h.mem rfl h1 h2)
      simp only [stepStmt, hguard, pairsOkAt_of_args hg.1 hga, hacc] at hcfg
      simp at hcfg
      exact Or.inr hcfg

/-- `let t = spawn f(borrows...);`: the places stay lent, and the new task is compatible
with every live one because the lease check admitted each of its borrows. -/
theorem spawn_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {t : Ticket} {args : List Borrow} {rest : List Stmt}
    (h : Sync ρ scope c tasks tasks st) (hg : guardOf c (.spawn t args) = true) :
    ∀ cfg ∈ stepStmt ρ (.spawn t args) rest tasks st,
      cfg = .trap ∨ cfg = .run rest ((t, args) :: tasks) [] st ∧
        Sync ρ scope (effOf c (.spawn t args)) ((t, args) :: tasks) ((t, args) :: tasks) st := by
  simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
  intro cfg hcfg
  cases hguard : argsGuarded ρ args with
  | false => simp only [stepStmt, hguard] at hcfg; simp at hcfg; exact Or.inl hcfg
  | true =>
      have hga := argsGuarded_of hguard
      have hfree : ∀ z ∈ args, heldRace ρ tasks z = false := fun z hz =>
        heldRace_none h.agree.leases_ok h.guarded (hga z hz) (hg.2 z hz).2
      have hacc : accessAll ρ tasks st args = none := accessAll_none args fun z hz => by
        obtain ⟨h1, h2⟩ := h.agree.live_of_livePlace (hg.2 z hz).1
        exact accessErr_none (hfree z hz) (memErr_none h.mem rfl h1 h2)
      simp only [stepStmt, hguard, pairsOkAt_of_args hg.1.1 hga, hacc] at hcfg
      simp at hcfg
      refine Or.inr ⟨hcfg, h.mem, List.pairwise_cons.mpr ⟨?_, h.compat⟩, ?_, ?_, h.scope_eq,
        h.agree.scalars_ok, h.agree.owners_ok, ?_⟩
      · intro U hU z hz w hw
        exact heldRace_eq_false_iff.mp (hfree z hz) U hU w hw
      · intro T hT z hz
        rcases List.mem_cons.mp hT with rfl | hT
        · exact h.agree.live_of_livePlace (hg.2 z hz).1
        · exact h.live T hT z hz
      · intro T hT z hz
        rcases List.mem_cons.mp hT with rfl | hT
        · exact hga z hz
        · exact h.guarded T hT z hz
      · show (t, args) :: c.leases = (t, args) :: tasks
        rw [h.agree.leases_ok]

/-- `wait(t)`: the task is gone and its borrows are returned.  Nothing about the state
changes, so the guard is not even needed. -/
theorem wait_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {t : Ticket} {rest : List Stmt} (h : Sync ρ scope c tasks tasks st) :
    ∀ cfg ∈ stepStmt ρ (.wait t) rest tasks st,
      cfg = .run rest (tasks.filter fun T => !(T.1 == t)) [] st ∧
        Sync ρ scope (effOf c (.wait t)) (tasks.filter fun T => !(T.1 == t))
          (tasks.filter fun T => !(T.1 == t)) st := by
  intro cfg hcfg
  simp only [stepStmt, List.mem_singleton] at hcfg
  refine ⟨hcfg, h.mem, List.Pairwise.sublist List.filter_sublist h.compat,
    fun T hT => h.live T (List.mem_filter.mp hT).1, fun T hT => h.guarded T (List.mem_filter.mp hT).1,
    h.scope_eq, h.agree.scalars_ok, h.agree.owners_ok, ?_⟩
  show c.leases.filter _ = tasks.filter _
  rw [h.agree.leases_ok]

/-- `parallel i in n { body }`: one lane per index is forked, each compatible with every
other lane by the region rule and with every live task by the lease check. -/
theorem parallel_sync {ρ : Valuation} {scope : List Var} {c : CState} {tasks : List Task}
    {st : State} {nb : Bound} {body : List Touch} {rest : List Stmt}
    (h : Sync ρ scope c tasks tasks st) (hg : guardOf c (.parallel nb body) = true) :
    ∀ cfg ∈ stepStmt ρ (.parallel nb body) rest tasks st,
      cfg = .run rest tasks (lanesOf (nb.eval ρ) body) st ∧
        Sync ρ scope (effOf c (.parallel nb body)) tasks (tasks ++ lanesOf (nb.eval ρ) body) st := by
  simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hg
  have hfree : ∀ a ∈ body, heldRace ρ tasks a.lease = false := fun a ha =>
    heldRace_none h.agree.leases_ok h.guarded (Touch.lease_guard ρ a) (hg.2 a ha).2
  intro cfg hcfg
  simp only [stepStmt, List.mem_singleton] at hcfg
  refine ⟨hcfg, h.mem, List.pairwise_append.mpr ⟨h.compat, lanesOf_pairwise ρ _ hg.1, ?_⟩, ?_, ?_,
    h.scope_eq, h.agree⟩
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

/-- **One step of a live thread.**  Any access of its footprint is race free and touches
a bound local, and the only visible write -- replacing a cell through a whole owner --
keeps the state in step with the same checker state. -/
theorem thread_sync {ρ : Valuation} {scope : List Var} {c : CState} {code : List Stmt}
    {tasks lanes : List Task} {st : State} (h : Sync ρ scope c tasks (tasks ++ lanes) st)
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
                ?_, h.guarded, h.scope_eq, h.agree.rebind hkeep rfl (fun q hq => Or.inr hq) ?_
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

theorem Ok_succ {ρ : Valuation} {scope : List Var} {cfg cfg' : Cfg} (h : Ok ρ scope cfg)
    (hs : cfg' ∈ succ ρ scope cfg) : Ok ρ scope cfg' := by
  cases cfg with
  | done st => exact absurd hs (by simp [succ])
  | trap => exact absurd hs (by simp [succ])
  | err e => exact absurd hs (by simp [succ])
  | run code tasks lanes st =>
  obtain ⟨c, hsync, d, hchk, hdl⟩ := h
  rcases List.mem_append.mp hs with hmain | hthread
  · -- the main thread steps, which a running region blocks
    cases lanes with
    | cons L others =>
        -- the region completes; the lanes are gone and the spawner may go on
        simp only [stepHost, List.mem_singleton] at hmain
        subst hmain
        exact Ok_of_sync ⟨hsync.mem, (List.pairwise_append.mp hsync.compat).1,
          fun T hT => hsync.live T (List.mem_append_left _ hT),
          fun T hT => hsync.guarded T (List.mem_append_left _ hT), hsync.scope_eq, hsync.agree⟩
          hchk hdl
    | nil =>
    simp only [stepHost] at hmain
    rw [List.append_nil] at hsync
    cases code with
    | nil =>
        have hcd : c = d := Option.some.inj hchk
        have htasks : tasks = [] := by rw [← hsync.agree.leases_ok, hcd, hdl]
        subst htasks
        obtain ⟨st', hrel, _, _⟩ := releaseAll_sound scope st hsync.mem
        simp only [stepMain, hrel, List.mem_singleton] at hmain
        subst hmain
        exact releaseAll_final hsync.mem hrel
    | cons s rest =>
    rw [checkBlock_cons] at hchk
    cases hc1 : checkStmt c s with
    | none => rw [hc1] at hchk; exact absurd hchk (by simp)
    | some c1 =>
    rw [hc1] at hchk
    simp only [stepMain] at hmain
    cases s with
    | alloc x =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨_, rfl, hs'⟩ := alloc_sync hsync hg cfg' hmain
        exact Ok_of_sync hs' hchk hdl
    | mkScalar x =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨_, rfl, hs'⟩ := mkScalar_sync hsync hg cfg' hmain
        exact Ok_of_sync hs' hchk hdl
    | copy y x =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨_, rfl, hs'⟩ := copy_sync hsync hg cfg' hmain
        exact Ok_of_sync hs' hchk hdl
    | move y x =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨_, rfl, hs'⟩ := move_sync hsync hg cfg' hmain
        exact Ok_of_sync hs' hchk hdl
    | drop x =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨_, rfl, hs'⟩ := drop_sync hsync hg cfg' hmain
        exact Ok_of_sync hs' hchk hdl
    | call args =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        rcases call_sync hsync hg cfg' hmain with rfl | rfl
        · trivial
        · exact Ok_of_sync hsync hchk hdl
    | spawn t args =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        rcases spawn_sync hsync hg cfg' hmain with rfl | ⟨rfl, hs'⟩
        · trivial
        · exact Ok_of_sync hs' hchk hdl
    | wait t =>
        obtain ⟨_, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨rfl, hs'⟩ := wait_sync hsync cfg' hmain
        exact Ok_of_sync hs' hchk hdl
    | parallel nb body =>
        obtain ⟨hg, rfl⟩ := checkStmt_straight hc1 nofun
        obtain ⟨rfl, hs'⟩ := parallel_sync hsync hg cfg' hmain
        exact ⟨_, hs', d, hchk, hdl⟩
    | ite thn els =>
        rw [checkStmt_ite] at hc1
        split at hc1
        · next a1 a2 h1 h2 =>
            unfold joinOf at hc1
            split at hc1
            · next hleq =>
                have hc1v := Option.some.inj hc1
                have hleases : a1.leases = a2.leases := of_decide_eq_true (by simpa using hleq)
                have hle1 : Le c1 a1 :=
                  ⟨by rw [← hc1v]; exact (checkBlock_scope h1).symm,
                   fun p hp => by rw [← hc1v] at hp; exact (mem_keepIn.mp hp).1,
                   fun p hp => by rw [← hc1v] at hp; exact (mem_keepIn.mp hp).1, by rw [← hc1v]⟩
                have hle2 : Le c1 a2 :=
                  ⟨by rw [← hc1v]; exact (checkBlock_scope h2).symm,
                   fun p hp => by rw [← hc1v] at hp; exact (mem_keepIn.mp hp).2,
                   fun p hp => by rw [← hc1v] at hp; exact (mem_keepIn.mp hp).2,
                   by rw [← hc1v]; exact hleases⟩
                simp only [stepStmt, List.mem_cons, List.not_mem_nil, or_false] at hmain
                -- either branch, followed by the rest, checks from the weaker joined state
                have hbranch : ∀ (a : CState) (br : List Stmt), Le c1 a →
                    checkBlock c br = some a → Ok ρ scope (.run (br ++ rest) tasks [] st) := by
                  intro a br hle hbr
                  obtain ⟨d', hd', hled⟩ := checkBlock_weaken hle hchk
                  exact Ok_of_sync hsync (by rw [checkBlock_append, hbr]; exact hd')
                    (by rw [← hled.leases_eq]; exact hdl)
                rcases hmain with rfl | rfl
                · exact hbranch a1 thn hle1 h1
                · exact hbranch a2 els hle2 h2
            · exact absurd hc1 (by simp)
        · exact absurd hc1 (by simp)
  · -- one live thread steps: a task, or a lane of the region that is running
    simp only [List.mem_flatMap] at hthread
    obtain ⟨y, hy, hcfg⟩ := hthread
    obtain ⟨_, rfl, hs'⟩ := thread_sync hsync hy cfg' hcfg
    exact ⟨c, hs', d, hchk, hdl⟩

end Ownership
end Cairn
