# Taking over an Ops-managed agent

The helper can replace a VCF Operations product-managed Telegraf agent on a Windows VM with open-source Telegraf, keeping the same Ops object and its metric history. This page describes what the helper does, what it refuses, and how the result was proven. The sequence was run by hand twice in the lab on October 9, 2026 (Windows Server 2022, VCF Operations 9.1) before it was built into the helper; issue #61 holds that record.

## When the option appears

- The VM list shows "Reporting, Ops managed" when the agent object's `AgentManagedType` is `Product Managed`.
- Detecting the endpoint in Step 3 finds the managed services (`salt-minion`, `ucp-minion`, `ucp-telegraf` on 9.1, all under `C:\VMware\UCP`). A stock SaltStack minion elsewhere on the box does not count. The banner shows the services, the managed Telegraf version, the VM binding from the salt grains, and the cleanup scope.
- Ordinary onboarding stays blocked until **Take over existing Ops agent** is checked. The Uninstall button is disabled for a managed install; Ops retires its own agent.
- Linux takeover is not supported; a Linux endpoint with the Ops agent is refused as before.

## What checking the box does

1. The managed `telegraf.conf` and `telegraf.d` fragments are ported into the Monitoring Inputs catalog. For the 9.1 stock configuration that is the Windows perf counter set, the `w3wp` process instance, and the three `win.`-prefixed `cpu`, `mem` and `swap` inputs (Windows OS Totals). Anything the catalog cannot express is kept verbatim in the Custom TOML fragment under a visible header; a fragment that does not parse blocks the takeover.
2. The Review page leads with the takeover plan: services, binding, collector, cleanup scope, the sequence, and the preserved/added/changed/retained/dropped summary of the import.
3. The Execute page switches to **Execute Takeover**. Dry-run is not available. Executing asks you to type the VM name; the typed confirmation is journaled verbatim.

From the CLI:

```
vcf-telegraf-helper run ... --vm-id vm-6068 --vc-id <vcenter uuid> --vm-name tg-w22-01 \
  --take-over-managed-agent --confirm-takeover tg-w22-01 [--continuity-wait 900]
```

Input flags other than workload additions (`--nginx`, `--mssql`, `--win-perf-object`, ...) are ignored in takeover mode; the imported configuration is the starting point.

## The seven stages

| Stage | What happens | Stops when |
| --- | --- | --- |
| 1. Capture | Managed footprint and configuration, the Ops agent object (resource id, type, stat-key count, newest sample), the VM's Ops resource id, the import. | Grains missing or naming another VM; object not Product Managed; import blocked; service query failed; no guest credential. |
| 2. Backup | `telegraf.conf`, `telegraf.d\*`, `mandatory_tags.bat`, grains and the captured object are written under the helper's config directory (`takeovers/<vcid>_<mor>/`). | `telegraf.conf` unreadable. |
| 3. Preflight | The ordinary configure workflow's non-destructive stages run while the managed agent is still up: connectivity, Ops integration and client certificate, rendered configuration, collector reachability, disk space. | Any of those fails. Nothing on the endpoint has changed. |
| 4. Retire | `DELETE /suite-api/api/applications/agents` with the guest credential as typed (UPN for a domain account, bare name for a local one) and `retainTelegrafConf: false`; the task is polled to FINISHED (about a minute in the lab). | The task fails, errors or exceeds the timeout (5 minutes). The task id is journaled; a re-run re-polls it before anything else. |
| 5. Clean | No managed service may remain. The certkeys directory is removed; `C:\VMware\UCP` only if the uninstall left it empty, otherwise it is reported and left. | A managed service survives, or the service query fails. |
| 6. Install | The prepared install is applied: open-source Telegraf 1.40.1 to `C:\telegraf`, the imported inputs, a fresh client certificate, the cloud proxy output with the same `vmId` and `vcid` headers the managed agent used, the tags script. | The ordinary workflow's apply, restart or verify fails. Monitoring is interrupted; re-running resumes here. |
| 7. Continuity | The agent object is polled (bounded by the continuity wait) for the same resource id, `AgentManagedType = Open Source`, and a sample newer than the cutover. | Never fails the run: a slow Ops is PENDING, a different object is CHANGED, both reported. |

Four results are reported separately: managed agent retirement, open-source installation, registration on the same Ops object, fresh metric ingestion. Fresh ingestion requires both a newer sample and the type flip, because Ops computes some stats itself every cycle.

## Interruption and resume

The journal records identities, stage outcomes, the uninstall task id, backup paths and the confirmation; never passwords, tokens or keys. If the app closes after the retirement, detecting the endpoint again shows **Interrupted Takeover Found** and the box resumes from the backup on the same endpoint only. A retirement whose task timed out is re-polled first: finished means resume, still running means wait, failed means a fresh takeover may start. A takeover interrupted after the install resumes straight into the continuity check.

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
