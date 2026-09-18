/-
An executable functional model of CAIRN's bounded collector, and its proofs.

    let used = compact out for i in n where pred(i) yield proj(i);

lowers to the guardless loop

    k = 0;
    for (i = 0; i < n; ++i) { if (pred(i)) { out[k] = proj(i); ++k; } }
    used = k;

with `out` of capacity exactly `n`.  The store `out[k]` carries no dynamic
bounds check; what licenses that is the cursor invariant `0 <= k <= i <= n <= M`.

Everything about that invariant below is derived from the certified affine
obligations in `Cairn.CollectorCertificates` -- the same seventeen obligations
the Python compiler checks before it emits the loop -- rather than re-proved by
`omega`.  `omega` appears only to translate between the affine encoding
`c + ck*K + ci*I + cn*N + cm*M >= 0` and ordinary inequalities, and to move
between `Nat` indices and their `Int` images.
-/
import Cairn.CollectorCertificates

universe u v

namespace Cairn
namespace Collector

variable {α : Type u} {β : Type v}

/-! ## The cursor invariant, over mathematical integers -/

/-- The loop invariant `0 <= K <= I <= N <= M`: `K` emitted outputs, `I` visited
inputs, `N` the output capacity, `M` the largest representable cursor. -/
structure Inv (K I N M : Int) : Prop where
  emitted_nonneg : 0 ≤ K
  emitted_le_visited : K ≤ I
  visited_le_capacity : I ≤ N
  capacity_le_max : N ≤ M

namespace Inv

variable {K I N M : Int}

theorem a0 (h : Inv K I N M) : 0 ≤ Form.eval ⟨0, 1, 0, 0, 0⟩ K I N M := by
  have := h.emitted_nonneg; simp only [Form.eval]; omega

theorem a1 (h : Inv K I N M) : 0 ≤ Form.eval ⟨0, -1, 1, 0, 0⟩ K I N M := by
  have := h.emitted_le_visited; simp only [Form.eval]; omega

theorem a2 (h : Inv K I N M) : 0 ≤ Form.eval ⟨0, 0, -1, 1, 0⟩ K I N M := by
  have := h.visited_le_capacity; simp only [Form.eval]; omega

theorem a3 (h : Inv K I N M) : 0 ≤ Form.eval ⟨0, 0, 0, -1, 1⟩ K I N M := by
  have := h.capacity_le_max; simp only [Form.eval]; omega

/-- The loop body runs only while `I < N`; that is the fifth active assumption. -/
theorem a4 (hlt : I < N) : 0 ≤ Form.eval ⟨-1, 0, -1, 1, 0⟩ K I N M := by
  simp only [Form.eval]; omega

/-- `initial.*`: the invariant holds on entry, where `K = I = 0`. -/
theorem init {N M : Int} (hn : 0 ≤ N) (hm : N ≤ M) : Inv 0 0 N M := by
  have b0 : 0 ≤ Form.eval ⟨0, 0, 0, 1, 0⟩ 0 0 N M := by simp only [Form.eval]; omega
  have b1 : 0 ≤ Form.eval ⟨0, 0, 0, -1, 1⟩ 0 0 N M := by simp only [Form.eval]; omega
  refine ⟨?_, ?_, ?_, ?_⟩
  · have := obligation_initial_nonnegative 0 0 N M b0 b1
    simp only [Form.eval] at this; omega
  · have := obligation_initial_cursor_before_input 0 0 N M b0 b1
    simp only [Form.eval] at this; omega
  · have := obligation_initial_input_before_capacity 0 0 N M b0 b1
    simp only [Form.eval] at this; omega
  · have := obligation_initial_capacity_representable 0 0 N M b0 b1
    simp only [Form.eval] at this; omega

/-- `store.nonnegative`: the store index is nonnegative. -/
theorem store_nonneg (h : Inv K I N M) (hlt : I < N) : 0 ≤ K := by
  have := obligation_store_nonnegative K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
  simp only [Form.eval] at this; omega

/-- `store.strictly_below_capacity`: the store index is strictly inside the
buffer, which is what removes the dynamic bounds check. -/
theorem store_lt_capacity (h : Inv K I N M) (hlt : I < N) : K < N := by
  have := obligation_store_strictly_below_capacity K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
  simp only [Form.eval] at this; omega

/-- `emit.cursor_increment_fits`: `++k` stays representable. -/
theorem emit_increment_fits (h : Inv K I N M) (hlt : I < N) : K + 1 ≤ M := by
  have := obligation_emit_cursor_increment_fits K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
  simp only [Form.eval] at this; omega

/-- `step.input_increment_fits`: `++i` stays representable. -/
theorem step_increment_fits (h : Inv K I N M) (hlt : I < N) : I + 1 ≤ M := by
  have := obligation_step_input_increment_fits K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
  simp only [Form.eval] at this; omega

/-- `emit.invariant.0-3`: the emitting branch `K' = K+1, I' = I+1` preserves it. -/
theorem emit (h : Inv K I N M) (hlt : I < N) : Inv (K + 1) (I + 1) N M := by
  refine ⟨?_, ?_, ?_, ?_⟩
  · have := obligation_emit_invariant_0 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega
  · have := obligation_emit_invariant_1 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega
  · have := obligation_emit_invariant_2 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega
  · have := obligation_emit_invariant_3 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega

/-- `skip.invariant.0-3`: the non-emitting branch `K' = K, I' = I+1` preserves it. -/
theorem skip (h : Inv K I N M) (hlt : I < N) : Inv K (I + 1) N M := by
  refine ⟨?_, ?_, ?_, ?_⟩
  · have := obligation_skip_invariant_0 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega
  · have := obligation_skip_invariant_1 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega
  · have := obligation_skip_invariant_2 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega
  · have := obligation_skip_invariant_3 K I N M h.a0 h.a1 h.a2 h.a3 (a4 hlt)
    simp only [Form.eval] at this; omega

/-- `exit.output_count_bounded`: on exit the emitted count is within capacity. -/
theorem exit_le_capacity (h : Inv K I N M) : K ≤ N := by
  have := obligation_exit_output_count_bounded K I N M h.a0 h.a1 h.a2 h.a3
  simp only [Form.eval] at this; omega

end Inv

/-- The invariant at `Nat`-valued cursors, as the executable model carries them. -/
def InvN (k i n : Nat) (M : Int) : Prop := Inv (k : Int) (i : Int) (n : Int) M

namespace InvN

theorem init {n : Nat} {M : Int} (hm : (n : Int) ≤ M) : InvN 0 0 n M := by
  have := Inv.init (N := (n : Int)) (M := M) (by omega) hm
  simpa [InvN] using this

theorem emit {k i n : Nat} {M : Int} (h : InvN k i n M) (hlt : i < n) :
    InvN (k + 1) (i + 1) n M := by
  have h2 : Inv ((k : Int) + 1) ((i : Int) + 1) (n : Int) M := Inv.emit h (by omega)
  have e1 : (((k + 1 : Nat)) : Int) = (k : Int) + 1 := by omega
  have e2 : (((i + 1 : Nat)) : Int) = (i : Int) + 1 := by omega
  unfold InvN; rw [e1, e2]; exact h2

theorem skip {k i n : Nat} {M : Int} (h : InvN k i n M) (hlt : i < n) :
    InvN k (i + 1) n M := by
  have h2 : Inv (k : Int) ((i : Int) + 1) (n : Int) M := Inv.skip h (by omega)
  have e2 : (((i + 1 : Nat)) : Int) = (i : Int) + 1 := by omega
  unfold InvN; rw [e2]; exact h2

/-- The guardless store is in range: `k < n` as `Nat` indices. -/
theorem store_lt_capacity {k i n : Nat} {M : Int} (h : InvN k i n M) (hlt : i < n) : k < n := by
  have := Inv.store_lt_capacity (K := (k : Int)) (I := (i : Int)) (N := (n : Int)) h (by omega)
  omega

theorem exit_le_capacity {k i n : Nat} {M : Int} (h : InvN k i n M) : k ≤ n := by
  have := Inv.exit_le_capacity (K := (k : Int)) (I := (i : Int)) (N := (n : Int)) h
  omega

end InvN

/-! ## List surgery used by the correctness proof -/

theorem take_set_succ (l : List β) (k : Nat) (v : β) (h : k < l.length) :
    (l.set k v).take (k + 1) = l.take k ++ [v] := by
  induction l generalizing k with
  | nil => simp at h
  | cons a t ih =>
    cases k with
    | zero => simp
    | succ k => simp only [List.length_cons] at h; simp [ih k (by omega)]

theorem drop_set_of_lt (l : List β) (k j : Nat) (v : β) (h : k < j) :
    (l.set k v).drop j = l.drop j := by
  induction l generalizing k j with
  | nil => simp
  | cons a t ih =>
    cases k with
    | zero => cases j with
      | zero => exact absurd h (Nat.lt_irrefl 0)
      | succ j => simp
    | succ k => cases j with
      | zero => exact absurd h (Nat.not_lt_zero _)
      | succ j => simp [ih k j (Nat.lt_of_succ_lt_succ h)]

theorem take_append_length (l₁ l₂ : List β) : (l₁ ++ l₂).take l₁.length = l₁ := by
  induction l₁ with
  | nil => simp
  | cons a t ih => simp [ih]

theorem drop_append_length (l₁ l₂ : List β) : (l₁ ++ l₂).drop l₁.length = l₂ := by
  induction l₁ with
  | nil => simp
  | cons a t ih => simp [ih]

/-! ## The executable model -/

/-- The lowered loop's mutable state: the output buffer and the two cursors. -/
structure State (β : Type v) where
  out : List β
  k : Nat
  i : Nat
deriving Repr, DecidableEq, Inhabited

/-- One iteration of the loop body: on a selected input store `proj x` at the
emitted cursor and bump it; always bump the visited cursor. -/
def step (pred : α → Bool) (proj : α → β) (s : State β) (x : α) : State β :=
  if pred x then
    { out := s.out.set s.k (proj x), k := s.k + 1, i := s.i + 1 }
  else
    { out := s.out, k := s.k, i := s.i + 1 }

/-- The loop itself: iterate the body over the inputs. -/
def run (pred : α → Bool) (proj : α → β) (xs : List α) (s : State β) : State β :=
  xs.foldl (step pred proj) s

/-- The whole construct: start both cursors at zero. -/
def collect (pred : α → Bool) (proj : α → β) (xs : List α) (out : List β) : State β :=
  run pred proj xs { out := out, k := 0, i := 0 }

@[simp] theorem run_nil (pred : α → Bool) (proj : α → β) (s : State β) :
    run pred proj [] s = s := rfl

@[simp] theorem run_cons (pred : α → Bool) (proj : α → β) (x : α) (xs : List α) (s : State β) :
    run pred proj (x :: xs) s = run pred proj xs (step pred proj s x) := rfl

theorem step_emit {pred : α → Bool} {proj : α → β} {s : State β} {x : α} (h : pred x = true) :
    step pred proj s x = { out := s.out.set s.k (proj x), k := s.k + 1, i := s.i + 1 } := by
  simp [step, h]

theorem step_skip {pred : α → Bool} {proj : α → β} {s : State β} {x : α} (h : pred x = false) :
    step pred proj s x = { out := s.out, k := s.k, i := s.i + 1 } := by
  simp [step, h]

/-- The model is executable, not merely declarative: collecting the even inputs
of `[1, 2, 3, 4]` (times ten) into a buffer of capacity four leaves `[20, 40]` in
the live prefix, `k = 2`, `i = 4`, and the tail untouched. -/
example :
    collect (fun n => n % 2 == 0) (fun n => n * 10) [1, 2, 3, 4] [0, 0, 0, 0]
      = ⟨[20, 40, 0, 0], 2, 4⟩ := by
  decide

/-! ## (a) The invariant is preserved by every iteration -/

/-- Iterating the body preserves the certified cursor invariant.  Each step uses
`emit.invariant.*` or `skip.invariant.*` and nothing else. -/
theorem run_preserves_inv (pred : α → Bool) (proj : α → β) (n : Nat) (M : Int) :
    ∀ (xs : List α) (out : List β) (k i : Nat),
      InvN k i n M → i + xs.length ≤ n →
      InvN (run pred proj xs ⟨out, k, i⟩).k (run pred proj xs ⟨out, k, i⟩).i n M := by
  intro xs
  induction xs with
  | nil => intro out k i h _; simpa using h
  | cons x xs ih =>
    intro out k i h hroom
    simp only [List.length_cons] at hroom
    have hlt : i < n := by omega
    cases hb : pred x with
    | false =>
      rw [run_cons, step_skip hb]
      exact ih out k (i + 1) (InvN.skip h hlt) (by omega)
    | true =>
      rw [run_cons, step_emit hb]
      exact ih (out.set k (proj x)) (k + 1) (i + 1) (InvN.emit h hlt) (by omega)

/-! ## (c) Stable selection: what the loop actually computes -/

/-- The generalized loop specification: after consuming `xs` from a state with
`k ≤ i` and enough room left, the cursors have advanced by the selected and the
visited counts, and the buffer holds the untouched prefix, the projections of the
selected inputs, then the untouched remainder. -/
theorem run_spec (pred : α → Bool) (proj : α → β) :
    ∀ (xs : List α) (out : List β) (k i : Nat),
      k ≤ i → i + xs.length ≤ out.length →
      (run pred proj xs ⟨out, k, i⟩).k = k + (xs.filter pred).length
      ∧ (run pred proj xs ⟨out, k, i⟩).i = i + xs.length
      ∧ (run pred proj xs ⟨out, k, i⟩).out =
          out.take k ++ (xs.filter pred).map proj
            ++ out.drop (k + (xs.filter pred).length) := by
  intro xs
  induction xs with
  | nil =>
    intro out k i _ _
    refine ⟨by simp, by simp, ?_⟩
    simp
  | cons x xs ih =>
    intro out k i hki hroom
    simp only [List.length_cons] at hroom
    have hk : k < out.length := by omega
    cases hb : pred x with
    | false =>
      have ih' := ih out k (i + 1) (by omega) (by omega)
      rw [run_cons, step_skip hb]
      refine ⟨?_, ?_, ?_⟩
      · rw [ih'.1]; simp [hb]
      · rw [ih'.2.1]; simp; omega
      · rw [ih'.2.2]; simp [hb]
    | true =>
      have hlen : (out.set k (proj x)).length = out.length := by simp
      have ih' := ih (out.set k (proj x)) (k + 1) (i + 1) (by omega) (by rw [hlen]; omega)
      rw [run_cons, step_emit hb]
      refine ⟨?_, ?_, ?_⟩
      · rw [ih'.1]; simp [hb]; omega
      · rw [ih'.2.1]; simp; omega
      · rw [ih'.2.2, take_set_succ _ _ _ hk, drop_set_of_lt _ _ _ _ (by omega)]
        have hidx : k + 1 + (xs.filter pred).length
            = k + ((x :: xs).filter pred).length := by
          simp [hb]; omega
        rw [hidx]
        simp [hb]

/-- **Stable selection.**  With capacity exactly `n`, the collector writes the
projections of the selected inputs, in input order, into `out[0..k)`; `k` is the
number of selected inputs and is within capacity; and `out[k..n)` is untouched. -/
theorem collect_spec (pred : α → Bool) (proj : α → β) (xs : List α) (out : List β)
    (hcap : out.length = xs.length) :
    (collect pred proj xs out).k = (xs.filter pred).length
    ∧ (collect pred proj xs out).k ≤ out.length
    ∧ (collect pred proj xs out).i = xs.length
    ∧ (collect pred proj xs out).out.length = out.length
    ∧ (collect pred proj xs out).out.take (collect pred proj xs out).k
        = (xs.filter pred).map proj
    ∧ (collect pred proj xs out).out.drop (collect pred proj xs out).k
        = out.drop (collect pred proj xs out).k := by
  have hspec := run_spec pred proj xs out 0 0 (by omega) (by omega)
  have hinv := run_preserves_inv pred proj out.length ((out.length : Int))
    xs out 0 0 (InvN.init (by omega)) (by omega)
  have hk : (collect pred proj xs out).k = (xs.filter pred).length := by
    simpa [collect] using hspec.1
  have hle : (collect pred proj xs out).k ≤ out.length :=
    InvN.exit_le_capacity (M := ((out.length : Int))) hinv
  have hout : (collect pred proj xs out).out
      = (xs.filter pred).map proj ++ out.drop (xs.filter pred).length := by
    have := hspec.2.2
    simpa [collect] using this
  have hmaplen : ((xs.filter pred).map proj).length = (xs.filter pred).length := by simp
  refine ⟨hk, hle, by simpa [collect] using hspec.2.1, ?_, ?_, ?_⟩
  · rw [hout]
    simp only [List.length_append, hmaplen, List.length_drop]
    omega
  · rw [hout, hk, ← hmaplen]
    exact take_append_length _ _
  · rw [hout, hk, ← hmaplen]
    exact drop_append_length _ _

/-! ## (b) Every store is in bounds, with no dynamic guard -/

/-- At every iteration of the loop -- in particular at every store -- the emitted
cursor is strictly below the capacity.  `store.strictly_below_capacity` is the
obligation that licenses emitting `out[k] = proj(i)` without a bounds check. -/
theorem store_index_lt_capacity (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    (run pred proj pre ⟨out, 0, 0⟩).k < out.length := by
  have hlensplit : xs.length = pre.length + (post.length + 1) := by
    rw [hsplit]; simp
  have hroom : 0 + pre.length ≤ out.length := by omega
  have hinv := run_preserves_inv pred proj out.length ((out.length : Int))
    pre out 0 0 (InvN.init (by omega)) hroom
  have hi : (run pred proj pre ⟨out, 0, 0⟩).i = pre.length := by
    have := (run_spec pred proj pre out 0 0 (by omega) (by omega)).2.1
    omega
  exact InvN.store_lt_capacity hinv (by omega)

/-- The same bound stated against the live buffer at the moment of the store, so
`List.set` never silently discards a write. -/
theorem store_index_lt_buffer_length (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    (run pred proj pre ⟨out, 0, 0⟩).k < (run pred proj pre ⟨out, 0, 0⟩).out.length := by
  have hsp : xs.length = pre.length + (post.length + 1) := by rw [hsplit]; simp
  have hspec := run_spec pred proj pre out 0 0 (by omega) (by omega)
  have hkf : (pre.filter pred).length ≤ pre.length := List.length_filter_le _ _
  have hlen : (run pred proj pre ⟨out, 0, 0⟩).out.length = out.length := by
    rw [hspec.2.2]
    simp only [List.length_append, List.length_take, List.length_map, List.length_drop]
    omega
  rw [hlen]
  exact store_index_lt_capacity pred proj xs out hcap pre x post hsplit

/-! ## (d) Neither increment overflows -/

/-- With the capacity representable (`n ≤ M`), `++k` and `++i` both stay within
`M` at every iteration: `emit.cursor_increment_fits` and
`step.input_increment_fits`. -/
theorem increments_fit (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (M : Int) (hM : (out.length : Int) ≤ M)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    ((run pred proj pre ⟨out, 0, 0⟩).k : Int) + 1 ≤ M
    ∧ ((run pred proj pre ⟨out, 0, 0⟩).i : Int) + 1 ≤ M := by
  have hlensplit : xs.length = pre.length + (post.length + 1) := by
    rw [hsplit]; simp
  have hinv := run_preserves_inv pred proj out.length M
    pre out 0 0 (InvN.init hM) (by omega)
  have hi : (run pred proj pre ⟨out, 0, 0⟩).i = pre.length := by
    have := (run_spec pred proj pre out 0 0 (by omega) (by omega)).2.1
    omega
  have hlt : ((run pred proj pre ⟨out, 0, 0⟩).i : Int) < (out.length : Int) := by omega
  exact ⟨Inv.emit_increment_fits hinv hlt, Inv.step_increment_fits hinv hlt⟩

end Collector
end Cairn
