# VCF Operations Open Telegraf Helper

A local administrator utility that simplifies and automates the supported open-source Telegraf onboarding workflow for VMware Cloud Foundation (VCF) Operations 9.1.

## Why this exists

VCF Operations supports collecting telemetry from open-source Telegraf agents on Linux and Windows systems. However, configuring this manually requires coordinating multiple disparate steps:
* Generating VCF Operations auth tokens via the Suite API.
* Configuring Cloud Proxy HTTP outputs with specific Wavefront metrics formats and headers.
* Authoring valid Telegraf TOML input configurations aligned with Broadcom metric schemas.
* Validating configuration syntax before restarting services.
* Safely restarting services without disrupting existing monitoring.

This helper compresses those manual steps into a fast, guided, transparent, and repeatable process on your local workstation.

## Product Principles

* **Follows Broadcom Documentation**: Uses the official VCF Operations 9.1 open-source Telegraf workflow. See [docs/references.md](docs/references.md).
* **Local-First Helper**: Behaves like an administrator utility (such as `vcf-cf-migrator`). No background daemons, databases, or centralized server infrastructure.
* **Safe Changes (Low Blast Radius)**: Generates isolated configuration fragments in `/etc/telegraf/telegraf.d/` (`vcf-helper-system.conf` and `cloudproxy-http.conf`). Existing user or vendor configurations are never overwritten.
* **Idempotent and Drift-Free**: Re-running configuration against an existing target produces deterministic files and reports whether updates were needed.
* **Transparent**: Every generated TOML fragment and planned command is previewed before application.
* **Honest Validation**: Separately validates configuration syntax, endpoint reachability, collector connectivity, and service state. Failures in one layer are never masked.
* **Multiple Deployment Modes**: Supports direct remote push (SSH), auditable shell script generation, and configuration-only output.

## Installation

```bash
git clone https://github.com/example/vcf-ops-telegraf-helper.git
cd vcf-ops-telegraf-helper
pip install -e .
```

## Quick Start

### 1. Native Desktop GUI (Lattice Design)
Launch the native PySide6 desktop helper interface styled with Lattice:
```bash
# Install with optional GUI dependencies:
pip install -e '.[gui]'

# Launch native desktop application:
vcf-telegraf-helper gui
```

### 2. Interactive Terminal Wizard
Step through environment configuration, endpoint detection, monitoring selection, preview, and execution in your terminal:
```bash
vcf-telegraf-helper wizard
```

### 3. Direct CLI Run
Run directly with options, generating a summary report:
```bash
vcf-telegraf-helper run \
  --vcf-url https://vcf-ops.corp.local \
  --collector 10.10.10.50 \
  --target-host 10.10.20.101 \
  --user root \
  --connection ssh \
  --preview \
  --export-md summary.md
```

### 3. Preview Generated TOML
Generate and inspect the Broadcom-recommended OS input configuration:
```bash
vcf-telegraf-helper render --cpu --mem --disk --net
```

## Documentation

* [docs/architecture.md](docs/architecture.md): Architectural design, boundaries, and safety models.
* [docs/supported-workflow.md](docs/supported-workflow.md): Detailed comparison against Broadcom's documented procedure.
* [docs/references.md](docs/references.md): Direct links to authoritative Broadcom technical documentation.
* [docs/development.md](docs/development.md): Development setup, testing, and linting guidelines.

## License

Apache-2.0
