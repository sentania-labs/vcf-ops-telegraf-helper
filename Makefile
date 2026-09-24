.PHONY: all lint test check install

all: check

lint:
	ruff check vcf_ops_telegraf_helper tests

test:
	pytest -v tests

check: lint test

install:
	pip install --user --break-system-packages --no-deps -e .
