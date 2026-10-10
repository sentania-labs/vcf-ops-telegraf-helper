# Taking over an Ops-managed agent

The helper can replace a VCF Operations product-managed Telegraf agent on a Windows VM with open-source Telegraf, keeping the same Ops object and its metric history. This page describes what the helper does, what it refuses, and how the result was proven. The sequence was run by hand twice in the lab on October 9, 2026 (Windows Server 2022, VCF Operations 9.1) before it was built into the helper; issue #61 holds that record.

## When the option appears

- The VM list appends "Ops managed" to the agent status when any agent object bound to the VM has `AgentManagedType = Product Managed`.
- Detecting the endpoint in Step 3 finds the managed services: any service named `ucp-*`, and any service running from under `C:\VMware\UCP` (`salt-minion`, `ucp-minion`, `ucp-telegraf` on 9.1). A stock SaltStack minion elsewhere on the box does not count. A failed service query is an error, never an empty result. The banner shows the services, the managed Telegraf version, the VM binding from the salt grains, and the cleanup scope. If the grains name another VM than the one selected, the option is disabled.
- Ordinary onboarding stays blocked until **Take over existing Ops agent** is checked. The Uninstall button is disabled for a managed install; Ops retires its own agent.
- Linux takeover is not supported. The helper does not detect the Ops agent on Linux at all; a takeover requested for a Linux endpoint stops at the first stage.

## What checking the box does

1. The managed `telegraf.conf` and `telegraf.d` fragments are ported into the Monitoring Inputs catalog. For the 9.1 stock configuration that is the Windows perf counter set, the `w3wp` process instance, and the three `win.`-prefixed `cpu`, `mem` and `swap` inputs (Windows OS Totals); a `win_services` self-check for the `telegraf` service is added. A plugin instance the catalog cannot express is kept verbatim in the Custom TOML fragment under a visible header. A few settings are rewritten to the helper's standard and listed as "Changed": the agent interval becomes 300 s, a perf counter object's measurement name and options (`IncludeTotal` and similar) follow the baseline, and a narrower instance list on a baseline object widens to all instances. A fragment that does not parse blocks the takeover. What you see in the catalog is what gets installed: edits apply, and the Baseline preset discards the import.
2. The Review page leads with the takeover plan: services, binding, collector, cleanup scope, the sequence, and the preserved/added/changed/retained/dropped summary of the import.
3. The Execute page switches to **Execute Takeover**. Dry-run is not available. Executing asks you to type the VM name; the typed confirmation is journaled verbatim.

From the CLI:

```
vcf-telegraf-helper run ... --vm-id vm-6068 --vc-id <vcenter uuid> --vm-name tg-w22-01 \
  --take-over-managed-agent --confirm-takeover tg-w22-01 [--continuity-wait 900]
```

`--vm-id` is required; `--vc-id` is looked up from inventory when it matches exactly one VM. `--confirm-takeover` must equal `--vm-name` or `--vm-id`. The imported configuration is the starting point, so `--dry-run`, `--preview`, every input flag (`--no-baseline`, `--cpu`, `--win-perf`, `--win-os`, `--win-services`, ...), `--replace-inputs`, `--install-telegraf` and `--force-new-cert` are refused. Workload additions (`--nginx`, `--apache`, `--mysql`, `--postgres`, `--mssql`, `--docker`, `--ping`, `--win-perf-object`) are merged on top; one the managed agent already collects is skipped with a warning. `--telegraf-version` is honoured (default 1.40.1). Exit codes: 0 on success, including results still pending in Ops; 1 when a stage fails; 2 for a usage error; 3 when Ops created a different object.

## The seven stages

| Stage | What happens | Stops when |
| --- | --- | --- |
| 1. Capture | Managed footprint and configuration, the Ops agent object (resource id, type, stat-key count, newest sample), the VM's Ops resource id, the import. | Not Windows; no vCenter id or MOR; no guest credential; connection fails; service query fails; no managed agent and nothing to resume; grains missing or naming another VM; VM or agent object not in Ops; object reported as something other than Product Managed; import blocked; a journaled uninstall task for this VM still running or finished while the services are back. |
| 2. Backup | `telegraf.conf`, `telegraf.d\*`, `mandatory_tags.bat`, grains and the captured object are written verbatim (mode 0600) under the helper's config directory (`takeovers/<vcid>_<mor>/`); a previous backup for the same VM is replaced. | `telegraf.conf` unreadable, or any managed file only partly read. |
| 3. Preflight | The ordinary configure workflow's non-destructive stages run while the managed agent is still up: connectivity, Ops integration and client certificate, rendered configuration, collector reachability, 500 MB free on C:. | Any of those fails. Nothing on the endpoint has changed. |
| 4. Retire | `DELETE /suite-api/api/applications/agents` with the guest credential exactly as typed (UPN for a domain account, bare name for a local one) and `retainTelegrafConf: false`; the task is polled every 10 s to FINISHED (about a minute in the lab). | Ops refuses the request; the task reports a message or an error stage; the task exceeds 5 minutes; the status query errors. The task id is journaled and a re-run reconciles it before anything else (see below). |
| 5. Clean | No managed service may remain. `C:\ProgramData\VMware\UCP\certkeys` is removed; `C:\VMware\UCP` (and an empty `C:\VMware`) only if the uninstall left no files in it, otherwise the leftovers are reported as a warning and left. | A managed service survives, a service still runs from under `C:\VMware\UCP`, the service query fails, or a removal fails. |
| 6. Install | The prepared install is applied: open-source Telegraf (1.40.1 unless another version was chosen) to `C:\telegraf`, the imported inputs, a fresh client certificate, the cloud proxy output with the same `vmId` and `vcid` headers the managed agent used, the tags script. | The ordinary workflow's apply, restart or verify fails. Monitoring is interrupted; a re-run repeats preflight and clean, then installs. |
| 7. Continuity | The agent object is polled every 60 s, for up to the continuity wait (900 s by default, CLI `--continuity-wait`; no GUI setting), for the same resource id, `AgentManagedType = Open Source`, and a sample newer than the cutover. | Never fails the run. A slow Ops is PENDING; a different object that already reports Open Source is CHANGED (a different object not yet flipped is still PENDING); an error during the check is UNKNOWN. Until the check passes, the journal stays at `installed` and detecting the endpoint again offers to re-run only this check. |

Four results are reported separately: managed agent retirement, open-source installation, registration on the same Ops object, fresh metric ingestion. Fresh ingestion requires both a newer sample and the type flip, because Ops computes some stats itself every cycle.

## Interruption and resume

The journal (`~/.config/vcf-ops-telegraf-helper/takeovers/<vcid>_<mor>.json`, mode 0600) records identities, hostname, Ops URL and collector, stage outcomes, the uninstall task id, cutover time, the Ops object before and after, backup paths and the confirmation text (GUI: "Typed '<name>' to confirm the takeover of <vm> at <time>"; CLI: the `--confirm-takeover` value); never passwords, tokens or keys. The backup files themselves are verbatim copies.

If the app closes before the retirement, nothing was changed and the next run starts over. If it closes after, detecting the endpoint again shows **Interrupted Takeover Found** and the box resumes from the backup, on the same host string as the journal (an IP and an FQDN of the same VM do not match) and only while the backup still holds `telegraf.conf`. A retirement that ended in a failed, errored or timed-out task is reconciled first: with the services gone, a task that finished resumes and anything else refuses until Ops is reconciled; with the services still present, a task still running or finished refuses, and a failed task lets a fresh takeover start. A journal that says retired, cleaned or installed while the services are back on the endpoint refuses rather than submit a second uninstall. A takeover interrupted after the install resumes straight into the continuity check. Closing the window during a running takeover is blocked.

## Lab evidence

Two manual runs on October 9, 2026 (tg-w22-01 and tg-w22-02, collector group VIP 172.27.8.54):

| | tg-w22-01 | tg-w22-02 |
| --- | --- | --- |
| Ops uninstall task | 52 s | 61 s |
| Endpoint clean after the task | yes, empty `C:\VMware\UCP` left | same |
| Same object id, flipped to Open Source | 12 min after uninstall | 9 min |
| Full 137-key set including `cpu|usage.*` | 22 min | 13 min |

The object is keyed by vCenter id and VM MOR only, so the open-source output with the same `vmId` and `vcid` headers lands on the same object and history continues; the only gap is the cutover window.

## Validation before release

The feature merges to main across parts 1 to 4, then a test build is cut and validated in the lab before any release:

1. Reinstall the managed agent on tg-w22-01 and tg-w22-02 through Ops.
2. Run the takeover once from the GUI and once from the CLI; confirm the four results, the Ops object, Guest Info (OS name and version, Telegraf version, IP) from the tags script, and the 137 stat keys.
3. Decline the option: nothing changes. Wrong guest credential: the task fails and nothing is installed. Kill the app after the retirement: detect again and resume.
4. Confirm the guest credential formats Ops accepts (UPN worked in Scott's testing; a local account is the bare name).

Rollback outside the helper: run the Ops **Install** on the VM again; the managed agent re-registers on the same object key. A follow-up issue tracks offering that from the helper.
