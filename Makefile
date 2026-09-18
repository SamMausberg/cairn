PYTHON ?= python3
CAIRN = $(PYTHON) bin/cairn

.PHONY: all check lint format test native systems proof lean gpu embedded context wheel audit demo
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
	$(PYTHON) tools/verify.py --gcc --sanitize
	$(PYTHON) bench/codegen_only.py

systems:
	$(PYTHON) tools/validate_systems.py
	$(CAIRN) run examples/systems
	$(CAIRN) test examples/systems

proof: lean
	$(CAIRN) certificates
	$(CAIRN) verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all

lean:
	$(PYTHON) tools/export_lean_certificates.py --check
	cd proofs && lake build

gpu:
	$(PYTHON) -m pytest -q tests/test_native_runtime.py tests/test_concurrency.py
	$(PYTHON) bench/parallel_gpu.py

embedded:
	$(PYTHON) -m pytest -q tests/test_freestanding.py

context:
	$(PYTHON) tools/measure_context.py

wheel:
	$(PYTHON) -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .

audit:
	$(PYTHON) tools/audit_repository.py

demo:
	$(CAIRN) run examples/hello
	$(CAIRN) test examples/hello
