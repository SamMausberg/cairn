PYTHON ?= python3

.PHONY: check test native wheel audit demo
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
