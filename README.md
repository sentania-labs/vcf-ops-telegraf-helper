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
* **Direct Push**: Deploys over SSH (Linux) or WinRM (Windows). The GUI shows the equivalent `vcf-telegraf-helper run` command for anyone who needs to adapt it.
* **Inventory-Driven Targeting**: Pick the VM from VCF Operations inventory (templates and deleted VMs excluded, powered-off VMs hidden by default). Guest OS, IP, agent status, and the agent's current collector come from VCF Operations, and each step unlocks only when the previous one is complete.

## Installation

Download the standalone binary for your desktop from [Releases](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases). These are the primary artifacts: Windows Authenticode signed, macOS Apple Silicon and Intel apps that are Developer ID signed, notarized and stapled, plus a Linux executable. No Python installation is required. Each release includes `SHA256SUMS` for the final downloads.

Windows SmartScreen may show an unrecognized-app prompt on early signed releases while reputation builds. Organizational application-control policies still apply.

### macOS

Choose `vcf-telegraf-helper-macos-arm64.zip` for **Apple Silicon** or
`vcf-telegraf-helper-macos-x86_64.zip` for **Intel** (Apple menu > About This Mac).
Download `SHA256SUMS` from the same release. In Terminal, from the download directory,
verify the selected archive before extracting it:

```bash
shasum -a 256 -c SHA256SUMS --ignore-missing
```

Confirm that your selected ZIP reports `OK`. Double-click the ZIP, move
**VCF Telegraf Helper.app** to **Applications**, then double-click the app to open
the wizard. No Python install or Terminal window is needed. The app has a Dock icon;
Quit or close the wizard when finished. Keep the entire app together when moving it.

Release apps are signed and include a stapled Apple notarization ticket. macOS may
still ask you to confirm opening an application downloaded from the internet, and
organizational application-control policies still apply. PR build artifacts are
unsigned development builds and do not have the release's notarization ticket.
These ZIP/app instructions apply to releases containing `.zip` Mac assets; v0.7.4
and earlier provide bare executables instead.

For command-line use, the same app contains the full CLI:

```bash
"/Applications/VCF Telegraf Helper.app/Contents/MacOS/vcf-telegraf-helper" --help
"/Applications/VCF Telegraf Helper.app/Contents/MacOS/vcf-telegraf-helper" render --cpu --mem
```

Optional one-line symlink (no administrator access needed):

```bash
mkdir -p "$HOME/.local/bin" && ln -s "/Applications/VCF Telegraf Helper.app/Contents/MacOS/vcf-telegraf-helper" "$HOME/.local/bin/vcf-telegraf-helper"
```

Add `$HOME/.local/bin` to your shell's `PATH` if it is not already there. The command
refuses to replace an existing link or program. Explicit commands such as `wizard`,
`render`, and `run` retain their usual behavior; invoking the bundled executable
without a command opens the GUI. Moving the app later requires updating the symlink.

### Python and development

Python wheels and source distributions are secondary artifacts. On managed Windows, invoke `python -m vcf_ops_telegraf_helper gui`; the launcher created by pip is unsigned and may be blocked by Defender ASR.

For development:

```bash
git clone https://github.com/sentania-labs/vcf-ops-telegraf-helper.git
cd vcf-ops-telegraf-helper
python -m pip install -e '.[gui]'
```

Desktop API trust uses the operating system certificate store, including Windows and macOS. `--ca-cert` selects a desktop-only CA bundle. Agent trust uses the collector CA deployed on the target; `--no-verify-ssl` does not disable it. Agent TLS has its own `--agent-verify-ssl` setting, enabled by default.

Reruns preserve deployed helper inputs unless `--replace-inputs` is selected. Other configuration fragments are retained. Unmanaged inputs in the main configuration require review before onboarding. Use `--dry-run` to inspect the prepared configuration and input diff without changing endpoint files; preparation may request a certificate from Ops. In the GUI, Step 5 is an offline template and Step 6 dry-run populates the exact prepared configuration. Live ingestion remains pending until Ops returns a sample newer than the run.

The VM picker starts with all agent states and lets you drag column boundaries to resize them.
Target detection selects the latest recommended agent when none is installed and keeps an
existing agent by default. An explicit installation/version choice is retained. Perfmon
selections appear directly in the counter panel; the Baseline preset resets additions.
Connection and discovery queries show an animated waiting dialog. After a successful apply,
you can exit or go back to revise settings and run again; Execute remains disabled until then.

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
  --ssh-user operator \
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
