# VCF Operations Open Telegraf Helper

A desktop app that guides you through installing and configuring open-source Telegraf agents for VMware Cloud Foundation (VCF) Operations 9.1.

## Why this exists

VCF Operations supports collecting telemetry from open-source Telegraf agents on Linux and Windows systems. However, configuring this manually requires coordinating multiple disparate steps:
* Generating VCF Operations auth tokens via the Suite API.
* Configuring Cloud Proxy HTTP outputs with specific Wavefront metrics formats and headers.
* Authoring valid Telegraf TOML input configurations aligned with Broadcom metric schemas.
* Validating configuration syntax before restarting services.
* Safely restarting services without disrupting existing monitoring.

This helper compresses those manual steps into a fast, guided, transparent, and repeatable process on your local workstation.

## Get started

**[Download the latest release](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest), then launch the app.** No Python installation is required.

| Your desktop | Download | Launch |
| --- | --- | --- |
| Windows | [Windows app (.exe)](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest/download/vcf-telegraf-helper-windows.exe) | Double-click the executable. |
| Mac, Apple Silicon | [Apple Silicon app (.zip)](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest/download/vcf-telegraf-helper-macos-arm64.zip) | Extract the ZIP, move **VCF Telegraf Helper.app** to **Applications**, then double-click the app. |
| Mac, Intel | [Intel Mac app (.zip)](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest/download/vcf-telegraf-helper-macos-x86_64.zip) | Extract the ZIP, move **VCF Telegraf Helper.app** to **Applications**, then double-click the app. |
| Linux desktop | [Linux executable](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest/download/vcf-telegraf-helper-linux) | Mark the file executable, then run it with `gui` to open the app. |

On a Mac, check **Apple menu > About This Mac** if you are unsure which download to choose. Keep the whole app together when moving it. The Mac ZIP/app instructions apply from v0.7.5 onward; older releases contain bare executables.

Windows releases are Authenticode signed. Mac apps are Developer ID signed, notarized, and stapled. Windows SmartScreen may still show an unrecognized-app prompt while reputation builds, and macOS may ask you to confirm opening a downloaded app. Your organization's application-control policies still apply. PR artifacts are unsigned development builds.

### Have these ready

- Your VCF Operations URL and an API token, or a username and password.
- The Cloud Proxy or collector group that should receive the agent's metrics.
- Guest credentials for the target VM: SSH with root or passwordless sudo for Linux, or WinRM with administrative rights for Windows.
- Any enterprise CA bundle needed to trust your Ops server.

### Follow the six steps in the app

1. **Connect:** enter the Ops connection details and validate them.
2. **Select VM:** pick the VM from inventory. All agent states are shown by default; resize columns as needed.
3. **Configure Target VM:** enter guest credentials, select the collector, and detect the endpoint. A missing agent defaults to the latest recommended version; an installed agent defaults to keeping it.
4. **Monitoring Inputs:** choose the metrics you want. Additional Windows Perfmon counters appear in the same panel. The Windows baseline includes **Windows OS Totals** (cpu, mem and swap with the `win.` prefix), the same inputs the Ops-managed agent collects, so the Windows OS object carries its full stat-key set. Re-running an existing Windows endpoint with **Replace existing helper inputs** adds them; if you added that fragment by hand as custom TOML, remove it to avoid collecting twice.
5. **Review & Preview:** inspect the offline configuration template.
6. **Execute & Verify:** use dry-run to inspect the exact prepared configuration, then apply it. After a successful apply, exit or go back to revise options and run again.

On Windows, desktop launch hides the executable's classic Command Prompt window. Commands launched in an existing terminal retain their output.

A small **New version available** link beneath the app name opens the newer release page. The app checks GitHub in the background at most once a day, with no login or Ops credentials. Offline or failed checks stay silent.

The app shows progress while connecting and querying. Dry-run leaves endpoint files unchanged, but preparation may request a client certificate from Ops. Fresh agent data can take a collection cycle to appear; a running service alone does not establish ingestion.

**Already running an Ops-managed agent?** The VM list marks it as "Reporting, Ops managed", and detecting the endpoint shows the managed services instead of letting ordinary onboarding continue. On Windows you can check **Take over existing Ops agent**: the helper captures the managed configuration and the Ops object, backs the configuration up on your workstation, asks VCF Operations to uninstall its agent (with the guest credential you entered, as typed), verifies the endpoint is clean, installs and enrolls open-source Telegraf with the same inputs, and then confirms that the same Ops object flipped to Open Source and is receiving samples. The Monitoring Inputs step starts from the imported configuration; the Baseline preset discards it. Executing asks you to type the VM name. Expect a monitoring gap of about 15 minutes between the Ops uninstall and the first open-source sample. If the app closes after the managed agent was retired, detect the endpoint again and the helper offers to resume from its journal (under the app's config directory, never holding passwords or keys); if it closes before that, nothing was changed and you start over. Linux takeover is not supported yet. From the CLI: `run ... --vm-id <mor> [--vc-id <vcenter uuid>] --vm-name <name> --take-over-managed-agent --confirm-takeover <name>`; input flags other than workload additions are refused in takeover mode, and the exit code is 3 when VCF Operations created a different object.

## Verify downloads (optional)

Each release includes `SHA256SUMS`. Download it alongside your chosen asset and verify the checksum before running it. On macOS, from the download directory:

```bash
shasum -a 256 -c SHA256SUMS --ignore-missing
```

Confirm that your selected ZIP reports `OK`. On Linux, use `sha256sum --check SHA256SUMS --ignore-missing`. On Windows, use `Get-FileHash .\vcf-telegraf-helper-windows.exe -Algorithm SHA256` and compare it with the matching entry in `SHA256SUMS`.

Uploaded release assets carry GitHub build provenance attestations from v0.7.5 onward. This covers binaries, Python packages, and `SHA256SUMS`; GitHub's automatically generated “Source code” archives are not covered. With GitHub CLI installed, verify an uploaded asset came from this repository's build:

```bash
gh attestation verify <file> --repo sentania-labs/vcf-ops-telegraf-helper
```

## Optional command-line use

The app includes a CLI for administrators who want repeatable commands or a terminal workflow. The GUI also displays a command for repeating the selected workflow.

On Windows, invoke the downloaded executable with a command. On Linux, use `./vcf-telegraf-helper-linux`. On macOS, use the executable inside the app:

```bash
"/Applications/VCF Telegraf Helper.app/Contents/MacOS/vcf-telegraf-helper" --help
"/Applications/VCF Telegraf Helper.app/Contents/MacOS/vcf-telegraf-helper" render --cpu --mem
```

The examples below use `vcf-telegraf-helper` as shorthand for your platform's executable, or the command installed with the Python package.

```bash
# Optional interactive terminal workflow
vcf-telegraf-helper wizard

# Preview TOML without connecting to a target
vcf-telegraf-helper render --cpu --mem --disk --net

# Configure a Linux target using explicit options
vcf-telegraf-helper run \
  --vcf-url https://vcf-ops.corp.local \
  --collector 10.10.10.50 \
  --target-host 10.10.20.101 \
  --ssh-user operator \
  --connection ssh \
  --preview \
  --export-md summary.md
```

For an optional Mac CLI symlink:

```bash
mkdir -p "$HOME/.local/bin" && ln -s "/Applications/VCF Telegraf Helper.app/Contents/MacOS/vcf-telegraf-helper" "$HOME/.local/bin/vcf-telegraf-helper"
```

Add `$HOME/.local/bin` to your shell's `PATH` if needed. The command refuses to replace an existing link or program. Moving the app later requires updating the symlink.

## Existing configuration and trust

Reruns preserve deployed helper inputs unless you select **Replace existing inputs** (`--replace-inputs` in the CLI). Other configuration fragments are retained. Unrelated inputs in the main configuration require review before onboarding. The Baseline preset resets added Perfmon selections.

Desktop API trust uses the operating system certificate store, including Windows and macOS. An enterprise CA bundle affects desktop-to-Ops trust. Agent-to-collector TLS is a separate setting, enabled by default, using the collector CA deployed on the target.

## Product Principles

* **Follows Broadcom Documentation**: Uses the official VCF Operations 9.1 open-source Telegraf workflow. See [docs/references.md](docs/references.md).
* **Local-First Helper**: Behaves like an administrator utility (such as `vcf-cf-migrator`). No background daemons, databases, or centralized server infrastructure.
* **Safe Changes (Low Blast Radius)**: Generates isolated configuration fragments in `/etc/telegraf/telegraf.d/` (`vcf-helper-system.conf` and `cloudproxy-http.conf`). Existing helper input selections and unrelated fragments are retained by default; replacing helper inputs is an explicit choice. Helper-managed collector output and security files are updated for the selected integration. A main configuration containing unrelated inputs requires review before onboarding.
* **Idempotent and Drift-Free**: Re-running configuration against an existing target produces deterministic files and reports whether updates were needed.
* **Transparent**: The app offers configuration previews and dry-run so you can inspect the prepared configuration before applying it.
* **Honest Validation**: Separately validates configuration syntax, endpoint reachability, collector connectivity, and service state. Failures in one layer are never masked.
* **Direct Push**: Deploys over SSH (Linux) or WinRM (Windows). The GUI shows the equivalent `vcf-telegraf-helper run` command for anyone who needs to adapt it.
* **Inventory-Driven Targeting**: Pick the VM from VCF Operations inventory (templates and deleted VMs excluded, powered-off VMs hidden by default). Guest OS, IP, agent status, and the agent's current collector come from VCF Operations, and each step unlocks only when the previous one is complete.

## Python and development

Python wheels and source distributions are secondary artifacts. The signed standalone app is the usual desktop entry point. For development:

```bash
git clone https://github.com/sentania-labs/vcf-ops-telegraf-helper.git
cd vcf-ops-telegraf-helper
python -m pip install -e '.[gui]'
python -m vcf_ops_telegraf_helper gui
```

On managed Windows, the pip-generated launcher is unsigned and may be blocked by Defender ASR; invoke `python -m vcf_ops_telegraf_helper gui` when using the Python installation.

## Documentation

* [docs/agent-takeover.md](docs/agent-takeover.md): Taking over an Ops-managed agent: what the helper does and refuses, the seven stages, resume, lab evidence, and the validation list for the test build.
* [docs/architecture.md](docs/architecture.md): Architectural design, boundaries, and safety models.
* [docs/supported-workflow.md](docs/supported-workflow.md): Detailed comparison against Broadcom's documented procedure.
* [docs/references.md](docs/references.md): Direct links to authoritative Broadcom technical documentation.
* [docs/development.md](docs/development.md): Development setup, testing, and linting guidelines.

## License

Apache-2.0
