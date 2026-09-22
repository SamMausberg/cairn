/-
Exact affine-implication certificates, mirroring `src/cairn/verify/linear_certificates.py`.

An affine form is five exact integer coefficients `(c, ck, ci, cn, cm)` denoting
`c + ck*K + ci*I + cn*N + cm*M`, asserted to be `>= 0`.  A rule is a list of
assumption forms plus a conclusion form.  A certificate supplies one nonnegative
integer weight per assumption together with a nonnegative integer constant; the
checker accepts exactly when, coefficient-wise,

    conclusion = (c0, 0, 0, 0, 0) + sum_j w_j * assumption_j.

`check_sound` is the mathematical content: acceptance implies the implication
holds for *every* integer assignment of `(K, I, N, M)`, not for sampled states.
-/

namespace Cairn

/-- An affine form `c + ck*K + ci*I + cn*N + cm*M` over the integers,
always read as the assertion `form >= 0`. -/
structure Form where
  c : Int
  ck : Int
  ci : Int
  cn : Int
  cm : Int
deriving DecidableEq, Repr, Inhabited

namespace Form

/-- Interpret a form at an integer assignment of the four cursor variables. -/
def eval (f : Form) (K I N M : Int) : Int :=
  f.c + f.ck * K + f.ci * I + f.cn * N + f.cm * M

/-- The identically zero form. -/
def zero : Form := ⟨0, 0, 0, 0, 0⟩

/-- The constant form `c`. -/
def const (c : Int) : Form := ⟨c, 0, 0, 0, 0⟩

/-- Coefficient-wise sum. -/
def add (a b : Form) : Form :=
  ⟨a.c + b.c, a.ck + b.ck, a.ci + b.ci, a.cn + b.cn, a.cm + b.cm⟩

/-- Coefficient-wise scaling by an integer. -/
def smul (w : Int) (a : Form) : Form :=
  ⟨w * a.c, w * a.ck, w * a.ci, w * a.cn, w * a.cm⟩

@[simp] theorem eval_zero (K I N M : Int) : zero.eval K I N M = 0 := by
  simp [zero, eval]

@[simp] theorem eval_const (c K I N M : Int) : (const c).eval K I N M = c := by
  simp [const, eval]

@[simp] theorem eval_add (a b : Form) (K I N M : Int) :
    (a.add b).eval K I N M = a.eval K I N M + b.eval K I N M := by
  simp only [add, eval, Int.add_mul]
  omega

@[simp] theorem eval_smul (w : Int) (a : Form) (K I N M : Int) :
    (smul w a).eval K I N M = w * a.eval K I N M := by
  simp only [smul, eval, Int.mul_add, Int.mul_assoc]

end Form

/-- A named implication between affine forms: all assumptions `>= 0` entail the
conclusion `>= 0`. -/
structure Rule where
  name : String
  assumptions : List Form
  conclusion : Form
deriving Repr, Inhabited

/-- Nonnegative rational-free witness: one weight per assumption plus a
nonnegative slack constant. -/
structure Certificate where
  weights : List Int
  nonnegativeConstant : Int
deriving Repr, Inhabited

/-- `sum_j w_j * a_j`, truncating at the shorter list (the checker separately
requires the two lists to have equal length). -/
def combine : List Int → List Form → Form
  | w :: ws, a :: as => Form.add (Form.smul w a) (combine ws as)
  | _, _ => Form.zero

/-- All forms in the list are nonnegative at the given assignment, spelled as a
right-nested conjunction so that a literal list of hypotheses can discharge it. -/
def satisfies (K I N M : Int) : List Form → Prop
  | [] => True
  | a :: rest => 0 ≤ a.eval K I N M ∧ satisfies K I N M rest

theorem forall_mem_of_satisfies {K I N M : Int} :
    ∀ {fs : List Form}, satisfies K I N M fs → ∀ a ∈ fs, 0 ≤ a.eval K I N M
  | [], _, _, h => by simp at h
  | _ :: rest, hs, a, ha => by
    cases List.mem_cons.mp ha with
    | inl h => exact h ▸ hs.1
    | inr h => exact forall_mem_of_satisfies hs.2 a h

/-- The trusted-Python checker, transcribed: exact integer coefficients, an
explicit length agreement, nonnegative weights and constant, and an exact
coefficient-wise identity.  (The Python version additionally rejects non-`int`
inputs and integers wider than 4096 bits; those are representation hygiene for a
dynamically typed host and carry no mathematical content here.) -/
def check (r : Rule) (c : Certificate) : Bool :=
  decide (r.assumptions.length ≤ 64) &&
  decide (c.weights.length = r.assumptions.length) &&
  c.weights.all (fun w => decide (0 ≤ w)) &&
  decide (0 ≤ c.nonnegativeConstant) &&
  decide (Form.add (Form.const c.nonnegativeConstant) (combine c.weights r.assumptions)
    = r.conclusion)

theorem eval_combine_nonneg {K I N M : Int} :
    ∀ (ws : List Int) (as : List Form),
      (∀ w ∈ ws, 0 ≤ w) → (∀ a ∈ as, 0 ≤ a.eval K I N M) →
      0 ≤ (combine ws as).eval K I N M
  | [], _, _, _ => by simp [combine]
  | _ :: _, [], _, _ => by simp [combine]
  | w :: ws, a :: as, hw, ha => by
    have hw0 : 0 ≤ w := hw w (by simp)
    have ha0 : 0 ≤ a.eval K I N M := ha a (by simp)
    have ih := eval_combine_nonneg ws as
      (fun x hx => hw x (by simp [hx])) (fun x hx => ha x (by simp [hx]))
    simp only [combine, Form.eval_add, Form.eval_smul]
    exact Int.add_nonneg (Int.mul_nonneg hw0 ha0) ih

/-- **Soundness of the certificate checker.**  If `check` accepts, then for every
integer assignment satisfying all assumptions, the conclusion is nonnegative. -/
theorem check_sound {r : Rule} {c : Certificate} (h : check r c = true) :
    ∀ K I N M : Int, (∀ a ∈ r.assumptions, 0 ≤ a.eval K I N M) →
      0 ≤ r.conclusion.eval K I N M := by
  simp only [check, Bool.and_eq_true, decide_eq_true_eq, List.all_eq_true] at h
  intro K I N M ha
  have hw : ∀ w ∈ c.weights, 0 ≤ w := fun w hw => by simpa using h.1.1.2 w hw
  calc (0 : Int)
      ≤ c.nonnegativeConstant + (combine c.weights r.assumptions).eval K I N M :=
        Int.add_nonneg h.1.2 (eval_combine_nonneg c.weights r.assumptions hw ha)
    _ = (Form.add (Form.const c.nonnegativeConstant)
          (combine c.weights r.assumptions)).eval K I N M := by simp
    _ = r.conclusion.eval K I N M := by rw [h.2]

/-- Convenience wrapper used by the generated per-obligation corollaries. -/
theorem check_sound' {r : Rule} {c : Certificate} (h : check r c = true) (K I N M : Int)
    (hs : satisfies K I N M r.assumptions) : 0 ≤ r.conclusion.eval K I N M :=
  check_sound h K I N M (forall_mem_of_satisfies hs)

end Cairn
