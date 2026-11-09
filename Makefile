PYTHON ?= python3

.PHONY: all lint test check install notices

all: check

lint:
	$(PYTHON) -m ruff check vcf_ops_telegraf_helper tests scripts

test:
	$(PYTHON) -m pytest -v tests

notices:
	$(PYTHON) scripts/generate_notices.py --verify

check: lint test notices

install:
	$(PYTHON) -m pip install --user --break-system-packages --no-deps -e .
