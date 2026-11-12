"""Plain words for what the program knows.

The six machine statuses, the five steps a person sees while a machine is worked on, and the
table that turns a failed stage into a reason and something to do. Nothing here talks to a
machine; it only translates.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from vcf_ops_telegraf_helper.models.vcf import VirtualMachineResource
from vcf_ops_telegraf_helper.models.workflow import StageResult, StageStatus, TakeoverStage, WorkflowStage
from vcf_ops_telegraf_helper.storage.history import KIND_REMOVE, ActivityEntry

APP_NAME = "Telegraf Helper"
APP_TAGLINE = "for VCF Operations"

COLLECTOR_IS = "A collector is the part of VCF Operations that receives the measurements."
FIRST_DATA_WAIT = "VCF Operations takes up to 5 minutes to show the first data."

# ---------------------------------------------------------------------------------------------
# Machine statuses
# ---------------------------------------------------------------------------------------------
STATUS_NONE = "none"
STATUS_SENDING = "sending"
STATUS_WAITING = "waiting"
STATUS_MANAGED = "managed"
STATUS_ATTENTION = "attention"
STATUS_INTERRUPTED = "interrupted"

STATUS_TEXT = {
    STATUS_NONE: "Not set up",
    STATUS_SENDING: "Sending data",
    STATUS_WAITING: "Set up, waiting for first data",
    STATUS_MANAGED: "VCF Operations' own agent",
    STATUS_ATTENTION: "Needs attention",
    STATUS_INTERRUPTED: "Takeover interrupted",
}
# The colour family of each status pill: "" (neutral), ok, acc (accent), warn, bad
STATUS_TONE = {
    STATUS_NONE: "",
    STATUS_SENDING: "ok",
    STATUS_WAITING: "acc",
    STATUS_MANAGED: "warn",
    STATUS_ATTENTION: "bad",
    STATUS_INTERRUPTED: "bad",
}

# How long after the app removed monitoring an old "Reporting" answer from VCF Operations is ignored
_REMOVAL_GRACE_SECONDS = 30 * 60
# How long a machine this app set up may wait for its first data before that counts as a problem
_FIRST_DATA_LIMIT_SECONDS = 60 * 60

INTERRUPTED_STATES = ("retired", "cleaned", "retire_failed")


@dataclass
class Problem:
    what: str
    todo: str


@dataclass
class MachineStatus:
    status: str
    problem: Optional[Problem] = None
    last: str = ""


def machine_status(
    vm: VirtualMachineResource,
    last_change: Optional[ActivityEntry] = None,
    takeover_state: Optional[str] = None,
    now: Optional[float] = None,
) -> MachineStatus:
    """One of the six statuses for a machine.

    VCF Operations is the truth for "sending data". The app's own history adds "needs
    attention" and "waiting for first data", and the takeover record adds "takeover
    interrupted". When they disagree (someone changed the machine by hand) VCF Operations wins.
    """
    now = now if now is not None else time.time()
    row = last_change.row_for(vm.resource_id) if last_change else None
    last = _last_sentence(last_change, row)

    if takeover_state in INTERRUPTED_STATES:
        return MachineStatus(STATUS_INTERRUPTED, Problem(
            "The old agent was removed, but open-source Telegraf was not installed yet. The machine is not monitored right now.",
            'Press "Finish the takeover". The app carries on from where it stopped; nothing is done twice.',
        ), last)
    reporting = vm.telegraf_status == "Reporting"
    # Right after this app took a machine over, VCF Operations may still describe the old agent
    taken_over = bool(
        last_change and row and row.ok and last_change.kind == "takeover"
        and now - last_change.at < _FIRST_DATA_LIMIT_SECONDS
    )
    if vm.is_ops_managed and vm.agent_registrations:
        if not taken_over:
            return MachineStatus(STATUS_MANAGED, None, last)
        return MachineStatus(STATUS_WAITING, None, last)
    removed_recently = bool(
        last_change and row and last_change.kind == KIND_REMOVE and row.ok
        and now - last_change.at < _REMOVAL_GRACE_SECONDS
    )
    if reporting and not removed_recently:
        return MachineStatus(STATUS_SENDING, None, last)
    if last_change and row:
        if last_change.kind == KIND_REMOVE and row.ok:
            return MachineStatus(STATUS_NONE, None, last)
        if not row.ok:
            return MachineStatus(STATUS_ATTENTION, Problem(row.msg, row.todo), last)
        if now - last_change.at < _FIRST_DATA_LIMIT_SECONDS:
            # Set up by this app a short while ago; VCF Operations has not listed it as reporting yet
            return MachineStatus(STATUS_WAITING, None, last)
        return MachineStatus(STATUS_ATTENTION, Problem(
            "Telegraf was set up on this machine, but VCF Operations is not showing any data from it.",
            "Press Try again. The app checks the machine, shows what is in the way, and sets it up again.",
        ), last)
    if takeover_state == "installed":
        return MachineStatus(STATUS_WAITING, None, last)
    if vm.telegraf_status == "No data" and vm.agent_registrations:
        return MachineStatus(STATUS_ATTENTION, Problem(
            "VCF Operations knows Telegraf on this machine, but no data is arriving from it.",
            "Press Try again. The app checks the machine, shows what is in the way, and sets it up again.",
        ), last)
    return MachineStatus(STATUS_NONE, None, last)


def _last_sentence(entry: Optional[ActivityEntry], row: Any) -> str:
    if entry is None or row is None or not row.ok:
        return ""
    verb = {"setup": "Set up", "change": "Changed", "takeover": "Taken over", "remove": "Monitoring removed"}.get(entry.kind)
    if not verb:
        return ""
    when = entry.when()
    when = when[0].lower() + when[1:] if when.startswith(("Today", "Yesterday")) else "on " + when
    return f"{verb} {when}."


# ---------------------------------------------------------------------------------------------
# The steps a person sees while one machine is worked on
# ---------------------------------------------------------------------------------------------
RUN_STEPS = [
    "Signing in",
    "Preparing the settings (nothing is changed yet)",
    "Installing Telegraf",
    "Writing the settings and starting Telegraf",
    "Checking that data is flowing",
]
PRACTICE_STEPS = [
    "Signing in",
    "Preparing the settings",
    "Checking that Telegraf could be installed",
    "Checking that the settings would be accepted",
    "Checking that the collector would take the data",
]
CHANGE_STEPS = [
    "Signing in",
    "Preparing the new settings (nothing is changed yet)",
    "Writing the new settings",
    "Restarting Telegraf",
    "Reading the settings back from the machine",
]
# Steps 0 and 1 change nothing on the machine, so a run can still be stopped during them
SAFE_STEPS = 2

TAKEOVER_NOTES = ["", "About a minute.", "", "Usually 10 to 15 minutes."]


def takeover_steps(version: str) -> List[str]:
    return [
        "Save a copy of the machine's current monitoring settings on this computer",
        "Ask VCF Operations to remove its own agent",
        f"Install open-source Telegraf {version} and give it the same job",
        "Wait until VCF Operations shows data arriving on the same machine record",
    ]


def step_label(kind: str, step: int, practice: bool = False, install: str = "install") -> str:
    """The words for one step, matching what really happens to this machine.

    install is one of: install, keep, update, push.
    """
    if practice:
        return PRACTICE_STEPS[step]
    if kind == "change":
        return CHANGE_STEPS[step]
    if step == 2:
        return {
            "push": "Sending Telegraf from this computer and installing it",
            "update": "Updating Telegraf",
            "keep": "Keeping the Telegraf that is already installed",
        }.get(install, RUN_STEPS[2])
    return RUN_STEPS[step]


def step_for_stage(stage: Any, kind: str = "setup") -> int:
    """Which of the five plain steps an engine stage belongs to, as it starts."""
    order = {
        WorkflowStage.CONNECT: 0,
        WorkflowStage.DETECT: 1,
        WorkflowStage.PREPARE_VCF: 1,
        WorkflowStage.RENDER_INPUTS: 1,
        WorkflowStage.VALIDATE: 1,
        WorkflowStage.APPLY: 2,
        WorkflowStage.RESTART: 3,
        WorkflowStage.VERIFY: 4,
    }
    return order.get(stage, 1)


def takeover_step_for_stage(stage: Any) -> int:
    """Which of the four plain takeover steps a takeover stage belongs to."""
    return {
        TakeoverStage.CAPTURE: 0,
        TakeoverStage.BACKUP: 0,
        TakeoverStage.PREFLIGHT: 0,
        TakeoverStage.RETIRE: 1,
        TakeoverStage.CLEAN: 1,
        TakeoverStage.INSTALL: 2,
        TakeoverStage.CONTINUITY: 3,
    }.get(stage, 0)


# ---------------------------------------------------------------------------------------------
# From a failed stage to a reason and something to do
# ---------------------------------------------------------------------------------------------
_TRY_AGAIN = "Press Try again."
_SUPPORT = ("Press Try again. If it happens again, open Technical details on this result and send them to "
            "whoever supports this app.")

# (stage or None for any, pattern in the stage message, what happened, what to do). First match wins.
_FAILURES: Sequence[tuple] = (
    (WorkflowStage.CONNECT, r"auth|password|permission denied|credential|401|access is denied|rejected|logon",
     "The machine did not accept that user name or password.",
     "Press Try again and give the sign-in that works on this machine."),
    (WorkflowStage.CONNECT, r".",
     "The app could not reach the machine to sign in.",
     "Check that the machine is switched on and that this computer can reach it on the network, then press Try again."),
    (WorkflowStage.DETECT, r"managed by VCF Operations",
     "This machine is monitored by VCF Operations' own agent.",
     'Replacing that agent is a takeover: use "Take over" on its row in the Machines list.'),
    (WorkflowStage.DETECT, r"Telegraf not installed",
     "Telegraf is not installed on this machine, and this run was told not to install it.",
     "Press Try again; the app installs Telegraf as part of the setup."),
    (WorkflowStage.PREPARE_VCF, r"Cannot reach VCF Operations",
     "This computer lost its connection to VCF Operations.",
     "Check that this computer is still on the office network or VPN, then press Try again."),
    (WorkflowStage.PREPARE_VCF, r".",
     "VCF Operations did not hand over what the machine needs to send its data.",
     "Check that your VCF Operations account is allowed to manage Telegraf agents, then press Try again. "
     "The exact answer from VCF Operations is under Technical details."),
    (WorkflowStage.RENDER_INPUTS, r"unmanaged inputs",
     "Telegraf on this machine already has its own settings, which this app did not write.",
     "Nothing was changed. Ask whoever looks after the machine to move those settings out of the main Telegraf "
     "file, then press Try again."),
    (WorkflowStage.RENDER_INPUTS, r"TOML|Render",
     "The settings could not be put together. If you added your own Telegraf settings, they contain a mistake.",
     'Nothing was changed. Press Try again and check "your own Telegraf settings" on the What to collect step.'),
    (WorkflowStage.VALIDATE, r"not reachable|unreachable",
     "The machine could not reach the collector (where its data is sent).",
     COLLECTOR_IS + " A firewall between the machine and the collector is the usual cause. Press Try again: the app "
     "tests every collector from this machine and offers one it can reach."),
    (WorkflowStage.VALIDATE, r".",
     "The choices for this machine do not fit together.",
     "Nothing was changed. Press Try again and check what is ticked on the What to collect step."),
    (WorkflowStage.APPLY, r"Insufficient disk space",
     "The machine does not have enough free disk space. The app needs 500 MB free to install Telegraf.",
     "Free some space on the machine, then press Try again."),
    (WorkflowStage.APPLY, r"sudo",
     "The account is not allowed to install software on the machine.",
     'Press Try again and sign in with the "root" account, or with one that has full "sudo" rights without being '
     "asked for a password."),
    (WorkflowStage.APPLY, r"did not arrive intact",
     "The copy of Telegraf sent from this computer did not arrive on the machine intact.",
     "Nothing was installed. Press Try again; the app sends it again."),
    (WorkflowStage.APPLY, r"could not be sent from this computer",
     "Telegraf could not be sent from this computer to the machine.",
     "This computer downloads Telegraf first, so check that it can reach the internet. Then press Try again."),
    (WorkflowStage.APPLY, r"auto-install|binary not found|Failed to (auto-install|upgrade)",
     "The machine could not download Telegraf. It has no way out to the internet.",
     "Press Try again. The app will offer to download Telegraf on this computer and send it across instead."),
    (WorkflowStage.APPLY, r"upgrade to .* failed|installed version remains",
     "Telegraf on the machine could not be updated.",
     "The version it had is still in place. Press Try again and keep the version it has."),
    (WorkflowStage.APPLY, r".",
     "The settings could not be written to the machine.",
     _SUPPORT),
    (WorkflowStage.RESTART, r"validation failed|Restored previous",
     "Telegraf did not accept the new settings, so the app put the previous settings back.",
     "The machine is as it was before. " + _SUPPORT),
    (WorkflowStage.RESTART, r".",
     "Telegraf did not start on the machine.",
     _SUPPORT),
    (WorkflowStage.VERIFY, r"Metrics transmission",
     "Telegraf is installed and running, but the collector refused its data.",
     "Press Try again. If it happens again, ask whoever runs VCF Operations to check that the collector accepts "
     "this machine."),
    (WorkflowStage.VERIFY, r"Service running",
     "Telegraf was set up but is not running on the machine.",
     _SUPPORT),
    (WorkflowStage.VERIFY, r".",
     "The setup finished, but one of the checks afterwards did not pass.",
     _SUPPORT),
    (TakeoverStage.CAPTURE, r"No Ops-managed agent found",
     "VCF Operations' own agent was not found on this machine.",
     "Nothing was changed. Press Refresh list on Machines: the machine may already have been taken over or changed "
     "by someone else."),
    (TakeoverStage.CAPTURE, r"connect",
     "The app could not sign in to the machine.",
     "Nothing was changed. Check the sign-in and that the machine is switched on, then try the takeover again."),
    (TakeoverStage.CAPTURE, r"bound to|grains|ambiguous",
     "The agent on this machine belongs to a different machine record in VCF Operations.",
     "Nothing was changed. Check that the address of this machine is right in VCF Operations, then try again."),
    (TakeoverStage.CAPTURE, r"cannot be ported",
     "The machine's current monitoring settings contain something the app cannot carry over safely.",
     "Nothing was changed. The detail is under Technical details; ask whoever looks after the machine to simplify "
     "its settings, then try again."),
    (TakeoverStage.CAPTURE, r".",
     "The app could not read the machine's current monitoring settings.",
     "Nothing was changed. " + _SUPPORT),
    (TakeoverStage.BACKUP, r".",
     "The app could not save a copy of the machine's current settings on this computer.",
     "Nothing was changed. Check that this computer has free disk space, then try again."),
    (TakeoverStage.PREFLIGHT, r"not reachable|unreachable",
     "The machine cannot reach the collector, so the replacement would not be able to send data.",
     "Nothing was changed: VCF Operations' own agent is still running. " + COLLECTOR_IS
     + " Ask for the firewall between the machine and the collector to be opened, then try again."),
    (TakeoverStage.PREFLIGHT, r".",
     "The replacement could not be prepared.",
     "Nothing was changed: VCF Operations' own agent is still running. " + _SUPPORT),
    (TakeoverStage.RETIRE, r".",
     "VCF Operations did not finish removing its own agent.",
     'The machine may not be monitored right now. Press "Finish the takeover": the app checks with VCF Operations '
     "and carries on from where it stopped."),
    (TakeoverStage.CLEAN, r".",
     "The old agent left something behind on the machine that the app could not clear.",
     'The machine is not monitored right now. Press "Finish the takeover" to try again from where it stopped.'),
    (TakeoverStage.INSTALL, r".",
     "The old agent was removed, but open-source Telegraf could not be installed.",
     'The machine is not monitored right now. Press "Finish the takeover" to try again from where it stopped. '
     "A copy of the old settings is safe on this computer."),
)


def explain_failure(stages: Sequence[StageResult]) -> Problem:
    """The reason and the remedy for the first failed stage of a run."""
    failed = next((s for s in stages if s.status == StageStatus.FAIL), None)
    if failed is None:
        return Problem("The run stopped before it finished.", _TRY_AGAIN)
    text = f"{failed.message} {failed.details or ''}"
    for stage, pattern, what, todo in _FAILURES:
        if stage == failed.stage and re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            if stage == WorkflowStage.APPLY and "Insufficient disk space" in text:
                found = re.search(r"\((\d+) MB free", text)
                if found:
                    what = (f"The machine does not have enough free disk space ({found.group(1)} MB free). "
                            "The app needs 500 MB free to install Telegraf.")
            return Problem(what, todo)
    return Problem("Something went wrong part-way through.", _SUPPORT)


def explain_exception(error: str) -> Problem:
    """A run that could not even start (the worker raised)."""
    return Problem("The app could not start work on this machine.",
                   _SUPPORT + (f" What the app saw: {error}" if error else ""))


def stage_log(name: str, stages: Sequence[StageResult], practice: bool = False, extra: Sequence[str] = ()) -> str:
    """The step-by-step log of one machine, for Technical details."""
    title = f"== {name}{' (practice run: nothing was written, installed or restarted)' if practice else ''} =="
    lines = [title]
    for s in stages:
        stage = s.stage.value if hasattr(s.stage, "value") else str(s.stage)
        lines.append(f"[{stage}] {s.status.value:<7} {s.message}")
        if s.details:
            lines.extend("    " + ln for ln in str(s.details).splitlines())
    lines.extend(extra)
    return "\n".join(lines)


def plural(count: int, one: str, many: Optional[str] = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def join_names(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
