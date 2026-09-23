/-
The phase rule of a cooperative region, `src/cairn/compiler/phases.py`: between two barriers, no two threads of one
block touch one element of a shared array where either of them writes it.

A block runs `n` threads.  Every thread runs the same phases, a barrier between each two, and within a phase the
threads' steps interleave in any order.  Part one takes a phase as given, one list of steps per thread, and proves
that when `accepts` holds no two threads are ever about to take clashing steps, and every interleaving that runs
every thread to its end leaves one memory and one set of registers: the ones each thread would leave running alone.
So a block of accepted phases has one result, whatever order its threads run in.

Part two is the source the checker reads, cut down to what decides the rule: reads and writes of one element at an
index computed from the thread's number and loop counters, `if` on the thread's number, loops of a known count and
barriers.  `phasesOf` runs it for one thread, as `phases.py` runs the body for every thread, and `program` accepts it
when no barrier stands under an `if` and every phase is accepted.  `tools/checks/differential_cooperative.py`
requires `program` and the Python checker to decide generated programs alike; that ties this model to the Python on
samples, and proves nothing about the Python itself.
-/

namespace Cairn
namespace Cooperative

/-! ### Phases, steps and interleavings -/

/-- An element: which array, and which index in it. -/
abbrev Loc := Nat × Nat

/-- One step of a thread: load an element into one of its registers, or store into an element a value computed from
its registers. -/
inductive Op where
  | load (r : Nat) (l : Loc)
  | store (l : Loc) (f : (Nat → Int) → Int)

def Op.loc : Op → Loc
  | .load _ l => l
  | .store l _ => l

def Op.writes : Op → Bool
  | .load _ _ => false
  | .store _ _ => true

/-- Two steps of two threads clash: one element, and one of them writes it. -/
def clash (a b : Op) : Bool := decide (a.loc = b.loc) && (a.writes || b.writes)

/-- One phase: what each thread runs between two barriers. -/
abbrev Phase := Nat → List Op

/-- The rule for the `n` threads of a block: no step of one thread clashes with a step of another. -/
def accepts (n : Nat) (ph : Phase) : Bool :=
  (List.range n).all fun t => (List.range n).all fun u =>
    t == u || (ph t).all fun a => (ph u).all fun b => !clash a b

/-- The state of a block within a phase: memory, every thread's registers, and how far each thread has run. -/
structure St where
  mem : Loc → Int
  regs : Nat → Nat → Int
  pc : Nat → Nat

/-- What one step does to the block, taken by thread `t`. -/
def Op.apply (t : Nat) (s : St) : Op → St
  | .load r l => { s with regs := fun u q => if u = t ∧ q = r then s.mem l else s.regs u q }
  | .store l f => { s with mem := fun m => if m = l then f (s.regs t) else s.mem m }

/-- Thread `t` takes its next step, if it has one. -/
def step (ph : Phase) (s : St) (t : Nat) : St :=
  match (ph t)[s.pc t]? with
  | some op => { op.apply t s with pc := fun u => if u = t then s.pc t + 1 else s.pc u }
  | none => s

/-- An interleaving: the threads in the order they take steps. -/
def run (ph : Phase) (s : St) (σ : List Nat) : St := σ.foldl (step ph) s

/-- Every thread of the block has run all of its steps. -/
def done (n : Nat) (ph : Phase) (s : St) : Prop := ∀ t, t < n → s.pc t = (ph t).length

/-- What one thread's steps do to its own registers and its own view of memory, if it ran alone. -/
def Op.alone : (Loc → Int) × (Nat → Int) → Op → (Loc → Int) × (Nat → Int)
  | (m, r), .load q l => (m, fun p => if p = q then m l else r p)
  | (m, r), .store l f => (fun x => if x = l then f r else m x, r)

/-- Thread `t` alone, from the phase's start, after its first `k` steps. -/
def solo (ph : Phase) (s₀ : St) (t k : Nat) : (Loc → Int) × (Nat → Int) :=
  ((ph t).take k).foldl Op.alone (s₀.mem, s₀.regs t)

/-- Some step of `ops` writes element `l`. -/
def writesAt (ops : List Op) (l : Loc) : Bool := ops.any fun op => op.writes && decide (op.loc = l)

/-! #### What acceptance gives -/

theorem accepts_pair {n : Nat} {ph : Phase} (h : accepts n ph = true) {t u : Nat} (ht : t < n) (hu : u < n)
    (hne : t ≠ u) {a b : Op} (ha : a ∈ ph t) (hb : b ∈ ph u) : clash a b = false := by
  simp only [accepts, List.all_eq_true, List.mem_range] at h
  have := h t ht u hu
  simp only [Bool.or_eq_true, beq_iff_eq, List.all_eq_true, Bool.not_eq_eq_eq_not, Bool.not_true] at this
  rcases this with h1 | h1
  · exact absurd h1 hne
  · exact h1 a ha b hb

theorem writesAt_iff {ops : List Op} {l : Loc} :
    writesAt ops l = true ↔ ∃ op ∈ ops, op.writes = true ∧ op.loc = l := by
  simp [writesAt]

/-- Two threads never write one element of an accepted phase. -/
theorem one_writer {n : Nat} {ph : Phase} (h : accepts n ph = true) {t u : Nat} (ht : t < n) (hu : u < n) {l : Loc}
    (hwt : writesAt (ph t) l = true) (hwu : writesAt (ph u) l = true) : t = u := by
  refine Decidable.byContradiction fun hne => ?_
  obtain ⟨a, ha, haw, hal⟩ := writesAt_iff.mp hwt
  obtain ⟨b, hb, _, hbl⟩ := writesAt_iff.mp hwu
  have := accepts_pair h ht hu hne ha hb
  simp [clash, hal, hbl, haw] at this

/-- A thread never reads an element another thread of an accepted phase writes. -/
theorem no_foreign_read {n : Nat} {ph : Phase} (h : accepts n ph = true) {t u : Nat} (ht : t < n) (hu : u < n)
    {a : Op} (ha : a ∈ ph t) (hwu : writesAt (ph u) a.loc = true) : t = u := by
  refine Decidable.byContradiction fun hne => ?_
  obtain ⟨b, hb, hbw, hbl⟩ := writesAt_iff.mp hwu
  have := accepts_pair h ht hu hne ha hb
  simp [clash, hbl, hbw] at this

/-- **No race.**  In any state of an accepted phase, the next steps of two different threads never clash. -/
theorem no_race {n : Nat} {ph : Phase} (h : accepts n ph = true) (s : St) {t u : Nat} (ht : t < n) (hu : u < n)
    (hne : t ≠ u) {a b : Op} (ha : (ph t)[s.pc t]? = some a) (hb : (ph u)[s.pc u]? = some b) :
    clash a b = false :=
  accepts_pair h ht hu hne (List.mem_of_getElem? ha) (List.mem_of_getElem? hb)

/-! #### A thread alone -/

theorem alone_keeps {ops : List Op} {l : Loc} (hw : writesAt ops l = false) :
    ∀ (m : Loc → Int) (r : Nat → Int), (ops.foldl Op.alone (m, r)).1 l = m l := by
  induction ops with
  | nil => intro m r; rfl
  | cons op rest ih =>
    intro m r
    have hop : ¬ (op.writes = true ∧ op.loc = l) := by
      intro hc; simp [writesAt, hc.1, hc.2] at hw
    have hrest : writesAt rest l = false := by
      simp only [writesAt, List.any_cons, Bool.or_eq_false_iff] at hw; exact hw.2
    cases op with
    | load q l' => simp only [List.foldl_cons, Op.alone]; exact ih hrest m _
    | store l' f =>
      simp only [List.foldl_cons, Op.alone]
      rw [ih hrest]
      have : l ≠ l' := by intro he; exact hop ⟨rfl, he.symm⟩
      simp [this]

theorem writesAt_take {ops : List Op} {l : Loc} (h : writesAt ops l = false) (k : Nat) :
    writesAt (ops.take k) l = false := by
  simp only [writesAt, List.any_eq_false] at h ⊢
  intro x hx
  exact h x (List.mem_of_mem_take hx)

theorem solo_succ {ph : Phase} {s₀ : St} {t k : Nat} {op : Op} (hk : (ph t)[k]? = some op) :
    solo ph s₀ t (k + 1) = Op.alone (solo ph s₀ t k) op := by
  unfold solo
  rw [List.take_add_one, hk]
  simp [List.foldl_append]

/-- Some thread of the block writes `l`, or none does: decided by looking at every thread's steps. -/
theorem writer_or_none (n : Nat) (ph : Phase) (l : Loc) :
    (∃ t, t < n ∧ writesAt (ph t) l = true) ∨ (∀ t, t < n → writesAt (ph t) l = false) := by
  cases hany : (List.range n).any fun t => writesAt (ph t) l
  · right
    intro t ht
    simp only [List.any_eq_false, List.mem_range] at hany
    simpa using hany t ht
  · left
    simp only [List.any_eq_true, List.mem_range] at hany
    exact hany

/-! #### The invariant every interleaving keeps -/

/-- What every state of an interleaving of an accepted phase satisfies: each thread's registers are the ones it
would have alone after as many steps, an element some thread writes holds what that thread alone would have put
there, and an element nobody writes holds what it held when the phase began. -/
structure Inv (n : Nat) (ph : Phase) (s₀ s : St) : Prop where
  regs : ∀ t, t < n → s.regs t = (solo ph s₀ t (s.pc t)).2
  owned : ∀ t, t < n → ∀ l, writesAt (ph t) l = true → s.mem l = (solo ph s₀ t (s.pc t)).1 l
  kept : ∀ l, (∀ t, t < n → writesAt (ph t) l = false) → s.mem l = s₀.mem l

theorem inv_start (n : Nat) (ph : Phase) (s₀ : St) (hpc : ∀ t, s₀.pc t = 0) : Inv n ph s₀ s₀ where
  regs := by intro t _; simp [solo, hpc]
  owned := by intro t _ l _; simp [solo, hpc]
  kept := by intro l _; rfl

theorem inv_step {n : Nat} {ph : Phase} (h : accepts n ph = true) {s₀ s : St} (hi : Inv n ph s₀ s) {t : Nat}
    (ht : t < n) : Inv n ph s₀ (step ph s t) := by
  unfold step
  cases hop : (ph t)[s.pc t]? with
  | none => exact hi
  | some op =>
    have hmem : op ∈ ph t := List.mem_of_getElem? hop
    have hsucc := solo_succ (s₀ := s₀) hop
    cases op with
    | load q l =>
      -- what thread t reads is what it would read alone
      have hread : s.mem l = (solo ph s₀ t (s.pc t)).1 l := by
        rcases writer_or_none n ph l with ⟨u, hu, hwu⟩ | hnone
        · have htu : t = u := no_foreign_read h ht hu hmem hwu
          subst htu
          exact hi.owned t ht l hwu
        · rw [hi.kept l hnone]
          unfold solo
          rw [alone_keeps (writesAt_take (hnone t ht) _)]
      constructor
      · intro u hu
        by_cases hut : u = t
        · subst hut
          simp only [Op.apply, ↓reduceIte, hsucc]
          funext p
          simp only [Op.alone]
          by_cases hp : p = q
          · simp [hp, hread]
          · simp [hp, hi.regs u hu]
        · simp only [Op.apply, hut, false_and, ↓reduceIte]
          exact hi.regs u hu
      · intro u hu l' hwl'
        by_cases hut : u = t
        · subst hut
          simp only [Op.apply, ↓reduceIte, hsucc, Op.alone]
          exact hi.owned u hu l' hwl'
        · simp only [Op.apply, hut, ↓reduceIte]
          exact hi.owned u hu l' hwl'
      · intro l' hn
        exact hi.kept l' hn
    | store l f =>
      have hwt : writesAt (ph t) l = true := writesAt_iff.mpr ⟨_, hmem, rfl, rfl⟩
      constructor
      · intro u hu
        by_cases hut : u = t
        · subst hut
          simp only [Op.apply, ↓reduceIte, hsucc, Op.alone]
          exact hi.regs u hu
        · simp only [Op.apply, hut, ↓reduceIte]
          exact hi.regs u hu
      · intro u hu l' hwl'
        by_cases hut : u = t
        · subst hut
          simp only [Op.apply, ↓reduceIte, hsucc, Op.alone]
          by_cases hl : l' = l
          · simp [hl, hi.regs u hu]
          · simp [hl, hi.owned u hu l' hwl']
        · have hl : l' ≠ l := by
            intro he
            subst he
            exact hut (one_writer h hu ht hwl' hwt)
          simp only [Op.apply, hut, ↓reduceIte, hl]
          exact hi.owned u hu l' hwl'
      · intro l' hn
        have hl : l' ≠ l := by
          intro he
          subst he
          simp [hn t ht] at hwt
        simp only [Op.apply, hl, ↓reduceIte]
        exact hi.kept l' hn

theorem inv_run {n : Nat} {ph : Phase} (h : accepts n ph = true) {s₀ : St} :
    ∀ (σ : List Nat) (s : St), (∀ t ∈ σ, t < n) → Inv n ph s₀ s → Inv n ph s₀ (run ph s σ) := by
  intro σ
  induction σ with
  | nil => intro s _ hi; exact hi
  | cons t rest ih =>
    intro s hσ hi
    simp only [run, List.foldl_cons]
    exact ih _ (fun u hu => hσ u (List.mem_cons_of_mem _ hu)) (inv_step h hi (hσ t List.mem_cons_self))

/-! #### One result -/

/-- The memory an accepted phase leaves: an element some thread writes holds what that thread alone leaves there,
and any other holds what it held. -/
theorem result_mem {n : Nat} {ph : Phase} {s₀ s : St} (hi : Inv n ph s₀ s)
    (hd : done n ph s) (l : Loc) :
    (∃ t, t < n ∧ writesAt (ph t) l = true ∧ s.mem l = (solo ph s₀ t (ph t).length).1 l) ∨
    ((∀ t, t < n → writesAt (ph t) l = false) ∧ s.mem l = s₀.mem l) := by
  rcases writer_or_none n ph l with ⟨t, ht, hwt⟩ | hnone
  · left
    refine ⟨t, ht, hwt, ?_⟩
    rw [hi.owned t ht l hwt, hd t ht]
  · right
    exact ⟨hnone, hi.kept l hnone⟩

/-- **One result.**  Any two interleavings of an accepted phase that run every thread to its end, from the same
start, leave the same memory and the same registers in every thread. -/
theorem deterministic {n : Nat} {ph : Phase} (h : accepts n ph = true) (s₀ : St) (hpc : ∀ t, s₀.pc t = 0)
    (σ τ : List Nat) (hσ : ∀ t ∈ σ, t < n) (hτ : ∀ t ∈ τ, t < n)
    (dσ : done n ph (run ph s₀ σ)) (dτ : done n ph (run ph s₀ τ)) :
    (run ph s₀ σ).mem = (run ph s₀ τ).mem ∧ ∀ t, t < n → (run ph s₀ σ).regs t = (run ph s₀ τ).regs t := by
  have iσ := inv_run h σ s₀ hσ (inv_start n ph s₀ hpc)
  have iτ := inv_run h τ s₀ hτ (inv_start n ph s₀ hpc)
  constructor
  · funext l
    rcases result_mem iσ dσ l with ⟨t, ht, hwt, eσ⟩ | ⟨hn, eσ⟩
    · rcases result_mem iτ dτ l with ⟨u, hu, hwu, eτ⟩ | ⟨hn', _⟩
      · have := one_writer h ht hu hwt hwu
        subst this
        rw [eσ, eτ]
      · simp [hn' t ht] at hwt
    · rcases result_mem iτ dτ l with ⟨u, hu, hwu, _⟩ | ⟨_, eτ⟩
      · simp [hn u hu] at hwu
      · rw [eσ, eτ]
  · intro t ht
    rw [iσ.regs t ht, iτ.regs t ht, dσ t ht, dτ t ht]

/-! #### A block: phases one after another -/

/-- A block runs its phases in turn: every thread finishes one before any starts the next, which is what a barrier
does, and each begins with every thread at its first step. -/
def reset (s : St) : St := { s with pc := fun _ => 0 }

def block (phs : List Phase) (σs : List (List Nat)) (s : St) : St :=
  (phs.zip σs).foldl (fun s (ph, σ) => run ph (reset s) σ) s

/-- Each phase's interleaving is of the block's threads and runs every one of them to its end. -/
def complete (n : Nat) : List Phase → List (List Nat) → St → Prop
  | ph :: phs, σ :: σs, s => (∀ t ∈ σ, t < n) ∧ done n ph (run ph (reset s) σ) ∧ complete n phs σs (run ph (reset s) σ)
  | [], [], _ => True
  | _, _, _ => False

/-- **A block has one result.**  When the rule accepts every phase, any two ways of running the block's phases, each
phase interleaved as it may be and run to its end, leave the same memory and the same registers. -/
theorem block_deterministic {n : Nat} :
    ∀ (phs : List Phase), (∀ ph ∈ phs, accepts n ph = true) → ∀ (σs τs : List (List Nat)) (s s' : St),
    s.mem = s'.mem → (∀ t, t < n → s.regs t = s'.regs t) →
    complete n phs σs s → complete n phs τs s' →
    (block phs σs s).mem = (block phs τs s').mem ∧
      ∀ t, t < n → (block phs σs s).regs t = (block phs τs s').regs t := by
  intro phs
  induction phs with
  | nil =>
    intro _ σs τs s s' hm hr cσ cτ
    cases σs <;> cases τs <;> simp_all [complete, block]
  | cons ph rest ih =>
    intro hacc σs τs s s' hm hr cσ cτ
    cases σs with
    | nil => simp [complete] at cσ
    | cons σ σs =>
      cases τs with
      | nil => simp [complete] at cτ
      | cons τ τs =>
        obtain ⟨hσ, dσ, cσ'⟩ := cσ
        obtain ⟨hτ, dτ, cτ'⟩ := cτ
        have hph := hacc ph List.mem_cons_self
        -- the phase from two starts that agree on memory and on the block's registers
        have agree : (run ph (reset s) σ).mem = (run ph (reset s') τ).mem ∧
            ∀ t, t < n → (run ph (reset s) σ).regs t = (run ph (reset s') τ).regs t := by
          have iσ := inv_run hph σ (reset s) hσ (inv_start n ph (reset s) (fun _ => rfl))
          have iτ := inv_run hph τ (reset s') hτ (inv_start n ph (reset s') (fun _ => rfl))
          have hsolo : ∀ t, t < n → solo ph (reset s) t (ph t).length = solo ph (reset s') t (ph t).length := by
            intro t ht
            simp only [solo, reset]
            rw [hm, hr t ht]
          constructor
          · funext l
            rcases result_mem iσ dσ l with ⟨t, ht, hwt, eσ⟩ | ⟨hn, eσ⟩
            · rcases result_mem iτ dτ l with ⟨u, hu, hwu, eτ⟩ | ⟨hn', _⟩
              · have := one_writer hph ht hu hwt hwu
                subst this
                rw [eσ, eτ, hsolo t ht]
              · simp [hn' t ht] at hwt
            · rcases result_mem iτ dτ l with ⟨u, hu, hwu, _⟩ | ⟨_, eτ⟩
              · simp [hn u hu] at hwu
              · rw [eσ, eτ]
                simp [reset, hm]
          · intro t ht
            rw [iσ.regs t ht, iτ.regs t ht, dσ t ht, dτ t ht, hsolo t ht]
        have := ih (fun p hp => hacc p (List.mem_cons_of_mem _ hp)) σs τs _ _ agree.1 agree.2 cσ' cτ'
        simpa [block, List.zip_cons_cons, List.foldl_cons] using this

/-! ### The source the checker reads, cut down -/

/-- A usize index: the thread's number, a literal, a loop counter (0 the innermost), and the operators; `none` where
the program's own guard would trap, as `phases.py` computes it. -/
inductive E where
  | tid
  | lit (k : Nat)
  | var (i : Nat)
  | add (a b : E)
  | sub (a b : E)
  | mul (a b : E)
  | div (a b : E)
  | mod (a b : E)
  | xor (a b : E)
deriving Repr

def LIMIT : Nat := 18446744073709551616

def E.eval (t : Nat) (vs : List Nat) : E → Option Nat
  | .tid => some t
  | .lit k => some k
  | .var i => vs[i]?
  | .add a b => (a.eval t vs).bind fun x => (b.eval t vs).bind fun y => if x + y < LIMIT then some (x + y) else none
  | .sub a b => (a.eval t vs).bind fun x => (b.eval t vs).bind fun y => if y ≤ x then some (x - y) else none
  | .mul a b => (a.eval t vs).bind fun x => (b.eval t vs).bind fun y => if x * y < LIMIT then some (x * y) else none
  | .div a b => (a.eval t vs).bind fun x => (b.eval t vs).bind fun y => if y = 0 then none else some (x / y)
  | .mod a b => (a.eval t vs).bind fun x => (b.eval t vs).bind fun y => if y = 0 then none else some (x % y)
  | .xor a b => (a.eval t vs).bind fun x => (b.eval t vs).map fun y => x ^^^ y

/-- A condition on the thread's number; `none` where it traps, and then neither arm runs. -/
inductive C where
  | lt (a b : E)
  | eq (a b : E)
  | and (a b : C)
  | not (a : C)
deriving Repr

def C.eval (t : Nat) (vs : List Nat) : C → Option Bool
  | .lt a b => (a.eval t vs).bind fun x => (b.eval t vs).map fun y => decide (x < y)
  | .eq a b => (a.eval t vs).bind fun x => (b.eval t vs).map fun y => decide (x = y)
  | .and a b => (a.eval t vs).bind fun x => if x then b.eval t vs else some false
  | .not a => (a.eval t vs).map (!·)

/-- A statement: read or write one element of array `arr`, a barrier, an `if` on a condition, a loop of `count`. -/
inductive S where
  | read (arr : Nat) (i : E)
  | write (arr : Nat) (i : E)
  | barrier
  | when (c : C) (body : List S)
  | loop (count : Nat) (body : List S)
deriving Repr

/-- One access of thread `t`, or none where its index traps or passes the array's end: then the guard aborts the
thread before it touches anything. -/
def access (sizes : List Nat) (arr : Nat) (i : Option Nat) (write : Bool) : List Op :=
  match i with
  | some k => if k < sizes.getD arr 0 then [if write then .store (arr, k) (fun _ => 0) else .load 0 (arr, k)] else []
  | none => []

mutual
/-- Thread `t` runs a statement: the phases it has finished, newest first, and the one it is in. -/
def S.flat (sizes : List Nat) (t : Nat) (vs : List Nat) : S → List (List Op) × List Op → List (List Op) × List Op
  | .read arr i, (done, cur) => (done, cur ++ access sizes arr (i.eval t vs) false)
  | .write arr i, (done, cur) => (done, cur ++ access sizes arr (i.eval t vs) true)
  | .barrier, (done, cur) => (cur :: done, [])
  | .when c body, acc => if c.eval t vs = some true then flatAll sizes t vs body acc else acc
  | .loop count body, acc => (List.range count).foldl (fun a k => flatAll sizes t (k :: vs) body a) acc

def flatAll (sizes : List Nat) (t : Nat) (vs : List Nat) : List S → List (List Op) × List Op → List (List Op) × List Op
  | [], acc => acc
  | s :: rest, acc => flatAll sizes t vs rest (s.flat sizes t vs acc)
end

/-- The phases thread `t` runs, first to last. -/
def phasesOf (sizes : List Nat) (body : List S) (t : Nat) : List (List Op) :=
  let (done, cur) := flatAll sizes t [] body ([], [])
  (cur :: done).reverse

mutual
/-- A barrier under an `if`, which not every thread may reach: the checker refuses it (E-COOP-BARRIER). -/
def S.divergent : Bool → S → Bool
  | under, .barrier => under
  | _, .when _ body => divergentAll true body
  | under, .loop _ body => divergentAll under body
  | _, _ => false

def divergentAll (under : Bool) : List S → Bool
  | [] => false
  | s :: rest => s.divergent under || divergentAll under rest
end

/-- The rule on source: every barrier outside every `if`, so every thread runs the same phases, and every phase
accepted. -/
def program (n : Nat) (sizes : List Nat) (body : List S) : Bool :=
  !divergentAll false body &&
    let count := (phasesOf sizes body 0).length
    (List.range count).all fun p => accepts n fun t => (phasesOf sizes body t).getD p []

end Cooperative
end Cairn

namespace Cairn
namespace Cooperative

/-! ### One example of each refusal, and one acceptance

Four threads and one array of four, decided by the kernel. -/

/-- Each thread writes its element, then reads the one its mirror wrote, with no barrier between: refused, as
E-COOP-UNORDERED refuses it. -/
theorem omitted_barrier_refused :
    program 4 [4] [.write 0 .tid, .read 0 (.sub (.lit 3) .tid)] = false := by decide

/-- The same with a barrier between: accepted. -/
theorem barrier_orders :
    program 4 [4] [.write 0 .tid, .barrier, .read 0 (.sub (.lit 3) .tid)] = true := by decide

/-- Threads 2k and 2k + 1 write element k: refused, as E-COOP-CONFLICT refuses it. -/
theorem conflict_refused : program 4 [4] [.write 0 (.div .tid (.lit 2))] = false := by decide

/-- A thread rewrites its element while its neighbour may still be reading it: refused, as E-COOP-REUSE refuses it. -/
theorem reuse_refused :
    program 4 [4] [.read 0 (.mod (.add .tid (.lit 1)) (.lit 4)), .write 0 .tid] = false := by decide

/-- A barrier only some threads reach: refused, as E-COOP-BARRIER refuses it. -/
theorem divergent_barrier_refused : program 4 [4] [.when (.lt .tid (.lit 2)) [.barrier]] = false := by decide

end Cooperative
end Cairn
