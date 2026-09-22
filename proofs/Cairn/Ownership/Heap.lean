/-
The heap invariant the machine keeps by itself once no fault has happened, and its
preservation by releasing, allocating, binding and moving a cell.
-/
import Cairn.Ownership.Machine

namespace Cairn
namespace Ownership

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
    rcases dec_eq_or_ne a b with hab | hab
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
    rcases dec_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp; exact Val.noConfusion hp
    rw [henv p hpx] at hp
    have hne : b ≠ a := fun hba => hpx (h.uniq p x b hp (hba ▸ hx))
    show (updL st.live a false) b = true
    rw [hlive b hne]; exact h.liveOfEnv p b hp
  · intro p q b hp0 hq0
    have hp : (upd st.env x Val.moved) p = .owner b := hp0
    have hq : (upd st.env x Val.moved) q = .owner b := hq0
    rcases dec_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp; exact Val.noConfusion hp
    rcases dec_eq_or_ne q x with hqx | hqx
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
    rcases dec_eq_or_ne b a with hba | hba
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
    rcases dec_eq_or_ne p x with hpx | hpx
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
      rcases dec_eq_or_ne r x with hrx | hrx
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
    rcases dec_eq_or_ne b st.next with hbn | hbn
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
    rcases dec_eq_or_ne b st.next with hbn | hbn
    · show st.frees b = 0; rw [hbn]; exact hnf
    · rw [updL_other hbn] at hb; exact h.liveUnfreed b hb
  · intro b hb
    rcases dec_eq_or_ne b st.next with hbn | hbn
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
    rcases dec_eq_or_ne p y with hpy | hpy
    · exact absurd hp (hpy ▸ hy b)
    · rw [hother p hpy] at hp; exact h1'.liveOfEnv p b hp
  · intro p q b hp0 hq0
    have hp : (upd st1.env y v) p = .owner b := hp0
    have hq : (upd st1.env y v) q = .owner b := hq0
    rcases dec_eq_or_ne p y with hpy | hpy
    · exact absurd hp (hpy ▸ hy b)
    rcases dec_eq_or_ne q y with hqy | hqy
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
    rcases dec_eq_or_ne p x with hpx | hpx
    · rw [hpx, upd_same] at hp; exact absurd hp (fun hc => Val.noConfusion hc)
    rcases dec_eq_or_ne p y with hpy | hpy
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
    rcases dec_eq_or_ne p y with hpy | hpy
    · exact absurd hpe (hpy ▸ h2y b)
    rcases dec_eq_or_ne p x with hpx | hpx
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

end Ownership
end Cairn
