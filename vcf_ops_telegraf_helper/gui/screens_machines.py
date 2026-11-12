"""Machines (the home screen) and the page of one machine."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLineEdit, QSizePolicy, QWidget

from vcf_ops_telegraf_helper.gui.model import Machine
from vcf_ops_telegraf_helper.gui.widgets import (
    adopt,
    Banner,
    Card,
    Headline,
    ListBox,
    Page,
    Pill,
    Tags,
    box,
    busy_bar,
    button,
    esc,
    hbox,
    label,
    link,
    mark,
    plain,
    reserve_bold,
    stretch,
    vbox,
)
from vcf_ops_telegraf_helper.storage.history import KIND_TEST
from vcf_ops_telegraf_helper.workflow.checks import collector_label
from vcf_ops_telegraf_helper.workflow.collect import effective_keys, names_of
from vcf_ops_telegraf_helper.workflow.plain import (
    FIRST_DATA_WAIT,
    STATUS_ATTENTION,
    STATUS_INTERRUPTED,
    STATUS_MANAGED,
    STATUS_NONE,
    STATUS_SENDING,
    STATUS_TEXT,
    STATUS_TONE,
    STATUS_WAITING,
    plural,
)

# The filter chips: key, words, the statuses each one covers
CHIPS = [
    ("all", "All", None),
    (STATUS_NONE, "Not set up", (STATUS_NONE,)),
    (STATUS_SENDING, "Monitored", (STATUS_SENDING, STATUS_WAITING)),
    (STATUS_ATTENTION, "Needs attention", (STATUS_ATTENTION, STATUS_INTERRUPTED)),
    (STATUS_MANAGED, "VCF Operations' own agent", (STATUS_MANAGED,)),
]
COL_TICK, COL_NAME, COL_SYSTEM, COL_ACTION = 34, 152, 196, 140


def _matches(machine: Machine, query: str) -> bool:
    query = query.strip().lower()
    return not query or query in f"{machine.name} {machine.address} {machine.os_name}".lower()


def visible_machines(win) -> List[Machine]:
    statuses = next(group for key, _text, group in CHIPS if key == win.filter)
    found = [m for m in win.machines if (win.show_off or not m.off) and (win.show_infra or not m.infra)]
    if statuses is not None:
        found = [m for m in found if m.status in statuses and not m.infra]
    return [m for m in found if _matches(m, win.query)]


def chip_counts(win) -> dict:
    listed = [m for m in win.listed() if _matches(m, win.query)]
    return {key: len(listed) if group is None else sum(1 for m in listed if m.status in group) for key, _t, group in CHIPS}


def _row_action(win, machine: Machine) -> Optional[QWidget]:
    if machine.infra:
        return None
    if machine.off:
        return label("Powered off", "small", wrap=False)
    name = f"action-{machine.name}"
    if machine.status == STATUS_NONE:
        return button("Set up", lambda: win.start_flow("setup", [machine.id]), "sm", name=name)
    if machine.status in (STATUS_SENDING, STATUS_WAITING):
        return button("Open", lambda: win.show_machine(machine.id), "sm", name=name)
    if machine.status == STATUS_MANAGED:
        return button("Take over…", lambda: win.start_flow("takeover", [machine.id]), "sm", name=name,
                      tip="Replace VCF Operations' own agent with open-source Telegraf. You see every step, and confirm, "
                          "before anything changes.")
    if machine.status == STATUS_INTERRUPTED:
        return button("Finish takeover", lambda: win.start_flow("takeover", [machine.id], resume=True), "primary sm",
                      name=name)
    return button("See problem", lambda: win.show_machine(machine.id), "sm", name=name)


def _machine_row(win, machine: Machine, on_tick) -> QFrame:
    row = QFrame()
    row.setObjectName(f"machine-{machine.name}")
    row.setProperty("class", "mrow mrow-sel" if machine.id in win.selection else "mrow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(10, 4, 12, 4)
    layout.setSpacing(0)
    row.setMinimumHeight(38)

    tick_holder = QWidget()
    tick_holder.setProperty("class", "clear")
    tick_holder.setFixedWidth(COL_TICK)
    if machine.selectable:
        tick = QCheckBox()
        tick.setObjectName(f"tick-{machine.name}")
        tick.setAccessibleName(f"Select {machine.name}")
        tick.setChecked(machine.id in win.selection)
        tick.clicked.connect(lambda state, m=machine: on_tick(m, bool(state)))
        tick_holder.setLayout(hbox(tick))
    layout.addWidget(tick_holder)

    if machine.infra:
        who = label(esc(machine.name), "nm", wrap=False)
        who.setToolTip(machine.name)
    else:
        who = button(machine.name, lambda: win.show_machine(machine.id), "namebtn", name=f"open-{machine.name}",
                     tip=f"Open {machine.name}")
    who.setMaximumWidth(COL_NAME - 8)
    name_box = box(vbox(who, label(esc(machine.address), "nm-sub", wrap=False), spacing=0))
    name_box.setFixedWidth(COL_NAME)
    layout.addWidget(name_box)

    system = label(esc(machine.os_name), "cell", wrap=False)
    system.setFixedWidth(COL_SYSTEM)
    system.setToolTip(machine.os_name)
    layout.addWidget(system)

    monitoring = vbox(spacing=2)
    if machine.infra:
        monitoring.addWidget(label("VMware system machine. Not monitored this way.", "small"))
    else:
        pill = Pill(STATUS_TEXT[machine.status], STATUS_TONE[machine.status])
        pill.setObjectName(f"status-{machine.name}")
        monitoring.addLayout(hbox(pill, stretch))
        if machine.status in (STATUS_ATTENTION, STATUS_INTERRUPTED) and machine.problem:
            why = label(esc(machine.problem.what), "why")
            why.setToolTip(machine.problem.what)
            monitoring.addWidget(why)
        elif machine.status == STATUS_MANAGED:
            monitoring.addWidget(label("Monitored already. You can switch it to Telegraf.", "why"))
    layout.addWidget(box(monitoring), 1)

    action_holder = QWidget()
    action_holder.setProperty("class", "clear")
    action_holder.setFixedWidth(COL_ACTION)
    action = _row_action(win, machine)
    action_holder.setLayout(hbox(stretch, action) if action is not None else hbox(stretch))
    layout.addWidget(action_holder)
    return row


def machines_page(win) -> Page:
    page = Page("machines")
    dots = "…"
    if win.loading:
        page.title("Machines", f"Reading the list of machines from VCF Operations{dots}")
        page.add(busy_bar(420)).end()
        return page
    if win.load_error:
        page.title("Machines")
        page.add(Banner(
            "bad", "<b>The list of machines could not be read from VCF Operations.</b><br>Check that this computer is "
            "still on the office network or VPN, then press Look again. If it keeps happening, the detail is in the app's "
            "own log (Settings, This app).", button("Look again", lambda: win.reload_machines(), "primary", name="machines-retry"),
            name="machines-error")).end()
        return page
    countable = [m for m in win.machines if not m.infra]
    if not countable:
        who = "your access token" if win.conn.use_token else f"the account <b>{esc(win.conn.username)}</b>"
        page.title("Machines")
        empty = vbox(
            label("VCF Operations did not list any machines", "h2-big", name="machines-none"),
            label(f"You are connected, but {who} cannot see any machines. Usually the account has not been given access "
                  "to them. Ask whoever runs VCF Operations, or sign in with an account that can see them.", "sub"),
            hbox(stretch, button("Look again", lambda: win.reload_machines(), "primary", name="machines-retry"),
                 button("Sign in as someone else", win.sign_out, name="machines-sign-out"), stretch),
            spacing=10)
        holder = box(empty)
        holder.setMaximumWidth(460)
        for index in range(2):
            empty.itemAt(index).widget().setAlignment(Qt.AlignHCenter)
        page.body.addStretch(1)
        page.body.addLayout(hbox(stretch, holder, stretch))
        page.body.addStretch(1)
        return page

    # Header: title, find box, refresh
    find = QLineEdit(win.query)
    find.setObjectName("machines-find")
    find.setPlaceholderText("Find a machine")
    find.setAccessibleName("Find a machine by name or address")
    find.setFixedWidth(180)
    find.setClearButtonEnabled(True)
    refresh = button("Refresh list", lambda: win.reload_machines(), name="machines-refresh",
                     tip="Read the list from VCF Operations again")
    heading = vbox(label("Machines", "h1", name="page-title"),
                   label("Every machine VCF Operations knows about.", "sub", name="page-sub"), spacing=3)
    page.body.addLayout(hbox((box(heading), 1), find, refresh, align=Qt.AlignTop))

    interrupted = next((m for m in win.machines if m.status == STATUS_INTERRUPTED), None)
    if interrupted is not None:
        page.add(Banner(
            "bad", f"<b>The takeover of {esc(interrupted.name)} stopped half-way.</b> The old agent is already removed, so "
            "that machine is not monitored right now. Nothing is lost: the app saved where it stopped and can carry on "
            "from there.",
            button("Finish the takeover", lambda: win.start_flow("takeover", [interrupted.id], resume=True), "primary",
                   name="banner-finish-takeover"), name="banner-interrupted"))
    if win.inventory_note:
        note = Banner("warn", "<b>Part of the list could not be read from VCF Operations</b>, so some statuses may be "
                      "out of date. Press Refresh list to try again.", name="banner-inventory")
        note.setToolTip(str(win.inventory_note))
        page.add(note)
    if win.platform_note:
        note = Banner("", "VCF Operations did not say which machines are VMware's own system machines, so some of them may "
                      "be listed below as ordinary machines. Leave those alone.", name="banner-platform")
        note.setToolTip(str(win.platform_note))
        page.add(note)
    if win.banner:
        kind, html = win.banner

        def hide() -> None:
            win.banner = None
            win.show_machines()

        page.add(Banner(kind, html, button("Hide", hide, "ghost sm", name="banner-hide"), name="banner"))

    chip_row = hbox(spacing=6)
    chips = {}
    table = ListBox(name="machines-list")
    foot_note = label("", "small", wrap=False, name="machines-hidden")
    foot_links = hbox(spacing=6)

    def choose(key: str) -> None:
        win.filter = key
        redraw()

    for key, text, _group in CHIPS:
        chip = button("", lambda k=key: choose(k), "chip", name=f"chip-{key}")
        chip.setCheckable(True)
        chips[key] = (chip, text)
        chip_row.addWidget(chip)
    chip_row.addStretch(1)
    page.body.addLayout(chip_row)

    header = QFrame()
    header.setProperty("class", "thead")
    head_layout = QHBoxLayout(header)
    head_layout.setContentsMargins(10, 7, 12, 7)
    head_layout.setSpacing(0)
    tick_all = QCheckBox()
    tick_all.setObjectName("tick-all")
    tick_all.setAccessibleName("Tick all machines shown")
    holder = QWidget()
    holder.setProperty("class", "clear")
    holder.setFixedWidth(COL_TICK)
    holder.setLayout(hbox(tick_all))
    head_layout.addWidget(holder)
    for text, width in (("MACHINE", COL_NAME), ("SYSTEM", COL_SYSTEM), ("MONITORING", None), ("", COL_ACTION)):
        cell = label(text, "th", wrap=False)
        if width:
            cell.setFixedWidth(width)
            head_layout.addWidget(cell)
        else:
            head_layout.addWidget(cell, 1)
    table.set_header(header)
    table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    page.body.addWidget(table, 1)
    page.body.addLayout(hbox(foot_note, foot_links, stretch, spacing=6))

    def on_tick(machine: Machine, on: bool) -> None:
        if on and machine.id not in win.selection:
            win.selection.append(machine.id)
        elif not on and machine.id in win.selection:
            win.selection.remove(machine.id)
        redraw(keep_focus=f"tick-{machine.name}")

    def on_tick_all(state: bool) -> None:
        shown = [m.id for m in visible_machines(win) if m.selectable]
        if state:
            win.selection.extend(i for i in shown if i not in win.selection)
        else:
            win.selection = [i for i in win.selection if i not in shown]
        redraw(keep_focus="tick-all")

    tick_all.clicked.connect(on_tick_all)

    def toggle(attr: str) -> None:
        setattr(win, attr, not getattr(win, attr))
        win.selection = [i for i in win.selection if win.machine(i) in visible_machines(win)]
        redraw()

    def clear_filter() -> None:
        win.filter, win.query = "all", ""
        find.setText("")
        redraw()

    def draw_footer() -> None:
        count = len(win.selection)
        if count:
            what = plural(count, "machine")
            on_screen = {m.id for m in visible_machines(win)}
            hidden = sum(1 for i in win.selection if i not in on_screen)
            aside = f", {hidden} not shown with this filter" if hidden else ""
            page.footer(
                label(f"<b>{what} ticked</b>{aside}", "note", wrap=False, name="selection-count"),
                link("Untick all", lambda: (win.selection.clear(), redraw()), name="selection-clear"), stretch,
                button("Test connection only", lambda: win.start_flow("test", list(win.selection)), name="selection-test"),
                button(f"Set up monitoring on {what} →", lambda: win.start_flow("setup", list(win.selection)),
                       "primary big", name="selection-setup"),
            )
        else:
            page.footer(label("To set up or test several machines together, tick them. The buttons for both appear here.",
                              "note", name="selection-hint"))

    def redraw(keep_focus: Optional[str] = None) -> None:
        counts = chip_counts(win)
        for key, (chip, text) in chips.items():
            chip.setText(plain(f"{text}  {counts[key]}"))
            reserve_bold(chip)
            chip.setChecked(win.filter == key)
        shown = visible_machines(win)
        table.clear()
        if not shown:
            query = win.query.strip()
            if query:
                among = f' among the machines that are "{dict((k, t) for k, t, _g in CHIPS)[win.filter]}"' if win.filter != "all" else ""
                title, text = "No machine matches", f'Nothing matches "{esc(query)}"{among}.'
            else:
                title, text = "No machines here", f'No machine is "{dict((k, t) for k, t, _g in CHIPS)[win.filter]}" right now.'
            empty = vbox(label(title, "h2-big", name="machines-empty"), label(text, "sub"),
                         hbox(stretch, button("Show all machines", clear_filter, name="machines-show-all"), stretch),
                         spacing=8, margins=(28, 28, 28, 28))
            for index in range(2):
                empty.itemAt(index).widget().setAlignment(Qt.AlignHCenter)
            table.add_row(box(empty))
        for machine in shown:
            table.add_row(_machine_row(win, machine, on_tick))
        selectable = [m for m in shown if m.selectable]
        tick_all.setChecked(bool(selectable) and all(m.id in win.selection for m in selectable))
        tick_all.setEnabled(bool(selectable))

        from vcf_ops_telegraf_helper.gui.widgets import _empty

        _empty(foot_links)
        hidden = sum(1 for m in win.machines if m.infra)
        off = sum(1 for m in win.machines if m.off and not m.infra)
        parts = []
        if hidden and not win.show_infra:
            foot_note.setText(f"{plural(hidden, 'VMware system machine is', 'VMware system machines are')} hidden.")
            foot_note.setToolTip("vCenter's own service machines and similar. They are never monitored this way.")
            foot_links.addWidget(link("Show", lambda: toggle("show_infra"), name="show-infra"))
        elif win.show_infra:
            foot_note.setText("Showing VMware system machines.")
            foot_links.addWidget(link("Hide them", lambda: toggle("show_infra"), name="show-infra"))
        else:
            foot_note.setText("")
        if off:
            if win.show_off:
                parts.append(label("&nbsp; Showing powered-off machines.", "small", wrap=False))
                parts.append(link("Hide them", lambda: toggle("show_off"), name="show-off"))
            else:
                parts.append(label(f"&nbsp; {plural(off, 'powered-off machine')} hidden.", "small", wrap=False))
                parts.append(link("Show", lambda: toggle("show_off"), name="show-off"))
        for part in parts:
            foot_links.addWidget(part)
        draw_footer()
        adopt(page)
        if keep_focus:
            target = page.findChild(QWidget, keep_focus)
            if target is not None:
                target.setFocus()

    def searched(text: str) -> None:
        win.query = text
        redraw()
        find.setFocus()

    find.textChanged.connect(searched)
    redraw()
    page.redraw = redraw  # type: ignore[attr-defined]
    return page


# ---------------------------------------------------------------------------------------------
# One machine
# ---------------------------------------------------------------------------------------------
def known_collecting(win, machine: Machine) -> Optional[List[str]]:
    """What the machine collects, as far as this app knows without signing in again."""
    facts = win.history.facts(machine.id)
    if facts.get("collecting") is not None:
        return list(facts["collecting"])
    return None


def _collector_text(win, machine: Machine) -> str:
    facts = win.history.facts(machine.id)
    if facts.get("collector"):
        return str(facts["collector"])
    vm = machine.vm
    match = next((c for c in win.collectors if c.is_collector_group and c.name and c.name == vm.collector_group), None) \
        or next((c for c in win.collectors if not c.is_collector_group and c.address == vm.collector_address), None)
    if match is not None:
        return collector_label(match)
    if vm.collector_group or vm.collector_address:
        return f'the collector "{vm.collector_group or vm.collector_address}"'
    return ""


def machine_page(win, machine: Machine) -> Page:
    page = Page("machine")
    page.body.addLayout(vbox(
        hbox(link("← All machines", lambda: win.show_machines(), name="machine-back"), stretch),
        label(esc(machine.name), "h1", name="page-title"),
        label(f"{esc(machine.os_name)} &middot; {esc(machine.address)}", "sub", name="page-sub"), spacing=3))

    def start(kind: str, **more):
        return lambda: win.start_flow(kind, [machine.id], **more)

    test = button("Test connection only", start("test"), name="machine-test")
    status = machine.status
    if machine.infra:
        page.add(Headline("info", "i", "This is one of VMware's own system machines",
                          "It runs the platform itself and is never monitored this way.", name="machine-headline"))
        actions: list = []
    elif status == STATUS_SENDING:
        page.add(Headline("ok", mark("ok"), "Sending data to VCF Operations",
                          f"VCF Operations is receiving data from this machine. {esc(machine.last)}", name="machine-headline"))
        actions = [button("Change what is collected", start("change"), "primary", name="machine-change"), test, stretch,
                   button("Remove monitoring…", lambda: ask_remove(win, machine), name="machine-remove")]
    elif status == STATUS_WAITING:
        page.add(Headline("info", mark("wait"), "Set up. Waiting for the first data to arrive",
                          f'{FIRST_DATA_WAIT} Press "Check now" to look again.', name="machine-headline"))
        actions = [button("Check now", lambda: check_now(win, machine), "primary", name="machine-check-now"),
                   button("Change what is collected", start("change"), name="machine-change"), stretch,
                   button("Remove monitoring…", lambda: ask_remove(win, machine), name="machine-remove")]
    elif status == STATUS_ATTENTION:
        problem = machine.problem
        page.add(Headline("bad", "!", "The last attempt to set up monitoring did not finish" if win.history.last_change(machine.id)
                          else "Monitoring on this machine is not working",
                          f"<b>What happened:</b> {esc(problem.what)}<br><b>What to do:</b> {esc(problem.todo)}",
                          name="machine-headline"))
        actions = [button("Try again", start("setup", retry=True), "primary", name="machine-retry"), test]
    elif status == STATUS_INTERRUPTED:
        page.add(Headline("bad", "!", "The takeover of this machine stopped half-way",
                          f"<b>What happened:</b> {esc(machine.problem.what)}<br><b>What to do:</b> {esc(machine.problem.todo)}",
                          name="machine-headline"))
        actions = [button("Finish the takeover", start("takeover", resume=True), "primary", name="machine-finish")]
    elif status == STATUS_MANAGED:
        page.add(Headline(
            "info", "i", "This machine is monitored by VCF Operations' own agent",
            'VCF Operations installed its own monitoring program (an "agent") here, and it is sending data. You can leave '
            "it as it is. Or take it over: replace that agent with open-source Telegraf, which you control from this app. "
            "Its history in VCF Operations is kept.", name="machine-headline"))
        actions = [button("Take over this machine…", start("takeover"), "primary", name="machine-takeover"), test]
    elif machine.off:
        page.add(Headline("info", "i", "This machine is powered off",
                          "Switch it on, then press Refresh list on Machines to set up monitoring.", name="machine-headline"))
        actions = []
    else:
        page.add(Headline("info", "i", "Monitoring is not set up on this machine",
                          "VCF Operations sees it from the outside only. Set up monitoring to collect measurements from "
                          "inside it.", name="machine-headline"))
        actions = [button("Set up monitoring", start("setup"), "primary", name="machine-setup"), test]

    facts = win.history.facts(machine.id)
    collecting = known_collecting(win, machine)
    now = Card("Collecting now")
    if status in (STATUS_SENDING, STATUS_WAITING) and collecting:
        now.add(Tags(names_of(effective_keys(collecting, machine.os))))
    elif status == STATUS_MANAGED:
        now.add(label("What VCF Operations' own agent collects. A takeover keeps it the same.", "small"))
    elif status in (STATUS_SENDING, STATUS_WAITING):
        now.add(label('The app has not read this from the machine yet. Press "Change what is collected" and it reads it.',
                      "small", name="machine-collecting-unknown"))
    else:
        now.add(label("Nothing yet.", "small", name="machine-collecting-none"))
    goes = _collector_text(win, machine)
    if not goes or status in (STATUS_NONE, STATUS_ATTENTION, STATUS_INTERRUPTED):
        where = '<span style="font-weight:400">Not sending yet</span>'
    else:
        where = esc(goes[0].upper() + goes[1:])
    if status == STATUS_MANAGED:
        telegraf = "VCF Operations' own copy"
    elif status == STATUS_INTERRUPTED:
        telegraf = "Not installed (the old agent was removed)"
    elif facts.get("telegraf"):
        telegraf = f"Version {esc(str(facts['telegraf']))}" + (
            ", installed by someone else and not yet reporting to VCF Operations"
            if status == STATUS_NONE and not win.history.last_change(machine.id) else "")
    elif status in (STATUS_SENDING, STATUS_WAITING):
        telegraf = "Installed"
    else:
        telegraf = "Not installed, as far as VCF Operations knows"
    now.add(8, label(
        '<table cellspacing="0" cellpadding="2">'
        f'<tr><td style="padding-right:18px">Data goes to</td><td>{where}</td></tr>'
        f'<tr><td style="padding-right:18px">Telegraf on machine</td><td>{telegraf}</td></tr></table>', "small",
        name="machine-facts"))
    now.body.addStretch(1)

    done = Card("What has been done to this machine")
    entries = win.history.for_machine(machine.id)
    if not entries:
        done.add(label("Nothing yet. Everything this app does to the machine will be listed here.", "small",
                       name="machine-history-none"))
    for entry in entries[:4]:
        row = entry.row_for(machine.id)
        worked = row.ok
        note = "" if worked else " &middot; did not work"
        if worked and (entry.practice or entry.kind == KIND_TEST):
            note = " &middot; nothing was changed"
        elif worked and row.left:
            note = " &middot; left out"
        elif worked and row.skipped:
            note = " &middot; stopped before it started"
        symbol = label(mark("ok" if worked and row.counted else ("skip" if worked else "bad")),
                       "tick" if worked and row.counted else ("small" if worked else "cross"), wrap=False)
        symbol.setFixedWidth(14)
        text = vbox(hbox(link(entry.title, lambda e=entry: win.show_entry(e.id, from_machine=machine.id),
                              name=f"history-{entry.id}"), stretch),
                    label(f"{esc(entry.when())}{note}", "small"), spacing=0)
        done.body.addLayout(hbox(symbol, (box(text), 1), spacing=8, align=Qt.AlignTop))
    done.body.addStretch(1)
    page.body.addLayout(hbox((now, 1), (done, 1), spacing=12))
    page.end()
    if actions:
        page.footer(*actions)
    return page


def check_now(win, machine: Machine) -> None:
    """Look for the machine's first data in VCF Operations right now."""
    adapter = win.adapter
    vm = machine.vm

    def work() -> str:
        return adapter.verify_ingestion(vm.name, vc_id=vm.vc_id, vm_mor=vm.vm_mor)

    def done(answer: str) -> None:
        if answer == "PASS":
            win.arrived.add(machine.id)
            win.refresh_statuses()
            win.toast(f"Data has arrived from {machine.name}.")
        else:
            win.toast(f"No data from {machine.name} yet. Try again in a few minutes.")
        if win.screen == "machine" and win.machine_id == machine.id and win.flow is None:
            win.show_machine(machine.id)

    win.toast(f"Looking for data from {machine.name}…")
    win.tasks.run(work, done, failed=lambda _exc: win.toast("VCF Operations did not answer. Try again in a moment."),
                  name="check-now")


def ask_remove(win, machine: Machine) -> None:
    from vcf_ops_telegraf_helper.gui.dialogs import confirm_remove, remove_monitoring

    if confirm_remove(win, machine):
        remove_monitoring(win, machine)
