/-
The lane pool's region protocol: what `cr::par::run` in `src/cairn/runtime/cairn_parallel.hpp` does
with a region of `n` lanes once it is wide enough to use the pool.  The ownership calculus assumes
that a region completes before the next statement; this file proves the protocol makes it so.

The region's indices are cut into `H` homes, home `h` holding `[bd h, bd (h + 1))`, each with a
counter of its own that starts at `bd h`.  The thread that starts a region is its first lane.  Every
lane, the starter and any pool worker that joins, goes through the homes in an order of its own: at
each it loads the home's counter and, while it is below the home's end, bumps it by `grain` and runs
the indices it claimed, and then it moves on.  A worker starts on the same home in every region,
which is what keeps its indices in its own core's cache from one region to the next, but nothing
here depends on the order a worker takes, only on the starter's taking every home.  When the starter
has been through its order it unlinks the region, after which no worker can join, and waits until no
worker is inside; a worker leaves once it has been through its order.  A worker joins only a linked
region (`Pool::adopt`).

The model interleaves atomic steps: a lane loading a counter, bumping it, running one claimed
chunk, a worker joining or leaving, the starter unlinking, and the starter returning.  It treats
every step as sequentially consistent.  The C++ bumps each counter with a relaxed `fetch_add`, which
is enough because a counter only hands out indices, and orders a leaving worker against the waiting
starter with the sequentially consistent pair in `leave` and `settle`.  That the C++ performs these
steps, and cuts the homes as `Cut.Ok` requires, is review, not proof.  A region below
`lanes::CUTOFF`, or in a process with one lane, is the plain loop on the starting thread and needs
none of this.
-/

namespace Cairn
namespace Region

/-- Where one lane is.  `r` counts the homes of its order it has finished. -/
inductive Phase where
  /-- A worker outside the region. -/
  | out
  /-- About to load the counter of its `r`-th home. -/
  | check (r : Nat)
  /-- About to bump it. -/
  | claim (r : Nat)
  /-- Running the claimed indices `[b, e)` of its `r`-th home. -/
  | run (r b e : Nat)
  /-- The starter, after unlinking: waiting for the workers inside. -/
  | settle
  /-- The starter, returned. -/
  | back
deriving DecidableEq, Repr

/-- The shared state of one region and its lanes.  `next h` is home `h`'s counter; `ran i` counts
how often index `i` has run. -/
structure St where
  next : Nat → Nat
  linked : Bool
  users : Nat
  starter : Phase
  workers : Nat → Phase
  ran : Nat → Nat

/-- How a region is cut: `H` homes with bounds `bd`, the starter's order `so` and worker `k`'s
order `wo k`, each naming the home it takes `r`-th. -/
structure Cut where
  H : Nat
  bd : Nat → Nat
  so : Nat → Nat
  wo : Nat → Nat → Nat

/-- What the pool's cut of `[0, n)` must satisfy: the homes are consecutive and cover `[0, n)`,
every order names homes, and the starter's names every home. -/
structure Cut.Ok (n : Nat) (c : Cut) : Prop where
  zero : c.bd 0 = 0
  top : c.bd c.H = n
  mono : ∀ a b, a ≤ b → c.bd a ≤ c.bd b
  so_lt : ∀ r, r < c.H → c.so r < c.H
  wo_lt : ∀ k r, r < c.H → c.wo k r < c.H
  every : ∀ h, h < c.H → ∃ r, r < c.H ∧ c.so r = h

/-- Point update of the workers. -/
def upd (w : Nat → Phase) (k : Nat) (p : Phase) : Nat → Phase := fun j => if j = k then p else w j

/-- Running the chunk `[b, e)` once. -/
def bump (ran : Nat → Nat) (b e : Nat) : Nat → Nat := fun i => if b ≤ i then (if i < e then ran i + 1 else ran i) else ran i

/-- Home `h`'s counter bumped by `g`. -/
def bumpAt (next : Nat → Nat) (h g : Nat) : Nat → Nat := fun j => if j = h then next h + g else next j

/-- One step of a lane inside the region, the starter or a worker alike, taking the homes in the
order `o`: load a counter, bump it, or run the chunk.  `none` is having been through every home. -/
inductive Lane (c : Cut) (o : Nat → Nat) (g : Nat) :
    Phase → (Nat → Nat) → (Nat → Nat) → Option Phase → (Nat → Nat) → (Nat → Nat) → Prop where
  | checkMore {r next ran} : r < c.H → next (o r) < c.bd (o r + 1) →
      Lane c o g (.check r) next ran (some (.claim r)) next ran
  | checkSkip {r next ran} : r < c.H → c.bd (o r + 1) ≤ next (o r) →
      Lane c o g (.check r) next ran (some (.check (r + 1))) next ran
  | checkDone {r next ran} : c.H ≤ r → Lane c o g (.check r) next ran none next ran
  | claimMore {r next ran} : next (o r) < c.bd (o r + 1) →
      Lane c o g (.claim r) next ran (some (.run r (next (o r)) (min (c.bd (o r + 1)) (next (o r) + g))))
        (bumpAt next (o r) g) ran
  | claimDone {r next ran} : c.bd (o r + 1) ≤ next (o r) →
      Lane c o g (.claim r) next ran (some (.check (r + 1))) (bumpAt next (o r) g) ran
  | run {r b e next ran} : Lane c o g (.run r b e) next ran (some (.check r)) next (bump ran b e)

/-- One step of the region with `W` workers.  `joins` says whether a worker may join; the region's
own semantics allows it, and `finishes` below shows that none needs to. -/
inductive Step (c : Cut) (g W : Nat) (joins : Bool) : St → St → Prop where
  | starter {s p next ran} : Lane c c.so g s.starter s.next s.ran (some p) next ran →
      Step c g W joins s { s with starter := p, next := next, ran := ran }
  | unlink {s next ran} : Lane c c.so g s.starter s.next s.ran none next ran →
      Step c g W joins s { s with starter := .settle, linked := false, next := next, ran := ran }
  | back {s} : s.starter = .settle → s.users = 0 → Step c g W joins s { s with starter := .back }
  | join {s k} : joins = true → k < W → s.workers k = .out → s.linked = true →
      Step c g W joins s { s with workers := upd s.workers k (.check 0), users := s.users + 1 }
  | worker {s k p next ran} : k < W → Lane c (c.wo k) g (s.workers k) s.next s.ran (some p) next ran →
      Step c g W joins s { s with workers := upd s.workers k p, next := next, ran := ran }
  | leave {s k next ran} : k < W → Lane c (c.wo k) g (s.workers k) s.next s.ran none next ran →
      Step c g W joins s { s with workers := upd s.workers k .out, users := s.users - 1, next := next, ran := ran }

/-- Steps in sequence. -/
inductive Reach (c : Cut) (g W : Nat) (joins : Bool) : St → St → Prop where
  | refl (s : St) : Reach c g W joins s s
  | tail {s t u : St} : Reach c g W joins s t → Step c g W joins t u → Reach c g W joins s u

/-- A region just published: every counter at the start of its home, the starter about to load its
first, no worker inside. -/
def start (c : Cut) : St :=
  { next := c.bd, linked := true, users := 0, starter := .check 0, workers := fun _ => .out, ran := fun _ => 0 }

/-! ## The homes -/

variable {n : Nat} {c : Cut}

/-- Two homes that share an index are one home. -/
theorem Cut.Ok.same (ok : c.Ok n) {h h' i : Nat} (h1 : c.bd h ≤ i) (h2 : i < c.bd (h + 1)) (h3 : c.bd h' ≤ i)
    (h4 : i < c.bd (h' + 1)) : h = h' := by
  rcases Nat.lt_trichotomy h h' with hl | he | hl
  · have := ok.mono (h + 1) h' (by omega); omega
  · exact he
  · have := ok.mono (h' + 1) h (by omega); omega

/-- Every index below `bd m` is in a home below `m`. -/
theorem Cut.Ok.home_below (ok : c.Ok n) : ∀ m i, i < c.bd m → ∃ h, h < m ∧ c.bd h ≤ i ∧ i < c.bd (h + 1)
  | 0, i, hi => by rw [ok.zero] at hi; exact absurd hi (Nat.not_lt_zero _)
  | m + 1, i, hi => by
      by_cases hm : i < c.bd m
      · obtain ⟨h, hh, h1, h2⟩ := ok.home_below m i hm
        exact ⟨h, by omega, h1, h2⟩
      · exact ⟨m, by omega, by omega, hi⟩

/-- Every index of the region is in some home. -/
theorem Cut.Ok.home (ok : c.Ok n) {i : Nat} (hi : i < n) : ∃ h, h < c.H ∧ c.bd h ≤ i ∧ i < c.bd (h + 1) :=
  ok.home_below c.H i (by rw [ok.top]; exact hi)

/-- A home below `H` ends at or before `n`. -/
theorem Cut.Ok.end_le (ok : c.Ok n) {h : Nat} (hh : h < c.H) : c.bd (h + 1) ≤ n := by
  have := ok.mono (h + 1) c.H (by omega); rw [ok.top] at this; exact this

/-! ## Counting over the workers -/

/-- `f 0 + ... + f (W - 1)`. -/
def total : Nat → (Nat → Nat) → Nat
  | 0, _ => 0
  | W + 1, f => total W f + f W

theorem total_congr {f f' : Nat → Nat} : ∀ W, (∀ j < W, f j = f' j) → total W f = total W f'
  | 0, _ => rfl
  | W + 1, h => by
      simp only [total, total_congr W fun j hj => h j (by omega), h W (by omega)]

theorem total_upd (F : Phase → Nat) (w : Nat → Phase) (k : Nat) (p : Phase) :
    ∀ W, k < W → total W (fun j => F (upd w k p j)) + F (w k) = total W (fun j => F (w j)) + F p
  | 0, h => absurd h (Nat.not_lt_zero _)
  | W + 1, h => by
      simp only [total]
      rcases Nat.lt_or_ge k W with hk | hk
      · have ih := total_upd F w k p W hk
        have hW : upd w k p W = w W := by simp [upd]; omega
        rw [hW]; omega
      · have hkW : k = W := by omega
        subst hkW
        have hsame : total k (fun j => F (upd w k p j)) = total k (fun j => F (w j)) :=
          total_congr k fun j hj => by
            have : j ≠ k := by omega
            simp [upd, this]
        rw [hsame]; simp [upd]; omega

theorem total_zero {f : Nat → Nat} : ∀ W, total W f = 0 → ∀ j < W, f j = 0
  | 0, _, _, hj => absurd hj (Nat.not_lt_zero _)
  | W + 1, h, j, hj => by
      simp only [total] at h
      rcases Nat.lt_or_ge j W with hjW | hjW
      · exact total_zero W (by omega) j hjW
      · have : j = W := by omega
        subst this; omega

/-- A total that no term raises and one term lowers is lower. -/
theorem total_le {f f' : Nat → Nat} : ∀ W, (∀ j < W, f' j ≤ f j) → total W f' ≤ total W f
  | 0, _ => Nat.le_refl _
  | W + 1, h => by
      simp only [total]
      have := total_le W fun j hj => h j (by omega)
      have := h W (by omega)
      omega

theorem total_lt {f f' : Nat → Nat} : ∀ W, (∀ j < W, f' j ≤ f j) → ∀ j, j < W → f' j < f j → total W f' < total W f
  | 0, _, _, hj, _ => absurd hj (Nat.not_lt_zero _)
  | W + 1, h, j, hj, hlt => by
      simp only [total]
      rcases Nat.lt_or_ge j W with hjW | hjW
      · have := total_lt W (fun i hi => h i (by omega)) j hjW hlt
        have := h W (by omega)
        omega
      · have : j = W := by omega
        subst this
        have := total_le j fun i hi => h i (by omega)
        omega

/-- A lane inside the region: loading, bumping or running. -/
def inside : Phase → Bool
  | .check _ => true
  | .claim _ => true
  | .run _ _ _ => true
  | _ => false

/-- The lane is running index `i` right now. -/
def runs (i : Nat) : Phase → Bool
  | .run _ b e => if b ≤ i then decide (i < e) else false
  | _ => false

/-- A lane that is claiming or running is at a home of its order. -/
def fits (H : Nat) : Phase → Prop
  | .claim r => r < H
  | .run r _ _ => r < H
  | _ => True

/-- How many homes of its order the starter has been through. -/
def reached (H : Nat) : Phase → Nat
  | .check r => r
  | .claim r => r
  | .run r _ _ => r
  | .settle => H
  | .back => H
  | .out => 0

/-- One for a Boolean that holds.  The conditions it counts are kept atomic, one comparison each,
because `omega` reaches for choice to use a negated conjunction. -/
def one (b : Bool) : Nat := if b then 1 else 0

@[simp] theorem one_true : one true = 1 := rfl
@[simp] theorem one_false : one false = 0 := rfl

@[simp] theorem inside_out : inside .out = false := rfl
@[simp] theorem inside_check (r : Nat) : inside (.check r) = true := rfl
@[simp] theorem runs_out (i : Nat) : runs i .out = false := rfl
@[simp] theorem runs_check (i r : Nat) : runs i (.check r) = false := rfl
@[simp] theorem runs_claim (i r : Nat) : runs i (.claim r) = false := rfl
@[simp] theorem runs_settle (i : Nat) : runs i .settle = false := rfl
@[simp] theorem runs_back (i : Nat) : runs i .back = false := rfl

/-- How many lanes are running index `i`. -/
def cover (W : Nat) (s : St) (i : Nat) : Nat :=
  one (runs i s.starter) + total W fun k => one (runs i (s.workers k))

/-! ## The invariant -/

/-- What holds of every state a region reaches.  `once` is the heart of it: an index of a home below
the home's counter has run or is running in exactly one lane, and one at or above it has not been
touched.  `seen` is what the starter knows when it moves on: each home it has been through is used up. -/
structure Inv (n W : Nat) (c : Cut) (s : St) : Prop where
  once : ∀ h i, c.bd h ≤ i → i < c.bd (h + 1) → s.ran i + cover W s i = if i < s.next h then 1 else 0
  outside : ∀ i, n ≤ i → s.ran i + cover W s i = 0
  low : ∀ h, c.bd h ≤ s.next h
  users : s.users = total W fun k => one (inside (s.workers k))
  workers : ∀ k, s.workers k ≠ .settle ∧ s.workers k ≠ .back
  fitsStarter : fits c.H s.starter
  fitsWorkers : ∀ k, fits c.H (s.workers k)
  linked : s.linked = inside s.starter
  starter : s.starter ≠ .out
  seen : ∀ r, r < c.H → r < reached c.H s.starter → c.bd (c.so r + 1) ≤ s.next (c.so r)
  quiet : s.starter = .back → s.users = 0

theorem total_nil : ∀ W, total W (fun _ => 0) = 0
  | 0 => rfl
  | W + 1 => by simp [total, total_nil W]

theorem Inv.initial (n W : Nat) (c : Cut) : Inv n W c (start c) where
  once h i h1 _ := by simp [Region.start, cover, one, runs, total_nil, Nat.not_lt.mpr h1]
  outside i _ := by simp [Region.start, cover, one, runs, total_nil]
  low _ := Nat.le_refl _
  users := by simp [Region.start, one, inside, total_nil]
  workers _ := ⟨nofun, nofun⟩
  fitsStarter := trivial
  fitsWorkers _ := trivial
  linked := rfl
  starter := nofun
  seen r _ h := by simp [Region.start, reached] at h
  quiet h := Phase.noConfusion h

/-! ## One lane's step -/

theorem upd_upd (w : Nat → Phase) (k : Nat) (p q : Phase) : upd (upd w k q) k p = upd w k p :=
  funext fun j => by unfold upd; split <;> rfl

theorem upd_self (w : Nat → Phase) (k : Nat) : upd w k (w k) = w :=
  funext fun j => by unfold upd; split <;> simp_all

/-- Changing one worker's phase changes a total by the difference at that worker. -/
theorem total_swap (F : Phase → Nat) (w : Nat → Phase) {W k : Nat} (hk : k < W) (p q : Phase) :
    total W (fun j => F (upd w k p j)) + F q = total W (fun j => F (upd w k q j)) + F p := by
  have h := total_upd F (upd w k q) k p W hk
  rw [upd_upd] at h
  simpa [upd] using h

variable {g : Nat} {o : Nat → Nat} {p : Phase} {next next' : Nat → Nat} {ran ran' : Nat → Nat} {q : Option Phase}

theorem one_runs (i r b e : Nat) : one (runs i (.run r b e)) = if b ≤ i then (if i < e then 1 else 0) else 0 := by
  unfold runs one
  by_cases h1 : b ≤ i <;> by_cases h2 : i < e <;> simp [h1, h2]

theorem bumpAt_ge (next : Nat → Nat) (h g j : Nat) : next j ≤ bumpAt next h g j := by
  unfold bumpAt
  split
  · rename_i hj; subst hj; omega
  · exact Nat.le_refl _

theorem Lane.inside_from (h : Lane c o g p next ran q next' ran') : inside p = true := by cases h <;> rfl

theorem Lane.inside_to {p' : Phase} (h : Lane c o g p next ran (some p') next' ran') : inside p' = true := by
  cases h <;> rfl

theorem Lane.mono (h : Lane c o g p next ran q next' ran') : ∀ j, next j ≤ next' j := by
  cases h <;> first | exact fun j => Nat.le_refl _ | exact fun j => bumpAt_ge _ _ _ j

theorem Lane.done (h : Lane c o g p next ran none next' ran') : next' = next ∧ ran' = ran ∧ ∃ r, p = .check r ∧ c.H ≤ r := by
  cases h with
  | checkDone hr => exact ⟨rfl, rfl, _, rfl, hr⟩

theorem Lane.fits_next (h : Lane c o g p next ran (some p') next' ran') (hf : fits c.H p) : fits c.H p' := by
  cases h with
  | checkMore hr _ => exact hr
  | checkSkip _ _ => trivial
  | claimMore _ => exact hf
  | claimDone _ => trivial
  | run => trivial

/-- **Each counter hands out each index of its home once.**  A lane's step keeps every index of a home
below that home's counter run or running in exactly one lane, every index at or above it untouched,
and every index past the region untouched. -/
theorem Lane.once (ok : c.Ok n) (ho : ∀ r, r < c.H → o r < c.H) (h : Lane c o g p next ran q next' ran')
    (hf : fits c.H p) (rest : Nat → Nat) (hlow : ∀ h, c.bd h ≤ next h)
    (hin : ∀ h i, c.bd h ≤ i → i < c.bd (h + 1) → ran i + (one (runs i p) + rest i) = if i < next h then 1 else 0)
    (hout : ∀ i, n ≤ i → ran i + (one (runs i p) + rest i) = 0) :
    (∀ h i, c.bd h ≤ i → i < c.bd (h + 1) →
      ran' i + (one (runs i (q.getD .out)) + rest i) = if i < next' h then 1 else 0) ∧
    (∀ i, n ≤ i → ran' i + (one (runs i (q.getD .out)) + rest i) = 0) := by
  cases h with
  | checkMore _ _ => exact ⟨fun h i h1 h2 => by simpa using hin h i h1 h2, fun i hi => by simpa using hout i hi⟩
  | checkSkip _ _ => exact ⟨fun h i h1 h2 => by simpa using hin h i h1 h2, fun i hi => by simpa using hout i hi⟩
  | checkDone _ => exact ⟨fun h i h1 h2 => by simpa using hin h i h1 h2, fun i hi => by simpa using hout i hi⟩
  | @claimDone r _ _ hle =>
      refine ⟨fun h i h1 h2 => ?_, fun i hi => by simpa using hout i hi⟩
      have := hin h i h1 h2
      simp only [Option.getD, runs_claim, runs_check, one_false] at this ⊢
      unfold bumpAt
      by_cases hh : h = o r
      · subst hh
        have h3 : i < next (o r) := by omega
        have h4 : i < next (o r) + g := by omega
        simp only [h3, h4, ↓reduceIte] at this ⊢; exact this
      · simp only [hh, ↓reduceIte]; exact this
  | @claimMore r _ _ hlt =>
      have hr : r < c.H := hf
      have hH := ho r hr
      have hend := ok.end_le hH
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_⟩
      · have := hin h i h1 h2
        simp only [Option.getD, runs_claim, one_false] at this ⊢
        rw [one_runs]
        unfold bumpAt
        by_cases hh : h = o r
        · subst hh
          simp only [↓reduceIte]
          generalize hB : min (c.bd (o r + 1)) (next (o r) + g) = B
          (repeat' split) <;> (repeat' split at this) <;> omega
        · simp only [hh, ↓reduceIte]
          have hlo := hlow (o r)
          by_cases ha : next (o r) ≤ i
          · have hb : min (c.bd (o r + 1)) (next (o r) + g) ≤ i :=
              Nat.le_of_not_lt fun hb => hh (ok.same h1 h2 (by omega) (by omega))
            (repeat' split) <;> (repeat' split at this) <;> omega
          · (repeat' split) <;> (repeat' split at this) <;> omega
      · have := hout i hi
        simp only [Option.getD, runs_claim, one_false] at this ⊢
        rw [one_runs]
        (repeat' split) <;> omega
  | @run r b e _ _ =>
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_⟩
      · have := hin h i h1 h2
        simp only [Option.getD, runs_check, one_false, bump] at this ⊢
        rw [one_runs] at this
        (repeat' split) <;> (repeat' split at this) <;> omega
      · have := hout i hi
        simp only [Option.getD, runs_check, one_false, bump] at this ⊢
        rw [one_runs] at this
        (repeat' split) <;> (repeat' split at this) <;> omega

/-! ## Every reachable state keeps the invariant -/

theorem seen_mono {s : St} {next' : Nat → Nat} (hs : ∀ r, r < c.H → r < reached c.H s.starter →
    c.bd (c.so r + 1) ≤ s.next (c.so r)) (hm : ∀ j, s.next j ≤ next' j) :
    ∀ r, r < c.H → r < reached c.H s.starter → c.bd (c.so r + 1) ≤ next' (c.so r) :=
  fun r hr hre => Nat.le_trans (hs r hr hre) (hm _)

/-- What the starter knows after its own step: a home it moves past is used up. -/
theorem Lane.seen (h : Lane c c.so g p next ran (some p') next' ran')
    (hs : ∀ r, r < c.H → r < reached c.H p → c.bd (c.so r + 1) ≤ next (c.so r)) :
    ∀ r, r < c.H → r < reached c.H p' → c.bd (c.so r + 1) ≤ next' (c.so r) := by
  have hm := h.mono
  intro r hr hre
  cases h with
  | checkMore _ _ => exact hs r hr hre
  | @checkSkip r0 _ _ _ hle =>
      simp only [reached] at hre
      rcases Nat.lt_or_ge r r0 with hlt | hge
      · exact hs r hr hlt
      · have : r = r0 := by omega
        subst this; exact hle
  | claimMore _ => exact Nat.le_trans (hs r hr hre) (hm _)
  | @claimDone r0 _ _ hle =>
      simp only [reached] at hre
      rcases Nat.lt_or_ge r r0 with hlt | hge
      · exact Nat.le_trans (hs r hr hlt) (hm _)
      · have : r = r0 := by omega
        subst this; exact Nat.le_trans hle (hm _)
  | run => exact hs r hr hre

theorem Inv.step (ok : c.Ok n) {W : Nat} {joins : Bool} {s t : St} (hs : Inv n W c s) (h : Step c g W joins s t) :
    Inv n W c t := by
  cases h with
  | @starter p' next' ran' hl =>
      have := hl.once ok ok.so_lt hs.fitsStarter (fun i => total W fun k => one (runs i (s.workers k))) hs.low
        (fun h i h1 h2 => hs.once h i h1 h2) (fun i hi => hs.outside i hi)
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_, fun h => Nat.le_trans (hs.low h) (hl.mono h), hs.users,
        hs.workers, hl.fits_next hs.fitsStarter, hs.fitsWorkers, ?_, ?_, hl.seen hs.seen, ?_⟩
      · simpa [cover] using this.1 h i h1 h2
      · simpa [cover] using this.2 i hi
      · show s.linked = inside p'
        rw [hl.inside_to, hs.linked, hl.inside_from]
      · intro e; dsimp only at e; have := hl.inside_to; rw [e] at this; exact Bool.noConfusion this
      · intro e; dsimp only at e; have := hl.inside_to; rw [e] at this; exact Bool.noConfusion this
  | @unlink next' ran' hl =>
      obtain ⟨hn, hr, r0, hp, hr0⟩ := hl.done
      subst hn hr
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_, hs.low, hs.users, hs.workers, trivial, hs.fitsWorkers, rfl, nofun,
        fun r hr _ => hs.seen r hr (by rw [hp]; simp only [reached]; omega), nofun⟩
      · have := hs.once h i h1 h2; simpa [cover, runs, hp] using this
      · have := hs.outside i hi; simpa [cover, runs, hp] using this
  | back hset hu =>
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_, hs.low, hs.users, hs.workers, trivial, hs.fitsWorkers, ?_, nofun,
        fun r hr _ => hs.seen r hr (by rw [hset]; simp only [reached]; exact hr), fun _ => hu⟩
      · have := hs.once h i h1 h2; simpa [cover, runs, hset] using this
      · have := hs.outside i hi; simpa [cover, runs, hset] using this
      · show s.linked = false; rw [hs.linked, hset]; rfl
  | @join k _ hk hout hlk =>
      have hsw := total_swap (fun ph => one (inside ph)) s.workers hk (.check 0) (s.workers k)
      rw [upd_self] at hsw
      simp only [hout, inside_out, inside_check, one_true, one_false] at hsw
      have hr (i : Nat) := total_swap (fun ph => one (runs i ph)) s.workers hk (.check 0) (s.workers k)
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_, hs.low, ?_, fun j => ?_, hs.fitsStarter, fun j => ?_, hs.linked,
        hs.starter, hs.seen, fun hb => ?_⟩
      · have hri := hr i
        rw [upd_self] at hri
        have := hs.once h i h1 h2
        simp only [cover, hout, runs_out, runs_check, one_false] at this hri ⊢
        omega
      · have hri := hr i
        rw [upd_self] at hri
        have := hs.outside i hi
        simp only [cover, hout, runs_out, runs_check, one_false] at this hri ⊢
        omega
      · dsimp only
        rw [hs.users]; omega
      · dsimp only; unfold upd; split
        · exact ⟨nofun, nofun⟩
        · exact hs.workers j
      · dsimp only; unfold upd; split
        · trivial
        · exact hs.fitsWorkers j
      · have := hs.linked
        dsimp only at hb
        rw [hb, hlk] at this
        exact Bool.noConfusion this
  | @worker k p' next' ran' hk hl =>
      have := hl.once ok (ok.wo_lt k) (hs.fitsWorkers k)
        (fun i => one (runs i s.starter) + total W fun j => one (runs i (upd s.workers k .out j))) hs.low
        (fun h i h1 h2 => by
          have hsw := total_swap (fun ph => one (runs i ph)) s.workers hk (s.workers k) .out
          rw [upd_self] at hsw
          have := hs.once h i h1 h2
          simp only [cover, runs_out, one_false] at this hsw ⊢
          omega)
        (fun i hi => by
          have hsw := total_swap (fun ph => one (runs i ph)) s.workers hk (s.workers k) .out
          rw [upd_self] at hsw
          have := hs.outside i hi
          simp only [cover, runs_out, one_false] at this hsw ⊢
          omega)
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_, fun h => Nat.le_trans (hs.low h) (hl.mono h), ?_, fun j => ?_,
        hs.fitsStarter, fun j => ?_, hs.linked, hs.starter,
        fun r hr hre => Nat.le_trans (hs.seen r hr hre) (hl.mono _), hs.quiet⟩
      · have hsw' := total_swap (fun ph => one (runs i ph)) s.workers hk p' .out
        have := this.1 h i h1 h2
        simp only [cover, Option.getD, runs_out, one_false] at this hsw' ⊢
        omega
      · have hsw' := total_swap (fun ph => one (runs i ph)) s.workers hk p' .out
        have := this.2 i hi
        simp only [cover, Option.getD, runs_out, one_false] at this hsw' ⊢
        omega
      · have hsw := total_swap (fun ph => one (inside ph)) s.workers hk p' (s.workers k)
        rw [upd_self] at hsw
        simp only [hl.inside_from, hl.inside_to, one_true] at hsw
        dsimp only
        rw [hs.users]; omega
      · dsimp only; unfold upd; split
        · have := hl.inside_to
          exact ⟨fun e => by rw [e] at this; exact Bool.noConfusion this,
            fun e => by rw [e] at this; exact Bool.noConfusion this⟩
        · exact hs.workers j
      · dsimp only; unfold upd; split
        · rename_i hj; subst hj; exact hl.fits_next (hs.fitsWorkers _)
        · exact hs.fitsWorkers j
  | @leave k next' ran' hk hl =>
      have := hl.once ok (ok.wo_lt k) (hs.fitsWorkers k)
        (fun i => one (runs i s.starter) + total W fun j => one (runs i (upd s.workers k .out j))) hs.low
        (fun h i h1 h2 => by
          have hsw := total_swap (fun ph => one (runs i ph)) s.workers hk (s.workers k) .out
          rw [upd_self] at hsw
          have := hs.once h i h1 h2
          simp only [cover, runs_out, one_false] at this hsw ⊢
          omega)
        (fun i hi => by
          have hsw := total_swap (fun ph => one (runs i ph)) s.workers hk (s.workers k) .out
          rw [upd_self] at hsw
          have := hs.outside i hi
          simp only [cover, runs_out, one_false] at this hsw ⊢
          omega)
      obtain ⟨hn, hr, _⟩ := hl.done
      subst hn hr
      refine ⟨fun h i h1 h2 => ?_, fun i hi => ?_, hs.low, ?_, fun j => ?_, hs.fitsStarter, fun j => ?_, hs.linked,
        hs.starter, hs.seen, fun e => by show s.users - 1 = 0; rw [hs.quiet e]⟩
      · simpa [cover, runs, Nat.add_assoc] using this.1 h i h1 h2
      · simpa [cover, runs, Nat.add_assoc] using this.2 i hi
      · have hsw := total_swap (fun ph => one (inside ph)) s.workers hk .out (s.workers k)
        rw [upd_self] at hsw
        simp only [hl.inside_from, inside_out, one_true, one_false, Nat.add_zero] at hsw
        dsimp only
        rw [hs.users, ← hsw, Nat.add_sub_cancel]
      · dsimp only; unfold upd; split
        · exact ⟨nofun, nofun⟩
        · exact hs.workers j
      · dsimp only; unfold upd; split
        · trivial
        · exact hs.fitsWorkers j

theorem Inv.reach (ok : c.Ok n) {g W : Nat} {joins : Bool} {s : St} (h : Reach c g W joins (start c) s) :
    Inv n W c s := by
  induction h with
  | refl => exact Inv.initial n W c
  | tail _ hst ih => exact ih.step ok hst

/-! ## When the starter returns -/

theorem total_of_zero {f : Nat → Nat} {W : Nat} (h : ∀ j < W, f j = 0) : total W f = 0 := by
  rw [total_congr W (f' := fun _ => 0) h, total_nil]

/-- **No worker is inside a region that has returned**, so nothing a lane does can race with the
statement after the region. -/
theorem quiet_when_back (ok : c.Ok n) {g W : Nat} {joins : Bool} {s : St} (h : Reach c g W joins (start c) s)
    (hb : s.starter = .back) : ∀ k < W, s.workers k = .out := by
  have hi := Inv.reach ok h
  have hz := total_zero W (hi.users ▸ hi.quiet hb)
  intro k hk
  have hk' := hz k hk
  have hw := hi.workers k
  cases hwk : s.workers k <;> simp_all [inside]

/-- **Every index runs exactly once before the region returns**, and nothing at or past `n` runs. -/
theorem runs_once (ok : c.Ok n) {g W : Nat} {joins : Bool} {s : St} (h : Reach c g W joins (start c) s)
    (hb : s.starter = .back) : ∀ i, s.ran i = if i < n then 1 else 0 := by
  have hi := Inv.reach ok h
  have hout := quiet_when_back ok h hb
  have hcov : ∀ i, cover W s i = 0 := fun i => by
    simp only [cover, hb, runs_back, one_false, Nat.zero_add]
    exact total_of_zero fun k hk => by simp [hout k hk]
  intro i
  by_cases hn : i < n
  · obtain ⟨h0, hh, h1, h2⟩ := ok.home hn
    obtain ⟨r, hr, hso⟩ := ok.every h0 hh
    have used := hi.seen r hr (by rw [hb]; simp only [reached]; exact hr)
    rw [hso] at used
    have := hi.once h0 i h1 h2
    have hlt : i < s.next h0 := by omega
    rw [hcov] at this
    simp only [hlt, hn, ↓reduceIte] at this ⊢; omega
  · have := hi.outside i (by omega)
    rw [hcov] at this
    simp only [hn, ↓reduceIte]; omega

/-! ## A region cannot deadlock -/

/-- What is left to do in one lane: for a lane still going through its order, four for each home
it has not reached, and a little more for where it is within the home it is at. -/
def weight (H : Nat) : Phase → Nat
  | .out => 0
  | .back => 0
  | .settle => 1
  | .claim r => 2 + 4 * (H - r)
  | .check r => 3 + 4 * (H - r)
  | .run r _ _ => 4 + 4 * (H - r)

/-- What is left to do in the region: the indices not yet claimed in each home, and each lane's
distance from leaving. -/
def measure (W : Nat) (c : Cut) (s : St) : Nat :=
  5 * total c.H (fun h => c.bd (h + 1) - s.next h) + weight c.H s.starter + total W fun k => weight c.H (s.workers k)

theorem Lane.exists (h : inside p = true) : ∃ q next' ran', Lane c o g p next ran q next' ran' := by
  cases p with
  | check r =>
      rcases Nat.lt_or_ge r c.H with hr | hr
      · rcases Nat.lt_or_ge (next (o r)) (c.bd (o r + 1)) with hn | hn
        · exact ⟨_, _, _, .checkMore hr hn⟩
        · exact ⟨_, _, _, .checkSkip hr hn⟩
      · exact ⟨_, _, _, .checkDone hr⟩
  | claim r =>
      rcases Nat.lt_or_ge (next (o r)) (c.bd (o r + 1)) with hn | hn
      · exact ⟨_, _, _, .claimMore hn⟩
      · exact ⟨_, _, _, .claimDone hn⟩
  | run r b e => exact ⟨_, _, _, .run⟩
  | _ => exact Bool.noConfusion h

/-- Every step of a lane brings it nearer to leaving, or claims indices. -/
theorem Lane.decreases (hg : 0 < g) (ho : ∀ r, r < c.H → o r < c.H) (hf : fits c.H p)
    (h : Lane c o g p next ran q next' ran') :
    5 * total c.H (fun h => c.bd (h + 1) - next' h) + q.elim 1 (weight c.H) <
      5 * total c.H (fun h => c.bd (h + 1) - next h) + weight c.H p := by
  cases h with
  | checkMore hr _ => simp only [weight, Option.elim]; omega
  | checkSkip hr _ => simp only [weight, Option.elim]; omega
  | checkDone hr => simp only [weight, Option.elim]; omega
  | @claimMore r _ _ hlt =>
      have hr : r < c.H := hf
      have hlt' := total_lt (f := fun h => c.bd (h + 1) - next h) (f' := fun h => c.bd (h + 1) - bumpAt next (o r) g h)
        c.H (fun j _ => by have := bumpAt_ge next (o r) g j; omega) (o r) (ho r hr)
        (by unfold bumpAt; simp only [↓reduceIte]; omega)
      simp only [weight, Option.elim]; omega
  | @claimDone r _ _ hle =>
      have hr : r < c.H := hf
      have hle' := total_le (f := fun h => c.bd (h + 1) - next h) (f' := fun h => c.bd (h + 1) - bumpAt next (o r) g h)
        c.H (fun j _ => by have := bumpAt_ge next (o r) g j; omega)
      simp only [weight, Option.elim]; omega
  | run => simp only [weight, Option.elim]; omega

theorem Step.lift {g W : Nat} {s t : St} (h : Step c g W false s t) : Step c g W true s t := by
  cases h with
  | starter hl => exact .starter hl
  | unlink hl => exact .unlink hl
  | back h1 h2 => exact .back h1 h2
  | join hj => exact absurd hj nofun
  | worker hk hl => exact .worker hk hl
  | leave hk hl => exact .leave hk hl

theorem Reach.head {g W : Nat} {joins : Bool} {s t u : St} (h1 : Step c g W joins s t)
    (h2 : Reach c g W joins t u) : Reach c g W joins s u := by
  induction h2 with
  | refl => exact .tail (.refl s) h1
  | tail _ h ih => exact .tail ih h

theorem inside_somewhere (w : Nat → Phase) :
    ∀ W, (∃ k, k < W ∧ inside (w k) = true) ∨ ∀ k < W, inside (w k) = false
  | 0 => .inr fun k hk => absurd hk (Nat.not_lt_zero _)
  | W + 1 => by
      rcases inside_somewhere w W with ⟨k, hk, h⟩ | h
      · exact .inl ⟨k, by omega, h⟩
      · cases hw : inside (w W) with
        | true => exact .inl ⟨W, by omega, hw⟩
        | false =>
            refine .inr fun k hk => ?_
            rcases Nat.lt_or_ge k W with hk' | hk'
            · exact h k hk'
            · rw [show k = W by omega]; exact hw

/-- From any state short of return there is a step that no worker's joining takes, and it leaves
less to do: a worker inside moves, or else the starter does. -/
theorem step_down (ok : c.Ok n) (hg : 0 < g) {W : Nat} {s : St} (hi : Inv n W c s) (hb : s.starter ≠ .back) :
    ∃ t, Step c g W false s t ∧ measure W c t < measure W c s := by
  rcases inside_somewhere s.workers W with ⟨k, hk, hin⟩ | hnone
  · obtain ⟨q, next', ran', hl⟩ := Lane.exists (c := c) (o := c.wo k) (g := g) (next := s.next) (ran := s.ran) hin
    have hd := hl.decreases hg (ok.wo_lt k) (hi.fitsWorkers k)
    cases q with
    | some p' =>
        have hsw := total_swap (weight c.H) s.workers hk p' (s.workers k)
        rw [upd_self] at hsw
        refine ⟨_, .worker hk hl, ?_⟩
        simp only [measure, Option.elim] at hd ⊢
        omega
    | none =>
        have hsw := total_swap (weight c.H) s.workers hk .out (s.workers k)
        rw [upd_self] at hsw
        refine ⟨_, .leave hk hl, ?_⟩
        simp only [measure, Option.elim, weight] at hd hsw ⊢
        omega
  · cases hst : s.starter with
    | back => exact absurd hst hb
    | out => exact absurd hst hi.starter
    | settle =>
        have hu : s.users = 0 := by
          rw [hi.users]; exact total_of_zero fun k hk => by simp [hnone k hk]
        refine ⟨_, .back hst hu, ?_⟩
        simp only [measure, hst, weight]; omega
    | _ =>
        obtain ⟨q, next', ran', hl⟩ := Lane.exists (c := c) (o := c.so) (g := g) (next := s.next) (ran := s.ran)
          (p := s.starter) (by rw [hst]; rfl)
        have hd := hl.decreases hg ok.so_lt hi.fitsStarter
        cases q with
        | some p' =>
            refine ⟨_, .starter hl, ?_⟩
            simp only [measure, Option.elim] at hd ⊢
            omega
        | none =>
            refine ⟨_, .unlink hl, ?_⟩
            simp only [measure, Option.elim, weight] at hd ⊢
            omega

/-- **A region cannot deadlock.**  From every state a region reaches, it returns with the starter
and the workers already inside taking steps and no further worker joining.  A worker that is
blocked, busy or absent can delay a region, since the starter waits for those inside, but a
region never waits for a worker to arrive. -/
theorem finishes (ok : c.Ok n) (hg : 0 < g) {W : Nat} {s : St} (h : Reach c g W true (start c) s) :
    ∃ t, Reach c g W false s t ∧ t.starter = .back := by
  suffices ∀ m s, measure W c s < m → Reach c g W true (start c) s →
      ∃ t, Reach c g W false s t ∧ t.starter = .back
    from this _ s (Nat.lt_succ_self _) h
  intro m
  induction m with
  | zero => intro s hm; exact absurd hm (Nat.not_lt_zero _)
  | succ m ih =>
      intro s hm hr
      cases hst : s.starter with
      | back => exact ⟨s, .refl s, hst⟩
      | _ =>
          obtain ⟨t, hstep, hlt⟩ := step_down ok hg (Inv.reach ok hr) (by rw [hst]; exact nofun)
          obtain ⟨u, hu, hback⟩ := ih t (by omega) (.tail hr hstep.lift)
          exact ⟨u, Reach.head hstep hu, hback⟩

end Region
end Cairn
