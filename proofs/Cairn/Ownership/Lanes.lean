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
  cases a
  · exact decide_eq_true (Nat.le_succ k)
  · rfl
  · rfl
  · rfl

/-- The lease check a region runs names `x[]`; a lane holds only its own element, so
it inherits the answer. -/
theorem races_borrow_of_lease {ρ : Valuation} {a : Touch} {k : Nat} {y : Borrow}
    (h : races ρ a.lease y = false) : races ρ (a.borrow k) y = false := by
  cases a with
  | other r m => exact h
  | whole r m => exact h
  | len r => exact h
  | elem r m =>
      show (meets ρ (.part r (.lit k) (.lit (k + 1))) y.1 && ((m == Mode.rw) || (y.2 == Mode.rw)))
        = false
      cases hm : meets ρ (Place.part r (.lit k) (.lit (k + 1))) y.1 with
      | false => rfl
      | true =>
          have hlease : (meets ρ (Place.elems r) y.1 && ((m == Mode.rw) || (y.2 == Mode.rw)))
              = false := h
          rw [meets_elems_of_part hm, Bool.true_and] at hlease
          rw [Bool.true_and]
          exact hlease

/-- An access that writes puts its local in `written`. -/
theorem writesVar_of_mem {body : List Touch} {a : Touch} (ha : a ∈ body) (hm : a.mode = Mode.rw) :
    writesVar body a.root.var = true := by
  simp only [writesVar, List.any_eq_true]
  refine ⟨a, ha, ?_⟩
  have h1 : (a.mode == Mode.rw) = true := by rw [hm]; rfl
  rw [h1, beq_place_self, Bool.and_self]

/-- **Under the region rule a lane holds only its own element, or the header.**  A
write is always the lane's own element; a `len` read is the header; nothing else
survives on a local the lanes write. -/
theorem borrow_shape {body : List Touch} (h : laneRule body = true) {a : Touch} (ha : a ∈ body)
    (hw : writesVar body a.root.var = true) (k : Nat) :
    a.borrow k = (.part a.root (.lit k) (.lit (k + 1)), a.mode) ∨
      a.borrow k = (.hdr a.root, Mode.ro) := by
  have hall := (List.all_eq_true.mp h) a ha
  cases a with
  | elem r m => exact Or.inl rfl
  | len r => exact Or.inr rfl
  | other r m =>
      have h2 : (!writesVar body r.var || false) = true := hall
      rw [show writesVar body r.var = true from hw] at h2
      exact Bool.noConfusion h2
  | whole r m =>
      have h2 : (!writesVar body r.var || false) = true := hall
      rw [show writesVar body r.var = true from hw] at h2
      exact Bool.noConfusion h2

/-- **Two lanes of an accepted region never race.**  If their borrows met with a write
among them they would be two writes, or a write and a read, of one local; the rule
then makes both of them the lane's own element, and the indices differ. -/
theorem lane_borrows_dont_race {ρ : Valuation} {body : List Touch} (h : laneRule body = true)
    {k l : Nat} (hkl : k ≠ l) {a b : Touch} (ha : a ∈ body) (hb : b ∈ body) :
    races ρ (a.borrow k) (b.borrow l) = false := by
  cases hr : races ρ (a.borrow k) (b.borrow l) with
  | false => rfl
  | true =>
      exfalso
      obtain ⟨hmeet, hmode⟩ := and_parts hr
      have hvar : a.root.var = b.root.var := by
        have ht : (a.borrow k).1.root.touches (b.borrow l).1.root = true :=
          (and_parts hmeet).1
        rw [Touch.borrow_root, Touch.borrow_root] at ht
        exact eq_of_beq (and_parts ht).1
      have hw : writesVar body a.root.var = true := by
        rcases Bool.or_eq_true_iff.mp hmode with hm | hm
        · rw [Touch.borrow_mode] at hm; exact writesVar_of_mem ha (eq_of_beq hm)
        · rw [Touch.borrow_mode] at hm
          rw [hvar]; exact writesVar_of_mem hb (eq_of_beq hm)
      have hwb : writesVar body b.root.var = true := by rw [← hvar]; exact hw
      rcases borrow_shape h ha hw k with hab | hab <;>
        rcases borrow_shape h hb hwb l with hbb | hbb <;>
        rw [hab, hbb] at hmeet hmode
      · rw [meets_own_element hkl] at hmeet; exact Bool.noConfusion hmeet
      · rw [meets_part_hdr] at hmeet; exact Bool.noConfusion hmeet
      · rw [meets_symm, meets_part_hdr] at hmeet; exact Bool.noConfusion hmeet
      · exact Bool.noConfusion hmode

/-- Every lane carries an index below `n`, so two of them never share one. -/
theorem lanesOf_index_lt : ∀ (n : Nat) {body : List Touch} {T : Task},
    T ∈ lanesOf n body → T.1 < n := by
  intro n
  induction n with
  | zero => intro body T hT; exact absurd hT List.not_mem_nil
  | succ k ih =>
      intro body T hT
      rcases List.mem_cons.mp hT with h1 | h2
      · rw [h1]; exact Nat.lt_succ_self k
      · exact Nat.lt_succ_of_lt (ih h2)

/-- Every borrow of a lane comes from an access of the body, taken at that lane's own
index. -/
theorem mem_lanesOf : ∀ (n : Nat) {body : List Touch} {T : Task}, T ∈ lanesOf n body →
    ∀ {y : Borrow}, y ∈ T.2 → ∃ a ∈ body, y = a.borrow T.1 := by
  intro n
  induction n with
  | zero => intro body T hT; exact absurd hT List.not_mem_nil
  | succ k ih =>
      intro body T hT y hy
      rcases List.mem_cons.mp hT with h1 | h2
      · subst h1
        obtain ⟨a, haa, hae⟩ := List.mem_map.mp hy
        exact ⟨a, haa, hae.symm⟩
      · exact ih h2 hy

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
      have hlt : U.1 < k := lanesOf_index_lt k hU
      have hne : k ≠ U.1 := by
        intro hc
        rw [hc] at hlt
        exact Nat.lt_irrefl U.1 hlt
      obtain ⟨a, ha, hae⟩ := List.mem_map.mp hx
      obtain ⟨b, hb, hbe⟩ := mem_lanesOf k hU hw
      rw [← hae, hbe]
      exact lane_borrows_dont_race h hne ha hb

end Ownership
end Cairn
