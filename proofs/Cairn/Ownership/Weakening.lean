/-
Forgetting.  After an `if` the checker goes on from the join, which claims no more than the path
the machine took.  `Le` is "claims no more than", `Checks` is acceptance that may forget between
two statements, and every accepted program has such a derivation.
-/
import Cairn.Ownership.Checker

namespace Cairn
namespace Ownership

/-! ## Claiming less

A state claims less when it knows fewer live locals and holds more leases.  A lease may name more
than the borrow it stands for (`Within`), and a part keeps its bounds only if the stronger state
lent it too: the chain may use the bounds of a part only where its guard ran. -/

/-- Every task of `M` is held through a lease of `L` under the same name. -/
def Covers (L M : List Task) : Prop :=
  ∀ T ∈ M, ∃ U ∈ L, U.1 = T.1 ∧ ∀ y ∈ T.2, ∃ z ∈ U.2, Within y z

theorem Covers.refl (L : List Task) : Covers L L :=
  fun T hT => ⟨T, hT, rfl, fun y hy => ⟨y, hy, Within.refl y⟩⟩

theorem Covers.trans {L M N : List Task} (h1 : Covers L M) (h2 : Covers M N) : Covers L N := by
  intro T hT
  obtain ⟨U, hU, hUT, hy⟩ := h2 T hT
  obtain ⟨V, hV, hVU, hz⟩ := h1 U hU
  refine ⟨V, hV, hVU.trans hUT, fun y hy' => ?_⟩
  obtain ⟨z, hz', hyz⟩ := hy y hy'
  obtain ⟨w, hw, hzw⟩ := hz z hz'
  exact ⟨w, hw, hyz.trans hzw⟩

theorem Covers.sub {L M N : List Task} (h : Covers L M) (hsub : ∀ T ∈ N, T ∈ M) : Covers L N :=
  fun T hT => h T (hsub T hT)

theorem Covers.cons {L M : List Task} (T : Task) (h : Covers L M) : Covers (T :: L) (T :: M) := by
  intro U hU
  rcases List.mem_cons.mp hU with rfl | hU
  · exact ⟨U, List.mem_cons_self .., rfl, fun y hy => ⟨y, hy, Within.refl y⟩⟩
  · obtain ⟨V, hV, h1, h2⟩ := h U hU
    exact ⟨V, List.mem_cons_of_mem _ hV, h1, h2⟩

/-- Waiting for `t` removes the same tasks on both sides, since a lease carries its task's name. -/
theorem Covers.filter {L M : List Task} (h : Covers L M) (t : Ticket) :
    Covers (L.filter fun T => !decide (T.1 = t)) (M.filter fun T => !decide (T.1 = t)) := by
  intro T hT
  have hT' := List.mem_filter.mp hT
  obtain ⟨U, hU, hUT, hy⟩ := h T hT'.1
  exact ⟨U, List.mem_filter.mpr ⟨hU, by rw [hUT]; exact hT'.2⟩, hUT, hy⟩

/-- `c'` claims no more than `c`. -/
structure Le (c' c : CState) : Prop where
  scalars : ∀ p ∈ c'.scalars, p ∈ c.scalars
  owners : ∀ p ∈ c'.owners, p ∈ c.owners
  groups : ∀ g, g ∈ c'.groups ↔ g ∈ c.groups
  leases : Covers c'.leases c.leases
  exact : ∀ T ∈ c'.leases, ∀ y ∈ T.2, y.1.range = none ∨ ∃ U ∈ c.leases, y ∈ U.2

theorem Le.refl (c : CState) : Le c c :=
  ⟨fun _ h => h, fun _ h => h, fun _ => Iff.rfl, Covers.refl _, fun T hT _ hy => Or.inr ⟨T, hT, hy⟩⟩

/-! ## The join claims no more than either path -/

theorem within_vague (y : Borrow) : Within y y.vague := by
  rcases y with ⟨p, m⟩
  cases p <;> first | exact Within.refl _ | exact ⟨rfl, Or.inr ⟨_, _, _, rfl, rfl⟩⟩

theorem vague_range (y : Borrow) : y.vague.1.range = none := by
  rcases y with ⟨p, m⟩
  cases p <;> rfl

/-- One side of the join: every lease of `L1` survives under its name, and a borrow the join
keeps exact was lent in `L1`. -/
theorem settle_le {L1 L2 L : List Task} (hc : L = L1 ∨ L = L2)
    (hs : ∀ T ∈ L2, T ∈ L1 ∨ T ∈ L2.filter fun T => !has L1 T) :
    Covers (settle L1 L2) L ∧ ∀ T ∈ settle L1 L2, ∀ y ∈ T.2, y.1.range = none ∨ ∃ U ∈ L, y ∈ U.2 := by
  constructor
  · intro T hT
    have hin : T ∈ L1 ++ L2.filter fun T => !has L1 T := by
      rcases hc with hc | hc <;> rw [hc] at hT
      · exact List.mem_append_left _ hT
      · rcases hs T hT with h | h
        · exact List.mem_append_left _ h
        · exact List.mem_append_right _ h
    refine ⟨_, List.mem_map_of_mem hin, rfl, fun y hy => ⟨_, List.mem_map_of_mem hy, ?_⟩⟩
    split
    · exact Within.refl y
    · exact within_vague y
  · intro T hT y hy
    obtain ⟨T0, _, rfl⟩ := List.mem_map.mp hT
    obtain ⟨y0, _, rfl⟩ := List.mem_map.mp hy
    split
    · next hboth =>
        right
        have hlent : lentIn L1 T0.1 y0 = true ∧ lentIn L2 T0.1 y0 = true := by simpa using hboth
        have found : ∀ L, lentIn L T0.1 y0 = true → ∃ U ∈ L, y0 ∈ U.2 := fun L hL => by
          obtain ⟨U, hU, hUy⟩ := List.any_eq_true.mp hL
          exact ⟨U, hU, has_iff.mp (Bool.and_eq_true_iff.mp hUy).2⟩
        rcases hc with hc | hc <;> rw [hc]
        · exact found L1 hlent.1
        · exact found L2 hlent.2
    · exact Or.inl (vague_range y0)

/-- **The join claims no more than either path.** -/
theorem joinOf_le {c1 c2 j : CState} (h : joinOf c1 c2 = some j) : Le j c1 ∧ Le j c2 := by
  unfold joinOf at h
  split at h
  · next hcond =>
      have hj := (Option.some.inj h).symm
      simp only [Bool.and_eq_true, List.all_eq_true, has_iff, decide_eq_true_eq] at hcond
      obtain ⟨⟨h12, h21⟩, _⟩ := hcond
      have hs : ∀ T ∈ c2.leases, T ∈ c1.leases ∨ T ∈ c2.leases.filter fun T => !has c1.leases T := by
        intro T hT
        cases hh : has c1.leases T
        · exact Or.inr (List.mem_filter.mpr ⟨hT, by rw [hh]; rfl⟩)
        · exact Or.inl (has_iff.mp hh)
      subst hj
      obtain ⟨cov1, ex1⟩ := settle_le (L := c1.leases) (Or.inl rfl) hs
      obtain ⟨cov2, ex2⟩ := settle_le (L := c2.leases) (Or.inr rfl) hs
      exact ⟨⟨fun p hp => (mem_keepIn.mp hp).1, fun p hp => (mem_keepIn.mp hp).1, fun _ => Iff.rfl,
          cov1, ex1⟩,
        ⟨fun p hp => (mem_keepIn.mp hp).2, fun p hp => (mem_keepIn.mp hp).2,
          fun g => ⟨h12 g, h21 g⟩, cov2, ex2⟩⟩
  · exact absurd h nofun

/-! ## Acceptance that may forget -/

/-- `Checks scope c code`: the checker accepts `code` from `c`, and may forget between two
statements.  An `if` is where it forgets: the code after it is checked from the join. -/
inductive Checks (scope : List Var) : CState → List Stmt → Prop where
  | nil {c : CState} : c.leases = [] → c.groups = [] → Checks scope c []
  | cons {c c' : CState} {s : Stmt} {rest : List Stmt} :
      checkStmt scope c s = some c' → Checks scope c' rest → Checks scope c (s :: rest)
  | weaken {c c' : CState} {code : List Stmt} : Le c' c → Checks scope c' code → Checks scope c code

/-- A block the checker accepts, followed by code that checks from a state claiming no more than
where the block ends. -/
theorem checks_append {scope : List Var} {j : CState} {rest : List Stmt} (hj : Checks scope j rest) :
    ∀ (ss : List Stmt) (c a : CState), checkBlock scope c ss = some a → Le j a →
      Checks scope c (ss ++ rest)
  | [], c, a, h, hle => by
      rw [checkBlock_nil, Option.some.injEq] at h
      subst h; exact .weaken hle hj
  | s :: ss, c, a, h, hle => by
      rw [checkBlock_cons] at h
      cases h1 : checkStmt scope c s with
      | none => rw [h1] at h; exact absurd h nofun
      | some c1 => rw [h1] at h; exact .cons h1 (checks_append hj ss c1 a h hle)

/-- Every accepted program has a derivation. -/
theorem Checks.of_accepts {p : Program} (h : accepts p = true) : Checks p.scope CState.start p.body := by
  unfold accepts at h
  split at h
  · next d hd =>
      simp only [Bool.and_eq_true, List.isEmpty_iff] at h
      simpa using checks_append (.nil h.1 h.2) p.body _ d hd (Le.refl d)
  · exact absurd h nofun

/-- A list with no member is empty; `List.eq_nil_iff_forall_not_mem` would reach for choice. -/
theorem nil_of_forall {α : Type} {l : List α} (h : ∀ a ∈ l, False) : l = [] := by
  cases l with
  | nil => rfl
  | cons a _ => exact (h a (List.mem_cons_self ..)).elim

/-- Where the code ends, nothing is still lent and no group is live. -/
theorem Checks.done {scope : List Var} {c : CState} {code : List Stmt} (h : Checks scope c code)
    (hc : code = []) : c.leases = [] ∧ c.groups = [] := by
  induction h with
  | nil h1 h2 => exact ⟨h1, h2⟩
  | cons _ _ _ => exact absurd hc nofun
  | weaken hle _ ih =>
      obtain ⟨h1, h2⟩ := ih hc
      refine ⟨nil_of_forall fun T hT => ?_, nil_of_forall fun g hg => ?_⟩
      · obtain ⟨U, hU, _⟩ := hle.leases T hT
        rw [h1] at hU; exact List.not_mem_nil hU
      · have := (hle.groups g).mpr hg
        rw [h2] at this; exact List.not_mem_nil this

end Ownership
end Cairn
