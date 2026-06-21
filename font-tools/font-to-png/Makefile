VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

FONT ?= ~/Downloads/bravura-bravura-1.392/redist/otf/Bravura.otf
OUT ?= ./bravura
ARGS ?=
CONFIG ?= config.json

.PHONY: venv install run run-config create-config clean

venv:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run:
	$(PYTHON) font_file_to_png.py $(FONT) --out $(OUT) $(ARGS)

run-config:
	$(PYTHON) font_file_to_png.py --config $(CONFIG)

create-config:
	$(PYTHON) font_file_to_png.py --new-config $(CONFIG)

clean:
	rm -rf $(VENV)
