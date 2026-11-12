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
| Mac, Apple Silicon | [Apple Silicon app (.dmg)](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest/download/vcf-telegraf-helper-macos-arm64.dmg) | Open the DMG, drag **VCF Telegraf Helper.app** to **Applications**, then double-click the app. |
| Mac, Intel | *Not built for now* | Intel Mac users can install the Python wheel: `pip install 'vcf-ops-telegraf-helper[gui]'` |
| Linux desktop | [Linux executable](https://github.com/sentania-labs/vcf-ops-telegraf-helper/releases/latest/download/vcf-telegraf-helper-linux) | Mark the file executable, then run it with `gui` to open the app. |

On a Mac, check **Apple menu > About This Mac** if you are unsure which download to choose. Keep the whole app together when moving it. The Mac DMG instructions apply from v0.8.8 onward; older releases contain zipped apps or bare executables.

Windows releases are Authenticode signed. Mac apps are Developer ID signed, notarized, and stapled. Windows SmartScreen may still show an unrecognized-app prompt while reputation builds, and macOS may ask you to confirm opening a downloaded app. Your organization's application-control policies still apply. PR artifacts are unsigned development builds.

### Have these ready

- The address of VCF Operations and your user name and password there (or an access token, if you were given one).
- A sign-in for the machines you want to monitor: on Linux the `root` account or one with full `sudo` rights that is not asked for a password, on Windows an administrator account.

That is all. The app finds out the rest for itself: how to reach each machine, what is installed on it, what software is running, which collector it can reach, and which Telegraf version to install.

### Using the app

The app opens on **Connect**: the address, your user name and your password. The address and name are remembered for next time; the password is asked each time and never saved. If VCF Operations uses a certificate your computer does not recognise (normal when a company issues its own), the app shows who the certificate was issued to and by and asks you to trust it once; it then accepts only that certificate and warns you if it ever changes. A certificate file from your IT team works too.

After connecting you land on **Machines**: every machine VCF Operations knows about, each with a plain status (Not set up, Sending data, Set up and waiting for first data, VCF Operations' own agent, Needs attention, Takeover interrupted) and the next step at the end of its row. **Activity** keeps everything the app has done, and **Settings** holds its own settings.

Anything you do to machines is a short guided path with its steps listed on the left:

- **Set up monitoring** on one machine (the button on its row) or on several at once (tick them, then the button at the bottom). You sign in once per kind of machine. A **Check** step signs in, looks at what is installed and running, and tests the collector and the download; it changes nothing, and any problem is listed first with its own fix (a different sign-in for that machine, a collector that machine can reach, or sending Telegraf from this computer when the machine has no way out to the internet). **What to collect** has the usual set ticked; software the app found running is marked, a found web server is switched on for you, and a found database asks for a database account. **Review** says what will happen to each machine; **Practice run** goes through every step that does not change a machine. Two machines are worked on at a time, and you can stop the ones not yet changed.
- **Test connection only** for machines, and **Test the connection now** under Settings for VCF Operations itself.
- **Change what is collected** from a machine's page: tick or untick, see exactly what starts and stops, apply. What you tick is what is applied, and the app reads the settings back from the machine to confirm.
- **Try again** after a failure keeps the choices from last time.
- **Remove monitoring** from a machine's page.

Every path ends on a result that says in a sentence what happened, counted against what you picked, with the reason and what to do next to anything that did not work. Under it, **Do this again from the command line** shows the complete command for what was just done, one per machine, with each password shown as `<password>`. The same result, with its command, is kept under Activity.

An installed Telegraf is kept at its version by default; you can update that one machine on the Check step, or change the default in Settings. A version is never moved down. On Windows the basics are collected the Windows way (the standard counter set with the `win.` totals the Ops-managed agent also collects), as one set.

A small **New version available** link in the left column opens the newer release page. The app checks GitHub in the background at most once a day, with no login or Ops credentials; Settings can switch this off. Offline or failed checks stay silent. On Windows, desktop launch hides the executable's classic Command Prompt window. Commands launched in an existing terminal retain their output.

A practice run leaves the machine unchanged, but preparing it may request a client certificate from VCF Operations. Fresh data can take a collection cycle to appear; until VCF Operations shows it, the machine says "waiting for first data" rather than "sending data".

**Already running an Ops-managed agent?** The machine's status is "VCF Operations' own agent", and a setup leaves it out. Press **Take over** on its row (Windows and Linux): the Review screen lists what will happen in order and what stays the same, offers a practice run, and asks you to type the machine's name. The helper then saves the managed configuration and the Ops object on your workstation, asks VCF Operations to uninstall its agent (with the machine sign-in you gave), verifies the machine is clean, installs and enrolls open-source Telegraf with the same inputs, and confirms that the same Ops object flipped to Open Source and is receiving samples. Expect a monitoring gap of about 15 minutes. If the app closes after the managed agent was retired, the machine shows "Takeover interrupted" with **Finish takeover**, which carries on from the journal (under the app's config directory, never holding passwords or keys); if it closes before that, nothing was changed and you start over. The Linux implementation follows the VCF Operations 9.1.1 layout on Ubuntu 24.04; see [docs/agent-takeover.md](docs/agent-takeover.md) for the stages and what is retained. From the CLI: `run ... --vm-id <mor> [--vc-id <vcenter uuid>] --vm-name <name> --take-over-managed-agent --confirm-takeover <name>`; input flags other than workload additions are refused in takeover mode, and the exit code is 3 when VCF Operations created a different object.

## Verify downloads (optional)

Each release includes `SHA256SUMS`. Download it alongside your chosen asset and verify the checksum before running it. On macOS, from the download directory:

```bash
shasum -a 256 -c SHA256SUMS --ignore-missing
```

Confirm that your selected download reports `OK`. On Linux, use `sha256sum --check SHA256SUMS --ignore-missing`. On Windows, use `Get-FileHash .\vcf-telegraf-helper-windows.exe -Algorithm SHA256` and compare it with the matching entry in `SHA256SUMS`.

Uploaded release assets carry GitHub build provenance attestations from v0.7.5 onward. This covers binaries, Python packages, and `SHA256SUMS`; GitHub's automatically generated “Source code” archives are not covered. With GitHub CLI installed, verify an uploaded asset came from this repository's build:

```bash
gh attestation verify <file> --repo sentania-labs/vcf-ops-telegraf-helper
```

## Optional command-line use

The app includes a CLI for administrators who want repeatable commands or a terminal workflow. Every result in the app, and every saved result under Activity, shows the command that does the same thing.

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

In the app, what you tick is what the machine collects: changing a machine, or setting up one that is already monitored, replaces the helper's inputs after showing you what starts and stops. From the CLI, a rerun preserves deployed helper inputs unless you pass `--replace-inputs`. Other configuration fragments are retained either way, and settings in the helper's file that are outside its list are carried along. Unrelated inputs in the main configuration require review before onboarding.

Desktop API trust uses the operating system certificate store, including Windows and macOS. A certificate the store does not know can be trusted by its fingerprint (the app offers this on first sight; `--trust-cert-sha256` in the CLI), or checked against a company certificate file (Settings, Advanced; `--ca-cert`). Agent-to-collector TLS is a separate setting, enabled by default, using the collector CA deployed on the target.

A machine that cannot reach the Telegraf download site can be sent the release from your computer (the Check step offers it; `--send-telegraf` in the CLI). Your computer downloads it over HTTPS from InfluxData, and the machine verifies the SHA-256 of the copy it received before installing it.

## Product Principles

* **Follows Broadcom Documentation**: Uses the official VCF Operations 9.1 open-source Telegraf workflow. See [docs/references.md](docs/references.md).
* **Local-First Helper**: Behaves like an administrator utility (such as `vcf-cf-migrator`). No background daemons, databases, or centralized server infrastructure.
* **Safe Changes (Low Blast Radius)**: Generates isolated configuration fragments in `/etc/telegraf/telegraf.d/` (`vcf-helper-system.conf` and `cloudproxy-http.conf`). Unrelated fragments are always retained. The helper's own inputs are replaced only when you change them, and the app shows what starts and stops first. Helper-managed collector output and security files are updated for the selected integration. A main configuration containing unrelated inputs requires review before onboarding.
* **Idempotent and Drift-Free**: Re-running configuration against an existing target produces deterministic files and reports whether updates were needed.
* **Transparent**: The Review step can show the exact settings files, and a practice run goes through every step that does not change a machine, so you can inspect what will be applied before applying it.
* **Honest Validation**: Separately validates configuration syntax, endpoint reachability, collector connectivity, and service state. Failures in one layer are never masked, and the app never reports success for something it did not do: a result counts against what you picked and names every machine that was left out, stopped or failed.
* **Direct Push**: Deploys over SSH (Linux) or WinRM (Windows). Every result shows the equivalent `vcf-telegraf-helper run` command for anyone who needs to adapt it.
* **Inventory-Driven Targeting**: Pick the VM from VCF Operations inventory (templates and deleted VMs excluded, powered-off VMs and platform VMs such as vCLS, Supervisor control plane and appliance nodes hidden by default; `vcf-telegraf-helper vms --include-platform-vms` and the "Show" link under the Machines list reveal the latter). Guest OS, IP, agent status, and the agent's current collector come from VCF Operations.

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

MIT; see [LICENSE](LICENSE). The app bundles third-party open-source components (including PySide6/Qt under LGPLv3 and paramiko under LGPL-2.1). Their notices and license texts are built into the app: open **About this app > Open-source licences** in the app or run `vcf-telegraf-helper --licenses`. Each release also attaches a `THIRD-PARTY-NOTICES-<platform>.txt` file.
