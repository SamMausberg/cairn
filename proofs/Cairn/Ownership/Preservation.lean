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

end Ownership
end Cairn
