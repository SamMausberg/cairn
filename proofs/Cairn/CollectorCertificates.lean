/-
GENERATED FILE - DO NOT EDIT BY HAND.

Regenerate with:
    .venv/bin/python tools/checks/export_lean_certificates.py
Check for drift with:
    .venv/bin/python tools/checks/export_lean_certificates.py --check

Source of truth: `collector_rules()` in `src/cairn/verify/linear_certificates.py`.
SHA-256 of the exported obligation table (as `cairn certificates` reports it):
    5648cb8f06439f9ba5d87f3ffcfdc090803bed8ae7353940f83f10736afec1f2
-/
import Cairn.Affine

namespace Cairn
namespace Collector

/-- Obligation `initial.nonnegative`. -/
def rule_initial_nonnegative : Rule where
  name := "initial.nonnegative"
  assumptions := [⟨0, 0, 0, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩]
  conclusion := ⟨0, 0, 0, 0, 0⟩

/-- Nonnegative-combination witness for `initial.nonnegative`. -/
def cert_initial_nonnegative : Certificate where
  weights := [0, 0]
  nonnegativeConstant := 0

/-- Obligation `initial.cursor_before_input`. -/
def rule_initial_cursor_before_input : Rule where
  name := "initial.cursor_before_input"
  assumptions := [⟨0, 0, 0, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩]
  conclusion := ⟨0, 0, 0, 0, 0⟩

/-- Nonnegative-combination witness for `initial.cursor_before_input`. -/
def cert_initial_cursor_before_input : Certificate where
  weights := [0, 0]
  nonnegativeConstant := 0

/-- Obligation `initial.input_before_capacity`. -/
def rule_initial_input_before_capacity : Rule where
  name := "initial.input_before_capacity"
  assumptions := [⟨0, 0, 0, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩]
  conclusion := ⟨0, 0, 0, 1, 0⟩

/-- Nonnegative-combination witness for `initial.input_before_capacity`. -/
def cert_initial_input_before_capacity : Certificate where
  weights := [1, 0]
  nonnegativeConstant := 0

/-- Obligation `initial.capacity_representable`. -/
def rule_initial_capacity_representable : Rule where
  name := "initial.capacity_representable"
  assumptions := [⟨0, 0, 0, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩]
  conclusion := ⟨0, 0, 0, -1, 1⟩

/-- Nonnegative-combination witness for `initial.capacity_representable`. -/
def cert_initial_capacity_representable : Certificate where
  weights := [0, 1]
  nonnegativeConstant := 0

/-- Obligation `store.nonnegative`. -/
def rule_store_nonnegative : Rule where
  name := "store.nonnegative"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨0, 1, 0, 0, 0⟩

/-- Nonnegative-combination witness for `store.nonnegative`. -/
def cert_store_nonnegative : Certificate where
  weights := [1, 0, 0, 0, 0]
  nonnegativeConstant := 0

/-- Obligation `store.strictly_below_capacity`. -/
def rule_store_strictly_below_capacity : Rule where
  name := "store.strictly_below_capacity"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨-1, -1, 0, 1, 0⟩

/-- Nonnegative-combination witness for `store.strictly_below_capacity`. -/
def cert_store_strictly_below_capacity : Certificate where
  weights := [0, 1, 0, 0, 1]
  nonnegativeConstant := 0

/-- Obligation `emit.cursor_increment_fits`. -/
def rule_emit_cursor_increment_fits : Rule where
  name := "emit.cursor_increment_fits"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨-1, -1, 0, 0, 1⟩

/-- Nonnegative-combination witness for `emit.cursor_increment_fits`. -/
def cert_emit_cursor_increment_fits : Certificate where
  weights := [0, 1, 0, 1, 1]
  nonnegativeConstant := 0

/-- Obligation `step.input_increment_fits`. -/
def rule_step_input_increment_fits : Rule where
  name := "step.input_increment_fits"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨-1, 0, -1, 0, 1⟩

/-- Nonnegative-combination witness for `step.input_increment_fits`. -/
def cert_step_input_increment_fits : Certificate where
  weights := [0, 0, 0, 1, 1]
  nonnegativeConstant := 0

/-- Obligation `emit.invariant.0`. -/
def rule_emit_invariant_0 : Rule where
  name := "emit.invariant.0"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨1, 1, 0, 0, 0⟩

/-- Nonnegative-combination witness for `emit.invariant.0`. -/
def cert_emit_invariant_0 : Certificate where
  weights := [1, 0, 0, 0, 0]
  nonnegativeConstant := 1

/-- Obligation `emit.invariant.1`. -/
def rule_emit_invariant_1 : Rule where
  name := "emit.invariant.1"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨0, -1, 1, 0, 0⟩

/-- Nonnegative-combination witness for `emit.invariant.1`. -/
def cert_emit_invariant_1 : Certificate where
  weights := [0, 1, 0, 0, 0]
  nonnegativeConstant := 0

/-- Obligation `emit.invariant.2`. -/
def rule_emit_invariant_2 : Rule where
  name := "emit.invariant.2"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨-1, 0, -1, 1, 0⟩

/-- Nonnegative-combination witness for `emit.invariant.2`. -/
def cert_emit_invariant_2 : Certificate where
  weights := [0, 0, 0, 0, 1]
  nonnegativeConstant := 0

/-- Obligation `emit.invariant.3`. -/
def rule_emit_invariant_3 : Rule where
  name := "emit.invariant.3"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨0, 0, 0, -1, 1⟩

/-- Nonnegative-combination witness for `emit.invariant.3`. -/
def cert_emit_invariant_3 : Certificate where
  weights := [0, 0, 0, 1, 0]
  nonnegativeConstant := 0

/-- Obligation `skip.invariant.0`. -/
def rule_skip_invariant_0 : Rule where
  name := "skip.invariant.0"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨0, 1, 0, 0, 0⟩

/-- Nonnegative-combination witness for `skip.invariant.0`. -/
def cert_skip_invariant_0 : Certificate where
  weights := [1, 0, 0, 0, 0]
  nonnegativeConstant := 0

/-- Obligation `skip.invariant.1`. -/
def rule_skip_invariant_1 : Rule where
  name := "skip.invariant.1"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨1, -1, 1, 0, 0⟩

/-- Nonnegative-combination witness for `skip.invariant.1`. -/
def cert_skip_invariant_1 : Certificate where
  weights := [0, 1, 0, 0, 0]
  nonnegativeConstant := 1

/-- Obligation `skip.invariant.2`. -/
def rule_skip_invariant_2 : Rule where
  name := "skip.invariant.2"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨-1, 0, -1, 1, 0⟩

/-- Nonnegative-combination witness for `skip.invariant.2`. -/
def cert_skip_invariant_2 : Certificate where
  weights := [0, 0, 0, 0, 1]
  nonnegativeConstant := 0

/-- Obligation `skip.invariant.3`. -/
def rule_skip_invariant_3 : Rule where
  name := "skip.invariant.3"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩, ⟨-1, 0, -1, 1, 0⟩]
  conclusion := ⟨0, 0, 0, -1, 1⟩

/-- Nonnegative-combination witness for `skip.invariant.3`. -/
def cert_skip_invariant_3 : Certificate where
  weights := [0, 0, 0, 1, 0]
  nonnegativeConstant := 0

/-- Obligation `exit.output_count_bounded`. -/
def rule_exit_output_count_bounded : Rule where
  name := "exit.output_count_bounded"
  assumptions := [⟨0, 1, 0, 0, 0⟩, ⟨0, -1, 1, 0, 0⟩, ⟨0, 0, -1, 1, 0⟩, ⟨0, 0, 0, -1, 1⟩]
  conclusion := ⟨0, -1, 0, 1, 0⟩

/-- Nonnegative-combination witness for `exit.output_count_bounded`. -/
def cert_exit_output_count_bounded : Certificate where
  weights := [0, 1, 1, 0]
  nonnegativeConstant := 0

/-- Every exported (rule, certificate) pair, in `collector_rules()` order. -/
def certificates : List (Rule × Certificate) :=
  [
    (rule_initial_nonnegative, cert_initial_nonnegative),
    (rule_initial_cursor_before_input, cert_initial_cursor_before_input),
    (rule_initial_input_before_capacity, cert_initial_input_before_capacity),
    (rule_initial_capacity_representable, cert_initial_capacity_representable),
    (rule_store_nonnegative, cert_store_nonnegative),
    (rule_store_strictly_below_capacity, cert_store_strictly_below_capacity),
    (rule_emit_cursor_increment_fits, cert_emit_cursor_increment_fits),
    (rule_step_input_increment_fits, cert_step_input_increment_fits),
    (rule_emit_invariant_0, cert_emit_invariant_0),
    (rule_emit_invariant_1, cert_emit_invariant_1),
    (rule_emit_invariant_2, cert_emit_invariant_2),
    (rule_emit_invariant_3, cert_emit_invariant_3),
    (rule_skip_invariant_0, cert_skip_invariant_0),
    (rule_skip_invariant_1, cert_skip_invariant_1),
    (rule_skip_invariant_2, cert_skip_invariant_2),
    (rule_skip_invariant_3, cert_skip_invariant_3),
    (rule_exit_output_count_bounded, cert_exit_output_count_bounded)
  ]

/-- The exported bundle has the expected size; a dropped rule fails here. -/
theorem certificates_length : certificates.length = 17 := by decide

/-- **Every exported certificate is accepted by the checker**, by kernel
computation over exact integers (no `native_decide`). -/
theorem all_checked : certificates.all (fun rc => check rc.1 rc.2) = true := by decide

/-- `initial.nonnegative`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_initial_nonnegative : ∀ K I N M : Int,
    satisfies K I N M rule_initial_nonnegative.assumptions →
      0 ≤ rule_initial_nonnegative.conclusion.eval K I N M :=
  check_sound' (r := rule_initial_nonnegative) (c := cert_initial_nonnegative) (by decide)

/-- `initial.cursor_before_input`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_initial_cursor_before_input : ∀ K I N M : Int,
    satisfies K I N M rule_initial_cursor_before_input.assumptions →
      0 ≤ rule_initial_cursor_before_input.conclusion.eval K I N M :=
  check_sound' (r := rule_initial_cursor_before_input) (c := cert_initial_cursor_before_input) (by decide)

/-- `initial.input_before_capacity`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_initial_input_before_capacity : ∀ K I N M : Int,
    satisfies K I N M rule_initial_input_before_capacity.assumptions →
      0 ≤ rule_initial_input_before_capacity.conclusion.eval K I N M :=
  check_sound' (r := rule_initial_input_before_capacity) (c := cert_initial_input_before_capacity) (by decide)

/-- `initial.capacity_representable`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_initial_capacity_representable : ∀ K I N M : Int,
    satisfies K I N M rule_initial_capacity_representable.assumptions →
      0 ≤ rule_initial_capacity_representable.conclusion.eval K I N M :=
  check_sound' (r := rule_initial_capacity_representable) (c := cert_initial_capacity_representable) (by decide)

/-- `store.nonnegative`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_store_nonnegative : ∀ K I N M : Int,
    satisfies K I N M rule_store_nonnegative.assumptions →
      0 ≤ rule_store_nonnegative.conclusion.eval K I N M :=
  check_sound' (r := rule_store_nonnegative) (c := cert_store_nonnegative) (by decide)

/-- `store.strictly_below_capacity`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_store_strictly_below_capacity : ∀ K I N M : Int,
    satisfies K I N M rule_store_strictly_below_capacity.assumptions →
      0 ≤ rule_store_strictly_below_capacity.conclusion.eval K I N M :=
  check_sound' (r := rule_store_strictly_below_capacity) (c := cert_store_strictly_below_capacity) (by decide)

/-- `emit.cursor_increment_fits`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_emit_cursor_increment_fits : ∀ K I N M : Int,
    satisfies K I N M rule_emit_cursor_increment_fits.assumptions →
      0 ≤ rule_emit_cursor_increment_fits.conclusion.eval K I N M :=
  check_sound' (r := rule_emit_cursor_increment_fits) (c := cert_emit_cursor_increment_fits) (by decide)

/-- `step.input_increment_fits`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_step_input_increment_fits : ∀ K I N M : Int,
    satisfies K I N M rule_step_input_increment_fits.assumptions →
      0 ≤ rule_step_input_increment_fits.conclusion.eval K I N M :=
  check_sound' (r := rule_step_input_increment_fits) (c := cert_step_input_increment_fits) (by decide)

/-- `emit.invariant.0`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_emit_invariant_0 : ∀ K I N M : Int,
    satisfies K I N M rule_emit_invariant_0.assumptions →
      0 ≤ rule_emit_invariant_0.conclusion.eval K I N M :=
  check_sound' (r := rule_emit_invariant_0) (c := cert_emit_invariant_0) (by decide)

/-- `emit.invariant.1`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_emit_invariant_1 : ∀ K I N M : Int,
    satisfies K I N M rule_emit_invariant_1.assumptions →
      0 ≤ rule_emit_invariant_1.conclusion.eval K I N M :=
  check_sound' (r := rule_emit_invariant_1) (c := cert_emit_invariant_1) (by decide)

/-- `emit.invariant.2`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_emit_invariant_2 : ∀ K I N M : Int,
    satisfies K I N M rule_emit_invariant_2.assumptions →
      0 ≤ rule_emit_invariant_2.conclusion.eval K I N M :=
  check_sound' (r := rule_emit_invariant_2) (c := cert_emit_invariant_2) (by decide)

/-- `emit.invariant.3`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_emit_invariant_3 : ∀ K I N M : Int,
    satisfies K I N M rule_emit_invariant_3.assumptions →
      0 ≤ rule_emit_invariant_3.conclusion.eval K I N M :=
  check_sound' (r := rule_emit_invariant_3) (c := cert_emit_invariant_3) (by decide)

/-- `skip.invariant.0`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_skip_invariant_0 : ∀ K I N M : Int,
    satisfies K I N M rule_skip_invariant_0.assumptions →
      0 ≤ rule_skip_invariant_0.conclusion.eval K I N M :=
  check_sound' (r := rule_skip_invariant_0) (c := cert_skip_invariant_0) (by decide)

/-- `skip.invariant.1`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_skip_invariant_1 : ∀ K I N M : Int,
    satisfies K I N M rule_skip_invariant_1.assumptions →
      0 ≤ rule_skip_invariant_1.conclusion.eval K I N M :=
  check_sound' (r := rule_skip_invariant_1) (c := cert_skip_invariant_1) (by decide)

/-- `skip.invariant.2`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_skip_invariant_2 : ∀ K I N M : Int,
    satisfies K I N M rule_skip_invariant_2.assumptions →
      0 ≤ rule_skip_invariant_2.conclusion.eval K I N M :=
  check_sound' (r := rule_skip_invariant_2) (c := cert_skip_invariant_2) (by decide)

/-- `skip.invariant.3`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_skip_invariant_3 : ∀ K I N M : Int,
    satisfies K I N M rule_skip_invariant_3.assumptions →
      0 ≤ rule_skip_invariant_3.conclusion.eval K I N M :=
  check_sound' (r := rule_skip_invariant_3) (c := cert_skip_invariant_3) (by decide)

/-- `exit.output_count_bounded`: the certified affine implication, for every integer
assignment of `K I N M` that satisfies the rule's assumptions. -/
theorem obligation_exit_output_count_bounded : ∀ K I N M : Int,
    satisfies K I N M rule_exit_output_count_bounded.assumptions →
      0 ≤ rule_exit_output_count_bounded.conclusion.eval K I N M :=
  check_sound' (r := rule_exit_output_count_bounded) (c := cert_exit_output_count_bounded) (by decide)

end Collector
end Cairn
