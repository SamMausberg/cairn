/-
The lane pool's region protocol: what `cr::par::run` in `src/cairn/runtime/cairn_parallel.hpp` does
with a region of `n` lanes once it is wide enough to use the pool.  The ownership calculus assumes
that a region completes before the next statement; this file proves the protocol makes it so.

The thread that starts a region is its first lane.  Every lane, the starter and any pool worker
that joins, loads a shared counter and, while it is below `n`, bumps it by `grain` and runs the
indices it claimed.  When a lane finds nothing left the starter unlinks the region, after which no
worker can join, and waits until no worker is inside; a worker leaves.  A worker joins only a
linked region with work left (`Pool::adopt`).

The model interleaves atomic steps: a lane loading the counter, bumping it, running one claimed
chunk, a worker joining or leaving, the starter unlinking, and the starter returning.  It treats
every step as sequentially consistent.  The C++ bumps the counter with a relaxed `fetch_add`, which
is enough because the counter only hands out indices, and orders a leaving worker against the
waiting starter with the sequentially consistent pair in `leave` and `settle`.  That the C++
performs these steps is review, not proof.  A region below `lanes::CUTOFF`, or in a process with
one lane, is the plain loop on the starting thread and needs none of this.
-/

namespace Cairn
namespace Region

/-- Where one lane is. -/
inductive Phase where
  /-- A worker outside the region. -/
  | out
  /-- About to load the counter. -/
  | check
  /-- About to bump it. -/
  | claim
  /-- Running the claimed indices `[b, e)`. -/
  | run (b e : Nat)
  /-- The starter, after unlinking: waiting for the workers inside. -/
  | settle
  /-- The starter, returned. -/
  | back
deriving DecidableEq, Repr

/-- The shared state of one region and its lanes.  `ran i` counts how often index `i` has run. -/
structure St where
  next : Nat
  linked : Bool
  users : Nat
  starter : Phase
  workers : Nat → Phase
  ran : Nat → Nat

/-- Point update of the workers. -/
def upd (w : Nat → Phase) (k : Nat) (p : Phase) : Nat → Phase := fun j => if j = k then p else w j

/-- Running the chunk `[b, e)` once. -/
def bump (ran : Nat → Nat) (b e : Nat) : Nat → Nat := fun i => if b ≤ i then (if i < e then ran i + 1 else ran i) else ran i

/-- One step of a lane inside the region, the starter or a worker alike: load the counter, bump it,
or run the chunk.  `none` is finding nothing left. -/
inductive Lane (n g : Nat) : Phase → Nat → (Nat → Nat) → Option Phase → Nat → (Nat → Nat) → Prop where
  | checkMore {next ran} : next < n → Lane n g .check next ran (some .claim) next ran
  | checkDone {next ran} : n ≤ next → Lane n g .check next ran none next ran
  | claimMore {next ran} : next < n → Lane n g .claim next ran (some (.run next (min n (next + g)))) (next + g) ran
  | claimDone {next ran} : n ≤ next → Lane n g .claim next ran none (next + g) ran
  | run {b e next ran} : Lane n g (.run b e) next ran (some .check) next (bump ran b e)

/-- One step of the region with `W` workers.  `joins` says whether a worker may join; the region's
own semantics allows it, and `finishes` below shows that none needs to. -/
inductive Step (n g W : Nat) (joins : Bool) : St → St → Prop where
  | starter {s p next ran} : Lane n g s.starter s.next s.ran (some p) next ran →
      Step n g W joins s { s with starter := p, next := next, ran := ran }
  | unlink {s next ran} : Lane n g s.starter s.next s.ran none next ran →
      Step n g W joins s { s with starter := .settle, linked := false, next := next, ran := ran }
  | back {s} : s.starter = .settle → s.users = 0 → Step n g W joins s { s with starter := .back }
  | join {s k} : joins = true → k < W → s.workers k = .out → s.linked = true → s.next < n →
      Step n g W joins s { s with workers := upd s.workers k .check, users := s.users + 1 }
  | worker {s k p next ran} : k < W → Lane n g (s.workers k) s.next s.ran (some p) next ran →
      Step n g W joins s { s with workers := upd s.workers k p, next := next, ran := ran }
  | leave {s k next ran} : k < W → Lane n g (s.workers k) s.next s.ran none next ran →
      Step n g W joins s { s with workers := upd s.workers k .out, users := s.users - 1, next := next, ran := ran }

/-- Steps in sequence. -/
inductive Reach (n g W : Nat) (joins : Bool) : St → St → Prop where
  | refl (s : St) : Reach n g W joins s s
  | tail {s t u : St} : Reach n g W joins s t → Step n g W joins t u → Reach n g W joins s u

/-- A region just published: the counter at zero, the starter about to load it, no worker inside. -/
def start : St :=
  { next := 0, linked := true, users := 0, starter := .check, workers := fun _ => .out, ran := fun _ => 0 }

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

/-- A lane inside the region: loading, bumping or running. -/
def inside : Phase → Bool
  | .check => true
  | .claim => true
  | .run _ _ => true
  | _ => false

/-- The lane is running index `i` right now. -/
def runs (i : Nat) : Phase → Bool
  | .run b e => if b ≤ i then decide (i < e) else false
  | _ => false

/-- One for a Boolean that holds.  The conditions it counts are kept atomic, one comparison each,
because `omega` reaches for choice to use a negated conjunction. -/
def one (b : Bool) : Nat := if b then 1 else 0

@[simp] theorem one_true : one true = 1 := rfl
@[simp] theorem one_false : one false = 0 := rfl

@[simp] theorem inside_out : inside .out = false := rfl
@[simp] theorem inside_check : inside .check = true := rfl
@[simp] theorem runs_out (i : Nat) : runs i .out = false := rfl
@[simp] theorem runs_check (i : Nat) : runs i .check = false := rfl
@[simp] theorem runs_claim (i : Nat) : runs i .claim = false := rfl
@[simp] theorem runs_settle (i : Nat) : runs i .settle = false := rfl
@[simp] theorem runs_back (i : Nat) : runs i .back = false := rfl

/-- How many lanes are running index `i`. -/
def cover (W : Nat) (s : St) (i : Nat) : Nat :=
  one (runs i s.starter) + total W fun k => one (runs i (s.workers k))

/-! ## The invariant -/

/-- What holds of every state a region reaches.  `once` is the heart of it: an index below the
counter has run or is running in exactly one lane, and one at or above it has not been touched. -/
structure Inv (n W : Nat) (s : St) : Prop where
  once : ∀ i, s.ran i + cover W s i = if i < min s.next n then 1 else 0
  users : s.users = total W fun k => one (inside (s.workers k))
  workers : ∀ k, s.workers k ≠ .settle ∧ s.workers k ≠ .back
  linked : s.linked = inside s.starter
  starter : s.starter ≠ .out
  past : s.starter = .settle ∨ s.starter = .back → n ≤ s.next
  quiet : s.starter = .back → s.users = 0

theorem total_nil : ∀ W, total W (fun _ => 0) = 0
  | 0 => rfl
  | W + 1 => by simp [total, total_nil W]

theorem Inv.initial (n W : Nat) : Inv n W start where
  once i := by simp [Region.start, cover, one, runs, total_nil]
  users := by simp [Region.start, one, inside, total_nil]
  workers _ := ⟨nofun, nofun⟩
  linked := rfl
  starter := nofun
  past h := by rcases h with h | h <;> exact Phase.noConfusion h
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

variable {n g : Nat} {p : Phase} {next next' : Nat} {ran ran' : Nat → Nat} {q : Option Phase}

theorem one_runs (i b e : Nat) : one (runs i (.run b e)) = if b ≤ i then (if i < e then 1 else 0) else 0 := by
  unfold runs one
  by_cases h1 : b ≤ i <;> by_cases h2 : i < e <;> simp [h1, h2]

theorem Lane.inside_from (h : Lane n g p next ran q next' ran') : inside p = true := by cases h <;> rfl

theorem Lane.inside_to {p' : Phase} (h : Lane n g p next ran (some p') next' ran') : inside p' = true := by
  cases h <;> rfl

theorem Lane.mono (h : Lane n g p next ran q next' ran') : next ≤ next' := by cases h <;> omega

theorem Lane.done (h : Lane n g p next ran none next' ran') : n ≤ next' ∧ ran' = ran := by
  cases h <;> exact ⟨by omega, rfl⟩

/-- **The counter hands out each index once.**  A lane's step keeps every index below the counter
run or running in exactly one lane, and every index at or above it untouched. -/
theorem Lane.once (h : Lane n g p next ran q next' ran') (rest : Nat → Nat)
    (hi : ∀ i, ran i + (one (runs i p) + rest i) = if i < min next n then 1 else 0) :
    ∀ i, ran' i + (one (runs i (q.getD .out)) + rest i) = if i < min next' n then 1 else 0 := by
  intro i
  have hi := hi i
  cases h with
  | checkMore _ => simpa [runs, one] using hi
  | checkDone _ => simpa [runs, one] using hi
  | claimDone hle =>
      simp only [runs, one, Option.getD, Bool.false_eq_true, ↓reduceIte] at hi ⊢
      rw [show min (next + g) n = n by omega]; rw [show min next n = n by omega] at hi; exact hi
  | claimMore hlt =>
      simp only [Option.getD, runs_claim, one_false] at hi ⊢
      rw [one_runs]
      generalize hA : min next n = A at hi
      generalize hB : min (next + g) n = B
      generalize hC : min n (next + g) = C
      (repeat' split) <;> split at hi <;> omega
  | run =>
      simp only [Option.getD, runs_check, one_false, bump] at hi ⊢
      rw [one_runs] at hi
      generalize hA : min next n = A at hi ⊢
      (repeat' split) <;> (repeat' split at hi) <;> omega

/-! ## Every reachable state keeps the invariant -/

theorem Inv.step {W : Nat} {joins : Bool} {s t : St} (hs : Inv n W s) (h : Step n g W joins s t) :
    Inv n W t := by
  cases h with
  | @starter p' next' ran' hl =>
      refine ⟨fun i => ?_, hs.users, hs.workers, ?_, ?_, ?_, ?_⟩
      · have := hl.once (fun i => total W fun k => one (runs i (s.workers k))) (fun i => hs.once i) i
        simpa [cover] using this
      · show s.linked = inside p'
        rw [hl.inside_to, hs.linked, hl.inside_from]
      · intro e; dsimp only at e; have := hl.inside_to; rw [e] at this; exact Bool.noConfusion this
      · intro e; dsimp only at e; have := hl.inside_to
        rcases e with e | e <;> rw [e] at this <;> exact Bool.noConfusion this
      · intro e; dsimp only at e; have := hl.inside_to; rw [e] at this; exact Bool.noConfusion this
  | @unlink next' ran' hl =>
      refine ⟨fun i => ?_, hs.users, hs.workers, rfl, nofun, fun _ => hl.done.1, nofun⟩
      have := hl.once (fun i => total W fun k => one (runs i (s.workers k))) (fun i => hs.once i) i
      simpa [cover, runs] using this
  | back hset hu =>
      refine ⟨fun i => ?_, hs.users, hs.workers, ?_, nofun, fun _ => hs.past (.inl hset), fun _ => hu⟩
      · have := hs.once i; simpa [cover, runs, hset] using this
      · show s.linked = false; rw [hs.linked, hset]; rfl
  | @join k _ hk hout hlk _ =>
      have hsw := total_swap (fun ph => one (inside ph)) s.workers hk .check (s.workers k)
      rw [upd_self] at hsw
      simp only [hout, inside_out, inside_check, one_true, one_false] at hsw
      refine ⟨fun i => ?_, ?_, fun j => ?_, hs.linked, hs.starter, hs.past, fun hb => ?_⟩
      · have hr := total_swap (fun ph => one (runs i ph)) s.workers hk .check (s.workers k)
        rw [upd_self] at hr
        have := hs.once i
        simp only [cover, hout, runs_out, runs_check, one_false] at this hr ⊢
        omega
      · dsimp only
        rw [hs.users]; omega
      · dsimp only; unfold upd; split
        · exact ⟨nofun, nofun⟩
        · exact hs.workers j
      · have := hs.linked
        dsimp only at hb
        rw [hb, hlk] at this
        exact Bool.noConfusion this
  | @worker k p' next' ran' hk hl =>
      refine ⟨fun i => ?_, ?_, fun j => ?_, hs.linked, hs.starter, fun e => Nat.le_trans (hs.past e) hl.mono,
        hs.quiet⟩
      · have hsw' := total_swap (fun ph => one (runs i ph)) s.workers hk p' .out
        have := hl.once (fun i => one (runs i s.starter) + total W fun j => one (runs i (upd s.workers k .out j)))
          (fun i => by
            have hsw := total_swap (fun ph => one (runs i ph)) s.workers hk (s.workers k) .out
            rw [upd_self] at hsw
            have := hs.once i
            simp only [cover, runs_out, one_false] at this hsw ⊢
            omega) i
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
  | @leave k next' ran' hk hl =>
      refine ⟨fun i => ?_, ?_, fun j => ?_, hs.linked, hs.starter, fun e => Nat.le_trans (hs.past e) hl.mono,
        fun e => by have := hs.quiet e; show s.users - 1 = 0; omega⟩
      · have := hl.once (fun i => one (runs i s.starter) + total W fun j => one (runs i (upd s.workers k .out j)))
          (fun i => by
            have hsw := total_swap (fun ph => one (runs i ph)) s.workers hk (s.workers k) .out
            rw [upd_self] at hsw
            have := hs.once i
            simp only [cover, runs_out, one_false] at this hsw ⊢
            omega) i
        simpa [cover, runs, Nat.add_assoc] using this
      · have hsw := total_swap (fun ph => one (inside ph)) s.workers hk .out (s.workers k)
        rw [upd_self] at hsw
        simp only [hl.inside_from, inside_out, one_true, one_false] at hsw
        dsimp only
        rw [hs.users]; omega
      · dsimp only; unfold upd; split
        · exact ⟨nofun, nofun⟩
        · exact hs.workers j

theorem Inv.reach {W : Nat} {joins : Bool} {s : St} (h : Reach n g W joins start s) : Inv n W s := by
  induction h with
  | refl => exact Inv.initial n W
  | tail _ hst ih => exact ih.step hst

/-! ## When the starter returns -/

theorem total_of_zero {f : Nat → Nat} {W : Nat} (h : ∀ j < W, f j = 0) : total W f = 0 := by
  rw [total_congr W (f' := fun _ => 0) h, total_nil]

/-- **No worker is inside a region that has returned**, so nothing a lane does can race with the
statement after the region. -/
theorem quiet_when_back {W : Nat} {joins : Bool} {s : St} (h : Reach n g W joins start s)
    (hb : s.starter = .back) : ∀ k < W, s.workers k = .out := by
  have hi := Inv.reach h
  have hz := total_zero W (hi.users ▸ hi.quiet hb)
  intro k hk
  have hk' := hz k hk
  have hw := hi.workers k
  cases hwk : s.workers k <;> simp_all [inside]

/-- **Every lane runs exactly once before the region returns**, and nothing at or past `n` runs. -/
theorem runs_once {W : Nat} {joins : Bool} {s : St} (h : Reach n g W joins start s)
    (hb : s.starter = .back) : ∀ i, s.ran i = if i < n then 1 else 0 := by
  have hi := Inv.reach h
  have hout := quiet_when_back h hb
  intro i
  have hcov : cover W s i = 0 := by
    simp only [cover, hb, runs_back, one_false, Nat.zero_add]
    exact total_of_zero fun k hk => by simp [hout k hk]
  have := hi.once i
  rw [hcov, Nat.min_eq_right (hi.past (.inr hb))] at this
  exact this

/-! ## A region cannot deadlock -/

/-- What is left to do: indices not yet claimed, and each lane's distance from leaving. -/
def weight : Phase → Nat
  | .out => 0
  | .back => 0
  | .settle => 1
  | .claim => 2
  | .check => 3
  | .run _ _ => 4

def measure (n W : Nat) (s : St) : Nat :=
  5 * (n - min s.next n) + weight s.starter + total W fun k => weight (s.workers k)

theorem Lane.exists (h : inside p = true) : ∃ q next' ran', Lane n g p next ran q next' ran' := by
  cases p with
  | check => exact (Nat.lt_or_ge next n).elim (fun h => ⟨_, _, _, .checkMore h⟩) fun h => ⟨_, _, _, .checkDone h⟩
  | claim => exact (Nat.lt_or_ge next n).elim (fun h => ⟨_, _, _, .claimMore h⟩) fun h => ⟨_, _, _, .claimDone h⟩
  | run b e => exact ⟨_, _, _, .run⟩
  | _ => exact Bool.noConfusion h

/-- Every step of a lane brings it nearer to leaving, or claims indices. -/
theorem Lane.decreases (hg : 0 < g) (h : Lane n g p next ran q next' ran') :
    5 * (n - min next' n) + (q.elim 1 weight) < 5 * (n - min next n) + weight p := by
  cases h <;> simp only [weight, Option.elim] <;> omega

theorem Step.lift {W : Nat} {s t : St} (h : Step n g W false s t) : Step n g W true s t := by
  cases h with
  | starter hl => exact .starter hl
  | unlink hl => exact .unlink hl
  | back h1 h2 => exact .back h1 h2
  | join hj => exact absurd hj nofun
  | worker hk hl => exact .worker hk hl
  | leave hk hl => exact .leave hk hl

theorem Reach.head {W : Nat} {joins : Bool} {s t u : St} (h1 : Step n g W joins s t)
    (h2 : Reach n g W joins t u) : Reach n g W joins s u := by
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
theorem step_down (hg : 0 < g) {W : Nat} {s : St} (hi : Inv n W s) (hb : s.starter ≠ .back) :
    ∃ t, Step n g W false s t ∧ measure n W t < measure n W s := by
  rcases inside_somewhere s.workers W with ⟨k, hk, hin⟩ | hnone
  · obtain ⟨q, next', ran', hl⟩ := Lane.exists (n := n) (g := g) (next := s.next) (ran := s.ran) hin
    have hd := hl.decreases hg
    cases q with
    | some p' =>
        have hsw := total_swap weight s.workers hk p' (s.workers k)
        rw [upd_self] at hsw
        refine ⟨_, .worker hk hl, ?_⟩
        simp only [measure, Option.elim] at hd ⊢
        omega
    | none =>
        have hsw := total_swap weight s.workers hk .out (s.workers k)
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
        obtain ⟨q, next', ran', hl⟩ := Lane.exists (n := n) (g := g) (next := s.next) (ran := s.ran)
          (p := s.starter) (by rw [hst]; rfl)
        have hd := hl.decreases hg
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
theorem finishes (hg : 0 < g) {W : Nat} {s : St} (h : Reach n g W true start s) :
    ∃ t, Reach n g W false s t ∧ t.starter = .back := by
  suffices ∀ m s, measure n W s < m → Reach n g W true start s → ∃ t, Reach n g W false s t ∧ t.starter = .back
    from this _ s (Nat.lt_succ_self _) h
  intro m
  induction m with
  | zero => intro s hm; exact absurd hm (Nat.not_lt_zero _)
  | succ m ih =>
      intro s hm hr
      cases hst : s.starter with
      | back => exact ⟨s, .refl s, hst⟩
      | _ =>
          obtain ⟨t, hstep, hlt⟩ := step_down hg (Inv.reach hr) (by rw [hst]; exact nofun)
          obtain ⟨u, hu, hback⟩ := ih t (by omega) (.tail hr hstep.lift)
          exact ⟨u, Reach.head hstep hu, hback⟩

end Region
end Cairn
