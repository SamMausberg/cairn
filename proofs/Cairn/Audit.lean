/-
Axiom audit.  `#print axioms` reports, for each named theorem, every axiom its
proof actually depends on.  A `sorry` shows up here as `sorryAx`; `native_decide`
shows up as `Lean.ofReduceBool`.  Neither may appear in the build log.

Build this file and read the log:

    lake build
    lake env lean Cairn/Audit.lean
-/
import Cairn.Affine
import Cairn.CollectorCertificates
import Cairn.Collector

namespace Cairn

/-! ### Checker soundness -/

#print axioms Cairn.check_sound
#print axioms Cairn.check_sound'
#print axioms Cairn.eval_combine_nonneg
#print axioms Cairn.forall_mem_of_satisfies

/-! ### The exported certificate bundle -/

#print axioms Cairn.Collector.all_checked
#print axioms Cairn.Collector.certificates_length

/-! ### The seventeen certified obligations -/

#print axioms Cairn.Collector.obligation_initial_nonnegative
#print axioms Cairn.Collector.obligation_initial_cursor_before_input
#print axioms Cairn.Collector.obligation_initial_input_before_capacity
#print axioms Cairn.Collector.obligation_initial_capacity_representable
#print axioms Cairn.Collector.obligation_store_nonnegative
#print axioms Cairn.Collector.obligation_store_strictly_below_capacity
#print axioms Cairn.Collector.obligation_emit_cursor_increment_fits
#print axioms Cairn.Collector.obligation_step_input_increment_fits
#print axioms Cairn.Collector.obligation_emit_invariant_0
#print axioms Cairn.Collector.obligation_emit_invariant_1
#print axioms Cairn.Collector.obligation_emit_invariant_2
#print axioms Cairn.Collector.obligation_emit_invariant_3
#print axioms Cairn.Collector.obligation_skip_invariant_0
#print axioms Cairn.Collector.obligation_skip_invariant_1
#print axioms Cairn.Collector.obligation_skip_invariant_2
#print axioms Cairn.Collector.obligation_skip_invariant_3
#print axioms Cairn.Collector.obligation_exit_output_count_bounded

/-! ### The invariant, as derived from those obligations -/

#print axioms Cairn.Collector.Inv.init
#print axioms Cairn.Collector.Inv.emit
#print axioms Cairn.Collector.Inv.skip
#print axioms Cairn.Collector.Inv.store_nonneg
#print axioms Cairn.Collector.Inv.store_lt_capacity
#print axioms Cairn.Collector.Inv.emit_increment_fits
#print axioms Cairn.Collector.Inv.step_increment_fits
#print axioms Cairn.Collector.Inv.exit_le_capacity

/-! ### The loop model -/

#print axioms Cairn.Collector.run_preserves_inv
#print axioms Cairn.Collector.run_spec
#print axioms Cairn.Collector.collect_spec
#print axioms Cairn.Collector.store_index_lt_capacity
#print axioms Cairn.Collector.store_index_lt_buffer_length
#print axioms Cairn.Collector.increments_fit

end Cairn
