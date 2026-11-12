# Architecture

## Overview

VCF Operations Open Telegraf Helper is a local-first administrator utility. It is designed to run on an administrator workstation (similar to `vcf-cf-migrator`) rather than as an always-on server, Kubernetes workload, or centralized platform.

Its primary purpose is to safely configure open-source Telegraf on Linux (and future Windows) systems to send telemetry to VCF Operations 9.1 collectors (Cloud Proxies) according to Broadcom official documentation.

```text
Admin Workstation
┌────────────────────────────────────────────────────────┐
│ UI Layer: Desktop app / Terminal Wizard / CLI          │
│  │                                                     │
│  ▼                                                     │
│ Workflow Engine (ConfigureEndpointWorkflow)            │
│  │                                                     │
│  ├─► VCF Operations Adapter (VCF91OpenTelegraf)        │
│  │     └── Validates API, collects auth token, checks  │
│  │                                                     │
│  ├─► Telegraf Configuration Renderer                   │
│  │     └── Structured model to valid Telegraf TOML     │
│  │                                                     │
│  ├─► Endpoint Executor (Local, Mock, SSH, Package)     │
│  │     └── Abstract command execution and file upload  │
│  │                                                     │
│  ├─► Validation Engine                                 │
│  │     └── Explicit checks across distinct boundaries  │
│  │                                                     │
│  └─► Local State Store                                 │
│        └── Ephemeral session data, saved environments  │
└────────────────────────────────────────────────────────┘
```

## Architectural Boundaries

### 1. Workflow Layer
The workflow layer coordinates operations as explicit sequential stages:
1. `CONNECT`: Test reachability and authenticate with the endpoint.
2. `DETECT`: Discover operating system, architecture, existing Telegraf binaries, paths, and service status.
3. `PREPARE_VCF`: Contact VCF Operations 9.1 API, acquire an auth token, and retrieve collector routing.
4. `RENDER_INPUTS`: Render structured input configurations (CPU, memory, disk, network) into TOML.
5. `VALIDATE`: Run syntax and reachability validations before touching target files.
6. `APPLY`: Upload managed configuration fragments to the endpoint.
7. `RESTART`: Safely reload or restart the Telegraf service.
8. `VERIFY`: Run validation checks (binary test run, service state, collector reachability).

The UI layer (desktop app, Terminal Wizard, and CLI) only renders inputs and displays output: it contains no direct operational logic.

The desktop app adds a thin layer between its screens and the engine, all of it usable without Qt:

* `workflow/checks.py`: the check a machine gets before anything is done to it (sign-in, what is installed and running, collector and download reachability). It changes nothing.
* `workflow/plan.py`: every choice of a run for one machine. The engine inputs and the command shown for doing the same thing from the command line are both built from it, so the command always matches what was done.
* `workflow/batch.py`: works on several machines, two at a time, and turns the run into one history entry that counts against what was picked.
* `workflow/collect.py` and `workflow/plain.py`: the plain names for what can be collected, the six machine statuses, and the table from a failed stage to a reason and something to do.
* `storage/history.py`: everything the app has done, kept on the workstation, never holding passwords.

The engine honours a stop request only before stage 6 (`APPLY`), the first stage that changes the endpoint.

### 2. VCF Operations Adapter
VCF Operations integration details are isolated behind the `VCFOpsIntegration` interface.
Release-specific behavior (such as VCF 9.1 Suite API token acquisition at `/suite-api/api/auth/token/acquire`) lives in `VCF91OpenTelegrafIntegration`. Future releases can be added without altering the workflow or renderer.

### 3. Telegraf Configuration Renderer
Monitoring selections are represented as typed, structured models (`MonitoringConfig`) and rendered into valid Telegraf TOML format.
The renderer is decoupled from execution and can be unit tested in isolation. It outputs clean, commented fragments adhering strictly to Broadcom recommendations for OS metrics.

### 4. Pluggable Endpoint Executors
Endpoint interactions are abstracted behind `EndpointExecutor`:
* `LocalExecutor`: Executes directly on the local machine (useful for testing or local endpoints).
* `MockExecutor`: Provides simulated responses for unit and integration testing without network dependencies.
* `SSHExecutor`: Handles secure remote command execution and SFTP uploads on Linux endpoints via Paramiko.

### 5. Validation Layer
Validation never collapses into a generic success status. It reports discrete statuses across independent failure domains:
* Structured configuration validity (types and bounds)
* TOML syntax correctness
* Endpoint reachability and credentials
* Telegraf configuration syntax validation (via `telegraf --test`)
* Collector network reachability (port 443)
* Telegraf systemd service health
* Metric ingestion confirmation

## Operational Safety and Blast Radius

### Blast Radius
The utility does not touch existing vendor or administrator configuration in `/etc/telegraf/telegraf.conf`.
All generated outputs are written to dedicated, application-owned fragments inside `/etc/telegraf/telegraf.d/`:
* `vcf-helper-system.conf`: Managed OS input plugins (CPU, memory, disk, network).
* `cloudproxy-http.conf`: Broadcom-standard Cloud Proxy HTTP output plugin.

Existing third-party or custom fragments in `telegraf.d/` are completely preserved.

### Drift and Idempotency
Running the workflow repeatedly against the same endpoint produces deterministic configuration. If no settings changed, the generated files match the existing files on disk, avoiding service disruptions.

### Recovery
If a configuration causes an issue, recovery is straightforward:
1. Remove `/etc/telegraf/telegraf.d/vcf-helper-system.conf` and `cloudproxy-http.conf`.
2. Run `systemctl restart telegraf`.
No complex uninstallers or database rollbacks are required.

### Secret Handling
1. Passwords and tokens are kept in memory for the duration of the run.
2. Plaintext passwords are not saved to persistent configuration files.
3. Secret values are sanitized from logs, CLI output, and exported reports.
