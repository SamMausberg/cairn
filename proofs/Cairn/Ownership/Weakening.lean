/-
Weakening.  A branch join throws locals away, so whatever checks from the joined state
must check from either branch's stronger state; this is what stepping into a branch needs.
-/
import Cairn.Ownership.Checker

namespace Cairn
namespace Ownership

/-! ## Weakening

A branch join throws locals away, so after `if` the checker holds a *weaker* state
than either branch produced.  Stepping into a branch therefore has to know that
whatever checks from the join also checks from the branch. -/

/-- `c` claims no more than `d` does. -/
structure Le (c d : CState) : Prop where
  scope_eq : c.scope = d.scope
  scalars : ∀ p, p ∈ c.scalars → p ∈ d.scalars
  owners : ∀ p, p ∈ c.owners → p ∈ d.owners
  leases_eq : c.leases = d.leases

theorem Le.refl (c : CState) : Le c c := ⟨rfl, fun _ h => h, fun _ h => h, rfl⟩

@[simp] theorem contains_iff_mem {l : List Var} {p : Var} : l.contains p = true ↔ p ∈ l := by
  simp

theorem mayAccess_congr {c d : CState} (h : Le c d) (x : Borrow) :
    d.mayAccess x = c.mayAccess x := by
  simp [CState.mayAccess, CState.inPlay, h.leases_eq]

theorem livePlace_mono {c d : CState} (h : Le c d) {p : Var} (hp : c.livePlace p = true) :
    d.livePlace p = true := by
  simp only [CState.livePlace, Bool.or_eq_true, contains_iff_mem] at hp ⊢
  exact hp.imp (h.scalars p) (h.owners p)

theorem guard_mono {c d : CState} (h : Le c d) {s : Stmt} (hs : guardOf c s = true) :
    guardOf d s = true := by
  have hsc : d.scope = c.scope := h.scope_eq.symm
  have hlc : d.leases = c.leases := h.leases_eq.symm
  have hmw : ∀ p, d.mayWrite p = c.mayWrite p := fun p => mayAccess_congr h _
  have hma : ∀ x, d.mayAccess x = c.mayAccess x := mayAccess_congr h
  have hargs : ∀ args : List Borrow,
      (args.all fun x => c.livePlace x.1.base && c.mayAccess x) = true →
      (args.all fun x => d.livePlace x.1.base && d.mayAccess x) = true := by
    intro args ha
    simp only [List.all_eq_true, Bool.and_eq_true] at ha ⊢
    intro x hx
    exact ⟨livePlace_mono h (ha x hx).1, by rw [hma]; exact (ha x hx).2⟩
  cases s with
  | alloc x => simpa only [guardOf, hsc, hmw] using hs
  | mkScalar x => simpa only [guardOf, hsc, hmw] using hs
  | copy y x =>
      simp only [guardOf, Bool.and_eq_true, contains_iff_mem, hsc, hmw, hma] at hs ⊢
      exact ⟨⟨⟨⟨hs.1.1.1.1, hs.1.1.1.2⟩, h.scalars x hs.1.1.2⟩, hs.1.2⟩, hs.2⟩
  | move y x =>
      simp only [guardOf, Bool.and_eq_true, contains_iff_mem, hsc, hmw] at hs ⊢
      exact ⟨⟨⟨⟨hs.1.1.1.1, hs.1.1.1.2⟩, h.owners x hs.1.1.2⟩, hs.1.2⟩, hs.2⟩
  | drop x =>
      simp only [guardOf, Bool.and_eq_true, contains_iff_mem, hmw] at hs ⊢
      exact ⟨h.owners x hs.1, hs.2⟩
  | call args =>
      simp only [guardOf, Bool.and_eq_true] at hs ⊢
      exact ⟨hs.1, hargs args hs.2⟩
  | spawn t args =>
      simp only [guardOf, Bool.and_eq_true, hlc] at hs ⊢
      exact ⟨⟨hs.1.1, hs.1.2⟩, hargs args hs.2⟩
  | wait t => simpa only [guardOf, hlc] using hs
  | ite thn els => rfl
  | parallel nb body =>
      simp only [guardOf, Bool.and_eq_true, List.all_eq_true] at hs ⊢
      refine ⟨hs.1, fun a ha => ?_⟩
      exact ⟨livePlace_mono h (hs.2 a ha).1, by rw [hma]; exact (hs.2 a ha).2⟩

theorem eff_mono {c d : CState} (h : Le c d) (s : Stmt) : Le (effOf c s) (effOf d s) := by
  have hs := h.scalars
  have ho := h.owners
  cases s <;> refine ⟨h.scope_eq, ?_, ?_, by simp [effOf, h.leases_eq]⟩ <;> intro p hp <;>
    (try simp only [effOf, mem_del, mem_add] at hp ⊢) <;>
    first
      | exact hs p hp
      | exact ho p hp
      | exact ⟨hs p hp.1, hp.2⟩
      | exact ⟨ho p hp.1, hp.2⟩
      | exact hp.imp id (hs p)
      | exact hp.imp id (ho p)
      | exact hp.imp id (fun hq => ⟨ho p hq.1, hq.2⟩)

mutual
/-- A structural measure that does not depend on `sizeOf`'s exact shape. -/
def stmtSize : Stmt → Nat
  | .ite thn els => blockSize thn + blockSize els + 1
  | _ => 1
def blockSize : List Stmt → Nat
  | [] => 0
  | s :: rest => stmtSize s + blockSize rest + 1
end

@[simp] theorem blockSize_nil : blockSize [] = 0 := rfl
@[simp] theorem blockSize_cons (s : Stmt) (rest : List Stmt) :
    blockSize (s :: rest) = stmtSize s + blockSize rest + 1 := rfl
@[simp] theorem stmtSize_ite (thn els : List Stmt) :
    stmtSize (.ite thn els) = blockSize thn + blockSize els + 1 := rfl

theorem joinOf_mono {c d c1 d1 c2 d2 j : CState} (h : Le c d) (h1 : Le c1 d1) (h2 : Le c2 d2)
    (hj : joinOf c c1 c2 = some j) : ∃ k, joinOf d d1 d2 = some k ∧ Le j k := by
  unfold joinOf at hj ⊢
  split at hj
  · next heq =>
      have hd : d1.leases = d2.leases := by
        rw [← h1.leases_eq, ← h2.leases_eq]
        exact of_decide_eq_true (by simpa using heq)
      rw [show (d1.leases == d2.leases) = true by simpa using hd]
      refine ⟨_, rfl, ?_⟩
      have hjv := Option.some.inj hj
      rw [← hjv]
      refine ⟨h.scope_eq, ?_, ?_, h1.leases_eq⟩
      · intro p hp
        simp only [mem_keepIn] at hp ⊢
        exact ⟨h1.scalars p hp.1, h2.scalars p hp.2⟩
      · intro p hp
        simp only [mem_keepIn] at hp ⊢
        exact ⟨h1.owners p hp.1, h2.owners p hp.2⟩
  · exact absurd hj (by simp)

/-- The straight-line half of weakening: a guard that passes in `c` passes in `d`. -/
theorem guardAux {c d c1 : CState} {s : Stmt} (hle : Le c d)
    (hc1 : (if guardOf c s then some (effOf c s) else none) = some c1) :
    ∃ d1, (if guardOf d s then some (effOf d s) else none) = some d1 ∧ Le c1 d1
      ∧ c1.scope = c.scope := by
  split at hc1
  · next hg =>
      rw [guard_mono hle hg]
      refine ⟨_, rfl, ?_, ?_⟩
      · rw [← Option.some.inj hc1]; exact eff_mono hle s
      · rw [← Option.some.inj hc1]; cases s <;> rfl
  · exact absurd hc1 (by simp)

/-- **Weakening.**  What checks from a weaker ownership state checks from a stronger
one, and the result stays weaker. -/
theorem checkBlock_mono : ∀ (n : Nat) (ss : List Stmt), blockSize ss < n →
    ∀ {c d c' : CState}, Le c d → checkBlock c ss = some c' →
      ∃ d', checkBlock d ss = some d' ∧ Le c' d' ∧ c'.scope = c.scope := by
  intro n
  induction n with
  | zero => intro ss hss; exact absurd hss (Nat.not_lt_zero _)
  | succ n ih =>
      intro ss hss c d c' hle hs
      cases ss with
      | nil => exact ⟨d, rfl, by rw [← Option.some.inj hs]; exact hle, by rw [← Option.some.inj hs]⟩
      | cons s rest =>
          have hrest : blockSize rest < n := by simp only [blockSize_cons] at hss; omega
          rw [checkBlock_cons] at hs
          cases hc1 : checkStmt c s with
          | none => rw [hc1] at hs; exact absurd hs (by simp)
          | some c1 =>
              rw [hc1] at hs
              have hstep : ∃ d1, checkStmt d s = some d1 ∧ Le c1 d1 ∧ c1.scope = c.scope := by
                cases s with
                | ite thn els =>
                    have hthn : blockSize thn < n := by
                      simp only [blockSize_cons, stmtSize_ite] at hss; omega
                    have hels : blockSize els < n := by
                      simp only [blockSize_cons, stmtSize_ite] at hss; omega
                    rw [checkStmt_ite] at hc1
                    split at hc1
                    · next a1 a2 h1 h2 =>
                        obtain ⟨d1, hd1, hle1, _⟩ := ih thn hthn hle h1
                        obtain ⟨d2, hd2, hle2, _⟩ := ih els hels hle h2
                        obtain ⟨k, hk, hlek⟩ := joinOf_mono hle hle1 hle2 hc1
                        refine ⟨k, by rw [checkStmt_ite, hd1, hd2]; exact hk, hlek, ?_⟩
                        unfold joinOf at hc1
                        split at hc1
                        · rw [← Option.some.inj hc1]
                        · exact absurd hc1 (by simp)
                    · exact absurd hc1 (by simp)
                | alloc x => exact guardAux hle hc1
                | mkScalar x => exact guardAux hle hc1
                | copy y x => exact guardAux hle hc1
                | move y x => exact guardAux hle hc1
                | drop x => exact guardAux hle hc1
                | call args => exact guardAux hle hc1
                | spawn t args => exact guardAux hle hc1
                | wait t => exact guardAux hle hc1
                | parallel nb body => exact guardAux hle hc1
              obtain ⟨d1, hd1, hle1, hsc1⟩ := hstep
              obtain ⟨d', hd', hled, hscd⟩ := ih rest hrest hle1 hs
              exact ⟨d', by rw [checkBlock_cons, hd1]; exact hd', hled, by rw [hscd, hsc1]⟩

theorem checkBlock_append (c : CState) : ∀ (l₁ l₂ : List Stmt),
    checkBlock c (l₁ ++ l₂) =
      match checkBlock c l₁ with
      | some c' => checkBlock c' l₂
      | none => none := by
  intro l₁
  induction l₁ generalizing c with
  | nil => intro l₂; rfl
  | cons s rest ih =>
      intro l₂
      rw [List.cons_append, checkBlock_cons, checkBlock_cons]
      cases h : checkStmt c s with
      | none => rfl
      | some c' => exact ih c' l₂

end Ownership
end Cairn
