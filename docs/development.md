# Development Guide

This guide covers local development, testing, and linting for VCF Operations Open Telegraf Helper.

## Prerequisites

* Python 3.10 or later (tested on Python 3.12)
* `pip` and `make`
* Virtual environment or local development setup

## Quickstart

1. Install development dependencies and the package in editable mode:
   ```bash
   pip install -e .
   ```

2. Run automated test suite:
   ```bash
   make test
   ```

3. Run code style and lint checks:
   ```bash
   make lint
   ```

4. Run all checks (matching the standard CI pipeline):
   ```bash
   make check
   ```

## Running the Utility Locally

### Native Desktop GUI (Lattice Design)
Launch the native PySide6 desktop GUI styled with Lattice design tokens:
```bash
# Ensure PySide6 is installed:
pip install -e '.[gui]'

# Launch the desktop window:
vcf-telegraf-helper gui
```

### Guided Wizard Mode
Launch the interactive terminal wizard:
```bash
vcf-telegraf-helper wizard
```

### Direct CLI Execution
Run the workflow using local or mock execution for safe verification:
```bash
vcf-telegraf-helper run \
  --vcf-url https://vcf-ops.example.local \
  --collector 192.168.10.50 \
  --target-host web01.example.local \
  --connection mock \
  --preview \
  --export-md run-summary.md
```

### Inspecting Rendered TOML
Render and print Telegraf TOML fragments directly to the console:
```bash
vcf-telegraf-helper render --cpu --mem --disk --net
```

## Adding New Monitoring Plugins

Input plugins follow a modular structure:
1. Define a Pydantic schema in `vcf_ops_telegraf_helper/models/monitoring.py`.
2. Implement the TOML table generation in `vcf_ops_telegraf_helper/renderer/renderer.py`.
3. Add unit test coverage in `tests/test_renderer.py` asserting correct TOML syntax and round-trip parsing.
4. Expose the plugin options in the CLI and wizard.
