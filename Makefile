PYTHON ?= python3
CAIRN = $(PYTHON) bin/cairn

.PHONY: help docs editors all check lint format test native systems proof lean gpu tune-device calibrate-device device-build embedded context wheel audit demo demo-repair demo-numeric demo-visual demo-implement bench scale
all: lint test proof

help:
	@echo 'all       lint, the test suite and the proofs'
	@echo 'check     cairn check examples/hello, the quickest sign the checkout works'
	@echo 'lint      ruff format --check, ruff check, mypy, cairn fmt --check'
	@echo 'format    rewrite Python and CAIRN sources in place'
	@echo 'test      the whole suite in parallel; tool-dependent parts skip with a reason'
	@echo 'native    both compilers with sanitizers, and the codegen comparison'
	@echo 'systems   the systems examples against independent oracles'
	@echo 'proof     certificates, the Lean build, the differential run, scalar module equivalence'
	@echo 'lean      the Lean half of proof alone'
	@echo 'gpu       runs device code: CUDA runtime, lanes, device plans, apps, the device benchmark'
	@echo 'tune-device      times device plans: FILE=... SYMBOL=... AT=n=1e7 (runs device code)'
	@echo 'calibrate-device measures the device into results/perf_model/device.json (runs device code)'
	@echo 'device-build  every test that compiles device code, where nvcc is installed; nothing runs on a device'
	@echo 'embedded  the freestanding image under QEMU (needs an AArch64 host)'
	@echo 'bench     the preregistered CPU baseline suite (hours)'
	@echo 'scale     check, build, rebuild and editor times of generated projects of 10k to 77k lines (an hour)'
	@echo 'docs      regenerate docs/std_api.md, docs/std/ and the capability matrix in docs/verification.md'
	@echo 'editors   regenerate the TextMate and Vim grammars and the agent skill from the compiler'
	@echo 'context   measure edit packets, cards and refusals in tokens, on scripted transcripts'
	@echo 'wheel     build the package offline into dist/'
	@echo 'audit     scan the committed history for credentials and binaries'
	@echo 'demo      run and test examples/hello'
	@echo 'demo-repair   an agent fixes a bug through the edit host, then cairn diff reviews it (demos/repair)'
	@echo 'demo-numeric  the plate on the host lanes against f64 and a C++ loop (demos/numeric)'
	@echo 'demo-visual   an agent sees a layout defect in a cairn shot and fixes it (demos/visual)'
	@echo 'demo-implement  an agent writes faster implementations; validation refuses one, cairn tune times the rest (demos/implement)'

check:
	$(CAIRN) check examples/hello

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy src/cairn
	$(CAIRN) fmt --check examples demos src/cairn/std src/cairn/templates

format:
	$(PYTHON) -m ruff format .
	$(CAIRN) fmt examples demos src/cairn/std src/cairn/templates

test:
	$(PYTHON) -m pytest -q tests -n auto

native:
	$(PYTHON) tools/checks/verify.py --gcc --sanitize
	$(PYTHON) bench/codegen/codegen_only.py

# The preregistered CPU baseline suite: bench/suite/PREREGISTRATION.md fixes what it measures before
# it runs. It writes under results/, never under evidence/, and nothing when a case disagrees.
bench:
	$(PYTHON) bench/suite/harness.py
	$(PYTHON) bench/suite/report.py

# How the tools grow with a project's size: bench/scale/measure.py writes results/scale/measure.json and prints a table.
scale:
	$(PYTHON) bench/scale/measure.py --modules 185 370 740 1480 --native 185 370 740 1480

systems:
	$(PYTHON) tools/checks/validate_systems.py
	$(CAIRN) run examples/systems
	$(CAIRN) test examples/systems

proof: lean
	$(CAIRN) certificates
	$(CAIRN) verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all

lean:
	$(PYTHON) tools/checks/export_lean_certificates.py --check
	cd proofs && PATH="$$HOME/.elan/bin:$$PATH" lake build
	$(PYTHON) tools/checks/differential_ownership.py --count 200
	$(PYTHON) tools/checks/differential_facts.py --count 2000
	$(PYTHON) tools/checks/differential_cooperative.py --count 1000
	$(PYTHON) tools/checks/differential_layouts.py --count 1000

# The device test run, in one process, one device run at a time (tools/support.py: device_reason, device_lock).
# It and the two targets after it are the only ones that run code on a CUDA device; everything else leaves it alone.
gpu:
	CAIRN_GPU_TESTS=1 $(PYTHON) -m pytest -q -p no:xdist tests/runtime/test_native_runtime.py \
	  tests/soundness/test_concurrency.py tests/soundness/test_plans.py tests/soundness/test_scan.py \
	  tests/soundness/test_device_paths.py tests/soundness/test_staging.py tests/soundness/test_tensor.py \
	  tests/projects/test_apps.py tests/projects/test_app_matmul.py \
	  tests/projects/test_app_analytics.py tests/projects/test_demos.py tests/verification/test_device_validation.py \
	  tests/projects/test_foreign.py
	CAIRN_GPU_TESTS=1 $(PYTHON) bench/gpu/parallel_gpu.py

# The two other targets that run device code. Only the owner runs them, never while anything else uses the device:
# each device run holds /tmp/cairn-gpu.lock, rests two seconds after, and one process makes at most 64 of them.
tune-device:
	CAIRN_GPU_TESTS=1 $(CAIRN) tune $(FILE) --symbol $(SYMBOL) --at $(AT) --measure 3 --device --format json

calibrate-device:
	CAIRN_GPU_TESTS=1 PYTHONPATH=src $(PYTHON) -m cairn.perf.on_device --out results/perf_model/device.json

# Every test that needs nvcc, run where nvcc is installed; CAIRN_GPU_TESTS stays unset, so each device run skips and
# each device build happens. NVCC_HOST is nvcc's host compiler for the builds a test does not name one for
# (tests/emitted.py); CI runs this under g++ and under clang++. tests/tooling/test_workflow.py holds this list to
# every module that builds device code or skips without nvcc.
NVCC_HOST ?= g++
DEVICE_TESTS = tests/language/test_assembly.py tests/language/test_assert_eq.py tests/language/test_gradients.py \
  tests/language/test_layouts.py tests/language/test_storage_floats.py \
  tests/projects/test_app_matmul.py tests/projects/test_cooperative_examples.py tests/projects/test_demos.py \
  tests/projects/test_emulation.py::test_the_device_command_is_unchanged_when_emulation_is_off \
  tests/projects/test_export.py \
  tests/projects/test_foreign.py tests/projects/test_test_blocks.py \
  tests/runtime/test_enqueue.py tests/runtime/test_execution.py tests/runtime/test_native_runtime.py \
  tests/soundness/test_cooperative.py tests/soundness/test_device_paths.py tests/soundness/test_fragments.py \
  tests/soundness/test_fusion.py tests/soundness/test_pipelines.py tests/soundness/test_plans.py \
  tests/soundness/test_scan.py tests/soundness/test_staging.py tests/soundness/test_tensor.py \
  tests/soundness/test_tensor_kernels.py \
  tests/tooling/test_device_cards.py tests/tooling/test_feedback.py tests/tooling/test_on_device.py \
  tests/tooling/test_predict.py tests/tooling/test_predict_cooperative.py tests/tooling/test_search.py \
  tests/tooling/test_search_instances.py tests/tooling/test_target.py \
  tests/tooling/test_tools.py::test_parallel_gpu_flags tests/tooling/test_tools.py::test_device_examples \
  tests/verification/test_device_validation.py
device-build:
	CAIRN_TEST_NVCC_HOST=$(NVCC_HOST) $(PYTHON) -m pytest -q -n 4 $(DEVICE_TESTS)

embedded:
	$(PYTHON) -m pytest -q tests/projects/test_freestanding.py

docs:
	$(CAIRN) doc --std --pages docs --format json > /dev/null
	$(PYTHON) tools/release/capability_matrix.py

editors:
	PYTHONPATH=src $(PYTHON) -m cairn.editor.grammar
	PYTHONPATH=src $(PYTHON) -m cairn.agent.skill

context:
	$(PYTHON) tools/ai/measure_context.py

wheel:
	$(PYTHON) -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .

audit:
	$(PYTHON) tools/release/audit_repository.py

demo:
	$(CAIRN) run examples/hello
	$(CAIRN) test examples/hello

demo-repair:
	$(PYTHON) demos/repair/run.py

demo-numeric:
	$(PYTHON) demos/numeric/run.py

demo-visual:
	$(PYTHON) demos/visual/run.py

demo-implement:
	$(PYTHON) demos/implement/run.py
