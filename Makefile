PYTHON ?= python3

.PHONY: all lint test check install

all: check

lint:
	$(PYTHON) -m ruff check vcf_ops_telegraf_helper tests

test:
	$(PYTHON) -m pytest -v tests

check: lint test

install:
	$(PYTHON) -m pip install --user --break-system-packages --no-deps -e .
