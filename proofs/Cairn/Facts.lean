/-
The guard-elision rule of `src/cairn/compiler/check/facts.py`: when lowering may leave out an index,
`+`, `-`, shift or narrowing guard because the checker showed it cannot fail.

A fact is an edge `x - y ≤ k` between two atoms.  An atom is zero, an immutable usize value (a
name, a field reached from an immutable local, a length), or such a value times a positive
constant.  `distance` is the Bellman-Ford search `facts.py` runs over the facts in scope,
`bounds` is what an expression is known not to exceed and known to reach, and `index`, `addOk`,
`subOk`, `atMostConst` and `partOk` are the five decisions lowering acts on.  Each is transliterated from
the Python, including the order in which bounds are kept and the four `WIDEST` of them, so that
`tools/checks/differential_facts.py` can require the two to decide generated inputs alike.

The theorems say that under every valuation that makes the facts in scope true, a discharged
index is below its extent, a discharged `+` stays at most the largest usize, a discharged `-`
does not go below zero, a value discharged below a constant is at most it, and a discharged part
lies inside its view.  That the facts
in scope are true where they are visible is the checker's bookkeeping: a binder's bounds, a
`let`, a condition and an early exit, over values that cannot change.  It is tested in
`tests/soundness/test_established.py`, not proved here.  So is lowering's use of the decisions.
-/

namespace Cairn
namespace Facts

/-- The largest usize. -/
def MAX : Int := 18446744073709551615

/-- An atom: zero, an immutable usize value, or one times a positive stride. -/
inductive Atom where
  | zero
  | plain (n : Nat)
  | prod (n S : Nat)
deriving DecidableEq, Repr

/-- An atom plus a constant. -/
abbrev Term := Atom × Int

/-- `(a, b, k)` says `a - b ≤ k`. -/
abbrev Fact := Atom × Atom × Int

def Atom.isProd : Atom → Bool
  | .prod _ _ => true
  | _ => false

/-- A plain atom times a stride, as `scaled` writes it; zero stays zero. -/
def Atom.times : Atom → Nat → Atom
  | .plain n, S => .prod n S
  | a, _ => a

def Atom.stride : Atom → List Nat
  | .prod _ S => [S]
  | _ => []

/-- What an atom is worth when the immutable values are `ρ`. -/
def Atom.val (ρ : Nat → Nat) : Atom → Int
  | .zero => 0
  | .plain n => ρ n
  | .prod n S => S * ρ n

/-- The list without repeats, each kept where it first appears, as `dict.fromkeys` keeps it. -/
def dedup {α : Type} [DecidableEq α] : List α → List α
  | [] => []
  | a :: l => a :: (dedup l).filter fun b => !decide (b = a)

theorem mem_dedup {α : Type} [DecidableEq α] {a : α} : ∀ {l : List α}, a ∈ dedup l → a ∈ l
  | [], h => h
  | b :: l, h => by
      rcases List.mem_cons.mp h with rfl | h
      · exact List.mem_cons_self ..
      · exact List.mem_cons_of_mem _ (mem_dedup (List.mem_filter.mp h).1)

/-! ## The search -/

def endpoints (fs : List Fact) : List Atom := fs.flatMap fun f => [f.1, f.2.1]

/-- Each fact between atoms that are not products, multiplied by every stride a product atom
among `atoms` and the facts names: `x - y ≤ k` gives `x*S - y*S ≤ k*S`. -/
def scaled (facts : List Fact) (atoms : List Atom) : List Fact :=
  (dedup ((atoms ++ endpoints facts).flatMap Atom.stride)).flatMap fun (S : Nat) =>
    (facts.filter fun f => !f.1.isProd && !f.2.1.isProd).map fun f => (f.1.times S, f.2.1.times S, f.2.2 * (S : Int))

/-- The best distance found so far to an atom. -/
def get (best : List (Atom × Int)) (a : Atom) : Option Int := (best.find? fun p => decide (p.1 = a)).map (·.2)

def put (best : List (Atom × Int)) (a : Atom) (d : Int) : List (Atom × Int) :=
  (a, d) :: best.filter fun p => !decide (p.1 = a)

/-- One round of relaxation over every edge, in order; whether anything changed. -/
def relax (edges : List Fact) (best : List (Atom × Int)) : List (Atom × Int) × Bool :=
  edges.foldl (fun st e =>
    match get st.1 e.1 with
    | some da => if da + e.2.2 < (get st.1 e.2.1).getD (MAX + 1) then (put st.1 e.2.1 (da + e.2.2), true) else st
    | none => st) (best, false)

/-- Rounds until one changes nothing, at most `r` of them. -/
def rounds (edges : List Fact) : Nat → List (Atom × Int) → Option (List (Atom × Int))
  | 0, _ => none
  | r + 1, best =>
      match relax edges best with
      | (best', true) => rounds edges r best'
      | (best', false) => some best'

/-- The least `k` with `source - target ≤ k` the facts give, by Bellman-Ford: the facts, their
scaled copies, every atom that is not a product at most `MAX`, and every atom at least zero. -/
def distance (facts : List Fact) (source target : Atom) : Option Int :=
  let fs := facts ++ scaled facts [source, target]
  let atoms := dedup (source :: target :: .zero :: endpoints fs)
  let edges := fs ++ ((atoms.filter fun a => !a.isProd).map fun a => (a, Atom.zero, MAX))
    ++ atoms.map fun a => (Atom.zero, a, (0 : Int))
  (rounds edges atoms.length [(source, 0)]).bind (get · target)

/-- `x - y ≤ slack` follows from the facts. -/
def atMost (facts : List Fact) (x y : Term) (slack : Int) : Bool :=
  match distance facts x.1 y.1 with
  | some d => decide (d ≤ slack - x.2 + y.2)
  | none => false

/-- The least constant the facts cap an atom at, when it is below `MAX`. -/
def ceiling (facts : List Fact) (a : Atom) : Option Int :=
  match distance facts a .zero with
  | some d => if d < MAX then some d else none
  | none => none

/-- Upper bounds of a sum: a bound of one side plus a bound of the other the facts cap. -/
def plus (facts : List Fact) (ha hb : List Term) : List Term :=
  ha.flatMap fun p => hb.flatMap fun q =>
    ((if q.1 = .zero then some 0 else ceiling facts q.1).toList.map fun cap => (p.1, p.2 + q.2 + cap))
      ++ (if p.1 = .zero then [] else (ceiling facts p.1).toList.map fun cap => (q.1, p.2 + q.2 + cap))

/-! ## Expressions -/

/-- A usize expression.  `atom` is a value the facts may name; `var` is one that can change, which
no fact names. -/
inductive E where
  | lit (k : Nat)
  | atom (n : Nat)
  | var (n : Nat)
  | add (x y : E)
  | sub (x y : E)
  | mul (x y : E)
  | div (x y : E)
  | mod (x y : E)
  | band (x y : E)
  | min (x y : E)
deriving Repr

/-- Checked usize evaluation: `none` is a trap. -/
def eval (ρ σ : Nat → Nat) : E → Option Nat
  | .lit k => some k
  | .atom n => some (ρ n)
  | .var n => some (σ n)
  | .add x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => if ((a + b : Nat) : Int) ≤ MAX then some (a + b) else none
    | _, _ => none
  | .sub x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => if b ≤ a then some (a - b) else none
    | _, _ => none
  | .mul x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => if ((a * b : Nat) : Int) ≤ MAX then some (a * b) else none
    | _, _ => none
  | .div x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => if b = 0 then none else some (a / b)
    | _, _ => none
  | .mod x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => if b = 0 then none else some (a % b)
    | _, _ => none
  | .band x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => some (a &&& b)
    | _, _ => none
  | .min x y => match eval ρ σ x, eval ρ σ y with
    | some a, some b => some (Nat.min a b)
    | _, _ => none

/-- The value as one atom plus a constant, when it is that: `facts.py:exact`. -/
def exact : E → Option Term
  | .lit k => some (.zero, k)
  | .atom n => some (.plain n, 0)
  | .add x y => match exact x, exact y with
    | some (a, j), some (.zero, k) => some (a, j + k)
    | some (.zero, j), some (a, k) => some (a, j + k)
    | _, _ => none
  | .sub x y => match exact x, exact y with
    | some (a, j), some (.zero, k) => some (a, j - k)
    | _, _ => none
  | .mul x y => match exact x, exact y with
    | some (.plain n, 0), some (.zero, k) => if 0 < k then some (.prod n k.toNat, 0) else none
    | some (.zero, k), some (.plain n, 0) => if 0 < k then some (.prod n k.toNat, 0) else none
    | _, _ => none
  | _ => none

/-- Only the bounds that are constants. -/
def consts (l : List Term) : List Term := l.filter fun t => decide (t.1 = .zero)

/-- The exact value first, then the other bounds, without repeats, four of each; zero is always
reached. -/
def trim (e : E) (high low : List Term) : List Term × List Term :=
  ((dedup ((exact e).toList ++ high)).take 4, (dedup ((exact e).toList ++ (.zero, 0) :: low)).take 4)

/-- What an expression is known not to exceed, and known to reach: `facts.py:bounds`. -/
def bounds (facts : List Fact) : E → List Term × List Term
  | .add x y => trim (.add x y) (plus facts (bounds facts x).1 (bounds facts y).1)
      ((bounds facts x).2.flatMap fun p => (consts (bounds facts y).2).map fun q => (p.1, p.2 + q.2))
  | .sub x y => trim (.sub x y)
      ((bounds facts x).1.flatMap fun p => (consts (bounds facts y).2).map fun q => (p.1, p.2 - q.2))
      ((bounds facts x).2.flatMap fun p => (consts (bounds facts y).1).map fun q => (p.1, p.2 - q.2))
  | .div x y => trim (.div x y) (bounds facts x).1 []
  | .mod x y => trim (.mod x y) ((bounds facts x).1 ++ (bounds facts y).1.map fun q => (q.1, q.2 - 1)) []
  | .band x y => trim (.band x y) ((bounds facts x).1 ++ (bounds facts y).1) []
  | .min x y => trim (.min x y) ((bounds facts x).1 ++ (bounds facts y).1) []
  | e => trim e [] []

/-! ## The five decisions -/

/-- An index below its view's extent. -/
def index (facts : List Fact) (i : E) (extent : Term) : Bool :=
  (bounds facts i).1.any fun x => atMost facts x extent (-1)

/-- A `+` that cannot pass `MAX`. -/
def addOk (facts : List Fact) (x y : E) : Bool :=
  (plus facts (bounds facts x).1 (bounds facts y).1).any fun t => atMost facts t (.zero, MAX) 0

/-- A `-` whose right side is no larger than its left. -/
def subOk (facts : List Fact) (x y : E) : Bool :=
  (bounds facts y).1.any fun t => (bounds facts x).2.any fun u => atMost facts t u 0

/-- A value at most a constant: a shift count below the width, or a narrowing that fits. -/
def atMostConst (facts : List Fact) (K : Int) (e : E) : Bool :=
  (bounds facts e).1.any fun t => atMost facts t (.zero, K) 0

/-- A part `x[lo..hi]` inside its view: `lo ≤ hi`, and `hi` at most the extent. -/
def partOk (facts : List Fact) (lo hi : E) (extent : Term) : Bool :=
  subOk facts hi lo && (bounds facts hi).1.any fun t => atMost facts t extent 0

/-! ## Soundness of the search -/

section Search

variable (ρ : Nat → Nat)

/-- Every immutable value is a usize. -/
def Fine : Prop := ∀ n, ((ρ n : Nat) : Int) ≤ MAX

/-- Every fact holds under `ρ`. -/
def Holds (fs : List Fact) : Prop := ∀ f ∈ fs, f.1.val ρ - f.2.1.val ρ ≤ f.2.2

variable {ρ}

theorem Atom.val_nonneg (a : Atom) : 0 ≤ a.val ρ := by
  cases a <;> simp only [Atom.val]
  · exact Int.le_refl 0
  · exact Int.natCast_nonneg _
  · exact Int.mul_nonneg (Int.natCast_nonneg _) (Int.natCast_nonneg _)

theorem Atom.val_times {a : Atom} (h : a.isProd = false) (S : Nat) : (a.times S).val ρ = S * a.val ρ := by
  cases a <;> simp_all [Atom.isProd, Atom.times, Atom.val]

theorem scaled_holds {facts : List Fact} (h : Holds ρ facts) (atoms : List Atom) : Holds ρ (scaled facts atoms) := by
  intro f hf
  simp only [scaled, List.mem_flatMap, List.mem_map, List.mem_filter, Bool.and_eq_true, Bool.not_eq_true'] at hf
  obtain ⟨S, _, g, ⟨hg, hp1, hp2⟩, rfl⟩ := hf
  simp only [Atom.val_times hp1, Atom.val_times hp2]
  have := h g hg
  have hS : (0 : Int) ≤ S := Int.natCast_nonneg S
  rw [← Int.mul_sub, Int.mul_comm g.2.2]
  exact Int.mul_le_mul_of_nonneg_left this hS

/-- Every entry of the table is a true bound on `source - entry`. -/
def Good (ρ : Nat → Nat) (s : Atom) (best : List (Atom × Int)) : Prop := ∀ p ∈ best, s.val ρ - p.1.val ρ ≤ p.2

theorem get_mem {best : List (Atom × Int)} {a : Atom} {d : Int} (h : get best a = some d) : (a, d) ∈ best := by
  simp only [get, Option.map_eq_some_iff] at h
  obtain ⟨p, hp, rfl⟩ := h
  have := List.find?_some hp
  have hm := List.mem_of_find?_eq_some hp
  simp only [decide_eq_true_eq] at this
  rw [← this]; exact hm

theorem Good.put {s : Atom} {best : List (Atom × Int)} (h : Good ρ s best) {a : Atom} {d : Int}
    (hd : s.val ρ - a.val ρ ≤ d) : Good ρ s (put best a d) := by
  intro p hp
  rcases List.mem_cons.mp hp with rfl | hp
  · exact hd
  · exact h p (List.mem_filter.mp hp).1

theorem relax_good {edges : List Fact} (he : Holds ρ edges) {s : Atom} {best : List (Atom × Int)}
    (h : Good ρ s best) : Good ρ s (relax edges best).1 := by
  unfold relax
  suffices ∀ (es : List Fact), (∀ e ∈ es, e ∈ edges) → ∀ st : List (Atom × Int) × Bool, Good ρ s st.1 →
      Good ρ s (es.foldl (fun st e =>
        match get st.1 e.1 with
        | some da => if da + e.2.2 < (get st.1 e.2.1).getD (MAX + 1) then (put st.1 e.2.1 (da + e.2.2), true) else st
        | none => st) st).1 from this edges (fun _ h => h) _ h
  intro es
  induction es with
  | nil => intro _ st hst; exact hst
  | cons e rest ih =>
      intro hsub st hst
      simp only [List.foldl_cons]
      apply ih (fun x hx => hsub x (List.mem_cons_of_mem _ hx))
      split
      · next da hda =>
          split
          · have hsa := hst _ (get_mem hda)
            have hab := he e (hsub e (List.mem_cons_self ..))
            exact hst.put (by simp only at hsa ⊢; omega)
          · exact hst
      · exact hst

theorem rounds_good {edges : List Fact} (he : Holds ρ edges) {s : Atom} :
    ∀ (r : Nat) (best out : List (Atom × Int)), Good ρ s best → rounds edges r best = some out → Good ρ s out
  | 0, _, _, _, h => by simp [rounds] at h
  | r + 1, best, out, hb, h => by
      unfold rounds at h
      have hg := relax_good he hb
      split at h
      · next b' heq => rw [heq] at hg; exact rounds_good he r b' out hg h
      · next b' heq => rw [heq] at hg; rw [Option.some.inj h] at hg; exact hg

/-- **The search is sound.**  Whatever distance it returns bounds `source - target` under every
valuation that makes the facts true and every immutable value a usize. -/
theorem distance_sound {facts : List Fact} (hf : Holds ρ facts) (hρ : Fine ρ) {s t : Atom} {d : Int}
    (h : distance facts s t = some d) : s.val ρ - t.val ρ ≤ d := by
  unfold distance at h
  simp only [Option.bind_eq_some_iff] at h
  obtain ⟨out, hr, hg⟩ := h
  generalize hfs : facts ++ scaled facts [s, t] = fs at hr
  generalize dedup (s :: t :: Atom.zero :: endpoints fs) = atoms at hr
  have he : Holds ρ (fs ++ ((atoms.filter fun a => !a.isProd).map fun a => (a, Atom.zero, MAX))
      ++ atoms.map fun a => (Atom.zero, a, (0 : Int))) := by
    intro e he
    simp only [List.mem_append, List.mem_map, List.mem_filter] at he
    rcases he with (he | ⟨a, ⟨_, hp⟩, rfl⟩) | ⟨a, _, rfl⟩
    · rw [← hfs] at he
      rcases List.mem_append.mp he with he | he
      · exact hf e he
      · exact scaled_holds hf _ e he
    · cases a with
      | zero => simp [Atom.val, MAX]
      | plain n => simpa [Atom.val] using hρ n
      | prod n S => simp [Atom.isProd] at hp
    · simpa [Atom.val] using Atom.val_nonneg (ρ := ρ) a
  exact rounds_good he _ [(s, 0)] out (fun p hp => by rcases List.mem_singleton.mp hp with rfl; simp) hr _
    (get_mem hg)

theorem atMost_sound {facts : List Fact} (hf : Holds ρ facts) (hρ : Fine ρ) {x y : Term} {slack : Int}
    (h : atMost facts x y slack = true) : x.1.val ρ + x.2 - (y.1.val ρ + y.2) ≤ slack := by
  unfold atMost at h
  split at h
  · next d hd =>
      have := distance_sound hf hρ hd
      simp only [decide_eq_true_eq] at h
      omega
  · exact absurd h nofun

theorem ceiling_sound {facts : List Fact} (hf : Holds ρ facts) (hρ : Fine ρ) {a : Atom} {c : Int}
    (h : ceiling facts a = some c) : a.val ρ ≤ c := by
  unfold ceiling at h
  split at h
  · next d hd =>
      have := distance_sound hf hρ hd
      split at h
      · rw [← Option.some.inj h]; simpa [Atom.val] using this
      · exact absurd h nofun
  · exact absurd h nofun

end Search

/-! ## Soundness of the bounds -/

section Bounds

variable {ρ σ : Nat → Nat} {facts : List Fact}

/-- `t` is at least `v`. -/
def Above (ρ : Nat → Nat) (v : Int) (t : Term) : Prop := v ≤ t.1.val ρ + t.2

/-- `t` is at most `v`. -/
def Below (ρ : Nat → Nat) (v : Int) (t : Term) : Prop := t.1.val ρ + t.2 ≤ v

theorem plus_sound (hf : Holds ρ facts) (hρ : Fine ρ) {a b : Int} {ha hb : List Term}
    (h1 : ∀ p ∈ ha, Above ρ a p) (h2 : ∀ q ∈ hb, Above ρ b q) : ∀ t ∈ plus facts ha hb, Above ρ (a + b) t := by
  intro t ht
  simp only [plus, List.mem_flatMap, List.mem_append, List.mem_map, Option.mem_toList] at ht
  obtain ⟨p, hp, q, hq, ht⟩ := ht
  have hpa := h1 p hp
  have hqb := h2 q hq
  unfold Above at hpa hqb ⊢
  rcases ht with ⟨cap, hcap, rfl⟩ | ht
  · split at hcap
    · next hz => rw [hz] at hqb; simp only [Atom.val] at hqb; rw [← Option.some.inj hcap]; simp only; omega
    · have := ceiling_sound hf hρ hcap; simp only; omega
  · split at ht
    · exact absurd ht (List.not_mem_nil)
    · obtain ⟨cap, hcap, rfl⟩ := List.mem_map.mp ht
      have := ceiling_sound hf hρ (Option.mem_toList.mp hcap)
      simp only; omega

/-- A binary operation evaluated: both sides evaluated, and the operation applied without a trap. -/
theorem eval_bin {x y : E} {v : Nat} {f : Nat → Nat → Option Nat}
    (h : (match eval ρ σ x, eval ρ σ y with | some a, some b => f a b | _, _ => none) = some v) :
    ∃ a b, eval ρ σ x = some a ∧ eval ρ σ y = some b ∧ f a b = some v := by
  cases ha : eval ρ σ x <;> cases hb : eval ρ σ y <;> simp only [ha, hb] at h <;> try contradiction
  exact ⟨_, _, rfl, rfl, h⟩

theorem some_of_ite {α : Type} {c : Prop} [Decidable c] {a v : α} (h : (if c then some a else none) = some v) : c ∧ a = v := by
  split at h
  · exact ⟨‹c›, Option.some.inj h⟩
  · contradiction

theorem some_of_ite' {α : Type} {c : Prop} [Decidable c] {a v : α} (h : (if c then none else some a) = some v) : ¬c ∧ a = v := by
  split at h
  · contradiction
  · exact ⟨‹¬c›, Option.some.inj h⟩

theorem exact_sound : ∀ {e : E} {t : Term} {v : Nat}, exact e = some t → eval ρ σ e = some v →
    (v : Int) = t.1.val ρ + t.2
  | .lit k, t, v, h, hv => by
      simp only [exact, Option.some.injEq] at h; simp only [eval, Option.some.injEq] at hv
      subst h hv; simp [Atom.val]
  | .atom n, t, v, h, hv => by
      simp only [exact, Option.some.injEq] at h; simp only [eval, Option.some.injEq] at hv
      subst h hv; simp [Atom.val]
  | .var _, _, _, h, _ => by simp [exact] at h
  | .add x y, t, v, h, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if ((a + b : Nat) : Int) ≤ MAX then some (a + b) else none) hv
      obtain ⟨_, rfl⟩ := some_of_ite hab
      simp only [exact] at h
      split at h <;> try contradiction
      all_goals
        next hx hy =>
        rw [← Option.some.inj h]
        have h1 := exact_sound hx ha; have h2 := exact_sound hy hb
        simp only [Atom.val] at h1 h2 ⊢; push_cast; omega
  | .sub x y, t, v, h, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if b ≤ a then some (a - b) else none) hv
      obtain ⟨hle, rfl⟩ := some_of_ite hab
      simp only [exact] at h
      split at h <;> try contradiction
      next hx hy =>
      rw [← Option.some.inj h]
      have h1 := exact_sound hx ha; have h2 := exact_sound hy hb
      simp only [Atom.val] at h1 h2 ⊢; omega
  | .mul x y, t, v, h, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if ((a * b : Nat) : Int) ≤ MAX then some (a * b) else none) hv
      obtain ⟨_, rfl⟩ := some_of_ite hab
      simp only [exact] at h
      split at h <;> try contradiction
      · next n k hx hy =>
          obtain ⟨hk, rfl⟩ := some_of_ite (a := (Atom.prod n k.toNat, (0 : Int))) h
          have h1 := exact_sound hx ha; have h2 := exact_sound hy hb
          simp only [Atom.val, Int.add_zero, Int.zero_add] at h1 h2 ⊢
          rw [Int.toNat_of_nonneg (by omega), ← h2, ← h1]; push_cast; exact Int.mul_comm _ _
      · next k n hx hy =>
          obtain ⟨hk, rfl⟩ := some_of_ite (a := (Atom.prod n k.toNat, (0 : Int))) h
          have h1 := exact_sound hx ha; have h2 := exact_sound hy hb
          simp only [Atom.val, Int.add_zero, Int.zero_add] at h1 h2 ⊢
          rw [Int.toNat_of_nonneg (by omega), ← h1, ← h2]; push_cast; rfl
  | .div _ _, _, _, h, _ => by simp [exact] at h
  | .mod _ _, _, _, h, _ => by simp [exact] at h
  | .band _ _, _, _, h, _ => by simp [exact] at h
  | .min _ _, _, _, h, _ => by simp [exact] at h

/-- Every upper bound is at least the value and every lower bound at most it. -/
def Sound (ρ : Nat → Nat) (v : Nat) (b : List Term × List Term) : Prop :=
  (∀ t ∈ b.1, Above ρ v t) ∧ ∀ t ∈ b.2, Below ρ v t

theorem trim_sound {e : E} {v : Nat} (hv : eval ρ σ e = some v) {high low : List Term}
    (hh : ∀ t ∈ high, Above ρ v t) (hl : ∀ t ∈ low, Below ρ v t) : Sound ρ v (trim e high low) := by
  have hex : ∀ t ∈ (exact e).toList, (v : Int) = t.1.val ρ + t.2 := fun t ht =>
    exact_sound (Option.mem_toList.mp ht) hv
  refine ⟨fun t ht => ?_, fun t ht => ?_⟩
  · rcases List.mem_append.mp (mem_dedup (List.mem_of_mem_take ht)) with h | h
    · exact Int.le_of_eq (hex t h)
    · exact hh t h
  · rcases List.mem_append.mp (mem_dedup (List.mem_of_mem_take ht)) with h | h
    · exact Int.le_of_eq (hex t h).symm
    · rcases List.mem_cons.mp h with rfl | h
      · simp [Below, Atom.val]
      · exact hl t h

theorem consts_val {l : List Term} {t : Term} (h : t ∈ consts l) : t ∈ l ∧ t.1.val ρ = 0 := by
  simp only [consts, List.mem_filter, decide_eq_true_eq] at h
  exact ⟨h.1, by rw [h.2]; rfl⟩

/-- **The bounds are sound.**  Whatever `bounds` says an expression does not exceed, and reaches,
holds of its value whenever it evaluates without a trap. -/
theorem bounds_sound (hf : Holds ρ facts) (hρ : Fine ρ) :
    ∀ {e : E} {v : Nat}, eval ρ σ e = some v → Sound ρ v (bounds facts e)
  | .lit k, v, hv => by
      simp only [bounds]; exact trim_sound hv (fun _ h => absurd h List.not_mem_nil) (fun _ h => absurd h List.not_mem_nil)
  | .atom n, v, hv => by
      simp only [bounds]; exact trim_sound hv (fun _ h => absurd h List.not_mem_nil) (fun _ h => absurd h List.not_mem_nil)
  | .var n, v, hv => by
      simp only [bounds]; exact trim_sound hv (fun _ h => absurd h List.not_mem_nil) (fun _ h => absurd h List.not_mem_nil)
  | .mul x y, v, hv => by
      simp only [bounds]; exact trim_sound hv (fun _ h => absurd h List.not_mem_nil) (fun _ h => absurd h List.not_mem_nil)
  | .add x y, v, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if ((a + b : Nat) : Int) ≤ MAX then some (a + b) else none) hv
      obtain ⟨_, rfl⟩ := some_of_ite hab
      have sx := bounds_sound hf hρ ha; have sy := bounds_sound hf hρ hb
      simp only [bounds]
      refine trim_sound hv (fun t ht => ?_) (fun t ht => ?_)
      · have := plus_sound hf hρ sx.1 sy.1 t ht; unfold Above at this ⊢; push_cast; exact this
      · simp only [List.mem_flatMap, List.mem_map] at ht
        obtain ⟨p, hp, q, hq, rfl⟩ := ht
        obtain ⟨hq, hq0⟩ := consts_val hq
        have h1 := sx.2 p hp; have h2 := sy.2 q hq
        unfold Below at h1 h2 ⊢; push_cast; rw [hq0] at h2; omega
  | .sub x y, v, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if b ≤ a then some (a - b) else none) hv
      obtain ⟨hle, rfl⟩ := some_of_ite hab
      have sx := bounds_sound hf hρ ha; have sy := bounds_sound hf hρ hb
      simp only [bounds]
      refine trim_sound hv (fun t ht => ?_) (fun t ht => ?_)
      · simp only [List.mem_flatMap, List.mem_map] at ht
        obtain ⟨p, hp, q, hq, rfl⟩ := ht
        obtain ⟨hq, hq0⟩ := consts_val hq
        have h1 := sx.1 p hp; have h2 := sy.2 q hq
        have hc : ((a - b : Nat) : Int) = a - b := Int.ofNat_sub hle
        unfold Above at h1 ⊢; unfold Below at h2; rw [hq0] at h2; dsimp only; omega
      · simp only [List.mem_flatMap, List.mem_map] at ht
        obtain ⟨p, hp, q, hq, rfl⟩ := ht
        obtain ⟨hq, hq0⟩ := consts_val hq
        have h1 := sx.2 p hp; have h2 := sy.1 q hq
        have hc : ((a - b : Nat) : Int) = a - b := Int.ofNat_sub hle
        unfold Below at h1 ⊢; unfold Above at h2; rw [hq0] at h2; dsimp only; omega
  | .div x y, v, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if b = 0 then none else some (a / b)) hv
      obtain ⟨_, rfl⟩ := some_of_ite' hab
      have sx := bounds_sound hf hρ ha
      simp only [bounds]
      refine trim_sound hv (fun t ht => ?_) (fun _ h => absurd h List.not_mem_nil)
      have := sx.1 t ht; unfold Above at this ⊢
      have : ((a / b : Nat) : Int) ≤ a := Int.ofNat_le.mpr (Nat.div_le_self a b)
      omega
  | .mod x y, v, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => if b = 0 then none else some (a % b)) hv
      obtain ⟨hb0, rfl⟩ := some_of_ite' hab
      have sx := bounds_sound hf hρ ha; have sy := bounds_sound hf hρ hb
      simp only [bounds]
      refine trim_sound hv (fun t ht => ?_) (fun _ h => absurd h List.not_mem_nil)
      rcases List.mem_append.mp ht with ht | ht
      · have := sx.1 t ht; unfold Above at this ⊢
        have : ((a % b : Nat) : Int) ≤ a := Int.ofNat_le.mpr (Nat.mod_le a b)
        omega
      · obtain ⟨q, hq, rfl⟩ := List.mem_map.mp ht
        have := sy.1 q hq; unfold Above at this ⊢
        have : ((a % b : Nat) : Int) < b := Int.ofNat_lt.mpr (Nat.mod_lt a (Nat.pos_of_ne_zero hb0))
        simp only at this ⊢; omega
  | .band x y, v, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => some (a &&& b)) hv
      cases Option.some.inj hab
      have sx := bounds_sound hf hρ ha; have sy := bounds_sound hf hρ hb
      simp only [bounds]
      refine trim_sound hv (fun t ht => ?_) (fun _ h => absurd h List.not_mem_nil)
      rcases List.mem_append.mp ht with ht | ht
      · have := sx.1 t ht; unfold Above at this ⊢
        have : ((a &&& b : Nat) : Int) ≤ a := Int.ofNat_le.mpr Nat.and_le_left
        omega
      · have := sy.1 t ht; unfold Above at this ⊢
        have : ((a &&& b : Nat) : Int) ≤ b := Int.ofNat_le.mpr Nat.and_le_right
        omega
  | .min x y, v, hv => by
      obtain ⟨a, b, ha, hb, hab⟩ := eval_bin (f := fun a b => some (Nat.min a b)) hv
      cases Option.some.inj hab
      have sx := bounds_sound hf hρ ha; have sy := bounds_sound hf hρ hb
      simp only [bounds]
      refine trim_sound hv (fun t ht => ?_) (fun _ h => absurd h List.not_mem_nil)
      rcases List.mem_append.mp ht with ht | ht
      · have := sx.1 t ht; unfold Above at this ⊢
        have : ((Nat.min a b : Nat) : Int) ≤ a := Int.ofNat_le.mpr (Nat.min_le_left a b)
        omega
      · have := sy.1 t ht; unfold Above at this ⊢
        have : ((Nat.min a b : Nat) : Int) ≤ b := Int.ofNat_le.mpr (Nat.min_le_right a b)
        omega

end Bounds

/-! ## The five decisions are sound -/

section Decisions

variable {ρ σ : Nat → Nat} {facts : List Fact}

/-- **A discharged index is in bounds.** -/
theorem index_sound (hf : Holds ρ facts) (hρ : Fine ρ) {i : E} {extent : Term} {v : Nat}
    (h : index facts i extent = true) (hv : eval ρ σ i = some v) : (v : Int) < extent.1.val ρ + extent.2 := by
  obtain ⟨x, hx, hm⟩ := List.any_eq_true.mp h
  have := (bounds_sound hf hρ hv).1 x hx
  have := atMost_sound hf hρ hm
  unfold Above at *; omega

/-- **A discharged `+` cannot pass the largest usize**, so its overflow guard never fires. -/
theorem add_sound (hf : Holds ρ facts) (hρ : Fine ρ) {x y : E} {a b : Nat} (h : addOk facts x y = true)
    (ha : eval ρ σ x = some a) (hb : eval ρ σ y = some b) : ((a + b : Nat) : Int) ≤ MAX := by
  obtain ⟨t, ht, hm⟩ := List.any_eq_true.mp h
  have := plus_sound hf hρ (bounds_sound hf hρ ha).1 (bounds_sound hf hρ hb).1 t ht
  have := atMost_sound hf hρ hm
  unfold Above at *; simp only [Atom.val] at *; push_cast; omega

/-- **A discharged `-` does not go below zero**, so its guard never fires. -/
theorem sub_sound (hf : Holds ρ facts) (hρ : Fine ρ) {x y : E} {a b : Nat} (h : subOk facts x y = true)
    (ha : eval ρ σ x = some a) (hb : eval ρ σ y = some b) : b ≤ a := by
  obtain ⟨t, ht, hm⟩ := List.any_eq_true.mp h
  obtain ⟨u, hu, hm⟩ := List.any_eq_true.mp hm
  have h1 := (bounds_sound hf hρ hb).1 t ht
  have h2 := (bounds_sound hf hρ ha).2 u hu
  have := atMost_sound hf hρ hm
  unfold Above Below at *; omega

/-- **A value discharged below a constant is at most it**: a shift count below the width, or the
operand of a narrowing conversion that fits. -/
theorem atMostConst_sound (hf : Holds ρ facts) (hρ : Fine ρ) {K : Int} {e : E} {v : Nat}
    (h : atMostConst facts K e = true) (hv : eval ρ σ e = some v) : (v : Int) ≤ K := by
  obtain ⟨t, ht, hm⟩ := List.any_eq_true.mp h
  have := (bounds_sound hf hρ hv).1 t ht
  have := atMost_sound hf hρ hm
  unfold Above at *; simp only [Atom.val] at *; omega

/-- **A discharged part lies inside its view**: its bounds are in order and its end is at most the
extent, so its guard never fires. -/
theorem part_sound (hf : Holds ρ facts) (hρ : Fine ρ) {lo hi : E} {extent : Term} {a b : Nat}
    (h : partOk facts lo hi extent = true) (ha : eval ρ σ lo = some a) (hb : eval ρ σ hi = some b) :
    a ≤ b ∧ (b : Int) ≤ extent.1.val ρ + extent.2 := by
  simp only [partOk, Bool.and_eq_true] at h
  refine ⟨sub_sound hf hρ h.1 hb ha, ?_⟩
  obtain ⟨t, ht, hm⟩ := List.any_eq_true.mp h.2
  have := (bounds_sound hf hρ hb).1 t ht
  have := atMost_sound hf hρ hm
  unfold Above at *; omega

end Decisions

end Facts
end Cairn
