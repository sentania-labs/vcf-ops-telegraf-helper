"""Activity (everything the app has done), one past run, and Settings."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLineEdit, QSizePolicy, QWidget

from vcf_ops_telegraf_helper.gui import dialogs
from vcf_ops_telegraf_helper.gui.model import AUTO, collector_key
from vcf_ops_telegraf_helper.gui.results import (
    command_box,
    names_summary,
    result_list,
    save_report,
    saved_headline,
    technical_text,
)
from vcf_ops_telegraf_helper.gui.widgets import (
    adopt,
    Banner,
    Card,
    ListBox,
    Page,
    Pill,
    box,
    busy_bar,
    button,
    esc,
    grid,
    hbox,
    label,
    link,
    mark,
    sentence,
    stretch,
    vbox,
)
from vcf_ops_telegraf_helper.logger import get_log_file_path
from vcf_ops_telegraf_helper.storage.history import KIND_SETUP, ActivityEntry, format_when
from vcf_ops_telegraf_helper.workflow.checks import collector_label
from vcf_ops_telegraf_helper.workflow.collect import CATALOG, GROUP_BASICS, name_of, ordered
from vcf_ops_telegraf_helper.workflow.connect import normalize_address
from vcf_ops_telegraf_helper.workflow.plain import COLLECTOR_IS, plural
from vcf_ops_telegraf_helper.workflow.plan import DEFAULT_TELEGRAF_VERSION, TELEGRAF_VERSIONS

DOTS = "…"


# ---------------------------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------------------------
def result_word(entry: ActivityEntry) -> Pill:
    """The outcome of a past run in a few words."""
    bad = entry.failed
    if not bad and entry.not_done:
        if entry.worked == 0:
            return Pill("Nothing changed", "warn")
        return Pill(f"{entry.worked} of {entry.total} {'set up' if entry.kind == KIND_SETUP else 'done'}", "warn")
    if not bad:
        return Pill("Passed" if (entry.practice or entry.kind == "test") else "Worked", "ok")
    if entry.worked:
        return Pill(f"{bad} of {entry.total} did not work", "bad")
    return Pill("Did not work", "bad")


def activity_page(win) -> Page:
    entries = win.history.entries()
    page = Page("activity")
    if not entries:
        page.title("Activity", "Everything this app does is recorded here.")
        empty = vbox(
            label("Nothing has been done yet", "h2-big", name="activity-empty"),
            label("When you set up, change, test or remove monitoring, the result is saved here so you can look it up "
                  "later.", "sub"),
            hbox(stretch, button("Go to Machines", lambda: win.show_machines(), "primary", name="activity-go-machines"),
                 stretch), spacing=10)
        for index in range(2):
            empty.itemAt(index).widget().setAlignment(Qt.AlignHCenter)
        holder = box(empty)
        holder.setMaximumWidth(440)
        page.body.addStretch(1)
        page.body.addLayout(hbox(stretch, holder, stretch))
        page.body.addStretch(1)
        return page
    find = QLineEdit()
    find.setObjectName("activity-find")
    find.setPlaceholderText("Find by machine or date")
    find.setAccessibleName("Find past activity by machine or date")
    find.setFixedWidth(200)
    find.setClearButtonEnabled(True)
    heading = vbox(label("Activity", "h1", name="page-title"),
                   label("Everything this app has done, newest first. Open a line to see the full result.", "sub",
                         name="page-sub"), spacing=3)
    page.body.addLayout(hbox((box(heading), 1), find, align=Qt.AlignTop))
    table = ListBox(name="activity-list")
    header = QFrame()
    header.setProperty("class", "thead")
    head = QHBoxLayout(header)
    head.setContentsMargins(14, 7, 14, 7)
    head.setSpacing(10)
    widths = ((150, "WHEN"), (190, "WHAT WAS DONE"), (None, "MACHINES"), (170, "RESULT"), (70, ""))
    for width, text in widths:
        cell = label(text, "th", wrap=False)
        if width:
            cell.setFixedWidth(width)
            head.addWidget(cell)
        else:
            head.addWidget(cell, 1)
    table.set_header(header)
    table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    page.body.addWidget(table, 1)

    def redraw(text: str = "") -> None:
        query = text.strip().lower()
        table.clear()
        shown = [e for e in entries if not query
                 or query in f"{e.title} {e.when()} {' '.join(r.name for r in e.rows)}".lower()]
        if not shown:
            empty = vbox(label(f'Nothing matches "{esc(text.strip())}"', "h2-big", name="activity-no-match"),
                         label("Try a machine name, or part of one.", "sub"), spacing=8, margins=(28, 28, 28, 28))
            for index in range(2):
                empty.itemAt(index).widget().setAlignment(Qt.AlignHCenter)
            table.add_row(box(empty))
        for entry in shown:
            row = QFrame()
            row.setObjectName(f"activity-{entry.id}")
            row.setProperty("class", "mrow")
            row.setMinimumHeight(38)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(14, 4, 14, 4)
            layout.setSpacing(10)
            when = label(esc(entry.when()), "cell", wrap=False)
            when.setFixedWidth(150)
            what = button(entry.title, lambda e=entry: win.show_entry(e.id), "namebtn", name=f"activity-title-{entry.id}")
            what.setFixedWidth(190)
            who = label(esc(names_summary(entry.rows)), "cell", wrap=False)
            pill_box = box(hbox(result_word(entry), stretch))
            pill_box.setFixedWidth(170)
            opener = button("Open", lambda e=entry: win.show_entry(e.id), "sm", name=f"activity-open-{entry.id}")
            open_box = box(hbox(stretch, opener))
            open_box.setFixedWidth(70)
            layout.addWidget(when)
            layout.addWidget(what)
            layout.addWidget(who, 1)
            layout.addWidget(pill_box)
            layout.addWidget(open_box)
            table.add_row(row)

    find.textChanged.connect(redraw)
    redraw()
    return page


def entry_page(win, entry: ActivityEntry) -> Page:
    from_machine = win.machine(getattr(win, "entry_from_machine", None))
    page = Page("entry", margins=(22, 16, 22, 12), spacing=9)
    page.entry_id = entry.id  # type: ignore[attr-defined]
    if from_machine is not None:
        back = link(f"← Back to {from_machine.name}", lambda: win.show_machine(from_machine.id), name="entry-back")
    else:
        back = link("← All activity", win.show_activity, name="entry-back")
    page.body.addLayout(vbox(hbox(back, stretch), label(esc(entry.title), "h1", name="page-title"),
                             label(f"{esc(entry.when())} &middot; {plural(entry.total, 'machine')}", "sub", name="page-sub"),
                             spacing=3))
    page.add(saved_headline(win, entry))
    command = command_box(win, entry)
    rows = result_list(win, entry, max_height=230 if command is not None else 400)
    rows.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    page.add(rows)
    if command is not None:
        page.add(command)
    page.body.addLayout(sentence(esc(entry.detail), link("Technical details", lambda: dialogs.show_text(
        win, "Technical details", technical_text(entry), "For people who want the detail. You never need to read this.",
        name="technical-dialog"), name="entry-technical")))
    page.end()
    page.footer(button(f"Save a report{DOTS}", lambda: save_report(win, entry), name="entry-report"), stretch)
    return page


# ---------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------
TABS = [("conn", "Connection"), ("defaults", "New setups"), ("look", "This app"), ("adv", "Advanced")]

SAVED_SAYS = {
    "collector": "The Review step of the next setup will show this collector, and say it is your choice.",
    "version": "The Check step of the next setup will say this version is installed, and that it is your choice.",
    "existing": "The Check step of the next setup will say what happens to a machine that already has Telegraf, and that "
                "it is your choice.",
    "check_ops": "Used the next time the app connects to VCF Operations.",
    "cert_file": "Used the next time the app connects to VCF Operations.",
    "check_collector": "Used from the next setup, and shown in the command on its result.",
    "win_mode": "Used the next time the app signs in to a Windows machine.",
    "updates": "",
    "theme": "",
}


def _setrow(title: str, small: str, control, last: bool = False, name: Optional[str] = None) -> QFrame:
    row = QFrame()
    row.setProperty("class", "setrow-last" if last else "setrow")
    if name:
        row.setObjectName(name)
    words = box(vbox(label(f"<b>{title}</b>", "plain"), label(small, "small"), spacing=1))
    words.setFixedWidth(250)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 11, 0, 11)
    layout.setSpacing(14)
    layout.addWidget(words, 0, Qt.AlignTop)
    if isinstance(control, QWidget):
        layout.addWidget(control, 1)
    else:
        layout.addLayout(control, 1)
    return row


def settings_page(win, tab: str = "conn") -> Page:
    settings = win.settings
    page = Page("settings")
    page.title("Settings")
    tab_row = QFrame()
    tab_row.setProperty("class", "tabline")
    tabs = hbox(spacing=2)
    for key, text in TABS:
        choice = button(text, lambda k=key: win.show_settings(k), "tab", name=f"settings-tab-{key}")
        choice.setCheckable(True)
        choice.setChecked(key == tab)
        tabs.addWidget(choice)
    tabs.addStretch(1)
    tab_row.setLayout(tabs)
    page.add(tab_row)

    saved_marks = {}

    def saved(key: str, extra: str = "") -> None:
        """Mark one control as saved and say where the setting will be seen at work."""
        settings.save(win.state_store)
        for name, (flag, says) in saved_marks.items():
            flag.setVisible(name == key)
            if says is not None:
                says.setVisible(name == key and bool(says.text()))
        if key in saved_marks and extra and saved_marks[key][1] is not None:
            saved_marks[key][1].setText(extra)
            saved_marks[key][1].setVisible(True)

    def mark_for(key: str) -> tuple:
        flag = label(f"{mark('ok')} Saved", "saved", wrap=False, name=f"saved-{key}")
        flag.hide()
        says = label(SAVED_SAYS.get(key, ""), "small", name=f"saved-says-{key}")
        says.hide()
        saved_marks[key] = (flag, says)
        return flag, says

    def choice(key: str, options: List[tuple], current: str, on_change, width: int = 340) -> QWidget:
        combo = QComboBox()
        combo.setObjectName(f"setting-{key}")
        for value, text in options:
            combo.addItem(text, value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.setMaximumWidth(width)
        flag, says = mark_for(key)

        def changed(_index: int) -> None:
            on_change(combo.currentData())
            saved(key)

        combo.currentIndexChanged.connect(changed)
        return box(vbox(hbox(combo, flag, stretch), says, spacing=4))

    def switch(key: str, value: bool, on_change) -> QWidget:
        tick = QCheckBox("On" if value else "Off")
        tick.setObjectName(f"setting-{key}")
        tick.setChecked(value)
        flag, says = mark_for(key)

        def changed(state: bool) -> None:
            tick.setText("On" if state else "Off")
            on_change(bool(state))
            saved(key)

        tick.clicked.connect(changed)
        return box(vbox(hbox(tick, flag, stretch), says, spacing=4))

    host = esc(normalize_address(win.conn.url)) if win.conn else ""
    who = "an access token" if (win.conn and win.conn.use_token) else esc(win.conn.username if win.conn else "")

    if tab == "conn":
        card = Card()
        card.add(label(
            '<table cellspacing="0" cellpadding="3">'
            f'<tr><td style="padding-right:24px">VCF Operations</td><td><b>{host}</b></td></tr>'
            f'<tr><td style="padding-right:24px">Signed in as</td><td><b>{who}</b></td></tr>'
            '<tr><td style="padding-right:24px">Status</td><td><b>● Connected</b></td></tr></table>', "plain",
            name="settings-connection"))
        test = button("Test the connection now", lambda: run_test(), "primary", name="settings-test")
        card.body.addLayout(hbox(test, button(f"Connect to a different VCF Operations, or as someone else{DOTS}",
                                              win.sign_out, name="settings-sign-out"), stretch))
        page.add(card)
        result_holder = box(vbox(label(
            "This tests the link between this computer and VCF Operations. To test the sign-in to a machine: open "
            'Machines, tick the machine, and press "Test connection only" at the bottom.', "small",
            name="settings-test-hint"), spacing=8))
        page.add(result_holder)
        page.primary = test

        def run_test() -> None:
            from vcf_ops_telegraf_helper.gui.widgets import _empty

            test.setEnabled(False)
            lines = [f"Reached {host}", f"Signed in as {who}", "Read the list of machines and collectors"]
            state = {"step": 0, "alive": True}
            page.on_leave = lambda: state.__setitem__("alive", False)

            def draw(done: bool = False, problem: Optional[tuple] = None, counts: str = "") -> None:
                if not state["alive"]:
                    return
                layout = result_holder.layout()
                _empty(layout)
                title = "Connection test"
                if done or problem:
                    title += " · " + format_when(time.time())
                result = Card(title)
                result.setObjectName("settings-test-result")
                for index, text in enumerate(lines):
                    if problem and index == state["step"]:
                        symbol, cls = mark("bad"), "cross"
                    elif state["step"] > index:
                        symbol, cls = mark("ok"), "tick"
                    else:
                        symbol, cls = (mark("wait") if state["step"] == index else mark("idle")), "small"
                    words = f"{text} {counts}" if (index == 2 and done) else text
                    result.body.addLayout(hbox(label(symbol, cls, wrap=False), (label(words, "plain"), 1), spacing=8))
                if problem:
                    result.add(Banner("bad", f"<b>{esc(problem[0])}</b><br>{esc(problem[1])}", name="settings-test-problem"))
                elif done:
                    result.add(Banner("ok", "<b>Everything works between this computer and VCF Operations.</b>",
                                      name="settings-test-ok"))
                    result.body.addLayout(hbox(
                        (label('To test the sign-in to a machine as well: open Machines, tick the machine, and press '
                               '"Test connection only" at the bottom. (The same button is on each machine\'s own page.)',
                               "small"), 1),
                        button("Go to Machines", lambda: win.show_machines(), name="settings-go-machines")))
                else:
                    result.add(busy_bar())
                layout.addWidget(result)
                adopt(result_holder)
                test.setEnabled(bool(done or problem))

            adapter = win.adapter
            draw()

            def reach():
                if not adapter.validate_connection():
                    raise RuntimeError("no answer")

            def step_failed(what: str, todo: str):
                return lambda _exc: draw(problem=(what, todo))

            def signed(_r) -> None:
                state["step"] = 2
                draw()
                win.tasks.run(lambda: (adapter.list_virtual_machines(strict=True), adapter.list_collector_targets()),
                              read, step_failed("VCF Operations did not hand over its list of machines.",
                                                "Check that your account is allowed to see them, then test again."),
                              name="test-read")

            def read(answer) -> None:
                vms, collectors = answer
                state["step"] = 3
                shown = sum(1 for vm in vms if not vm.is_platform_vm)
                draw(done=True, counts=f"({shown}) and collectors ({len(collectors)})")

            def reached(_r) -> None:
                state["step"] = 1
                draw()
                win.tasks.run(adapter.verify_credentials, signed,
                              step_failed("VCF Operations no longer accepts this sign-in.",
                                          'Press "Connect to a different VCF Operations, or as someone else" and sign in '
                                          "again."), name="test-signin")

            win.tasks.run(reach, reached, step_failed(f"Could not reach {normalize_address(win.conn.url)}.",
                                                      "Check that this computer is on the office network or VPN, then "
                                                      "test again."), name="test-reach")

    elif tab == "defaults":
        card = Card(margins=(16, 4, 16, 4), spacing=0)
        defaults_flag, defaults_says = mark_for("defaults")
        ticks = []

        def default_changed(key: str, on: bool) -> None:
            chosen = [k for k in settings.defaults if k != key] + ([key] if on else [])
            settings.defaults = ordered(chosen)
            saved("defaults", f'New setups will {"also collect" if on else "no longer collect"} "{name_of(key)}" unless '
                              "you change it.")

        for entry in [e for e in CATALOG if e.group == GROUP_BASICS]:
            tick = QCheckBox(entry.name)
            tick.setObjectName(f"default-{entry.key}")
            tick.setChecked(entry.key in settings.defaults)
            tick.clicked.connect(lambda state, k=entry.key: default_changed(k, bool(state)))
            ticks.append(tick)
        defaults_box = vbox(spacing=6)
        defaults_box.addWidget(grid(3, ticks, spacing=6))
        defaults_box.addLayout(hbox(defaults_flag, (defaults_says, 1), spacing=8))
        card.add(_setrow("Collect these by default",
                         "Ticked already when you set up a machine. You can still change them for each setup.", defaults_box))
        options = [(AUTO, "Let the app choose (recommended)")] + [
            (collector_key(c), collector_label(c)[0].upper() + collector_label(c)[1:]) for c in win.collectors]
        card.add(_setrow(
            "Send data to this collector",
            f"{COLLECTOR_IS} Letting the app choose means: the one the machine already uses, or else the recommended "
            "group, as long as the machine can reach it.",
            choice("collector", options, settings.collector, lambda value: setattr(settings, "collector", value))))
        versions = [(v, f"{v} (recommended)" if v == DEFAULT_TELEGRAF_VERSION else v) for v in TELEGRAF_VERSIONS]
        card.add(_setrow("Telegraf version to install", "Only used on machines that do not have Telegraf yet.",
                         choice("version", versions, settings.version, lambda value: setattr(settings, "version", value))))
        card.add(_setrow(
            "If a machine already has Telegraf",
            "Keeping it is safest. Updating only ever moves a machine up to the version above, never down.",
            choice("existing", [("keep", "Keep the version it has (recommended)"),
                                ("update", "Update it to the version above")], settings.existing,
                   lambda value: setattr(settings, "existing", value)), last=True))
        page.add(card)
        page.body.addLayout(hbox(
            (label("Changes are saved as you make them and used from the next setup. Machines that are already set up are "
                   'not touched: to change one of those, open it from Machines and press "Change what is collected".',
                   "small"), 1),
            button("Go to Machines", lambda: win.show_machines(), name="settings-go-machines")))

    elif tab == "look":
        card = Card(margins=(16, 4, 16, 4), spacing=0)
        theme_flag, _says = mark_for("theme")
        seg = hbox(spacing=0)
        buttons = {}

        def theme(value: str) -> None:
            win.set_theme(value)
            for key, widget in buttons.items():
                widget.setChecked(key == value)
            saved("theme")

        for position, (value, text) in enumerate((("dark", "Dark"), ("light", "Light"))):
            choose = button(text, lambda v=value: theme(v), "seg seg-first" if position == 0 else "seg seg-last",
                            name=f"setting-theme-{value}")
            choose.setCheckable(True)
            choose.setChecked(win.current_theme == value)
            buttons[value] = choose
            seg.addWidget(choose)
        seg.addWidget(theme_flag)
        seg.addStretch(1)
        card.add(_setrow("Colours", "Changes straight away and is remembered.", seg))
        card.add(_setrow("Tell me when a new version is out",
                         "Looks once a day. Sends nothing about you or your machines.",
                         switch("updates", settings.updates, lambda value: setattr(settings, "updates", value))))
        log_path = get_log_file_path()

        def show_log() -> None:
            text = "The log is empty: nothing has been written yet."
            if log_path.exists():
                try:
                    text = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-250:])
                except OSError as exc:
                    text = f"The log could not be read: {exc}"
            dialogs.show_text(win, "The app's own log", text, f"The newest lines of {esc(str(log_path))}",
                              name="log-dialog")

        card.add(_setrow(
            "Help with a problem",
            "A detailed record of what the app did, for whoever supports it. The results of your runs are under Activity, "
            "not here.",
            hbox(button("Show the app's own log", show_log, "sm", name="settings-log"),
                 button("Open its folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path.parent))), "sm",
                        name="settings-log-folder"), stretch)))
        history_note = label("", "saved", wrap=False, name="history-cleared")

        def clear_history() -> None:
            dialog = dialogs.Dialog(win, "Clear the history?", name="clear-history-dialog")
            dialog.add(label("Every past result under Activity is deleted from this computer. Machines are not touched, "
                             "and what the app knows about each machine is kept.", "plain"))
            dialog.add_button("Keep the history", dialog.reject, name="clear-history-cancel")
            dialog.add_button("Clear the history", dialog.accept, "danger", name="clear-history-ok")
            if dialog.exec() == dialogs.Dialog.Accepted:
                win.history.clear()
                win.refresh_statuses()
                history_note.setText(f"{mark('ok')} Cleared")

        card.add(_setrow("History",
                         "Kept on this computer only, until you clear it. It holds machine names, times, results and "
                         "commands, never passwords.",
                         hbox(button(f"Clear the history{DOTS}", clear_history, "sm", name="settings-clear-history"),
                              history_note, stretch)))
        card.add(_setrow("About", "Version, licence and open-source notices.",
                         hbox(button("About this app", win._show_about_dialog, "sm", name="settings-about"), stretch),
                         last=True))
        page.add(card)

    else:
        page.add(Banner("", "You do not need anything on this page for normal use. It is here for unusual networks.",
                        name="settings-advanced-note"))
        card = Card(margins=(16, 4, 16, 4), spacing=0)
        card.add(_setrow("Check VCF Operations' security certificate", "Leave on. Turn off only on a private lab network.",
                         switch("check_ops", settings.check_ops, lambda value: setattr(settings, "check_ops", value))))
        cert_flag, cert_says = mark_for("cert_file")
        shown = label(esc(Path(settings.cert_file).name) if settings.cert_file else "None chosen", "small", wrap=False,
                      name="setting-cert-file")

        def choose_cert() -> None:
            path = win.choose_certificate_file()
            if not path:
                return
            settings.cert_file = path
            shown.setText(esc(Path(path).name))
            saved("cert_file")

        def clear_cert() -> None:
            settings.cert_file = None
            shown.setText("None chosen")
            saved("cert_file")

        cert_line = hbox(shown, button(f"Choose{DOTS}", choose_cert, "sm", name="setting-cert-choose"),
                         link("Stop using it", clear_cert, name="setting-cert-clear"), cert_flag, stretch)
        card.add(_setrow("Company certificate file",
                         "Only if your IT team gave you one. Otherwise the app asks you to trust the certificate the first "
                         "time it sees it.", vbox(cert_line, cert_says, spacing=4)))
        card.add(_setrow("Machines check the collector's certificate", "When a machine sends its data.",
                         switch("check_collector", settings.check_collector, _set_collector_check(win))))
        card.add(_setrow(
            "Signing in to Windows machines",
            "Whether the app's connection to a Windows machine is encrypted. Many Windows machines only accept the "
            "unencrypted kind inside a private network.",
            choice("win_mode", [("auto", "Encrypted first; unencrypted if the machine refuses (recommended)"),
                                ("secure", "Encrypted only"), ("plain", "Unencrypted only")], settings.win_mode,
                   lambda value: setattr(settings, "win_mode", value), width=430), last=True))
        page.add(card)
    page.end()
    return page


def _set_collector_check(win):
    def apply(value: bool) -> None:
        win.settings.check_collector = value
        if win.conn is not None:
            # Takes effect on the next setup in this sitting, not only after reconnecting
            win.conn.agent_verify_ssl = value

    return apply
