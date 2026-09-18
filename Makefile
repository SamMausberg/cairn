PYTHON ?= python3

.PHONY: check test native systems proof context wheel audit demo
check:
	$(PYTHON) bin/cairn check examples/hello

test:
	$(PYTHON) -m pytest -q tests

native:
	$(PYTHON) tools/verify.py --gcc --sanitize
	$(PYTHON) bench/codegen_only.py

wheel:
	$(PYTHON) -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir dist .

audit:
	$(PYTHON) tools/audit_repository.py

demo:
	$(PYTHON) bin/cairn run examples/hello
	$(PYTHON) bin/cairn test examples/hello

systems:
	$(PYTHON) tools/validate_systems.py
	$(PYTHON) bin/cairn run examples/systems
	$(PYTHON) bin/cairn test examples/systems

proof:
	$(PYTHON) bin/cairn certificates
	$(PYTHON) bin/cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all

context:
	$(PYTHON) tools/measure_context.py
