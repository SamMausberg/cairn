/-
Axiom audit.  `#print axioms` reports, for each named theorem, every axiom its
proof actually depends on.  A `sorry` shows up here as `sorryAx`; `native_decide`
shows up as `Lean.ofReduceBool`.  Neither may appear in the build log.

Build this file and read the log:

    lake build
    lake env lean Cairn/Audit.lean
-/
import Cairn.Affine
import Cairn.Places
import Cairn.CollectorCertificates
import Cairn.Collector
import Cairn.Ownership
import Cairn.Region
import Cairn.Facts

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

/-! ### The ownership and lease calculus -/

#print axioms Cairn.Ownership.reaches_sound
#print axioms Cairn.Ownership.ovl_sound
#print axioms Cairn.Ownership.accepted_no_fault
#print axioms Cairn.Ownership.accepted_no_use_after_move
#print axioms Cairn.Ownership.accepted_no_use_after_free
#print axioms Cairn.Ownership.accepted_no_double_free
#print axioms Cairn.Ownership.accepted_race_free
#print axioms Cairn.Ownership.accepted_threads_disjoint
#print axioms Cairn.Ownership.accepted_no_leaked_ticket
#print axioms Cairn.Ownership.accepted_no_use_after_wait
#print axioms Cairn.Ownership.accepted_no_aliased_args
#print axioms Cairn.Ownership.accepted_frees_each_allocation_once
#print axioms Cairn.Ownership.accepted_progress
#print axioms Cairn.Ownership.Ok_succ
#print axioms Cairn.Ownership.Ok_start
#print axioms Cairn.Ownership.Checks.of_accepts
#print axioms Cairn.Ownership.joinOf_le
#print axioms Cairn.Ownership.Sync.weaken
#print axioms Cairn.Ownership.releaseAll_final
#print axioms Cairn.Ownership.lanesOf_pairwise
#print axioms Cairn.Ownership.lane_borrows_dont_race
#print axioms Cairn.Ownership.races_borrow_of_lease
#print axioms Cairn.Ownership.ownership_regression

/-! ### The lane pool's region protocol -/

#print axioms Cairn.Region.Lane.once
#print axioms Cairn.Region.Inv.reach
#print axioms Cairn.Region.runs_once
#print axioms Cairn.Region.quiet_when_back
#print axioms Cairn.Region.finishes

/-! ### The guard-elision rule -/

#print axioms Cairn.Facts.distance_sound
#print axioms Cairn.Facts.bounds_sound
#print axioms Cairn.Facts.index_sound
#print axioms Cairn.Facts.add_sound
#print axioms Cairn.Facts.sub_sound
#print axioms Cairn.Facts.atMostConst_sound
#print axioms Cairn.Facts.part_sound

/-! ### Non-vacuity: the faults are reachable for rejected programs -/

#print axioms Cairn.Ownership.Regress.leasedRead_races
#print axioms Cairn.Ownership.Regress.overlappingTasks_races
#print axioms Cairn.Ownership.Regress.overlappingParts_races
#print axioms Cairn.Ownership.Regress.sameFieldToTwoTasks_races
#print axioms Cairn.Ownership.Regress.fieldPartsOverlapInOneCall_aliases
#print axioms Cairn.Ownership.Regress.laneWritesFixedIndex_races
#print axioms Cairn.Ownership.Regress.laneWritesShared_races
#print axioms Cairn.Ownership.Regress.laneReadsOther_races
#print axioms Cairn.Ownership.Regress.laneTwoStrides_races
#print axioms Cairn.Ownership.meets_own_block
#print axioms Cairn.Ownership.Regress.backwardsPart_traps
#print axioms Cairn.Ownership.Regress.copyAnOwner_doubleFrees
#print axioms Cairn.Ownership.Regress.useAfterDrop_usesDeadPlace
#print axioms Cairn.Ownership.Regress.unawaitedTicket_leaks
#print axioms Cairn.Ownership.Regress.witnesses_are_rejected
#print axioms Cairn.Ownership.Regress.groupNeverWaited_leaks
#print axioms Cairn.Ownership.Regress.collectAfterWait_deadGroup
#print axioms Cairn.Ownership.Regress.groupLeaseFromOnePath_races
#print axioms Cairn.Ownership.Regress.groupFactFromOnePath_races
#print axioms Cairn.Ownership.Regress.group_witnesses_are_rejected

/-! The executable regression the Python gate asserts on. -/

#eval Cairn.Ownership.Regress.line

end Cairn
