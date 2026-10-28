"""Discovery and post-retirement cleanup of the Linux Ops agent."""
from __future__ import annotations

import json
import shlex

from vcf_ops_telegraf_helper.workflow.managed import LINUX_PATHS, ManagedInstallation, read_managed_config

ROOT = LINUX_PATHS.root
REGISTRY = ROOT + "/uaf/agents-registry.json"
SERVICE_NAMES = ("salt-minion", "ucp-salt-minion", "ucp-minion", "ucp-telegraf")


def shell(executor, script, timeout=30):
    # SSHExecutor elevates bash as a whole, including every pipeline/redirection.
    return executor.execute("bash -c " + shlex.quote(script), timeout=timeout)


def detect_linux_managed_installation(executor, read_config=True):
    found = ManagedInstallation(LINUX_PATHS)
    result = shell(executor, """set -e
# Include disabled units and loaded units whose files were removed.
files=$(systemctl list-unit-files --type=service --no-legend --no-pager)
loaded=$(systemctl list-units --all --type=service --no-legend --no-pager --plain)
units=$(printf '%s\\n%s\\n' "$files" "$loaded" | awk '$1 !~ /@\\.service$/ {print $1}' | sort -u)
if [ -n "$units" ]; then
    systemctl show --no-pager --property=Id,LoadState,ActiveState,SubState,ExecStart,FragmentPath $units
fi""", timeout=30)
    if not result.success:
        found.query_error = ("Could not list systemd services. Check that the SSH account has passwordless sudo rights "
                             "to run systemctl, then detect the endpoint again. This check made no changes.")
        return found
    for block in result.stdout.strip().split("\n\n"):
        if not block.strip():
            continue
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not props.get("Id") or not props.get("LoadState"):
            found.query_error = ("The systemd service response was incomplete. Check systemctl on the VM, then detect "
                                 "the endpoint again. This check made no changes.")
            return found
        name = props["Id"].removesuffix(".service")
        command = props.get("ExecStart", "")
        fragment = props.get("FragmentPath", "")
        if props["LoadState"] == "not-found":
            continue
        if ROOT + "/" in command or fragment.startswith(ROOT + "/") or name.startswith("ucp-"):
            state = "Running" if props.get("ActiveState") == "active" else props.get("ActiveState", "unknown")
            found.services[name] = (state, command)
    found.query_ok = True
    if not read_config:
        return found
    found.root_present = executor.file_exists(ROOT)
    found.cert_dir_present = executor.file_exists(LINUX_PATHS.cert_dir)
    if not found.present:
        return found
    version = shell(executor, shlex.quote(ROOT + "/ucp-telegraf/usr/bin/telegraf") + " version", timeout=10)
    if version.success and version.stdout.strip():
        found.telegraf_version = version.stdout.strip().splitlines()[0]
    read_managed_config(executor, found)
    # Preserve the uninstall manifest before Ops removes it. No key material is backed up.
    for relative in ("uaf/agents-registry.json", "tmp/telegraf_post_install_linux.sh"):
        path = ROOT + "/" + relative
        try:
            if executor.file_exists(path):
                found.extra_files[relative] = executor.download(path)
        except Exception:
            found.read_errors.append(f"Could not read {path}")
    return found


def vendor_uninstall_commands(registry):
    """Accept explicit uninstall commands in nested service records; never infer a hook invocation."""
    commands = []
    if not registry:
        return commands
    data = json.loads(registry)

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower().replace('_', '').replace('-', '') in ("uninstallcommand", "uninstallcmd"):
                    if not isinstance(child, str):
                        raise ValueError("Unsupported vendor uninstall command")
                    args = shlex.split(child)
                    # Run argv, not arbitrary shell source from the registry. Only vendor executables/hooks.
                    executable = args[1] if args and args[0] in ("sh", "bash", "/bin/sh", "/bin/bash") and len(args) > 1 else (args[0] if args else "")
                    if not executable.startswith(ROOT + "/") or ".." in executable.split("/"):
                        raise ValueError("Vendor uninstall command is outside the managed directory")
                    if any(token in child for token in (";", "|", "&", "`", "$", "\n", ">", "<")):
                        raise ValueError("Unsupported shell syntax in vendor uninstall command")
                    commands.append(shlex.join(args))
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    return list(dict.fromkeys(commands))


def clean_linux_managed_installation(executor, registry=None):
    """Return removed paths and retained items; fail before OSS install on surviving services/processes."""
    removed, left = [], []
    before = detect_linux_managed_installation(executor, read_config=False)
    if not before.query_ok:
        raise RuntimeError(before.query_error)
    roots = shell(executor, f"[ ! -L /opt/vmware ] && [ ! -L {ROOT} ]")
    if not roots.success:
        raise RuntimeError("Managed root is redirected; cleanup cannot proceed safely")
    try:
        commands = vendor_uninstall_commands(registry)
        if registry and not commands:
            left.append("vendor uninstall manifest has no supported commands; checked cleanup used instead")
    except (ValueError, TypeError):
        commands = []
        left.append("vendor uninstall manifest could not be used; checked cleanup used instead")
    for command in commands:
        # A successful Ops retirement may already have removed the hook.
        args = shlex.split(command)
        executable = args[1] if args[0] in ("sh", "bash", "/bin/sh", "/bin/bash") else args[0]
        if executor.file_exists(executable):
            result = shell(executor, command, timeout=120)
            if not result.success:
                left.append("vendor uninstall command failed; checked cleanup used instead")
    after_vendor = detect_linux_managed_installation(executor, read_config=False)
    if not after_vendor.query_ok:
        raise RuntimeError(after_vendor.query_error)
    names = sorted(after_vendor.services)
    # Unit ownership was established by discovery. Do not touch a stock Salt install elsewhere.
    for name in names:
        unit = shlex.quote(name + ".service")
        result = shell(executor, f"""set -e
unit={unit}
load=$(systemctl show "$unit" --property=LoadState --value)
if [ "$load" != not-found ]; then
    systemctl stop "$unit"
    fragment=$(systemctl show "$unit" --property=FragmentPath --value)
    systemctl disable "$unit"
    case "$fragment" in
        /etc/systemd/system/*|/usr/lib/systemd/system/*|/lib/systemd/system/*) rm -f -- "$fragment" ;;
        {ROOT}/*|'') ;;
        *) exit 1 ;;
    esac
    for base in /etc/systemd/system /usr/lib/systemd/system /lib/systemd/system; do
        rm -rf -- "$base/$unit.d"
    done
fi""", timeout=60)
        if not result.success:
            raise RuntimeError(f"Could not remove managed unit {name}")
    # Fail on a surviving process before removing its executable. Exclude this shell and ancestors.
    result = shell(executor, f"""set -e
ancestors=" $$ "
p=$PPID
while [ "$p" -gt 1 ]; do
    ancestors="$ancestors$p "
    p=$(awk '/^PPid:/ {{print $2}}' /proc/$p/status) || exit 1
done
for proc in /proc/[0-9]*; do
    pid=${{proc##*/}}
    case "$ancestors" in *" $pid "*) continue ;; esac
    exe=$(readlink "$proc/exe" 2>/dev/null) || continue
    args=$(tr '\\0' ' ' < "$proc/cmdline" 2>/dev/null) || continue
    case "$exe $args" in *{ROOT}/*) echo 'Managed process remains'; exit 1 ;; esac
done
# Refuse redirected managed roots before recursive removal.
[ ! -L /opt/vmware ] && [ ! -L {ROOT} ]
for path in salt-minion ucp-salt-minion ucp-minion ucp-telegraf certkeys; do
    rm -rf -- "{ROOT}/$path"
done
systemctl daemon-reload
# Remove empty directories only; unknown files stay visible for review.
if [ -d {ROOT} ]; then find {ROOT} -depth -type d -empty -delete; fi
""", timeout=60)
    if not result.success:
        raise RuntimeError("Managed processes or files could not be removed safely")
    after = detect_linux_managed_installation(executor, read_config=False)
    if not after.query_ok or after.present:
        raise RuntimeError("Managed services remain or their removal could not be verified")
    for name in ("salt-minion", "ucp-salt-minion", "ucp-minion", "ucp-telegraf", "certkeys"):
        path = ROOT + "/" + name
        if executor.file_exists(path):
            raise RuntimeError(f"Managed files remain under {path}")
        removed.append(path)
    if executor.file_exists(ROOT):
        left.append(ROOT + " still holds files the Ops uninstall left; review and delete them by hand")
    # No known vendor artifact proves account creation, so even an idle account is not safe to delete.
    account = shell(executor, """set -e
if getent passwd arcuser >/dev/null || getent group arcgroup >/dev/null; then echo RETAINED; fi
for entry in /etc/sudoers /etc/sudoers.d/*; do
    [ -f "$entry" ] || continue
    if grep -Eq '(^|[[:space:],])(arcuser|%arcgroup)([[:space:],]|$)' "$entry"; then
        echo RETAINED
    else
        code=$?
        [ "$code" -eq 1 ] || exit "$code"
    fi
done""")
    if not account.success:
        raise RuntimeError("Could not check the managed account")
    if "RETAINED" in account.stdout:
        left.append("the arcuser account, arcgroup group and their sudoers rule were kept because the helper "
                    "cannot prove the Ops installer created them; remove them by hand if nothing else uses them")
    return removed, left
