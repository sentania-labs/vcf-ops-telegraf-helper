# VCF Operations Open Telegraf Helper
## Product Specification

**Status:** Draft  
**Version:** 0.2  
**Product Shape:** Local helper application / utility  
**Primary Goal:** Make onboarding and configuring open-source Telegraf monitoring for VCF Operations simple, guided, transparent, and repeatable.

---

# 1. Executive Summary

VCF Operations supports monitoring applications and operating systems by ingesting telemetry from open-source Telegraf agents running on Windows and Linux.

The supported workflow is powerful, but it requires administrators to coordinate several steps manually:

- Install or locate Telegraf
- Obtain the appropriate VCF Operations / collector configuration
- Configure the supported VCF Operations output
- Configure Telegraf input plugins
- Validate Telegraf configuration
- Restart or reload the Telegraf service
- Verify telemetry flow
- Troubleshoot failures across the endpoint, collector, and VCF Operations

This project provides a **local guided helper utility** over that supported workflow.

The application is intentionally closer to a migration/configuration utility such as `vcf-cf-migrator` than to a persistent service such as `vcf-doctor`.

The MVP should feel like:

> Launch utility → connect to VCF Operations → choose a target → choose what to monitor → generate/apply configuration → validate → verify → exit.

The utility does **not** replace:

- Telegraf
- VCF Operations
- Operations Collectors / Cloud Proxies
- Broadcom-supported Telegraf integration mechanisms

It automates and simplifies the supported process.

---

# 2. Authoritative Product Documentation

Implementation must remain aligned with Broadcom's supported VCF Operations open-source Telegraf workflow.

Primary documentation:

**VCF Operations 9.1: Monitoring Applications Using Open Source Telegraf**

https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-1/workload-monitoring-and-observability/monitoring-applications-using-open-source-telegraf.html

This documentation should be treated as the normative reference for:

- Supported workflow
- Supported bootstrap/configuration mechanism
- Collector interaction
- VCF Operations authentication requirements
- Telegraf output configuration
- Supported operational assumptions

The utility must not silently substitute an undocumented ingestion mechanism where a supported Broadcom workflow exists.

Where product behavior differs by VCF Operations release, the utility should detect or request the version and adjust behavior explicitly.

---

# 3. Product Positioning

This is **not initially a web application or server platform**.

It is a local helper utility intended to be run by an administrator when onboarding or updating Telegraf monitoring.

Conceptually:

```text
Administrator Workstation
┌────────────────────────────────────────────┐
│ VCF Operations Open Telegraf Helper        │
│                                            │
│  1. Connect to VCF Operations              │
│  2. Select / connect to endpoint           │
│  3. Detect Telegraf                        │
│  4. Choose monitoring                      │
│  5. Generate configuration                 │
│  6. Validate                               │
│  7. Apply                                  │
│  8. Verify                                 │
└───────────────┬────────────────────────────┘
                │
         SSH / PowerShell /
         generated package
                │
        ┌───────┴────────┐
        │                │
     Linux            Windows
     Telegraf         Telegraf
        │                │
        └───────┬────────┘
                │
                v
       Operations Collector
                │
                v
         VCF Operations
```

The tool may evolve into a client/server product later if fleet management becomes valuable, but the MVP must not require a persistent server component.

---

# 4. Problem Statement

The current workflow requires administrators to understand several independent concerns:

- VCF Operations
- Operations Collectors / Cloud Proxies
- Telegraf installation
- Telegraf TOML syntax
- Input plugin configuration
- Operating-system service management
- SSH / Windows remote administration
- Authentication
- Certificates
- Troubleshooting metric flow

The utility should compress those concerns into one guided workflow while keeping the resulting configuration transparent.

---

# 5. Product Principles

## 5.1 Follow the supported Broadcom workflow

Broadcom documentation is the source of truth.

The helper automates that workflow rather than replacing it.

## 5.2 Telegraf remains the endpoint agent

Do not create a proprietary endpoint agent.

## 5.3 Local-first

The utility should be runnable from an administrator workstation without requiring deployment of a server-side product.

## 5.4 Guided, not opaque

Users should not need to understand Telegraf TOML for common monitoring cases.

However, users must be able to inspect the generated configuration.

## 5.5 Safe changes

Prefer application-owned configuration fragments.

Do not overwrite unrelated existing Telegraf configuration.

## 5.6 Idempotent operations

Re-running the utility against an endpoint should not create duplicate configuration or repeatedly modify unchanged settings.

## 5.7 Useful without remote administration access

Remote push is convenient, but generating scripts/configuration for manual deployment must remain a first-class workflow.

## 5.8 No fake success

The tool must distinguish clearly between:

- Generated successfully
- Validated locally
- Applied successfully
- Telegraf running
- Collector reachable
- Telemetry confirmed in VCF Operations

---

# 6. Primary User Journey

## Step 1: VCF Operations

User provides:

- VCF Operations URL
- Authentication
- Operations Collector / Cloud Proxy information if needed
- TLS options

Action:

**Validate**

Result:

```text
✓ VCF Operations reachable
✓ Authentication successful
✓ Version detected: 9.1
✓ Collector reachable
```

## Step 2: Target System

User specifies:

- Hostname or IP
- Windows or Linux
- Connection method

Connection modes:

- Direct remote connection
- Generate deployment package/script
- Configuration-only

For direct connection, the utility detects:

- Operating system
- Architecture
- Telegraf presence
- Telegraf version
- Service status
- Existing configuration locations

## Step 3: VCF Operations Telegraf Configuration

The utility performs or generates the supported Broadcom configuration/bootstrap procedure described in the VCF Operations documentation.

The exact implementation must be version-aware.

The user should not need to manually construct the VCF Operations output configuration.

## Step 4: Choose Monitoring

Initial guided monitoring options:

```text
Operating System
[x] CPU
[x] Memory
[x] Disk
[x] Disk I/O
[x] Network
[x] System

Availability
[ ] Process
[ ] Service
[ ] HTTP endpoint
[ ] TCP port

Advanced
[ ] Custom Telegraf input
```

## Step 5: Configure Inputs

Only request settings required by selected plugins.

## Step 6: Review

Display:

- Target
- VCF Operations destination
- Collector
- Telegraf version
- Selected plugins
- Generated configuration
- Files that will change
- Commands that will run

The user can inspect generated TOML before applying.

## Step 7: Validate

Validation should include:

- Structured configuration validation
- TOML generation validation
- Telegraf test/config validation where supported
- VCF Operations/collector reachability
- Plugin-specific checks where practical

## Step 8: Apply

The utility:

1. Backs up application-managed or changed configuration as appropriate
2. Applies the VCF Operations bootstrap/output configuration
3. Writes managed input configuration
4. Validates the final configuration
5. Restarts/reloads Telegraf if necessary
6. Verifies service state

## Step 9: Verify

Display independently:

```text
Telegraf installed          ✓
Configuration valid         ✓
Telegraf service running    ✓
Collector reachable         ✓
Metrics generated locally   ✓
Metrics visible in Ops      ✓ / ? / ✕
```

---

# 7. Application Form Factor

The MVP should be a local executable or locally launched utility.

Acceptable implementation models include:

- Native/local GUI application
- Packaged desktop application
- Local GUI backed by an embedded/local process
- CLI plus guided interactive UI if that best fits the implementation

The product must **not require**:

- PostgreSQL
- Kubernetes
- A persistent API server
- Central background workers
- An always-on service
- A shared multi-user database

A local embedded database such as SQLite is acceptable if structured persistence is useful.

Simple local configuration files are also acceptable.

---

# 8. Local Persistence

Persist only what materially improves repeated use.

Useful saved data may include:

- Known VCF Operations environments
- Collector information
- Recently used targets
- Monitoring templates
- Non-secret preferences
- Generated configuration history
- Last-known Telegraf metadata

Do not turn the MVP into a CMDB.

Secrets should be stored using operating-system-native secure storage where practical.

If secure persistence is not available, allow credentials to be session-only.

---

# 9. Target Platforms

## Managed Endpoints

### Linux

Initial targets:

- Ubuntu
- Debian
- RHEL-compatible distributions

### Windows

Initial targets:

- Windows Server
- Windows 11 for lab/testing

---

# 10. Endpoint Interaction Modes

## 10.1 Direct Push

Linux:
- SSH

Windows:
- PowerShell Remoting / WinRM or another suitable supported mechanism

Workflow:

1. Connect
2. Detect
3. Install if requested
4. Configure
5. Validate
6. Restart/reload
7. Verify

## 10.2 Generate Package / Script

Generate a self-contained deployment bundle.

Linux examples:

- shell script
- Telegraf configuration fragments
- helper/bootstrap invocation instructions

Windows examples:

- PowerShell script
- Telegraf configuration fragments
- helper/bootstrap invocation instructions

## 10.3 Configuration Only

Generate:

- Telegraf input configuration
- VCF Operations-related configuration/instructions
- validation commands
- deployment guidance

---

# 11. Telegraf Configuration Model

Internally model configuration as structured data.

Example:

```yaml
inputs:
  - plugin: cpu
    enabled: true

  - plugin: mem
    enabled: true

  - plugin: disk
    enabled: true

  - plugin: http_response
    enabled: true
    config:
      urls:
        - https://app.example.com/health
      expected_status: 200
```

Then render the configuration into Telegraf TOML.

Do not make raw TOML the only internal representation.

---

# 12. Managed Configuration

Prefer a dedicated application-managed Telegraf configuration fragment/directory.

The utility should track which files it owns.

Do not overwrite unrelated user-managed configuration.

Exact paths vary by OS/package and must be discovered rather than assumed globally.

---

# 13. Initial Monitoring Plugins

## Core OS Monitoring

- CPU
- Memory
- Disk
- Disk I/O
- Network
- System
- Swap where applicable

## Process Monitoring

Configure by process name or pattern.

## Service Monitoring

Linux:
- system/service state where supported

Windows:
- Windows service state

## HTTP Endpoint Monitoring

- URL
- method
- expected response
- timeout
- TLS behavior
- optional headers

## TCP Monitoring

- hostname
- port
- timeout

## Advanced Custom Input

Allow an advanced user to add a raw Telegraf input fragment.

Still validate before application.

---

# 14. Monitoring Templates

Support simple reusable presets.

Examples:

- Basic Linux Server
- Basic Windows Server
- Web Server
- Application Server

Templates should initially be local files or local embedded configuration.

Do not build a centralized template service for MVP.

---

# 15. Validation

Validation is one of the highest-value features.

## 15.1 Local / Structural Validation

Check:

- Required fields
- Supported plugin configuration
- Valid generated TOML
- Required credentials/references present

## 15.2 Endpoint Validation

Where possible:

- Execute Telegraf validation/test command
- Capture stdout/stderr
- Detect invalid plugin configuration
- Detect missing binaries
- Detect permission issues

## 15.3 Connectivity Validation

Independently test:

- Endpoint connection
- VCF Operations connection
- Collector connection
- Required ports/endpoints

## 15.4 Post-Apply Validation

Check:

- Telegraf service state
- Configuration validity
- Local metric production
- Collector reachability
- VCF Operations ingestion if supported through available APIs/workflow

---

# 16. Session / Operation Model

The MVP does not need a server-style background job system.

Instead model actions as explicit local operations.

Example:

```text
Operation: Configure db01

[1/8] Connect to db01                    ✓
[2/8] Detect Telegraf                    ✓
[3/8] Generate VCF Ops configuration     ✓
[4/8] Generate input configuration       ✓
[5/8] Validate configuration             ✓
[6/8] Apply configuration                ✓
[7/8] Restart Telegraf                   ✓
[8/8] Verify telemetry                   ✓
```

Operations should:

- Stream progress
- Capture command output
- Surface actionable errors
- Allow saving/exporting a report

---

# 17. Reporting

At the end of a run, offer a summary.

Allow exporting the summary as Markdown or JSON.

---

# 18. Suggested Internal Architecture

Keep boundaries clean even though this is a local utility.

```text
UI / CLI
   |
   v
Workflow / Application Layer
   |
   +--> VCF Operations Adapter
   |
   +--> Telegraf Configuration Renderer
   |
   +--> Endpoint Executor
   |      +--> SSH
   |      +--> Windows
   |      +--> Local / Package
   |
   +--> Validation
   |
   +--> Local Settings / Templates
```

## VCF Operations Adapter

Owns:

- Connectivity
- Version detection
- Supported VCF Operations integration workflow
- Collector interaction
- Future API calls

## Telegraf Renderer

Owns:

- Structured configuration
- Plugin rendering
- TOML generation
- Managed-file composition

## Endpoint Executor

Owns:

- Command execution
- File upload/download
- Remote connectivity
- Privilege handling

## Workflow Layer

Coordinates:

- Detect
- Generate
- Validate
- Apply
- Verify

---

# 19. Version Awareness

VCF Operations integration details may evolve across releases.

The utility should have an explicit compatibility layer.

Conceptually:

```text
VCFOpsIntegration
    |
    +--> VCF91OpenTelegrafIntegration
    +--> future release integration
```

The documentation URL for the matching VCF release should be visible in project documentation and help/reference surfaces.

---

# 20. Security

Requirements:

- Do not log secrets
- Mask credentials in UI/output
- Prefer session-only credentials by default
- Use OS-native secure storage if credentials are persisted
- Show commands without exposing secret values
- Avoid writing secrets into generated reports
- Validate TLS by default
- Require explicit action to disable certificate validation

---

# 21. Non-Goals for MVP

Do not implement:

- Central fleet-management server
- Multi-user web portal
- PostgreSQL
- Kubernetes deployment
- Persistent background workers
- Custom endpoint agent
- Automatic dashboard generation
- Automatic alert creation
- Management Pack Builder automation
- Full Telegraf plugin catalog
- Large-scale upgrade orchestration
- Complex configuration policy inheritance
- CMDB functionality
- Enterprise RBAC

---

# 22. Future Evolution

## Phase 2: Better Utility

- More Telegraf plugins
- Bulk target processing
- Agent upgrades
- Configuration diff/drift detection
- Richer templates
- Improved metric verification
- Import/export configuration profiles
- Better reporting

## Phase 3: Optional Server Component

Only add a server component if real usage demonstrates value in:

- Fleet inventory
- Scheduled health checking
- Continuous drift detection
- Central policy
- Shared templates
- Multi-user administration
- Remote execution workers

The local utility should remain useful even if a server component is later added.

## Phase 4: VCF Operations Authoring

Potential future capabilities:

- Metric discovery
- Dashboards
- Symptoms / alerts
- Resource relationships
- Management Pack Builder integration
- Application-aware monitoring packs

---

# 23. MVP Acceptance Criteria

The MVP is successful when a user can:

1. Launch the utility locally.
2. Connect to a VCF Operations 9.1 environment.
3. Validate VCF Operations connectivity.
4. Select or specify an Operations Collector as required.
5. Connect to one Linux target.
6. Connect to one Windows target.
7. Detect an existing Telegraf installation.
8. Install Telegraf when appropriate or generate installation guidance/script.
9. Apply the documented VCF Operations open-source Telegraf integration workflow.
10. Select core OS monitoring through the UI.
11. Configure at least one process/service monitor.
12. Configure an HTTP or TCP monitor.
13. Preview generated TOML.
14. Validate the configuration before applying it.
15. Apply configuration idempotently.
16. Restart/reload Telegraf as needed.
17. Verify Telegraf service health.
18. Verify collector connectivity.
19. Show whether telemetry can be confirmed in VCF Operations.
20. Export a run summary.
21. Complete the workflow without requiring deployment of a server component.

---

# 24. Engineering Priorities

In order:

1. Correct implementation of Broadcom's supported workflow
2. Safe endpoint changes
3. Excellent validation
4. Useful error messages
5. Simple guided UX
6. Transparency of generated configuration
7. Idempotency
8. Clean internal boundaries
9. Cross-platform extensibility
10. Visual polish

---

# 25. Definition of Done

For the first useful release:

- Utility launches locally with minimal setup
- No persistent server is required
- VCF Operations 9.1 connection workflow is functional
- Broadcom documentation is linked from the application/docs
- Linux workflow operates end-to-end
- Windows workflow operates end-to-end
- Core Telegraf inputs can be configured without manual TOML authoring
- Generated TOML is viewable
- Validation is functional
- Changes are idempotent
- Existing unrelated Telegraf configuration is preserved
- Operation progress and errors are visible
- Run summary can be exported
- Automated tests cover rendering, workflow decisions, and configuration safety
- No secrets appear in logs or exported reports

---

# 26. Guiding Product Statement

> Make the supported VCF Operations open-source Telegraf workflow feel like a guided administrator utility instead of a documentation exercise.

