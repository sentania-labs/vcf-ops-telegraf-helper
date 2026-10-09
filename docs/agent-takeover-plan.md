# Ops agent takeover implementation plan

Add an unchecked **Take over existing Ops agent** option that replaces the selected VM's product-managed agent with OSS Telegraf. Preserve its monitoring configuration, allow additions, and keep the original collector and VM identity by default. This is an implementation plan; takeover is not available in v0.7.5.

## Validation limits

Scott has no known-working managed-agent installation on a Windows Server version earlier than Server 2025. A managed installation exists on `ca`, but its reporting status is unconfirmed. Its operating system and agent health must be checked before using it as a test target. Scott will deploy additional VMs compatible with the managed agent for experimentation; verify their managed agents are reporting before using them as the continuity baseline.

This candidate can support testing discovery, backup, cleanup, OSS installation, and new agent reporting once target-specific execution is authorized. It cannot establish continuity with a known-working managed-agent baseline. Record the original and resulting resource IDs where available, but do not treat equal IDs as proof that historical data and metric continuity were preserved.

Retain the existing vCenter VM object. Reuse of the OS/application object and its history remains an unverified acceptance criterion until a working managed-agent baseline is available. Do not force resource IDs, delete VM inventory, or report a seamless migration to hide this gap.

## User flow

Show the takeover option only when discovery identifies a product-managed installation. Keep normal onboarding blocked for that installation unless takeover is explicitly selected. Extend identification and guarding to both operating systems; the current explicit `ucp-telegraf` refusal is Windows-specific.

Checking the option prepares a read-only migration preview. Show the selected VM, current collector, existing monitoring inputs, requested additions, and cleanup scope. Uninstallation starts only after Execute and an explicit confirmation explaining the monitoring interruption. Preserve that confirmation and the selected identities in a durable operation record.

Imported monitoring becomes the starting configuration in Monitoring Inputs. Existing settings remain selected, additions are merged, and the Baseline preset becomes an explicit reset. Preserve fragments that the structured editor cannot represent, with a visible indication that they are retained. Never silently discard incompatible settings.

Execution reports four distinct results: managed-agent retirement, OSS installation, registration, and fresh metric ingestion. Service state alone is not completion.

## Execution order

1. **Identify the exact installation.** Capture the VM's Ops resource ID, vCenter ID, VM MOR, OS/application resource IDs, full resource identifiers, relationships, collector, agent version, service settings, binaries, configuration files, included fragments, and required auxiliary files. Block ambiguous bindings and unrelated hosts.
2. **Create a protected recovery snapshot.** Back up the original configuration and installation metadata on the endpoint, outside the cleanup scope. Preserve required scripts and credentials without placing raw configuration, passwords, or private keys in UI logs, exports, GitHub artifacts, or the operation journal. Journal backup references and stage results, not secrets.
3. **Stage and validate the replacement.** Download the selected OSS package and prepare the migrated configuration before removing the managed agent. Resolve unsupported plugins, encrypted credentials, missing scripts, incompatible paths, and runtime dependencies before cutover. Show a configuration diff with preserved, added, changed, and blocked settings. Mask sensitive values in imported previews and diffs; use protected references to preserve credentials through editing and replay.
4. **Retire the installation through Ops.** Use the supported resource-scoped uninstall API and poll its task to a terminal result. The API's retain-configuration flag defaults to true; after the independent snapshot is verified, request full removal for the replacement path. Verify Ops retirement and endpoint state separately. Remove residual managed services and files only when they belong to the captured installation. Leave shared adapters and vCenter-to-collector mappings intact.
5. **Install and enroll OSS Telegraf.** Install the staged package, apply imported inputs plus approved additions, and use the saved VM binding and original collector. Generate fresh OSS integration credentials and output configuration. Do not carry over the managed agent's control credentials or start both agents together.
6. **Verify the resulting state.** Confirm the managed services are absent, OSS Telegraf is healthy, and registration is bound to the selected VM. Require agent metric samples newer than the cutover. Compare old and new resource IDs and report continuity as verified, changed, or unverified. When a working baseline becomes available, compare retained metric categories and representative counters as well.

Ops documents a [resource-scoped uninstall API](https://developer.broadcom.com/xapis/vcf-operations-api/latest/suite-api/api/applications/agents/delete/) and an [agent task-status API](https://developer.broadcom.com/xapis/vcf-operations-api/latest/suite-api/api/applications/agents/taskId/status/get/). Validate their behavior and retirement semantics against the target VCF Operations version before enabling cutover.

For installations without a usable Ops control channel, the documented [unsubscribe API](https://developer.broadcom.com/xapis/vcf-operations-api/latest/suite-api/api/applications/unsubscribe/put/) returns an unbootstrap bundle and has a cleanup option. Treat this as a separately validated recovery path, not an automatic fallback after an uncertain uninstall result.

## Configuration preservation

Import the complete effective configuration, not just the main TOML file. Retain counters, instances, intervals, measurements, tags, processors, aggregators, and unrelated outputs. Preserve file ordering and resolve duplicate or overlapping plugin instances deliberately. Proposed additions must extend the imported configuration without collecting the same metrics twice.

Replace the managed Ops output and control scripts with the supported OSS integration. Retain other outputs only after their paths, credentials, and dependencies remain valid. Relative paths, external scripts, service accounts, and encrypted values require explicit migration handling. A setting that cannot be carried over blocks cutover until resolved.

Keep imported settings visible in the UI. Do not replace them with the helper's default baseline. The CLI repeat command must express the same imported configuration and additions, using protected configuration references where credentials are involved.

## Identity and registration

The helper already enrolls managed VMs using the vCenter ID and VM MOR. Use the captured pair and the selected Ops resource ID throughout takeover, rather than resolving the VM again by hostname. Preserve the collector by default to avoid an unnecessary adapter or registration change.

Capture the old OS/application resource key and identifiers before retirement. Determine whether OSS enrollment reuses that resource under the same adapter and collector. If a new resource is created, retain the original records and report the changed identity. Do not claim that linking to the same VM also retained the OS object's metric history.

Update ingestion verification to query the expected agent resource and validate its relationship to the selected VM. The current hostname-first lookup can select an unrelated registration. vCenter hypervisor statistics are not proof of agent ingestion.

## Failure and recovery

| Failure point | Required behavior |
| --- | --- |
| Inspection, backup, or configuration migration | Stop before uninstall. Leave the existing agent active. |
| Ops uninstall fails or times out | Keep replacement blocked. Reconcile task and endpoint state before retrying; do not launch another uninstall blindly. |
| Managed installation removed, OSS installation fails | Report the monitoring interruption. Offer resume or a separate restore action using the saved configuration and original Ops installation path. |
| OSS runs but enrollment or reporting fails | Preserve the backup and identify the failed layer. Report pending collection separately from failure. |
| App closes during migration | Reconcile the operation journal with Ops and the endpoint, then resume from observed state. |
| Restore fails | Keep recovery data and report the incomplete state. Never mark the takeover successful or run both agents to conceal failure. |

The [managed-agent install API](https://developer.broadcom.com/xapis/vcf-operations-api/latest/suite-api/api/applications/agents/post/) is a candidate restore path, with the original shared mappings left intact. Test it with the saved configuration before promising automatic rollback. Keep backups after the operation; backup deletion is a separate explicit action.

Broadcom's [managed-agent removal guidance](https://knowledge.broadcom.com/external/article/384995) includes `ucp-minion`, `ucp-salt-minion`, and `ucp-telegraf`, not Telegraf alone. That guidance targets Aria Operations 8.x, so confirm the actual components and paths on VCF Operations 9.1 before applying cleanup.

## Implementation milestones

1. **Read-only inspection and lab evidence.** Inspect an authorized candidate such as `ca`, capture identity and configuration, and determine whether the managed agent reports. For the additional experimental VMs Scott will deploy, establish working managed-agent reporting first, record VM and OS/application resource IDs, capture historical samples and representative monitoring settings, then run takeover and compare the results. Keep the continuity limitation explicit until that experiment succeeds.
2. **Migration core.** Add a takeover plan, protected snapshot, durable journal, Ops lifecycle methods, stage reconciliation, and cutover orchestration around the existing installer. Preserve the ownership guard until retirement is verified.
3. **Configuration import and additive editing.** Map supported inputs into the UI, retain other compatible fragments, and merge approved additions. Include configuration dependencies and credential handling.
4. **UI and CLI.** Add the unchecked option, read-only preview, explicit confirmation, progress, resume and restore states, and an explicit CLI takeover option. Keep ordinary onboarding behavior unchanged.
5. **Validation and delivery.** Test interrupted operations, failures, recovery, config preservation, precise identity binding, and fresh OSS ingestion. Render and inspect the UI, validate native packages, update docs, review, and merge. Release separately when requested.

Object/history continuity can remain unverified while discovery and migration mechanics are developed. Successful new OSS reporting must not be presented as evidence that old history survived.

## Acceptance criteria

- Declining takeover makes no changes.
- Unreadable backup data, incompatible inputs, missing credentials, or ambiguous identity block before removal.
- Cleanup changes only the captured installation on the selected endpoint.
- Ops retirement and removal of managed control services are both verified.
- The effective OSS configuration preserves imported settings and includes approved additions once.
- The UI and CLI represent the same migrated configuration.
- Fresh OSS metrics are verified against the selected identity after cutover.
- Interrupted operations can be reconciled and resumed without duplicate registrations.
- Recovery preserves the backup and does not leave two agents running.
- Sensitive values do not appear in normal logs, exports, or CI artifacts.
- OS object and historical continuity remain explicitly unverified until tested with a known-working managed-agent baseline.

## Quiet release notice

The approved separate UI improvement is a small **New version available** link under the app name. It opens the newer release's page. No tray popup, modal dialog, or blocked startup.

Check this repository's latest stable release in a background worker with a short timeout and a locally cached result, at most daily. Compare parsed versions; ignore drafts and prereleases. Use only the repository's HTTPS release URL. Network failures, rate limits, malformed responses, and offline launches stay silent. Test newer, equal, older, development, and unavailable versions, plus both themes and narrow windows. This notice is implemented separately from the takeover work.

## Lab evidence, October 9, 2026 (tg-w22-01, Windows Server 2022, VCF Operations 9.1)

A product-managed agent installed through Ops that day was replaced with OSS Telegraf 1.40.1. Record: lab-admin `docs/authorizations/2026-10-09-telegraf-managed-agent-takeover-tg-w22-01.md`.

**Continuity is verified on Windows.** The Ops object `Windows OS on <host>` (adapter APPOSUCP, kind `win`) is keyed by `{VCID, VMMOR}` only. The OSS output sends the same pair in its `vcid` and `vmId` headers, so after the cutover Ops kept the same resource id, flipped `AgentManagedType` from `Product Managed` to `Open Source`, and metric history continued on the object. The only gap was the cutover window (15 minutes here). This closes the plan's open acceptance criterion for Windows; Linux is expected to behave the same (same key) but was not run.

**What the managed installation looks like (9.1).** Services `salt-minion` and `ucp-minion` (both `C:\VMware\UCP\salt\nssm.exe`, Automatic) and `ucp-telegraf` (`C:\VMware\UCP\ucp-telegraf\telegraf.exe --config ...\telegraf.conf --config-directory ...\telegraf.d`, Manual, started by the minion). Files under `C:\VMware\UCP` and certs under `C:\ProgramData\VMware\UCP\certkeys`. No MSI, no uninstall registry entry, no scheduled tasks. `C:\VMware\UCP\uaf\agents-registry.json` lists the product's own uninstall commands. Broadcom KB 384995's names (`ucp-salt-minion`) are the 8.x ones; use the service names above on 9.1.

**Uninstall through Ops works and is fast.** `DELETE /suite-api/api/applications/agents` with `resourceCredentials[{resourceId: <VM resource id>, username, password}]` and `retainTelegrafConf: false` returned a task; `GET /suite-api/api/applications/agents/{taskId}/status` went SUBMITTING, then FINISHED in 52 seconds. On the endpoint every service, process and the certkeys directory were gone; an empty `C:\VMware\UCP` remained and must be removed by the helper. The Ops object stays `DATA_RECEIVING` with the old property until the first OSS sample.

**Configuration port.** The managed `telegraf.conf` carried the product's `win_perf_counters` set (identical to the `win-perf-counters.conf` the opensource bootstrap drops; Wavefront output maps `win_cpu` and `win.cpu` to the same keys) plus `inputs.cpu`, `inputs.mem`, `inputs.swap` with `name_prefix = "win."`. Those three inputs are the entire granularity delta (8 Ops stat keys: `cpu|usage.user/system/guest`, `mem|total/used/used.percent`, `swap|total/used.percent`; 137 keys managed vs 114 without them). The helper's Windows baseline should include them.

**Vendor bug to patch on every Windows OSS enrollment.** `telegraf-utils.ps1 opensource` writes the Guest Info exec as `commands = ["C:\\Program Files\\Telegraf\\telegraf.d\\mandatory_tags.bat C:\\Program Files\\Telegraf\\telegraf.exe"]`; telegraf splits on spaces and runs `C:\Program`. Quoting is not enough because the .bat then runs its first argument unquoted (`%TELEGRAF_BIN_PATH% --version`) and exits 1. Working fix: run the .bat with no argument and change its default line to `set TELEGRAF_BIN_PATH="C:\Program Files\Telegraf\telegraf.exe"`. Without this, OSS Windows objects never get `Guest Info|OS Name`, `OS Version`, `Telegraf Version`, `IP` (the lab's dcint1 shows the gap since September 24).

**Timing.** Uninstall under 1 minute, enrollment about 1 minute, first OSS sample about 6 minutes after the service start (300 s interval); `inputs.cpu` percentages need a second interval.
