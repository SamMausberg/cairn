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

/-- The heap invariant does not look at the live groups. -/
theorem MemOk.regroup {st : State} {scope} (h : MemOk st scope) (G : List (Ticket × Nat)) :
    MemOk { st with groups := G } scope :=
  ⟨h.liveOfEnv, h.uniq, h.covered, h.liveUnfreed, h.freedOnce, h.beyond⟩

theorem MemOk.lt_next {st : State} {scope} (h : MemOk st scope) {a : AllocId}
    (ha : st.live a = true) : a < st.next := by
  rcases Nat.lt_or_ge a st.next with hlt | hge
  · exact hlt
  · rw [(h.beyond a hge).1] at ha; exact Bool.noConfusion ha

/-! ### Reading a point update

Every step below changes the local map or the liveness map at one point.  These two
say what such a map holds, so that each clause of the invariant becomes a case split
on "the point that changed, or another". -/

theorem upd_eq_owner {f : Var → Val} {x p : Var} {v : Val} {b : AllocId} :
    upd f x v p = .owner b ↔ (p = x ∧ v = .owner b) ∨ (p ≠ x ∧ f p = .owner b) := by
  unfold upd; split <;> simp_all

theorem updL_eq_true {f : AllocId → Bool} {a b : AllocId} {v : Bool} :
    updL f a v b = true ↔ (b = a ∧ v = true) ∨ (b ≠ a ∧ f b = true) := by
  unfold updL; split <;> simp_all

/-- **The heap invariant depends on the locals only through who holds what.**  A new
local map that holds exactly the cells the old one held, each in one local of the
scope, keeps every clause, because the heap itself is untouched. -/
theorem memOk_env {st : State} {scope : List Var} (h : MemOk st scope) {f : Var → Val}
    (hheld : ∀ p a, f p = .owner a → ∃ q, st.env q = .owner a)
    (huniq : ∀ p q a, f p = .owner a → f q = .owner a → p = q)
    (hcover : ∀ a, st.live a = true → ∃ p ∈ scope, f p = .owner a) :
    MemOk { st with env := f } scope :=
  ⟨fun p a hp => (hheld p a hp).elim fun q hq => h.liveOfEnv q a hq, huniq, hcover,
    h.liveUnfreed, h.freedOnce, h.beyond⟩

/-! ### Releasing -/

/-- A successful release either found nothing to free, or freed exactly the cell the
place held. -/
theorem release_cases {st st' : State} {x : Var} (hr : release x st = .ok st') :
    (st' = st ∧ ∀ a, st.env x ≠ .owner a) ∨
    (∃ a, st.env x = .owner a ∧ st.live a = true ∧
      st' = { st with env := upd st.env x .moved, live := updL st.live a false,
                      frees := updN st.frees a (st.frees a + 1) }) := by
  revert hr
  unfold release
  split
  · next a heq =>
      split
      · next hl => exact fun hr => Or.inr ⟨a, heq, hl, (Except.ok.inj hr).symm⟩
      · exact fun hr => absurd hr (by simp)
  all_goals
    next heq =>
      exact fun hr => Or.inl ⟨(Except.ok.inj hr).symm,
        fun b hb => by rw [heq] at hb; exact Val.noConfusion hb⟩

theorem release_ok {st : State} {scope} (h : MemOk st scope) (x : Var) :
    ∃ st', release x st = .ok st' := by
  unfold release
  split
  · next a heq => rw [h.liveOfEnv x a heq]; exact ⟨_, rfl⟩
  all_goals exact ⟨st, rfl⟩

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
    rw [upd_same]; exact nofun

/-- Releasing the cell `a` held in `x`: nothing else held it, so every clause about
another cell is as it was, and `a` itself goes from live to released once. -/
theorem memOk_release {st st' : State} {scope} {x : Var} (h : MemOk st scope)
    (hr : release x st = .ok st') : MemOk st' scope := by
  rcases release_cases hr with ⟨rfl, _⟩ | ⟨a, hx, ha, rfl⟩
  · exact h
  have hother : ∀ p b, upd st.env x .moved p = .owner b → p ≠ x ∧ b ≠ a ∧ st.env p = .owner b := by
    intro p b hp
    obtain ⟨hpx, hp⟩ := (upd_eq_owner.mp hp).resolve_left fun ⟨_, e⟩ => nomatch e
    exact ⟨hpx, fun hba => hpx (h.uniq p x b hp (hba ▸ hx)), hp⟩
  have hkept : ∀ b, updL st.live a false b = true → b ≠ a ∧ st.live b = true := fun b hb =>
    (updL_eq_true.mp hb).resolve_left fun ⟨_, e⟩ => nomatch e
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p b hp
    obtain ⟨_, hba, hp⟩ := hother p b hp
    exact updL_eq_true.mpr (Or.inr ⟨hba, h.liveOfEnv p b hp⟩)
  · intro p q b hp hq
    exact h.uniq p q b (hother p b hp).2.2 (hother q b hq).2.2
  · intro b hb
    obtain ⟨hba, hb⟩ := hkept b hb
    obtain ⟨p, hp, hpe⟩ := h.covered b hb
    have hpx : p ≠ x := fun e => hba (Val.owner.inj ((e ▸ hpe).symm.trans hx))
    exact ⟨p, hp, upd_eq_owner.mpr (Or.inr ⟨hpx, hpe⟩)⟩
  · intro b hb
    obtain ⟨hba, hb⟩ := hkept b hb
    show updN st.frees a (st.frees a + 1) b = 0
    rw [updN_other hba]; exact h.liveUnfreed b hb
  · intro b hb
    rcases dec_eq_or_ne b a with hba | hba
    · right; rw [hba]
      show updN st.frees a (st.frees a + 1) a = 1
      rw [updN_same, h.liveUnfreed a ha]
    rcases h.freedOnce b hb with hl | hf
    · exact Or.inl (updL_eq_true.mpr (Or.inr ⟨hba, hl⟩))
    · right
      show updN st.frees a (st.frees a + 1) b = 1
      rw [updN_other hba]; exact hf
  · intro b hb
    have hba : b ≠ a := fun e => absurd (h.lt_next ha) (Nat.not_lt.mpr (e ▸ hb))
    exact ⟨by show updL st.live a false b = false; rw [updL_other hba]; exact (h.beyond b hb).1,
      by show updN st.frees a (st.frees a + 1) b = 0; rw [updN_other hba]; exact (h.beyond b hb).2⟩

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

/-- Putting a brand new cell in a local that holds none: the frontier moves by one, the
new cell is live and held by `x` alone, and every old cell is where it was. -/
theorem memOk_fresh {st : State} {scope} {x : Var} (h : MemOk st scope) (hx : x ∈ scope)
    (hnx : ∀ a, st.env x ≠ .owner a) :
    MemOk { st with env := upd st.env x (.owner st.next), live := updL st.live st.next true,
                    next := st.next + 1 } scope := by
  have hnl : st.live st.next = false := (h.beyond st.next (Nat.le_refl _)).1
  have henv : ∀ p b, upd st.env x (.owner st.next) p = .owner b ↔
      (p = x ∧ st.next = b) ∨ (p ≠ x ∧ st.env p = .owner b) := by
    intro p b; rw [upd_eq_owner]; simp
  have hold : ∀ p b, st.env p = .owner b → b ≠ st.next := fun p b hp e => by
    have := h.liveOfEnv p b hp
    rw [e, hnl] at this; exact Bool.noConfusion this
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
  · intro p b hp
    rcases (henv p b).mp hp with ⟨_, rfl⟩ | ⟨_, hp⟩
    · exact updL_eq_true.mpr (Or.inl ⟨rfl, rfl⟩)
    · exact updL_eq_true.mpr (Or.inr ⟨hold p b hp, h.liveOfEnv p b hp⟩)
  · intro p q b hp hq
    rcases dec_eq_or_ne b st.next with rfl | hbn
    · have hnew : ∀ r, upd st.env x (.owner st.next) r = .owner st.next → r = x := fun r hr =>
        ((henv r _).mp hr).elim (·.1) fun ⟨_, hr⟩ => absurd rfl (hold r _ hr)
      rw [hnew p hp, hnew q hq]
    · have hold' : ∀ r, upd st.env x (.owner st.next) r = .owner b → st.env r = .owner b :=
        fun r hr => ((henv r b).mp hr).elim (fun ⟨_, e⟩ => absurd e.symm hbn) (·.2)
      exact h.uniq p q b (hold' p hp) (hold' q hq)
  · intro b hb
    rcases updL_eq_true.mp hb with ⟨rfl, _⟩ | ⟨_, hb⟩
    · exact ⟨x, hx, (henv x _).mpr (Or.inl ⟨rfl, rfl⟩)⟩
    · obtain ⟨p, hp, hpe⟩ := h.covered b hb
      exact ⟨p, hp, (henv p b).mpr (Or.inr ⟨fun e => hnx b (e ▸ hpe), hpe⟩)⟩
  · intro b hb
    rcases updL_eq_true.mp hb with ⟨rfl, _⟩ | ⟨_, hb⟩
    · exact (h.beyond _ (Nat.le_refl _)).2
    · exact h.liveUnfreed b hb
  · intro b hb
    have hb : b < st.next + 1 := hb
    rcases dec_eq_or_ne b st.next with hbn | hbn
    · exact Or.inl (updL_eq_true.mpr (Or.inl ⟨hbn, rfl⟩))
    rcases h.freedOnce b (Nat.lt_of_le_of_ne (Nat.le_of_lt_succ hb) hbn) with hl | hf
    · exact Or.inl (updL_eq_true.mpr (Or.inr ⟨hbn, hl⟩))
    · exact Or.inr hf
  · intro b hb
    have hb : st.next + 1 ≤ b := hb
    have hbn : b ≠ st.next := fun e => Nat.not_succ_le_self _ (e ▸ hb)
    exact ⟨by show updL st.live st.next true b = false
              rw [updL_other hbn]; exact (h.beyond b (Nat.le_of_succ_le hb)).1,
           (h.beyond b (Nat.le_of_succ_le hb)).2⟩

theorem reallocAt_ok {st : State} {scope} (h : MemOk st scope) (x : Var) :
    ∃ st', reallocAt x st = .ok st' := by
  obtain ⟨st1, h1⟩ := release_ok h x
  exact ⟨_, by unfold reallocAt; rw [h1]⟩

theorem reallocAt_spec {st st' : State} {x : Var} (hr : reallocAt x st = .ok st') :
    ∃ st1, release x st = .ok st1 ∧
      st' = { st1 with env := upd st1.env x (.owner st1.next), live := updL st1.live st1.next true,
                       next := st1.next + 1 } := by
  unfold reallocAt at hr
  split at hr
  · exact absurd hr (by simp)
  · next st1 h1 => exact ⟨st1, h1, (Except.ok.inj hr).symm⟩

theorem memOk_reallocAt {st st' : State} {scope} {x : Var} (h : MemOk st scope)
    (hx : x ∈ scope) (hr : reallocAt x st = .ok st') : MemOk st' scope := by
  obtain ⟨st1, h1, rfl⟩ := reallocAt_spec hr
  exact memOk_fresh (memOk_release h h1) hx (release_env_self h1)

theorem reallocAt_env_ne {st st' : State} {x p : Var} (hr : reallocAt x st = .ok st')
    (hne : p ≠ x) : st'.env p = st.env p := by
  obtain ⟨st1, h1, rfl⟩ := reallocAt_spec hr
  show upd st1.env x _ p = _
  rw [upd_other hne]; exact release_env_ne h1 hne

theorem reallocAt_env_self {st st' : State} {x : Var} (hr : reallocAt x st = .ok st') :
    ∃ a, st'.env x = .owner a := by
  obtain ⟨st1, h1, rfl⟩ := reallocAt_spec hr
  exact ⟨st1.next, upd_same _ _ _⟩

/-! ### What the heap steps leave alone -/

theorem release_groups {st st' : State} {x : Var} (hr : release x st = .ok st') :
    st'.groups = st.groups := by
  rcases release_cases hr with ⟨rfl, _⟩ | ⟨_, _, _, rfl⟩ <;> rfl

theorem reallocAt_groups {st st' : State} {x : Var} (hr : reallocAt x st = .ok st') :
    st'.groups = st.groups := by
  obtain ⟨st1, h1, rfl⟩ := reallocAt_spec hr
  exact (release_groups h1 : st1.groups = st.groups)

/-! ### Binding a value -/

theorem bindAt_ok {st : State} {scope} (h : MemOk st scope) (y : Var) (v : Val) :
    ∃ st', bindAt y v st = .ok st' := by
  obtain ⟨st1, h1⟩ := release_ok h y
  exact ⟨_, by unfold bindAt; rw [h1]⟩

theorem bindAt_spec {st st' : State} {y : Var} {v : Val} (hb : bindAt y v st = .ok st') :
    ∃ st1, release y st = .ok st1 ∧ st' = { st1 with env := upd st1.env y v } := by
  unfold bindAt at hb
  split at hb
  · exact absurd hb (by simp)
  · next st1 h1 => exact ⟨st1, h1, (Except.ok.inj hb).symm⟩

theorem bindAt_groups {st st' : State} {y : Var} {v : Val} (hb : bindAt y v st = .ok st') :
    st'.groups = st.groups := by
  obtain ⟨st1, h1, rfl⟩ := bindAt_spec hb
  exact (release_groups h1 : st1.groups = st.groups)

theorem bindAt_env_ne {st st' : State} {y p : Var} {v : Val} (hb : bindAt y v st = .ok st')
    (hne : p ≠ y) : st'.env p = st.env p := by
  obtain ⟨st1, h1, rfl⟩ := bindAt_spec hb
  show upd st1.env y v p = _
  rw [upd_other hne]; exact release_env_ne h1 hne

theorem bindAt_env_self {st st' : State} {y : Var} {v : Val} (hb : bindAt y v st = .ok st') :
    st'.env y = v := by
  obtain ⟨st1, h1, rfl⟩ := bindAt_spec hb
  exact upd_same _ _ _

/-- Binding a value that is not a cell keeps the heap invariant: after the release,
every cell is held where it was. -/
theorem memOk_bindAt {st st' : State} {scope} {y : Var} {v : Val} (h : MemOk st scope)
    (hv : ∀ a, v ≠ .owner a) (hb : bindAt y v st = .ok st') : MemOk st' scope := by
  obtain ⟨st1, h1, rfl⟩ := bindAt_spec hb
  have h1' := memOk_release h h1
  have hold : ∀ p a, upd st1.env y v p = .owner a → p ≠ y ∧ st1.env p = .owner a :=
    fun p a hp => (upd_eq_owner.mp hp).resolve_left fun ⟨_, e⟩ => hv a e
  refine memOk_env h1' (fun p a hp => ⟨p, (hold p a hp).2⟩)
    (fun p q a hp hq => h1'.uniq p q a (hold p a hp).2 (hold q a hq).2) fun a ha => ?_
  obtain ⟨p, hp, hpe⟩ := h1'.covered a ha
  exact ⟨p, hp, upd_eq_owner.mpr (Or.inr ⟨fun e => release_env_self h1 a (e ▸ hpe), hpe⟩)⟩

/-- Moving a cell from `x` to `y`: the heap is untouched, and the holders are relabelled
so that the cell stays held exactly once.  This is the one step where the intermediate
state would break the invariant, which is why it is proved in one piece. -/
theorem memOk_move {st st1 : State} {scope} {y x : Var} {a : AllocId} (h : MemOk st scope)
    (hy : y ∈ scope) (hxy : y ≠ x) (hx : st.env x = .owner a)
    (hb : bindAt y (st.env x) st = .ok st1) :
    MemOk { st1 with env := upd st1.env x .moved } scope := by
  obtain ⟨st2, h2r, rfl⟩ := bindAt_spec hb
  have h2 := memOk_release h h2r
  have h2x : st2.env x = .owner a := (release_env_ne h2r (Ne.symm hxy)).trans hx
  -- who holds what after the move: `a` in `y`, and every other cell where it was
  have hkey : ∀ p b, upd (upd st2.env y (st.env x)) x .moved p = .owner b ↔
      (p = y ∧ b = a) ∨ (p ≠ x ∧ p ≠ y ∧ st2.env p = .owner b) := by
    intro p b
    rw [upd_eq_owner, upd_eq_owner, hx]
    constructor
    · rintro (⟨_, e⟩ | ⟨hpx, ⟨rfl, e⟩ | ⟨hpy, hp⟩⟩)
      · exact nomatch e
      · exact Or.inl ⟨rfl, (Val.owner.inj e).symm⟩
      · exact Or.inr ⟨hpx, hpy, hp⟩
    · rintro (⟨rfl, rfl⟩ | ⟨hpx, hpy, hp⟩)
      · exact Or.inr ⟨hxy, Or.inl ⟨rfl, rfl⟩⟩
      · exact Or.inr ⟨hpx, Or.inr ⟨hpy, hp⟩⟩
  refine memOk_env h2 ?_ ?_ ?_
  · intro p b hp
    rcases (hkey p b).mp hp with ⟨_, rfl⟩ | ⟨_, _, hp⟩
    · exact ⟨x, h2x⟩
    · exact ⟨p, hp⟩
  · intro p q b hp hq
    rcases (hkey p b).mp hp with ⟨rfl, rfl⟩ | ⟨hpx, hpy, hp2⟩ <;>
      rcases (hkey q _).mp hq with ⟨rfl, hq2⟩ | ⟨hqx, hqy, hq2⟩
    · rfl
    · exact absurd (h2.uniq q x _ hq2 h2x) hqx
    · exact absurd (h2.uniq p x _ hp2 (hq2 ▸ h2x)) hpx
    · exact h2.uniq p q b hp2 hq2
  · intro b hb
    obtain ⟨p, hp, hpe⟩ := h2.covered b hb
    have hpy : p ≠ y := fun e => release_env_self h2r b (e ▸ hpe)
    rcases dec_eq_or_ne p x with rfl | hpx
    · exact ⟨y, hy, (hkey y b).mpr (Or.inl ⟨rfl, Val.owner.inj (hpe.symm.trans h2x)⟩)⟩
    · exact ⟨p, hp, (hkey p b).mpr (Or.inr ⟨hpx, hpy, hpe⟩)⟩

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
