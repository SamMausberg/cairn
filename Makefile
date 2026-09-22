PYTHON ?= python3
CAIRN = $(PYTHON) bin/cairn

.PHONY: help docs all check lint format test native systems proof lean gpu embedded context wheel audit demo bench
all: lint test proof

help:
	@echo 'all       lint, the test suite and the proofs'
	@echo 'lint      ruff format --check, ruff check, mypy, cairn fmt --check'
	@echo 'format    rewrite Python and CAIRN sources in place'
	@echo 'test      the whole suite in parallel; tool-dependent parts skip with a reason'
	@echo 'native    both compilers with sanitizers, and the codegen comparison'
	@echo 'systems   the systems examples against independent oracles'
	@echo 'proof     certificates, the Lean build, the differential run, scalar module equivalence'
	@echo 'lean      the Lean half of proof alone'
	@echo 'gpu       the only target that runs device code: CUDA runtime, lanes, apps, the device benchmark'
	@echo 'embedded  the freestanding image under QEMU (needs an AArch64 host)'
	@echo 'bench     the preregistered CPU baseline suite (hours)'
	@echo 'docs      regenerate docs/std_api.md'
	@echo 'wheel     build the package offline into dist/'
	@echo 'audit     scan the committed history for credentials and binaries'
	@echo 'demo      run and test examples/hello'

check:
	$(CAIRN) check examples/hello

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy src/cairn
	$(CAIRN) fmt --check examples src/cairn/std

format:
	$(PYTHON) -m ruff format .
	$(CAIRN) fmt examples src/cairn/std

test:
	$(PYTHON) -m pytest -q tests -n auto

native:
	$(PYTHON) tools/checks/verify.py --gcc --sanitize
	$(PYTHON) bench/cpu/codegen_only.py

# The preregistered CPU baseline suite: bench/suite/PREREGISTRATION.md fixes what it measures before
# it runs. It writes under results/, never under evidence/, and nothing when a case disagrees.
bench:
	$(PYTHON) bench/suite/harness.py
	$(PYTHON) bench/suite/report.py

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

# The only target that runs code on a CUDA device, in one process, one device run at a time
# (tools/support.py: device_reason, device_lock). Everything else leaves the device alone.
gpu:
	CAIRN_GPU_TESTS=1 $(PYTHON) -m pytest -q -p no:xdist tests/runtime/test_native_runtime.py \
	  tests/soundness/test_concurrency.py tests/projects/test_apps.py tests/projects/test_app_analytics.py
	CAIRN_GPU_TESTS=1 $(PYTHON) bench/gpu/parallel_gpu.py

embedded:
	$(PYTHON) -m pytest -q tests/projects/test_freestanding.py

docs:
	$(CAIRN) doc --std > docs/std_api.md

context:
	$(PYTHON) tools/ai/measure_context.py

wheel:
	$(PYTHON) -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .

audit:
	$(PYTHON) tools/release/audit_repository.py

demo:
	$(CAIRN) run examples/hello
	$(CAIRN) test examples/hello
