/-
The lanes a region forks.  A lane has the shape of a task, so the machine races lanes
against each other and against live tasks with one mechanism; this file proves that the
lanes of a region the rule accepts are pairwise compatible.
-/
import Cairn.Ownership.Syntax

namespace Cairn
namespace Ownership

/-! ### The lanes a region forks

A lane has exactly the shape of a task -- a name and a footprint -- so the machine
races them against each other with the same machinery.  What differs is only where
the footprint comes from: a task is handed one at its spawn, a lane computes one from
its own index. -/

/-- One lane per index below `n`, holding what the body touches at that index. -/
def lanesOf (n : Nat) (body : List Touch) : List Task :=
  match n with
  | 0 => []
  | k + 1 => (k, body.map (Touch.borrow k)) :: lanesOf k body

@[simp] theorem Touch.borrow_root (a : Touch) (k : Nat) : (a.borrow k).1.root = a.root := by
  cases a <;> rfl

@[simp] theorem Touch.borrow_mode (a : Touch) (k : Nat) : (a.borrow k).2 = a.mode := by
  cases a <;> rfl

@[simp] theorem Touch.lease_root (a : Touch) : a.lease.1.root = a.root := by cases a <;> rfl

@[simp] theorem Touch.lease_base (a : Touch) : a.lease.1.base = a.root.var := by cases a <;> rfl

@[simp] theorem Touch.borrow_base (a : Touch) (k : Nat) : (a.borrow k).1.base = a.root.var := by
  cases a <;> rfl

/-- Every place a region names is guarded: a lane's own element is `[k, k+1)`, and
nothing else carries bounds at all.  So a region never traps. -/
@[simp] theorem Touch.lease_guard (ρ : Valuation) (a : Touch) : a.lease.1.guard ρ = true := by
  cases a <;> rfl

@[simp] theorem Touch.borrow_guard (ρ : Valuation) (a : Touch) (k : Nat) :
    (a.borrow k).1.guard ρ = true := by
  cases a <;> simp [Touch.borrow, Place.guard, Bound.eval]

/-- The lease check a region runs names `x[]`; a lane holds only its own element, so
it inherits the answer. -/
theorem races_borrow_of_lease {ρ : Valuation} {a : Touch} {k : Nat} {y : Borrow}
    (h : races ρ a.lease y = false) : races ρ (a.borrow k) y = false := by
  cases a <;> try exact h
  all_goals
    simp only [races, Bool.and_eq_false_iff, Touch.lease, Touch.borrow] at h ⊢
    exact h.imp (bool_false_of_imp meets_elems_of_part) id

/-- An access that writes puts its local in `written`. -/
theorem writesVar_of_mem {body : List Touch} {a : Touch} (ha : a ∈ body) (hm : a.mode = Mode.rw) :
    writesVar body a.root.var = true := by
  simp only [writesVar, List.any_eq_true]
  refine ⟨a, ha, ?_⟩
  have h1 : (a.mode == Mode.rw) = true := by rw [hm]; rfl
  rw [h1, beq_place_self, Bool.and_self]

/-- An access the facts place in a block of stride `S` holds that block of each lane. -/
theorem Touch.borrow_of_stride {a : Touch} {S : Nat} (h : a.stride = some S) (k : Nat) :
    a.borrow k = (.part a.root (.lit (k * S)) (.lit (k * S + S)), a.mode) := by
  cases a <;> simp_all [Touch.stride, Touch.borrow, Touch.root, Touch.mode]

/-- A `len` read holds the header, to read. -/
theorem Touch.borrow_of_unrecorded {a : Touch} (h : a.recorded = false) (k : Nat) :
    a.borrow k = (.hdr a.root, Mode.ro) := by
  cases a <;> simp_all [Touch.recorded, Touch.borrow, Touch.root]

/-- **Under the region rule every access to a local the lanes write has one stride.**  The
first recorded access fixes it, and each other one must agree. -/
theorem rule_stride {body : List Touch} (h : laneRule body = true) {a : Touch} (ha : a ∈ body)
    (hra : a.recorded = true) (hw : writesVar body a.root.var = true) :
    ∃ S, a.stride = some S ∧ ∀ b ∈ body, b.recorded = true → b.root.var = a.root.var → b.stride = some S := by
  have hall := (List.all_eq_true.mp h) a ha
  simp only [hra, hw, Bool.not_true, Bool.false_or, Bool.and_eq_true, List.all_eq_true, Bool.or_eq_true,
    Bool.not_eq_true', decide_eq_true_eq] at hall
  obtain ⟨hsome, hevery⟩ := hall
  cases hs : a.stride with
  | none => rw [hs] at hsome; exact Bool.noConfusion hsome
  | some S =>
      refine ⟨S, rfl, fun b hb hrb hvb => ?_⟩
      rcases hevery b hb with hno | hyes
      · simp [hrb, hvb] at hno
      · rw [hyes, hs]

/-- **Two lanes of an accepted region never race.**  If their borrows met with a write among
them they would be two accesses of one local the lanes write; the rule gives both one stride, so
each is its lane's own block, and two lanes' blocks of one stride are apart.  A `len` read is the
header, which no block meets. -/
theorem lane_borrows_dont_race {ρ : Valuation} {body : List Touch} (h : laneRule body = true)
    {k l : Nat} (hkl : k ≠ l) {a b : Touch} (ha : a ∈ body) (hb : b ∈ body) :
    races ρ (a.borrow k) (b.borrow l) = false := by
  cases hr : races ρ (a.borrow k) (b.borrow l) with
  | false => rfl
  | true =>
      exfalso
      obtain ⟨hmeet, hmode⟩ := Bool.and_eq_true_iff.mp hr
      have hvar : a.root.var = b.root.var := by
        have ht := (Bool.and_eq_true_iff.mp hmeet).1
        rw [Touch.borrow_root, Touch.borrow_root] at ht
        exact eq_of_beq (Bool.and_eq_true_iff.mp ht).1
      have hw : writesVar body a.root.var = true := by
        rcases Bool.or_eq_true_iff.mp hmode with hm | hm
        · rw [Touch.borrow_mode] at hm; exact writesVar_of_mem ha (eq_of_beq hm)
        · rw [Touch.borrow_mode] at hm
          rw [hvar]; exact writesVar_of_mem hb (eq_of_beq hm)
      have hwb : writesVar body b.root.var = true := by rw [← hvar]; exact hw
      cases hra : a.recorded <;> cases hrb : b.recorded
      · rw [Touch.borrow_of_unrecorded hra, Touch.borrow_of_unrecorded hrb] at hmode
        exact Bool.noConfusion hmode
      · obtain ⟨S, hS, _⟩ := rule_stride h hb hrb hwb
        rw [Touch.borrow_of_unrecorded hra, Touch.borrow_of_stride hS, meets_symm, meets_part_hdr] at hmeet
        exact Bool.noConfusion hmeet
      · obtain ⟨S, hS, _⟩ := rule_stride h ha hra hw
        rw [Touch.borrow_of_stride hS, Touch.borrow_of_unrecorded hrb, meets_part_hdr] at hmeet
        exact Bool.noConfusion hmeet
      · obtain ⟨S, hS, hall⟩ := rule_stride h ha hra hw
        rw [Touch.borrow_of_stride hS, Touch.borrow_of_stride (hall b hb hrb hvar.symm), meets_own_block hkl] at hmeet
        exact Bool.noConfusion hmeet

/-- Every lane carries an index below `n`, so two of them never share one. -/
theorem lanesOf_index_lt : ∀ (n : Nat) {body : List Touch} {T : Task},
    T ∈ lanesOf n body → T.1 < n := by
  intro n
  induction n with
  | zero => simp [lanesOf]
  | succ k ih =>
      intro body T hT
      rcases List.mem_cons.mp hT with rfl | h
      · exact Nat.lt_succ_self k
      · exact Nat.lt_succ_of_lt (ih h)

/-- Every borrow of a lane comes from an access of the body, taken at that lane's own
index. -/
theorem mem_lanesOf : ∀ (n : Nat) {body : List Touch} {T : Task}, T ∈ lanesOf n body →
    ∀ {y : Borrow}, y ∈ T.2 → ∃ a ∈ body, y = a.borrow T.1 := by
  intro n
  induction n with
  | zero => simp [lanesOf]
  | succ k ih =>
      intro body T hT y hy
      rcases List.mem_cons.mp hT with rfl | h
      · obtain ⟨a, ha, rfl⟩ := List.mem_map.mp hy
        exact ⟨a, ha, rfl⟩
      · exact ih h hy

/-- **The lanes of an accepted region are pairwise compatible.** -/
theorem lanesOf_pairwise (ρ : Valuation) : ∀ (n : Nat) {body : List Touch},
    laneRule body = true → (lanesOf n body).Pairwise (NoRacePair ρ) := by
  intro n
  induction n with
  | zero => intro body _; exact List.Pairwise.nil
  | succ k ih =>
      intro body h
      refine List.pairwise_cons.mpr ⟨?_, ih h⟩
      intro U hU x hx w hw
      have hne : k ≠ U.1 := Nat.ne_of_gt (lanesOf_index_lt k hU)
      obtain ⟨a, ha, hae⟩ := List.mem_map.mp hx
      obtain ⟨b, hb, hbe⟩ := mem_lanesOf k hU hw
      rw [← hae, hbe]
      exact lane_borrows_dont_race h hne ha hb

end Ownership
end Cairn
