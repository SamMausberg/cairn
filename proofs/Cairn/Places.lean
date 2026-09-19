/-
Places, bounds and visible disjointness.

This file is the part of the ownership calculus that talks about *what one borrow
names*.  It is the mechanization of `checking.py:path` and `checking.py:overlaps`:

* `path` turns an expression into a syntactic place identity: `r.a` for a field,
  `d` for the owner, `d[]` for its elements, `d[lo..hi]` for a part.  A bound
  survives only when it cannot change -- an integer literal, or the name of an
  immutable value -- and anything else becomes `?`.  `Place` below is a `Root` (a
  local and a field path) plus one constructor per shape.  There is no `?`
  constructor, and none is needed: a single element and every place carrying a `?`
  is mapped onto `elems`, which the section "The `?` shapes, case by case" below
  checks row by row against `overlaps`.
* `overlaps` decides disjointness of two parts of one array *syntactically*: they
  are disjoint only when one visibly ends at or before the other begins, where the
  order of two bounds may be derived by chaining the `lo <= hi` facts of every other
  part in play.  `ovl` below is that decision; `reaches` is the chain.  Outside the
  parts it is the dotted-prefix test of the two bases: places of different locals
  never overlap, distinct fields of one record never overlap, and a field overlaps
  its record.
* Beside the syntactic decision there is the real one: under a valuation of the
  immutable names, each place denotes a set of indices (and possibly the header),
  and two accesses really collide when those sets meet.  `meets` is that relation.

The bridge between them is `ovl_sound`: if every fact in play is true under the
valuation, then what the checker calls disjoint really is disjoint.  A fact is true
exactly when the `lo <= hi` guard that `cr::part` performs at the slice has already
run, which is why the machine in `Ownership.lean` performs that guard -- and traps
when it fails -- before a borrow is ever taken.
-/

namespace Cairn
namespace Ownership

/-! ## Elementary decidability

Nothing in this development uses excluded middle; these two are the case splits
that replace it. -/

/-- Decidable case split, so that nothing below needs excluded middle. -/
theorem nat_eq_or_ne (p q : Nat) : p = q ∨ p ≠ q :=
  match Nat.decEq p q with
  | isTrue h => Or.inl h
  | isFalse h => Or.inr h

/-- The same case split for anything with decidable equality, which is every type in
this development. -/
theorem dec_eq_or_ne {α : Type} [DecidableEq α] (p q : α) : p = q ∨ p ≠ q :=
  if h : p = q then Or.inl h else Or.inr h

/-- `Nat.beq` is reflexive; proved here so that nothing reaches for the general
`LawfulBEq` lemma, whose instance chain drags `Classical.choice` in. -/
theorem beq_place_self (x : Nat) : (x == x) = true := by
  cases h : (x == x) with
  | true => rfl
  | false => exact absurd rfl (beq_eq_false_iff_ne.mp h)

theorem beq_place_comm (p q : Nat) : (p == q) = (q == p) := by
  rcases nat_eq_or_ne p q with hpq | hpq
  · rw [hpq]
  · rw [beq_eq_false_iff_ne.mpr hpq, beq_eq_false_iff_ne.mpr fun hc => hpq hc.symm]

/-! ## Names, bounds and valuations -/

/-- A local of the scope under check: the owner a place is rooted at. -/
abbrev Var := Nat

/-- The name of an immutable natural usable as a part bound. -/
abbrev BName := Nat

/-- A part bound.  `checking.py:path` keeps a bound only when it cannot change: an
integer literal, or a name `stable` vouches for (an immutable local of value mode).
Anything else becomes `?` there, and a place carrying a `?` overlaps everything of
its base; such parts are not modelled. -/
inductive Bound where
  | lit (n : Nat)
  | nm (v : BName)
deriving DecidableEq, Repr, Inhabited

/-- What the immutable names are worth at run time.  It is arbitrary and fixed:
every theorem quantifies over it, so no result may depend on the numbers. -/
abbrev Valuation := BName → Nat

/-- The run-time value of a bound. -/
def Bound.eval (ρ : Valuation) : Bound → Nat
  | .lit n => n
  | .nm v => ρ v

/-- The order two bounds show on their face, with no facts in hand: the same bound,
or two literals.  This is the `before` line of `checking.py:overlaps.reaches`
(`x == goal or (x.isdigit() and goal.isdigit() and int(x) <= int(goal))`); a literal
and a name are never comparable, even when the name happens to hold that number. -/
def leB : Bound → Bound → Bool
  | .lit m, .lit n => decide (m ≤ n)
  | .nm u, .nm v => u == v
  | _, _ => false

theorem leB_sound {ρ : Valuation} : ∀ {a b : Bound}, leB a b = true → a.eval ρ ≤ b.eval ρ := by
  intro a b h
  cases a <;> cases b <;> simp only [leB] at h <;>
    first
      | exact absurd h (fun hc => Bool.noConfusion hc)
      | exact of_decide_eq_true h
      | (rename_i u v
         have huv : u = v := by simp at h; exact h
         simp [Bound.eval, huv])

/-! ## Chaining the guarded facts

A part `d[lo..hi]` that is in play contributes the fact `lo <= hi`, because the
slice was formed by `cr::part`, which traps unless `lo <= hi`.  `reaches facts x
goal` is "`x <= goal` is derivable from those facts", exactly as the inner
`reaches` of `checking.py:overlaps`: start at `x`, step across a fact whose lower
bound is visibly at or above where you are, and stop when the goal is visibly at or
above where you are.  The Python version is a depth-first search with a `seen` set;
this one saturates the reachable set one round at a time, which is structurally
recursive on the fuel.  Both are meant to decide the same reachability -- a shortest
chain never repeats a fact, so one round per fact is enough -- but that the two
implementations agree is a claim about the Python code, and nothing here proves
it. -/

/-- One `lo <= hi` known to hold because its slice was guarded. -/
abbrev Fact := Bound × Bound

/-- Every fact in the list really holds under `ρ`. -/
def FactsTrue (ρ : Valuation) (facts : List Fact) : Prop :=
  ∀ f ∈ facts, f.1.eval ρ ≤ f.2.eval ρ

/-- One saturation round: everything already reached, plus the upper bound of every
fact whose lower bound is visibly at or above something already reached. -/
def grow (facts : List Fact) (S : List Bound) : List Bound :=
  S ++ facts.filterMap fun f => if S.any (fun u => leB u f.1) then some f.2 else none

/-- `goal` is reached from `S` in at most `n` rounds. -/
def reachesFuel (facts : List Fact) : Nat → List Bound → Bound → Bool
  | 0, S, goal => S.any (fun u => leB u goal)
  | n + 1, S, goal => S.any (fun u => leB u goal) || reachesFuel facts n (grow facts S) goal

/-- `x <= goal`, derived by chaining the facts.  One round per fact is enough: a
shortest chain never uses one twice. -/
def reaches (facts : List Fact) (x goal : Bound) : Bool :=
  reachesFuel facts facts.length [x] goal

theorem grow_sound {ρ : Valuation} {facts : List Fact} (hf : FactsTrue ρ facts)
    {m : Nat} {S : List Bound} (hS : ∀ u ∈ S, m ≤ u.eval ρ) :
    ∀ u ∈ grow facts S, m ≤ u.eval ρ := by
  intro u hu
  rcases List.mem_append.mp hu with h1 | h2
  · exact hS u h1
  · obtain ⟨f, hfm, hfe⟩ := List.mem_filterMap.mp h2
    split at hfe
    · next hany =>
        have hfu : f.2 = u := Option.some.inj hfe
        obtain ⟨v, hv, hlv⟩ := List.any_eq_true.mp hany
        calc m ≤ v.eval ρ := hS v hv
          _ ≤ f.1.eval ρ := leB_sound hlv
          _ ≤ f.2.eval ρ := hf f hfm
          _ = u.eval ρ := by rw [hfu]
    · exact absurd hfe (by simp)

theorem reachesFuel_sound {ρ : Valuation} {facts : List Fact} (hf : FactsTrue ρ facts) :
    ∀ (n : Nat) (S : List Bound) (goal : Bound) (m : Nat), (∀ u ∈ S, m ≤ u.eval ρ) →
      reachesFuel facts n S goal = true → m ≤ goal.eval ρ := by
  intro n
  induction n with
  | zero =>
      intro S goal m hS h
      obtain ⟨u, hu, hlu⟩ := List.any_eq_true.mp h
      exact Nat.le_trans (hS u hu) (leB_sound hlu)
  | succ n ih =>
      intro S goal m hS h
      rcases Bool.or_eq_true_iff.mp h with h1 | h2
      · obtain ⟨u, hu, hlu⟩ := List.any_eq_true.mp h1
        exact Nat.le_trans (hS u hu) (leB_sound hlu)
      · exact ih (grow facts S) goal m (grow_sound hf hS) h2

/-- **Chaining is sound.**  Every order the checker derives from the facts holds of
the numbers, provided the facts themselves do. -/
theorem reaches_sound {ρ : Valuation} {facts : List Fact} (hf : FactsTrue ρ facts)
    {x goal : Bound} (h : reaches facts x goal = true) : x.eval ρ ≤ goal.eval ρ :=
  reachesFuel_sound hf _ [x] goal (x.eval ρ)
    (fun u hu => by rw [List.eq_of_mem_singleton hu]; exact Nat.le_refl _) h

/-! ## Roots: the local, and the field path into it -/

/-- The name of a record field. -/
abbrev FName := Nat

/-- The storage a borrow is rooted at: a local, and the field path into it that
`checking.py:path` writes as `r.a.b`.  The empty path is the local itself. -/
structure Root where
  var : Var
  fields : List FName
deriving DecidableEq, Repr, Inhabited

/-- One field path is a prefix of the other, so the two name storage that meets.
This is the dotted-prefix test `checking.py:overlaps` falls back on
(`base_a == base_b or base_a.startswith(base_b + ".")`): `r.a` is inside `r`, and
`r.a` and `r.b` are apart. -/
def nested : List FName → List FName → Bool
  | [], _ => true
  | _ :: _, [] => true
  | f :: fs, g :: gs => f == g && nested fs gs

theorem nested_symm : ∀ l m : List FName, nested l m = nested m l := by
  intro l
  induction l with
  | nil => intro m; cases m <;> rfl
  | cons f fs ih =>
      intro m
      cases m with
      | nil => rfl
      | cons g gs => simp only [nested, ih gs, beq_place_comm f g]

/-- **Two roots name overlapping storage**: the same local, and one field path inside
the other.  A field path is exact -- nothing about it is approximated -- so the
checker and the machine ask the root exactly the same question. -/
def Root.touches (r s : Root) : Bool := (r.var == s.var) && nested r.fields s.fields

theorem Root.touches_symm (r s : Root) : r.touches s = s.touches r := by
  simp only [Root.touches, beq_place_comm r.var s.var, nested_symm r.fields s.fields]

/-- The local itself overlaps every root inside it, whatever field path that root
names. -/
theorem Root.touches_var {x : Var} {s : Root} (h : s.var = x) :
    Root.touches ⟨x, []⟩ s = true := by
  simp only [Root.touches, h, beq_place_self, nested, Bool.and_true]

/-! ## Places -/

/-- What one borrow names: a root, and one of the shapes `checking.py:path` produces.
`checking.py:lend` chooses between the first three by the parameter it is filling:

* `whole r` is the storage itself (`d`, `r.a`), header and elements: what
  `rw<Buf[T]>` lends, and the only borrow through which a task can replace the cell
  (`swap`);
* `hdr r` is the header alone -- the length and the identity of the cell -- which is
  what `len(d)` reads (`leased(..., elements = False)`);
* `elems r` is every element (`d[]`, `r.xs[]`): what an array view `rw<T>[n]` of a
  whole owner lends, which leaves the owner's length readable;
* `part r lo hi` is the elements `[lo, hi)` (`d[lo..hi]`, `r.xs[lo..hi]`). -/
inductive Place where
  | whole (r : Root)
  | hdr (r : Root)
  | elems (r : Root)
  | part (r : Root) (lo hi : Bound)
deriving DecidableEq, Repr, Inhabited

/-! ### The `?` shapes, case by case

`checking.py:path` writes three shapes this type has no constructor for.  A single element
`a[i]` becomes `"a[]"`, whatever the index is.  A part of a part `a[lo..hi][j..k]` becomes
`"a[?..?]"`, since it sits somewhere inside the outer part and nothing says where.  A part one
of whose bounds can change -- a mutable local, a call -- keeps the bounds it can see and writes
the rest as `?`, so `a[m..3]` with `m` mutable becomes `"a[?..3]"`.

Every one of them is modelled here as `elems`, conservatively, and the two agree case by case.
Write `d` and `e` for two locals and `q` for any `?`-carrying string over the base `d`:

| `checking.py` | here | both say |
| --- | --- | --- |
| `overlaps(q, "d[0..3]")` | `ovl (elems d) (part d 0 3)` | `true` |
| `overlaps(q, "d[]")`, `overlaps("d[]", "d[0..3]")` | `ovl (elems d) (elems d)`, `ovl (elems d) (part d 0 3)` | `true` |
| `overlaps(q, "d")` | `ovl (elems d) (whole d)` | `true` |
| a held `q` is skipped by `leased(..., elements = False)` | `ovl (hdr d) (elems d)` | `false` |
| `overlaps(q, "e[0..3]")` | the roots do not touch | `false` |
| a `?` bound never reaches `edges` | `Place.range (elems d) = none` | no chainable fact |

The last row is the one that costs something.  `overlaps` collects the bounds of every part of
the base that is in play and drops the pairs carrying a `?`, so a part whose bounds are not
visible orders nothing: with `d[a..b]` in play the two ends `d[0..a]` and `d[b..n]` are visibly
disjoint, and with a `?`-carrying part in its place they are not.  `Place.range` returns `none`
for `elems`, which is exactly that.

Two further parts of the mapping sit on the machine's side.  `Place.ext ρ (elems d) = .all` is
every element, which contains whatever `a[i]`, the inner part or the invisible bound really
touches, so `ovl_sound` below still covers the real access.  And `Place.guard ρ (elems d)` is
`true`: forming `d[m..3]` does run the `lo <= hi` guard, but the model takes no fact from it, so
there is nothing to discharge.

This is a claim about `checking.py`, checked row by row against it and pinned by the `example`s
under `ovl` below and by the programs in `Ownership.lean`'s `Regress` section.  Nothing here
proves it, because nothing here reads the Python source. -/

/-- The storage a place is rooted at. -/
def Place.root : Place → Root
  | .whole r => r
  | .hdr r => r
  | .elems r => r
  | .part r _ _ => r

/-- The local a place is rooted at: the unit of ownership, whatever field path the
place names inside it. -/
def Place.base (p : Place) : Var := p.root.var

/-- Does the place name elements -- does `path` write a `[` for it?  This is the
`"[" not in p` of `checking.py:leased(..., elements = False)`: a `len` read is
disturbed by a lease of the owner, never by one of its elements or of a part. -/
def Place.overElements : Place → Bool
  | .elems _ => true
  | .part _ _ _ => true
  | _ => false

/-- The fact a place contributes to the chain: a part was guarded `lo <= hi`. -/
def Place.range : Place → Option Fact
  | .part _ lo hi => some (lo, hi)
  | _ => none

/-- The guard `cr::part(p, lo, hi, n, want)` performs where the slice is formed.
The real guard also checks `hi <= n` and that the length matches the callee's
extent; only `lo <= hi` matters to the alias rules, so only it is modelled. -/
def Place.guard (ρ : Valuation) : Place → Bool
  | .part _ lo hi => decide (lo.eval ρ ≤ hi.eval ρ)
  | _ => true

/-- The facts of the parts of one array that are in play.  `checking.py:overlaps`
collects exactly these: the places of `others` whose base is the same *string* --
the same local and the same field path -- and that carry two visible bounds, plus
the two places being compared. -/
def factsFor (r : Root) (inPlay : List Place) : List Fact :=
  inPlay.filterMap fun p => if p.root = r then p.range else none

theorem factsFor_true {ρ : Valuation} {r : Root} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) : FactsTrue ρ (factsFor r inPlay) := by
  intro f hf
  obtain ⟨p, hp, hpe⟩ := List.mem_filterMap.mp hf
  have hrange : p.range = some f := by
    split at hpe
    · exact hpe
    · exact absurd hpe (by simp)
  have hpg := hg p hp
  cases p with
  | whole y => exact absurd hrange (by simp [Place.range])
  | hdr y => exact absurd hrange (by simp [Place.range])
  | elems y => exact absurd hrange (by simp [Place.range])
  | part y lo hi =>
      have hfe : f = (lo, hi) := (Option.some.inj hrange).symm
      rw [hfe]
      exact of_decide_eq_true hpg

/-! ## The syntactic decision

`ovl inPlay r s` is `checking.py:overlaps(a, b, others)`: same base, and then, for
two parts, "not visibly disjoint". -/

/-- One part ends at or before the other begins, by chaining the facts in play.
Symmetric by construction. -/
def visiblyDisjoint (facts : List Fact) (lo1 hi1 lo2 hi2 : Bound) : Bool :=
  reaches facts hi1 lo2 || reaches facts hi2 lo1

/-- **The checker's overlap test.**  Two places whose roots do not touch never
overlap: different locals, or distinct fields of one record.  Two parts of one root
are disjoint exactly when they are visibly disjoint.  The header and the elements do
not overlap, which is why `len(d)` stays readable while a view of `d`'s elements is
lent.  Everything else overlaps: in particular `whole r` overlaps every place of `r`,
since replacing the cell disturbs all of them, `elems r` overlaps every part (its
bounds are not visible), and two parts of *different* roots that touch -- a part of a
field of a part -- overlap, which `path` never produces but the fallback covers. -/
def ovl (inPlay : List Place) (r s : Place) : Bool :=
  r.root.touches s.root &&
    (match r, s with
      | .part x lo1 hi1, .part y lo2 hi2 =>
          if x = y then !visiblyDisjoint (factsFor x inPlay) lo1 hi1 lo2 hi2 else true
      | .hdr _, .part _ _ _ => false
      | .part _ _ _, .hdr _ => false
      | .hdr _, .elems _ => false
      | .elems _, .hdr _ => false
      | _, _ => true)

theorem ovl_symm (inPlay : List Place) (r s : Place) : ovl inPlay r s = ovl inPlay s r := by
  cases r <;> cases s <;> simp only [ovl, Place.root, Root.touches_symm] <;>
    first
      | rfl
      | (rename_i x lo1 hi1 y lo2 hi2
         rcases dec_eq_or_ne x y with h | h
         · subst h; simp only [visiblyDisjoint, Bool.or_comm]
         · split
           · next hc => exact absurd hc h
           · split
             · next hc => exact absurd hc.symm h
             · rfl)

/-- `whole x` touches the root of every place of the local `x`, field path and all. -/
theorem touches_base {x : Var} {p : Place} (h : p.base = x) :
    Root.touches ⟨x, []⟩ p.root = true :=
  Root.touches_var h

/-- `whole x` overlaps every place of the local `x`: this is what makes a lease of
any part, or of any field, block a move, a drop or a rebinding of the owner. -/
theorem ovl_whole (inPlay : List Place) (x : Var) (r : Place) (h : r.base = x) :
    ovl inPlay (.whole ⟨x, []⟩) r = true := by
  have ht : Root.touches ⟨x, []⟩ r.root = true := touches_base h
  cases r <;> simp only [Place.root] at ht <;> simp only [ovl, Place.root, ht, Bool.true_and]

/-! ### The `?` shapes, pinned

One `example` per row of the table beside `Place`: what `checking.py:overlaps` answers for the
strings `path` writes with a `?`, and what `ovl` answers for the `elems` they are mapped to. -/

section ElemsMapping

private def dLocal : Root := ⟨0, []⟩
private def eLocal : Root := ⟨1, []⟩
private def aBound : Bound := .nm 0
private def bBound : Bound := .nm 1
private def nBound : Bound := .nm 2

/-- `overlaps("d[?..?]", "d[0..3]") = True`: a `?`-carrying part overlaps every part of its
base, and so does `elems`. -/
example : ovl [] (.elems dLocal) (.part dLocal (.lit 0) (.lit 3)) = true := by decide

/-- `overlaps("d[]", "d[]") = True`: two element accesses of one array overlap. -/
example : ovl [] (.elems dLocal) (.elems dLocal) = true := by decide

/-- `overlaps("d[?..?]", "d") = True`: the whole owner too. -/
example : ovl [] (.elems dLocal) (.whole dLocal) = true := by decide

/-- `leased(place, mode, elements = False)` skips every held place whose string carries a `[`,
so `len(d)` stays readable while a `?`-carrying part of `d` is lent. -/
example : ovl [] (.hdr dLocal) (.elems dLocal) = false := by decide

/-- `overlaps("e[?..?]", "d[0..3]") = False`: two different locals never touch, and a `?` does
not change that. -/
example : ovl [] (.elems eLocal) (.part dLocal (.lit 0) (.lit 3)) = false := by decide

/-- The chain with a part whose bounds are visible in the middle: `d[0..a]` and `d[b..n]` are
visibly disjoint, because `d[a..b]` contributes `a <= b`.  This is
`overlaps("d[0..a]", "d[b..n]", ("d[a..b]",)) = False`. -/
example :
    ovl [.part dLocal (.lit 0) aBound, .part dLocal aBound bBound, .part dLocal bBound nBound]
      (.part dLocal (.lit 0) aBound) (.part dLocal bBound nBound) = false := by decide

/-- The same chain with a `?`-carrying part in the middle: it contributes no edge, so the two
ends are no longer ordered and both checkers keep them apart.  This is
`overlaps("d[0..a]", "d[b..n]", ("d[?..b]",)) = True`. -/
example :
    ovl [.part dLocal (.lit 0) aBound, .elems dLocal, .part dLocal bBound nBound]
      (.part dLocal (.lit 0) aBound) (.part dLocal bBound nBound) = true := by decide

/-- `elems` carries no `lo <= hi` fact, which is what "a `?` bound never reaches `edges`"
means. -/
example : Place.range (.elems dLocal) = none := rfl

end ElemsMapping

/-! ## The real relation

Under a valuation each place denotes a footprint: a Boolean saying whether it
touches the header, and the set of element indices it may touch.  Two accesses
collide when the footprints meet.  This is what the machine in `Ownership.lean`
calls a race. -/

/-- The element footprint of a place: every element, or the half-open range. -/
inductive Ext where
  | all
  | rng (lo hi : Nat)
deriving DecidableEq, Repr, Inhabited

/-- Two element footprints have an index in common.  A backwards or empty range
meets nothing, which is why `d[6..3]` cannot race with anything -- but forming it
traps first. -/
def Ext.meets : Ext → Ext → Bool
  | .all, .all => true
  | .all, .rng lo hi => decide (lo < hi)
  | .rng lo hi, .all => decide (lo < hi)
  | .rng l1 h1, .rng l2 h2 => decide (l1 < h1 ∧ l2 < h2 ∧ l1 < h2 ∧ l2 < h1)

theorem Ext.meets_symm (a b : Ext) : a.meets b = b.meets a := by
  cases a <;> cases b <;> simp only [Ext.meets] <;>
    first
      | rfl
      | (rw [decide_eq_decide]
         exact ⟨fun h => ⟨h.2.1, h.1, h.2.2.2, h.2.2.1⟩,
                fun h => ⟨h.2.1, h.1, h.2.2.2, h.2.2.1⟩⟩)

/-- Every index a range touches, the whole extent touches.  This is what lets a lane
be checked against `x[]` -- what `where` writes for an index -- and then run holding
only its own element. -/
theorem Ext.meets_all_of_rng {lo hi : Nat} :
    ∀ {e : Ext}, (Ext.rng lo hi).meets e = true → Ext.all.meets e = true := by
  intro e h
  cases e with
  | all => rfl
  | rng l h' =>
      simp only [Ext.meets] at h ⊢
      exact decide_eq_true (of_decide_eq_true h).2.1

def Place.ext (ρ : Valuation) : Place → Ext
  | .whole _ => .all
  | .elems _ => .all
  | .hdr _ => .rng 0 0
  | .part _ lo hi => .rng (lo.eval ρ) (hi.eval ρ)

/-- Does the access touch the header -- the length and the identity of the cell? -/
def Place.header : Place → Bool
  | .whole _ => true
  | .hdr _ => true
  | _ => false

/-- **The real overlap.**  Two accesses whose roots meet collide when both touch the
header or their element ranges share an index. -/
def meets (ρ : Valuation) (r s : Place) : Bool :=
  r.root.touches s.root && ((r.header && s.header) || (r.ext ρ).meets (s.ext ρ))

theorem meets_symm (ρ : Valuation) (r s : Place) : meets ρ r s = meets ρ s r := by
  simp only [meets, Root.touches_symm r.root s.root, Bool.and_comm (r.header) (s.header),
    Ext.meets_symm (r.ext ρ) (s.ext ρ)]

/-- **The bridge.**  What the checker decides syntactically is true of the numbers,
provided every fact in play was guarded first. -/
theorem ovl_sound {ρ : Valuation} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) {r s : Place}
    (h : ovl inPlay r s = false) : meets ρ r s = false := by
  unfold ovl at h
  unfold meets
  cases hbase : r.root.touches s.root with
  | false => simp
  | true =>
      rw [hbase] at h
      simp only [Bool.true_and] at h ⊢
      cases r <;> cases s <;>
        simp only [Place.header, Place.ext, Bool.and_self, Bool.and_false, Bool.false_and,
          Bool.false_or, Ext.meets] <;>
        first
          | exact absurd h (fun hc => Bool.noConfusion hc)
          | (rw [decide_eq_false_iff_not]; omega)
          | (rename_i x lo1 hi1 y lo2 hi2
             have h' : (if x = y then !visiblyDisjoint (factsFor x inPlay) lo1 hi1 lo2 hi2
                        else true) = false := h
             split at h'
             · have hvd : visiblyDisjoint (factsFor x inPlay) lo1 hi1 lo2 hi2 = true := by
                 cases hv : visiblyDisjoint (factsFor x inPlay) lo1 hi1 lo2 hi2 with
                 | true => rfl
                 | false => rw [hv] at h'; exact absurd h' (fun hc => Bool.noConfusion hc)
               have hft : FactsTrue ρ (factsFor x inPlay) := factsFor_true hg
               rw [decide_eq_false_iff_not]
               rcases Bool.or_eq_true_iff.mp hvd with h1 | h2
               · have := reaches_sound hft h1
                 omega
               · have := reaches_sound hft h2
                 omega
             · exact absurd h' (fun hc => Bool.noConfusion hc))

/-- **A lane's own element sits inside the elements.**  The lease check a region runs
names `x[]` -- what `where` writes for an index -- while a lane holds only element
`[i]`, so whatever does not meet the elements does not meet one of them either. -/
theorem meets_elems_of_part {ρ : Valuation} {r : Root} {lo hi : Bound} {q : Place}
    (h : meets ρ (.part r lo hi) q = true) : meets ρ (.elems r) q = true := by
  simp only [meets, Place.root, Place.header, Place.ext, Bool.false_and, Bool.false_or,
    Bool.and_eq_true] at h ⊢
  exact ⟨h.1, Ext.meets_all_of_rng h.2⟩

/-- **Two lanes never touch the same element.**  Their indices differ, and each holds
the single element at its own index. -/
theorem meets_own_element {ρ : Valuation} {r s : Root} {k l : Nat} (h : k ≠ l) :
    meets ρ (.part r (.lit k) (.lit (k + 1))) (.part s (.lit l) (.lit (l + 1))) = false := by
  simp only [meets, Place.header, Place.ext, Bound.eval, Bool.false_and, Bool.false_or,
    Ext.meets, Bool.and_eq_false_iff, decide_eq_false_iff_not]
  right; omega

/-- The header's extent is empty, so nothing meets it. -/
theorem Ext.meets_hdr : ∀ e : Ext, e.meets (.rng 0 0) = false := by
  intro e; cases e <;> simp [Ext.meets]

/-- **The header is not an element.**  A `len` read never meets a lane's element. -/
theorem meets_part_hdr {ρ : Valuation} {r s : Root} {lo hi : Bound} :
    meets ρ (.part r lo hi) (.hdr s) = false := by
  simp only [meets, Place.header, Place.ext, Bool.false_and, Bool.false_or,
    Ext.meets_hdr, Bool.and_false]

/-! ## Borrows and conflicts -/

/-- How a call borrows a place for its duration. -/
inductive Mode where
  | ro
  | rw
deriving DecidableEq, Repr, Inhabited

/-- One borrow in an argument list: the place and the mode it is lent in. -/
abbrev Borrow := Place × Mode

/-- The name a `let t = spawn f(...)` binds. -/
abbrev Ticket := Nat

/-- A live task: the ticket that must be waited, and the footprint it holds. -/
abbrev Task := Ticket × List Borrow

/-- **The checker's rule**: `overlaps(a, b, lent) and "rw" in (m, k)`. -/
def conflict (inPlay : List Place) (a b : Borrow) : Bool :=
  ovl inPlay a.1 b.1 && (a.2 == Mode.rw || b.2 == Mode.rw)

/-- **The machine's rule**: the two accesses really touch a common location and one
of them writes. -/
def races (ρ : Valuation) (a b : Borrow) : Bool :=
  meets ρ a.1 b.1 && (a.2 == Mode.rw || b.2 == Mode.rw)

theorem conflict_symm (inPlay : List Place) (a b : Borrow) :
    conflict inPlay a b = conflict inPlay b a := by
  simp only [conflict, ovl_symm inPlay a.1 b.1, Bool.or_comm (a.2 == Mode.rw)]

theorem races_symm (ρ : Valuation) (a b : Borrow) : races ρ a b = races ρ b a := by
  simp only [races, meets_symm ρ a.1 b.1, Bool.or_comm (a.2 == Mode.rw)]

theorem conflict_sound {ρ : Valuation} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) {a b : Borrow}
    (h : conflict inPlay a b = false) : races ρ a b = false := by
  unfold conflict at h
  cases hm : ovl inPlay a.1 b.1 with
  | false =>
      show (meets ρ a.1 b.1 && _) = false
      rw [ovl_sound hg hm, Bool.false_and]
  | true =>
      have hmode : ((a.2 == Mode.rw) || (b.2 == Mode.rw)) = false := by
        rw [hm, Bool.true_and] at h; exact h
      show (meets ρ a.1 b.1 && _) = false
      rw [hmode, Bool.and_false]

/-- Both halves of a conjunction that holds, without reaching for a `simp` lemma. -/
theorem and_parts {a b : Bool} (h : (a && b) = true) : a = true ∧ b = true := by
  cases a
  · exact absurd h (fun hc => Bool.noConfusion hc)
  · cases b
    · exact absurd h (fun hc => Bool.noConfusion hc)
    · exact ⟨rfl, rfl⟩

/-- Two threads that hold nothing in common that either writes. -/
def NoRacePair (ρ : Valuation) (T U : Task) : Prop :=
  ∀ x ∈ T.2, ∀ y ∈ U.2, races ρ x y = false

theorem noRacePair_symm (ρ : Valuation) (T U : Task) (h : NoRacePair ρ T U) :
    NoRacePair ρ U T := by
  intro y hy x hx
  rw [races_symm]; exact h x hx y hy

/-- Every place the live tasks hold: the `lent` list of `checking.py:leased`. -/
def lentPlaces (tasks : List Task) : List Place :=
  tasks.flatMap fun T => T.2.map Prod.fst

/-- `checking.py:leased` -- does any live task hold something that conflicts? -/
def heldConflict (inPlay : List Place) (tasks : List Task) (x : Borrow) : Bool :=
  tasks.any fun T => T.2.any fun y => conflict inPlay x y

/-- The same question of the machine: does any live task really race with this
access? -/
def heldRace (ρ : Valuation) (tasks : List Task) (x : Borrow) : Bool :=
  tasks.any fun T => T.2.any fun y => races ρ x y

theorem heldConflict_eq_false_iff {inPlay : List Place} {tasks : List Task} {x : Borrow} :
    heldConflict inPlay tasks x = false ↔ ∀ T ∈ tasks, ∀ y ∈ T.2, conflict inPlay x y = false := by
  constructor
  · intro h T hT y hy
    cases hc : conflict inPlay x y with
    | false => rfl
    | true =>
        have hall : heldConflict inPlay tasks x = true := by
          simp only [heldConflict, List.any_eq_true]
          exact ⟨T, hT, y, hy, hc⟩
        rw [hall] at h; exact Bool.noConfusion h
  · intro h
    cases hc : heldConflict inPlay tasks x with
    | false => rfl
    | true =>
        simp only [heldConflict, List.any_eq_true] at hc
        obtain ⟨T, hT, y, hy1, hy2⟩ := hc
        rw [h T hT y hy1] at hy2; exact Bool.noConfusion hy2

theorem heldRace_eq_false_iff {ρ : Valuation} {tasks : List Task} {x : Borrow} :
    heldRace ρ tasks x = false ↔ ∀ T ∈ tasks, ∀ y ∈ T.2, races ρ x y = false := by
  constructor
  · intro h T hT y hy
    cases hc : races ρ x y with
    | false => rfl
    | true =>
        have hall : heldRace ρ tasks x = true := by
          simp only [heldRace, List.any_eq_true]
          exact ⟨T, hT, y, hy, hc⟩
        rw [hall] at h; exact Bool.noConfusion h
  · intro h
    cases hc : heldRace ρ tasks x with
    | false => rfl
    | true =>
        simp only [heldRace, List.any_eq_true] at hc
        obtain ⟨T, hT, y, hy1, hy2⟩ := hc
        rw [h T hT y hy1] at hy2; exact Bool.noConfusion hy2

theorem mem_lentPlaces {tasks : List Task} {T : Task} (hT : T ∈ tasks) {y : Borrow}
    (hy : y ∈ T.2) : y.1 ∈ lentPlaces tasks := by
  simp only [lentPlaces, List.mem_flatMap]
  exact ⟨T, hT, List.mem_map.mpr ⟨y, hy, rfl⟩⟩

/-- **The lease rule is sound.**  If the checker sees no conflict with the live
leases, and every part in play was guarded, then no live task really races with the
access. -/
theorem heldRace_of_heldConflict {ρ : Valuation} {tasks : List Task} {x : Borrow}
    {inPlay : List Place} (hg : ∀ p ∈ inPlay, p.guard ρ = true)
    (h : heldConflict inPlay tasks x = false) : heldRace ρ tasks x = false := by
  rw [heldRace_eq_false_iff]
  intro T hT y hy
  exact conflict_sound hg (heldConflict_eq_false_iff.mp h T hT y hy)

/-- A place nobody may write is a place no live task holds at all: `whole x`
overlaps every place of `x`, field paths and parts included. -/
theorem untouched_of_write_ok {inPlay : List Place} {tasks : List Task} {x : Var}
    (h : heldConflict inPlay tasks (.whole ⟨x, []⟩, Mode.rw) = false) :
    ∀ T ∈ tasks, ∀ y ∈ T.2, y.1.base ≠ x := by
  intro T hT y hy hc
  have hcf := heldConflict_eq_false_iff.mp h T hT y hy
  have hone : conflict inPlay (.whole ⟨x, []⟩, Mode.rw) y = true := by
    show (ovl inPlay (.whole ⟨x, []⟩) y.1 && ((Mode.rw == Mode.rw) || (y.2 == Mode.rw))) = true
    rw [ovl_whole inPlay x y.1 hc]
    rfl
  rw [hone] at hcf
  exact Bool.noConfusion hcf

end Ownership
end Cairn
