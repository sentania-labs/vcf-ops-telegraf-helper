"""How a finished run is shown: on the Results step and on a saved Activity entry.

Both screens use the same parts: a headline that counts against what the person picked, one
line per machine with the reason and the button for it, and the command for doing the same
thing from the command line.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import QFileDialog

from vcf_ops_telegraf_helper.gui.widgets import CommandBox, Headline, ListBox, Row, button, esc, mark
from vcf_ops_telegraf_helper.storage.history import (
    KIND_CHANGE,
    KIND_REMOVE,
    KIND_SETUP,
    KIND_TAKEOVER,
    KIND_TEST,
    ActivityEntry,
    ActivityRow,
)
from vcf_ops_telegraf_helper.workflow.plain import (
    STATUS_ATTENTION,
    STATUS_INTERRUPTED,
    STATUS_MANAGED,
    STATUS_NONE,
    STATUS_SENDING,
    STATUS_WAITING,
    plural,
)


def _rank(row: ActivityRow) -> int:
    """Problems first, so a long list never pushes them out of sight."""
    if not row.ok:
        return 0
    if row.left or row.skipped:
        return 1
    return 2 if row.pending else 3


def put_right_since(win, entry: ActivityEntry, row: ActivityRow) -> bool:
    """True only when a later run on this machine really worked."""
    later = win.history.last_change(row.machine_id)
    if later is None or later.id == entry.id or later.at <= entry.at:
        return False
    after = later.row_for(row.machine_id)
    return bool(after is not None and after.ok)


def _fix_button(win, entry: ActivityEntry, row: ActivityRow, machine):
    """The button that puts a failed line right, when the machine is still in a state where it applies."""
    if machine is None or not machine.selectable:
        return None
    name = f"result-retry-{row.name}"
    if machine.status == STATUS_INTERRUPTED:
        return button("Finish the takeover", lambda: win.start_flow("takeover", [machine.id], resume=True), "primary sm",
                      name=f"result-finish-{row.name}")
    if entry.kind == KIND_TAKEOVER:
        if machine.status == STATUS_MANAGED:
            return button("Try again", lambda: win.start_flow("takeover", [machine.id]), "primary sm", name=name)
        return None
    if entry.kind == KIND_CHANGE:
        if machine.status in (STATUS_SENDING, STATUS_WAITING, STATUS_ATTENTION):
            return button("Try again", lambda: win.start_flow("change", [machine.id], retry=True), "primary sm", name=name)
        return None
    if entry.kind == KIND_SETUP and machine.status in (STATUS_ATTENTION, STATUS_NONE):
        return button("Try again", lambda: win.start_flow("setup", [machine.id], retry=True), "primary sm", name=name)
    return None


def result_list(win, entry: ActivityEntry, max_height: Optional[int] = None) -> ListBox:
    """One line per machine: what happened, what to do, and the button that does it."""
    box = ListBox(max_height=max_height, name="result-list")
    tight = (14, 5, 14, 5) if len(entry.rows) > 4 else (14, 9, 14, 9)
    for row in sorted(entry.rows, key=_rank):
        machine = win.machine(row.machine_id)
        line = Row(row.name, kind="" if row.ok else "bad", name_width=170, object_name=f"result-{row.name}", margins=tight)
        if row.left or row.skipped:
            symbol = mark("skip")
        elif not row.ok:
            symbol = mark("bad")
        else:
            symbol = mark("wait") if row.pending else mark("ok")
        text = f"{symbol} " + (esc(row.msg) if row.ok else f"<b>{esc(row.msg)}</b>")
        fixed = bool(not row.ok and not row.test and put_right_since(win, entry, row))
        if fixed:
            text += ('<br><span style="font-size:12px">A later attempt on this machine worked, so there is nothing left '
                     "to do.</span>")
        elif not row.ok and row.todo:
            text += f'<br><span style="font-size:12px"><b>What to do:</b> {esc(row.todo)}</span>'
        line.say(text)
        if not row.ok and not row.test and not fixed:
            fix = _fix_button(win, entry, row, machine)
            if fix is not None:
                line.add_actions(fix)
        elif (row.left or row.skipped) and machine is not None and machine.status in (STATUS_NONE, STATUS_ATTENTION) \
                and machine.selectable and entry.kind == KIND_SETUP:
            line.add_actions(button("Set it up now", lambda m=machine: win.start_flow("setup", [m.id]), "sm",
                                    name=f"result-setup-{row.name}"))
        box.add_row(line)
    return box


def command_box(win, entry: ActivityEntry) -> Optional[CommandBox]:
    commands = [(row.name, row.cmd) for row in sorted(entry.rows, key=_rank) if row.cmd]
    return CommandBox(commands, toast=win.toast) if commands else None


def all_word(count: int) -> str:
    return "Both machines are" if count == 2 else f"All {count} machines are"


def live_headline(entry: ActivityEntry) -> Headline:
    """The headline of a run that has just finished. It never says "all" for fewer than were picked."""
    real = [r for r in entry.rows if r.counted]
    bad = [r for r in real if not r.ok]
    pending = [r for r in real if r.pending]
    noted = [r for r in real if r.note and r.ok]
    skipped = [r for r in entry.rows if r.skipped]
    left = [r for r in entry.rows if r.left]
    picked = len(entry.rows)
    name = "result-headline"
    first = esc(entry.rows[0].name) if entry.rows else "the machine"

    if entry.practice:
        if bad:
            return Headline("bad", "!", "The practice run found a problem. Nothing was changed.",
                            "The reason and what to do are next to the machine below.", name=name)
        if not real:
            return Headline("info", "i", "Stopped. Nothing was changed.", "You stopped the practice run before it finished.",
                            name=name)
        if left or skipped:
            gone = len(left) + len(skipped)
            how = "left out or stopped" if (left and skipped) else ("left out" if left else "stopped")
            return Headline(
                "warn", "!",
                f"Practice run finished for {len(real)} of the {picked} machines you picked. Nothing was changed.",
                f"{'It' if len(real) == 1 else 'They'} passed every step that does not change a machine. {gone} "
                f"{'was' if gone == 1 else 'were'} {how} and {'was' if gone == 1 else 'were'} not practised: see below.",
                name=name)
        return Headline("info", "i", "Practice run finished. Nothing was changed.",
                        "Every step that does not change a machine was carried out, and all of them passed.", name=name)
    if entry.kind == KIND_TAKEOVER:
        if bad:
            return Headline("bad", "!", f"The takeover of {first} did not finish",
                            "What happened and what to do are below.", name=name)
        if noted:
            return Headline("warn", "!", f"{first} has been taken over, with one thing to know",
                            "It is monitored by open-source Telegraf now. Read the line below.", name=name)
        if pending:
            return Headline("info", mark("wait"), f"{first} has been taken over. Waiting for VCF Operations to show its data",
                            "Open-source Telegraf is installed and running. The first data can take up to 15 minutes.",
                            name=name)
        return Headline("ok", mark("ok"), f"{first} has been taken over",
                        "It is monitored by open-source Telegraf now, and its history in VCF Operations is intact.", name=name)
    if entry.kind == KIND_CHANGE:
        if bad:
            return Headline("bad", "!", f"The change was not applied to {first}",
                            "What happened and what to do are below.", name=name)
        if not real:
            return Headline("info", "i", "Stopped. Nothing was changed.", "You stopped before the machine had been changed.",
                            name=name)
        return Headline("ok", mark("ok"), f"The change is in effect on {first}",
                        "The app read the settings back from the machine to confirm.", name=name)
    if bad:
        worked = len(real) - len(bad)
        gone = len(left) + len(skipped)
        if gone:
            # Count against what was picked, and name the ones that were not attempted as well
            how = "left out or stopped" if (left and skipped) else ("left out" if left else "stopped")
            title = (f"{worked} of the {picked} machines you picked {'is' if worked == 1 else 'are'} set up. "
                     f"{len(bad)} need{'s' if len(bad) == 1 else ''} attention, and {gone} "
                     f"{'was' if gone == 1 else 'were'} {how}.")
        else:
            title = (f"{worked} of {plural(len(real), 'machine')} set up. "
                     f"{len(bad)} need{'s' if len(bad) == 1 else ''} attention.")
        return Headline("bad", "!", title, "The ones that worked are not affected." if worked
                        else "What happened and what to do are below.", name=name)
    if not real:
        return Headline("info", "i", "Stopped. Nothing was changed.", "You stopped before any machine had been changed.",
                        name=name)
    if left or skipped:
        gone = len(left) + len(skipped)
        how = "left out or stopped" if (left and skipped) else ("left out" if left else "stopped")
        of_those = (f"Of the {len(real)} set up, {len(real) - len(pending)} already sending data and {len(pending)} waiting "
                    "for first data. ") if pending else ""
        return Headline(
            "warn", "!",
            f"{len(real)} of the {picked} machines you picked {'is' if len(real) == 1 else 'are'} set up. "
            f"{gone} {'was' if gone == 1 else 'were'} {how}.",
            f"{of_those}The machines not set up are listed below with the reason. Nothing was done to them.", name=name)
    title = f"{esc(real[0].name)} is set up" if len(real) == 1 else f"{all_word(len(real))} set up"
    if pending:
        small = ("Waiting for its first data to reach VCF Operations (up to 5 minutes)." if len(real) == 1 else
                 f"{len(real) - len(pending)} already sending data; {len(pending)} waiting for first data (up to 5 minutes).")
    else:
        small = "Data is arriving in VCF Operations."
    return Headline("info" if len(pending) == len(real) else "ok", mark("ok"), title, small, name=name)


def saved_headline(win, entry: ActivityEntry) -> Headline:
    """The headline of a saved entry, read later."""
    bad = [r for r in entry.rows if not r.ok]
    name = "result-headline"
    if bad:
        fixed = entry.kind != KIND_TEST and all(put_right_since(win, entry, r) for r in bad)
        if len(bad) == entry.total:
            title = "This did not work" if entry.total == 1 else "None of these worked"
        else:
            title = f"{entry.worked} of {entry.total} worked. {len(bad)} did not."
        small = ("It has been put right since: see below." if fixed
                 else "The reason and what to do are next to each machine below.")
        return Headline("bad", "!", title, small, name=name)
    if entry.not_done:
        if entry.worked == 0:
            return Headline("info", "i", "Nothing was changed",
                            "Every machine was left out or the run was stopped. Each machine's line below says which.",
                            name=name)
        verb = "set up" if entry.kind == KIND_SETUP else "done"
        return Headline(
            "warn", "!",
            f"{entry.worked} of the {entry.total} machines picked {'was' if entry.worked == 1 else 'were'} {verb}. "
            f"{entry.not_done} {'was' if entry.not_done == 1 else 'were'} left out or stopped.",
            "Each machine's line below says which.", name=name)
    if entry.practice:
        return Headline("ok", mark("ok"), "The practice run passed. Nothing was changed.", name=name)
    if entry.kind == KIND_TEST:
        return Headline("ok", mark("ok"), "The test passed. Nothing was changed.", name=name)
    if entry.kind == KIND_TAKEOVER and any(r.note for r in entry.rows):
        return Headline("warn", "!", "This worked, with one thing to know", "Read the line below.", name=name)
    waiting = sum(1 for r in entry.rows if r.pending)
    if waiting:
        # What was true when the run ended; the Machines list says where each machine stands now
        return Headline(
            "info", mark("wait"),
            "This worked. " + ("It was" if entry.total == 1 else f"{waiting} of {entry.total} were")
            + " still waiting for first data when it finished",
            "The Machines list shows whether the data has arrived since.", name=name)
    if entry.kind == KIND_REMOVE:
        return Headline("ok", mark("ok"), "Monitoring was removed", name=name)
    return Headline("ok", mark("ok"), "This worked", name=name)


def save_report(win, entry: ActivityEntry) -> None:
    """Save the full result as a file the person chooses."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in entry.title.lower()).strip("-")
    suggested = str(Path.home() / f"telegraf-helper-{slug}.md")
    path, _ = QFileDialog.getSaveFileName(win, "Save a report", suggested, "Report (*.md);;All files (*)")
    if not path:
        return
    try:
        Path(path).write_text(entry.report or f"# {entry.title}\n", encoding="utf-8")
    except OSError as exc:
        win.toast(f"The report could not be saved there: {exc.strerror or exc}")
        return
    win.toast(f"Report saved to {path}")


def technical_text(entry: ActivityEntry) -> str:
    return entry.log or "No step-by-step record was kept for this."


def names_summary(rows: List[ActivityRow]) -> str:
    if len(rows) <= 2:
        return ", ".join(r.name for r in rows)
    return f"{rows[0].name} and {len(rows) - 1} more"
