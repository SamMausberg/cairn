/-
The layout rules of `src/cairn/compiler/layout_algebra.py`: a declared storage layout gives every element its own
offset, and a declared spread gives every element of its tile exactly one (participant, value) holder.

A storage layout is transliterated from the Python: each dimension is a list of modes, an extent and a stride,
and a coordinate's digits in its modes, fastest first, times their strides sum to an offset, which a swizzle then
permutes.  A spread's modes carry a stride for every coordinate of its tile, added to its origin, and with `wrap`
each coordinate is reduced modulo the tile.  Here every list of dimensions is written with the fastest dimension first, the reverse
of the Python's row-major order, so an element's number is `c + C * r` either way.  The checks are written as
their definitions, by counting holders element by element; the Python counts in one pass instead, and
`tools/checks/differential_layouts.py` requires the two to give generated layouts the same verdict, the first
element left to nobody, the first held twice and the first two elements that share an offset included.

The theorems say what a layout that passes promises to code that writes through it.  Every element of a spread's
tile has one holder and one only, so a phase in which every participant writes each of its values writes every
element once; and with a storage layout that passes its own rule, no two holders write one offset.  That the
lowering computes `D.at(t, v)` as `offset (coords t v)` is review of `layouts.lower`, not proof.
-/

namespace Cairn
namespace Layout

/-! ### Storage layouts -/

/-- What a coordinate adds through its modes: its digits, fastest first, each times its mode's stride. -/
def place (x : Nat) : List (Nat × Nat) → Nat
  | [] => 0
  | (e, s) :: rest => (x % e) * s + place (x / e) rest

/-- A product of extents. -/
def size : List Nat → Nat
  | [] => 1
  | n :: ns => n * size ns

/-- One dimension's extent: the product of its modes' extents. -/
def extentOf (modes : List (Nat × Nat)) : Nat := size (modes.map Prod.fst)

/-- CuTe's `Swizzle<B, M, S>`: bits `M + S` to `M + S + B` of an offset flip bits `M` to `M + B`. -/
def swizzled (o : Nat) : Nat × Nat × Nat → Nat
  | (b, m, s) => if b = 0 then o else o ^^^ ((o >>> s) &&& (((1 <<< b) - 1) <<< m))

/-- A storage layout: each dimension's modes, the fastest dimension first, and its swizzle. -/
structure Storage where
  dims : List (List (Nat × Nat))
  swizzle : Nat × Nat × Nat

def Storage.shape (L : Storage) : List Nat := L.dims.map extentOf

/-- The sum each coordinate adds through its own dimension's modes. -/
def placeAll : List Nat → List (List (Nat × Nat)) → Nat
  | x :: xs, modes :: rest => place x modes + placeAll xs rest
  | _, _ => 0

def Storage.offset (L : Storage) (coords : List Nat) : Nat := swizzled (placeAll coords L.dims) L.swizzle

/-- The coordinate of element number `e`, fastest dimension first. -/
def unravel (e : Nat) : List Nat → List Nat
  | [] => []
  | n :: ns => (e % n) :: unravel (e / n) ns

/-- The number of a coordinate: `number [c, r] [C, R] = c + C * r`, as the Python counts row by row. -/
def number : List Nat → List Nat → Nat
  | a :: as, n :: ns => a + n * number as ns
  | _, _ => 0

/-- Every coordinate below its extent, and one for each dimension. -/
def inside : List Nat → List Nat → Bool
  | [], [] => true
  | a :: as, n :: ns => decide (a < n) && inside as ns
  | _, _ => false

/-- The offset of element number `e`. -/
def Storage.at (L : Storage) (e : Nat) : Nat := L.offset (unravel e L.shape)

/-- Whether an element shares its offset with an earlier one. -/
def Storage.shares (L : Storage) (e : Nat) : Bool := (List.range e).any (fun a => L.at a == L.at e)

/-- The first element that shares its offset with an earlier one, with the earliest it shares it with: what
`injective` in layout_algebra.py reports, or `none` when every element has an offset of its own. -/
def Storage.clash (L : Storage) : Option (Nat × Nat) :=
  match (List.range (size L.shape)).find? L.shares with
  | none => none
  | some e => ((List.range e).find? (fun a => L.at a == L.at e)).map (fun a => (a, e))

/-! ### Spreads -/

/-- Add the steps of a spread's modes, each times its digit of `x`, to the coordinate `acc`. -/
def placeVec (x : Nat) : List (Nat × List Nat) → List Nat → List Nat
  | [], acc => acc
  | (e, s) :: rest, acc => placeVec (x / e) rest (List.zipWith (· + ·) acc (s.map (· * (x % e))))

structure Spread where
  tile : Storage
  participants : List (Nat × List Nat)
  values : List (Nat × List Nat)
  wrap : Bool
  /-- Where participant 0's value 0 sits. -/
  origin : List Nat

def Spread.count (d : Spread) : Nat := size (d.participants.map Prod.fst)
def Spread.each (d : Spread) : Nat := size (d.values.map Prod.fst)

/-- The coordinate participant `t`'s value `v` names. -/
def Spread.coords (d : Spread) (t v : Nat) : List Nat :=
  let raw := placeVec v d.values (placeVec t d.participants d.origin)
  if d.wrap then List.zipWith (· % ·) raw d.tile.shape else raw

/-- Every (participant, value) pair. -/
def pairs (T V : Nat) : List (Nat × Nat) := (List.range T).flatMap (fun t => (List.range V).map (fun v => (t, v)))

def Spread.pairs (d : Spread) : List (Nat × Nat) := Layout.pairs d.count d.each

/-- The element a pair holds. -/
def Spread.element (d : Spread) (p : Nat × Nat) : Nat := number (d.coords p.1 p.2) d.tile.shape

/-- The pairs that hold element `e`. -/
def Spread.holders (d : Spread) (e : Nat) : List (Nat × Nat) := d.pairs.filter (fun p => d.element p == e)

/-- Some pair names a coordinate outside the tile: the Python refuses that as a malformed layout. -/
def Spread.outside (d : Spread) : Bool := d.pairs.any (fun p => !inside (d.coords p.1 p.2) d.tile.shape)

/-- The first element with two holders, `E-LAYOUT-OVERLAP`. -/
def Spread.overlap (d : Spread) : Option Nat := (List.range (size d.tile.shape)).find? (fun e => 2 ≤ (d.holders e).length)

/-- The first element with none, `E-LAYOUT-GAP`. -/
def Spread.gap (d : Spread) : Option Nat := (List.range (size d.tile.shape)).find? (fun e => (d.holders e).length == 0)

/-- What the checker accepts of a declared spread. -/
def Spread.ok (d : Spread) : Bool := !d.outside && d.overlap.isNone && d.gap.isNone

/-! ### What a coordinate's number is -/

theorem number_lt : ∀ (cs ns : List Nat), inside cs ns = true → number cs ns < size ns
  | [], [], _ => by simp [number, size]
  | a :: as, n :: ns, h => by
    simp only [inside, Bool.and_eq_true, decide_eq_true_eq] at h
    have ih := number_lt as ns h.2
    simp only [number, size]
    have : n * number as ns + n ≤ n * size ns := by
      have := Nat.mul_le_mul_left n (Nat.succ_le_of_lt ih)
      simpa [Nat.mul_succ] using this
    omega
  | [], _ :: _, h => by simp [inside] at h
  | _ :: _, [], h => by simp [inside] at h

theorem unravel_number : ∀ (cs ns : List Nat), inside cs ns = true → unravel (number cs ns) ns = cs
  | [], [], _ => rfl
  | a :: as, n :: ns, h => by
    simp only [inside, Bool.and_eq_true, decide_eq_true_eq] at h
    have hn : 0 < n := Nat.lt_of_le_of_lt (Nat.zero_le a) h.1
    simp only [number, unravel]
    have hmod : (a + n * number as ns) % n = a := by
      rw [Nat.add_mul_mod_self_left]; exact Nat.mod_eq_of_lt h.1
    have hdiv : (a + n * number as ns) / n = number as ns := by
      rw [Nat.add_mul_div_left _ _ hn, Nat.div_eq_of_lt h.1, Nat.zero_add]
    rw [hmod, hdiv, unravel_number as ns h.2]
  | [], _ :: _, h => by simp [inside] at h
  | _ :: _, [], h => by simp [inside] at h

/-! ### What a storage layout that passes promises -/

theorem clash_none_find (L : Storage) (h : L.clash = none) :
    (List.range (size L.shape)).find? L.shares = none := by
  unfold Storage.clash at h
  cases hf : (List.range (size L.shape)).find? L.shares with
  | none => rfl
  | some e =>
    simp only [hf] at h
    have hs : L.shares e = true := List.find?_some hf
    unfold Storage.shares at hs
    obtain ⟨a, ha, hat⟩ := List.any_eq_true.mp hs
    cases hg : (List.range e).find? (fun a => L.at a == L.at e) with
    | none =>
      have := List.find?_eq_none.mp hg a ha
      exact absurd hat this
    | some b => simp [hg] at h

/-- No two elements of a storage layout that passes share an offset. -/
theorem storage_distinct (L : Storage) (h : L.clash = none) :
    ∀ a b, a < size L.shape → b < size L.shape → L.at a = L.at b → a = b := by
  have none := List.find?_eq_none.mp (clash_none_find L h)
  intro a b ha hb hab
  have later : ∀ x y, x < y → y < size L.shape → L.at x = L.at y → False := by
    intro x y hxy hy hxy'
    have := none y (List.mem_range.mpr hy)
    apply this
    unfold Storage.shares
    exact List.any_eq_true.mpr ⟨x, List.mem_range.mpr hxy, beq_iff_eq.mpr hxy'⟩
  rcases Nat.lt_trichotomy a b with lt | eq | gt
  · exact (later a b lt hb hab).elim
  · exact eq
  · exact (later b a gt ha hab.symm).elim

/-! ### What a spread that passes promises -/

theorem ok_parts (d : Spread) (h : d.ok = true) :
    d.outside = false ∧ d.overlap = none ∧ d.gap = none := by
  simp only [Spread.ok, Bool.and_eq_true, Bool.not_eq_true', Option.isNone_iff_eq_none] at h
  exact ⟨h.1.1, h.1.2, h.2⟩

/-- Every pair of a spread that passes names a coordinate inside its tile. -/
theorem ok_inside (d : Spread) (h : d.ok = true) :
    ∀ p ∈ d.pairs, inside (d.coords p.1 p.2) d.tile.shape = true := by
  intro p hp
  have hout := (ok_parts d h).1
  unfold Spread.outside at hout
  have := List.any_eq_false.mp hout p hp
  cases hi : inside (d.coords p.1 p.2) d.tile.shape
  · exact absurd (by rw [hi]; rfl) this
  · rfl

/-- Every element of the tile has exactly one holder. -/
theorem ok_one_holder (d : Spread) (h : d.ok = true) :
    ∀ e, e < size d.tile.shape → (d.holders e).length = 1 := by
  intro e he
  obtain ⟨_, hover, hgap⟩ := ok_parts d h
  have no2 := List.find?_eq_none.mp hover e (List.mem_range.mpr he)
  have no0 := List.find?_eq_none.mp hgap e (List.mem_range.mpr he)
  simp only [decide_eq_true_eq, Nat.not_le, beq_iff_eq] at no2 no0
  omega

/-- A phase in which every participant writes each of its values writes every element of the tile. -/
theorem ok_covers (d : Spread) (h : d.ok = true) :
    ∀ e, e < size d.tile.shape → ∃ p ∈ d.pairs, d.element p = e := by
  intro e he
  have hone := ok_one_holder d h e he
  obtain ⟨p, hp⟩ := List.length_eq_one_iff.mp hone
  have hmem : p ∈ d.holders e := by rw [hp]; exact List.mem_singleton_self p
  unfold Spread.holders at hmem
  obtain ⟨hin, heq⟩ := List.mem_filter.mp hmem
  exact ⟨p, hin, beq_iff_eq.mp heq⟩

/-- Two pairs that hold one element are one pair: no two participants, and no two values of one, write it. -/
theorem ok_one_writer (d : Spread) (h : d.ok = true) :
    ∀ p ∈ d.pairs, ∀ q ∈ d.pairs, d.element p = d.element q → p = q := by
  intro p hp q hq hpq
  have hlt := number_lt _ _ (ok_inside d h p hp)
  have hone := ok_one_holder d h (d.element p) hlt
  obtain ⟨r, hr⟩ := List.length_eq_one_iff.mp hone
  have mp : p ∈ d.holders (d.element p) := List.mem_filter.mpr ⟨hp, beq_iff_eq.mpr rfl⟩
  have mq : q ∈ d.holders (d.element p) := List.mem_filter.mpr ⟨hq, beq_iff_eq.mpr hpq.symm⟩
  rw [hr] at mp mq
  rw [List.mem_singleton.mp mp, List.mem_singleton.mp mq]

/-- With a tile that passes its own rule, no two pairs of a spread that passes write one offset: writes through
`D.at(t, v)` never collide. -/
theorem ok_no_collision (d : Spread) (h : d.ok = true) (hs : d.tile.clash = none) :
    ∀ p ∈ d.pairs, ∀ q ∈ d.pairs,
      d.tile.offset (d.coords p.1 p.2) = d.tile.offset (d.coords q.1 q.2) → p = q := by
  intro p hp q hq hoff
  have ip := ok_inside d h p hp
  have iq := ok_inside d h q hq
  have ap : d.tile.at (d.element p) = d.tile.offset (d.coords p.1 p.2) := by
    unfold Storage.at Spread.element; rw [unravel_number _ _ ip]
  have aq : d.tile.at (d.element q) = d.tile.offset (d.coords q.1 q.2) := by
    unfold Storage.at Spread.element; rw [unravel_number _ _ iq]
  have same := storage_distinct d.tile hs _ _ (number_lt _ _ ip) (number_lt _ _ iq)
    (by show d.tile.at (d.element p) = d.tile.at (d.element q); rw [ap, aq, hoff])
  exact ok_one_writer d h p hp q hq same

/-! ### Non-vacuity: each refusal happens -/

/-- 3 x 4 participants over a 4 x 4 tile, as `spread(rows(4, 4), 3, 4, 1, 1)`: row 3 is left to nobody. -/
def gappy : Spread :=
  { tile := ⟨[[(4, 1)], [(4, 4)]], (0, 0, 0)⟩,
    participants := [(4, [1, 0]), (3, [0, 1])],
    values := [(1, [1, 0]), (1, [0, 1]), (1, [4, 0]), (1, [0, 3])],
    wrap := true, origin := [0, 0] }

theorem gappy_gap : gappy.gap = some 12 := by decide

/-- Four participants across a 2 x 2 tile, as `spread(rows(2, 2), 1, 4, 1, 1)`: the last two wrap onto the
first two. -/
def doubled : Spread :=
  { tile := ⟨[[(2, 1)], [(2, 2)]], (0, 0, 0)⟩,
    participants := [(4, [1, 0]), (1, [0, 1])],
    values := [(1, [1, 0]), (1, [0, 1]), (1, [4, 0]), (2, [0, 1])],
    wrap := true, origin := [0, 0] }

theorem doubled_overlap : doubled.overlap = some 0 := by decide

/-- The same four participants over a 2 x 4 tile hold every element once. -/
theorem fitted_ok : ({ doubled with tile := ⟨[[(4, 1)], [(2, 4)]], (0, 0, 0)⟩ } : Spread).ok = true := by decide

/-- A swizzle with no shift clears the bits it would flip, so two elements meet. -/
theorem cleared_clash : (Storage.mk [[(4, 1)], [(4, 4)]] (1, 0, 0)).clash = some (0, 1) := by decide

/-! ### The mma.sync accumulator's share

A tensor-core fragment is a warp's value, and a lane stores only the elements it holds (`cr::frag::holder` and
`cr::frag::element` in `runtime/cairn_fragment.hpp`).  For an m16n8 accumulator that share is
`spread(rows(16, 8), 8, 4, 1, 2)`: it is the PTX ISA's, and it passes the rule, so `ok_one_writer` says the 32 lanes
of a warp store each element once between them. -/

def accumulatorShare : Spread :=
  { tile := ⟨[[(8, 1)], [(16, 8)]], (0, 0, 0)⟩,
    participants := [(4, [2, 0]), (8, [0, 1])],
    values := [(2, [1, 0]), (1, [0, 1]), (1, [8, 0]), (2, [0, 8])],
    wrap := true, origin := [0, 0] }

theorem accumulator_share_ok : accumulatorShare.ok = true := by decide

/-- Lane `l`'s value `v` is element `(l / 4 + 8 * (v / 2), 2 * (l % 4) + v % 2)`, column first here. -/
theorem accumulator_share_is_the_isa :
    ∀ l, l < 32 → ∀ v, v < 4 → accumulatorShare.coords l v = [2 * (l % 4) + v % 2, l / 4 + 8 * (v / 2)] := by
  decide

end Layout
end Cairn
