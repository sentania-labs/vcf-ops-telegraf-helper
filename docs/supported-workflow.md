# Supported Workflow

This document explains which portions of Broadcom's supported VCF Operations 9.1 open-source Telegraf workflow are automated by the utility, and which boundaries are left to standard infrastructure mechanisms.

## Workflow Overview

Broadcom documents a multi-step procedure to connect an open-source Telegraf agent on an endpoint (VM or physical server) to VCF Operations:

1. **Prerequisites Verification**: Ensuring packages such as `curl`, `jq`, `uuidgen`, and network access to Cloud Proxy port 443 exist.
2. **Authentication**: Acquiring a temporary auth token from the VCF Operations API (`POST /suite-api/api/auth/token/acquire`).
3. **Collector Configuration / Bootstrap**: Downloading `telegraf-utils.sh` from the Cloud Proxy or writing the Wavefront HTTP output configuration to `/etc/telegraf/telegraf.d/cloudproxy-http.conf`.
4. **Input Plugin Configuration**: Editing `/etc/telegraf/telegraf.conf` or adding fragments to `/etc/telegraf/telegraf.d/` with OS monitoring settings matching VCF Operations metric schemas.
5. **Config Validation**: Running `/usr/bin/telegraf --test` or syntax checks to catch failures before restart.
6. **Service Lifecycle**: Restarting or reloading the `telegraf` systemd service.
7. **Verification**: Confirming local metric generation and checking ingestion status in VCF Operations.

## What the Utility Automates

The helper orchestrates these steps from an administrator workstation without requiring manual SSH copy-pasting, ad-hoc curl commands, or hand-edited TOML files:

* **VCF Operations Environment Connectivity**:
  Tests API reachability, validates TLS settings, and obtains the auth token using official API paths.
* **Endpoint Inspection**:
  Discovers target operating system, architecture, existing Telegraf binaries, configuration directories, and current service state.
* **Standard Telegraf Output Generation**:
  Generates the Broadcom-compliant `[[outputs.http]]` fragment for the Cloud Proxy endpoint using Wavefront format, with required headers (`uuid`, `ip`, `hostname`).
* **Structured Input Plugin Rendering**:
  Produces clean, standalone input fragments (e.g. `vcf-helper-system.conf`) for CPU, Memory, Disk, and Network monitoring with Broadcom-recommended field options.
* **Validation at Multiple Boundaries**:
  Tests generated TOML syntax locally, validates Telegraf configuration syntax on the remote endpoint prior to applying, and confirms network path reachability to the Cloud Proxy.
* **Safe Application**:
  Writes isolated fragments into `telegraf.d/` rather than modifying the vendor or user `telegraf.conf` file directly.
* **Service Restart and Telemetry Check**:
  Restarts the service via systemd, tests local metric generation with `--test`, and inspects service status.
* **Audit and Run Reporting**:
  Exports an honest stage-by-stage Markdown or JSON report showing exact commands executed, exit codes, and verification outcomes.

## Operational Boundaries

* **Blast Radius Control**:
  The utility only creates and manages its own configuration fragments (prefixed with `vcf-helper-` or standard `cloudproxy-http.conf`). It never deletes or alters unmanaged files in `/etc/telegraf/telegraf.d/`.
* **Drift Management**:
  Configuration generation is idempotent. Running the utility multiple times produces identical fragments and reports whether files changed.
* **Recovery Mechanism**:
  Because configuration is modularized into drop-in fragments under `telegraf.d/`, rolling back changes requires only removing the managed `.conf` files and restarting Telegraf.
* **Deployment Modes**:
  For environments where direct SSH push is restricted by security policy, the utility supports `generate script` and `config only` modes to produce auditable bash scripts and configuration bundles for local execution through existing change-management channels.
