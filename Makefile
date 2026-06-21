.PHONY: run

VENV := .venv
PY   := $(VENV)/bin/python3

$(VENV):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q rich

run: $(VENV)
	$(PY) runner.py
