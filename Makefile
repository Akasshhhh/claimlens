PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: install test run verify hash

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip==25.0.1
	$(PIP) install -r requirements.lock

test:
	$(PYTHON) -m pytest -q

run:
	$(PYTHON) -m uvicorn claimlens.api.app:app --host 127.0.0.1 --port 8000

verify:
	$(PYTHON) -m claimlens.runner.verify

hash:
	$(PYTHON) -m claimlens.tools.hash_inputs
