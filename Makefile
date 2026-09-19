PYTHON ?= python3
CAIRN = $(PYTHON) bin/cairn

.PHONY: docs all check lint format test native systems proof lean gpu embedded context wheel audit demo bench
all: lint test proof

check:
	$(CAIRN) check examples/hello

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
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

gpu:
	$(PYTHON) -m pytest -q tests/runtime/test_native_runtime.py tests/soundness/test_concurrency.py
	$(PYTHON) bench/gpu/parallel_gpu.py

embedded:
	$(PYTHON) -m pytest -q tests/projects/test_freestanding.py

docs:
	$(CAIRN) doc --std > docs/guide/std_api.md

context:
	$(PYTHON) tools/ai/measure_context.py

wheel:
	$(PYTHON) -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .

audit:
	$(PYTHON) tools/release/audit_repository.py

demo:
	$(CAIRN) run examples/hello
	$(CAIRN) test examples/hello
